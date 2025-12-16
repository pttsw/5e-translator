import json
import re

class Term:
    """术语
    用于表示dnd中的术语，包括英文、中文、分类等信息
    """
    def __init__(self, en: str, category: str, cn: str):
        self.en = en
        self.category = category
        self.cn = cn
        
    def __eq__(self, value):
        if isinstance(value, Term):
            return self.en == value.en and self.cn == value.cn
        return False
    
    def __hash__(self):
        return hash((self.en, self.cn))
    
    def to_json(self):
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str):
        return cls(**json.loads(json_str))

    def to_serializable(self):
        return {"en": self.en, "category": self.category, "cn": self.cn}
    # def __dict__(self):
    #     return {"en": self.en, "category": self.category, "cn": self.cn}
    

def to_terms(en, cn, category):
    if en == None or cn == None or en.strip() == "" or cn.strip() == "" or en.lower() == cn.lower():
        return []
    
    pattern = r'^[a-zA-Z]\d+[a-zA-Z]?[.:。：]\s?'
    if re.match(pattern, en):
        en = en[re.search(pattern, en).end():].strip()
        if re.match(pattern, cn):
            cn = cn[re.search(pattern, cn).end():].strip()
        else :
            print(f"{en} -> {cn} cn 不匹配模式: {cn}")
    
    if len(en.split(' ')) > 3:
        return []
    if "{@recharge" in en:
        en = en[:en.index('{')].strip()
        cn = cn[:cn.index('{')].strip()
        return [Term(en, category, cn)]
    elif "(Costs" in en:
        # 部分生物技能中包含了 skill_name (Costs x Action)，所以去掉后面的cost部分作为术语
        en = en[:en.index('(')].strip()
        if "(" in cn:
            cn = cn[:cn.index('(')].strip()
        elif "（" in cn:
            cn = cn[:cn.index('（')].strip()
        return [Term(en, category, cn)]
    elif "·" in cn:
        # 处理人名，如果包含·，则将其拆分成多个Term
        ens = en.split(' ')
        cns = cn.split('·')
        if len(ens) == len(cns):
            return [Term(ens[i].strip(), category, cns[i].strip()) for i in range(len(ens))]
        return []
    elif "{@" in en or "{=amount1/v}" in en:
        # 包含链接的不要，包含食谱数量的不要
        return []
    else:
        return [Term(en, category, cn)]
    return []