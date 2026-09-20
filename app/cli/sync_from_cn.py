"""Import hand-written Chinese JSON translations into the translation database."""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.core.translator import JsonAnalyser, JsonGenerator
from app.core.utils import Job, check_prefix, check_suffix, parse_custom_format, split_string


_JOB_MARKER_RE = re.compile(r"\{!@ ([^}]+)\}")
_EXACT_USAGE_PREFIX = "__SYNC_CN_EXACT__"
_QUOTE_STYLE_TRANSLATION = str.maketrans({
    "“": '"', "”": '"', "„": '"', "＂": '"',
    "「": '"', "」": '"', "『": '"', "』": '"',
    "‘": "'", "’": "'", "‚": "'", "＇": "'",
})


@dataclass
class AlignedLeaf:
    path: str
    manual_value: str
    job_uids: List[str]


@dataclass
class AlignmentResult:
    translations: Dict[str, str] = field(default_factory=dict)
    leaves: List[AlignedLeaf] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class SyncConflict:
    job: Job
    database_value: str
    manual_value: str
    decision: str = ""


@dataclass
class SyncSummary:
    added: int = 0
    updated: int = 0
    kept_database: int = 0
    matched: int = 0
    conflicts: int = 0
    verified: bool = False
    processed_files: int = 0
    skipped_files: int = 0
    failed_files: List[Tuple[str, str]] = field(default_factory=list)


def _json_path(parent: str, component) -> str:
    if isinstance(component, int):
        return f"{parent}[{component}]"
    return f"{parent}/{component}" if parent else f"/{component}"


def _extract_marker_values(template: str, manual: str) -> Optional[List[Tuple[str, str]]]:
    """Extract values occupying analyser job markers from one translated string."""
    matches = list(_JOB_MARKER_RE.finditer(template))
    if not matches:
        return []
    if len(matches) == 1 and matches[0].span() == (0, len(template)):
        return [(matches[0].group(1), manual)]

    pattern_parts = ["^"]
    cursor = 0
    for index, match in enumerate(matches):
        pattern_parts.append(re.escape(template[cursor:match.start()]))
        pattern_parts.append("(.*?)" if index < len(matches) - 1 else "(.*)")
        cursor = match.end()
    pattern_parts.append(re.escape(template[cursor:]))
    pattern_parts.append("$")
    extracted = re.match("".join(pattern_parts), manual, flags=re.DOTALL)
    if extracted is None:
        return None
    return [
        (match.group(1), value)
        for match, value in zip(matches, extracted.groups())
    ]


