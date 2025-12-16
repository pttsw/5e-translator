
import os
from config import PARA_OUTPUT_DIR
from app.core.utils.parser import get_source_json_to_full

class ParaFile:
    def __init__(self, rel_path, paraStrings = []):
        self.rel_path = self.__replace_source_to_cn(rel_path)
        self.file_path = os.path.join(PARA_OUTPUT_DIR, self.rel_path)
        self.paraStrings = paraStrings
        
    def __replace_source_to_cn(self, rel_path):
        source = rel_path.split("/")[0]
        print(source)
        source_cn = get_source_json_to_full(source)
        print(source_cn)
        return source_cn + rel_path[len(source):]
        
class ParaStrings:
    def __init__(self, key, original, translation, file="", stage=1, context=""):
        self.key = key
        self.original = original
        self.translation = translation
        self.file = file
        self.stage = stage
        self.context = context

    def __dict__(self):
        return {
            "key": self.key,
            "original": self.original,
            "translation": self.translation,
            "file": self.file,
            "stage": self.stage,
            "context": self.context,
        }
    def __upload_file_dict__(self):
        return {
            "key": self.key,
            "original": self.original,
            "translation": self.translation,
            "context": self.context,
        }