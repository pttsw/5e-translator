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
            for index, v in enumerate(en_list):
                if isinstance(v, dict):
                    new_dict = {}
                    for k, value in v.items():
                        split_k = k.split("|")
                        db_bean = self.dictionary.get(split_k[0],
                                                        load_from_sql=False,
                                                        tag="feat",
                                                        ignore_case=True)
                        if not db_bean:
                            db_bean = self.dictionary.get(split_k[0],
                                                                    load_from_sql=True,
                                                                    tag="feat",
                                                                    ignore_case=True)
                            if db_bean == None:
                                # 这里之所以不去调用KIMI接口是避免代码逻辑过于复杂，更新完数据如果出现了新的法术，可能会导致第一次翻译时无法找到中文，再执行一次脚本即可解决
                                cn_feat_name = split_k[0]  # 用英文原文先糊弄过去，同时警告
                                logger.error(f"无法找到专长名：{cn_feat_name}的翻译")
                            else:
                                cn_feat_name = db_bean['cn']    
                                self.dictionary.putSource(
                                    key=split_k[0], value=cn_feat_name, rel_f=self.rel_path)
                        else:
                            cn_feat_name = db_bean['cn']
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
