import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from sqlalchemy import case, func, text
try:
    from flask import current_app, has_app_context
except Exception:
    current_app = None

    def has_app_context():
        return False

from app.core.spliter import split_origin_files
from app.core.translator import JsonAnalyser, JsonGenerator
from app.core.utils.loader import write_file_work_infos
from app.model import FileCacheJobModel, FileModule, SourceModel, WordsModel, db, session
from config import APP_TEMP_PATH, EN_PATH, HOMEBREW_EN_PATH, SPLITED_5ETOOLS_EN_PATH, UA_EN_PATH, logger

SOURCE_ROOTS = [EN_PATH, HOMEBREW_EN_PATH, UA_EN_PATH]
SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-sync")
SYNC_TASK_LOCK = threading.Lock()
SYNC_TASKS = {}
SYNC_TASKS_BY_FILE = {}
SYNC_SUCCESS_COOLDOWN_SECONDS = 30
CACHE_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-cache")
CACHE_JOB_DISPATCH_LOCK = threading.Lock()
CACHE_JOB_RUNNING_TIMEOUT_SECONDS = 15 * 60
CACHE_JOB_LOCK_TIMEOUT_SECONDS = 1
JOB_CACHE_LOCK = threading.Lock()
JOB_CACHE_LOCKS = {}
CACHE_SCHEMA_LOCK = threading.Lock()
CACHE_SCHEMA_READY = False
CACHE_STATUS_FRESH = "fresh"
CACHE_STATUS_DIRTY = "dirty"
CACHE_STATUS_REBUILDING = "rebuilding"
CACHE_STATUS_ERROR = "error"


def ensure_cache_runtime_schema():
    global CACHE_SCHEMA_READY
    if CACHE_SCHEMA_READY:
        return
    with CACHE_SCHEMA_LOCK:
        if CACHE_SCHEMA_READY:
            return
        try:
            from app.model import ensure_file_cache_job_table_schema, ensure_file_table_schema
            ensure_file_table_schema()
            ensure_file_cache_job_table_schema()
            CACHE_SCHEMA_READY = True
        except Exception as exc:
            session.rollback()
            logger.error(f"缓存一致性运行时迁移失败: {exc}")


def get_split_file_path(rel_path: str) -> str:
    return os.path.join(SPLITED_5ETOOLS_EN_PATH, rel_path)


def get_source_file_path(rel_path: str) -> str:
    for root_path in SOURCE_ROOTS:
        if not root_path:
            continue
        source_path = os.path.join(root_path, rel_path)
        if os.path.exists(source_path):
            return source_path
    return os.path.join(EN_PATH, rel_path)


def normalize_source_file(source_file: str) -> str:
    source_file = os.path.normpath(source_file)
    if not os.path.isabs(source_file):
        return source_file

    for root_path in SOURCE_ROOTS:
        if not root_path:
            continue
        root_path = os.path.abspath(root_path)
        try:
            if os.path.commonpath([source_file, root_path]) == root_path:
                return os.path.relpath(source_file, root_path)
        except ValueError:
            continue

    checked_roots = [root for root in SOURCE_ROOTS if root]
    raise ValueError(f"{source_file} is not under a configured source root: {checked_roots}")


def list_source_json_files(source_path: str) -> List[str]:
    source_file = normalize_source_file(source_path)
    resolved_path = get_source_file_path(source_file)
    if not os.path.exists(resolved_path):
        checked_roots = [root for root in SOURCE_ROOTS if root]
        raise FileNotFoundError(f"{source_file} not found under source roots: {checked_roots}")
    if os.path.isfile(resolved_path):
        return [source_file]

    source_files = []
    for root, _, files in os.walk(resolved_path):
        for file_name in files:
            if not file_name.lower().endswith(".json"):
                continue
            file_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(file_path, resolved_path)
            source_files.append(os.path.normpath(os.path.join(source_file, rel_path)))
    return sorted(source_files)


def get_job_cache_paths(rel_path: str):
    return (
        os.path.join(APP_TEMP_PATH, rel_path + ".jobs"),
        os.path.join(APP_TEMP_PATH, rel_path),
    )


def _get_job_cache_lock(split_file: str):
    with JOB_CACHE_LOCK:
        if split_file not in JOB_CACHE_LOCKS:
            JOB_CACHE_LOCKS[split_file] = threading.Lock()
        return JOB_CACHE_LOCKS[split_file]


def _load_json_file(path: str):
    with open(path, "r") as json_file:
        return json.load(json_file)


