import json
import uuid
import os
import time
from typing import List, Tuple
from config import  EN_PATH, PLU_EN_PATH, SKIP_FILES, SKIP_DIRS, logger, SPLITED_5ETOOLS_EN_PATH, HOMEBREW_EN_PATH, UA_EN_PATH, PLU_SCENES_PATH, PLU_DBD_PATH, ADD_TRANSLATOR_KEY
from app.core.utils import read_file, get_rel_path, FileWorkInfo, Job, get_file_name_from_obj
from app.core.database import DBDictionary
from langchain_core.runnables import Runnable
from .base_analyser import BaseAnalyser
from .spell_source_analyser import SpellSourceAnalyser
from .foundry_items_analyser import FoundryItemsAnalyser
from .books_analyser import BooksAnalyser
from .adventures_analyser import AdventuresAnalyser
from .backgrounds_analyser import BackgroundsAnalyser
from .items_analyser import ItemsAnalyser


class JsonAnalyser(Runnable):
    def __init__(self) -> (None):
        self.job_list: List[Job] = []  # Job列表，存放所有Job
        self.name_list: List[str] = []  # 存放所有name的值（目前仅用于更新数据库的is_key字段）
        self.ok = self.__init_dictionary()  # 初始化字典
        if not self.ok:
            return
        self.knowledge = None
        self.byhand = False
        self.mode = '5et' # 是否是处理拆分后的数据
        self.en_obj = None # 英文json对象
        self.entries = None # 存放所有的entry
        self.force_title = False

    def __init_dictionary(self):
        """
        初始化字典
        """
        self.dictionary = DBDictionary()
        return self.dictionary.ok

    def invoke(self, input, config=None, **kwargs):
        inputs = [input] if isinstance(input, str) else input
        self.mode = config['metadata'].get('mode', '5et')
        self.force_title = config['metadata'].get('force_title', False)

        # print(config)
        for j in inputs:
            self.en_obj = None
            if self.mode == '5et':
                # 判断j是否在EN_PATH下的文件
                if not j.startswith(EN_PATH):
                    logger.error(f"JsonAnalyser: 文件{j}不在{EN_PATH}目录下，跳过处理")
                    continue
            elif self.mode == 'splited':
                # 判断j是否在SPLITED_5ETOOLS_DATA_DIR下的文件
                if not j.startswith(SPLITED_5ETOOLS_EN_PATH):
                    logger.error(f"JsonAnalyser: 文件{j}不在{SPLITED_5ETOOLS_EN_PATH}目录下，跳过处理")
                    continue
            elif self.mode == 'homebrew':
                if not j.startswith(HOMEBREW_EN_PATH):
                    logger.error(f"JsonAnalyser: 文件{j}不在{HOMEBREW_EN_PATH}目录下，跳过处理")
                    continue
            elif self.mode == 'ua':
                if not j.startswith(UA_EN_PATH):
                    logger.error(f"JsonAnalyser: 文件{j}不在{UA_EN_PATH}目录下，跳过处理")
                    continue
            elif self.mode == 'plu':
                if not j.startswith(PLU_EN_PATH):
                    logger.error(f"JsonAnalyser: 文件{j}不在{PLU_EN_PATH}目录下，跳过处理")
                    continue
            elif self.mode == 'scenes':
                if not j.startswith(PLU_SCENES_PATH):
                    logger.error(f"JsonAnalyser: 文件{j}不在{PLU_SCENES_PATH}目录下，跳过处理")
                    continue
                
            logger.info(f"开始解析 {j} 中的Json")
            job_list, obj, ok = self.json_2_job(j)
            if not ok:
                logger.error(f"JsonAnalyser: 分析 {j} 时出错")
                continue
            if job_list is None or obj is None:
                logger.warning(f"JsonAnalyser: 文件 {j} 中的Json为空，跳过处理")
                continue
            if self.mode == '5et':
                for k in obj.keys():
                    if k in ADD_TRANSLATOR_KEY:
                        for s in obj[k]:
                            if not isinstance(s, dict): continue
                            obj_file_name=get_file_name_from_obj(s, k)
                            if obj_file_name == '': continue
                            info = self.dictionary.get_file_info(obj_file_name)
                            if info is None: continue
                            if info['translate'] != info['total']: continue
                            
                            s['translator'] = '不全书'
            #     # 设置翻译tag
            #     for k in obj.keys():
            #         if k == 'spell':
            #             for s in obj[k]:
            #                 s['translator'] = '不全书'

            #     yield FileWorkInfo(job_list, obj, self.rel_path, self.rel_path)
            if self.mode == 'splited':
                self.__update_file_table(self.en_obj, get_rel_path(j, SPLITED_5ETOOLS_EN_PATH), self.rel_path, job_list)
                yield FileWorkInfo(job_list, obj, self.rel_path, get_rel_path(j, SPLITED_5ETOOLS_EN_PATH))
            elif self.mode == 'plu':
                yield FileWorkInfo(job_list, obj, self.rel_path, self.rel_path.replace('plu/', ''))
            else:
                yield FileWorkInfo(job_list, obj, self.rel_path, self.rel_path)

    def __update_file_table(self, en_obj:dict, file_path: str, source_file: str, job_list: List[Job]):
        """
        更新文件表

        :param file_path: 文件路径
        :return: None
        """
        sql_job_list = [j for j in job_list if j.sql_id is not None]
        total = len(sql_job_list)
        proofread = sum([1 for j in sql_job_list if j.is_proofread])
        translate = sum([1 for j in sql_job_list if j.is_proofread or j.is_key])
        # 获取en_obj除了_meta以外的key
        en_obj_key = [k for k in self.en_obj.keys() if k != '_meta']
        if len(en_obj_key) != 1:
            logger.error(f"JsonAnalyser: 文件 {file_path} 中的Json格式错误，跳过处理")
            return
        pure_en = json.dumps(self.en_obj[en_obj_key[0]])
        
        ok = self.dictionary.update_file_table(file_path, source_file, total, translate, proofread, pure_en)
        if not ok:
            logger.error(f"JsonAnalyser: 更新文件表 {file_path} 时出错")
            return
        else:
            logger.info(f"JsonAnalyser: 更新文件表 {file_path} 成功, total={total}, translate={translate}, proofread={proofread}")
    def txt_2_json(self, json_txt):
        """
        json转txt

        :param json_txt
        :return:object, bool Json对象，是否成功
        """
        return json.loads(json_txt), True

    def json_2_job(self, json_file: str) -> (Tuple[List[Job], object, bool]):
        """
        json_2_job: 分析JSON文件组中需要翻译的任务

        :param json_file: 传入英文Json文件的路径
        :return: jobList, object, bool: 工作列表、替换work_id后的原文件内容、 是否成功
        """
        self.job_list = []

        en_json_obj = None
        # 获取相对路径，这个路径会根据是否是PLU的源数据来做不同的处理
        if self.mode == 'plu':
            self.rel_path = get_rel_path(json_file, PLU_EN_PATH)
        elif self.mode == 'dbd':
            self.rel_path = os.path.join("dbd", get_rel_path(json_file, PLU_DBD_PATH))
        elif self.mode == 'scenes':
            self.rel_path = get_rel_path(json_file, PLU_SCENES_PATH)
        elif self.mode == 'splited':
            en_json_obj, ok = self.txt_2_json(read_file(json_file))
            if not ok:
                return None, None, False
            if not isinstance(en_json_obj, dict):
                return None, None, True
            if "_meta" not in en_json_obj or "origin_file" not in en_json_obj["_meta"]:
                return None, None, True
            self.rel_path = en_json_obj["_meta"]["origin_file"]
        elif self.mode == 'homebrew':
            self.rel_path = get_rel_path(json_file, HOMEBREW_EN_PATH)
        elif self.mode == 'ua':
            self.rel_path = get_rel_path(json_file, UA_EN_PATH)
        else:
            self.rel_path = get_rel_path(json_file)

        # 清空字典缓存，防止上一个文件的字典污染这次查询
        self.dictionary.clear()
        # 从数据库导出当前文件的相关翻译条目，为后续直接匹配正确的翻译做准备
        if self.mode == 'plu':
            self.dictionary.dump([self.rel_path, os.path.join('plu/', self.rel_path)])
        else:
            self.dictionary.dump([self.rel_path])
        # 清空name_list，防止上一个文件的name污染这次查询
        self.name_list = []
        # 跳过文件夹
        if any(skip_dir in self.rel_path for skip_dir in SKIP_DIRS):
            return None, None, False
        # 跳过文件
        if self.rel_path in SKIP_FILES:
            return None, None, False
        if en_json_obj is None:
            # 读取json文件
            en_json_obj, ok = self.txt_2_json(read_file(json_file))
            if not ok:
                return None, None, False
        self.en_obj = en_json_obj

        obj = {}  # 替换了Job uuid 标识符的json对象
        if self.rel_path in ["spells/sources.json", "icon-class.json", "icon-spell.json", "icon-feat.json", "icon-subclass.json", "bestiary/foundry-integration-token-subjects.json"]:
            # 针对法术source文件进行特殊处理
            obj, self.job_list = SpellSourceAnalyser(
                self.dictionary, self.rel_path).process(en_json_obj)
        elif self.rel_path == "foundry-items.json" \
            or self.rel_path == "foundry-optionalfeatures.json" \
            or self.rel_path == "class/foundry.json"\
            or self.rel_path == "spells/foundry.json":
            obj, self.job_list = FoundryItemsAnalyser(
                self.dictionary, self.rel_path).process(en_json_obj)
        elif self.rel_path == "books.json" and self.mode == '5et':
            obj, self.job_list = BooksAnalyser(
                self.dictionary, self.rel_path).process(en_json_obj)
        elif self.rel_path == "adventures.json" and self.mode == '5et':
            obj, self.job_list = AdventuresAnalyser(
                self.dictionary, self.rel_path).process(en_json_obj)
        elif self.rel_path == "backgrounds.json":
            obj, self.job_list = BackgroundsAnalyser(
                self.dictionary, self.rel_path).process(en_json_obj)
        elif self.rel_path == "items.json":
            obj, self.job_list = ItemsAnalyser(
                self.dictionary, self.rel_path).process(en_json_obj)
        else:  # 正常处理文本逻辑
            if self.mode == 'plu':
                self.rel_path = os.path.join('plu/', self.rel_path)
            # 只处理dict格式的文件
            analyser = BaseAnalyser(self.dictionary, self.rel_path, self.force_title)
            if self.mode == 'homebrew':
                en_name = json_file[json_file.rfind(';')+1:json_file.rfind('.json')].strip()
                analyser.set_job('#HOME_BREW', en_name, None)
            elif self.mode == 'ua':
                en_category = json_file[json_file.rfind('/')+1:json_file.rfind('-')].strip()
                en_name = json_file[json_file.rfind('-')+1:json_file.rfind('.json')].strip()
                analyser.set_job('#UA_CATEGORY', en_category, None)
                analyser.set_job('#UA', en_name, None)
            obj, self.job_list = analyser.process(en_json_obj, self.byhand)
        
        if self.mode == 'ua' or self.mode == 'homebrew':
            # 设置为当前时间戳
            obj['_meta']['dateLastModified'] = int(time.time())

        return self.job_list, obj, True


if __name__ == "__main__":
    json_analyser = JsonAnalyser(has_knowledge=False)
    f = "spells/spells-tce.json"
    # jf = [EN_PATH+f, CN_PATH+f]
    # jf = PLU_EN_PATH+f
    # json_analyser.json_2_job(jf,True)
    jf = os.path.join(EN_PATH, f)
    json_analyser.json_2_job(jf, False)
