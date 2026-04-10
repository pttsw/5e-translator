import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from sqlalchemy import case, func

from app.core.spliter import split_origin_files
from app.core.translator import JsonAnalyser, JsonGenerator
from app.core.utils.loader import write_file_work_infos
from app.model import FileModule, SourceModel, WordsModel, db, session
from config import APP_TEMP_PATH, EN_PATH, HOMEBREW_EN_PATH, SPLITED_5ETOOLS_EN_PATH, UA_EN_PATH, logger

SOURCE_ROOTS = [EN_PATH, HOMEBREW_EN_PATH, UA_EN_PATH]
SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-sync")
SYNC_TASK_LOCK = threading.Lock()
SYNC_TASKS = {}
SYNC_TASKS_BY_FILE = {}
SYNC_SUCCESS_COOLDOWN_SECONDS = 30


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


def get_job_cache_paths(rel_path: str):
    return (
        os.path.join(APP_TEMP_PATH, rel_path + ".jobs"),
        os.path.join(APP_TEMP_PATH, rel_path),
    )


def update_job_cache_word(split_file: str, word_id: int, **updates) -> bool:
    if not split_file or not word_id:
        return False
    job_path, _ = get_job_cache_paths(split_file)
    if not os.path.exists(job_path):
        return False
    try:
        with open(job_path, "r") as job_file:
            job_list = json.load(job_file)
    except Exception as exc:
        logger.error(f"读取jobs缓存失败: {split_file}, {exc}")
        return False

    changed = False
    for job in job_list:
        if int(job.get("sql_id") or 0) != int(word_id):
            continue
        for key, value in updates.items():
            if value is not None:
                job[key] = value
                changed = True

    if not changed:
        return False

    try:
        with open(job_path, "w") as job_file:
            json.dump(job_list, job_file, ensure_ascii=False, indent=2)
        update_file_progress_from_jobs(split_file, job_list)
        return True
    except Exception as exc:
        logger.error(f"写入jobs缓存失败: {split_file}, {exc}")
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


def is_job_cache_stale(split_file: str) -> bool:
    split_path = get_split_file_path(split_file)
    job_path, cn_path = get_job_cache_paths(split_file)
    if not os.path.exists(split_path):
        return False
    if not os.path.exists(job_path) or not os.path.exists(cn_path):
        return True
    split_mtime = os.path.getmtime(split_path)
    return os.path.getmtime(job_path) < split_mtime or os.path.getmtime(cn_path) < split_mtime


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


def mark_files_stale(file_paths: List[str]):
    file_paths = sorted({file_path for file_path in file_paths if file_path})
    if not file_paths:
        return
    FileModule.query.filter(FileModule.file.in_(file_paths)).update(
        {FileModule.stale: 1},
        synchronize_session=False,
    )
    session.commit()


def clear_file_stale(file_path: str):
    if not file_path:
        return
    FileModule.query.filter_by(file=file_path).update(
        {FileModule.stale: 0},
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
    file_paths = sorted({file_path for file_path in file_paths if file_path})
    if not file_paths:
        return {}

    rows = (
        session.query(
            SourceModel.file.label("file"),
            func.count(func.distinct(SourceModel.word_id)).label("total"),
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


def update_file_progress_from_jobs(split_file: str, job_list: List[dict]) -> Dict[str, int]:
    progress = get_progress_from_jobs(job_list)
    file_row = FileModule.query.filter_by(file=split_file).first()
    if file_row is not None:
        file_row.total = progress["total"]
        file_row.translate = progress["translate"]
        file_row.proofread = progress["proofread"]
        file_row.stale = 0
        session.commit()
    return progress


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
        file_work_info.job_list = [j.__dict__ for j in file_work_info.job_list if j.sql_id]
        write_file_work_infos(file_work_info, APP_TEMP_PATH)
        job_list = file_work_info.job_list
        cn_obj = file_work_info.cn_obj
    return job_list, cn_obj


def ensure_jobs_cache(split_file: str):
    if is_job_cache_stale(split_file):
        rebuild_split_file(split_file)
    job_path, cn_path = get_job_cache_paths(split_file)
    with open(job_path, "r") as job_file:
        job_list = json.load(job_file)
    with open(cn_path, "r") as cn_file:
        cn_obj = json.load(cn_file)
    return job_list, cn_obj


def rebuild_jobs_by_source(source_file: str):
    split_files = _scan_split_files_by_source(source_file)
    for split_file in split_files:
        rebuild_split_file(split_file)
    return split_files


def refresh_file_runtime(split_file: str):
    job_list, _ = rebuild_split_file(split_file)
    progress = update_file_progress_from_jobs(split_file, job_list)
    clear_file_stale(split_file)
    return {split_file: progress}


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
        if rebuild_jobs:
            refresh_file_runtime(split_file)
        else:
            file_work_infos = list((JsonAnalyser()).invoke(
                [get_split_file_path(split_file)], {"metadata": {"mode": "splited"}}
            ))
            job_list = []
            for file_work_info in file_work_infos:
                job_list = [j.__dict__ for j in file_work_info.job_list if j.sql_id]
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
        mark_files_stale(sibling_files)
    return {
        "source_file": source_file,
        "file": split_file,
        "removed": False,
    }


def sync_source_file(source_file: str, rebuild_jobs: bool = True) -> Dict[str, List[str]]:
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
            if rebuild_jobs:
                rebuild_split_file(split_file)
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
