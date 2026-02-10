import uuid
from config import logger
from typing import List
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from app.core.utils import parse_foundry_items_uuid_format, need_translate_str
class AdventuresAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)
        self.book_translators: dict = {
            "LMoP": "庞瓦西等",
            "IDRotF": "亮君等/AI搬运",
            "DIP": "亮君等/AI搬运",
            "COS": "愈伤之叶等/AI搬运",
            "KKW": "神秘的万智牌手六人组",
            "UtHftLH": "东方等"
        }
        
        
    def process(self,  en_dict: dict, byhand: bool = False):
        res_dict, self.job_list = super().process(en_dict, byhand)
        
        for book in res_dict["adventure"]:
            if book["id"] in self.book_translators.keys():
                book["translator"] = self.book_translators[book["id"]]
            else:
                book["translator"] = "机翻"

        return res_dict, self.job_list
