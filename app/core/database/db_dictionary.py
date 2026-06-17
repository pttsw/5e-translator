import threading
import time
import datetime
import json
import os
import re
from .memory_db_dictionary import MemoryDBDictionary
from .mysql_db import MySQLDatabase
from config import DB_CONFIG, logger
from app.core.utils import find_reference
from app.core.utils import normalize_tagless_text
from app.core.bean.term import Term, to_terms


class DBDictionary:
    _instance_lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if os.getenv("TRANSLATOR_DB_BACKEND", "").lower() == "memory":
            return MemoryDBDictionary(*args, **kwargs)
        if not hasattr(DBDictionary, "_instance"):
            with DBDictionary._instance_lock:
                if not hasattr(DBDictionary, "_instance"):
                    DBDictionary._instance = object.__new__(cls)
        return DBDictionary._instance
    
    def __init__(self, source="", version='2.0.0', d_dict={}, conn_num=None
                 ) -> None:
        conn_num = conn_num or int(os.getenv("TRANSLATOR_DB_CONN_NUM", "1"))
        if getattr(self, "_initialized", False):
            self.source = source or self.source
            self.version = version or self.version
            self.__ensure_connection_count(conn_num)
            return

        self._initialized = True
        self.source = source
        self.version = version
        self.lock = threading.Lock()
        self.db_list: list[MySQLDatabase] = []
        self.available_list = []
        self.ok = True
        self.__ensure_connection_count(conn_num)
        if not self.ok:
            logger.info("初始化数据库字典出错")
            return

        # self.dictionary = d_dict
        self.dictionary = {}
        self.lower_dictionary = {}
        self.proofread_set = set()

    def __del__(self):
        self.close()

    def close(self):
        for db in getattr(self, "db_list", []):
            db.close()

    def __ensure_connection_count(self, conn_num: int):
        conn_num = max(int(conn_num or 1), 1)
        while len(self.db_list) < conn_num:
            db = MySQLDatabase(host=DB_CONFIG['HOST'],
                               port=DB_CONFIG['PORT'],
                               user=DB_CONFIG['USER'],
                               password=DB_CONFIG['PASSWORD'],
                               database=DB_CONFIG['DATABASE'])
            self.db_list.append(db)
            self.available_list.append(True)
            if not db.ok:
                self.ok = False

    @staticmethod
    def _should_skip_term_en(en: str) -> bool:
        normalized = (en or "").strip()
        if normalized.lower().startswith("area"):
            return True
        if normalized.startswith("+"):
            return True
        if normalized[:1].isdigit():
            return True
        if re.match(r"^\d+\.", normalized):
            return True
        if re.match(r"^\d+\s+\S+\.?$", normalized):
            return True
        if re.match(r"^[A-Za-z0-9]{1,3}[.:：。]", normalized):
            return True
        if re.match(r"^[\(\[（【]\d+[\)\]）】]", normalized):
            return True
        if len(normalized) >= 2 and (
            (normalized.startswith("(") and normalized.endswith(")")) or
            (normalized.startswith("（") and normalized.endswith("）")) or
            (normalized.startswith("[") and normalized.endswith("]")) or
            (normalized.startswith("【") and normalized.endswith("】"))
        ):
            return True
        return False

    def __get_priority(self, r, k, tag):
        """获取翻译优先级

        Args:
            r (dict): 数据库查询结果
            k (str): 待翻译的英文
            tag (str): 标签

        Returns:
            int: 翻译优先级
        """
        conditions = [
            r['en'] == k,
            r['category'] == tag,
            r['proofread'] == 1
        ]
        priority = 0
        for i, cond in enumerate(conditions):
            if cond:
                priority += 2 ** (2 - i)
        return priority

    @staticmethod
    def __job_usage_context(job):
        if job is None:
            return {
                "uid": "",
                "key_path": "",
                "context_key": "",
                "context_label": "",
            }
        return {
            "uid": getattr(job, "uid", "") or "",
            "key_path": getattr(job, "key_path", "") or "",
            "context_key": getattr(job, "context_key", "") or "",
            "context_label": getattr(job, "context_label", "") or "",
        }

    def __row_to_bean(self, row: dict):
        return {
            'en': row['en'],
            'cn': row['cn'],
            'category': row['category'],
            'proofread': row['proofread'],
            'is_key': row['is_key'],
            'sql_id': row['id'],
            'modified_at': row['modified_at'],
        }

    def __get_usage_match(self, db, k: str, tag: str, rel_f: str, job=None):
        if not rel_f or job is None:
            return None
        context = self.__job_usage_context(job)
        target_tag = tag if tag not in ("", None) else None
        base_select = (
            "SELECT w.id, w.en, w.cn, w.json_file, w.proofread, w.category, "
            "w.modified_at, w.is_key "
            "FROM word_usage wu JOIN words w ON w.id = wu.word_id "
            "WHERE wu.file = %s AND w.en = %s AND "
        )
        lookups = []
        if context["uid"]:
            lookups.append(("wu.uid = %s", context["uid"]))
        if context["key_path"]:
            lookups.append(("wu.key_path = %s", context["key_path"]))
        if context["context_key"]:
            lookups.append(("wu.context_key = %s", context["context_key"]))
        for usage_sql, usage_value in lookups:
            rows = db.execute_query(
                base_select + usage_sql + " "
                "ORDER BY w.proofread DESC, w.modified_at DESC LIMIT 1",
                (rel_f, k, usage_value),
            )
            if rows:
                if usage_sql == "wu.uid = %s" and rows[0]['category'] != target_tag:
                    logger.info(
                        "word_usage按uid命中但category不同，优先使用已绑定记录: "
                        f"file={rel_f}, uid={usage_value}, word_id={rows[0]['id']}, "
                        f"word_category={rows[0]['category']}, job_category={target_tag}"
                    )
                    return self.__row_to_bean(rows[0])
                if rows[0]['category'] != target_tag:
                    continue
                return self.__row_to_bean(rows[0])
        return None

    def __put_word_usage(self, db, word_id: int, rel_f: str, job=None):
        if not word_id or not rel_f:
            return True
        context = self.__job_usage_context(job)
        uid = context["uid"] or f"legacy:{word_id}"
        return db.execute_non_query(
            """
            INSERT INTO word_usage
                (word_id, file, uid, key_path, context_key, context_label, version)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                word_id = VALUES(word_id),
                key_path = VALUES(key_path),
                context_key = VALUES(context_key),
                context_label = VALUES(context_label),
                version = VALUES(version)
            """,
            (
                word_id,
                rel_f,
                uid,
                context["key_path"],
                context["context_key"],
                context["context_label"],
                self.version,
            ),
        )

    def __get_usage_matches_by_uid(self, db, keys: list, tags: list, rel_f: str, contexts: list):
        uid_requests = {}
        for index, (k, t) in enumerate(zip(keys, tags)):
            if index >= len(contexts):
                continue
            job_context = contexts[index]
            uid = getattr(job_context, "uid", "") or ""
            if not uid:
                continue
            uid_requests[uid] = (k, t, job_context)
        if not uid_requests:
            return {}

        result = {}
        uids = list(uid_requests.keys())
        chunk_size = 500
        for start in range(0, len(uids), chunk_size):
            chunk = uids[start:start + chunk_size]
            placeholders = ','.join(['%s'] * len(chunk))
            rows = db.execute_query(
                "SELECT wu.uid, w.id, w.en, w.cn, w.json_file, w.proofread, "
                "w.category, w.modified_at, w.is_key "
                "FROM word_usage wu JOIN words w ON w.id = wu.word_id "
                f"WHERE wu.file = %s AND wu.uid IN ({placeholders})",
                (rel_f, *chunk),
            ) or []
            for row in rows:
                uid = row.get('uid') or ""
                request = uid_requests.get(uid)
                if request is None:
                    continue
                k, t, _job_context = request
                target_tag = t if t not in ("", None) else None
                if row['en'] != k:
                    continue
                if row['category'] != target_tag:
                    logger.debug(
                        "word_usage按uid命中但category不同，优先使用已绑定记录: "
                        f"file={rel_f}, uid={uid}, word_id={row['id']}, "
                        f"word_category={row['category']}, job_category={target_tag}"
                    )
                result[uid] = self.__row_to_bean(row)
        return result

    def __put_word_usages_if_needed(self, db, usage_items: list):
        desired = {}
        for word_id, rel_f, job_context in usage_items:
            if not word_id or not rel_f:
                continue
            context = self.__job_usage_context(job_context)
            uid = context["uid"] or f"legacy:{word_id}"
            desired[(rel_f, uid)] = {
                "word_id": word_id,
                "file": rel_f,
                "uid": uid,
                "key_path": context["key_path"],
                "context_key": context["context_key"],
                "context_label": context["context_label"],
            }
        if not desired:
            return True

        existing = {}
        files_to_uids = {}
        for file_name, uid in desired.keys():
            files_to_uids.setdefault(file_name, []).append(uid)
        chunk_size = 500
        for file_name, uids in files_to_uids.items():
            for start in range(0, len(uids), chunk_size):
                chunk = uids[start:start + chunk_size]
                placeholders = ','.join(['%s'] * len(chunk))
                rows = db.execute_query(
                    "SELECT file, uid, word_id, key_path, context_key, context_label "
                    "FROM word_usage "
                    f"WHERE file = %s AND uid IN ({placeholders})",
                    (file_name, *chunk),
                ) or []
                for row in rows:
                    existing[(row['file'], row['uid'])] = row

        pending = []
        for key, item in desired.items():
            current = existing.get(key)
            if current and (
                int(current.get('word_id') or 0) == int(item["word_id"])
                and (current.get('key_path') or "") == item["key_path"]
                and (current.get('context_key') or "") == item["context_key"]
                and (current.get('context_label') or "") == item["context_label"]
            ):
                continue
            pending.append(item)
        if not pending:
            return True

        ok = True
        for start in range(0, len(pending), chunk_size):
            chunk = pending[start:start + chunk_size]
            values_sql = ','.join(['(%s, %s, %s, %s, %s, %s, %s)'] * len(chunk))
            params = []
            for item in chunk:
                params.extend([
                    item["word_id"],
                    item["file"],
                    item["uid"],
                    item["key_path"],
                    item["context_key"],
                    item["context_label"],
                    self.version,
                ])
            ok = db.execute_non_query(
                "INSERT INTO word_usage "
                "(word_id, file, uid, key_path, context_key, context_label, version) "
                f"VALUES {values_sql} "
                "ON DUPLICATE KEY UPDATE "
                "word_id = VALUES(word_id), "
                "key_path = VALUES(key_path), "
                "context_key = VALUES(context_key), "
                "context_label = VALUES(context_label), "
                "version = VALUES(version)",
                tuple(params),
            ) and ok
        return ok
    
    def update_by_hand(self, k: str, v: str, tag=""):
        """手动更新数据库

        Args:
            k (str): 待翻译的英文
            v (str): 翻译结果
            tag (str, optional): 标签. Defaults to "".
        """
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        res = db.select('words', columns=['id', 'en', 'cn','source', 'json_file', 'proofread','category'], condition={
                'en': k}, order_by='version desc')
        if len(res) == 0 or all([r['proofread'] == 1 for r in res]):
            # raise Exception(f"数据库中不存在{k}")
            self.__release_db(db_index)
            return
        proofread_res_list = list(filter(lambda x: x['proofread'] == 1, res))
        all_proofread_cn_str_equals = len(set([r['cn'] for r in proofread_res_list])) == 1
        
        proofread_cn_str = proofread_res_list[0]['cn'] if all_proofread_cn_str_equals else ''
        
        if all_proofread_cn_str_equals:
            all_proofreaded = True
            for r in filter(lambda x: x['proofread'] == 0, res):
                if r['cn'] != proofread_cn_str:
                    all_proofreaded = False
                else:
                    print(f"自动校对单词 {r['id']} {r['en']} 为 {proofread_cn_str}")
                    db.update('words', {'proofread': 1,'modified_at': datetime.datetime.now()}, {'id': r['id']})
            if all_proofreaded:
                self.__release_db(db_index)
                return
        # for r in proofread_res_list:
        #     if proofread_cn_str == '':
        #         proofread_cn_str = r['cn']
        #     elif proofread_cn_str != r['cn']:
        references = find_reference(k)
        for ref in references:
            print(ref)
        for i, r in enumerate(res):
            print(i,r)
        if len(res) == 1:
            selected_i = 0
        else:
            selected_i = int(input("请输入要更新的项："))

        selected_id = res[selected_i]['id']

        input_str = input("请输入翻译结果：")
        if input_str == "":
            db.update('words', {'proofread': 1,'modified_at': datetime.datetime.now()}, {'id': selected_id})
        elif input_str != "skip":
            db.update('words', {'cn': input_str, 'proofread': 1,'modified_at': datetime.datetime.now()}, {'id': selected_id})
        self.__release_db(db_index)
    
    def get(self, k: str, rel_f="", load_from_sql=False, ignore_case=False, tag="", correct_tag_from_db=False, job_context=None):
        """从数据库读取翻译

        Args:
            k (str): 待翻译的英文
            rel_f (str, optional): 来源文件. Defaults to "".
            load_from_sql (bool, optional): 是否从数据库读取，如果为False，则直接从内存中读取. Defaults to True.
            ignore_case (bool, optional): 是否忽略大小写. Defaults to False.
            tag (str, optional): 标签. Defaults to "".

        Returns:
            str: 翻译结果
            bool: 若翻译成功，则返回True， 否则返回False
        """
        v_bean = None # 翻译结果   
        if rel_f and job_context is not None and load_from_sql:
            db_index = self.__get_db_index()
            db = self.db_list[db_index]
            try:
                v_bean = self.__get_usage_match(db, k, tag, rel_f, job_context)
            finally:
                self.__release_db(db_index)
        redis_bean = self.__get_redis(k, tag, ignore_case, correct_tag_from_db)
        if v_bean is None and redis_bean != None:
            v_bean = redis_bean
            if rel_f != "" and job_context is not None:
                self.__put_usage_by_word_id(v_bean['sql_id'], rel_f, job_context)
        # elif ignore_case:
        #     # tempd = list(map(lambda dk: {dk.lower():self.dictionary[dk]},self.dictionary.keys()))
        #     matched = False # 是否匹配到大小写不一致，且tag不匹配的值。
        #     for dk in self.dictionary.keys():
        #         if db_k.lower() == dk.lower():
        #             v = self.dictionary[dk]
        #             break
        #         if not matched and k.lower() == dk.lower():
        #             v = self.dictionary[dk]
        #             matched = True
        # 从数据库中读取
        if v_bean == None and load_from_sql:
            logger.debug(f"从数据库中读取{k}")
            db_index = self.__get_db_index()
            db = self.db_list[db_index]
            res = db.select('words', columns=['id', 'en', 'cn', 'json_file', 'proofread','category','modified_at','is_key'], condition={
                            'en': k}, order_by='version desc')
            self.__release_db(db_index)
            if res == None:
                return None
            if len(res) > 0:
                v_bean = self.__get_best_match(res, k, tag)
                if v_bean == None:
                    return None
                if rel_f != "":
                    if correct_tag_from_db:
                        redis_bean = self.__get_redis(k, tag, ignore_case, False)
                        if redis_bean == None or redis_bean['category'] != v_bean['category']:
                            self.__put_usage_by_word_id(v_bean['sql_id'], rel_f, job_context)
                            logger.info(f"插入word_usage表成功，word_id: {v_bean['sql_id']}, en: {k}, cn: {v_bean['cn']}")
                    else:
                        # 尝试插入source表
                        self.__put_usage_by_word_id(v_bean['sql_id'], rel_f, job_context)
                        logger.info(f"插入word_usage表成功，word_id: {v_bean['sql_id']}, en: {k}, cn: {v_bean['cn']}")
 
                self.__put_redis(k, v_bean['cn'], v_bean['category'], v_bean['proofread'], v_bean['is_key'], v_bean['sql_id'], v_bean['modified_at'])
        # self.__release_db(db_index)
        # logger.debug(f"get函数执行时间：{time.time() - start_time} 秒")
        return v_bean
    
    def __get_best_match(self, res: list, k: str, tag: str) -> (dict):
        v_bean = None
        # MYSQL查出来不区分大小写，所以需要精细化判断一下
        max_priority = -1
        best_match = None
        if len(res) <= 0:
            logger.error(f"从数据库中查询到空结果，en: {k}, tag: {tag}")
            return None
        for r in res:
            priority = self.__get_priority(r, k, tag)
            if priority > max_priority:
                max_priority = priority
                best_match = r
        if best_match:
            v_bean = {
                'en': best_match['en'],
                'cn': best_match['cn'],
                'category': best_match['category'],
                'proofread': best_match['proofread'],
                'is_key': best_match['is_key'], # 是否有校对
                'sql_id': best_match['id'],
                'modified_at': best_match['modified_at'],
            }
            # if rel_f != "":
            #     if correct_tag_from_db:
            #         redis_bean = self.__get_redis(k, tag, ignore_case, False)
            #         if redis_bean == None or redis_bean['category'] != best_match['category']:
            #             self.__put_source_by_word_id(best_match['id'], rel_f)
            #             logger.info(f"插入source表成功，word_id: {best_match['id']}, en: {best_match['en']}, cn: {best_match['cn']}")
            #     else:
            #         # 尝试插入source表
            #         self.__put_source_by_word_id(best_match['id'], rel_f)
            #         logger.info(f"插入source表成功，word_id: {best_match['id']}, en: {best_match['en']}, cn: {best_match['cn']}")
        if v_bean == None:

            v_bean = {
                'en': res[0]['en'],
                'cn': res[0]['cn'],
                'category': res[0]['category'],
                'proofread': res[0]['proofread'],
                'is_key': res[0]['is_key'],
                'sql_id': res[0]['id'],
                'modified_at': best_match['modified_at'],
            }
        return v_bean
    
    def get_bunch(self, keys: list, tags: list, rel_f:str, ignore_case: bool = False, correct_tag_from_db: bool = False, job_contexts=None) -> (dict):
        """批量获取翻译

        Args:
            keys (list): 待翻译的英文列表
            tag (str, optional): 标签. Defaults to "".
            ignore_case (bool, optional): 是否忽略大小写. Defaults to False.
            correct_tag_from_db (bool, optional): 是否从数据库中获取标签. Defaults to False.

        Returns:
            dict: 翻译结果，key为(en, tag)，value为翻译结果
        """
        res_beans = {}
        query_requests = []
        query_keys = []
        seen_query_keys = set()
        query_beans = []
        cached_usage_items = []
        contexts = list(job_contexts or [])
        if contexts and rel_f:
            db_index = self.__get_db_index()
            db = self.db_list[db_index]
            try:
                usage_matches = self.__get_usage_matches_by_uid(db, keys, tags, rel_f, contexts)
                for k, t, job_context in zip(keys, tags, contexts):
                    uid = getattr(job_context, "uid", "") or ""
                    usage_bean = usage_matches.get(uid) if uid else self.__get_usage_match(db, k, t, rel_f, job_context)
                    if usage_bean is not None:
                        if uid:
                            res_beans[(k, t, uid)] = usage_bean
                        else:
                            res_beans[(k, t)] = usage_bean
                        self.__put_redis(k, usage_bean['cn'], usage_bean['category'], usage_bean['proofread'], usage_bean['is_key'], usage_bean['sql_id'], usage_bean['modified_at'])
            finally:
                self.__release_db(db_index)
        for index, (k, t) in enumerate(zip(keys, tags)):
            uid = getattr(contexts[index], "uid", "") if index < len(contexts) else ""
            if (k, t) in res_beans or (uid and (k, t, uid) in res_beans):
                continue
            redis_bean = self.__get_redis(k, t, ignore_case, correct_tag_from_db)
            if redis_bean != None:
                if uid:
                    res_beans[(k, t, uid)] = redis_bean
                else:
                    res_beans[(k, t)] = redis_bean
                if rel_f and index < len(contexts):
                    job_context = contexts[index] if index < len(contexts) else None
                    cached_usage_items.append((redis_bean['sql_id'], rel_f, job_context))
            else:
                job_context = contexts[index] if index < len(contexts) else None
                query_requests.append((k, t, job_context))
                if k not in seen_query_keys:
                    query_keys.append(k)
                    seen_query_keys.add(k)
        if len(query_keys) == 0:
            if cached_usage_items:
                db_index = self.__get_db_index()
                db = self.db_list[db_index]
                try:
                    ok = self.__put_word_usages_if_needed(db, cached_usage_items)
                    if not ok:
                        logger.error(f"批量回填word_usage失败，file: {rel_f}")
                finally:
                    self.__release_db(db_index)
            return res_beans
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        try:
            placeholders = ','.join(['%s'] * len(query_keys))
            query_sql = f"select id, en, cn, json_file, proofread, category, modified_at, is_key from words where en in ({placeholders}) order by version desc"
            params = tuple(query_keys)
            logger.debug(f"从数据库中查询keys: {query_sql}")
            res = db.execute_query(query_sql, params)
            if res is None:
                return res_beans
            if ignore_case:
                returned_lower_keys = {
                    row['en'].lower()
                    for row in res
                    if isinstance(row.get('en'), str)
                }
                missing_lower_keys = []
                seen_lower_keys = set()
                for query_k in query_keys:
                    lower_key = query_k.lower()
                    if lower_key in returned_lower_keys or lower_key in seen_lower_keys:
                        continue
                    missing_lower_keys.append(lower_key)
                    seen_lower_keys.add(lower_key)
                if missing_lower_keys:
                    lower_placeholders = ','.join(['%s'] * len(missing_lower_keys))
                    lower_query_sql = (
                        "select id, en, cn, json_file, proofread, category, modified_at, is_key "
                        f"from words where LOWER(en) in ({lower_placeholders}) order by version desc"
                    )
                    lower_res = db.execute_query(lower_query_sql, tuple(missing_lower_keys))
                    if lower_res:
                        seen_ids = {row['id'] for row in res}
                        res.extend(row for row in lower_res if row['id'] not in seen_ids)
            logger.debug(f"从数据库中查询到结果，keys数量: {len(query_keys)}, res数量: {len(res)}")
            grouped_res = {}
            grouped_lower_res = {}
            for row in res:
                grouped_res.setdefault(row['en'], []).append(row)
                if ignore_case and isinstance(row.get('en'), str):
                    grouped_lower_res.setdefault(row['en'].lower(), []).append(row)

            for query_k, query_t, job_context in query_requests:
                res_k = grouped_res.get(query_k, [])
                if len(res_k) == 0 and ignore_case:
                    res_k = grouped_lower_res.get(query_k.lower(), [])
                if len(res_k) == 0:
                    logger.info(f"数据库中不包含此项，en: {query_k}, tag: {query_t}")
                    continue
                v_bean = self.__get_best_match(res_k, query_k, query_t)
                if v_bean == None:
                    continue
                res_beans[(query_k, query_t)] = v_bean
                query_beans.append((v_bean, job_context))
                self.__put_redis(query_k, v_bean['cn'], v_bean['category'], v_bean['proofread'], v_bean['is_key'], v_bean['sql_id'], v_bean['modified_at'])
            if rel_f != "":
                usage_items = cached_usage_items + [
                    (v_bean['sql_id'], rel_f, job_context)
                    for v_bean, job_context in query_beans
                ]
                if usage_items:
                    ok = self.__put_word_usages_if_needed(db, usage_items)
                    if not ok:
                        logger.error(f"插入word_usage表失败，file: {rel_f}")
        finally:
            self.__release_db(db_index)
        return res_beans

    def get_tag_only_update_match(self, en: str, tag: str = ""):
        """Find a unique cached record whose visible text matches despite tag changes."""
        target_tag = tag if tag not in ("", None) else None
        target_text = normalize_tagless_text(en)
        if target_text == "":
            return None

        candidates = []
        seen_ids = set()
        for cached_en, beans in self.dictionary.items():
            if normalize_tagless_text(cached_en) != target_text:
                continue
            for bean in beans:
                sql_id = bean.get("sql_id")
                if sql_id in seen_ids:
                    continue
                if bean.get("en") == en and bean.get("category") == target_tag:
                    continue
                seen_ids.add(sql_id)
                candidate = dict(bean)
                candidate["old_en"] = bean.get("en") or cached_en
                candidate["old_category"] = bean.get("category")
                candidates.append(candidate)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def put2(self, key: str, value, rel_f: str) -> (bool):
        self.lock.acquire()
        # TODO 这里会导致json_file字段无法更新，但是为了快，暂时先这样
        # if key in self.dictionary.keys() and self.dictionary[key] == value:
        #     self.lock.release()
        #     return True
        self.dictionary[key] = value
        res = self.db.select('words', columns=[
                             'id', 'json_file', 'version', 'source'], condition={'en': key, 'cn': value})
        if res == None:
            self.lock.release()
            return False
        if len(res) == 0:
            self.db.insert('words', {'en': key, 'cn': value, 'json_file': rel_f,
                           'source': self.source, 'version': self.version, 'modified_by': 1})
            rows = self.db.execute_query(
                "SELECT id FROM words WHERE en = %s AND cn = %s",
                (key, value),
            ) or []
            for row in rows:
                self.__put_word_usage(self.db, row['id'], rel_f)
            self.lock.release()
            return True
        for r in res:
            self.__put_word_usage(self.db, r['id'], rel_f)
        self.lock.release()
        return True

    def __put_usage_by_word_id(self, word_id: int, rel_f: str, job_context=None):
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        self.__put_word_usage(db, word_id, rel_f, job_context)
        self.__release_db(db_index)
        # logger.info(f"插入word_usage表成功，file: {rel_f}, word_id: {word_id}, version: {self.version}")
        
    def put(self, key: str, value, rel_f: str, insert_word=True, proofread=False, tag="", job_context=None) -> (bool):
        """
        插入翻译结果

        Args:
            key (str): 英文
            value (str): 中文
            rel_f (str): 来源文件
            insert_word (bool, optional): 是否插入到words表中. Defaults to True.
            proofread (bool, optional): 是否校对过. Defaults to False.
            tag (str, optional): 标签. Defaults to "".

        Returns:
            bool: 若插入成功，则返回True， 否则返回False
        """
        start_time = time.time()
        db_index = self.__get_db_index()
        db = self.db_list[db_index]

        # 更新words表
        if insert_word:
            p = 1 if proofread else 0
            # 去重校验
            res = db.select('words', columns=['id', 'json_file', 'version', 'source'], condition={
                            'BINARY en': key, 'cn': value})
            if res == None:
                self.__release_db(db_index)
                logger.error(f"查询words表失败，en: {key}, cn: {value}, file: {rel_f}, version: {self.version}, proofread: {p}, category: {tag}")
                return False
            if len(res) == 0:
                logger.info(f"插入words表，en: {key}, cn: {value}, file: {rel_f}, version: {self.version}, proofread: {p}, category: {tag}")
                ok = db.insert('words', {'en': key, 'cn': value, 'json_file': rel_f,
                          'source': self.source, 'version': self.version, 'proofread': p, 'modified_by': 1, 'category':tag})
                if not ok:
                    logger.error(f"插入words表失败，en: {key}, cn: {value}, file: {rel_f}, version: {self.version}, proofread: {p}, category: {tag}")
                    return False
        word_rows = []
        if tag != "":
            word_rows = db.execute_query(
                "SELECT id FROM words WHERE BINARY en = %s AND cn = %s AND category = %s",
                (key, value, tag),
            ) or []
        else:
            word_rows = db.execute_query(
                "SELECT id FROM words WHERE BINARY en = %s AND cn = %s AND (category is null OR category = '')",
                (key, value),
            ) or []
        word_id = None
        for row in word_rows:
            if word_id is None:
                word_id = row['id']
            self.__put_word_usage(db, row['id'], rel_f, job_context)
        self.__release_db(db_index)
        logger.debug(f"put函数执行时间：{time.time() - start_time} 秒")
        return word_id or True

    def put_term(self, term, source="", modified_by=1, modified_reson=""):
        """写入Term表

        Args:
            term (Term): _description_
        """
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        try:
            return db.insert('term', {
                'en': term.en,
                'cn': term.cn,
                'category': term.category or '',
                'source': source or '',
                'modified_reson': modified_reson or '',
                'modified_by': modified_by,
            })
        finally:
            self.__release_db(db_index)

    def get_term_entries(self, en: str):
        """按英文查询 term 表记录。"""
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        try:
            res = db.select(
                'term',
                columns=['en', 'cn', 'category', 'source'],
                condition={'en': en},
                order_by='category asc'
            )
            return res or []
        finally:
            self.__release_db(db_index)

    def get_all_term_entries(self):
        """读取 term 表中的全部原始记录。"""
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        try:
            return db.select('term', columns=['en', 'cn', 'category']) or []
        finally:
            self.__release_db(db_index)

    def update_term(self, en: str, old_cn: str, new_cn: str, category="", source=None, modified_by=1, modified_reson=""):
        """更新 term 表中的单条记录。"""
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        try:
            params = [new_cn, modified_by, modified_reson or "", en, category or "", old_cn]
            if source is None:
                sql = (
                    "UPDATE term SET cn = %s, modified_by = %s, modified_reson = %s "
                    "WHERE en = %s AND category = %s AND cn = %s"
                )
            else:
                sql = (
                    "UPDATE term SET cn = %s, modified_by = %s, modified_reson = %s, source = %s "
                    "WHERE en = %s AND category = %s AND cn = %s"
                )
                params = [new_cn, modified_by, modified_reson or "", source, en, category or "", old_cn]
            return db.execute_non_query(sql, tuple(params))
        finally:
            self.__release_db(db_index)
    
    def get_all_term(self):
        """获取所有的Term，并按照一定规则格式化和筛选

        Returns:
            _type_: _description_
        """
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        term_res = db.select('term', columns=['en', 'cn', 'category'])
        words_res = db.execute_query("select en, cn, category from words where proofread = 1 and (LENGTH(en) - LENGTH(replace(en,' ','')) < 4 or en like '%{@recharge%}' or en like '%(Costs%Action%)') and en != cn and (category != 'no-term' or category is null)")
        res = set()
        for r in term_res:
            if self._should_skip_term_en(r['en']):
                continue
            res.add(Term(r['en'], r['category'], r['cn']))
        for r in words_res:
            if self._should_skip_term_en(r['en']):
                continue
            res.update(to_terms(r['en'], r['cn'], r['category']))
        self.__release_db(db_index)
        return res

    def get_word_terms_for_sync(self):
        """按 add 模式的来源规则，从 words 表生成候选术语。"""
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        try:
            words_res = db.execute_query(
                "select en, cn, category from words "
                "where proofread = 1 "
                "and (LENGTH(en) - LENGTH(replace(en,' ','')) < 4 "
                "or en like '%{@recharge%}' or en like '%(Costs%Action%)') "
                "and en != cn and (category != 'no-term' or category is null)"
            )
            res = set()
            for r in words_res or []:
                if self._should_skip_term_en(r['en']):
                    continue
                res.update(to_terms(r['en'], r['cn'], r['category']))
            return res
        finally:
            self.__release_db(db_index)
        
    def update(self, sql_id:int, en_or_cn:str, cn=None, proofread=True, tag="", rel_f="", job_context=None) -> (bool):
        start_time = time.time()
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        if proofread:
            p = 1
        else:
            p = 0
            
        en = None
        if cn is None:
            cn = en_or_cn
        else:
            en = en_or_cn

        res = db.select('words', columns=['id', 'json_file', 'version', 'source'], condition={
                        'id': sql_id})
        if res == None:
            self.__release_db(db_index)
            return False
        if len(res) == 0:
            print(f"{db_index}:update fail: no record:\n" +
                    sql_id+"\n" + cn)
        #     db.insert('words', {'en': key, 'cn': value, 'json_file': rel_f, 'source': self.source, 'version': self.version, 'modified_by':1})
        for r in res:
            update_data = {'cn': cn, 'source': self.source, 'proofread': p,
                        'version': self.version, 'modified_by': 1 ,'modified_at': datetime.datetime.now()}
            if en is not None:
                update_data['en'] = en
            if tag not in ("", None):
                update_data['category'] = tag
            db.update('words', update_data, {'id': r['id']})
            if rel_f:
                self.__put_word_usage(db, r['id'], rel_f, job_context)
        # db.execute_non_query("""insert into source (file, word_id, version) 
        #                           SELECT %s, id, %s FROM words 
        #                           WHERE BINARY en = %s AND cn = %s 
        #                           ON DUPLICATE KEY UPDATE version = VALUES(version);""", (rel_f, self.version, key, value))
        self.__release_db(db_index)
        logger.debug(f"update函数执行时间：{time.time() - start_time} 秒")
        return True

    def putSource(self, key: str, value, rel_f: str):
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        rows = db.execute_query(
            "SELECT id FROM words WHERE BINARY en = %s AND cn = %s",
            (key, value),
        ) or []
        for row in rows:
            self.__put_word_usage(db, row['id'], rel_f)
        self.__release_db(db_index)
        return True

    def __get_db_index(self):
        # 获取数据库连接池中可用连接的索引
        while True:
            self.lock.acquire()
            for i, available in enumerate(self.available_list):
                if available:
                    self.available_list[i] = False
                    self.lock.release()
                    return i
            self.lock.release()
            time.sleep(1)

    def __release_db(self, db_index: int):
        self.lock.acquire()
        self.available_list[db_index] = True
        self.lock.release()

    def clear(self):
        self.dictionary = {}
        self.lower_dictionary = {}
        self.proofread_set = set()

    def __put_redis(self, en, cn, category = None, proofread = 0, is_key = False, sql_id = None, modified_at = 0):
        
        bean = {
            'en': en,
            'cn': cn,
            'category': category,
            'proofread': proofread,
            'is_key': is_key,
            'sql_id': sql_id,
            'modified_at': modified_at
        }
        if en in self.dictionary:
            if any(c['cn'] == cn and c['category'] == category for c in self.dictionary[en]):
                logger.debug(f"重复插入{en}->{cn}")
                return
            self.dictionary[en].append(bean)
        else:
            self.dictionary[en] = [bean]
            
        en = en.lower()
        if en in self.lower_dictionary:
            self.lower_dictionary[en].append(bean)
        else:
            self.lower_dictionary[en] = [bean]
        
    def __get_redis(self, en, tag = None, ignore_case = False, correct_tag_from_db=False):
        if en in self.dictionary:
            cn_bean = None
            target_category = tag if tag else None
            candidates = (c for c in self.dictionary[en] if c['category'] == target_category)
            for c in candidates:
                if c['proofread'] == 1:
                    return c
                cn_bean = c
            if cn_bean != None:
                return cn_bean
            if tag not in (None, ""):
                return None
            if not correct_tag_from_db:
                for c in self.dictionary[en]:
                    if c['proofread'] == 1:
                        return c
                return self.dictionary[en][0]
        
        # 忽略大小写
        en = en.lower()
        if ignore_case and en in self.lower_dictionary:
            cn_bean = None
            if tag != None and tag != "":
                for c in self.lower_dictionary[en]:
                    if c['category'] == tag:
                        if c['proofread'] == 1:
                            return c
                        cn_bean = c
            if cn_bean != None:
                return cn_bean
            if correct_tag_from_db:
                return None
            if tag not in (None, ""):
                return None
            for c in self.lower_dictionary[en]:
                if c['proofread'] == 1:
                    return c
            return self.lower_dictionary[en][0]
        return None
    
    def dump(self, file_names=[]):
        """
        file_name: 按照文件名提取，为空时则提取全部
        """
        self.lock.acquire()
        if len(file_names) == 0:
            records = self.db_list[0].select(
                'words', columns=['cn', 'en', 'version', 'proofread', 'category', 'modified_at', 'is_key'])
        else:
            placeholders = ','.join(['%s'] * len(file_names))
            sql = f"select id, cn, en, version, proofread, category, modified_at, is_key from words where id in (select word_id from word_usage where file in ({placeholders}))"
            params = tuple(file_names)
            records = self.db_list[0].execute_query(sql, params)
        self.lock.release()

        version_dict = {}

        for i, s in enumerate(records):
            en = s['en']
            cn = s['cn']
            # db_k = en
            self.__put_redis(en, cn, s['category'], s['proofread'], s['is_key'], s['id'], s['modified_at'])
                
            v = version_dict.get(en)

            if v != None and v >= s['version']:
                continue
    def dumpLockedEntries(self, entry_names=[]):
        """
        提取已锁定的文件
        """
        self.lock.acquire()
        if len(entry_names) == 0:
            self.lock.release()
            return {}
        else:
            placeholders = ','.join(['%s'] * len(entry_names))
            sql = f"select file, en_json, cn_json from file where source_file in ({placeholders}) and locked = 1"
            params = tuple(entry_names)
            files = self.db_list[0].execute_query(sql, params)
        self.lock.release()
        res = {}
        for f in files:
            en_json = json.loads(f['en_json'])
            cn_json = json.loads(f['cn_json'])
            res[f['file']] = {
                'en_json': en_json,
                'cn_json': cn_json
            }
        return res

    def is_proofread(self, k: str):
        return k in self.proofread_set

    def get_credits(self, file_name: str):
        self.lock.acquire()
        credits = self.db_list[0].select(
            'credits', columns=['job_type', 'names'],
            condition={'file': file_name})
        self.lock.release()
        return credits
    
    def update_file_table(self, file_path: str, source_file: str, total: int, translate: int, proofread: int, en_json: str = None):
        parent_dir = os.path.dirname(file_path)
        self.lock.acquire()
        ok = self.db_list[0].execute_non_query(
            "INSERT INTO file (file, parent_dir, source_file, total, translate, proofread, en_json) VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE parent_dir = VALUES(parent_dir), total = VALUES(total), translate = VALUES(translate), proofread = VALUES(proofread), en_json = VALUES(en_json)",
            (file_path, parent_dir, source_file, total, translate, proofread, en_json))
        self.lock.release()
        return ok
    
    def get_file_info(self, file_path: str):
        if not file_path.endswith('.json'):
            file_path += '.json'
        self.lock.acquire()
        info = self.db_list[0].select(
            'file', columns=['total', 'translate', 'proofread'],
            condition={'file': file_path})
        self.lock.release()
        return info[0] if info else None

if __name__ == "__main__":
    d = DBDictionary()
    print(d.get('Elf'))
