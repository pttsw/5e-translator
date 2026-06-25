from app.core.database.memory_db_dictionary import MemoryDBDictionary
from app.core.translator.analyser.base_analyser import BaseAnalyser


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


def test_list_item_uid_prefers_name_over_index():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    analyser = BaseAnalyser(db, "bestiary/bestiary-test.json")

    _, jobs = analyser.process({
        "monster": [
            {"name": "Goblin Boss", "entries": ["Hit."]},
            {"name": "Orc Boss", "entries": ["Hit."]},
        ]
    })

    entry_jobs = [job for job in jobs if job.en_str == "Hit."]

    assert len(entry_jobs) == 2
    assert "$.monster[name=Goblin-Boss].entries[0].0" in {job.uid for job in entry_jobs}
    assert "$.monster[name=Orc-Boss].entries[0].0" in {job.uid for job in entry_jobs}


def test_list_item_uid_uses_source_to_disambiguate_duplicate_names():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    analyser = BaseAnalyser(db, "actions.json")

    _, jobs = analyser.process({
        "action": [
            {"name": "Escape a Grapple", "source": "PHB", "entries": ["Old text."]},
            {"name": "Escape a Grapple", "source": "XPHB", "entries": ["New text."]},
        ]
    })

    entry_uids = {job.en_str: job.uid for job in jobs if job.en_str in ("Old text.", "New text.")}

    assert entry_uids["Old text."] == "$.action[name=Escape-a-Grapple;source=PHB].entries[0].0"
    assert entry_uids["New text."] == "$.action[name=Escape-a-Grapple;source=XPHB].entries[0].0"


