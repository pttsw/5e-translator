from app.core.translator.job_processor import JobProcessor
from app.core.utils import Job


def make_pending_job(uid):
    job = Job(uid, "Hit.", None, rel_path="bestiary/test.json", tag="entries")
    job.need_translate = True
    return job


def test_processor_dedupes_missing_jobs_but_keeps_aliases():
    processor = JobProcessor(thread_num=1, update=False, use_ai=False)
    processor.use_ai = True
    processor.byhand = False
    first = make_pending_job("first")
    second = make_pending_job("second")

    work_jobs = processor._dedupe_translation_jobs([first, second])

    assert work_jobs == [first]
    assert processor.translation_aliases == {"first": [second]}


def test_processor_propagates_representative_translation_to_alias():
    processor = JobProcessor(thread_num=1, update=False, use_ai=False)
    processor.use_ai = True
    processor.byhand = False
    first = make_pending_job("first")
    second = make_pending_job("second")
    processor._dedupe_translation_jobs([first, second])
    first.cn_str = "命中。"
    first.sql_id = 7
    processor.done_jobs = [first]

    processor._propagate_translation_aliases()

    assert second.cn_str == "命中。"
    assert second.sql_id == 7
    assert second in processor.done_jobs
