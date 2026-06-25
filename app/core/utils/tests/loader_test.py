import json
from datetime import datetime

from app.core.utils import FileWorkInfo
from app.core.utils.loader import (
    _find_json_files,
    clean_5etools_json,
    find_files,
    order_cn_entity_fields,
    write_file_work_infos,
)


class _JobWithDatetime:
    def __init__(self):
        self.uid = "job-1"
        self.modified_at = datetime(2026, 6, 22, 13, 45, 0)


def test_find_files_skips_matching_file_names_in_directory(tmp_path):
    included = tmp_path / "include.json"
    skipped = tmp_path / "Death's Friend.json"
    matching_directory = tmp_path / "Death's Friend directory"
    matching_directory.mkdir()
    nested = matching_directory / "nested.json"

    included.write_text("{}", encoding="utf-8")
    skipped.write_text("{}", encoding="utf-8")
    nested.write_text("{}", encoding="utf-8")

    results = list(find_files(str(tmp_path), skip_file_name="Death's Friend"))

    assert results == [str(included), str(nested)]


def test_find_files_skips_a_matching_single_file(tmp_path):
    skipped = tmp_path / "prefix-skip-me-suffix.json"
    skipped.write_text("{}", encoding="utf-8")

    assert list(find_files(str(skipped), skip_file_name="skip-me")) == []


def test_find_json_files_runnable_input_remains_backward_compatible(tmp_path):
    json_file = tmp_path / "sample.json"
    json_file.write_text("{}", encoding="utf-8")

    assert list(_find_json_files(str(tmp_path))) == [str(json_file)]
    assert list(_find_json_files((str(tmp_path), "sample"))) == []


def test_order_cn_entity_fields_prioritizes_entity_name_fields_recursively():
    obj = {
        "entries": [
            {
                "source": "XDMG",
                "type": "item",
                "entries": [
                    {
                        "page": 1,
                        "name": "Inner",
                        "type": "section",
                        "ENG_name": "Inner",
                    }
                ],
                "name": "Arcane Study",
                "ENG_name": "Arcane Study",
            }
        ],
        "name": "Root without type",
        "ENG_name": "Root without type",
    }

    ordered = order_cn_entity_fields(obj)

    assert list(ordered.keys()) == ["entries", "name", "ENG_name"]
    assert list(ordered["entries"][0].keys()) == [
        "ENG_name",
        "name",
        "type",
        "source",
        "entries",
    ]
    assert list(ordered["entries"][0]["entries"][0].keys()) == [
        "ENG_name",
        "name",
        "type",
        "page",
    ]


def test_write_file_work_infos_orders_final_cn_json(tmp_path):
    file_info = FileWorkInfo(
        job_list=[],
        json_obj={},
        json_path="source.json",
        out_path="out.json",
    )
    file_info.cn_obj = {
        "entity": {
            "type": "item",
            "other": True,
            "name": "中文名",
            "ENG_name": "English Name",
        }
    }

    write_file_work_infos(file_info, out_path=str(tmp_path))

    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert list(written["entity"].keys()) == ["ENG_name", "name", "type", "other"]


def test_clean_5etools_json_matches_clean_jsons_formatting():
    cleaned = clean_5etools_json({
        "text": "“Smart”—…\u200B",
        "items": ["A", "B"],
    })

    assert cleaned.endswith("\n")
    assert '\n\t"text": "\\"Smart\\"\\u2014...",' in cleaned
    assert "\u200B" not in json.loads(cleaned)["text"]


def test_write_file_work_infos_uses_5etools_json_format(tmp_path):
    file_info = FileWorkInfo(
        job_list=[],
        json_obj={"text": "A—B"},
        json_path="source.json",
        out_path="out.json",
    )
    file_info.cn_obj = {"text": "C—D"}

    write_file_work_infos(file_info, out_path=str(tmp_path))

    en_content = (tmp_path / "out.json.en").read_text(encoding="utf-8")
    cn_content = (tmp_path / "out.json").read_text(encoding="utf-8")
    jobs_content = (tmp_path / "out.json.jobs").read_text(encoding="utf-8")
    assert en_content == '{\n\t"text": "A\\u2014B"\n}\n'
    assert cn_content == '{\n\t"text": "C\\u2014D"\n}\n'
    assert jobs_content == "[]\n"


def test_write_file_work_infos_serializes_datetime_in_jobs(tmp_path):
    file_info = FileWorkInfo(
        job_list=[_JobWithDatetime()],
        json_obj={},
        json_path="source.json",
        out_path="out.json",
    )

    write_file_work_infos(file_info, out_path=str(tmp_path))

    jobs = json.loads((tmp_path / "out.json.jobs").read_text(encoding="utf-8"))
    assert jobs[0]["modified_at"] == "2026-06-22T13:45:00"
