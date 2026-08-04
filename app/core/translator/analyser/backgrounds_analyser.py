import uuid
from config import logger
from typing import List
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from app.core.utils import parse_foundry_items_uuid_format, need_translate_str
class BackgroundsAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)
        
    def process_list(self, en_list, key_path, current_names: list = [], tag: str = ""):
        res_list = []
        if "feats" in key_path:
            feat_names = []
            seen_feat_names = set()
            for v in en_list:
                if not isinstance(v, dict):
                    continue
                for k in v.keys():
                    feat_name = k.split("|")[0]
                    if feat_name in seen_feat_names:
                        continue
                    seen_feat_names.add(feat_name)
                    feat_names.append(feat_name)
            feat_translations = {}
            if feat_names:
                batch_res = self.dictionary.get_bunch(
                    feat_names,
                    ["feat"] * len(feat_names),
                    self.usage_rel_path,
                    ignore_case=True,
                    correct_tag_from_db=self.correct_tag_from_db,
                )
                for feat_name in feat_names:
                    db_bean = batch_res.get((feat_name, "feat"))
                    if db_bean is not None:
                        feat_translations[feat_name] = db_bean["cn"]
            for index, v in enumerate(en_list):
                if isinstance(v, dict):
                    new_dict = {}
                    for k, value in v.items():
                        split_k = k.split("|")
                        cn_feat_name = feat_translations.get(split_k[0])
                        if cn_feat_name is None:
                            # 这里之所以不去调用KIMI接口是避免代码逻辑过于复杂，更新完数据如果出现了新的法术，可能会导致第一次翻译时无法找到中文，再执行一次脚本即可解决
                            cn_feat_name = split_k[0]  # 用英文原文先糊弄过去，同时警告
                            logger.error(f"无法找到专长名：{cn_feat_name}的翻译")
                        split_k[0] = cn_feat_name
                        new_dict["|".join(split_k)] = value
                    res_list.append(new_dict)
                else:
                    list_item_key_path = self._list_item_key_path(key_path, index, v)
                    tmp_list, ok = self.process_base_item(v, list_item_key_path, current_names, tag="feat")
                    if not ok:
                        logger.error(f"{self.rel_path}解析{v}时出错")
                    res_list.append(tmp_list)
            return res_list, True
        
        return super().process_list(en_list, key_path, current_names, tag)
