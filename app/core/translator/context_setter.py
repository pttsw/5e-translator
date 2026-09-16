"""Attach a small piece of source JSON context to translation jobs."""

import copy
import hashlib
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from langchain_core.runnables import Runnable

from ..utils.file_work_info import FileWorkInfo


PLACEHOLDER_PATTERN = re.compile(r"\{!@\s+([^}]+)\}")
PATH_SEGMENT_PATTERN = re.compile(r"^([^\[]+)(?:\[(.*)\])?$")


class ContextSetter(Runnable):
    """为需要翻译的 Job 添加其在英文 JSON 中的局部上下文。

    上下文从 Job 所在值的直接容器开始，逐层向根节点扩展。选取的
    是字符串总长度首次超过 ``max_chars`` 的最小祖先；如果整个对象
    都没有超过该阈值，则使用根对象。
    """

    def __init__(self, max_chars: int = 1000):
        if max_chars < 0:
            raise ValueError("max_chars must be non-negative")
        self.max_chars = max_chars

    def invoke(self, input, config=None, **kwargs):
        for file_info in self._as_file_infos(input):
            self._set_file_contexts(file_info)
            yield file_info

    @staticmethod
    def _as_file_infos(value) -> Iterable[FileWorkInfo]:
        if isinstance(value, FileWorkInfo):
            return (value,)
        return value

    def _set_file_contexts(self, file_info: FileWorkInfo) -> None:
        jobs = list(getattr(file_info, "job_list", ()) or ())
        pending_jobs = [job for job in jobs if getattr(job, "need_translate", False)]
        if not pending_jobs:
            return

        source = getattr(file_info, "json_obj", None)
        if source is None:
            for job in pending_jobs:
                job.set_context(None)
            return

        jobs_by_uid = {
            str(job.uid): job
            for job in jobs
            if getattr(job, "uid", None) is not None
        }
        restored_source = self._restore_placeholders(source, jobs_by_uid)
        placeholder_paths = self._index_placeholder_paths(source)

        for job in pending_jobs:
            uid = str(getattr(job, "uid", ""))
            paths = placeholder_paths.get(uid, ())
            if paths:
                # UIDs are expected to be unique. Keeping the first occurrence
                # makes malformed duplicate input deterministic.
                value_path = paths[0]
                context_path = value_path[:-1] if value_path else value_path
            else:
                value_path = self._resolve_job_path(
                    restored_source,
                    getattr(job, "key_path", "") or getattr(job, "entry_path", ""),
                )
                context_path = value_path[:-1] if value_path else value_path

            context = self._expand_context(restored_source, context_path)
            job.set_context(copy.deepcopy(context))

    def _expand_context(self, source: Any, start_path: Tuple[Any, ...]) -> Any:
        """Return the smallest ancestor whose string content crosses the limit."""
        path = start_path
        context = self._get_at_path(source, path)
        while self._string_length(context) <= self.max_chars and path:
            path = path[:-1]
            context = self._get_at_path(source, path)
        return context

    @classmethod
    def _string_length(cls, value: Any) -> int:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict):
            return sum(cls._string_length(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return sum(cls._string_length(item) for item in value)
        return 0

    @classmethod
    def _restore_placeholders(cls, value: Any, jobs_by_uid: Dict[str, Any]) -> Any:
        if isinstance(value, str):
            return PLACEHOLDER_PATTERN.sub(
                lambda match: cls._job_english(jobs_by_uid.get(match.group(1)), match.group(0)),
                value,
            )
        if isinstance(value, dict):
            return {
                key: cls._restore_placeholders(item, jobs_by_uid)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._restore_placeholders(item, jobs_by_uid) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._restore_placeholders(item, jobs_by_uid) for item in value)
        return value

    @staticmethod
    def _job_english(job: Any, fallback: str) -> str:
        if job is None:
            return fallback
        english = getattr(job, "en_str", None)
        return english if isinstance(english, str) else fallback

    @classmethod
    def _index_placeholder_paths(cls, source: Any) -> Dict[str, List[Tuple[Any, ...]]]:
        result: Dict[str, List[Tuple[Any, ...]]] = defaultdict(list)

        def visit(value: Any, path: Tuple[Any, ...]) -> None:
            if isinstance(value, str):
                for match in PLACEHOLDER_PATTERN.finditer(value):
                    result[match.group(1)].append(path)
            elif isinstance(value, dict):
                for key, item in value.items():
                    visit(item, path + (key,))
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    visit(item, path + (index,))

        visit(source, ())
        return result

    @classmethod
    def _resolve_job_path(cls, source: Any, key_path: str) -> Tuple[Any, ...]:
        """Resolve analyser key paths, returning the deepest valid prefix.

        Tag sub-jobs append virtual path segments (for example ``/spell[0]``)
        which do not exist in the JSON. Returning the valid prefix still places
        those jobs in the context of their containing source string.
        """
        if not isinstance(key_path, str) or not key_path:
            return ()

        current = source
        resolved: Tuple[Any, ...] = ()
        for raw_segment in (part for part in key_path.strip("/").split("/") if part):
            matched = PATH_SEGMENT_PATTERN.match(raw_segment)
            if matched is None or not isinstance(current, dict):
                break
            key, selector = matched.groups()
            if key not in current:
                break
            current = current[key]
            resolved += (key,)
            if selector is None:
                continue
            if not isinstance(current, (list, tuple)):
                break
            index = cls._select_list_index(current, selector)
            if index is None:
                break
            current = current[index]
            resolved += (index,)
        return resolved

    @classmethod
    def _select_list_index(cls, values: Any, selector: str) -> Optional[int]:
        if selector.isdigit():
            index = int(selector)
            return index if index < len(values) else None

        criteria = {}
        for part in selector.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                criteria[key] = value

        explicit_index = criteria.pop("index", None)
        candidates = []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            if all(cls._matches_path_criterion(item, key, expected)
                   for key, expected in criteria.items()):
                candidates.append(index)

        if explicit_index is not None and explicit_index.isdigit():
            index = int(explicit_index)
            if index < len(values) and (not criteria or index in candidates):
                return index
            return None
        return candidates[0] if len(candidates) == 1 else None

    @classmethod
    def _matches_path_criterion(cls, item: Dict[str, Any], key: str, expected: str) -> bool:
        if cls._stable_path_token(item.get(key)) == expected:
            return True
        # The analyser may replace ``name`` with an existing translation while
        # retaining the source value in its generated ``ENG_name`` field.
        return key == "name" and cls._stable_path_token(item.get("ENG_name")) == expected

    @staticmethod
    def _stable_path_token(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"[^0-9A-Za-z_.@|:-]+", "-", text).strip("-")
        if len(text) > 80:
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
            text = f"{text[:70]}-{digest}"
        return text

    @staticmethod
    def _get_at_path(source: Any, path: Tuple[Any, ...]) -> Any:
        current = source
        for part in path:
            current = current[part]
        return current
