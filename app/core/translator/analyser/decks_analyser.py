from config import logger
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from app.core.utils import Job


class DecksAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)

    def process_base_item(self, en_item, key_path: str, current_names: list = [], tag=""):
        if isinstance(en_item, dict) and key_path.endswith("/outcomes"):
            res, ok = self._process_outcomes(en_item, key_path, current_names)
            if not ok:
                logger.error(f"{self.rel_path}解析outcomes时出错")
            return res, ok
        return super().process_base_item(en_item, key_path, current_names, tag)

    def _process_outcomes(self, outcomes: dict, key_path: str, current_names: list):
        return self._process_outcomes_level(outcomes, key_path, current_names)

    def _process_outcomes_level(self, value, key_path: str, current_names: list, source_hint: str = ""):
        if not isinstance(value, dict):
            return self.process_base_item(value, key_path, current_names=current_names)

        res_dict = {}
        has_nested_dict = any(isinstance(v, dict) for v in value.values())
        translated_keys = {}
        if not has_nested_dict:
            translated_keys = self._translate_card_uid_keys(
                value.keys(), source_hint, key_path,
            )
        for k, v in value.items():
            next_source_hint = k if has_nested_dict and self._looks_like_source_bucket(v) else source_hint
            if isinstance(v, dict):
                processed_value, ok = self._process_outcomes_level(
                    v,
                    key_path + "/" + k,
                    current_names=current_names,
                    source_hint=next_source_hint,
                )
                res_key = k
            else:
                res_key = translated_keys.get(k, k)
                processed_value, ok = v, True

            if not ok:
                return None, False
            res_dict[res_key] = processed_value
        return res_dict, True

    @staticmethod
    def _looks_like_source_bucket(value) -> bool:
        if not isinstance(value, dict) or not value:
            return False
        return all(not isinstance(v, dict) for v in value.values())

    def _translate_card_uid_keys(
        self, keys, source_hint: str = "", key_path: str = "",
    ) -> dict:
        requests = []
        split_key_map = {}
        for key_index, key in enumerate(keys):
            split_key = key.split("|")
            split_key_map[key] = split_key
            if len(split_key) < 2:
                continue
            for index, name in enumerate(split_key):
                if index == 2:
                    continue
                part_path = f"{key_path}/@key[{key_index}]/part[{index}]"
                uid = self._job_uid_from_key_path(part_path, name)
                requests.append((key, index, Job(
                    uid, name, None, rel_path=self.rel_path, tag="card",
                    key_path=part_path, entry_path=key_path,
                )))

        translations = {}
        if requests:
            batch_res = self.dictionary.get_bunch(
                [job.en_str for _, _, job in requests],
                ["card"] * len(requests),
                self.usage_rel_path,
                ignore_case=True,
                correct_tag_from_db=self.correct_tag_from_db,
                job_contexts=[job for _, _, job in requests],
            )
            for original_key, index, job in requests:
                db_bean = batch_res.get((job.en_str, "card", job.uid))
                if db_bean is None:
                    db_bean = batch_res.get((job.en_str, "card"))
                if db_bean is not None:
                    job.cn_str = db_bean["cn"]
                    job.sql_id = db_bean["sql_id"]
                    job.is_proofread = db_bean["proofread"]
                    job.is_key = db_bean["is_key"]
                    job.modified_at = db_bean["modified_at"]
                    job.exact_translation = db_bean.get("exact_translation", False)
                self._add_job(job)
                translations[(original_key, index)] = (
                    f"{{!@ {job.uid}}}" if self.preserve_job_markers
                    else (job.cn_str or job.en_str)
                )

        translated_key_map = {}
        for key, split_key in split_key_map.items():
            translated_key_map[key] = self._translate_card_uid_key(key, split_key, translations, source_hint)
        return translated_key_map

    def _translate_card_uid_key(self, key: str, split_key: list, translations: dict, source_hint: str = "") -> str:
        if len(split_key) < 2:
            return key

        translated_parts = []
        for index, part in enumerate(split_key):
            if index == 2:
                translated_parts.append(part)
                continue

            cn_part = translations.get((key, index))
            if cn_part is None:
                cn_part = part
                logger.error(f"无法找到卡牌名：{part}的翻译")
            translated_parts.append(cn_part)

        return "|".join(translated_parts)
