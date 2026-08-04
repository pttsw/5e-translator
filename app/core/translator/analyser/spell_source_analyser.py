from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from config import logger

class SpellSourceAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)
        self.byhand = False
        
    def process(self, en_dict: dict, byhand: bool = False):
        self.byhand = byhand
        obj = {}
        for k, v in en_dict.items():  # 第一层是“来源”名，无需翻译
            spell_names = list(v.keys())
            spell_translations = {}
            if spell_names:
                batch_res = self.dictionary.get_bunch(
                    spell_names,
                    ["spell"] * len(spell_names),
                    self.usage_rel_path,
                    ignore_case=True,
                    correct_tag_from_db=self.correct_tag_from_db,
                )
                for spell_name in spell_names:
                    db_bean = batch_res.get((spell_name, "spell"))
                    if db_bean is not None:
                        spell_translations[spell_name] = db_bean["cn"]
            spell_in_source_dict = {}
            cn_spell_name = k
            for spell_name, spell_source in v.items():  # 第二层的key是法术名，需要翻译
                cn_spell_name = spell_translations.get(spell_name)
                if cn_spell_name is None:
                    # 这里之所以不去调用KIMI接口是避免代码逻辑过于复杂，更新完数据如果出现了新的法术，可能会导致第一次翻译时无法找到中文，再执行一次脚本即可解决
                    cn_spell_name = spell_name  # 用英文原文先糊弄过去，同时警告
                    logger.error(f"无法找到法术名：{spell_name}的翻译")

                spell_in_source_dict[cn_spell_name], ok = self.process_base_item(
                    spell_source, '')
                if not ok:
                    logger.error(f"{self.rel_path}解析出错！")
                    return None, False
            obj[k] = spell_in_source_dict
        return obj, self.job_list
