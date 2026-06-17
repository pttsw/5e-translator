from app.core.database.memory_db_dictionary import MemoryDBDictionary


def reset_memory_db(db):
    db.dictionary = {}
    db.lower_dictionary = {}
    db.proofread_set = set()
    db.store_records = []
    db.store_by_id = {}
    db.source_index = {}
    db.word_usage = []
    db.usage_by_file_uid = {}
    db.file_table = {}
    db.locked_entries = {}
    db.credits_table = {}
    db.next_sql_id = 1


def test_memory_db_finds_unique_tag_only_update_match():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    old_en = (
        "When you take the Magic action, you cast a spell that requires "
        "a Magic action."
    )
    new_en = (
        "When you take the {@action Magic|XPHB} action, you cast a spell "
        "that requires a {@action Magic|XPHB} action."
    )
    db._insert_record(
        old_en,
        "当你执行Magic动作时，你施放一个需要Magic动作的法术。",
        "actions.json",
        proofread=True,
        tag="entries",
        sql_id=9,
    )
    db.dump(["actions.json"])

    match = db.get_tag_only_update_match(new_en, "entries")

    assert match["sql_id"] == 9
    assert match["proofread"] == 1
    assert match["old_en"] == old_en


def test_memory_db_does_not_guess_when_tagless_match_is_ambiguous():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    old_en = "Magic action."
    new_en = "{@action Magic|XPHB} action."
    db._insert_record(old_en, "A", "actions.json", tag="entries", sql_id=1)
    db._insert_record(old_en, "B", "actions.json", tag="name", sql_id=2)
    db.dump(["actions.json"])

    assert db.get_tag_only_update_match(new_en, "entries") is None


def test_memory_db_matches_idrotf_filter_and_adventure_metadata_changes():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    old_en = (
        "Auril's decision to live among mortals is explained in "
        "{@adventure appendix C|IDRotF|21}."
    )
    new_en = (
        "{@filter Auril's|bestiary|source=IDRotF|search=Auril} decision to live "
        "among mortals is explained in {@adventure appendix C|IDRotF|21}."
    )
    db._insert_record(
        old_en,
        "奥瑞尔生活在凡人之中的决定在附录C中解释。",
        "adventure/adventure-idrotf.json",
        proofread=True,
        tag="entries",
        sql_id=21,
    )
    db.dump(["adventure/adventure-idrotf.json"])

    match = db.get_tag_only_update_match(new_en, "entries")

    assert match["sql_id"] == 21
    assert match["old_en"] == old_en


def test_memory_db_cache_requires_exact_tag_when_tag_is_requested():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    db._insert_record("Light", "光亮术", "", proofread=True, tag="spell", sql_id=979)
    db._insert_record("Light", "轻型", "", proofread=True, tag="itemProperty", sql_id=1856)

    assert db.get("Light", tag="spell")["sql_id"] == 979
    assert db.get("Light", tag="itemProperty")["sql_id"] == 1856
    assert db.get("Light", tag="class") is None
