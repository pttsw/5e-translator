import os
import sys
import types

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_DATABASE", "test")
os.environ.setdefault("5ETOOLS_EN_PATH", "/tmp")
os.environ.setdefault("OUTPUT_PATH", "/tmp")
os.environ.setdefault("CHM_ROOT_DIR", "/tmp")
if "app" not in sys.modules:
    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [APP_ROOT]
    sys.modules["app"] = app_pkg
if "app.core" not in sys.modules:
    core_pkg = types.ModuleType("app.core")
    core_pkg.__path__ = [os.path.join(APP_ROOT, "core")]
    sys.modules["app.core"] = core_pkg
if "app.core.translator" not in sys.modules:
    translator_pkg = types.ModuleType("app.core.translator")
    translator_pkg.__path__ = [os.path.join(APP_ROOT, "core", "translator")]
    sys.modules["app.core.translator"] = translator_pkg
if "app.core.utils" not in sys.modules:
    utils_pkg = types.ModuleType("app.core.utils")
    utils_pkg.__path__ = [os.path.join(APP_ROOT, "core", "utils")]
    sys.modules["app.core.utils"] = utils_pkg
if "dotenv" not in sys.modules:
    dotenv_pkg = types.ModuleType("dotenv")
    dotenv_pkg.load_dotenv = lambda *args, **kwargs: None
    dotenv_pkg.dotenv_values = lambda *args, **kwargs: {}
    sys.modules["dotenv"] = dotenv_pkg
if "flasgger" not in sys.modules:
    flasgger_pkg = types.ModuleType("flasgger")
    class _Swagger:
        DEFAULT_CONFIG = {}

        def __init__(self, *args, **kwargs):
            pass

        def init_app(self, *args, **kwargs):
            return None

    flasgger_pkg.Swagger = _Swagger
    sys.modules["flasgger"] = flasgger_pkg

from app.core.utils.job import Job as _BootstrapJob
from app.core.utils.file_work_info import FileWorkInfo as _BootstrapFileWorkInfo
from app.core.utils.status import TranslatorStatus as _BootstrapTranslatorStatus
from app.core.utils.utils import format_llm_msg as _bootstrap_format_llm_msg
from app.core.utils.utils import replace_cn_pattern as _bootstrap_replace_cn_pattern
from app.core.utils.utils import need_translate_str as _bootstrap_need_translate_str
from app.core.utils.utils import check_prefix as _bootstrap_check_prefix
from app.core.utils.utils import check_suffix as _bootstrap_check_suffix
from app.core.utils.utils import parse_custom_format as _bootstrap_parse_custom_format
from app.core.utils.utils import parse_foundry_items_uuid_format as _bootstrap_parse_foundry_items_uuid_format
from app.core.utils.reference_finder import find_reference as _bootstrap_find_reference

sys.modules["app.core.utils"].Job = _BootstrapJob
sys.modules["app.core.utils"].FileWorkInfo = _BootstrapFileWorkInfo
sys.modules["app.core.utils"].TranslatorStatus = _BootstrapTranslatorStatus
sys.modules["app.core.utils"].format_llm_msg = _bootstrap_format_llm_msg
sys.modules["app.core.utils"].replace_cn_pattern = _bootstrap_replace_cn_pattern
sys.modules["app.core.utils"].need_translate_str = _bootstrap_need_translate_str
sys.modules["app.core.utils"].check_prefix = _bootstrap_check_prefix
sys.modules["app.core.utils"].check_suffix = _bootstrap_check_suffix
sys.modules["app.core.utils"].parse_custom_format = _bootstrap_parse_custom_format
sys.modules["app.core.utils"].parse_foundry_items_uuid_format = _bootstrap_parse_foundry_items_uuid_format
sys.modules["app.core.utils"].find_reference = _bootstrap_find_reference

from app.core.translator.batch_chunker import BatchChunker, BatchUnit
from app.core.translator.batch_job_processor import BatchJobProcessor
from app.core.utils.file_work_info import FileWorkInfo
from app.core.utils.job import Job


def make_job(uid, en_str, *, key_path, entry_path=None, need_translate=True, cn_str=None, batch_seq=-1):
    job = Job(
        uid=uid,
        en_str=en_str,
        cn_str=cn_str,
        rel_path="adventure/adventure-wdh.json",
        tag="",
        knowledge=[],
        current_names=[],
        key_path=key_path,
        entry_path=entry_path or key_path,
        batch_seq=batch_seq,
    )
    job.need_translate = need_translate
    return job


def test_batch_chunker_keeps_original_split_unit_when_under_threshold():
    file_info = FileWorkInfo(
        job_list=[
            make_job(
                "$.spell[0].entries[0]",
                "You hurl a bubble of acid.",
                key_path="/spell[0]/entries[0]",
                batch_seq=0,
            )
        ],
        json_obj={
            "spell": [
                {
                    "name": "Acid Splash",
                    "source": "PHB",
                    "entries": [
                        "{!@ $.spell[0].entries[0]}",
                        "This spell's damage increases by {@damage 1d6}.",
                    ],
                }
            ]
        },
        json_path="spells/spells-phb.json",
        out_path="phb/spell/acid-splash.json",
    )

    chunker = BatchChunker(max_chars=12000)
    units = chunker.build_units(file_info)

    assert len(units) == 1
    assert units[0].batch_id == "phb/spell/acid-splash.json"
    assert units[0].jobs[0].uid == "$.spell[0].entries[0]"
    assert "Acid Splash" in units[0].context_text
    assert "You hurl a bubble of acid." in units[0].context_text


