import redis   # 导入redis 模块
from typing import List

redis_pool = redis.ConnectionPool(
    host='localhost', port=6379, decode_responses=True)


class RedisDB:
    def __init__(self, db=0) -> (None):
        self.db_id = db
        self.r = redis.StrictRedis(connection_pool=redis_pool, db=db)

    def put(self, key: str, value: str, tag="") -> (bool):
        tag_v = "%" + value
        if tag is not None and tag!= "":
            tag_v = tag + tag_v
        prefix = tag_v[:tag_v.find('%') + 1]
        stale_values = [
            old_value for old_value in self.r.sinter(key)
            if old_value.startswith(prefix)
        ]
        if stale_values:
            self.r.srem(key, *stale_values)
        return self.r.sadd(key, tag_v)

    def get(self, key: str, tag="") -> (List[str]):
        value = self.r.sinter(key)
        if value == None or len(value) == 0:
            return []
        if tag == None or tag == "":
            return [v[v.find('%')+1:] for v in value]
        else:
            # print(tag, value)
            prefix = tag + "%"
            tag_v = [v[v.find('%')+1:] for v in value if v.startswith(prefix)]
            if len(tag_v) > 0:
                return tag_v
            return []
    # def update(self, key: str, value: str, tag="") -> (bool):
    #     tag_k = key
    #     if tag != "":
    #         tag_k = key + "%" + tag
    #     return self.r.set(tag_k, value)
    def get_list(self, key_list: list):
        keys = list(dict.fromkeys(key_list))
        if not keys:
            return {}

        pipeline = self.r.pipeline(transaction=False)
        for key in keys:
            pipeline.smembers(key)

        terms = {}
        for key, values in zip(keys, pipeline.execute()):
            if not values:
                terms[key] = "null"
                continue

            parsed_values = []
            for value in sorted(values):
                category, separator, cn = value.partition('%')
                if not separator:
                    category, cn = "", value
                parsed_values.append({"cn": cn, "category": category})
            terms[key] = parsed_values
        return terms

    def keys(self):
        return list(self.r.scan_iter())
    def clean(self):
        self.r.flushdb()
