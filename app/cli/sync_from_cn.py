"""Import hand-written Chinese JSON translations into the translation database."""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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


@dataclass
class ClassifyResult:
    missing: List[SyncConflict] = field(default_factory=list)
    matching: List[SyncConflict] = field(default_factory=list)
    auto_keep: List[SyncConflict] = field(default_factory=list)
    auto_modify: List[SyncConflict] = field(default_factory=list)
    auto_rebind: List[SyncConflict] = field(default_factory=list)
    conflict: List[SyncConflict] = field(default_factory=list)

    @property
    def all_conflicts(self) -> List[SyncConflict]:
        return self.auto_keep + self.auto_modify + self.auto_rebind + self.conflict


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


def resolve_cn_path(
    en_path: str, cn_path: Optional[str] = None, announce: bool = True,
) -> str:
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
    if announce:
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


differs_only_by_quote_style = _differs_only_by_quote_style
differs_only_inside_tags = _differs_only_inside_tags


def classify_alignment(
    alignment: AlignmentResult, jobs_by_uid: Dict[str, Job]
) -> ClassifyResult:
    """Classify aligned jobs for both preview and the mutating sync path."""
    result = ClassifyResult()
    for uid, manual_value in alignment.translations.items():
        job = jobs_by_uid[uid]
        item = SyncConflict(job, job.cn_str or "", manual_value)
        if job.sql_id is None or getattr(job, "tag_sync_required", False):
            result.missing.append(item)
        elif job.cn_str == manual_value:
            result.matching.append(item)
        elif manual_value == job.en_str or differs_only_by_quote_style(job.cn_str, manual_value):
            item.decision = "keep"
            result.auto_keep.append(item)
        elif differs_only_inside_tags(job.cn_str, manual_value):
            item.decision = "auto_modify"
            result.auto_modify.append(item)
        else:
            result.conflict.append(item)
    return result


_ACTIONS = {"add", "modify", "keep"}
_MATCH_KEYS = {"en", "en_in", "sql_id", "sql_id_in", "tag_child", "file"}


def _is_tag_child(job: Job) -> bool:
    return bool(job.tag and re.search(rf"/{re.escape(job.tag)}\[\d+\]$", job.key_path or ""))


def _validate_decision_rules(rules_dict: dict) -> dict:
    if not isinstance(rules_dict, dict):
        raise ValueError("决策文件根节点必须是对象")
    unknown = set(rules_dict) - {"default", "rules"}
    if unknown:
        raise ValueError(f"决策文件含未知字段: {sorted(unknown)}")
    normalized = {"rules": rules_dict.get("rules", [])}
    if not isinstance(normalized["rules"], list):
        raise ValueError("rules 必须是数组")

    def validate_rule(rule, location, allow_match):
        if not isinstance(rule, dict):
            raise ValueError(f"{location} 必须是对象")
        allowed = {"action", "fallback", "reason"} | ({"match"} if allow_match else set())
        extra = set(rule) - allowed
        if extra:
            raise ValueError(f"{location} 含未知字段: {sorted(extra)}")
        if rule.get("action") not in _ACTIONS:
            raise ValueError(f"{location}.action 必须是 add/modify/keep")
        if not isinstance(rule.get("reason"), str) or not rule["reason"].strip():
            raise ValueError(f"{location}.reason 不能为空")
        if "fallback" in rule and rule["fallback"] not in _ACTIONS:
            raise ValueError(f"{location}.fallback 必须是 add/modify/keep")
        if allow_match:
            match = rule.get("match")
            if not isinstance(match, dict) or not match:
                raise ValueError(f"{location}.match 必须是非空对象")
            match_extra = set(match) - _MATCH_KEYS
            if match_extra:
                raise ValueError(f"{location}.match 含未知字段: {sorted(match_extra)}")
            for key in ("en_in", "sql_id_in"):
                if key in match and not isinstance(match[key], list):
                    raise ValueError(f"{location}.match.{key} 必须是数组")
            if "tag_child" in match and not isinstance(match["tag_child"], bool):
                raise ValueError(f"{location}.match.tag_child 必须是布尔值")
        return dict(rule)

    normalized["rules"] = [validate_rule(rule, f"rules[{index}]", True)
                             for index, rule in enumerate(normalized["rules"])]
    if "default" in rules_dict:
        normalized["default"] = validate_rule(rules_dict["default"], "default", False)
    return normalized


