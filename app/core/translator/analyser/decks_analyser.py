from config import logger
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary


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
            translated_keys = self._translate_card_uid_keys(value.keys(), source_hint)
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

    def _translate_card_uid_keys(self, keys, source_hint: str = "") -> dict:
        names_to_translate = []
        seen_names = set()
        split_key_map = {}
        for key in keys:
            split_key = key.split("|")
            split_key_map[key] = split_key
            if len(split_key) < 2:
                continue
            for index, name in enumerate(split_key):
                if index == 2:
                    continue
                if name in seen_names:
                    continue
                seen_names.add(name)
                names_to_translate.append(name)

        translations = {}
        if names_to_translate:
            batch_res = self.dictionary.get_bunch(
                names_to_translate,
                ["card"] * len(names_to_translate),
                self.usage_rel_path,
                ignore_case=True,
                correct_tag_from_db=self.correct_tag_from_db,
            )
            for name in names_to_translate:
                db_bean = batch_res.get((name, "card"))
                if db_bean is not None:
                    translations[name] = db_bean["cn"]

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

            cn_part = translations.get(part)
            if cn_part is None:
                cn_part = part
                logger.error(f"无法找到卡牌名：{part}的翻译")
            translated_parts.append(cn_part)

        return "|".join(translated_parts)
