from app.core.translator.job_processor import JobProcessor
from app.core.utils import FileWorkInfo, Job


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


class FakeFactory:
    def __init__(self):
        self.jobs = []
        self.started = False
        self.reset_called = False

    def reset(self):
        self.reset_called = True

    def set_progress_context(self, label):
        self.label = label

    def add_jobs(self, jobs):
        self.jobs.extend(jobs)

    def set_finish(self, add_finish):
        self.add_finish = add_finish

    def start_work(self):
        self.started = True

    def isAllDone(self):
        return True


def test_processor_skips_factory_when_no_jobs_need_translation():
    processor = JobProcessor(thread_num=1, update=False, use_ai=False)
    fake_factory = FakeFactory()
    processor._JobProcessor__init_factory = lambda: setattr(processor, "factory", fake_factory) or True
    ready = Job("ready", "Hit.", "命中。", rel_path="bestiary/test.json", tag="entries")
    file_info = FileWorkInfo([ready], {}, "test.json", "test.json")

    result = list(processor.invoke([file_info], config={"metadata": {"use_ai": False}}))

    assert result == [file_info]
    assert processor.done_jobs == [ready]
    assert fake_factory.started is False
    assert fake_factory.jobs == []


def test_processor_only_queues_jobs_that_need_translation():
    processor = JobProcessor(thread_num=1, update=False, use_ai=False)
    fake_factory = FakeFactory()
    processor._JobProcessor__init_factory = lambda: setattr(processor, "factory", fake_factory) or True
    ready = Job("ready", "Hit.", "命中。", rel_path="bestiary/test.json", tag="entries")
    pending = make_pending_job("pending")
    file_info = FileWorkInfo([ready, pending], {}, "test.json", "test.json")

    result = list(processor.invoke([file_info], config={"metadata": {"use_ai": False, "cache": False}}))

    assert result == [file_info]
    assert processor.done_jobs == [ready]
    assert fake_factory.started is True
    assert fake_factory.jobs == [pending]


def test_processor_validates_llm_answer_without_reloading_stale_parent_translation():
    en_str = (
        "Creatures from the {@table Elemental Earth Encounters|BGG} table "
        "arrive (see {@book chapter 3|BGG|3})."
    )
    stale_cn = (
        "来自{@book Elemental Earth Encounters table|BGG|3|元素土遭遇表}中的"
        "生物出现了（见{@book chapter 3|BGG|3}）。"
    )
    llm_answer = (
        "来自{@table Elemental Earth Encounters|BGG}表格中的生物出现了"
        "（见{@book chapter 3|BGG|3}）。"
    )

    processor = JobProcessor.__new__(JobProcessor)
    processor.done_jobs = [
        Job("parent", en_str, stale_cn, tag="book", sql_id=265051)
    ]
    processor.dictionary = type(
        "FakeDictionary",
        (),
        {"get": lambda self, en, tag="": (None, False)},
    )()

    result, ok = processor._JobProcessor__replace_sub_jobs(
        llm_answer,
        en_str,
        tag="book",
        process_full_value=False,
    )

    assert ok is True
    assert result == llm_answer