class RulesDecisionProvider:
    def __init__(self, rules_dict: dict):
        self.rules = _validate_decision_rules(rules_dict)
        self.log: List[dict] = []

    @staticmethod
    def _matches(match: dict, conflict: SyncConflict) -> bool:
        job = conflict.job
        values = {"en": job.en_str, "sql_id": job.sql_id,
                  "tag_child": _is_tag_child(job), "file": job.rel_path}
        for key, expected in match.items():
            if key == "en_in" and values["en"] not in expected:
                return False
            if key == "sql_id_in" and values["sql_id"] not in expected:
                return False
            if key not in {"en_in", "sql_id_in"} and values[key] != expected:
                return False
        return True

    def choose(self, conflict: SyncConflict) -> Tuple[dict, str]:
        for index, rule in enumerate(self.rules["rules"]):
            if self._matches(rule["match"], conflict):
                return rule, f"rules[{index}]"
        default = self.rules.get("default")
        if default is None:
            raise ValueError(
                f"决策规则未覆盖冲突: file={conflict.job.rel_path}, "
                f"uid={conflict.job.uid}, en={conflict.job.en_str!r}"
            )
        return default, "default"

    def __call__(self, conflict: SyncConflict, index: int, total: int) -> str:
        rule, matched = self.choose(conflict)
        conflict._decision_rule = rule
        conflict._decision_match = matched
        return rule["action"]


def build_decision_provider_from_rules(rules_dict: dict) -> RulesDecisionProvider:
    """Validate rules and return the provider used by both CLI and library calls."""
    return RulesDecisionProvider(rules_dict)


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


def _report_entry(section: str, item: SyncConflict) -> dict:
    job = item.job
    return {
        "uid": job.uid,
        "en": job.en_str,
        "cn_db": item.database_value,
        "cn_file": item.manual_value,
        "sql_id": job.sql_id,
        "is_proofread": bool(job.is_proofread),
        "tag": job.tag,
        "key_path": job.key_path,
        "tag_child": _is_tag_child(job),
        "file": job.rel_path,
    }


def _chunks(values: Sequence[Any], size: int = 500):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _query_preview_evidence(db, english: Sequence[str], word_ids: Sequence[int]) -> dict:
    candidates = {value: [] for value in sorted(set(english))}
    usage_files = {str(value): {} for value in sorted(set(word_ids))}
    if hasattr(db, "store_records"):
        for en in candidates:
            rows = [row for row in db.store_records if row["en"] == en]
            for row in rows:
                usages = [u for u in db.word_usage if u["word_id"] == row["id"]]
                candidates[en].append({
                    "id": row["id"], "cn": row["cn"],
                    "proofread": bool(row["proofread"]),
                    "is_key": bool(row["is_key"]), "usage_count": len(usages),
                    "json_file": row.get("json_file", ""),
                })
        for word_id in word_ids:
            counts = usage_files[str(word_id)]
            for usage in db.word_usage:
                if usage["word_id"] == word_id:
                    counts[usage["file"]] = counts.get(usage["file"], 0) + 1
    else:
        sql_db = db.db_list[0]
        for chunk in _chunks(sorted(candidates)):
            placeholders = ",".join(["%s"] * len(chunk))
            rows = sql_db.execute_query(
                "SELECT w.id,w.en,w.cn,w.proofread,w.is_key,w.json_file,"
                "COUNT(wu.id) usage_count FROM words w LEFT JOIN word_usage wu "
                "ON wu.word_id=w.id WHERE BINARY w.en IN (" + placeholders + ") "
                "GROUP BY w.id,w.en,w.cn,w.proofread,w.is_key,w.json_file",
                tuple(chunk),
            ) or []
            for row in rows:
                candidates[row["en"]].append({
                    "id": row["id"], "cn": row["cn"],
                    "proofread": bool(row["proofread"]),
                    "is_key": bool(row["is_key"]),
                    "usage_count": row["usage_count"],
                    "json_file": row.get("json_file", ""),
                })
        for chunk in _chunks(sorted(set(word_ids))):
            placeholders = ",".join(["%s"] * len(chunk))
            rows = sql_db.execute_query(
                "SELECT word_id,file,COUNT(*) usage_count FROM word_usage "
                "WHERE word_id IN (" + placeholders + ") GROUP BY word_id,file",
                tuple(chunk),
            ) or []
            for row in rows:
                usage_files[str(row["word_id"])][row["file"]] = row["usage_count"]
    for rows in candidates.values():
        rows.sort(key=lambda row: (-row["usage_count"], row["id"]))
    return {"candidates": candidates, "usage_files": usage_files}


