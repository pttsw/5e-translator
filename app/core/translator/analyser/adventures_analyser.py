import uuid
from config import logger
from typing import List
from .base_analyser import BaseAnalyser
from app.core.database import DBDictionary
from app.core.utils import parse_foundry_items_uuid_format, need_translate_str
class AdventuresAnalyser(BaseAnalyser):
    def __init__(self, dictionary: DBDictionary, rel_path: str) -> (None):
        super().__init__(dictionary, rel_path)

    def process(self,  en_dict: dict, byhand: bool = False):
        res_dict, self.job_list = super().process(en_dict, byhand)

        translator = self.get_translator_from_credits()
        for book in res_dict["adventure"]:
            book["translator"] = translator

        return res_dict, self.job_list
