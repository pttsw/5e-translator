"""Import hand-written Chinese JSON translations into the translation database."""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.core.translator import JsonAnalyser, JsonGenerator
from app.core.utils import Job


_JOB_MARKER_RE = re.compile(r"\{!@ ([^}]+)\}")
_EXACT_USAGE_PREFIX = "__SYNC_CN_EXACT__"


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


def _generate(info, mode: str):
    generator = JsonGenerator(1)
    return next(generator.invoke([info], config={"metadata": {
        "mode": mode,
        "generator_update_db": False,
    }})).cn_obj


def sync_from_cn(
    en_path: str,
    cn_path: str,
    mode: str = "5et",
    decision_provider: Optional[Callable[[SyncConflict, int, int], str]] = None,
    source_root: Optional[str] = None,
) -> SyncSummary:
    """Synchronize one English/Chinese JSON pair and verify a fresh DB-only generation."""
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
        if job.sql_id is None:
            missing.append((job, manual_value))
            expected_values[uid] = manual_value
        elif job.cn_str == manual_value:
            summary.matched += 1
            matching.append((job, manual_value))
            expected_values[uid] = manual_value
        else:
            conflict = SyncConflict(job, job.cn_str, manual_value)
            if manual_value == job.en_str:
                conflict.decision = "keep"
                expected_values[uid] = job.cn_str
            conflicts.append(conflict)

    summary.conflicts = len(conflicts)
    pending_conflicts = [conflict for conflict in conflicts if not conflict.decision]
    if pending_conflicts and decision_provider is None and not sys.stdin.isatty():
        raise RuntimeError("发现译文冲突，但当前不是交互终端；请在终端中重新运行以逐条确认")
    provider = decision_provider or _prompt_conflict
    modified_values_by_id = {}
    for index, conflict in enumerate(pending_conflicts, start=1):
        while True:
            decision = provider(conflict, index, len(pending_conflicts))
            if decision not in {"add", "modify", "keep"}:
                raise ValueError(f"未知冲突决定: {decision}")
            previous_value = modified_values_by_id.get(conflict.job.sql_id)
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
            modified_values_by_id[conflict.job.sql_id] = conflict.manual_value
        expected_values[conflict.job.uid] = (
            conflict.database_value if decision == "keep" else conflict.manual_value
        )

    # Build the exact expected output before mutating the database.
    exact_uids = {job.uid for job, _ in missing}
    matching_uids = {job.uid for job, _ in matching}
    exact_uids.update(matching_uids)
    exact_uids.update(
        conflict.job.uid
        for conflict in conflicts
        if conflict.decision in {"add", "modify"}
    )
    for job in info.job_list:
        if job.uid in expected_values:
            job.cn_str = expected_values[job.uid]
        if job.uid in exact_uids:
            if job.uid not in matching_uids:
                job.is_proofread = False
            _mark_exact_usage(job)
    expected_obj = _generate(copy.deepcopy(info), mode)

    db = analyser.dictionary
    for job, manual_value in matching:
        _mark_exact_usage(job)
        if not db.put(
            job.en_str, manual_value, job.rel_path, proofread=False,
            tag=job.tag, job_context=job, rebind_usage=True,
        ):
            raise RuntimeError(f"绑定一致译文失败: {job.en_str}")
    for job, manual_value in missing:
        _mark_exact_usage(job)
        if not db.put(
            job.en_str, manual_value, job.rel_path, proofread=False,
            tag=job.tag, job_context=job, rebind_usage=True,
        ):
            raise RuntimeError(f"新增译文失败: {job.en_str}")
        summary.added += 1

    for conflict in conflicts:
        job = conflict.job
        if conflict.decision == "keep":
            summary.kept_database += 1
            continue
        if conflict.decision == "add":
            _mark_exact_usage(job)
            ok = db.put(
                job.en_str, conflict.manual_value, job.rel_path, proofread=False,
                tag=job.tag, job_context=job, rebind_usage=True,
            )
            summary.added += 1
        else:
            _mark_exact_usage(job)
            ok = db.update(
                job.sql_id, job.en_str, conflict.manual_value, proofread=False,
                tag=job.tag, rel_f=job.rel_path, job_context=job,
            )
            summary.updated += 1
        if not ok:
            raise RuntimeError(f"写入冲突决定失败: {job.en_str}")

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
    return summary


def print_sync_summary(summary: SyncSummary) -> None:
    print(
        "同步完成："
        f"新增 {summary.added}，修改 {summary.updated}，"
        f"以库中为准 {summary.kept_database}，一致 {summary.matched}，"
        f"冲突 {summary.conflicts}。"
    )
    print("验证通过：正常 translate 流程可稳定复现本次确认后的结果。")