def align_manual_translations(template_obj, manual_obj, jobs: Sequence[Job]) -> AlignmentResult:
    """Align analyser markers with the corresponding strings in a hand-written JSON."""
    result = AlignmentResult()
    jobs_by_uid = {job.uid: job for job in jobs}
    jobs_by_key_path: Dict[str, List[Job]] = {}
    for job in jobs:
        jobs_by_key_path.setdefault(job.key_path, []).append(job)

    def align_tag_children(parent_job: Job, manual_value: str, path: str) -> None:
        """Align analyser jobs created for the values inside 5etools tags."""
        en_tags, en_values, en_valid = parse_custom_format(parent_job.en_str, False)
        cn_tags, cn_values, cn_valid = parse_custom_format(manual_value, False)
        if not en_tags:
            return
        if not en_valid or not cn_valid or len(en_values) != len(cn_values):
            return

        cn_values_by_tag: Dict[str, List[str]] = {}
        for tag, value in zip(cn_tags, cn_values):
            cn_values_by_tag.setdefault(tag, []).append(value)
        tag_offsets: Dict[str, int] = {}

        for index, (tag, en_value) in enumerate(zip(en_tags, en_values)):
            tag_offset = tag_offsets.get(tag, 0)
            matching_cn_values = cn_values_by_tag.get(tag, [])
            if tag_offset >= len(matching_cn_values):
                return
            cn_value = matching_cn_values[tag_offset]
            tag_offsets[tag] = tag_offset + 1
            child_path = f"{parent_job.key_path}/{tag}[{index}]"
            candidates = jobs_by_key_path.get(child_path, [])
            en_parts = split_string(en_value)
            cn_parts = split_string(cn_value)
            if len(en_parts) != len(cn_parts):
                continue
            for en_part, cn_part in zip(en_parts, cn_parts):
                en_without_prefix, _ = check_prefix(en_part)
                en_pure, _ = check_suffix(en_without_prefix)
                cn_without_prefix, _ = check_prefix(cn_part)
                cn_pure, _ = check_suffix(cn_without_prefix)
                child = next(
                    (job for job in candidates if job.en_str == en_pure), None
                )
                if child is None:
                    continue
                previous = result.translations.get(child.uid)
                if previous is not None and previous != cn_pure:
                    # A single analyser child can occur in multiple fields of
                    # one tag (for example, both the entity name and display
                    # text) while the hand-written JSON translates those
                    # fields differently. The exact parent translation remains
                    # authoritative; keep the first stable child mapping so
                    # this lossy database representation does not abort sync.
                    continue
                result.translations[child.uid] = cn_pure

    def walk(template, manual, path=""):
        if isinstance(template, dict):
            if not isinstance(manual, dict):
                result.errors.append(f"{path or '/'}: 英文为对象，但中文不是对象")
                return
            for key, value in template.items():
                if key not in manual:
                    if _contains_marker(value):
                        result.errors.append(f"{_json_path(path, key)}: 中文文件缺少字段")
                    continue
                walk(value, manual[key], _json_path(path, key))
            return
        if isinstance(template, list):
            if not isinstance(manual, list):
                result.errors.append(f"{path or '/'}: 英文为数组，但中文不是数组")
                return
            if len(template) != len(manual):
                result.errors.append(
                    f"{path or '/'}: 数组长度不一致（英文 {len(template)}，中文 {len(manual)}）"
                )
            for index, value in enumerate(template[: len(manual)]):
                walk(value, manual[index], _json_path(path, index))
            return
        if not isinstance(template, str) or not _JOB_MARKER_RE.search(template):
            return
        if not isinstance(manual, str):
            result.errors.append(f"{path or '/'}: 待翻译英文为字符串，但中文不是字符串")
            return
        pairs = _extract_marker_values(template, manual)
        if pairs is None:
            result.errors.append(f"{path or '/'}: 无法按固定文本边界对齐多个翻译片段")
            return
        uids = []
        for uid, value in pairs:
            if uid not in jobs_by_uid:
                result.errors.append(f"{path or '/'}: 找不到翻译任务 {uid}")
                continue
            previous = result.translations.get(uid)
            if previous is not None and previous != value:
                result.errors.append(f"{path or '/'}: 同一翻译任务对应了不同中文")
                continue
            result.translations[uid] = value
            uids.append(uid)
            align_tag_children(jobs_by_uid[uid], value, path)
        if uids:
            result.leaves.append(AlignedLeaf(path, manual, uids))

    walk(template_obj, manual_obj)
    return result