def _atomic_write_json_file(path: str, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    with open(temp_path, "w") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _load_split_file_fallback(split_file: str):
    split_path = get_split_file_path(split_file)
    try:
        return _load_json_file(split_path)
    except Exception as exc:
        cached_obj = load_cached_json_content(split_file)
        if cached_obj is not None:
            logger.warning(f"拆分文件缺失，使用temp内容缓存兜底: {split_file}")
            return cached_obj
        logger.error(f"读取拆分文件兜底失败: {split_file}, {exc}")
        return {}


def get_cached_content_paths(rel_path: str):
    return (
        os.path.join(APP_TEMP_PATH, rel_path + ".en"),
        os.path.join(APP_TEMP_PATH, rel_path),
    )


def has_jobs_cache(split_file: str) -> bool:
    job_path, cn_path = get_job_cache_paths(split_file)
    return os.path.exists(job_path) and os.path.exists(cn_path)


def load_cached_json_content(split_file: str):
    for cache_path in get_cached_content_paths(split_file):
        if not os.path.exists(cache_path):
            continue
        try:
            return _load_json_file(cache_path)
        except Exception as exc:
            logger.error(f"读取temp内容缓存失败: {split_file}, {cache_path}, {exc}")
    return None


def _read_jobs_cache_files(split_file: str):
    job_path, cn_path = get_job_cache_paths(split_file)
    return _load_json_file(job_path), _load_json_file(cn_path)


def _fallback_jobs_cache(split_file: str, exc: Exception = None):
    if exc is not None:
        logger.error(f"读取/重建jobs缓存失败，使用拆分文件兜底: {split_file}, {exc}")
    return [], _load_split_file_fallback(split_file)


def update_job_cache_word(
    split_file: str,
    word_id: int,
    uid: Optional[str] = None,
    en_str: Optional[str] = None,
    **updates,
) -> bool:
    if not split_file or not word_id:
        return False
    job_path, _ = get_job_cache_paths(split_file)
    if not os.path.exists(job_path):
        return False
    cache_lock = _get_job_cache_lock(split_file)
    with cache_lock:
        try:
            with open(job_path, "r") as job_file:
                job_list = json.load(job_file)
        except Exception as exc:
            logger.error(f"读取jobs缓存失败: {split_file}, {exc}")
            return False

        changed = False
        for job in job_list:
            has_word_id = int(job.get("sql_id") or 0) == int(word_id)
            is_missing_word = not job.get("sql_id")
            has_uid = bool(uid) and job.get("uid") == uid
            has_en = not uid and bool(en_str) and job.get("en_str") == en_str
            if not has_word_id and not (is_missing_word and (has_uid or has_en)):
                continue
            if not job.get("sql_id"):
                job["sql_id"] = word_id
                changed = True
            for key, value in updates.items():
                if value is not None:
                    if job.get(key) == value:
                        continue
                    job[key] = value
                    changed = True

        if not changed:
            return False

        try:
            _atomic_write_json_file(job_path, job_list)
            update_file_progress_from_jobs(split_file, job_list)
            return True
        except Exception as exc:
            logger.error(f"写入jobs缓存失败: {split_file}, {exc}")
            mark_file_cache_error(split_file, str(exc))
            return False


def get_source_mtime(source_file: str) -> float:
    source_path = get_source_file_path(source_file)
    if not os.path.exists(source_path):
        return 0
    return os.path.getmtime(source_path)


def is_source_file_stale(source_file: Optional[str], split_file: str) -> bool:
    if not source_file:
        return False
    source_path = get_source_file_path(source_file)
    split_path = get_split_file_path(split_file)
    if not os.path.exists(source_path) or not os.path.exists(split_path):
        return False
    return os.path.getmtime(source_path) > os.path.getmtime(split_path)


def is_file_marked_stale(split_file: str) -> bool:
    file_row = FileModule.query.filter_by(file=split_file).first()
    return bool(file_row and file_row.stale)


def is_file_cache_revision_stale(split_file: str) -> bool:
    ensure_cache_runtime_schema()
    file_row = FileModule.query.filter_by(file=split_file).first()
    if file_row is None:
        return False
    return (
        int(file_row.cache_revision or 0) < int(file_row.content_revision or 0)
        or (file_row.cache_status or CACHE_STATUS_FRESH) != CACHE_STATUS_FRESH
    )


def is_job_cache_stale(split_file: str) -> bool:
    split_path = get_split_file_path(split_file)
    job_path, cn_path = get_job_cache_paths(split_file)
    if not os.path.exists(split_path):
        return False
    if not os.path.exists(job_path) or not os.path.exists(cn_path):
        return True
    split_mtime = os.path.getmtime(split_path)
    return (
        os.path.getmtime(job_path) < split_mtime
        or os.path.getmtime(cn_path) < split_mtime
        or is_file_cache_revision_stale(split_file)
    )


def _load_split_origin_file(split_path: str) -> Optional[str]:
    try:
        with open(split_path, "r") as file:
            obj = json.load(file)
    except Exception as exc:
        logger.error(f"读取拆分文件失败: {split_path}, {exc}")
        return None
    if not isinstance(obj, dict):
        return None
    meta = obj.get("_meta", {})
    if not isinstance(meta, dict):
        return None
    return meta.get("origin_file")


def _delete_cache_file(path: str):
    if os.path.exists(path):
        os.remove(path)


def _delete_split_file(rel_path: str):
    split_path = get_split_file_path(rel_path)
    if os.path.exists(split_path):
        os.remove(split_path)
    job_path, cn_path = get_job_cache_paths(rel_path)
    _delete_cache_file(job_path)
    _delete_cache_file(cn_path)


def mark_files_dirty(
    file_paths: List[str],
    *,
    bump_revision: bool = True,
    status: str = CACHE_STATUS_DIRTY,
    error_message: str = "",
    commit: bool = True,
) -> Dict[str, int]:
    ensure_cache_runtime_schema()
    file_paths = sorted({file_path for file_path in file_paths if file_path})
    if not file_paths:
        return {}
    revisions = {}
    file_rows = FileModule.query.filter(FileModule.file.in_(file_paths)).all()
    for file_row in file_rows:
        if bump_revision:
            file_row.content_revision = int(file_row.content_revision or 0) + 1
        file_row.stale = 1
        file_row.cache_status = status
        file_row.cache_error = error_message or ""
        revisions[file_row.file] = int(file_row.content_revision or 0)
    if commit:
        session.commit()
    return revisions


def mark_files_stale(file_paths: List[str]):
    return mark_files_dirty(file_paths)


def mark_file_cache_rebuilding(file_path: str, *, commit: bool = True):
    ensure_cache_runtime_schema()
    if not file_path:
        return
    file_row = FileModule.query.filter_by(file=file_path).first()
    if file_row is None:
        return
    file_row.cache_status = CACHE_STATUS_REBUILDING
    file_row.cache_error = ""
    if commit:
        session.commit()


def mark_file_cache_error(file_path: str, error_message: str, *, commit: bool = True):
    ensure_cache_runtime_schema()
    if not file_path:
        return
    file_row = FileModule.query.filter_by(file=file_path).first()
    if file_row is None:
        return
    file_row.stale = 1
    file_row.cache_status = CACHE_STATUS_ERROR
    file_row.cache_error = str(error_message or "")[:1024]
    if commit:
        session.commit()


def clear_file_stale(file_path: str):
    ensure_cache_runtime_schema()
    if not file_path:
        return
    FileModule.query.filter_by(file=file_path).update(
        {
            FileModule.stale: 0,
            FileModule.cache_status: CACHE_STATUS_FRESH,
            FileModule.cache_error: "",
        },
        synchronize_session=False,
    )
    session.commit()


def _scan_split_files_by_source(source_file: str, root_dir: str = SPLITED_5ETOOLS_EN_PATH) -> List[str]:
    matched_files = []
    for root, _, files in os.walk(root_dir):
        for file_name in files:
            if not file_name.endswith(".json"):
                continue
            split_path = os.path.join(root, file_name)
            origin_file = _load_split_origin_file(split_path)
            if origin_file == source_file:
                matched_files.append(os.path.relpath(split_path, root_dir))
    return matched_files


def refresh_progress_by_files(file_paths: List[str], clear_stale: bool = True) -> Dict[str, Dict[str, int]]:
    ensure_cache_runtime_schema()
    file_paths = sorted({file_path for file_path in file_paths if file_path})
    if not file_paths:
        return {}

    rows = (
        session.query(
            SourceModel.file.label("file"),
            func.count(SourceModel.id).label("total"),
            func.sum(
                case(
                    ((WordsModel.proofread == 1) | (WordsModel.is_key == 1), 1),
                    else_=0,
                )
            ).label("translate"),
            func.sum(
                case(
                    (WordsModel.proofread == 1, 1),
                    else_=0,
                )
            ).label("proofread"),
        )
        .join(WordsModel, WordsModel.id == SourceModel.word_id)
        .filter(SourceModel.file.in_(file_paths))
        .group_by(SourceModel.file)
        .all()
    )
    stats_map = {
        row.file: {
            "total": int(row.total or 0),
            "translate": int(row.translate or 0),
            "proofread": int(row.proofread or 0),
        }
        for row in rows
    }

    file_rows = FileModule.query.filter(FileModule.file.in_(file_paths)).all()
    for file_row in file_rows:
        stats = stats_map.get(file_row.file, {"total": 0, "translate": 0, "proofread": 0})
        file_row.total = stats["total"]
        file_row.translate = stats["translate"]
        file_row.proofread = stats["proofread"]
        if clear_stale:
            file_row.stale = 0
            file_row.cache_revision = int(file_row.content_revision or 0)
            file_row.cache_status = CACHE_STATUS_FRESH
            file_row.cache_error = ""
        stats_map[file_row.file] = stats
    session.commit()
    return stats_map


def refresh_progress_by_word_ids(word_ids: List[int]) -> Dict[str, Dict[str, int]]:
    word_ids = sorted({word_id for word_id in word_ids if word_id})
    if not word_ids:
        return {}
    file_rows = (
        session.query(SourceModel.file)
        .filter(SourceModel.word_id.in_(word_ids))
        .distinct()
        .all()
    )
    file_paths = [row[0] for row in file_rows]
    return refresh_progress_by_files(file_paths)


def get_progress_from_jobs(job_list: List[dict]) -> Dict[str, int]:
    total = len(job_list)
    translate = sum(1 for job in job_list if job.get("is_proofread") or job.get("is_key"))
    proofread = sum(1 for job in job_list if job.get("is_proofread"))
    return {
        "total": total,
        "translate": translate,
        "proofread": proofread,
    }


def update_file_progress_from_jobs(split_file: str, job_list: List[dict], cache_revision: Optional[int] = None) -> Dict[str, int]:
    ensure_cache_runtime_schema()
    progress = get_progress_from_jobs(job_list)
    file_row = FileModule.query.filter_by(file=split_file).first()
    if file_row is not None:
        file_row.total = progress["total"]
        file_row.translate = progress["translate"]
        file_row.proofread = progress["proofread"]
        file_row.stale = 0
        file_row.cache_revision = (
            int(cache_revision)
            if cache_revision is not None
            else int(file_row.content_revision or 0)
        )
        file_row.cache_status = CACHE_STATUS_FRESH
        file_row.cache_error = ""
        session.commit()
    return progress


def _replace_missing_job_placeholders(value, job_list: List[dict]):
    replacements = {}
    for job in job_list:
        if job.get("sql_id") or not job.get("uid"):
            continue
        english_text = job.get("en_str")
        if english_text is None:
            continue
        replacements[f"{{!@ {job['uid']}}}"] = english_text
        job["cn_str"] = english_text

    def replace(current):
        if isinstance(current, str):
            for placeholder, english_text in replacements.items():
                current = current.replace(placeholder, english_text)
            return current
        if isinstance(current, list):
            return [replace(item) for item in current]
        if isinstance(current, dict):
            return {key: replace(item) for key, item in current.items()}
        return current

    return replace(value)


def _contains_job_placeholder(value) -> bool:
    if isinstance(value, str):
        return "{!@ " in value
    if isinstance(value, list):
        return any(_contains_job_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_job_placeholder(item) for item in value.values())
    return False


def rebuild_split_file(split_file: str):
    split_path = get_split_file_path(split_file)
    if not os.path.exists(split_path):
        raise FileNotFoundError(split_path)

    file_work_infos = (JsonAnalyser() | JsonGenerator(1)).invoke(
        [split_path], {"metadata": {"mode": "splited"}}
    )
    job_list = []
    cn_obj = None
    for file_work_info in file_work_infos:
        file_work_info.job_list = [j.__dict__ for j in file_work_info.job_list]
        file_work_info.cn_obj = _replace_missing_job_placeholders(
            file_work_info.cn_obj, file_work_info.job_list
        )
        write_file_work_infos(file_work_info, APP_TEMP_PATH)
        job_list = file_work_info.job_list
        cn_obj = file_work_info.cn_obj
    return job_list, cn_obj


def ensure_jobs_cache(split_file: str):
    ensure_cache_runtime_schema()
    cache_lock = _get_job_cache_lock(split_file)
    with cache_lock:
        try:
            cached_job_list = None
            cached_cn_obj = None
            try:
                cached_job_list, cached_cn_obj = _read_jobs_cache_files(split_file)
            except Exception:
                pass

            if is_job_cache_stale(split_file):
                file_row = FileModule.query.filter_by(file=split_file).first()
                target_revision = int(file_row.content_revision or 0) if file_row is not None else None
                mark_file_cache_rebuilding(split_file)
                job_list, cn_obj = rebuild_split_file(split_file)
                if job_list:
                    update_file_progress_from_jobs(split_file, job_list, cache_revision=target_revision)
                    return job_list, cn_obj
                if cached_job_list:
                    logger.warning(f"重建jobs缓存为空，保留已有缓存: {split_file}")
                    mark_files_dirty([split_file], bump_revision=False)
                    return cached_job_list, cached_cn_obj
                if cn_obj is not None:
                    update_file_progress_from_jobs(split_file, job_list, cache_revision=target_revision)
                    return job_list, cn_obj
            job_list, cn_obj = _read_jobs_cache_files(split_file)
            if _contains_job_placeholder(cn_obj):
                cached_job_list, cached_cn_obj = job_list, cn_obj
                file_row = FileModule.query.filter_by(file=split_file).first()
                target_revision = int(file_row.content_revision or 0) if file_row is not None else None
                mark_file_cache_rebuilding(split_file)
                job_list, cn_obj = rebuild_split_file(split_file)
                if job_list:
                    update_file_progress_from_jobs(split_file, job_list, cache_revision=target_revision)
                    return job_list, cn_obj
                if cached_job_list:
                    logger.warning(f"重建jobs缓存为空，保留已有缓存: {split_file}")
                    mark_files_dirty([split_file], bump_revision=False)
                    return cached_job_list, cached_cn_obj
                if cn_obj is not None:
                    return job_list, cn_obj
                job_list, cn_obj = _read_jobs_cache_files(split_file)
            return job_list, cn_obj
        except Exception as exc:
            mark_file_cache_error(split_file, str(exc))
            return _fallback_jobs_cache(split_file, exc)


def rebuild_jobs_by_source(source_file: str):
    split_files = _scan_split_files_by_source(source_file)
    for split_file in split_files:
        rebuild_split_file(split_file)
    return split_files


def refresh_file_runtime(split_file: str, cache_revision: Optional[int] = None):
    ensure_cache_runtime_schema()
    file_row = FileModule.query.filter_by(file=split_file).first()
    target_revision = (
        int(cache_revision)
        if cache_revision is not None
        else (int(file_row.content_revision or 0) if file_row is not None else None)
    )
    mark_file_cache_rebuilding(split_file)
    try:
        job_list, _ = rebuild_split_file(split_file)
        progress = update_file_progress_from_jobs(split_file, job_list, cache_revision=target_revision)
    except Exception as exc:
        mark_file_cache_error(split_file, str(exc))
        raise
    return {split_file: progress}


def _active_cache_job_for_file(file_path: str):
    return (
        FileCacheJobModel.query
        .filter(FileCacheJobModel.file == file_path)
        .filter(FileCacheJobModel.status.in_(["queued", "running"]))
        .order_by(
            case((FileCacheJobModel.status == "queued", 0), else_=1),
            FileCacheJobModel.id.desc(),
        )
        .first()
    )


def _cache_job_dict(job):
    return job.to_dict() if job is not None else {}


def _resolve_flask_app(flask_app=None):
    if flask_app is not None:
        return flask_app
    if has_app_context() and current_app is not None:
        return current_app._get_current_object()
    return None


def enqueue_cache_refresh_jobs(
    file_revisions: Dict[str, int],
    *,
    exclude_files: Optional[List[str]] = None,
    reason: str = "cache-dirty",
    priority: int = 100,
    flask_app=None,
) -> List[Dict[str, object]]:
    ensure_cache_runtime_schema()
    excluded = {
        file_path.strip("/")
        for file_path in (exclude_files or [])
        if file_path
    }
    wanted = {
        (file_path or "").strip("/"): int(revision or 0)
        for file_path, revision in (file_revisions or {}).items()
        if file_path and file_path.strip("/") not in excluded
    }
    if not wanted:
        return []

    queued_jobs = []
    try:
        for file_path, target_revision in sorted(wanted.items()):
            job = _active_cache_job_for_file(file_path)
            if job is not None and job.status == "queued":
                job.target_revision = max(int(job.target_revision or 0), target_revision)
                job.priority = min(int(job.priority or priority), priority)
                job.reason = reason or job.reason
                job.message = "已合并刷新任务"
            elif job is not None and job.status == "running":
                queued = FileCacheJobModel(
                    file=file_path,
                    target_revision=target_revision,
                    reason=reason,
                    priority=priority,
                    status="queued",
                    message="运行中任务之后继续刷新",
                )
                session.add(queued)
                job = queued
            else:
                job = FileCacheJobModel(
                    file=file_path,
                    target_revision=target_revision,
                    reason=reason,
                    priority=priority,
                    status="queued",
                    message="已加入刷新队列",
                )
                session.add(job)
            queued_jobs.append(job)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error(f"缓存刷新任务入队失败: {exc}")
        return []

    app = _resolve_flask_app(flask_app)
    if app is not None:
        dispatch_cache_refresh_jobs(app)
    return [_cache_job_dict(job) for job in queued_jobs]


def dispatch_cache_refresh_jobs(flask_app=None):
    app = _resolve_flask_app(flask_app)
    if app is None:
        return False
    with CACHE_JOB_DISPATCH_LOCK:
        CACHE_JOB_EXECUTOR.submit(_run_cache_refresh_jobs, app)
    return True


def get_cache_refresh_job(job_id: int) -> Optional[Dict[str, object]]:
    ensure_cache_runtime_schema()
    job = FileCacheJobModel.query.get(job_id)
    return _cache_job_dict(job) if job is not None else None


def recover_stale_cache_jobs(timeout_seconds: int = CACHE_JOB_RUNNING_TIMEOUT_SECONDS) -> int:
    ensure_cache_runtime_schema()
    cutoff = datetime.datetime.now() - datetime.timedelta(seconds=timeout_seconds)
    try:
        count = (
            FileCacheJobModel.query
            .filter(FileCacheJobModel.status == "running")
            .filter(FileCacheJobModel.started_at < cutoff)
            .update(
                {
                    FileCacheJobModel.status: "queued",
                    FileCacheJobModel.message: "运行超时，已重新入队",
                },
                synchronize_session=False,
            )
        )
        session.commit()
        return int(count or 0)
    except Exception as exc:
        session.rollback()
        logger.error(f"恢复超时缓存任务失败: {exc}")
        return 0


def _is_mysql_session():
    try:
        return session.get_bind().dialect.name == "mysql"
    except Exception:
        return False


def _cache_file_lock_name(file_path: str) -> str:
    return f"5e-translator:file-cache:{file_path}"[:64]


def _acquire_cache_file_lock(file_path: str) -> bool:
    if not _is_mysql_session():
        return True
    result = session.execute(
        text("SELECT GET_LOCK(:lock_name, :timeout)"),
        {
            "lock_name": _cache_file_lock_name(file_path),
            "timeout": CACHE_JOB_LOCK_TIMEOUT_SECONDS,
        },
    ).scalar()
    return int(result or 0) == 1


def _release_cache_file_lock(file_path: str):
    if not _is_mysql_session():
        return
    try:
        session.execute(
            text("SELECT RELEASE_LOCK(:lock_name)"),
            {"lock_name": _cache_file_lock_name(file_path)},
        )
        session.commit()
    except Exception:
        session.rollback()


def _claim_next_cache_job():
    while True:
        job = (
            FileCacheJobModel.query
            .filter(FileCacheJobModel.status == "queued")
            .order_by(FileCacheJobModel.priority.asc(), FileCacheJobModel.id.asc())
            .first()
        )
        if job is None:
            return None
        updated = (
            FileCacheJobModel.query
            .filter(FileCacheJobModel.id == job.id)
            .filter(FileCacheJobModel.status == "queued")
            .update(
                {
                    FileCacheJobModel.status: "running",
                    FileCacheJobModel.attempts: int(job.attempts or 0) + 1,
                    FileCacheJobModel.message: "刷新中",
                    FileCacheJobModel.started_at: datetime.datetime.now(),
                    FileCacheJobModel.finished_at: None,
                },
                synchronize_session=False,
            )
        )
        session.commit()
        if updated:
            return FileCacheJobModel.query.get(job.id)


def _finish_cache_job(job, status: str, message: str = ""):
    job.status = status
    job.message = str(message or "")[:1024]
    job.finished_at = datetime.datetime.now()
    session.commit()


def _run_cache_refresh_jobs(flask_app):
    with flask_app.app_context():
        recover_stale_cache_jobs()
        while True:
            job = _claim_next_cache_job()
            if job is None:
                break
            has_lock = False
            try:
                has_lock = _acquire_cache_file_lock(job.file)
                if not has_lock:
                    job.status = "queued"
                    job.message = "等待同文件刷新锁"
                    session.commit()
                    break
                file_row = FileModule.query.filter_by(file=job.file).first()
                if file_row is None:
                    _finish_cache_job(job, "success", "文件已不存在")
                    continue
                current_revision = int(file_row.content_revision or 0)
                target_revision = max(int(job.target_revision or 0), current_revision)
                if (
                    int(file_row.cache_revision or 0) >= target_revision
                    and (file_row.cache_status or CACHE_STATUS_FRESH) == CACHE_STATUS_FRESH
                ):
                    _finish_cache_job(job, "success", "缓存已由其他任务刷新")
                    continue
                refresh_file_runtime(job.file, cache_revision=target_revision)
                _finish_cache_job(job, "success", "刷新完成")
            except Exception as exc:
                logger.error(f"后台刷新文件缓存失败: {job.file}, {exc}")
                mark_file_cache_error(job.file, str(exc))
                _finish_cache_job(job, "error", str(exc))
            finally:
                if has_lock:
                    _release_cache_file_lock(job.file)
        db.session.remove()


def _copy_single_split_file(temp_split_dir: str, split_file: str) -> bool:
    temp_split_file = os.path.join(temp_split_dir, split_file)
    if not os.path.exists(temp_split_file):
        return False
    target_split_file = get_split_file_path(split_file)
    os.makedirs(os.path.dirname(target_split_file), exist_ok=True)
    shutil.copy2(temp_split_file, target_split_file)
    return True


def sync_split_file(split_file: str, rebuild_jobs: bool = True) -> Dict[str, object]:
    split_file = split_file.strip("/")
    file_row = FileModule.query.filter_by(file=split_file).first()
    if file_row is None:
        raise FileNotFoundError(f"{split_file} not found in file table")
    if not file_row.source_file:
        raise FileNotFoundError(f"{split_file} has no source_file")

    source_file = file_row.source_file
    source_was_stale = is_source_file_stale(source_file, split_file)
    source_path = get_source_file_path(source_file)
    if not os.path.exists(source_path):
        checked_roots = [root for root in SOURCE_ROOTS if root]
        raise FileNotFoundError(f"{source_file} not found under source roots: {checked_roots}")

    with tempfile.TemporaryDirectory(prefix="split-sync-", dir="/tmp") as temp_dir:
        temp_source_path = os.path.join(temp_dir, source_file)
        temp_split_dir = os.path.join(temp_dir, "split-data")
        temp_combine_dir = os.path.join(temp_dir, "combine-info")
        os.makedirs(os.path.dirname(temp_source_path), exist_ok=True)
        shutil.copy2(source_path, temp_source_path)
        try:
            split_origin_files(temp_dir, temp_split_dir, temp_combine_dir)
        except Exception as exc:
            logger.error(f"按拆分文件同步失败: {split_file}, {exc}")
            raise RuntimeError(str(exc) or "split failed")

        exists_after_split = _copy_single_split_file(temp_split_dir, split_file)

    if not exists_after_split:
        _delete_split_file(split_file)
        FileModule.query.filter_by(file=split_file).delete(synchronize_session=False)
        session.commit()
        return {
            "source_file": source_file,
            "file": split_file,
            "removed": True,
        }

    try:
        dirty_revisions = mark_files_dirty([split_file])
        if rebuild_jobs:
            refresh_file_runtime(split_file, cache_revision=dirty_revisions.get(split_file))
        else:
            file_work_infos = list((JsonAnalyser()).invoke(
                [get_split_file_path(split_file)], {"metadata": {"mode": "splited"}}
            ))
            job_list = []
            for file_work_info in file_work_infos:
                job_list = [j.__dict__ for j in file_work_info.job_list]
            update_file_progress_from_jobs(split_file, job_list)
    except Exception as exc:
        logger.error(f"重建拆分文件缓存失败: {split_file}, {exc}")
        raise

    if source_was_stale:
        sibling_files = [
            row.file for row in FileModule.query
            .filter(FileModule.source_file == source_file)
            .filter(FileModule.file != split_file)
            .all()
        ]
        sibling_revisions = mark_files_dirty(sibling_files)
        enqueue_cache_refresh_jobs(
            sibling_revisions,
            reason="source-sync-sibling",
            priority=80,
        )
    return {
        "source_file": source_file,
        "file": split_file,
        "removed": False,
    }


def sync_source_file(source_file: str, rebuild_jobs: bool = True) -> Dict[str, List[str]]:
    source_file = normalize_source_file(source_file)
    source_path = get_source_file_path(source_file)
    if not os.path.exists(source_path):
        checked_roots = [root for root in SOURCE_ROOTS if root]
        raise FileNotFoundError(f"{source_file} not found under source roots: {checked_roots}")

    old_files = [row.file for row in FileModule.query.filter_by(source_file=source_file).all()]

    with tempfile.TemporaryDirectory(prefix="split-sync-", dir="/tmp") as temp_dir:
        temp_source_path = os.path.join(temp_dir, source_file)
        temp_split_dir = os.path.join(temp_dir, "split-data")
        temp_combine_dir = os.path.join(temp_dir, "combine-info")
        os.makedirs(os.path.dirname(temp_source_path), exist_ok=True)
        shutil.copy2(source_path, temp_source_path)
        try:
            split_origin_files(temp_dir, temp_split_dir, temp_combine_dir)
        except Exception as exc:
            logger.error(f"按源文件同步拆分失败: {source_file}, {exc}")
            raise RuntimeError(str(exc) or "split failed")

        new_files = _scan_split_files_by_source(source_file, temp_split_dir)
        for split_file in new_files:
            temp_split_file = os.path.join(temp_split_dir, split_file)
            target_split_file = get_split_file_path(split_file)
            os.makedirs(os.path.dirname(target_split_file), exist_ok=True)
            shutil.copy2(temp_split_file, target_split_file)

    removed_files = [rel_path for rel_path in old_files if rel_path not in new_files]
    for rel_path in removed_files:
        _delete_split_file(rel_path)
    if removed_files:
        FileModule.query.filter(FileModule.file.in_(removed_files)).delete(synchronize_session=False)
        session.commit()

    for split_file in new_files:
        try:
            dirty_revisions = mark_files_dirty([split_file])
            if rebuild_jobs:
                refresh_file_runtime(split_file, cache_revision=dirty_revisions.get(split_file))
            else:
                list((JsonAnalyser()).invoke(
                    [get_split_file_path(split_file)], {"metadata": {"mode": "splited"}}
                ))
        except Exception as exc:
            logger.error(f"重建拆分文件缓存失败: {split_file}, {exc}")
            raise
    if not rebuild_jobs:
        refresh_progress_by_files(new_files)
    return {"source_file": source_file, "files": new_files, "removed_files": removed_files}


def sync_source_path(source_path: str, rebuild_jobs: bool = True) -> Dict[str, object]:
    source_files = list_source_json_files(source_path)
    if not source_files:
        raise FileNotFoundError(f"No JSON files found under source path: {source_path}")
    if len(source_files) == 1 and not os.path.isdir(get_source_file_path(normalize_source_file(source_path))):
        return sync_source_file(source_files[0], rebuild_jobs=rebuild_jobs)

    results = []
    for index, source_file in enumerate(source_files, 1):
        logger.info(f"同步源文件目录进度: {index}/{len(source_files)} {source_file}")
        results.append(sync_source_file(source_file, rebuild_jobs=rebuild_jobs))
    return {
        "source_path": normalize_source_file(source_path),
        "source_files": source_files,
        "synced": len(results),
        "results": results,
    }


def _run_sync_task(task_id: str, flask_app, source_file: str):
    with flask_app.app_context():
        with SYNC_TASK_LOCK:
            task = SYNC_TASKS.get(task_id)
            if task is None:
                return
            task["status"] = "running"
            task["message"] = "同步中"
            rebuild_jobs = task["rebuild_jobs"]
            task["started_at"] = time.time()
        try:
            split_file = task["file"]
            result = sync_split_file(split_file, rebuild_jobs=rebuild_jobs)
            with SYNC_TASK_LOCK:
                task = SYNC_TASKS.get(task_id)
                if task is not None:
                    task["status"] = "success"
                    task["message"] = "同步完成"
                    task["result"] = result
                    task["rebuild_jobs"] = rebuild_jobs
                    task["source_mtime"] = get_source_mtime(task["source_file"])
                    task["completed_at"] = time.time()
        except Exception as exc:
            logger.error(f"后台同步失败: {task.get('file')}, {exc}")
            with SYNC_TASK_LOCK:
                task = SYNC_TASKS.get(task_id)
                if task is not None:
                    task["status"] = "error"
                    task["message"] = str(exc)
                    task["completed_at"] = time.time()
        finally:
            db.session.remove()


def enqueue_sync_split_file(split_file: str, flask_app, rebuild_jobs: bool = True) -> Dict[str, object]:
    split_file = split_file.strip("/")
    file_row = FileModule.query.filter_by(file=split_file).first()
    if file_row is None or not file_row.source_file:
        raise FileNotFoundError(f"{split_file} has no source_file")
    source_file = file_row.source_file
    current_source_mtime = get_source_mtime(source_file)
    with SYNC_TASK_LOCK:
        existing_task_id = SYNC_TASKS_BY_FILE.get(split_file)
        if existing_task_id:
            existing_task = SYNC_TASKS.get(existing_task_id)
            if existing_task:
                if existing_task["status"] == "queued":
                    if rebuild_jobs and not existing_task.get("rebuild_jobs"):
                        existing_task["rebuild_jobs"] = True
                        existing_task["message"] = "已升级为完整同步任务"
                    return dict(existing_task)
                if existing_task["status"] == "running":
                    if rebuild_jobs and not existing_task.get("rebuild_jobs"):
                        existing_task["message"] = "已有同步任务执行中"
                    return dict(existing_task)
                if existing_task["status"] == "success":
                    completed_at = existing_task.get("completed_at", 0)
                    task_source_mtime = existing_task.get("source_mtime", 0)
                    is_recent = (time.time() - completed_at) <= SYNC_SUCCESS_COOLDOWN_SECONDS
                    if current_source_mtime <= task_source_mtime and is_recent:
                        return dict(existing_task)

        task_id = uuid.uuid4().hex
        task = {
            "task_id": task_id,
            "file": split_file,
            "source_file": source_file,
            "status": "queued",
            "message": "已加入同步队列",
            "result": None,
            "rebuild_jobs": rebuild_jobs,
            "source_mtime": current_source_mtime,
            "completed_at": 0,
        }
        SYNC_TASKS[task_id] = task
        SYNC_TASKS_BY_FILE[split_file] = task_id

    SYNC_EXECUTOR.submit(_run_sync_task, task_id, flask_app, source_file)
    return dict(task)


def get_sync_task(task_id: str) -> Optional[Dict[str, object]]:
    with SYNC_TASK_LOCK:
        task = SYNC_TASKS.get(task_id)
        if task is None:
            return None
        return dict(task)