def test_batch_chunker_resplits_large_adventure_unit_by_structure_block():
    first_section_text = "A simple question began rattling around in my head. " * 180
    second_section_text = "Pronunciation guides help at the table. " * 180
    first_uid = "$.data[0].entries[0]"
    second_uid = "$.data[1].entries[0]"
    file_info = FileWorkInfo(
        job_list=[
            make_job(
                first_uid,
                first_section_text,
                key_path="/data[0]/entries[0]",
                entry_path="/data[0]",
                batch_seq=0,
            ),
            make_job(
                second_uid,
                second_section_text,
                key_path="/data[1]/entries[0]",
                entry_path="/data[1]",
                batch_seq=1,
            ),
        ],
        json_obj={
            "data": [
                {
                    "type": "section",
                    "name": "Foreword",
                    "entries": [
                        "{!@ $.data[0].entries[0]}",
                    ],
                },
                {
                    "type": "entries",
                    "name": "Pronunciation Guide",
                    "entries": [
                        "{!@ $.data[1].entries[0]}",
                    ],
                },
            ]
        },
        json_path="adventure/adventure-wdh.json",
        out_path="wdh/adventure/000.json",
    )

    chunker = BatchChunker(max_chars=12000)
    units = chunker.build_units(file_info)

    assert len(units) == 2
    assert units[0].batch_id == "wdh/adventure/000.json#0"
    assert units[1].batch_id == "wdh/adventure/000.json#1"
    assert [job.uid for job in units[0].jobs] == [first_uid]
    assert [job.uid for job in units[1].jobs] == [second_uid]
    assert "Foreword" in units[0].context_text
    assert "Pronunciation Guide" in units[1].context_text


def test_batch_chunker_falls_back_to_job_grouping_when_no_structural_block_matches():
    jobs = [
        make_job(
            "$.adventure.text.0",
            "alpha " * 1500,
            key_path="/adventure/text[0]",
            entry_path="/unmatched[0]",
            batch_seq=0,
        ),
        make_job(
            "$.adventure.text.1",
            "beta " * 1500,
            key_path="/adventure/text[1]",
            entry_path="/unmatched[1]",
            batch_seq=1,
        ),
    ]
    file_info = FileWorkInfo(
        job_list=jobs,
        json_obj={"adventure": {"text": ["{!@ $.adventure.text.0}", "{!@ $.adventure.text.1}"]}},
        json_path="adventure/adventure-wdh.json",
        out_path="wdh/adventure/fallback.json",
    )

    chunker = BatchChunker(max_chars=1200)
    units = chunker.build_units(file_info)

    assert len(units) == 2
    assert units[0].jobs[0].uid == "$.adventure.text.0"
    assert units[1].jobs[0].uid == "$.adventure.text.1"


def test_batch_chunker_split_retry_unit_prefers_entry_boundaries():
    jobs = [
        make_job("$.data[0].entries[0]", "alpha " * 400, key_path="/data[0]/entries[0]", entry_path="/data[0]", batch_seq=0),
        make_job("$.data[0].entries[1]", "beta " * 400, key_path="/data[0]/entries[1]", entry_path="/data[0]", batch_seq=1),
        make_job("$.data[1].entries[0]", "gamma " * 400, key_path="/data[1]/entries[0]", entry_path="/data[1]", batch_seq=2),
        make_job("$.data[1].entries[1]", "delta " * 400, key_path="/data[1]/entries[1]", entry_path="/data[1]", batch_seq=3),
    ]
    chunker = BatchChunker(max_chars=1200)
    batch_unit = BatchUnit(
        batch_id="wdh/adventure/retry.json#0",
        parent_batch_id="wdh/adventure/retry.json",
        chunk_index=0,
        jobs=jobs,
        context_text="retry context",
    )
    child_units = chunker.split_retry_unit(batch_unit)

    assert len(child_units) == 2
    assert [job.uid for job in child_units[0].jobs] == ["$.data[0].entries[0]", "$.data[0].entries[1]"]
    assert [job.uid for job in child_units[1].jobs] == ["$.data[1].entries[0]", "$.data[1].entries[1]"]


