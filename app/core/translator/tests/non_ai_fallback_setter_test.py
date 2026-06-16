from app.core.translator.non_ai_fallback_setter import NonAiFallbackSetter
from app.core.translator.json_generator import JsonGenerator
from app.core.utils.file_work_info import FileWorkInfo
from app.core.utils.job import Job


def test_non_ai_fallback_setter_only_fills_database_misses():
    translated = Job(uid="1", en_str="Fireball", cn_str="火球术", sql_id=1)
    missing = Job(
        uid="2",
        en_str="Cast {@spell Fireball}.",
        cn_str=None,
        sql_id=None,
    )
    file_info = FileWorkInfo(
        job_list=[translated, missing],
        json_obj={},
        json_path="book/example.json",
        out_path="book/example.json",
    )

    result = list(NonAiFallbackSetter().invoke([file_info]))

    assert result == [file_info]
    assert translated.cn_str == "火球术"
    assert missing.cn_str == "Cast {@spell Fireball}."


def test_json_generator_translates_known_tags_after_english_fallback():
    tag_job = Job(
        uid="tag",
        en_str="Fireball",
        cn_str="火球术",
        tag="spell",
        sql_id=1,
    )
    sentence_job = Job(
        uid="sentence",
        en_str="Cast {@spell Fireball} now.",
        cn_str=None,
        sql_id=None,
    )
    file_info = FileWorkInfo(
        job_list=[tag_job, sentence_job],
        json_obj={"entries": ["{!@ sentence}"]},
        json_path="book/example.json",
        out_path="book/example.json",
    )
    list(NonAiFallbackSetter().invoke([file_info]))

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = file_info.job_list
    generator.dictionary = None
    cn_obj, ok = generator._JsonGenerator__replace_jobs(file_info.json_obj)

    assert ok is True
    assert cn_obj["entries"] == ["Cast {@spell 火球术} now."]
