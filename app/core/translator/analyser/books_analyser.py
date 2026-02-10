import uuid
from config import logger
from typing import List
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from app.core.utils import need_translate_str
class BooksAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)
        self.book_translators: dict = {
            "PHB": "不全书",
            "XPHB": "不全书",
            "DMG": "不全书",
            "XDMG": "不全书",
            "MM": "不全书",
            "XMM": "不全书/AI搬运",
            "TCE": "不全书",
        }
        
        
    def process(self,  en_dict: dict, byhand: bool = False):
        res_dict, self.job_list = super().process(en_dict, byhand)
        
        for book in res_dict["book"]:
            if book["id"] in self.book_translators.keys():
                book["translator"] = self.book_translators[book["id"]]
            else:
                book["translator"] = "机翻"

        return res_dict, self.job_list