def _promote_reusable_conflicts(classified: ClassifyResult, db) -> None:
    """Turn conflicts with an existing exact (en, cn) row into usage rebinds."""
    if not classified.conflict:
        return
    evidence = _query_preview_evidence(
        db, [item.job.en_str for item in classified.conflict], [],
    )
    remaining = []
    for item in classified.conflict:
        candidate = next(
            (row for row in evidence["candidates"].get(item.job.en_str, [])
             if row["cn"] == item.manual_value),
            None,
        )
        if candidate is None:
            remaining.append(item)
            continue
        item.decision = "add"
        item._reuse_sql_id = candidate["id"]
        classified.auto_rebind.append(item)
    classified.conflict = remaining


def _find_collisions(classified: ClassifyResult) -> List[dict]:
    by_id: Dict[int, List[Tuple[str, SyncConflict]]] = {}
    for section in ("auto_modify", "conflict"):
        for item in getattr(classified, section):
            if item.job.sql_id is not None:
                by_id.setdefault(item.job.sql_id, []).append((section, item))
    collisions = []
    for word_id, items in by_id.items():
        values = sorted({item.manual_value for _, item in items})
        if len(values) < 2:
            continue
        sections = {section for section, _ in items}
        kind = ("conflict-vs-auto_modify" if len(sections) > 1
                else "conflict-vs-conflict")
        collisions.append({
            "word_id": word_id,
            "uids": sorted(item.job.uid for _, item in items),
            "distinct_file_values": values,
            "kind": kind,
        })
    return sorted(collisions, key=lambda item: item["word_id"])


def preview_sync_from_cn(
    en_path: str,
    cn_path: Optional[str] = None,
    mode: str = "5et",
    source_root: Optional[str] = None,
    include_matching: bool = False,
) -> dict:
    """Analyze one pair and return a deterministic, JSON-serializable report."""
    cn_path = resolve_cn_path(en_path, cn_path, announce=False)
    manual_obj = _read_json(cn_path)
    analyser = JsonAnalyser(
        source_root=source_root, read_only=True, preserve_job_markers=True,
    )
    info = next(analyser.invoke([en_path], config={"metadata": {
        "mode": mode, "force_title": False,
    }}), None)
    if info is None:
        raise RuntimeError("英文文件未产生可同步的分析结果，请检查路径与 --mode")
    alignment = align_manual_translations(info.json_obj, manual_obj, info.job_list)
    classified = classify_alignment(alignment, {job.uid: job for job in info.job_list})
    entries = {}
    _promote_reusable_conflicts(classified, analyser.dictionary)
    for section in ("missing", "auto_keep", "auto_modify", "auto_rebind", "conflict"):
        rows = [_report_entry(section, item) for item in getattr(classified, section)]
        if section == "auto_rebind":
            for row, item in zip(rows, classified.auto_rebind):
                row["reuse_sql_id"] = item._reuse_sql_id
        entries[section] = sorted(
            rows, key=lambda row: (section, row["en"], row["cn_db"], row["cn_file"], row["uid"])
        )
    if include_matching:
        rows = [_report_entry("matching", item) for item in classified.matching]
        entries["matching"] = sorted(
            rows, key=lambda row: ("matching", row["en"], row["cn_db"], row["cn_file"], row["uid"])
        )
    evidence = _query_preview_evidence(
        analyser.dictionary,
        [item.job.en_str for item in (
            classified.missing + classified.auto_rebind + classified.conflict
        )],
        [item.job.sql_id for item in classified.conflict + classified.auto_modify
         if item.job.sql_id is not None]
        + [item._reuse_sql_id for item in classified.auto_rebind],
    )
    return {
        "en_path": str(en_path), "cn_path": str(cn_path), "mode": mode,
        "summary": {
            "total_jobs": len(info.job_list),
            "translated": len(alignment.translations),
            "missing": len(classified.missing),
            "matching": len(classified.matching),
            "auto_keep": len(classified.auto_keep),
            "auto_modify": len(classified.auto_modify),
            "auto_rebind": len(classified.auto_rebind),
            "conflict": len(classified.conflict),
            "alignment_errors": len(alignment.errors),
        },
        "entries": entries,
        "evidence": evidence,
        "collisions": _find_collisions(classified),
        "alignment_errors": sorted(alignment.errors),
    }


