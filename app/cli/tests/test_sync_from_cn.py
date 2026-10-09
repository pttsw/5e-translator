import importlib
import json
from pathlib import Path

import pytest

from app.cli.sync_from_cn import (
    SyncConflict,
    SyncSummary,
    _prompt_conflict,
    align_manual_translations,
    build_decision_provider_from_rules,
    classify_alignment,
    preview_sync_from_cn,
    resolve_cn_path,
    sync_from_cn,
    sync_from_cn_path,
    validate_preview_decisions,
)
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
    parent = Job(
        "$.entry", "Use {@spell Fireball|PHB|fireball}.", None,
        tag="spell", key_path="/entry",
    )
    child0 = Job(
        "$.entry.spell[0].0", "Fireball", None,
        tag="spell", key_path="/entry/spell[0]",
    )
    child1 = Job(
        "$.entry.spell[0].1", "PHB", None,
        tag="spell", key_path="/entry/spell[0]",
    )
    child2 = Job(
        "$.entry.spell[0].2", "fireball", None,
        tag="spell", key_path="/entry/spell[0]",
    )

    result = align_manual_translations(
        {"entry": "{!@ $.entry}"},
        {"entry": "使用 {@spell 火球术|PHB|火球术}。"},
        [parent, child0, child1, child2],
    )

    assert result.errors == []
    assert result.translations == {
        "$.entry": "使用 {@spell 火球术|PHB|火球术}。",
        "$.entry.spell[0].0": "火球术",
        "$.entry.spell[0].1": "PHB",
        "$.entry.spell[0].2": "火球术",
    }


def test_align_manual_translations_handles_reordered_tags():
    parent = Job(
        "$.entry", "Meet {@creature Szass Tam|AUD|Szass Tam's} in {@area chapter 7|300|x}.",
        None, key_path="/entry",
    )
    creature = Job(
        "$.entry.creature[0].2", "Szass Tam's", None,
        tag="creature", key_path="/entry/creature[0]",
    )
    area = Job(
        "$.entry.area[1].0", "chapter 7", None,
        tag="area", key_path="/entry/area[1]",
    )

    result = align_manual_translations(
        {"entry": "{!@ $.entry}"},
        {"entry": "在{@area 第七章|300|x}会见{@creature 萨扎斯·坦|AUD|萨扎斯·坦的}。"},
        [parent, creature, area],
    )

    assert result.errors == []
    assert result.translations[creature.uid] == "萨扎斯·坦的"
    assert result.translations[area.uid] == "第七章"


def test_align_manual_translations_matches_reordered_repeated_tags_by_known_cn():
    parent = Job(
        "$.entry", "Use {@skill Acrobatics} or {@skill Athletics}.", None,
        key_path="/entry",
    )
    acrobatics = Job(
        "$.entry.skill[0].0", "Acrobatics", "特技",
        tag="skill", key_path="/entry/skill[0]",
    )
    athletics = Job(
        "$.entry.skill[1].0", "Athletics", "运动",
        tag="skill", key_path="/entry/skill[1]",
    )

    result = align_manual_translations(
        {"entry": "{!@ $.entry}"},
        {"entry": "使用{@skill 运动}或{@skill 特技}。"},
        [parent, acrobatics, athletics],
    )

    assert result.errors == []
    assert result.translations[acrobatics.uid] == "特技"
    assert result.translations[athletics.uid] == "运动"


def test_align_manual_translations_keeps_first_ambiguous_repeated_tag_child():
    parent = Job(
        "$.entry",
        "Read {@book Shadowfell|XDMG|5|Shadowfell}.",
        None,
        key_path="/entry",
    )
    child = Job(
        "$.entry.book[0].0",
        "Shadowfell",
        None,
        tag="book",
        key_path="/entry/book[0]",
    )

    result = align_manual_translations(
        {"entry": "{!@ $.entry}"},
        {"entry": "阅读{@book 城主指南|XDMG|5|堕影冥界}。"},
        [parent, child],
    )

    assert result.errors == []
    assert result.translations[parent.uid] == "阅读{@book 城主指南|XDMG|5|堕影冥界}。"
    assert result.translations[child.uid] == "城主指南"


