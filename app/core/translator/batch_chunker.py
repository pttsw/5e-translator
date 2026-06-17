import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import List

from app.core.utils import FileWorkInfo, Job


@dataclass
class BatchUnit:
    batch_id: str
    parent_batch_id: str
    chunk_index: int
    jobs: List[Job]
    context_text: str
    breadcrumb: List[str] = field(default_factory=list)
    known_translations: List[dict] = field(default_factory=list)
    err_time: int = 0
    source_hash: str = ""
    retry_depth: int = 0
    last_request: str = ""
    last_response: str = ""
    failure_reason: str = ""

    def __post_init__(self):
        if not self.source_hash:
            payload = {
                "batch_id": self.batch_id,
                "job_uids": [job.uid for job in self.jobs],
                "context_text": self.context_text,
            }
            self.source_hash = hashlib.sha1(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()


class BatchChunker:
    def __init__(self, max_chars: int = 12000):
        self.max_chars = max_chars

    def build_units(self, file_info: FileWorkInfo) -> List[BatchUnit]:
        batch_id = file_info.out_path or file_info.json_path
        self._assign_batch_metadata(file_info, batch_id)
        pending_source = getattr(file_info, "batch_pending_jobs", None)
        if pending_source is None:
            pending_source = file_info.job_list
        pending_jobs = [job for job in pending_source if job.need_translate]
        if not pending_jobs:
            return []

        known_translations = [
            {"uid": job.uid, "en_str": job.en_str, "cn_str": job.cn_str}
            for job in file_info.job_list
            if (not job.need_translate) and isinstance(job.cn_str, str) and job.cn_str != ""
        ]

        restored_obj = self._restore_placeholders(file_info.json_obj, file_info.job_list)
        context_text = self._serialize_context(restored_obj)
        if len(context_text) <= self.max_chars:
            return [BatchUnit(
                batch_id=batch_id,
                parent_batch_id=batch_id,
                chunk_index=0,
                jobs=pending_jobs,
                context_text=context_text,
                breadcrumb=self._build_breadcrumb(pending_jobs),
                known_translations=known_translations,
            )]

        return self._split_large_unit(
            batch_id=batch_id,
            restored_obj=restored_obj,
            pending_jobs=pending_jobs,
            known_translations=known_translations,
        )

    def split_retry_unit(self, batch_unit: BatchUnit) -> List[BatchUnit]:
        if len(batch_unit.jobs) <= 1:
            return []

        job_groups = self._group_jobs_for_retry(batch_unit.jobs)
        if len(job_groups) <= 1:
            left_jobs, right_jobs = self._split_jobs_balanced(batch_unit.jobs)
            job_groups = [left_jobs, right_jobs]
        else:
            job_groups = self._merge_groups_for_retry(job_groups)

        child_units = []
        for child_index, jobs in enumerate(job_groups):
            if not jobs:
                continue
            child_units.append(
                BatchUnit(
                    batch_id=f"{batch_unit.batch_id}/sub-{batch_unit.retry_depth + 1}-{child_index}",
                    parent_batch_id=batch_unit.batch_id,
                    chunk_index=child_index,
                    jobs=jobs,
                    context_text=self._build_context_from_jobs(jobs),
                    breadcrumb=self._build_breadcrumb(jobs) or batch_unit.breadcrumb,
                    known_translations=batch_unit.known_translations,
                    retry_depth=batch_unit.retry_depth + 1,
                )
            )
        return child_units

    def _assign_batch_metadata(self, file_info: FileWorkInfo, batch_id: str):
        file_info.batch_meta["unit_id"] = batch_id
        file_info.batch_meta["unit_type"] = self._get_unit_type(file_info)
        for index, job in enumerate(file_info.job_list):
            job.batch_id = batch_id
            job.batch_seq = index

    def _get_unit_type(self, file_info: FileWorkInfo) -> str:
        if isinstance(file_info.json_obj, dict):
            keys = [key for key in file_info.json_obj.keys() if key != "_meta"]
            if len(keys) == 1:
                return keys[0]
        return ""

    def _serialize_context(self, restored_obj) -> str:
        try:
            return json.dumps(restored_obj, ensure_ascii=False, indent=2)
        except Exception:
            return str(restored_obj)

    def _restore_placeholders(self, value, job_list: List[Job]):
        if isinstance(value, str):
            restored_value = value
            for job in job_list:
                restored_value = restored_value.replace(f"{{!@ {job.uid}}}", job.en_str)
            return restored_value
        if isinstance(value, list):
            return [self._restore_placeholders(item, job_list) for item in value]
        if isinstance(value, dict):
            return {key: self._restore_placeholders(item, job_list) for key, item in value.items()}
        return value

    def _build_breadcrumb(self, jobs: List[Job]) -> List[str]:
        breadcrumb = []
        for job in jobs:
            for name_pair in job.current_names:
                if not name_pair:
                    continue
                name = name_pair[0]
                if isinstance(name, str) and name not in breadcrumb:
                    breadcrumb.append(name)
        return breadcrumb[:5]

    def _build_context_from_jobs(self, jobs: List[Job]) -> str:
        context_lines = []
        for job in jobs:
            if job.current_names:
                names = " > ".join(name_pair[0] for name_pair in job.current_names if name_pair)
                if names:
                    context_lines.append(f"[PATH] {names}")
            context_lines.append(job.en_str)
        context_text = "\n\n".join(context_lines)
        context_text = re.sub(r"\n{3,}", "\n\n", context_text)
        return context_text[:self.max_chars]

    def _split_large_unit(self, batch_id: str, restored_obj, pending_jobs: List[Job], known_translations: List[dict]) -> List[BatchUnit]:
        blocks = self._extract_blocks(restored_obj)
        block_units = self._build_units_from_blocks(batch_id, pending_jobs, known_translations, blocks)
        if block_units:
            return block_units
        return self._split_large_unit_by_jobs(batch_id, pending_jobs, known_translations)

    def _build_units_from_blocks(self, batch_id: str, pending_jobs: List[Job], known_translations: List[dict], blocks: List[dict]) -> List[BatchUnit]:
        if not blocks:
            return []

        units = []
        matched_job_ids = set()
        current_jobs = []
        current_texts = []
        current_chars = 0
        chunk_index = 0

        for block in blocks:
            block_jobs = self._match_jobs_for_block(block["path"], pending_jobs)
            if not block_jobs:
                continue
            block_text = block["text"]
            block_chars = len(block_text)
            if current_jobs and current_chars + block_chars > self.max_chars:
                units.append(self._build_chunk_unit_from_context(
                    batch_id,
                    chunk_index,
                    current_jobs,
                    known_translations,
                    "\n\n".join(current_texts),
                ))
                current_jobs = []
                current_texts = []
                current_chars = 0
                chunk_index += 1
            current_jobs.extend([job for job in block_jobs if job not in current_jobs])
            matched_job_ids.update(job.uid for job in block_jobs)
            current_texts.append(block_text)
            current_chars += block_chars

        if current_jobs:
            units.append(self._build_chunk_unit_from_context(
                batch_id,
                chunk_index,
                current_jobs,
                known_translations,
                "\n\n".join(current_texts),
            ))

        unmatched_jobs = [job for job in pending_jobs if job.uid not in matched_job_ids]
        if unmatched_jobs:
            current_jobs = []
            current_chars = 0
            for job in unmatched_jobs:
                job_chars = len(job.en_str or "")
                if current_jobs and current_chars + job_chars > self.max_chars:
                    units.append(self._build_chunk_unit(batch_id, chunk_index, current_jobs, known_translations))
                    current_jobs = []
                    current_chars = 0
                    chunk_index += 1
                current_jobs.append(job)
                current_chars += job_chars
            if current_jobs:
                units.append(self._build_chunk_unit(batch_id, chunk_index, current_jobs, known_translations))
        return units

    def _split_large_unit_by_jobs(self, batch_id: str, pending_jobs: List[Job], known_translations: List[dict]) -> List[BatchUnit]:
        units = []
        current_jobs = []
        current_chars = 0
        chunk_index = 0
        for job in pending_jobs:
            job_chars = len(job.en_str or "")
            if current_jobs and current_chars + job_chars > self.max_chars:
                units.append(self._build_chunk_unit(batch_id, chunk_index, current_jobs, known_translations))
                current_jobs = []
                current_chars = 0
                chunk_index += 1
            current_jobs.append(job)
            current_chars += job_chars
        if current_jobs:
            units.append(self._build_chunk_unit(batch_id, chunk_index, current_jobs, known_translations))
        return units

    def _group_jobs_for_retry(self, jobs: List[Job]) -> List[List[Job]]:
        groups = []
        current_group = []
        current_key = None
        for job in jobs:
            group_key = self._get_retry_group_key(job)
            if current_group and group_key != current_key:
                groups.append(current_group)
                current_group = []
            current_group.append(job)
            current_key = group_key
        if current_group:
            groups.append(current_group)
        return groups

    def _get_retry_group_key(self, job: Job) -> str:
        if job.entry_path:
            return job.entry_path
        if job.current_names:
            return " > ".join(name_pair[0] for name_pair in job.current_names if name_pair)
        if job.key_path:
            return job.key_path
        return job.uid

    def _merge_groups_for_retry(self, groups: List[List[Job]]) -> List[List[Job]]:
        if len(groups) == 2:
            return groups

        total_chars = sum(sum(len(job.en_str or "") for job in group) for group in groups)
        target_chars = total_chars / 2
        left_groups = []
        left_chars = 0
        split_index = 0
        for index, group in enumerate(groups):
            group_chars = sum(len(job.en_str or "") for job in group)
            if index > 0 and left_chars + group_chars > target_chars:
                split_index = index
                break
            left_groups.append(group)
            left_chars += group_chars
            split_index = index + 1

        if split_index <= 0 or split_index >= len(groups):
            left_jobs, right_jobs = self._split_jobs_balanced([job for group in groups for job in group])
            return [left_jobs, right_jobs]

        left_jobs = [job for group in groups[:split_index] for job in group]
        right_jobs = [job for group in groups[split_index:] for job in group]
        return [left_jobs, right_jobs]

    def _split_jobs_balanced(self, jobs: List[Job]):
        total_chars = sum(len(job.en_str or "") for job in jobs)
        target_chars = total_chars / 2
        left_jobs = []
        left_chars = 0
        for index, job in enumerate(jobs):
            job_chars = len(job.en_str or "")
            if index > 0 and left_chars + job_chars > target_chars:
                return left_jobs, jobs[index:]
            left_jobs.append(job)
            left_chars += job_chars
        midpoint = len(jobs) // 2
        return jobs[:midpoint], jobs[midpoint:]

    def _extract_blocks(self, value, path: str = "") -> List[dict]:
        blocks = []
        if isinstance(value, dict):
            block_path = path or "/"
            if self._is_structural_block(value):
                text = self._serialize_context(value)
                if len(text) <= self.max_chars:
                    blocks.append({"path": block_path, "text": text})
                    return blocks
            for key, item in value.items():
                child_path = f"{path}/{key}" if path else f"/{key}"
                blocks.extend(self._extract_blocks(item, child_path))
            return blocks
        if isinstance(value, list):
            for index, item in enumerate(value):
                child_path = f"{path}[{index}]"
                blocks.extend(self._extract_blocks(item, child_path))
            return blocks
        return []

    def _is_structural_block(self, value: dict) -> bool:
        if "entries" in value or "items" in value or "rows" in value:
            return True
        block_type = value.get("type")
        if isinstance(block_type, str) and block_type in {"section", "entries", "inset", "table", "list", "quote"}:
            return True
        return False

    def _match_jobs_for_block(self, block_path: str, pending_jobs: List[Job]) -> List[Job]:
        matched = []
        normalized_block_path = block_path.rstrip("/")
        for job in pending_jobs:
            key_path = (job.key_path or "").rstrip("/")
            entry_path = (job.entry_path or "").rstrip("/")
            if key_path.startswith(normalized_block_path) or entry_path.startswith(normalized_block_path):
                matched.append(job)
        return matched

    def _build_chunk_unit(self, batch_id: str, chunk_index: int, jobs: List[Job], known_translations: List[dict]) -> BatchUnit:
        context_text = self._build_context_from_jobs(jobs)
        return self._build_chunk_unit_from_context(batch_id, chunk_index, jobs, known_translations, context_text)

    def _build_chunk_unit_from_context(self, batch_id: str, chunk_index: int, jobs: List[Job], known_translations: List[dict], context_text: str) -> BatchUnit:
        return BatchUnit(
            batch_id=f"{batch_id}#{chunk_index}",
            parent_batch_id=batch_id,
            chunk_index=chunk_index,
            jobs=jobs,
            context_text=context_text[:self.max_chars],
            breadcrumb=self._build_breadcrumb(jobs),
            known_translations=known_translations,
        )