def _merge_preview_reports(reports: List[dict], en_path: str, cn_path: str, mode: str) -> dict:
    summary_keys = ("total_jobs", "translated", "missing", "matching", "auto_keep",
                    "auto_modify", "auto_rebind", "conflict", "alignment_errors")
    merged = {
        "en_path": en_path, "cn_path": cn_path, "mode": mode,
        "summary": {key: sum(report["summary"][key] for report in reports)
                    for key in summary_keys},
        "entries": {}, "evidence": {"candidates": {}, "usage_files": {}},
        "collisions": [], "alignment_errors": [], "files": reports,
    }
    sections = sorted({section for report in reports for section in report["entries"]})
    for section in sections:
        merged["entries"][section] = sorted(
            [row for report in reports for row in report["entries"].get(section, [])],
            key=lambda row: (section, row["en"], row["cn_db"], row["cn_file"], row["uid"]),
        )
    for report in reports:
        for en, rows in report["evidence"]["candidates"].items():
            known = {row["id"] for row in merged["evidence"]["candidates"].get(en, [])}
            merged["evidence"]["candidates"].setdefault(en, []).extend(
                row for row in rows if row["id"] not in known
            )
        for word_id, counts in report["evidence"]["usage_files"].items():
            merged["evidence"]["usage_files"].setdefault(word_id, {}).update(counts)
        merged["collisions"].extend(report["collisions"])
        merged["alignment_errors"].extend(
            f"{report.get('relative_path', '')}: {error}" for error in report["alignment_errors"]
        )
    merged["collisions"].sort(key=lambda item: item["word_id"])
    return merged


def preview_sync_from_cn_path(
    en_path: str, cn_path: Optional[str] = None, mode: str = "5et",
    source_root: Optional[str] = None, include_matching: bool = False,
) -> dict:
    """Preview one file or recursively preview paired JSON files."""
    en_root = Path(en_path)
    if en_root.is_file():
        return preview_sync_from_cn(
            str(en_root), cn_path, mode, source_root, include_matching,
        )
    if not en_root.is_dir():
        raise FileNotFoundError(f"英文路径不存在: {en_root}")
    cn_root = Path(resolve_cn_path(str(en_root), cn_path, announce=False))
    if not cn_root.is_dir():
        raise ValueError(f"--en 是目录时，--cn 也必须是目录: {cn_root}")
    reports = []
    for en_file in sorted(en_root.rglob("*.json")):
        relative = en_file.relative_to(en_root)
        cn_file = cn_root / relative
        if not cn_file.is_file():
            continue
        report = preview_sync_from_cn(
            str(en_file), str(cn_file), mode, source_root or str(en_root), include_matching,
        )
        report["relative_path"] = str(relative)
        reports.append(report)
    if not reports:
        raise ValueError(f"未在中文目录中找到任何对应 JSON: {cn_root}")
    return _merge_preview_reports(reports, str(en_root), str(cn_root), mode)


def validate_preview_decisions(report: dict, provider: RulesDecisionProvider) -> List[dict]:
    """Fail closed while dry-running rule coverage and word-id collisions."""
    if report.get("files"):
        for file_report in report["files"]:
            validate_preview_decisions(file_report, provider)
        return provider.log
    modified = {}
    for row in report["entries"].get("auto_modify", []):
        old = modified.get(row["sql_id"])
        if old is not None and old != row["cn_file"]:
            raise ValueError(
                f"预演碰撞: word_id={row['sql_id']}，冲突值={old!r}/{row['cn_file']!r}"
            )
        modified[row["sql_id"]] = row["cn_file"]
    for index, row in enumerate(report["entries"].get("conflict", []), start=1):
        job = Job(row["uid"], row["en"], row["cn_db"], rel_path=row.get("file", ""),
                  tag=row.get("tag", ""), key_path=row.get("key_path", ""))
        job.sql_id = row["sql_id"]
        conflict = SyncConflict(job, row["cn_db"], row["cn_file"])
        action = provider(conflict, index, report["summary"]["conflict"])
        fallback_used = False
        previous = modified.get(row["sql_id"])
        if action == "modify" and previous is not None and previous != row["cn_file"]:
            fallback = conflict._decision_rule.get("fallback")
            if fallback is None or fallback == "modify":
                raise ValueError(
                    f"预演碰撞: word_id={row['sql_id']}，冲突值="
                    f"{previous!r}/{row['cn_file']!r}，规则={conflict._decision_match}"
                )
            action, fallback_used = fallback, True
        if action == "modify":
            modified[row["sql_id"]] = row["cn_file"]
        provider.log.append({
            "index": index, "uid": row["uid"], "sql_id": row["sql_id"],
            "en": row["en"], "cn_db": row["cn_db"], "cn_file": row["cn_file"],
            "file": row.get("file", ""),
            "matched": conflict._decision_match, "action": action,
            "fallback_used": fallback_used, "reason": conflict._decision_rule["reason"],
        })
    return provider.log


