import threading
import time
import datetime
import json
from .mysql_db import MySQLDatabase
from config import DB_CONFIG, logger
from app.core.utils import find_reference
from app.core.bean.term import Term, to_terms


class DBDictionary:
    _instance_lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if not hasattr(DBDictionary, "_instance"):
            with DBDictionary._instance_lock:
                if not hasattr(DBDictionary, "_instance"):
                    DBDictionary._instance = object.__new__(cls)
        return DBDictionary._instance
    
    def __init__(self, source="", version='2.0.0', d_dict={}, conn_num=30
                 ) -> None:
        self.db_list: list[MySQLDatabase] = []
        self.available_list = []
        self.ok = True
        for i in range(conn_num):
            self.db_list.append(MySQLDatabase(host=DB_CONFIG['HOST'],
                                              port=DB_CONFIG['PORT'],
                                              user=DB_CONFIG['USER'],
                                              password=DB_CONFIG['PASSWORD'],
                                              database=DB_CONFIG['DATABASE']))
            self.available_list.append(True)
            if not self.db_list[i].ok:
                self.ok = False
        self.source = source
        self.version = version
        if not self.ok:
            logger.info("初始化数据库字典出错")
            return

        self.lock = threading.Lock()
        # self.dictionary = d_dict
        self.dictionary = {}
        self.lower_dictionary = {}
        self.proofread_set = set()

    def __del__(self):
        self.close()

    def close(self):
        for db in self.db_list:
            db.close()

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
    
    def get(self, k: str, rel_f="", load_from_sql=False, ignore_case=False, tag="", correct_tag_from_db=False):
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
        start_time = time.time()
        v_bean = None # 翻译结果   
        redis_bean = self.__get_redis(k, tag, ignore_case, correct_tag_from_db)
        if redis_bean != None:
            v_bean = redis_bean
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
                            self.__put_source_by_word_id(v_bean['sql_id'], rel_f)
                            logger.info(f"插入source表成功，word_id: {v_bean['sql_id']}, en: {k}, cn: {v_bean['cn']}")
                    else:
                        # 尝试插入source表
                        self.__put_source_by_word_id(v_bean['sql_id'], rel_f)
                        logger.info(f"插入source表成功，word_id: {v_bean['sql_id']}, en: {k}, cn: {v_bean['cn']}")
 
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
    
    def get_bunch(self, keys: list, tags: list, rel_f:str, ignore_case: bool = False, correct_tag_from_db: bool = False) -> (dict):
        """批量获取翻译

        Args:
            keys (list): 待翻译的英文列表
            tag (str, optional): 标签. Defaults to "".
            ignore_case (bool, optional): 是否忽略大小写. Defaults to False.
            correct_tag_from_db (bool, optional): 是否从数据库中获取标签. Defaults to False.

        Returns:
            dict: 翻译结果，key为英文，value为翻译结果
        """
        res_beans = []
        query_keys = []
        query_beans = []
        for k, t in zip(keys, tags):
            redis_bean = self.__get_redis(k, t, ignore_case, correct_tag_from_db)
            if redis_bean != None:
                res_beans.append(redis_bean)
            else:
                query_keys.append(k)
        if len(query_keys) == 0:
            return res_beans
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        placeholders = ','.join(['%s'] * len(query_keys))
        query_sql = f"select id, en, cn, json_file, proofread, category, modified_at, is_key from words where en in ({placeholders}) order by version desc"
        params = tuple(query_keys)
        logger.debug(f"从数据库中查询keys: {query_sql}")
        res = db.execute_query(query_sql, params)
        logger.debug(f"从数据库中查询到结果，keys数量: {len(query_keys)}, res数量: {len(res)}")
        for query_k in query_keys:
            res_k = [r for r in res if r['en'] == query_k]
            if len(res_k) == 0:
                logger.error(f"从数据库中不包含此项，en: {query_k}, tag: {t}")
                continue
            v_bean = self.__get_best_match(res_k, query_k, t)
            if v_bean == None:
                continue
            res_beans.append(v_bean)
            query_beans.append(v_bean)
            self.__put_redis(query_k, v_bean['cn'], v_bean['category'], v_bean['proofread'], v_bean['is_key'], v_bean['sql_id'], v_bean['modified_at'])                
        if rel_f != "":
            insert_values = [f"(\"{rel_f}\", {v_bean['sql_id']}, \"{self.version}\")" for v_bean in query_beans]
            if len(insert_values) != 0:
                insert_sql = f"insert into source (file, word_id, version) values {','.join(insert_values)} ON DUPLICATE KEY UPDATE version = VALUES(version);"
                logger.debug(f"插入source表，sql: {insert_sql}")
                ok = db.execute_non_query(insert_sql)
                if not ok:
                    logger.error(f"插入source表失败，values: {insert_values}")
        self.__release_db(db_index)
        return res_beans

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
            self.db.execute_non_query(
                "insert into source (file, word_id, version) values (%s, select id from words where en = %s and cn = %s, %s);", (rel_f, key, value, self.version))
            self.lock.release()
            return True
        for r in res:
            self.db.execute_non_query(
                "insert into source (file, word_id, version) values (%s, %s, %s);", (rel_f, r['id'], self.version))
        self.lock.release()
        return True

    def __put_source_by_word_id(self, word_id: int, rel_f: str):
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        db.execute_non_query(
            "insert into source (file, word_id, version) values (%s, %s, %s) ON DUPLICATE KEY UPDATE version = VALUES(version);", (rel_f, word_id, self.version))
        self.__release_db(db_index)
        # logger.info(f"插入source表成功，file: {rel_f}, word_id: {word_id}, version: {self.version}")
        
    def put(self, key: str, value, rel_f: str, insert_word=True, proofread=False, tag="") -> (bool):
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
        # 更新source表
        if tag != "":
            db.execute_non_query("""insert into source (file, word_id, version) 
                                    SELECT %s, id, %s FROM words 
                                    WHERE BINARY en = %s AND cn = %s AND category = %s
                                    ON DUPLICATE KEY UPDATE version = VALUES(version);""", (rel_f, self.version, key, value, tag))
        else:
            db.execute_non_query("""insert into source (file, word_id, version) 
                        SELECT %s, id, %s FROM words 
                        WHERE BINARY en = %s AND cn = %s AND category is null
                        ON DUPLICATE KEY UPDATE version = VALUES(version);""", (rel_f, self.version, key, value))
        self.__release_db(db_index)
        logger.debug(f"put函数执行时间：{time.time() - start_time} 秒")
        return True

    def put_term(self, term):
        """写入Term表

        Args:
            term (Term): _description_
        """
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        db.insert('term', {'en': term.en, 'cn': term.cn, 'category': term.category})
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
            res.add(Term(r['en'], r['category'], r['cn']))
        for r in words_res:
            res.update(to_terms(r['en'], r['cn'], r['category']))
        self.__release_db(db_index)
        return res
        
    def update(self, sql_id:int, cn:str, proofread=True, tag="") -> (bool):
        start_time = time.time()
        db_index = self.__get_db_index()
        db = self.db_list[db_index]
        if proofread:
            p = 1
        else:
            p = 0
            
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
            db.update('words', { 'cn': cn, 'source': self.source, 'proofread': p,
                        'version': self.version, 'modified_by': 1 ,'modified_at': datetime.datetime.now()}, {'id': r['id']})
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
        db.execute_non_query("""insert into source (file, word_id, version) 
                            SELECT %s, id, %s FROM words 
                            WHERE BINARY en = %s AND cn = %s 
                            ON DUPLICATE KEY UPDATE version = VALUES(version);""", (rel_f, self.version, key, value))
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
                logger.warning(f"重复插入{en}->{cn}")
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
            sql = f"select id, cn, en, version, proofread, category, modified_at, is_key from words where id in (select word_id from source where file in ({placeholders}))"
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
        self.lock.acquire()
        ok = self.db_list[0].execute_non_query(
            "INSERT INTO file (file, source_file, total, translate, proofread, en_json) VALUES (%s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE total = VALUES(total), translate = VALUES(translate), proofread = VALUES(proofread), en_json = VALUES(en_json)",
            (file_path, source_file, total, translate, proofread, en_json))
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
