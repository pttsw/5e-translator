import threading

from app.core.database.db_dictionary import DBDictionary
from app.core.utils import Job


class FakeDB:
    def __init__(self):
        self.queries = []

    def execute_query(self, sql, params):
        self.queries.append((sql, params))
        if "LOWER(en)" not in sql:
            return []
        return [
            {
                "id": 42,
                "en": "misty step",
                "cn": "迷踪步",
                "json_file": "",
                "proofread": 1,
                "category": "spell",
                "modified_at": 0,
                "is_key": False,
            }
        ]

    def close(self):
        return


def make_dictionary(fake_db):
    dictionary = object.__new__(DBDictionary)
    dictionary.source = ""
    dictionary.version = "2.0.0"
    dictionary.lock = threading.Lock()
    dictionary.db_list = [fake_db]
    dictionary.available_list = [True]
    dictionary.ok = True
    dictionary.dictionary = {}
    dictionary.lower_dictionary = {}
    dictionary.proofread_set = set()
    return dictionary


def test_get_bunch_uses_case_insensitive_sql_fallback():
    fake_db = FakeDB()
    dictionary = make_dictionary(fake_db)

    result = dictionary.get_bunch(
        ["Misty Step"],
        ["spell"],
        "",
        ignore_case=True,
    )

    assert result[("Misty Step", "spell")]["cn"] == "迷踪步"
    assert result[("Misty Step", "spell")]["sql_id"] == 42
    assert len(fake_db.queries) == 2
    assert fake_db.queries[1][1] == ("misty step",)


class FakeBatchCursor:
    def __init__(self, existing_words=None):
        self.inserted_words = []
        self.updated_words = []
        self.usages = []
        self._rows = []
        self.existing_words = list(existing_words or [])

    def execute(self, sql, params):
        if not sql.startswith("SELECT id, en, cn, category FROM words"):
            raise AssertionError(sql)
        requested = set(zip(params[0::2], params[1::2]))
        inserted_rows = [
            {"id": index + 100, "en": row[0], "cn": row[1], "category": row[6]}
            for index, row in enumerate(self.inserted_words)
            if (row[0], row[1]) in requested
        ]
        self._rows = [
            row for row in self.existing_words
            if (row["en"], row["cn"]) in requested
        ] + inserted_rows

    def fetchall(self):
        return self._rows

    def executemany(self, sql, params):
        if sql.startswith("INSERT INTO words"):
            self.inserted_words.extend(params)
        elif sql.startswith("UPDATE words"):
            self.updated_words.extend(params)
        elif sql.startswith("INSERT INTO word_usage"):
            self.usages.extend(params)
        else:
            raise AssertionError(sql)


class FakeBatchDB:
    def __init__(self, existing_words=None):
        self.cursor = FakeBatchCursor(existing_words)
        self.transaction_calls = 0

    def execute_transaction(self, callback):
        self.transaction_calls += 1
        return callback(self.cursor)

    def close(self):
        return


def test_apply_sync_batch_uses_one_transaction_and_deduplicates_words():
    fake_db = FakeBatchDB()
    dictionary = make_dictionary(fake_db)
    dictionary.source = "sync-cn"
    progress = []
    first = Job("uid-1", "Magic Bolt", None, rel_path="sample.json", tag="spell")
    first.context_label = "__SYNC_CN_EXACT__|name"
    second = Job("uid-2", "Magic Bolt", None, rel_path="sample.json", tag="spell")
    second.context_label = "__SYNC_CN_EXACT__|alias"
    changed = Job("uid-3", "Old", None, rel_path="sample.json", tag="spell")

    assert dictionary.apply_sync_batch(
        [
            {"en": "Magic Bolt", "cn": "魔法飞弹", "rel_f": "sample.json",
             "proofread": False, "tag": "spell", "job_context": first},
            {"en": "Magic Bolt", "cn": "魔法飞弹", "rel_f": "sample.json",
             "proofread": False, "tag": "spell", "job_context": second},
        ],
        [
            {"sql_id": 42, "en": "Old", "cn": "新", "rel_f": "sample.json",
             "proofread": False, "tag": "spell", "job_context": changed},
        ],
        progress_callback=lambda stage, current, total: progress.append(
            (stage, current, total)
        ),
    )

    assert fake_db.transaction_calls == 1
    assert len(fake_db.cursor.inserted_words) == 1
    assert fake_db.cursor.inserted_words[0][5] == 0
    assert len(fake_db.cursor.updated_words) == 1
    assert fake_db.cursor.updated_words[0][3] == 0
    assert {row[2] for row in fake_db.cursor.usages} == {"uid-1", "uid-2", "uid-3"}
    assert {row[0] for row in fake_db.cursor.usages} == {42, 100}
    assert [event[0] for event in progress] == [
        "lookup", "insert", "update", "usage", "commit",
    ]
    assert progress[-1] == ("commit", 1, 1)


def test_apply_sync_batch_reuses_same_text_with_different_category():
    fake_db = FakeBatchDB(existing_words=[
        {"id": 77, "en": "Magic Bolt", "cn": "魔法飞弹", "category": "item"},
    ])
    dictionary = make_dictionary(fake_db)
    job = Job("uid-1", "Magic Bolt", None, rel_path="sample.json", tag="spell")

    assert dictionary.apply_sync_batch(
        [{"en": "Magic Bolt", "cn": "魔法飞弹", "rel_f": "sample.json",
          "proofread": False, "tag": "spell", "job_context": job}],
        [],
    )

    assert fake_db.cursor.inserted_words == []
    assert len(fake_db.cursor.usages) == 1
    assert fake_db.cursor.usages[0][0] == 77