def _contains_marker(value) -> bool:
    if isinstance(value, str):
        return _JOB_MARKER_RE.search(value) is not None
    if isinstance(value, list):
        return any(_contains_marker(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_marker(item) for item in value.values())
    return False


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def resolve_cn_path(en_path: str, cn_path: Optional[str] = None) -> str:
    """在未指定中文文件时，将英文路径中的 data-bak 目录替换为 data。"""
    if cn_path:
        return cn_path

    parts = list(Path(en_path).parts)
    try:
        data_bak_index = parts.index("data-bak")
    except ValueError as exc:
        raise ValueError(
            "未指定 --cn，且 --en 路径中不包含 data-bak 目录，"
            "无法自动推导中文文件"
        ) from exc

    parts[data_bak_index] = "data"
    candidate = Path(*parts)
    source_is_directory = Path(en_path).is_dir()
    candidate_exists = (
        candidate.is_dir() if source_is_directory else candidate.is_file()
    )
    if not candidate_exists:
        target_kind = "目录" if source_is_directory else "文件"
        raise FileNotFoundError(
            f"未指定 --cn，自动推导的中文{target_kind}不存在: {candidate}"
        )
    target_kind = "目录" if source_is_directory else "文件"
    print(
        f"未指定 --cn，自动使用中文{target_kind}: {candidate}",
        flush=True,
    )
    return str(candidate)


def _value_at_path(obj, path: str):
    """Read the simple paths emitted by align_manual_translations."""
    current = obj
    for key, index in re.findall(r"/([^/\[]+)|\[(\d+)\]", path):
        current = current[int(index)] if index else current[key]
    return current


def _first_difference(left, right, path="") -> str:
    if type(left) is not type(right):
        return f"{path or '/'}: 类型不同 {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if left.keys() != right.keys():
            return f"{path or '/'}: 字段不同 {list(left.keys())} != {list(right.keys())}"
        for key in left:
            difference = _first_difference(left[key], right[key], _json_path(path, key))
            if difference:
                return difference
        return ""
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path or '/'}: 数组长度不同 {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = _first_difference(left_item, right_item, _json_path(path, index))
            if difference:
                return difference
        return ""
    if left != right:
        return f"{path or '/'}: {left!r} != {right!r}"
    return ""


def _prompt_conflict(conflict: SyncConflict, index: int, total: int) -> str:
    print(f"\n冲突 {index}/{total}")
    print(f"位置: {conflict.job.rel_path} {conflict.job.key_path or conflict.job.uid}")
    print(f"英文: {conflict.job.en_str}")
    print(f"库中: {conflict.database_value}")
    proofread_status = "已校对" if conflict.job.is_proofread else "未校对"
    print(f"库中校对状态: {proofread_status}")
    print(f"文件: {conflict.manual_value}")
    while True:
        choice = input(
            "请选择：[a] 新增独立译文  [m] 修改现有译文  [k] 以库中为准  [q] 退出："
        ).strip().lower()
        aliases = {
            "a": "add", "add": "add", "新增": "add",
            "m": "modify", "modify": "modify", "修改": "modify",
            "k": "keep", "keep": "keep", "库": "keep", "以库中为准": "keep",
        }
        if choice in aliases:
            return aliases[choice]
        if choice in {"q", "quit", "退出"}:
            raise KeyboardInterrupt("用户取消同步，未执行本次同步写入")
        print("输入无效，请输入 a、m、k 或 q。")


def _mark_exact_usage(job: Job) -> None:
    label = job.context_label or ""
    if not label.startswith(_EXACT_USAGE_PREFIX):
        job.context_label = f"{_EXACT_USAGE_PREFIX}|{label}"
    job.exact_translation = True


def _differs_only_by_quote_style(left: str, right: str) -> bool:
    """仅在单/双引号的字形样式不同时返回 True。"""
    if not isinstance(left, str) or not isinstance(right, str) or left == right:
        return False
    return left.translate(_QUOTE_STYLE_TRANSLATION) == right.translate(
        _QUOTE_STYLE_TRANSLATION
    )


def _tag_content_skeleton(value: str) -> Optional[str]:
    """将每个完整的外层 {@...} 替换为占位符，保留标签外文本。"""
    parts = []
    cursor = 0
    tag_count = 0
    while cursor < len(value):
        start = value.find("{@", cursor)
        if start == -1:
            parts.append(value[cursor:])
            break
        parts.append(value[cursor:start])
        depth = 1
        index = start + 2
        while index < len(value) and depth:
            if value[index] == "{":
                depth += 1
            elif value[index] == "}":
                depth -= 1
            index += 1
        if depth:
            return None
        parts.append("{#SYNC_TAG#}")
        tag_count += 1
        cursor = index
    if tag_count == 0:
        return None
    return "".join(parts)


def _differs_only_inside_tags(left: str, right: str) -> bool:
    """仅在一个或多个 {@...} 内部内容不同时返回 True。"""
    if not isinstance(left, str) or not isinstance(right, str) or left == right:
        return False
    left_skeleton = _tag_content_skeleton(left)
    right_skeleton = _tag_content_skeleton(right)
    return left_skeleton is not None and left_skeleton == right_skeleton


def _print_database_progress(stage: str, current: int, total: int) -> None:
    stages = {
        "lookup": (1, "检查已有译文"),
        "insert": (2, "新增译文"),
        "update": (3, "修改译文"),
        "usage": (4, "绑定翻译位置"),
        "commit": (5, "提交事务"),
    }
    index, label = stages[stage]
    if total > 0:
        detail = f"{current}/{total}"
    else:
        detail = "0"
    print(f"写入数据库 [{index}/5] {label}：{detail}", flush=True)


def _generate(info, mode: str):
    generator = JsonGenerator(1)
    return next(generator.invoke([info], config={"metadata": {
        "mode": mode,
        "generator_update_db": False,
    }})).cn_obj


def sync_from_cn(
    en_path: str,
    cn_path: Optional[str] = None,
    mode: str = "5et",
    decision_provider: Optional[Callable[[SyncConflict, int, int], str]] = None,
    source_root: Optional[str] = None,
) -> SyncSummary:
    """Synchronize one English/Chinese JSON pair and verify a fresh DB-only generation."""
    cn_path = resolve_cn_path(en_path, cn_path)
    manual_obj = _read_json(cn_path)
    analyser = JsonAnalyser(
        source_root=source_root, read_only=True, preserve_job_markers=True,
    )
    info = next(analyser.invoke([en_path], config={"metadata": {
        "mode": mode,
        "force_title": False,
    }}), None)
    if info is None:
        raise RuntimeError("英文文件未产生可同步的分析结果，请检查路径与 --mode")

    alignment = align_manual_translations(info.json_obj, manual_obj, info.job_list)
    if alignment.errors:
        details = "\n".join(f"- {message}" for message in alignment.errors[:20])
        suffix = "" if len(alignment.errors) <= 20 else f"\n- 另有 {len(alignment.errors) - 20} 项"
        raise ValueError(f"中英文 JSON 无法安全对齐：\n{details}{suffix}")

    summary = SyncSummary()
    jobs_by_uid = {job.uid: job for job in info.job_list}
    conflicts: List[SyncConflict] = []
    missing: List[Tuple[Job, str]] = []
    matching: List[Tuple[Job, str]] = []
    expected_values: Dict[str, str] = {}

    for uid, manual_value in alignment.translations.items():
        job = jobs_by_uid[uid]
        # tag-only fallback 只证明可见文本有译文，不代表数据库中
        # 存在当前完整英文。同步时应新增完整带标签记录，而非报冲突。
        if job.sql_id is None or getattr(job, "tag_sync_required", False):
            job.tag_sync_required = False
            job.sql_id = None
            missing.append((job, manual_value))
            expected_values[uid] = manual_value
        elif job.cn_str == manual_value:
            summary.matched += 1
            matching.append((job, manual_value))
            expected_values[uid] = manual_value
        else:
            conflict = SyncConflict(job, job.cn_str, manual_value)
            if (
                manual_value == job.en_str
                or _differs_only_by_quote_style(job.cn_str, manual_value)
            ):
                conflict.decision = "keep"
                expected_values[uid] = job.cn_str
            elif _differs_only_inside_tags(job.cn_str, manual_value):
                conflict.decision = "auto_modify"
                expected_values[uid] = manual_value
            conflicts.append(conflict)

    summary.conflicts = len(conflicts)
    pending_conflicts = [conflict for conflict in conflicts if not conflict.decision]
    if pending_conflicts and decision_provider is None and not sys.stdin.isatty():
        raise RuntimeError("发现译文冲突，但当前不是交互终端；请在终端中重新运行以逐条确认")
    provider = decision_provider or _prompt_conflict
    modified_values_by_id = {}
    for index, conflict in enumerate(conflicts, start=1):
        if conflict.decision == "keep":
            continue
        word_id = conflict.job.sql_id
        if word_id in modified_values_by_id:
            conflict.database_value = modified_values_by_id[word_id]
            if conflict.database_value == conflict.manual_value:
                # 前面已确认修改同一 words 记录，本位置将自然
                # 获得相同译文，无需再让用户重复确认。
                conflict.decision = "modify"
                expected_values[conflict.job.uid] = conflict.manual_value
                continue
            # 同一 ID 前面已确认为另一个译文，再次修改会破坏
            # 已确认位置；即使本项原本仅标签内容不同，也必须人工决定。
            if conflict.decision == "auto_modify":
                conflict.decision = ""
        if conflict.decision == "auto_modify":
            conflict.decision = "modify"
            modified_values_by_id[word_id] = conflict.manual_value
            expected_values[conflict.job.uid] = conflict.manual_value
            continue
        while True:
            decision = provider(conflict, index, len(conflicts))
            if decision not in {"add", "modify", "keep"}:
                raise ValueError(f"未知冲突决定: {decision}")
            previous_value = modified_values_by_id.get(word_id)
            if (
                decision == "modify"
                and previous_value is not None
                and previous_value != conflict.manual_value
            ):
                message = (
                    "同一数据库记录在不同位置对应不同手工译文，不能同时“修改”；"
                    "本项请选择“新增”或“以库中为准”"
                )
                if decision_provider is not None:
                    raise ValueError(message)
                print(message)
                continue
            break
        conflict.decision = decision
        if decision == "modify":
            modified_values_by_id[word_id] = conflict.manual_value
        expected_values[conflict.job.uid] = (
            conflict.database_value if decision == "keep" else conflict.manual_value
        )

    # Build the exact expected output before mutating the database.
    exact_uids = {job.uid for job, _ in missing}
    new_uids = set(exact_uids)
    matching_uids = {job.uid for job, _ in matching}
    exact_uids.update(matching_uids)
    exact_uids.update(
        conflict.job.uid
        for conflict in conflicts
        if conflict.decision in {"add", "modify"}
    )
    new_uids.update(
        conflict.job.uid
        for conflict in conflicts
        if conflict.decision == "add"
    )
    for job in info.job_list:
        if job.uid in expected_values:
            job.cn_str = expected_values[job.uid]
        if job.uid in exact_uids:
            if job.uid in new_uids:
                job.is_proofread = False
            _mark_exact_usage(job)
    expected_obj = _generate(copy.deepcopy(info), mode)

    put_items = []
    update_items = []
    for job, manual_value in matching:
        _mark_exact_usage(job)
        put_items.append({
            "en": job.en_str,
            "cn": manual_value,
            "rel_f": job.rel_path,
            "proofread": bool(job.is_proofread),
            "tag": job.tag,
            "job_context": job,
        })
    for job, manual_value in missing:
        _mark_exact_usage(job)
        put_items.append({
            "en": job.en_str,
            "cn": manual_value,
            "rel_f": job.rel_path,
            "proofread": False,
            "tag": job.tag,
            "job_context": job,
        })
    summary.added += len(missing)
    updated_word_ids = set()
    for conflict in conflicts:
        job = conflict.job
        if conflict.decision == "keep":
            summary.kept_database += 1
            continue
        _mark_exact_usage(job)
        if conflict.decision == "add":
            put_items.append({
                "en": job.en_str,
                "cn": conflict.manual_value,
                "rel_f": job.rel_path,
                "proofread": False,
                "tag": job.tag,
                "job_context": job,
            })
            summary.added += 1
        else:
            update_items.append({
                "sql_id": job.sql_id,
                "en": job.en_str,
                "cn": conflict.manual_value,
                "rel_f": job.rel_path,
                "proofread": bool(job.is_proofread),
                "tag": job.tag,
                "job_context": job,
            })
            if job.sql_id not in updated_word_ids:
                updated_word_ids.add(job.sql_id)
                summary.updated += 1

    db = analyser.dictionary
    if not db.apply_sync_batch(
        put_items, update_items, progress_callback=_print_database_progress,
    ):
        raise RuntimeError("批量写入同步结果失败")

    # A fresh analysis is the important test: it proves normal translate can reproduce it.
    verify_analyser = JsonAnalyser(
        source_root=source_root, read_only=True, preserve_job_markers=True,
    )
    verify_info = next(verify_analyser.invoke([en_path], config={"metadata": {
        "mode": mode,
        "force_title": False,
    }}), None)
    if verify_info is None:
        raise RuntimeError("数据库写入后重新分析英文文件失败")
    actual_obj = _generate(verify_info, mode)
    if actual_obj != expected_obj:
        difference = _first_difference(expected_obj, actual_obj)
        raise RuntimeError(
            "同步已写库，但重新执行翻译未生成相同结果；"
            f"请勿覆盖中文文件并检查日志。首个差异：{difference}"
        )

    keep_uids = {item.job.uid for item in conflicts if item.decision == "keep"}
    for leaf in alignment.leaves:
        if keep_uids.intersection(leaf.job_uids):
            continue
        actual_value = _value_at_path(actual_obj, leaf.path)
        if actual_value != leaf.manual_value:
            raise RuntimeError(
                f"翻译流程未能复现手工中文字段: {leaf.path}；"
                f"文件={leaf.manual_value!r}；生成={actual_value!r}"
            )

    summary.verified = True
    summary.processed_files = 1
    return summary


def sync_from_cn_path(
    en_path: str,
    cn_path: Optional[str] = None,
    mode: str = "5et",
    decision_provider: Optional[Callable[[SyncConflict, int, int], str]] = None,
    source_root: Optional[str] = None,
) -> SyncSummary:
    """同步单个 JSON，或递归同步目录中所有可配对的 JSON。"""
    en_root = Path(en_path)
    if en_root.is_file():
        return sync_from_cn(
            str(en_root), cn_path, mode=mode,
            decision_provider=decision_provider, source_root=source_root,
        )
    if not en_root.is_dir():
        raise FileNotFoundError(f"英文路径不存在: {en_root}")

    resolved_cn_root = Path(resolve_cn_path(str(en_root), cn_path))
    if not resolved_cn_root.is_dir():
        raise ValueError(
            f"--en 是目录时，--cn 也必须是目录: {resolved_cn_root}"
        )

    en_files = sorted(path for path in en_root.rglob("*.json") if path.is_file())
    if not en_files:
        raise ValueError(f"英文目录中没有 JSON 文件: {en_root}")

    pairs = []
    missing_cn_files = []
    for en_file in en_files:
        relative_path = en_file.relative_to(en_root)
        cn_file = resolved_cn_root / relative_path
        if cn_file.is_file():
            pairs.append((en_file, cn_file, relative_path))
        else:
            missing_cn_files.append((relative_path, cn_file))

    for relative_path, cn_file in missing_cn_files:
        print(
            f"跳过缺少中文对应文件的 JSON: {relative_path} -> {cn_file}",
            flush=True,
        )
    if not pairs:
        raise ValueError(
            f"未在中文目录中找到任何对应 JSON: {resolved_cn_root}"
        )

    total = SyncSummary(verified=True, skipped_files=len(missing_cn_files))
    for index, (en_file, cn_file, relative_path) in enumerate(pairs, start=1):
        print(
            f"\n处理文件 [{index}/{len(pairs)}]: {relative_path}",
            flush=True,
        )
        try:
            current = sync_from_cn(
                str(en_file), str(cn_file), mode=mode,
                decision_provider=decision_provider, source_root=source_root,
            )
        except Exception as exc:
            total.verified = False
            total.failed_files.append((str(relative_path), str(exc)))
            print(
                f"处理失败，继续下一个文件: {relative_path}\n原因: {exc}",
                file=sys.stderr, flush=True,
            )
            continue
        total.added += current.added
        total.updated += current.updated
        total.kept_database += current.kept_database
        total.matched += current.matched
        total.conflicts += current.conflicts
        total.processed_files += current.processed_files
        total.verified = total.verified and current.verified
    return total


def print_sync_summary(summary: SyncSummary) -> None:
    if summary.processed_files or summary.skipped_files or summary.failed_files:
        print(
            f"文件汇总：成功 {summary.processed_files}，"
            f"失败 {len(summary.failed_files)}，跳过 {summary.skipped_files}。"
        )
        for file_path, error in summary.failed_files:
            print(f"- 失败: {file_path}: {error}")
    print(
        "同步完成："
        f"新增 {summary.added}，修改 {summary.updated}，"
        f"以库中为准 {summary.kept_database}，一致 {summary.matched}，"
        f"冲突 {summary.conflicts}。"
    )
    if summary.verified:
        print("验证通过：正常 translate 流程可稳定复现本次确认后的结果。")
    else:
        print("验证未全部通过：失败文件已列出，其余文件已继续处理。")
