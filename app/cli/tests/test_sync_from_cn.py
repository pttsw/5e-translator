import json

import pytest

from app.cli.sync_from_cn import align_manual_translations, sync_from_cn
from app.core.database.memory_db_dictionary import MemoryDBDictionary
from app.core.translator import JsonGenerator
from app.core.utils import FileWorkInfo, Job


def _reset_memory_db(monkeypatch):
    monkeypatch.setenv("TRANSLATOR_DB_BACKEND", "memory")
    monkeypatch.setenv("TRANSLATOR_DISABLE_REDIS", "1")
    MemoryDBDictionary._instance = None
    return MemoryDBDictionary(source="GPT")


def _write_pair(tmp_path):
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    en_obj = {
        "spell": [{
            "name": "Magic Bolt",
            "source": "TST",
            "entries": ["A bright bolt hits the target."],
        }]
    }
    cn_obj = {
        "spell": [{
            "ENG_name": "Magic Bolt",
            "name": "魔法飞弹",
            "source": "TST",
            "entries": ["一道明亮的飞弹击中目标。"],
        }]
    }
    en_path.write_text(json.dumps(en_obj), encoding="utf-8")
    cn_path.write_text(json.dumps(cn_obj, ensure_ascii=False), encoding="utf-8")
    return en_path, cn_path


def test_align_manual_translations_extracts_direct_markers():
    parent = Job("$.entry", "Use {@spell Fireball|PHB|fireball}.", None, tag="spell")
    child0 = Job("$.entry.spell[0].0", "Fireball", None, tag="spell")
    child1 = Job("$.entry.spell[0].1", "PHB", None, tag="spell")
    child2 = Job("$.entry.spell[0].2", "fireball", None, tag="spell")

    result = align_manual_translations(
        {"entry": "{!@ $.entry}"},
        {"entry": "使用 {@spell 火球术|PHB|火球术}。"},
        [parent, child0, child1, child2],
    )

    assert result.errors == []
    assert result.translations == {"$.entry": "使用 {@spell 火球术|PHB|火球术}。"}


def test_generator_preserves_proofread_text_with_intentionally_omitted_repeated_tag(monkeypatch):
    _reset_memory_db(monkeypatch)
    en = "Gain {@condition Poisoned|XPHB}; while poisoned, repeat the save."
    cn = "陷入{@condition 中毒|XPHB}；中毒期间重复豁免。"
    job = Job("$.entry", en, cn, is_proofread=True, exact_translation=True)
    info = FileWorkInfo([job], {"entry": "{!@ $.entry}"}, "sample.json", "sample.json")

    generated = next(JsonGenerator(1).invoke(
        [info], config={"metadata": {"mode": "5et", "generator_update_db": False}}
    ))

    assert generated.cn_obj["entry"] == cn


def test_sync_from_cn_adds_missing_and_reproduces_manual_json(tmp_path, monkeypatch):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path)
    )

    assert summary.added == 2
    assert summary.verified is True
    assert {row["cn"] for row in db.store_records} == {"魔法飞弹", "一道明亮的飞弹击中目标。"}
    assert {row["proofread"] for row in db.store_records} == {0}

    repeated = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path)
    )
    assert repeated.added == 0
    assert repeated.matched == 2
    assert repeated.verified is True


@pytest.mark.parametrize(
    ("decision", "expected_cn", "added", "updated", "kept"),
    [
        ("add", "魔法飞弹", 2, 0, 0),
        ("modify", "魔法飞弹", 1, 1, 0),
        ("keep", "旧译名", 1, 0, 1),
    ],
)
def test_sync_from_cn_applies_conflict_decision(
    tmp_path, monkeypatch, decision, expected_cn, added, updated, kept
):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    db.put("Magic Bolt", "旧译名", "sample.json", proofread=True, tag="spell")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=lambda *_: decision,
    )

    context = Job(
        "$.spell[name=Magic-Bolt;source=TST].name.0",
        "Magic Bolt", None, rel_path="sample.json", tag="spell",
        key_path="/spell[name=Magic-Bolt;source=TST]/name",
    )
    bean = db.get(
        "Magic Bolt", rel_f="sample.json", load_from_sql=True,
        tag="spell", job_context=context,
    )
    assert bean["cn"] == expected_cn
    if decision in {"add", "modify"}:
        assert bean["proofread"] == 0
    else:
        assert bean["proofread"] == 1
    assert (summary.added, summary.updated, summary.kept_database) == (added, updated, kept)
    assert summary.verified is True


def test_sync_from_cn_automatically_keeps_database_when_manual_equals_english(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    manual_obj = json.loads(cn_path.read_text(encoding="utf-8"))
    manual_obj["spell"][0]["name"] = "Magic Bolt"
    cn_path.write_text(json.dumps(manual_obj, ensure_ascii=False), encoding="utf-8")
    db.put("Magic Bolt", "旧译名", "sample.json", proofread=True, tag="spell")

    def unexpected_prompt(*_args):
        raise AssertionError("英文原文占位不应进入冲突确认")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=unexpected_prompt,
    )

    assert summary.conflicts == 1
    assert summary.kept_database == 1
    assert summary.added == 1
    assert summary.verified is True
