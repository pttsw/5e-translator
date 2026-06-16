from unittest.mock import Mock, patch

from app.core.translator.job_processor import JobProcessor
from app.core.utils.job import Job
from app.core.utils.status import TranslatorStatus


def test_non_ai_fallback_keeps_english_and_translates_known_tag_values():
    processor = JobProcessor.__new__(JobProcessor)
    processor.done_jobs = []
    processor.dictionary = Mock()

    def get_translation(en, tag=""):
        translations = {
            ("Fireball", "spell"): ("火球术", True),
        }
        return translations.get((en, tag), (en, False))

    processor.dictionary.get.side_effect = get_translation
    job = Job(
        uid="1",
        en_str="Cast {@spell Fireball} at the {@creature Unknown Beast}.",
        cn_str=None,
        tag="",
        sql_id=None,
    )

    processor._apply_non_ai_fallback(job)

    assert job.cn_str == "Cast {@spell 火球术} at the {@creature Unknown Beast}."


def test_non_ai_fallback_uses_plain_english_when_there_are_no_tags():
    processor = JobProcessor.__new__(JobProcessor)
    processor.done_jobs = []
    processor.dictionary = Mock()
    processor.dictionary.get.return_value = (None, False)
    job = Job(uid="2", en_str="No database translation exists.", cn_str=None)

    processor._apply_non_ai_fallback(job)

    assert job.cn_str == job.en_str


def test_non_ai_processor_does_not_initialize_ai_or_write_fallback_to_database():
    with patch.object(JobProcessor, "_JobProcessor__init_dictionary", return_value=True):
        with patch.object(
            JobProcessor,
            "_JobProcessor__init_adapter",
            return_value=True,
        ) as init_adapter:
            processor = JobProcessor(thread_num=1, update=True, use_ai=False)

    init_adapter.assert_not_called()
    processor.dictionary = Mock()
    processor.dictionary.get.return_value = (None, False)
    processor.done_jobs = []
    processor.byhand = False
    processor.force = True
    processor.cache = False
    processor._JobProcessor__init_factory()
    job = Job(uid="3", en_str="Missing entry.", cn_str=None, sql_id=None)
    job.need_translate = True

    completed_job, status = processor.factory.work_func(job, 0)
    processor.factory.done_func(completed_job)

    assert status == TranslatorStatus.SUCCESS
    assert job.cn_str == job.en_str
    processor.dictionary.put.assert_not_called()
    processor.dictionary.update.assert_not_called()