def test_batch_job_processor_rejects_missing_uid_in_response():
    processor = BatchJobProcessor.__new__(BatchJobProcessor)
    batch_unit = BatchChunker(max_chars=12000).build_units(
        FileWorkInfo(
            job_list=[
                make_job("$.spell[0].entries[0]", "You hurl a bubble of acid.", key_path="/spell[0]/entries[0]", batch_seq=0),
                make_job("$.spell[0].entries[1]", "This spell's damage increases.", key_path="/spell[0]/entries[1]", batch_seq=1),
            ],
            json_obj={
                "spell": [
                    {"name": "Acid Splash", "entries": ["{!@ $.spell[0].entries[0]}", "{!@ $.spell[0].entries[1]}"]}
                ]
            },
            json_path="spells/spells-phb.json",
            out_path="phb/spell/acid-splash.json",
        )
    )[0]

    invalid_msg = (
        '{"batch_id":"phb/spell/acid-splash.json","source_hash":"%s",'
        '"items":[{"uid":"$.spell[0].entries[0]","trans_str":"你投出一个酸液气泡。"}],"add_terms":{}}'
        % batch_unit.source_hash
    )

    assert processor._parse_batch_response(invalid_msg, batch_unit) is None


def test_batch_job_processor_rejects_duplicate_uid_in_response():
    processor = BatchJobProcessor.__new__(BatchJobProcessor)
    batch_unit = BatchChunker(max_chars=12000).build_units(
        FileWorkInfo(
            job_list=[
                make_job("$.spell[0].entries[0]", "You hurl a bubble of acid.", key_path="/spell[0]/entries[0]", batch_seq=0),
                make_job("$.spell[0].entries[1]", "This spell's damage increases.", key_path="/spell[0]/entries[1]", batch_seq=1),
            ],
            json_obj={
                "spell": [
                    {"name": "Acid Splash", "entries": ["{!@ $.spell[0].entries[0]}", "{!@ $.spell[0].entries[1]}"]}
                ]
            },
            json_path="spells/spells-phb.json",
            out_path="phb/spell/acid-splash.json",
        )
    )[0]

    invalid_msg = (
        '{"batch_id":"phb/spell/acid-splash.json","source_hash":"%s","items":['
        '{"uid":"$.spell[0].entries[0]","trans_str":"你投出一个酸液气泡。"},'
        '{"uid":"$.spell[0].entries[0]","trans_str":"该法术的伤害会提高。"}],"add_terms":{}}'
        % batch_unit.source_hash
    )

    assert processor._parse_batch_response(invalid_msg, batch_unit) is None


def test_batch_job_processor_applies_reordered_items_by_uid():
    processor = BatchJobProcessor.__new__(BatchJobProcessor)
    processor.cache = False
    processor._JobProcessor__replace_sub_jobs = lambda cn_str, en_str=None, tag="": (cn_str, True)

    job_one = make_job(
        "$.spell[0].entries[0]",
        "You hurl a bubble of acid.",
        key_path="/spell[0]/entries[0]",
        batch_seq=0,
    )
    job_two = make_job(
        "$.spell[0].entries[1]",
        "This spell's damage increases by {@damage 1d6}.",
        key_path="/spell[0]/entries[1]",
        batch_seq=1,
    )
    batch_unit = BatchChunker(max_chars=12000).build_units(
        FileWorkInfo(
            job_list=[job_one, job_two],
            json_obj={
                "spell": [
                    {"name": "Acid Splash", "entries": ["{!@ $.spell[0].entries[0]}", "{!@ $.spell[0].entries[1]}"]}
                ]
            },
            json_path="spells/spells-phb.json",
            out_path="phb/spell/acid-splash.json",
        )
    )[0]

    msg = (
        '{"batch_id":"phb/spell/acid-splash.json","source_hash":"%s","items":['
        '{"uid":"$.spell[0].entries[1]","trans_str":"该法术的伤害会提高{@damage 1d6}。"},'
        '{"uid":"$.spell[0].entries[0]","trans_str":"你投出一个酸液气泡。"}],"add_terms":{}}'
        % batch_unit.source_hash
    )

    parsed = processor._parse_batch_response(msg, batch_unit)
    assert parsed is not None
    assert processor._apply_batch_response(batch_unit, parsed) is True
    assert job_one.cn_str == "你投出一个酸液气泡。"
    assert job_two.cn_str == "该法术的伤害会提高{@damage 1d6}。"


def test_batch_job_processor_rejects_brace_count_mismatch():
    processor = BatchJobProcessor.__new__(BatchJobProcessor)
    processor.cache = False
    processor._JobProcessor__replace_sub_jobs = lambda cn_str, en_str=None, tag="": (cn_str, True)

    job = make_job(
        "$.spell[0].entries[1]",
        "This spell's damage increases by {@damage 1d6}.",
        key_path="/spell[0]/entries[1]",
        batch_seq=0,
    )
    batch_unit = BatchChunker(max_chars=12000).build_units(
        FileWorkInfo(
            job_list=[job],
            json_obj={"spell": [{"name": "Acid Splash", "entries": ["{!@ $.spell[0].entries[1]}"]}]},
            json_path="spells/spells-phb.json",
            out_path="phb/spell/acid-splash.json",
        )
    )[0]
    parsed = {
        "batch_id": batch_unit.batch_id,
        "source_hash": batch_unit.source_hash,
        "items": [{"uid": job.uid, "trans_str": "该法术的伤害会提高damage 1d6。"}],
        "add_terms": {},
    }

    assert processor._apply_batch_response(batch_unit, parsed) is False
