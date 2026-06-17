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
