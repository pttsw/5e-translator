from app.core.database.dictionary import DatabaseAdapter


class FakeRedis:
    def __init__(self):
        self.puts = []

    def get(self, _key, tag=""):
        return []

    def put(self, key, value, tag=""):
        self.puts.append((key, value, tag))
        return True


class FakeDBDictionary:
    def get(self, key, rel_f="", load_from_sql=False, tag=""):
        assert key == "Light"
        assert tag == "spell"
        return {
            "cn": "光亮术",
            "category": "spell",
        }


def test_database_adapter_writes_db_hit_back_to_redis_with_actual_category():
    adapter = DatabaseAdapter.__new__(DatabaseAdapter)
    adapter.disable_redis = False
    adapter.redis_db = FakeRedis()
    adapter.db_d = FakeDBDictionary()

    value, ok = adapter.get("Light", tag="spell")

    assert ok is True
    assert value == "光亮术"
    assert adapter.redis_db.puts == [("Light", "光亮术", "spell")]