def test_list_item_uid_adds_index_for_duplicate_stable_keys():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    analyser = BaseAnalyser(db, "bastions.json")

    _, jobs = analyser.process({
        "facility": [
            {
                "name": "Workshop",
                "source": "XDMG",
                "entries": [
                    {
                        "name": "Craft Options",
                        "entries": [
                            {
                                "type": "list",
                                "items": [
                                    {"name": "Craft:", "entry": "First option."},
                                    {"name": "Craft:", "entry": "Second option."},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    })

    entry_uids = {job.en_str: job.uid for job in jobs if job.en_str in ("First option.", "Second option.")}

    assert entry_uids["First option."] == "$.facility[name=Workshop;source=XDMG].entries[name=Craft-Options].entries[0].items[name=Craft:;index=0].entry.0"
    assert entry_uids["Second option."] == "$.facility[name=Workshop;source=XDMG].entries[name=Craft-Options].entries[0].items[name=Craft:;index=1].entry.0"


def test_card_tag_source_segment_is_not_translated_as_job():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    analyser = BaseAnalyser(db, "adventure/adventure-toa.json")

    _, jobs = analyser.process({
        "entries": [
            "{@card Obo'laka|Omu's Trickster Gods|ToA|Obo'laka's card}"
        ]
    })

    job_texts = {job.en_str for job in jobs}
    assert "ToA" not in job_texts
    assert {
        "Obo'laka",
        "Omu's Trickster Gods",
        "Obo'laka's card",
    }.issubset(job_texts)


def test_class_tag_source_and_level_segments_are_not_translated_as_jobs():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    analyser = BaseAnalyser(db, "deities.json")

    _, jobs = analyser.process({
        "entries": [
            "{@class fighter|phb|Battle Master|Battle Master|phb|2-0}"
        ]
    })

    job_texts = {job.en_str for job in jobs}
    assert "phb" not in job_texts
    assert "2-0" not in job_texts
    assert {"fighter", "Battle Master"}.issubset(job_texts)


def test_get_job_uses_stable_uid_and_key_path_for_duplicate_text():
    db = MemoryDBDictionary()
    reset_memory_db(db)
    analyser = BaseAnalyser(db, "items.json")

    analyser.set_job("$.item[name=First].entries[0].0", "Hit.", None, tag="entries", key_path="/item[name=First]/entries[0]")
    analyser.set_job("$.item[name=Second].entries[0].0", "Hit.", None, tag="entries", key_path="/item[name=Second]/entries[0]")

    by_uid = analyser.get_job(
        "Hit.",
        tag="entries",
        uid="$.item[name=Second].entries[0].0",
    )
    by_key_path = analyser.get_job(
        "Hit.",
        tag="entries",
        key_path="/item[name=First]/entries[0]",
    )

    assert by_uid.uid == "$.item[name=Second].entries[0].0"
    assert by_key_path.uid == "$.item[name=First].entries[0].0"


def test_memory_db_usage_can_disambiguate_same_en_and_tag():
    db = MemoryDBDictionary()
    reset_memory_db(db)

    class Context:
        def __init__(self, uid, key_path, context_key):
            self.uid = uid
            self.key_path = key_path
            self.context_key = context_key
            self.context_label = context_key

    first = Context("$.monster[name=Goblin].entries[0].0", "/monster[name=Goblin]/entries[0]", "/monster[name=Goblin]/entries[0]")
    second = Context("$.monster[name=Orc].entries[0].0", "/monster[name=Orc]/entries[0]", "/monster[name=Orc]/entries[0]")

    first_record = db._insert_record("Hit.", "命中。", "", tag="entries", sql_id=1)
    second_record = db._insert_record("Hit.", "击中。", "", tag="entries", sql_id=2)
    db._put_word_usage(first_record["id"], "bestiary/test.json", first)
    db._put_word_usage(second_record["id"], "bestiary/test.json", second)

    result = db.get_bunch(
        ["Hit.", "Hit."],
        ["entries", "entries"],
        "bestiary/test.json",
        job_contexts=[first, second],
    )

    assert result[("Hit.", "entries", first.uid)]["cn"] == "命中。"
    assert result[("Hit.", "entries", second.uid)]["cn"] == "击中。"


def test_memory_db_uid_usage_beats_inferred_category_for_same_en():
    db = MemoryDBDictionary()
    reset_memory_db(db)

    class Context:
        uid = "$.data[id=05d;page=143].entries[id=086;page=146].entries[2].rows[9].row[4].0"
        key_path = "/data[id=05d;page=143]/entries[id=086;page=146]/entries[2]/rows[9]/row[4]"
        context_key = "/data[id=05d;page=143]/entries[id=086;page=146]/entries[2]/rows[9]"
        context_label = "Equipment"

    spell_record = db._insert_record("Light", "光亮术", "", proofread=True, tag="spell", sql_id=979)
    item_property_record = db._insert_record("Light", "轻型", "", proofread=True, tag="itemProperty", sql_id=1856)
    db._put_word_usage(item_property_record["id"], "book/book-phb.json", Context())
    db.dump(["book/book-phb.json"])

    result = db.get_bunch(
        ["Light"],
        [""],
        "book/book-phb.json",
        ignore_case=True,
        job_contexts=[Context()],
    )

    assert result[("Light", "", Context.uid)]["sql_id"] == 1856
    assert result[("Light", "", Context.uid)]["cn"] == "轻型"
    assert db.usage_by_file_uid[("book/book-phb.json", Context.uid)]["word_id"] == 1856
    assert spell_record["id"] == 979


def test_memory_db_backfill_does_not_overwrite_existing_uid_word_id():
    db = MemoryDBDictionary()
    reset_memory_db(db)

    class Context:
        uid = "$.language[name=Quori;source=ERLW;page=6].typicalSpeakers[2].filter[0].0"
        key_path = "/language[name=Quori;source=ERLW;page=6]/typicalSpeakers[2]/filter[0]"
        context_key = "/language[name=Quori;source=ERLW;page=6]/typicalSpeakers[2]"
        context_label = "Quori"

    creature_record = db._insert_record("Quori", "梦灵", "", tag=None, sql_id=10955)
    language_record = db._insert_record("Quori", "梦灵语", "", tag="language", sql_id=486671)

    db._put_word_usage(creature_record["id"], "languages.json", Context())
    db._put_word_usage(language_record["id"], "languages.json", Context())

    assert db.usage_by_file_uid[("languages.json", Context.uid)]["word_id"] == 10955


def test_memory_db_uid_usage_match_ignores_case():
    db = MemoryDBDictionary()
    reset_memory_db(db)

    class Context:
        uid = "$.language[name=Quori;source=ERLW;page=6].typicalSpeakers[2].filter[0].0"
        key_path = "/language[name=Quori;source=ERLW;page=6]/typicalSpeakers[2]/filter[0]"
        context_key = "/language[name=Quori;source=ERLW;page=6]/typicalSpeakers[2]"
        context_label = "Quori"

    creature_record = db._insert_record("Quori", "梦灵", "", tag=None, sql_id=10955)
    db._insert_record("Quori", "梦灵语", "", tag="language", sql_id=486671)
    db._put_word_usage(creature_record["id"], "languages.json", Context())

    result = db.get_bunch(
        ["quori"],
        ["bestiary"],
        "languages.json",
        ignore_case=True,
        job_contexts=[Context()],
    )

    assert result[("quori", "bestiary", Context.uid)]["sql_id"] == 10955
    assert result[("quori", "bestiary", Context.uid)]["cn"] == "梦灵"


def test_memory_db_backfills_stable_usage_when_cache_hits_legacy_file_dump():
    db = MemoryDBDictionary()
    reset_memory_db(db)

    class Context:
        uid = "$.action[name=Dash].name"
        key_path = "/action[name=Dash]/name"
        context_key = "/action[name=Dash]"
        context_label = "Dash"

    record = db._insert_record("Dash", "疾走", "", tag="action", sql_id=11)
    db._put_word_usage(record["id"], "actions.json")
    db.dump(["actions.json"])

    result = db.get_bunch(
        ["Dash"],
        ["action"],
        "actions.json",
        job_contexts=[Context()],
    )

    assert result[("Dash", "action", Context.uid)]["cn"] == "疾走"
    assert db.usage_by_file_uid[("actions.json", Context.uid)]["word_id"] == 11


def test_split_file_word_usage_uses_origin_file_from_meta():
    db = MemoryDBDictionary()
    reset_memory_db(db)

    record = db._insert_record("Light", "轻型", "", proofread=True, tag="itemProperty", sql_id=1856)
    analyser = BaseAnalyser(db, "phb/book/light.json")

    _, jobs = analyser.process({
        "_meta": {"origin_file": "book/book-phb.json"},
        "item": {
            "name": "Lamp",
            "source": "PHB",
            "entries": ["Light"],
        },
    })

    light_job = next(job for job in jobs if job.en_str == "Light")
    assert light_job.cn_str == "轻型"
    assert light_job.sql_id == record["id"]
    assert light_job.uid == "$.item[name=Lamp;source=PHB].entries[0].0"
    assert light_job.key_path == "/item[name=Lamp;source=PHB]/entries[0]"
    assert ("book/book-phb.json", light_job.uid) in db.usage_by_file_uid
    assert ("phb/book/light.json", light_job.uid) not in db.usage_by_file_uid