def test_sync_from_cn_persists_jobs_inside_tags(tmp_path, monkeypatch):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "adventure.json"
    cn_path = tmp_path / "adventure-cn.json"
    en_path.write_text(json.dumps({
        "adventure": [{
            "name": "Test Adventure",
            "source": "TST",
            "entries": [
                "Read {@adventure Chapter 1: Red Wizards.|TST|1} and visit {@area Mission Rewards|abc|x}."
            ],
        }],
    }), encoding="utf-8")
    cn_path.write_text(json.dumps({
        "adventure": [{
            "ENG_name": "Test Adventure",
            "name": "测试冒险",
            "source": "TST",
            "entries": [
                "阅读{@adventure 第一章：红袍法师。|TST|1}并前往{@area 任务奖励|abc|x}。"
            ],
            "translator": "机翻",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
    )

    translations = {(row["en"], row["category"]): row["cn"] for row in db.store_records}
    assert translations[("Chapter 1: Red Wizards.", "adventure")] == "第一章：红袍法师。"
    assert translations[("Mission Rewards", "area")] == "任务奖励"
    assert summary.verified is True


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


def test_sync_from_cn_adds_missing_and_reproduces_manual_json(
    tmp_path, monkeypatch, capsys
):
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
    output = capsys.readouterr().out
    for label in (
        "[1/5] 检查已有译文",
        "[2/5] 新增译文",
        "[3/5] 修改译文",
        "[4/5] 绑定翻译位置",
        "[5/5] 提交事务",
    ):
        assert label in output


def test_sync_from_cn_derives_cn_path_from_data_bak(tmp_path, monkeypatch, capsys):
    _reset_memory_db(monkeypatch)
    en_dir = tmp_path / "data-bak"
    cn_dir = tmp_path / "data"
    en_dir.mkdir()
    cn_dir.mkdir()
    en_path, authored_cn_path = _write_pair(en_dir)
    inferred_cn_path = cn_dir / en_path.name
    inferred_cn_path.write_text(
        authored_cn_path.read_text(encoding="utf-8"), encoding="utf-8",
    )

    summary = sync_from_cn(
        str(en_path), mode="5et", source_root=str(en_dir),
    )

    assert summary.added == 2
    assert summary.verified is True
    assert f"自动使用中文文件: {inferred_cn_path}" in capsys.readouterr().out


def test_sync_from_cn_reloads_exact_translation_for_tag_only_string(
    tmp_path, monkeypatch
):
    _reset_memory_db(monkeypatch)
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    en_path.write_text(json.dumps({
        "item": [{
            "name": "Crafting Materials",
            "source": "TST",
            "entries": ["{@item Acid|XPHB}, {@item Oil|XPHB}"],
        }],
    }), encoding="utf-8")
    cn_path.write_text(json.dumps({
        "item": [{
            "ENG_name": "Crafting Materials",
            "name": "制作材料",
            "source": "TST",
            "entries": ["{@item 强酸|XPHB}, {@item 燃油|XPHB}"],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
    )

    assert summary.verified is True


def test_sync_from_cn_treats_tag_only_fallback_as_missing(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    db.put(
        "Cartographer's Tools", "制图工具", "old.json",
        proofread=True, tag="item",
    )
    en_path = tmp_path / "backgrounds.json"
    cn_path = tmp_path / "backgrounds-cn.json"
    en_path.write_text(json.dumps({
        "background": [{
            "name": "Archaeologist",
            "source": "EFA",
            "entries": ["{@item Cartographer's Tools|XPHB}"],
        }],
    }), encoding="utf-8")
    cn_path.write_text(json.dumps({
        "background": [{
            "ENG_name": "Archaeologist",
            "name": "考古学家",
            "source": "EFA",
            "entries": ["{@item 制图工具|XPHB}"],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    def unexpected_prompt(*_args):
        raise AssertionError("仅标签变化的兜底命中不应进入冲突确认")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=unexpected_prompt,
    )

    exact = [
        row for row in db.store_records
        if row["en"] == "{@item Cartographer's Tools|XPHB}"
    ]
    assert len(exact) == 1
    assert exact[0]["cn"] == "{@item 制图工具|XPHB}"
    assert exact[0]["proofread"] == 0
    assert summary.conflicts == 0
    assert summary.verified is True


def test_resolve_cn_path_reports_missing_inferred_file(tmp_path):
    en_dir = tmp_path / "data-bak"
    en_dir.mkdir()
    en_path = en_dir / "missing.json"
    en_path.write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="data/missing.json"):
        resolve_cn_path(str(en_path))


def test_resolve_cn_path_requires_data_bak_component(tmp_path):
    en_path = tmp_path / "sample.json"
    en_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="不包含 data-bak"):
        resolve_cn_path(str(en_path))


def test_sync_from_cn_path_recursively_processes_matching_json_files(
    tmp_path, monkeypatch, capsys
):
    _reset_memory_db(monkeypatch)
    en_root = tmp_path / "data-bak"
    cn_root = tmp_path / "data"
    (en_root / "nested").mkdir(parents=True)
    (cn_root / "nested").mkdir(parents=True)

    def write_pair(relative_path, name, cn_name, entry, cn_entry):
        en_file = en_root / relative_path
        cn_file = cn_root / relative_path
        en_file.write_text(json.dumps({
            "spell": [{"name": name, "source": "TST", "entries": [entry]}],
        }), encoding="utf-8")
        cn_file.write_text(json.dumps({
            "spell": [{
                "ENG_name": name, "name": cn_name,
                "source": "TST", "entries": [cn_entry],
            }],
        }, ensure_ascii=False), encoding="utf-8")

    write_pair("first.json", "First", "第一个", "First entry.", "第一条。")
    write_pair(
        "nested/second.json", "Second", "第二个",
        "Second entry.", "第二条。",
    )
    (en_root / "missing.json").write_text("{}", encoding="utf-8")

    summary = sync_from_cn_path(
        str(en_root), mode="5et", source_root=str(en_root),
    )

    assert summary.processed_files == 2
    assert summary.skipped_files == 1
    assert summary.added == 4
    assert summary.verified is True
    output = capsys.readouterr().out
    assert "处理文件 [1/2]: first.json" in output
    assert "处理文件 [2/2]: nested/second.json" in output
    assert "跳过缺少中文对应文件的 JSON: missing.json" in output


def test_sync_from_cn_path_continues_after_one_file_fails(
    tmp_path, monkeypatch, capsys
):
    module = importlib.import_module("app.cli.sync_from_cn")
    en_root = tmp_path / "data-bak"
    cn_root = tmp_path / "data"
    en_root.mkdir()
    cn_root.mkdir()
    for filename in ("first.json", "second.json"):
        (en_root / filename).write_text("{}", encoding="utf-8")
        (cn_root / filename).write_text("{}", encoding="utf-8")

    calls = []

    def fake_sync(en_path, *_args, **_kwargs):
        filename = Path(en_path).name
        calls.append(filename)
        if filename == "first.json":
            raise RuntimeError("重新执行翻译未生成相同结果")
        return SyncSummary(verified=True, processed_files=1, matched=2)

    monkeypatch.setattr(module, "sync_from_cn", fake_sync)

    summary = module.sync_from_cn_path(str(en_root))
    module.print_sync_summary(summary)

    assert calls == ["first.json", "second.json"]
    assert summary.processed_files == 1
    assert summary.failed_files == [
        ("first.json", "重新执行翻译未生成相同结果")
    ]
    assert summary.verified is False
    captured = capsys.readouterr()
    assert "处理失败，继续下一个文件: first.json" in captured.err
    assert "文件汇总：成功 1，失败 1，跳过 0。" in captured.out
    assert "验证未全部通过" in captured.out


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
    if decision == "add":
        assert bean["proofread"] == 0
    else:
        assert bean["proofread"] == 1
    assert (summary.added, summary.updated, summary.kept_database) == (added, updated, kept)
    assert summary.verified is True


@pytest.mark.parametrize(
    ("is_proofread", "expected_status"),
    [(True, "已校对"), (False, "未校对")],
)
def test_prompt_conflict_shows_database_proofread_status(
    monkeypatch, capsys, is_proofread, expected_status
):
    job = Job(
        "$.spell.name", "Magic Bolt", "旧译名", rel_path="sample.json",
        tag="spell", is_proofread=is_proofread,
    )
    conflict = SyncConflict(job, "旧译名", "魔法飞弹")
    monkeypatch.setattr("builtins.input", lambda _prompt: "k")

    assert _prompt_conflict(conflict, 1, 1) == "keep"

    output = capsys.readouterr().out
    assert f"库中校对状态: {expected_status}" in output


def test_sync_from_cn_does_not_repeat_modify_confirmation_for_same_word_id(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    en_path.write_text(json.dumps({
        "spell": [
            {"name": "First", "source": "TST", "entries": ["Shared text."]},
            {"name": "Second", "source": "TST", "entries": ["Shared text."]},
        ],
    }), encoding="utf-8")
    cn_path.write_text(json.dumps({
        "spell": [
            {"ENG_name": "First", "name": "第一个", "source": "TST",
             "entries": ["共享译文。"]},
            {"ENG_name": "Second", "name": "第二个", "source": "TST",
             "entries": ["共享译文。"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    word_id = db.put(
        "Shared text.", "旧译文。", "old.json", proofread=True, tag="spell",
    )
    prompted = []

    def decide(conflict, *_args):
        prompted.append((conflict.job.sql_id, conflict.database_value))
        return "modify"

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=decide,
    )

    assert prompted == [(word_id, "旧译文。")]
    assert summary.conflicts == 2
    assert summary.updated == 1
    assert db.store_by_id[word_id]["cn"] == "共享译文。"
    assert db.store_by_id[word_id]["proofread"] == 1
    usages = [usage for usage in db.word_usage if usage["word_id"] == word_id]
    assert len([usage for usage in usages if usage["file"] == "sample.json"]) == 2
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


def test_sync_from_cn_automatically_keeps_database_when_only_quotes_differ(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    manual_obj = json.loads(cn_path.read_text(encoding="utf-8"))
    manual_obj["spell"][0]["name"] = "“魔法飞弹”"
    cn_path.write_text(json.dumps(manual_obj, ensure_ascii=False), encoding="utf-8")
    db.put("Magic Bolt", '"魔法飞弹"', "sample.json", proofread=True, tag="spell")

    def unexpected_prompt(*_args):
        raise AssertionError("仅引号样式不同不应进入冲突确认")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=unexpected_prompt,
    )

    assert summary.conflicts == 1
    assert summary.kept_database == 1
    assert summary.added == 1
    assert len([row for row in db.store_records if row["en"] == "Magic Bolt"]) == 1
    assert db.store_records[0]["cn"] == '"魔法飞弹"'
    assert summary.verified is True


def test_sync_from_cn_automatically_modifies_when_only_tag_contents_differ(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    english = "Use {@item Cartographer's Tools|PHB}."
    database_cn = "使用 {@item 制图工具|PHB}。"
    manual_cn = "使用 {@item 制图工具|XPHB}。"
    en_path.write_text(json.dumps({
        "spell": [{"name": "Tools", "source": "TST", "entries": [english]}],
    }), encoding="utf-8")
    cn_path.write_text(json.dumps({
        "spell": [{
            "ENG_name": "Tools", "name": "工具",
            "source": "TST", "entries": [manual_cn],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    word_id = db.put(
        english, database_cn, "old.json", proofread=True, tag="spell",
    )

    def unexpected_prompt(*_args):
        raise AssertionError("仅 {@...} 内部不同不应进入冲突确认")

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=unexpected_prompt,
    )

    assert db.store_by_id[word_id]["cn"] == manual_cn
    assert db.store_by_id[word_id]["proofread"] == 1
    assert summary.conflicts == 1
    assert summary.updated == 1
    assert summary.verified is True


def test_sync_from_cn_preserves_unproofread_status_when_modifying_existing_word(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    word_id = db.put(
        "Magic Bolt", "旧译名", "sample.json", proofread=False, tag="spell",
    )

    summary = sync_from_cn(
        str(en_path), str(cn_path), mode="5et", source_root=str(tmp_path),
        decision_provider=lambda *_: "modify",
    )

    assert db.store_by_id[word_id]["cn"] == "魔法飞弹"
    assert db.store_by_id[word_id]["proofread"] == 0
    assert summary.updated == 1
    assert summary.verified is True


def test_sync_batch_reuses_translation_regardless_of_category(monkeypatch):
    db = _reset_memory_db(monkeypatch)
    db.put("Magic Bolt", "魔法飞弹", "old.json", proofread=True, tag="item")
    job = Job(
        "$.spell.name", "Magic Bolt", None,
        rel_path="sample.json", tag="spell",
    )

    assert db.apply_sync_batch(
        [{
            "en": "Magic Bolt", "cn": "魔法飞弹", "rel_f": "sample.json",
            "proofread": False, "tag": "spell", "job_context": job,
        }],
        [],
    )

    assert len(db.store_records) == 1
    assert db.store_records[0]["category"] == "item"
    assert db.usage_by_file_uid[("sample.json", "$.spell.name")]["word_id"] == (
        db.store_records[0]["id"]
    )


def test_preview_classifies_without_writing_and_omits_matching(tmp_path, monkeypatch):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    db.put("Magic Bolt", "魔法飞弹", "sample.json", proofread=True, tag="spell")
    before_words = [dict(row) for row in db.store_records]
    before_usage = [dict(row) for row in db.word_usage]

    report = preview_sync_from_cn(
        str(en_path), str(cn_path), source_root=str(tmp_path),
    )

    assert report["summary"]["matching"] == 1
    assert report["summary"]["missing"] == 1
    assert "matching" not in report["entries"]
    assert report["entries"]["missing"][0]["en"] == "A bright bolt hits the target."
    assert report["alignment_errors"] == []
    assert db.store_records == before_words
    assert db.word_usage == before_usage
    json.loads(json.dumps(report, ensure_ascii=False))


def test_preview_detects_conflict_collision(tmp_path, monkeypatch):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    en_path.write_text(json.dumps({"spell": [
        {"name": "One", "source": "TST", "entries": ["Shared."]},
        {"name": "Two", "source": "TST", "entries": ["Shared."]},
    ]}), encoding="utf-8")
    cn_path.write_text(json.dumps({"spell": [
        {"ENG_name": "One", "name": "一", "source": "TST", "entries": ["甲。"]},
        {"ENG_name": "Two", "name": "二", "source": "TST", "entries": ["乙。"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    word_id = db.put("Shared.", "旧。", "old.json", proofread=True, tag="spell")

    report = preview_sync_from_cn(
        str(en_path), str(cn_path), source_root=str(tmp_path),
    )

    collision = next(item for item in report["collisions"] if item["word_id"] == word_id)
    assert collision["kind"] == "conflict-vs-conflict"
    assert collision["distinct_file_values"] == ["乙。", "甲。"]


@pytest.mark.parametrize("bad_rules", [
    {"default": {"action": "drop", "reason": "x"}},
    {"default": {"action": "keep"}},
    {"rules": [{"match": {"unknown": 1}, "action": "keep", "reason": "x"}]},
])
def test_decision_rule_schema_rejects_invalid_documents(bad_rules):
    with pytest.raises(ValueError):
        build_decision_provider_from_rules(bad_rules)


def test_preview_decisions_fail_closed_and_use_collision_fallback(tmp_path, monkeypatch):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    en_path.write_text(json.dumps({"spell": [
        {"name": "One", "source": "TST", "entries": ["Shared."]},
        {"name": "Two", "source": "TST", "entries": ["Shared."]},
    ]}), encoding="utf-8")
    cn_path.write_text(json.dumps({"spell": [
        {"ENG_name": "One", "name": "一", "source": "TST", "entries": ["甲。"]},
        {"ENG_name": "Two", "name": "二", "source": "TST", "entries": ["乙。"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    db.put("Shared.", "旧。", "old.json", proofread=True, tag="spell")
    report = preview_sync_from_cn(str(en_path), str(cn_path), source_root=str(tmp_path))

    uncovered = build_decision_provider_from_rules({
        "rules": [{"match": {"en": "other"}, "action": "keep", "reason": "x"}],
    })
    with pytest.raises(ValueError, match="未覆盖冲突"):
        validate_preview_decisions(report, uncovered)

    provider = build_decision_provider_from_rules({
        "default": {"action": "modify", "fallback": "add", "reason": "采用文件译文"},
    })
    log = validate_preview_decisions(report, provider)
    assert [entry["action"] for entry in log] == ["modify", "add"]
    assert [entry["fallback_used"] for entry in log] == [False, True]


def test_rules_provider_applies_collision_fallback_and_logs_actual_actions(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "sample.json"
    cn_path = tmp_path / "sample-cn.json"
    en_path.write_text(json.dumps({"spell": [
        {"name": "One", "source": "TST", "entries": ["Shared."]},
        {"name": "Two", "source": "TST", "entries": ["Shared."]},
    ]}), encoding="utf-8")
    cn_path.write_text(json.dumps({"spell": [
        {"ENG_name": "One", "name": "一", "source": "TST", "entries": ["甲。"]},
        {"ENG_name": "Two", "name": "二", "source": "TST", "entries": ["乙。"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    db.put("Shared.", "旧。", "old.json", proofread=True, tag="spell")
    provider = build_decision_provider_from_rules({
        "default": {"action": "modify", "fallback": "add", "reason": "采用文件译文"},
    })
    decision_log = []

    summary = sync_from_cn(
        str(en_path), str(cn_path), source_root=str(tmp_path),
        decision_provider=provider, decision_log=decision_log,
    )

    assert summary.verified is True
    assert summary.updated == 1
    assert [entry["action"] for entry in decision_log] == ["modify", "add"]
    assert decision_log[1]["fallback_used"] is True
    assert {row["cn"] for row in db.store_records if row["en"] == "Shared."} == {"甲。", "乙。"}


def test_sync_rebinds_existing_exact_en_cn_without_prompt(tmp_path, monkeypatch):
    db = _reset_memory_db(monkeypatch)
    en_path, cn_path = _write_pair(tmp_path)
    old_id = db.put(
        "Magic Bolt", "旧译名", "old.json", proofread=True, tag="spell",
    )
    desired_id = db.put(
        "Magic Bolt", "魔法飞弹", "other.json", proofread=False, tag="item",
    )
    words_before = [dict(row) for row in db.store_records]

    report = preview_sync_from_cn(
        str(en_path), str(cn_path), source_root=str(tmp_path),
    )
    assert report["summary"]["auto_rebind"] == 1
    assert report["summary"]["conflict"] == 0
    assert report["entries"]["auto_rebind"][0]["reuse_sql_id"] == desired_id

    def unexpected_prompt(*_args):
        raise AssertionError("已有相同 en/cn 时不应询问用户")

    summary = sync_from_cn(
        str(en_path), str(cn_path), source_root=str(tmp_path),
        decision_provider=unexpected_prompt,
    )

    assert summary.verified is True
    assert len(db.store_records) == len(words_before) + 1  # 仅另一个缺失句子新增 words
    assert db.store_by_id[old_id]["cn"] == "旧译名"
    assert any(
        usage["file"] == "sample.json" and usage["word_id"] == desired_id
        for usage in db.word_usage
    )


def test_sync_decks_rebinds_names_and_outcome_keys_then_reproduces_file(
    tmp_path, monkeypatch
):
    db = _reset_memory_db(monkeypatch)
    en_path = tmp_path / "decks.json"
    cn_path = tmp_path / "decks-cn.json"
    en_path.write_text(json.dumps({
        "deck": [{
            "name": "Tarokka Deck", "source": "CoS",
            "spreads": [{
                "name": "Reading",
                "outcomes": {"adventure": {"CoS": {
                    "warrior|tarokka deck|cos": "7fe",
                }}},
            }],
        }],
        "card": [{"name": "Skull", "source": "BMT"}],
    }), encoding="utf-8")
    cn_path.write_text(json.dumps({
        "deck": [{
            "ENG_name": "Tarokka Deck", "name": "塔罗卡牌组", "source": "CoS",
            "spreads": [{
                "ENG_name": "Reading", "name": "解读",
                "outcomes": {"adventure": {"CoS": {
                    "武者|塔罗卡牌组|cos": "7fe",
                }}},
            }],
        }],
        "card": [{"ENG_name": "Skull", "name": "头骨", "source": "BMT"}],
    }, ensure_ascii=False), encoding="utf-8")
    db.put("Tarokka Deck", "塔罗卡牌组", "old.json", proofread=True, tag="deck")
    db.put("Reading", "解读", "old.json", proofread=True, tag="deck")
    db.put("Skull", "骷髅", "old.json", proofread=True, tag="deck")
    skull_id = db.put("Skull", "头骨", "other.json", proofread=True, tag="card")
    db.put("warrior", "战士", "old.json", proofread=True, tag="card")
    warrior_id = db.put("warrior", "武者", "other.json", proofread=True, tag="card")
    db.put("tarokka deck", "塔罗卡牌组", "old.json", proofread=True, tag="card")

    def unexpected_prompt(*_args):
        raise AssertionError("已有相同 en/cn 的名称和字典键不应询问用户")

    summary = sync_from_cn(
        str(en_path), str(cn_path), source_root=str(tmp_path),
        decision_provider=unexpected_prompt,
    )

    assert summary.verified is True
    assert any(u["file"] == "decks.json" and u["word_id"] == skull_id for u in db.word_usage)
    assert any(u["file"] == "decks.json" and u["word_id"] == warrior_id for u in db.word_usage)
