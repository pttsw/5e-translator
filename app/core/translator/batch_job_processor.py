import json
import os
from typing import List

from config import OUT_PATH, PROMOT_BATCH, logger
from app.core.utils import FileWorkInfo
from app.core.utils import Job, TranslatorStatus, format_llm_msg, replace_cn_pattern
from app.core.bean.term import to_terms
from .batch_chunker import BatchChunker, BatchUnit
from .job_processor import JobProcessor


class BatchJobProcessor(JobProcessor):
    def __init__(self, thread_num: int = 10, update: bool = False, use_ai: bool = True):
        super().__init__(thread_num=thread_num, update=update, use_ai=use_ai)
        self.batch_max_chars = 12000

    def invoke(self, input, config=None, **kwargs):
        config = config or {"metadata": {}}
        if not config['metadata'].get('use_ai', self.use_ai):
            yield from super().invoke(input, config=config, **kwargs)
            return

        inputs = [input] if isinstance(input, str) else input
        self.config = config or {"metadata": {}}
        self.byhand = config['metadata'].get('byhand', False)
        self.force = config['metadata'].get('force', False)
        self.mode = config['metadata'].get('mode', '5et')
        self.cache = config['metadata'].get('cache', True)
        self.batch_max_chars = config['metadata'].get('batch_max_chars', 12000)
        if self.byhand:
            self.thread_num = 1

        self.ok = self._JobProcessor__init_factory()  # type: ignore[attr-defined]
        if not self.ok:
            logger.error("初始化LLM工厂失败")
            return

        self.chunker = BatchChunker(max_chars=self.batch_max_chars)
        for res in inputs:
            logger.info(f"开始批量处理 {res.json_path} 中的Job")
            self.done_jobs = []
            self.factory.reset()
            self._JobProcessor__load_temple_terms(res.job_list, res.out_path)  # type: ignore[attr-defined]
            res.batch_pending_jobs = self._dedupe_translation_jobs(res.job_list)
            units = self.chunker.build_units(res)
            self.factory.set_progress_context(label=f"File {res.out_path}")
            if not units:
                yield res
                continue

            self.factory.work_func = self._build_batch_work_func()  # type: ignore[attr-defined]
            self.factory.done_func = self._build_done_func()  # type: ignore[attr-defined]
            self.factory.add_jobs(units)
            self.factory.set_finish(True)
            self.factory.start_work()

            if not self.factory.isAllDone():
                failed_units = getattr(self.factory, "failed_jobs", [])
                failed_jobs = []
                for unit in failed_units:
                    if isinstance(unit, BatchUnit):
                        failed_jobs.extend(unit.jobs)
                self._dump_failed_batches(res.out_path, [unit for unit in failed_units if isinstance(unit, BatchUnit)])
                fallback_jobs = self._fallback_failed_jobs(res, failed_jobs)
                if fallback_jobs:
                    self._dump_failed_jobs(res.out_path, fallback_jobs)
                    continue
            self._propagate_translation_aliases()
            yield res

    def _build_batch_work_func(self):
        def work_func(batch_unit: BatchUnit, worker_id: int):
            if not batch_unit.jobs:
                return batch_unit, TranslatorStatus.SUCCESS
            return self._process_batch_unit(batch_unit, worker_id)
        return work_func

    def _process_batch_unit(self, batch_unit: BatchUnit, worker_id: int):
        request = self._build_batch_request(batch_unit)
        batch_unit.last_request = request
        logger.info(
            f"批次开始: id={batch_unit.batch_id}, depth={batch_unit.retry_depth}, "
            f"jobs={len(batch_unit.jobs)}, context_chars={len(batch_unit.context_text)}"
        )
        if not self._ensure_adapter():
            batch_unit.failure_reason = "adapter_init_failed"
            return None, TranslatorStatus.FAILURE
        msg, status = self.adapter.sendText(
            request,
            PROMOT_BATCH,
            structured_output=True,
            response_mode="batch",
        )
        batch_unit.last_response = "" if msg is None else str(msg)
        if status != TranslatorStatus.SUCCESS:
            batch_unit.failure_reason = f"adapter_status:{status.name}"
            logger.warning(f"批量翻译失败: {batch_unit.batch_id}, status={status.name}")
            if status == TranslatorStatus.FAILURE:
                sub_ok = self._retry_as_sub_batches(batch_unit, worker_id)
                if sub_ok:
                    return batch_unit, TranslatorStatus.SUCCESS
            return None, status

        batch_data = self._parse_batch_response(msg, batch_unit)
        if batch_data is None:
            batch_unit.failure_reason = "parse_batch_response_failed"
            logger.warning(f"批量结果解析失败: {batch_unit.batch_id}")
            if self._retry_as_sub_batches(batch_unit, worker_id):
                return batch_unit, TranslatorStatus.SUCCESS
            return None, TranslatorStatus.FAILURE

        ok = self._apply_batch_response(batch_unit, batch_data)
        if not ok:
            batch_unit.failure_reason = "apply_batch_response_failed"
            if self._retry_as_sub_batches(batch_unit, worker_id):
                return batch_unit, TranslatorStatus.SUCCESS
            return None, TranslatorStatus.FAILURE

        logger.info(f"批次完成: id={batch_unit.batch_id}, jobs={len(batch_unit.jobs)}")
        return batch_unit, TranslatorStatus.SUCCESS

    def _build_done_func(self):
        def done_func(batch_unit: BatchUnit):
            if batch_unit is None:
                logger.error("处理完成批次错误: None")
                return
            for job in batch_unit.jobs:
                if not self._store_completed_job(job):
                    logger.error(f"处理完成批次Job错误: {job}")
        return done_func

    def _build_batch_request(self, batch_unit: BatchUnit) -> str:
        reference = []
        parents = []
        for job in batch_unit.jobs:
            parents.extend(job.current_names)
            reference.extend(job.knowledge)
            reference.extend([f"{term.en}:{term.cn}" for term in job.terms])
            if self.cache:
                for temp in self._JobProcessor__search_temple_terms(job.en_str):  # type: ignore[attr-defined]
                    if temp not in job.terms:
                        job.terms.append(temp)
        payload = {
            "batch_id": batch_unit.batch_id,
            "source_hash": batch_unit.source_hash,
            "breadcrumb": batch_unit.breadcrumb,
            "parents": parents,
            "reference": reference,
            "context": batch_unit.context_text,
            "known_translations": batch_unit.known_translations,
            "items": [
                {"uid": job.uid, "seq": job.batch_seq, "en_str": job.en_str, "tag": job.tag}
                for job in batch_unit.jobs
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _retry_as_sub_batches(self, batch_unit: BatchUnit, worker_id: int) -> bool:
        if len(batch_unit.jobs) <= 1:
            return False
        child_units = self.chunker.split_retry_unit(batch_unit)
        if len(child_units) < 2:
            return False

        logger.warning(
            f"批次拆分重试: id={batch_unit.batch_id}, depth={batch_unit.retry_depth}, "
            f"children={[len(child.jobs) for child in child_units]}"
        )
        for child_unit in child_units:
            _, status = self._process_batch_unit(child_unit, worker_id)
            if status != TranslatorStatus.SUCCESS:
                return False
        return True

    def _parse_batch_response(self, msg: str, batch_unit: BatchUnit):
        batch_data, ok = format_llm_msg(msg)
        if not ok or not isinstance(batch_data, dict):
            return None
        if batch_data.get("batch_id") != batch_unit.batch_id:
            return None
        if batch_data.get("source_hash") != batch_unit.source_hash:
            return None
        items = batch_data.get("items")
        if not isinstance(items, list):
            return None
        expected_uids = {job.uid for job in batch_unit.jobs}
        response_uids = []
        for item in items:
            if not isinstance(item, dict):
                return None
            uid = item.get("uid")
            if not isinstance(uid, str):
                return None
            response_uids.append(uid)
        if set(response_uids) != expected_uids or len(response_uids) != len(set(response_uids)):
            return None
        return batch_data

    def _apply_batch_response(self, batch_unit: BatchUnit, batch_data: dict) -> bool:
        item_map = {item["uid"]: item for item in batch_data["items"]}
        for job in batch_unit.jobs:
            item = item_map.get(job.uid)
            if item is None:
                return False
            cn_str = replace_cn_pattern(item.get("trans_str"), job.en_str)
            if not isinstance(cn_str, str):
                return False
            if cn_str.count('{') != job.en_str.count('{') or cn_str.count('}') != job.en_str.count('}'):
                job.last_answer = cn_str
                return False
            _, ok = self._JobProcessor__replace_sub_jobs(cn_str, job.en_str, job.tag)  # type: ignore[attr-defined]
            if not ok:
                job.last_answer = cn_str
                return False
            job.cn_str = cn_str
            if self.cache:
                job_terms = to_terms(job.en_str, job.cn_str, job.tag)
                for term in job_terms:
                    self._JobProcessor__add_temple_terms(term.en, term.cn)  # type: ignore[attr-defined]
        if isinstance(batch_data.get("add_terms"), dict):
            for term, cn in batch_data["add_terms"].items():
                self._JobProcessor__add_temple_terms(term, cn)  # type: ignore[attr-defined]
        return True

    def _dump_failed_jobs(self, out_path: str, failed_jobs: List[Job]):
        failed_path = os.path.join(OUT_PATH, out_path + '.failed_jobs.json')
        try:
            os.makedirs(os.path.dirname(failed_path), exist_ok=True)
            with open(failed_path, 'w') as fh:
                json.dump([job.to_serializable() for job in failed_jobs], fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error(f'写出 failed_jobs 文件失败: {exc}')

    def _dump_failed_batches(self, out_path: str, failed_units: List[BatchUnit]):
        failed_path = os.path.join(OUT_PATH, out_path + '.failed_batches.json')
        try:
            os.makedirs(os.path.dirname(failed_path), exist_ok=True)
            payload = []
            for unit in failed_units:
                payload.append({
                    "batch_id": unit.batch_id,
                    "parent_batch_id": unit.parent_batch_id,
                    "chunk_index": unit.chunk_index,
                    "retry_depth": unit.retry_depth,
                    "job_count": len(unit.jobs),
                    "job_uids": [job.uid for job in unit.jobs],
                    "source_hash": unit.source_hash,
                    "failure_reason": unit.failure_reason,
                    "last_request": unit.last_request,
                    "last_response": unit.last_response,
                })
            with open(failed_path, 'w') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error(f'写出 failed_batches 文件失败: {exc}')

    def _fallback_failed_jobs(self, res: FileWorkInfo, failed_jobs: List[Job]) -> List[Job]:
        if not failed_jobs:
            return []

        logger.warning(f"{res.json_path} 中有 {len(failed_jobs)} 个 batch Job 失败，回退到单 Job 流程")
        fallback_file = FileWorkInfo(failed_jobs, res.json_obj, res.json_path, res.out_path)
        fallback_processor = JobProcessor(
            thread_num=self.thread_num,
            update=self.update,
            use_ai=self.use_ai,
        )
        fallback_results = list(fallback_processor.invoke([fallback_file], config=self.config))
        if not fallback_results:
            return failed_jobs

        completed_uids = {
            job.uid
            for fallback_res in fallback_results
            for job in fallback_res.job_list
            if isinstance(job.cn_str, str) and job.cn_str != ""
        }
        unresolved_jobs = [job for job in failed_jobs if job.uid not in completed_uids]
        self.done_jobs.extend([
            job for fallback_res in fallback_results for job in fallback_res.job_list
            if isinstance(job.cn_str, str) and job.cn_str != ""
        ])
        return unresolved_jobs
