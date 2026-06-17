import threading

from app.core.database.db_dictionary import DBDictionary


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
