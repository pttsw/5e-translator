from app.core.database.redis_db import RedisDB


class FakeRedis:
    def __init__(self, values):
        self.values = set(values)

    def sinter(self, _key):
        return self.values

    def srem(self, _key, *values):
        for value in values:
            self.values.discard(value)
        return len(values)

    def sadd(self, _key, value):
        before = len(self.values)
        self.values.add(value)
        return 1 if len(self.values) > before else 0


class FakePipeline:
    def __init__(self, values_by_key):
        self.values_by_key = values_by_key
        self.keys = []

    def smembers(self, key):
        self.keys.append(key)
        return self

    def execute(self):
        return [self.values_by_key.get(key, set()) for key in self.keys]


class FakePipelineRedis:
    def __init__(self, values_by_key):
        self.values_by_key = values_by_key
        self.pipeline_instance = None

    def pipeline(self, transaction=True):
        assert transaction is False
        self.pipeline_instance = FakePipeline(self.values_by_key)
        return self.pipeline_instance


def test_redis_get_does_not_fallback_to_other_tag_when_tag_is_requested():
    redis_db = RedisDB.__new__(RedisDB)
    redis_db.r = FakeRedis({"itemProperty%轻型", "book%轻型"})

    assert redis_db.get("Light", tag="spell") == []


def test_redis_get_returns_matching_tag_value():
    redis_db = RedisDB.__new__(RedisDB)
    redis_db.r = FakeRedis({"itemProperty%轻型", "spell%光亮术"})

    assert redis_db.get("Light", tag="spell") == ["光亮术"]


def test_redis_get_matches_exact_tag_prefix():
    redis_db = RedisDB.__new__(RedisDB)
    redis_db.r = FakeRedis({"spellcasting%施法", "spell%光亮术"})

    assert redis_db.get("Light", tag="spell") == ["光亮术"]


def test_redis_put_replaces_existing_value_for_same_tag_only():
    redis_db = RedisDB.__new__(RedisDB)
    redis_db.r = FakeRedis({"itemProperty%轻型", "spell%轻型"})

    redis_db.put("Light", "光亮术", tag="spell")

    assert redis_db.r.values == {"itemProperty%轻型", "spell%光亮术"}


def test_redis_get_list_deduplicates_keys_and_batches_queries():
    redis_db = RedisDB.__new__(RedisDB)
    redis_db.r = FakePipelineRedis({
        "light": {"spell%光亮术", "normal%光照"},
    })

    result = redis_db.get_list(["light", "missing", "light"])

    assert redis_db.r.pipeline_instance.keys == ["light", "missing"]
    assert result == {
        "light": [
            {"cn": "光照", "category": "normal"},
            {"cn": "光亮术", "category": "spell"},
        ],
        "missing": "null",
    }


def test_redis_get_list_returns_empty_mapping_without_opening_pipeline():
    redis_db = RedisDB.__new__(RedisDB)
    redis_db.r = FakePipelineRedis({})

    assert redis_db.get_list([]) == {}
    assert redis_db.r.pipeline_instance is None