def sync_from_cn(
    en_path: str,
    cn_path: Optional[str] = None,
    mode: str = "5et",
    decision_provider: Optional[Callable[[SyncConflict, int, int], str]] = None,
    source_root: Optional[str] = None,
    decision_log: Optional[List[dict]] = None,
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

    jobs_by_uid = {job.uid: job for job in info.job_list}
    classified = classify_alignment(alignment, jobs_by_uid)
    _promote_reusable_conflicts(classified, analyser.dictionary)
    summary = SyncSummary(matched=len(classified.matching))
    conflicts = classified.all_conflicts
    missing = [(item.job, item.manual_value) for item in classified.missing]
    matching = [(item.job, item.manual_value) for item in classified.matching]
    expected_values: Dict[str, str] = {}
    for item in classified.missing:
        item.job.tag_sync_required = False
        item.job.sql_id = None
        expected_values[item.job.uid] = item.manual_value
    for item in classified.matching:
        expected_values[item.job.uid] = item.manual_value
    for item in classified.auto_keep:
        expected_values[item.job.uid] = item.database_value
    for item in classified.auto_modify:
        expected_values[item.job.uid] = item.manual_value

    summary.conflicts = len(conflicts)
    pending_conflicts = [conflict for conflict in conflicts if not conflict.decision]
    if pending_conflicts and decision_provider is None and not sys.stdin.isatty():
        raise RuntimeError("发现译文冲突，但当前不是交互终端；请在终端中重新运行以逐条确认")
    provider = decision_provider or _prompt_conflict
    modified_values_by_id = {}
    for index, conflict in enumerate(conflicts, start=1):
        if conflict.decision == "keep":
            continue
        if conflict.decision == "add":
            expected_values[conflict.job.uid] = conflict.manual_value
            continue
        word_id = conflict.job.sql_id
        if word_id in modified_values_by_id:
            conflict._original_database_value = conflict.database_value
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
                rule = getattr(conflict, "_decision_rule", None)
                if rule and rule.get("fallback"):
                    decision = rule["fallback"]
                    conflict._fallback_used = True
                    if decision == "modify":
                        raise ValueError(
                            f"word_id={word_id} 的 fallback 仍为 modify，"
                            f"冲突值={previous_value!r}/{conflict.manual_value!r}，"
                            f"规则={getattr(conflict, '_decision_match', '')}"
                        )
                    break
                if decision_provider is not None:
                    raise ValueError(
                        f"{message}；word_id={word_id}，"
                        f"冲突值={previous_value!r}/{conflict.manual_value!r}，"
                        f"规则={getattr(conflict, '_decision_match', '自定义 provider')}"
                    )
                print(message)
                continue
            break
        conflict.decision = decision
        if decision == "modify":
            modified_values_by_id[word_id] = conflict.manual_value
        expected_values[conflict.job.uid] = (
            conflict.database_value if decision == "keep" else conflict.manual_value
        )
        if isinstance(provider, RulesDecisionProvider):
            rule = conflict._decision_rule
            entry = {
                "index": index, "uid": conflict.job.uid,
                "sql_id": conflict.job.sql_id, "en": conflict.job.en_str,
                "cn_db": getattr(conflict, "_original_database_value", conflict.database_value),
                "cn_file": conflict.manual_value, "file": conflict.job.rel_path,
                "matched": conflict._decision_match, "action": decision,
                "fallback_used": bool(getattr(conflict, "_fallback_used", False)),
                "reason": rule["reason"],
            }
            provider.log.append(entry)
            if decision_log is not None:
                decision_log.append(entry)

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
    decision_log: Optional[List[dict]] = None,
) -> SyncSummary:
    """同步单个 JSON，或递归同步目录中所有可配对的 JSON。"""
    en_root = Path(en_path)
    if en_root.is_file():
        return sync_from_cn(
            str(en_root), cn_path, mode=mode,
            decision_provider=decision_provider, source_root=source_root,
            decision_log=decision_log,
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
                decision_log=decision_log,
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
