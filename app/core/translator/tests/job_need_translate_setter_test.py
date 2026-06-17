from app.core.translator.job_need_translate_setter import JobNeedTranslateSetter
from app.core.utils.job import Job


def test_tag_only_update_jobs_do_not_go_back_to_llm():
    job = Job(
        uid="1",
        en_str="The fastest way is by {@item dogsled|IDRotF}.",
        cn_str="最快的方式是乘坐dogsled。",
        sql_id=42,
        tag="adventure",
        tag_sync_required=True,
        old_en_str="The fastest way is by dogsled.",
    )
    setter = JobNeedTranslateSetter()

    assert setter._JobNeedTranslateSetter__need_translate_job(job) is False


def test_tag_only_update_jobs_fall_back_to_llm_when_cn_has_no_safe_target():
    job = Job(
        uid="1",
        en_str="The fastest way is by {@item dogsled|IDRotF}.",
        cn_str="最快的方式是乘坐狗拉雪橇。",
        sql_id=42,
        tag="adventure",
        tag_sync_required=True,
        old_en_str="The fastest way is by dogsled.",
    )
    setter = JobNeedTranslateSetter()

    assert setter._JobNeedTranslateSetter__need_translate_job(job) is True


def test_tag_only_update_jobs_use_other_job_translation_as_safe_target():
    job = Job(
        uid="1",
        en_str="The fastest way is by {@item dogsled|IDRotF}.",
        cn_str="最快的方式是乘坐狗拉雪橇。",
        sql_id=42,
        tag="adventure",
        tag_sync_required=True,
        old_en_str="The fastest way is by dogsled.",
    )
    setter = JobNeedTranslateSetter()
    setter.done_jobs = [
        Job(uid="term", en_str="dogsled", cn_str="狗拉雪橇", tag="item")
    ]

    assert setter._JobNeedTranslateSetter__need_translate_job(job) is False


def test_tag_only_update_jobs_use_other_job_translation_as_safe_target():
    job = Job(
        uid="1",
        en_str="The fastest way is by {@item dogsled|IDRotF}.",
        cn_str="最快的方式是乘坐狗拉雪橇。",
        sql_id=42,
        tag="adventure",
        tag_sync_required=True,
        old_en_str="The fastest way is by dogsled.",
    )
    setter = JobNeedTranslateSetter()
    setter.done_jobs = [
        Job(uid="term", en_str="dogsled", cn_str="狗拉雪橇", tag="item")
    ]

    assert setter._JobNeedTranslateSetter__need_translate_job(job) is False


def test_tag_only_update_jobs_can_sync_changed_existing_cn_tag():
    job = Job(
        uid="1",
        en_str="They are not {@status Surprised|XPHB}.",
        cn_str="他们不会被{@table 突袭|RMR|突袭}。",
        sql_id=42,
        tag="adventure",
        tag_sync_required=True,
        old_en_str="They are not {@table Surprise|RMR|Surprised}.",
    )
    setter = JobNeedTranslateSetter()

    assert setter._JobNeedTranslateSetter__need_translate_job(job) is False
