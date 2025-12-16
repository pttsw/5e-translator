import uuid
from config import logger
from typing import List
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from app.core.utils import parse_foundry_items_uuid_format, need_translate_str
class BooksAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)
        self.book_translators: dict = {
            "PHB": "不全书/Kiwee",
            "XPHB": "不全书/Kiwee",
            "DMG": "不全书/Kiwee",
            "XDMG": "不全书/Kiwee",
            "MM": "不全书/山德鲁",
            "XMM": "不全书/AI搬运",
            "TCE": "不全书/Kiwee",
        }
        
        
    def process(self,  en_dict: dict, byhand: bool = False):
        res_dict, self.job_list = super().process(en_dict, byhand)
        
        for book in res_dict["book"]:
            if book["id"] in self.book_translators.keys():
                book["translator"] = self.book_translators[book["id"]]
            else:
                book["translator"] = "机翻"

        return res_dict, self.job_list
