import json
import requests
from app.core.database import DBDictionary
from config import PARA_API, PROJECT_ID, PARA_TOKEN
def set_terms_to_para():
    mysql_db = DBDictionary()
    mysql_terms = mysql_db.get_all_term()
    print(len(mysql_terms))
    para_term_list = []
    for term in mysql_terms:
        para_term_list.append({
            "term": term.en,
            "translation": term.cn,
            "note": term.category,
        })
    temp_file = "temp_para_terms.json"
    with open(temp_file, 'w') as f:
        # 将para_term_list转为json字符串
        json_str = json.dumps(para_term_list, ensure_ascii=False, indent=4)
        f.write(json_str)
    headers = {
        "Authorization": f"Bearer {PARA_TOKEN}",
    }
    response = requests.put(
        f"{PARA_API}/{PROJECT_ID}/terms",
        headers=headers,
        files={"file": (temp_file, open(temp_file, "rb"))}  # 文件字段放在files中
    )