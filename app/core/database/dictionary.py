import threading
import os
from . import DBDictionary, RedisDB
from .memory_db_dictionary import MemoryDBDictionary
from config import logger
class DatabaseAdapter:
    def __init__(self, source="", version='1.209.1', conn_num=None) -> (None):
        self.lock = threading.Lock()
        self.ok = True
        use_memory_backend = os.getenv("TRANSLATOR_DB_BACKEND", "").lower() == "memory"
        self.disable_redis = use_memory_backend or os.getenv("TRANSLATOR_DISABLE_REDIS", "").lower() in ("1", "true", "yes", "on")
        self.redis_db = None
        # self.redis_db = None
        # if not self.redis_db.ok:
        #     self.ok = False
        #     return
        self.db_d = DBDictionary(source, version, conn_num=conn_num)
        if not self.db_d.ok:
            logger.warning("MySQL 词典不可用，回退到内存词典后端")
            self.db_d = MemoryDBDictionary(source, version)
            self.ok = self.db_d.ok
            self.disable_redis = True
            return

    def _get_redis_db(self):
        if self.disable_redis:
            return None
        if self.redis_db is None:
            try:
                self.redis_db = RedisDB()
            except Exception as exc:
                logger.warning(f"Redis 不可用，跳过 Redis 缓存: {exc}")
                self.disable_redis = True
                return None
        return self.redis_db
            
    def get(self, k: str, rel_f="", tag=""):
        v = None
        redis_db = self._get_redis_db()
        if redis_db != None:
            v_list = redis_db.get(k, tag=tag)
            if v_list != None and len(v_list) > 0:
                return v_list[0], True
        if self.db_d != None:
            # TODO: 这里的load_from_sql不应该为True，因为这个函数只有job_processor调用，这是不应该再回表查了
            # 现在开启这里的原因是，部分有tag且大小写不一致的情况没有完全写入source表里，所以无法确保在analysis阶段找到，后续应该处理这种情况
            
            v = self.db_d.get(k, rel_f, load_from_sql=True, tag=tag)
            if v != None:
                if redis_db != None:
                    cache_tag = v.get('category') if isinstance(v, dict) else tag
                    redis_db.put(k, v['cn'], tag=cache_tag or "")
                return v['cn'], True
        return v, False
    

    def put(self, key: str, value: str, rel_f:str, proofread = False, tag="", job_context=None) -> (bool):
        redis_db = self._get_redis_db()
        if redis_db != None:
            ok = redis_db.put(key, value, tag=tag)
            if not ok:
                logger.error(f"写入redis失败:{key}, {value}, {rel_f}, {proofread}, {tag}")
        ok = self.db_d.put(key, value, rel_f, proofread=proofread, tag=tag, job_context=job_context)
        return ok
    
    def update(self, sql_id: int, en:str, cn: str, proofread: bool= False, tag="", rel_f="", job_context=None) -> (bool):
        redis_db = self._get_redis_db()
        if redis_db != None:
            ok = redis_db.put(en, cn, tag=tag)
            if not ok:
                return ok
        return self.db_d.update(sql_id, en, cn, proofread=proofread, tag=tag, rel_f=rel_f, job_context=job_context)
    # def update(self, key: str, value: str, old_value, rel_f:str, proofread=False) -> (bool):
    #     return False
        # ok = True
        # ok = self.redis_db.update(key, value, tag=tag)
        # if not ok:
        #     return ok
        # if self.db_d != None:
        #     ok = self.db_d.update(key, value, old_value, rel_f, proofread=proofread)
        #     if not ok:
        #         return False
        # return True
    
    # def close(self):
    #     if self.redis_db != None:
    #         self.redis_db.close()
