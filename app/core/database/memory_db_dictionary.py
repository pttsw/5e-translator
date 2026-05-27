import json
import os
import threading
from typing import Dict, List, Optional

from config import logger


class MemoryDBDictionary:
    _instance_lock = threading.Lock()
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, source="", version="2.0.0", d_dict=None, conn_num=30) -> None:
        if getattr(self, "_initialized", False):
            if source:
                self.source = source
            if version:
                self.version = version
            return
        self._initialized = True
        self.ok = True
        self.source = source
        self.version = version
        self.lock = threading.Lock()

        self.dictionary: Dict[str, List[dict]] = {}
        self.lower_dictionary: Dict[str, List[dict]] = {}
        self.proofread_set = set()

        self.store_records: List[dict] = []
        self.store_by_id: Dict[int, dict] = {}
        self.source_index: Dict[str, set] = {}
        self.file_table: Dict[str, dict] = {}
        self.locked_entries: Dict[str, dict] = {}
        self.credits_table: Dict[str, List[dict]] = {}
        self.next_sql_id = 1

        seed_path = os.getenv("TRANSLATOR_MEMORY_DB_SEED", "").strip()
        if seed_path:
            self._load_seed(seed_path)

    def close(self):
        return

    def clear(self):
        self.dictionary = {}
        self.lower_dictionary = {}
        self.proofread_set = set()

    def _load_seed(self, seed_path: str):
        if not os.path.exists(seed_path):
            logger.warning(f"内存测试库 seed 文件不存在: {seed_path}")
            return
        try:
            with open(seed_path, "r") as fh:
                payload = json.load(fh)
        except Exception as exc:
            logger.warning(f"读取内存测试库 seed 失败: {exc}")
            return

        for record in payload.get("words", []):
            self._insert_record(
                en=record.get("en", ""),
                cn=record.get("cn", ""),
                rel_f=record.get("json_file", ""),
                proofread=bool(record.get("proofread", False)),
                tag=record.get("category", ""),
                is_key=bool(record.get("is_key", False)),
                sql_id=record.get("sql_id"),
                modified_at=record.get("modified_at", 0),
            )
        self.locked_entries.update(payload.get("locked_entries", {}))
        self.credits_table.update(payload.get("credits", {}))
        self.file_table.update(payload.get("files", {}))

    def _insert_record(self, en: str, cn: str, rel_f: str, proofread=False, tag="", is_key=False, sql_id=None, modified_at=0):
        sql_id = sql_id or self.next_sql_id
        self.next_sql_id = max(self.next_sql_id, sql_id + 1)
        record = {
            "id": sql_id,
            "en": en,
            "cn": cn,
            "json_file": rel_f,
            "proofread": 1 if proofread else 0,
            "category": tag if tag != "" else None,
            "modified_at": modified_at,
            "is_key": is_key,
            "source": self.source,
            "version": self.version,
        }
        self.store_records.append(record)
        self.store_by_id[sql_id] = record
        if rel_f:
            self.source_index.setdefault(rel_f, set()).add(sql_id)
        self._put_cache(record)
        return record

    def _put_cache(self, record: dict):
        bean = {
            "en": record["en"],
            "cn": record["cn"],
            "category": record["category"],
            "proofread": record["proofread"],
            "is_key": record["is_key"],
            "sql_id": record["id"],
            "modified_at": record["modified_at"],
        }
        self.dictionary.setdefault(record["en"], [])
        if not any(c["sql_id"] == bean["sql_id"] for c in self.dictionary[record["en"]]):
            self.dictionary[record["en"]].append(bean)
        lower_en = record["en"].lower()
        self.lower_dictionary.setdefault(lower_en, [])
        if not any(c["sql_id"] == bean["sql_id"] for c in self.lower_dictionary[lower_en]):
            self.lower_dictionary[lower_en].append(bean)
        if bean["proofread"] == 1:
            self.proofread_set.add(record["en"])

    def _to_bean(self, record: dict) -> dict:
        return {
            "en": record["en"],
            "cn": record["cn"],
            "category": record["category"],
            "proofread": record["proofread"],
            "is_key": record["is_key"],
            "sql_id": record["id"],
            "modified_at": record["modified_at"],
        }

    def _pick_best_match(self, records: List[dict], en: str, tag: str):
        if not records:
            return None
        target_tag = tag if tag not in ("", None) else None
        exact = [r for r in records if r["en"] == en and r["category"] == target_tag]
        if exact:
            proofread = [r for r in exact if r["proofread"] == 1]
            return self._to_bean(proofread[0] if proofread else exact[0])
        exact_en = [r for r in records if r["en"] == en]
        if exact_en:
            proofread = [r for r in exact_en if r["proofread"] == 1]
            return self._to_bean(proofread[0] if proofread else exact_en[0])
        proofread = [r for r in records if r["proofread"] == 1]
        return self._to_bean(proofread[0] if proofread else records[0])

    def _query_store(self, en: str, ignore_case=False):
        if ignore_case:
            lower_en = en.lower()
            return [record for record in self.store_records if record["en"].lower() == lower_en]
        return [record for record in self.store_records if record["en"] == en]

    def get(self, k: str, rel_f="", load_from_sql=False, ignore_case=False, tag="", correct_tag_from_db=False):
        target_tag = tag if tag not in ("", None) else None
        if k in self.dictionary:
            cached = [bean for bean in self.dictionary[k] if bean["category"] == target_tag]
            if cached:
                proofread = [bean for bean in cached if bean["proofread"] == 1]
                return proofread[0] if proofread else cached[0]
            if not correct_tag_from_db and self.dictionary[k]:
                proofread = [bean for bean in self.dictionary[k] if bean["proofread"] == 1]
                return proofread[0] if proofread else self.dictionary[k][0]
        if ignore_case and k.lower() in self.lower_dictionary:
            cached = self.lower_dictionary[k.lower()]
            if target_tag is not None:
                tag_matches = [bean for bean in cached if bean["category"] == target_tag]
                if tag_matches:
                    proofread = [bean for bean in tag_matches if bean["proofread"] == 1]
                    return proofread[0] if proofread else tag_matches[0]
                if correct_tag_from_db:
                    return None
            proofread = [bean for bean in cached if bean["proofread"] == 1]
            return proofread[0] if proofread else cached[0]
        if not load_from_sql:
            return None
        records = self._query_store(k, ignore_case=ignore_case)
        bean = self._pick_best_match(records, k, tag)
        if bean and rel_f and bean["sql_id"] in self.store_by_id:
            self.source_index.setdefault(rel_f, set()).add(bean["sql_id"])
        if bean:
            self._put_cache(self.store_by_id[bean["sql_id"]])
        return bean

    def get_bunch(self, keys: list, tags: list, rel_f: str, ignore_case=False, correct_tag_from_db=False):
        res = []
        for key, tag in zip(keys, tags):
            bean = self.get(
                key,
                rel_f=rel_f,
                load_from_sql=True,
                ignore_case=ignore_case,
                tag=tag,
                correct_tag_from_db=correct_tag_from_db,
            )
            if bean is not None:
                res.append(bean)
        return res

    def put(self, key: str, value: str, rel_f: str, proofread=False, tag=""):
        self._insert_record(key, value, rel_f, proofread=proofread, tag=tag)
        return True

    def update(self, sql_id: int, cn: str, proofread: bool = False, tag=""):
        record = self.store_by_id.get(sql_id)
        if record is None:
            return False
        record["cn"] = cn
        if proofread:
            record["proofread"] = 1
        if tag not in ("", None):
            record["category"] = tag
        self._refresh_cache_for_en(record["en"])
        return True

    def _refresh_cache_for_en(self, en: str):
        if en in self.dictionary:
            del self.dictionary[en]
        lower_en = en.lower()
        if lower_en in self.lower_dictionary:
            del self.lower_dictionary[lower_en]
        matching_records = [record for record in self.store_records if record["en"] == en]
        for record in matching_records:
            self._put_cache(record)

    def putSource(self, key: str, value, rel_f: str):
        for record in self.store_records:
            if record["en"] == key and record["cn"] == value:
                self.source_index.setdefault(rel_f, set()).add(record["id"])
        return True

    def dump(self, file_names=None):
        file_names = file_names or []
        self.clear()
        if not file_names:
            records = list(self.store_records)
        else:
            selected_ids = set()
            for file_name in file_names:
                selected_ids.update(self.source_index.get(file_name, set()))
            records = [self.store_by_id[sql_id] for sql_id in selected_ids if sql_id in self.store_by_id]
        for record in records:
            self._put_cache(record)

    def dumpLockedEntries(self, entry_names=None):
        if not entry_names:
            return {}
        return {
            entry_name: self.locked_entries[entry_name]
            for entry_name in entry_names
            if entry_name in self.locked_entries
        }

    def get_credits(self, file_name: str):
        return self.credits_table.get(file_name, [])

    def update_file_table(self, file_path: str, source_file: str, total: int, translate: int, proofread: int, en_json: str = None):
        self.file_table[file_path] = {
            "file": file_path,
            "parent_dir": os.path.dirname(file_path),
            "source_file": source_file,
            "total": total,
            "translate": translate,
            "proofread": proofread,
            "en_json": en_json,
        }
        return True

    def get_file_info(self, file_path: str):
        if not file_path.endswith(".json"):
            file_path += ".json"
        return self.file_table.get(file_path)

    def update_by_hand(self, k: str, v: str, tag=""):
        bean = self.get(k, load_from_sql=True, tag=tag)
        if bean is None:
            self.put(k, v or "", "", proofread=True, tag=tag)
            return
        self.update(bean["sql_id"], v or bean["cn"], proofread=True, tag=tag)
