
# file_loader
# output: files
# 
# json_job_gen
# jobs
# kimi_pool
# res

import os
import json
import re
import subprocess
from datetime import date, datetime
from config import *
from langchain_core.runnables import RunnableLambda
from .file_work_info import FileWorkInfo
from typing import List


def _find_json_files(input_value):
    if isinstance(input_value, (tuple, list)):
        root_folder, skip_file_name = input_value
    else:
        root_folder, skip_file_name = input_value, ''
    return find_files(root_folder, skip_file_name=skip_file_name)


find_json_files = RunnableLambda(_find_json_files)
write_translate_cache = RunnableLambda(lambda file_work_info: write_translate_cache_func(file_work_info))


def find_files(root_folder: str, file_extension='.json', skip_file_name=''):
    # 判断root_folder是文件还是文件夹
    if os.path.isfile(root_folder):
        file_name = os.path.basename(root_folder)
        if skip_file_name and skip_file_name in file_name:
            return
        # 如果是文件则直接返回
        if root_folder.endswith(file_extension):
            yield root_folder
    else:
        for root, _, files in os.walk(root_folder):
            for file in files:
                if file.endswith(file_extension) and not (
                    skip_file_name and skip_file_name in file
                ):
                    yield os.path.join(root, file)

def read_file(json_file):
    with open(json_file, 'r') as file:
        content = file.read()
        return content


def get_same_files(en_root_folder=EN_PATH, cn_root_folder='') :
    en_json_files = find_json_files(en_root_folder)
    res = []
    for en_json in en_json_files:
        rel_file_path = get_rel_path(en_json, en_root_folder)
        cn_json = os.path.join(cn_root_folder, rel_file_path)
        if not os.path.exists(cn_json):
            cn_json = ""
        res.append([en_json, cn_json])
    return res

def get_rel_path(en_file, en_root_folder=EN_PATH):
    return os.path.relpath(en_file, en_root_folder)

def write_translate_cache_func(file_work_infos:List[FileWorkInfo], out_path:str=OUT_PATH):
    """将翻译的中间过程obj及job_list写入缓存文件

    Args:
        file_work_infos (List[FileWorkInfo]): _description_

    Yields:
        _type_: _description_
    """
    for file_work_info in file_work_infos:
        yield write_file_work_infos(file_work_info, out_path)


def order_cn_entity_fields(obj):
    if isinstance(obj, list):
        return [order_cn_entity_fields(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    ordered_items = [
        (key, order_cn_entity_fields(value))
        for key, value in obj.items()
    ]
    if not all(key in obj for key in ("ENG_name", "name", "type")):
        return dict(ordered_items)

    ordered_map = dict(ordered_items)
    priority_keys = ("ENG_name", "name", "type")
    return {
        **{key: ordered_map[key] for key in priority_keys},
        **{key: value for key, value in ordered_items if key not in priority_keys},
    }


_CLEAN_JSON_SCRIPT = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "../../../scripts/clean_5etools_json.mjs",
))
_FIVETOOLS_ROOT = os.getenv("FIVETOOLS_ROOT", "/data/5etools-mirror-2.github.io")
_SHARED_REPLACEMENTS = {
    "’": "'",
    "‘": "'",
    "": "'",
    "\u02BC": "'",
    "…": "...",
    "\u200B": "",
    "\u2002": " ",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "Ĳ": "IJ",
    "ĳ": "ij",
    "Ǉ": "LJ",
    "ǈ": "Lj",
    "ǉ": "lj",
    "Ǌ": "NJ",
    "ǋ": "Nj",
    "ǌ": "nj",
    "ﬅ": "ft",
    "“": '"',
    "”": '"',
    "\u201a": ",",
}
_STR_REPLACEMENTS = {
    "—": "\\u2014",
    "–": "\\u2013",
    "‑": "\\u2011",
    "−": "\\u2212",
    " ": "\\u00A0",
    " ": "\\u2007",
}
_SOFT_HYPHEN_REMOVE_REGEX = re.compile(r"\u00AD *\r?\n?\r?")


def _clean_5etools_string(value: str):
    for old, new in _SHARED_REPLACEMENTS.items():
        value = value.replace(old, new)
    return _SOFT_HYPHEN_REMOVE_REGEX.sub("", value)


def _clean_5etools_strings(obj):
    if isinstance(obj, str):
        return _clean_5etools_string(obj)
    if isinstance(obj, list):
        return [_clean_5etools_strings(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _clean_5etools_strings(value) for key, value in obj.items()}
    return obj


def _to_json_safe(obj):
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_to_json_safe(item) for item in obj]
    if isinstance(obj, tuple):
        return [_to_json_safe(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): _to_json_safe(value) for key, value in obj.items()}
    if hasattr(obj, "to_serializable"):
        return _to_json_safe(obj.to_serializable())
    if hasattr(obj, "__dict__"):
        return _to_json_safe(obj.__dict__)
    return str(obj)


def _clean_json_with_python_fallback(obj):
    cleaned = _clean_5etools_strings(obj)
    content = json.dumps(cleaned, ensure_ascii=False, indent="\t") + "\n"
    for old, new in _STR_REPLACEMENTS.items():
        content = content.replace(old, new)
    return content


def clean_5etools_json(obj):
    obj = _to_json_safe(obj)
    if os.path.exists(_CLEAN_JSON_SCRIPT) and os.path.isdir(_FIVETOOLS_ROOT):
        try:
            raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            proc = subprocess.run(
                ["node", _CLEAN_JSON_SCRIPT],
                input=raw,
                text=True,
                capture_output=True,
                check=True,
                env={**os.environ, "FIVETOOLS_ROOT": _FIVETOOLS_ROOT},
            )
            return proc.stdout
        except (OSError, subprocess.CalledProcessError):
            pass
    return _clean_json_with_python_fallback(obj)
        
def write_file_work_infos(file_work_info:FileWorkInfo, out_path:str=OUT_PATH):
    out_file_path = os.path.join(out_path, file_work_info.out_path)  # 输出目录
    rel_out_dir = os.path.dirname(out_file_path)  # 输出文件的文件夹

    # 若输出文件夹不存在则创建
    if not os.path.exists(rel_out_dir):
        os.makedirs(rel_out_dir)

    # 写入替换好Job uuid的Json文件
    with open(out_file_path + '.en', 'w') as file:
        file.write(clean_5etools_json(file_work_info.json_obj))
    if file_work_info.cn_obj is not None:
        file_work_info.cn_obj = order_cn_entity_fields(file_work_info.cn_obj)
        with open(out_file_path, 'w') as file:
            file.write(clean_5etools_json(file_work_info.cn_obj))
    # 写入Job列表文件
    with open(out_file_path+'.jobs', 'w') as file:
        file.write(clean_5etools_json(file_work_info.job_list))
    file_work_info.json_path = out_file_path
    return file_work_info
    
if __name__ == "__main__":
    for f in get_same_files():
        print(f)
    print(len(get_same_files()))
    # print(find_json_files(EN_PATH))
