from app.core.translator.llm_factory import LLMFactory
from app.core.utils import Job, TranslatorStatus


def test_factory_aborts_remaining_jobs_on_fatal_status():
    processed = []

    def work_func(job, worker_id):
        processed.append(job.uid)
        return None, TranslatorStatus.FATAL

    factory = LLMFactory(
        work_num=1,
        work_func=work_func,
        done_func=lambda job: None,
        all_done_func=lambda jobs: None,
    )
    jobs = [Job(uid=str(i), en_str=f"job-{i}", cn_str="") for i in range(3)]

    factory.add_jobs(jobs)
    factory.set_finish(True)
    factory.start_work()

    assert processed == ["0"]
    assert factory.finish_count == 0
    assert factory.error_count == 3
    assert len(factory.failed_jobs) == 3
