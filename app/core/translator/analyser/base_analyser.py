import uuid
from config import logger,KEY_MATCHED_TAG
from typing import List, Optional
from app.core.utils import Job, check_skip_key, parse_custom_format, only_has_format, split_string, need_translate_str, check_prefix, check_suffix, get_tag_from_rel_path,get_source_from_rel_path, replace_split_values, process_filter_split_values, process_post_filter_split_values
from app.core.database import DBDictionary
# 添加jsonpath_ng的导入
from jsonpath_ng import parse
from jsonpath_ng.ext import parse as ext_parse


class BaseAnalyser:
    def __init__(self, dictionary: DBDictionary, rel_path: str, force_title: bool = False) -> (None):
        self.name_list: List[str] = []
        self.job_list: List[Job] = []
        self.dictionary = dictionary
        self.rel_path = rel_path
        self.name_tag = get_tag_from_rel_path(self.rel_path)
        self.source = get_source_from_rel_path(self.rel_path)
        self.byhand = False
        self.load_from_sql = True
        self.name_should_proofread = False # 对于Job的Parent是否只添加校对过得
        self.correct_tag_from_db = False # 是否根据标签从数据库中准确抽取？(影响性能)
        self.locked_entries = {} # 已锁定的条目
        self.force_title = force_title
        
    def process(self,  en_obj: dict, byhand: bool = False):
        self.byhand = byhand
        
        self.locked_entries = self.dictionary.dumpLockedEntries([self.rel_path])
        if (isinstance(en_obj, dict)):
            if "_meta" in en_obj.keys() and "sources" in en_obj["_meta"]:
                for index, source in enumerate(en_obj["_meta"]["sources"]):
                    if "full" in source.keys():
                        source["full"] = self.str_2_job(source["full"], current_names=[], tag="adventure", key_path=f"._meta.sources[{index}].full")
            res_dict, ok = self.process_first_level(en_obj, self.source)
            # res_dict, ok = self.process_dict(en_obj, "", tag=self.name_tag)
            if not ok:
                raise RuntimeError(f'process error:{en_obj}')
            
            # 设置作者信息
            if "data" in res_dict.keys() and isinstance(res_dict["data"], list):
                credit_section = res_dict["data"][-1]
                if credit_section["type"] == "section" and "ENG_name" in credit_section.keys() and "credit" in credit_section["ENG_name"].lower():
                    credits = self.dictionary.get_credits(self.rel_path)

                    if credits:
                        current_credits = {							
                            "ENG_name": "5et-cn translator",
                            "name": "5et汉化",
                            "type": "list",
                            "style": "list-hang-notitle",
                            "items":[]
                        }
                        for credit in credits:
                            current_credits["items"].append({
                                "name": credit["job_type"],
                                "type": "item",
                                "entry": credit["names"]
                            })
                        credit_section["entries"].append(current_credits)
        elif (isinstance(en_obj, list)):
            res_dict, ok = self.process_list(en_obj, "", tag=self.name_tag)
            if not ok:
                raise RuntimeError(f'process error:{en_obj}')
        # 最终从数据库里查job
        query_en_list = [j.en_str for j in self.job_list if j.cn_str == None]
        query_tag_list = [j.tag for j in self.job_list if j.cn_str == None]
        query_res_list = self.dictionary.get_bunch(query_en_list, query_tag_list, self.rel_path, ignore_case=True, correct_tag_from_db=self.correct_tag_from_db)
        for j in self.job_list:
            if j.cn_str != None:
                continue
            db_res = self.dictionary.get(j.en_str, rel_f=self.rel_path, load_from_sql=False, ignore_case=True, tag=j.tag, correct_tag_from_db=self.correct_tag_from_db)
            if db_res:
                j.cn_str = db_res['cn']
                j.sql_id = db_res['sql_id']
                j.is_proofread = db_res['proofread']
                j.is_key = db_res['is_key']
        return res_dict, self.job_list

    def str_2_job(self, en_str: str, current_names: list = [], tag = "", key_path = ""):
        """根据字符串生成JOB对象

        Args:
            en_str (str): 原始英文字符串
            current_name (str, optional): 所属元素的名字. Defaults to None.

        Returns:
            job_str: 整理后的有job替换id的字符串
        """
        if self.force_title and "name" not in key_path:
            return en_str
        # "需要考虑句中有{@tag 标识|对的|1}"的情况。job添加["需要考虑句中有{@tag 标识|对的|1}","标识","对的",]
        match_k, match_v, is_valid = parse_custom_format(en_str)
        if len(match_v) == 0:
            # 没找到tag则直接整句按|拆分即可
            return self.__split_and_append_job(en_str, current_names=current_names, tag=tag, key_path=key_path)
        else:
            # 使用jsonpath作为唯一标识
            # 如果有key_path，使用它生成jsonpath表达式
            if key_path:
                # 转换key_path格式为标准jsonpath
                jsonpath_expr = f'$' + key_path.replace('/', '.')
                uid = jsonpath_expr
            else:
                # 如果没有key_path，使用字符串内容生成一个唯一标识
                uid = f'$._dynamic.{hash(en_str)}'
            
            cn_str = None
            if only_has_format(en_str):
                cn_str = en_str
            j = self.get_job(en_str, tag=tag)
            if j is not None:
                uid = j.uid
            else:
                # 将本身添加到Job列表
                self.set_job(uid, en_str, cn_str, current_names=current_names, tag=tag)
            en_str = f'{{!@ {uid}}}'

            # 处理tag的value内容
            index=0
            for m, t in zip(match_v, match_k):
                # 为tag的value内容生成子路径
                sub_key_path = f"{key_path}/{t}/{index}" if key_path else f"/{t}/{index}"
                self.__split_and_append_job(m, t, current_names=current_names, key_path=sub_key_path)
                index+=1
            return en_str

    def set_job(self, uid: str, en: str, cn: Optional[str] = None, tag=None, current_names: List = []):
        """向Job列表中添加Job

        Args:
            uid (str): Job的UUID
            en (str): 英文文本
            cn (str, optional): 中文文本. Defaults to None.
            tag (str, optional): 标签. Defaults to "".
            current_names (list, optional): 当前元素的名称列表. Defaults to [].
        """
        is_proofread = False
        sql_id = None
        is_key = False
        modified_at = 0
        if cn is None:
            # 如果只有{@tag xxx}的文本，则无需翻译，直接原样放置即可
            if only_has_format(en):
                cn = en
            # else:
        #         # 先从内存中读取
        #         cn_bean = self.dictionary.get(
        #             en, load_from_sql=False, ignore_case=True, tag=tag)
        #         if cn_bean != None and self.correct_tag_from_db and tag != "" and cn_bean['category'] != tag:
        #             cn_bean = None
        #         if cn_bean == None and self.load_from_sql:
        #             # 从数据库中读取
        #             cn_bean = self.dictionary.get(
        #                 en, rel_f=self.rel_path, load_from_sql=True, ignore_case=True, tag=tag, correct_tag_from_db=self.correct_tag_from_db)
        #         if cn_bean != None:
        #             cn = cn_bean['cn']
        #             is_proofread = cn_bean['proofread']
        #             is_key = cn_bean['is_key']
        #             sql_id = cn_bean['sql_id']
        #             modified_at = cn_bean['modified_at']
        
        # 手动翻译关键字（name）
        if self.byhand and is_proofread != 1 and ((current_names != [] and en == current_names[-1]) or  len(en.split(' ')) < 5) and need_translate_str(en):
        # if self.byhand and is_proofread != 1 and len(en.split(' ')) < 4:
            print(f'手动翻译：{en} -> {cn}')
            self.dictionary.update_by_hand(en, cn)
            
        # 将当前元素的名称列表转换为(name,cn_str)的元组列表
        names_in_job = []
        for name in current_names:
            name_job = self.get_job(name,tag=self.name_tag)
            if name_job is None:
                continue
            if (not self.name_should_proofread) or name_job.is_proofread:
                names_in_job.append((name,name_job.cn_str))
            else:
                names_in_job.append((name, ""))
        self.job_list.append(Job(uid, en, cn, rel_path=self.rel_path, tag=tag, knowledge=[
        ], current_names=names_in_job, is_proofread=is_proofread, is_key=is_key, sql_id=sql_id, modified_at=modified_at))

    def process_first_level(self, obj: dict, source = None):
        """处理dict的第一级，将其中的每个元素添加到Job列表中

        Args:
            obj (dict): 英文dict
        """
        res_dict = {}
        for k, v in obj.items():
            if k == '_meta':
                meta_obj = v
                for kk, vv in v.items():
                    if kk == 'optionalFeatureTypes':
                        optFeat, ok = self.process_base_item(vv, f"/{kk}", current_names=[], tag="feat")
                        if not ok:
                            raise Exception(f"处理optionalFeatureTypes {kk} 失败")
                        meta_obj[kk] = optFeat
                    else:
                        meta_obj[kk] = vv
                        
                res_dict[k] = meta_obj
                continue
            if isinstance(v, dict):
                cn_json, is_ok = self.process_locked_entry(v, k, source)
                if not is_ok:
                    tmp_dict, ok = self.process_base_item(v, f"/{k}", current_names=[], tag=self.name_tag)
                    if not ok:
                        raise Exception(f"处理dict {k} 失败")
                    res_dict[k] = tmp_dict
                else:
                    res_dict[k] = cn_json
            elif isinstance(v, list):
                res_dict[k] = []
                for index, entry in enumerate(v):
                    if isinstance(entry, dict):
                        cn_json, is_ok = self.process_locked_entry(entry, k, source)
                        if is_ok:
                            res_dict[k].append(cn_json)
                            continue
                    list_item_key_path = f"/{k}[{index}]"
                    tmp_dict, ok = self.process_base_item(entry,  list_item_key_path, current_names=[], tag=self.name_tag)
                    if not ok:
                        raise Exception(f"处理list {k} 失败")
                    res_dict[k].append(tmp_dict)
        return res_dict, True



    def process_locked_entry(self, obj: dict, key:str, source = None):
        """处理已锁定的条目

        Args:
            obj (dict): 已锁定的条目
        """
        if len(self.locked_entries) == 0:
            return None, False
        if not isinstance(obj, dict):
            return None, False
        if 'source' in obj.keys():
            source = obj['source'].lower()
        if source is None or source == "": 
            return None,False
        if 'name' not in obj.keys():
            return None, False
        entry_id = f'{obj["name"].replace(" ","-").lower()}'
        if 'id' in obj.keys():
            entry_id = f'{obj["id"]}'
        file_name = f'{source}/{key}/{entry_id}.json'
        if self.locked_entries.get(file_name) is None:
            return None, False
        cn_json = self.locked_entries[file_name].get('cn_json')
        if cn_json is None:
            return None, False
        return self.locked_entries[file_name].get('cn_json'), True

    def process_dict(self, en_dict: dict, key_path: str, current_names: list = [], skip_keys: list = [], tag: str = ""):
        """检查dict类型，输出处理完的dict

        Args:
            en_dict (dict): 英文dict
            key_path (str): key路径
            current_name (str, optional): 所属元素的名称. Defaults to None.

        Returns:
            res_dict(dict): 中文dict
            is_ok(bool): 是否解析成功
        """
        res_dict = {}  # 结果dict
        __current_names = current_names.copy()  # 当前元素的名称列表，深拷贝，防止污染上一级的
        skip_name = False
        # 先找到name字段，放入当前元素的名称列表，同时增加ENG_name字段记录原始英文名
        if "name" in en_dict.keys() and isinstance(en_dict['name'], str):
            __current_names.append(en_dict["name"])
            self.name_list.append(en_dict["name"])
            
            # 去除ENG_name里面的标识符
            # 如果eng_name里开头是{@，且只有一个{@,且结尾是}。则截取第一个空格到第一个|或}之间的内容
            eng_name = en_dict["name"]
            if eng_name.startswith("{@") and eng_name.count("{@") == 1 and eng_name.endswith("}"):
                start_idx = eng_name.find(" ")
                end_idx = eng_name.find("|")
                eng_name = eng_name[start_idx+1:end_idx]
            
            res_dict['ENG_name'] = eng_name
            name_job = self.get_job(en_dict["name"],tag=tag)
            if name_job is None:
                cn_bean = self.dictionary.get(
                    en_dict["name"], load_from_sql=False, ignore_case=True, tag=tag)
                if cn_bean != None:
                    res_dict['name'] = cn_bean['cn']
                            # 将当前元素的名称列表转换为(name,cn_str)的元组列表
                    names_in_job = []
                    for name in current_names:
                        name_job = self.get_job(name,tag=tag)
                        if name_job is None:
                            continue
                        if (not self.name_should_proofread) or name_job.is_proofread:
                            names_in_job.append((name,name_job.cn_str))
                        else:
                            names_in_job.append((name, ""))
                    names_in_job.append((en_dict["name"], cn_bean['cn'] if cn_bean['proofread'] else ""))
                    # 使用jsonpath作为唯一标识
                    name_jsonpath = f'$' + (key_path + '/name').replace('/', '.')
                    self.job_list.append(Job(name_jsonpath, en_dict["name"], cn_bean['cn'], rel_path=self.rel_path, tag=tag, knowledge=[
                    ], current_names=names_in_job, is_proofread=cn_bean['proofread'], is_key=cn_bean['is_key'], sql_id=cn_bean['sql_id'], modified_at=cn_bean['modified_at']))
                    skip_name = True
            elif "{@" not in en_dict["name"] and name_job.cn_str != None:
                res_dict['name'] = name_job.cn_str
                skip_name = True
                # print(res_dict['name'])
        # 递归处理dict的所有字段
        for k, v in en_dict.items():
            # 检查是否需要跳过
            if skip_name and k == 'name':
                continue
            if check_skip_key(k, v, key_path) or k in skip_keys:
                res_dict[k] = v
            else:
                if k in KEY_MATCHED_TAG.keys():
                    tag = KEY_MATCHED_TAG[k]
                tmp_dict, ok = self.process_base_item(
                    v, key_path+'/'+k, __current_names, tag=tag)
                if not ok:
                    raise RuntimeError(f'process error:{k}')
                res_dict[k] = tmp_dict
            if k == 'nameSuffix' and ' Barding' != v:
                # 后置描述改前置
                res_dict['namePrefix'] = res_dict['nameSuffix']
                del res_dict['nameSuffix']
        return res_dict, True

    def process_base_item(self, en_item, key_path: str, current_names: list = [], tag=""):
        """转换json_item的入口函数，主要用于根据不同的类型调用不同的处理函数

        Args:
            en_item (obj): 英文的json_item
            key_path (str): 路径
            current_name (str, optional): 所属元素的名称. Defaults to None.

        Returns:
            res_item: 转换后的item
            is_ok(bool): 是否成功
        """
        if en_item is None:
            # logger.info(f"{self.rel_path}中存在空变量！请注意！")
            return en_item, True
        elif isinstance(en_item, int) or isinstance(en_item, float) or isinstance(en_item, bool):
            # 整型、浮点型、布尔型不翻译
            return en_item, True
        elif isinstance(en_item, str):
            # 传递key_path给str_2_job，用于生成jsonpath
            tmp_str = self.str_2_job(en_item, current_names, tag, key_path)
            return tmp_str, True
        elif isinstance(en_item, dict):
            return self.process_dict(en_item, key_path, current_names, tag=tag)
        elif isinstance(en_item, list):
            return self.process_list(en_item, key_path, current_names, tag=tag)
        return None, False

    def process_list(self, en_list, key_path, current_names: list = [], tag: str = ""):
        res_list = []
        for index, v in enumerate(en_list):
            # 对于列表项，我们需要在key_path后添加索引以确保唯一性
            list_item_key_path = f"{key_path}[{index}]"
            tmp_list, ok = self.process_base_item(v, list_item_key_path, current_names, tag=tag)
            if not ok:
                logger.error(f"{self.rel_path}解析{v}时出错")
            res_list.append(tmp_list)

        return res_list, True

    def __split_and_append_job(self, s, tag=None, current_names: list = [], key_path=""):
        """
        对输入的字符串进行分割检查和处理，生成带有Job UUID替换标识的字符串。

        Args:
            s (str): 待处理的字符串
            tag (str, optional): 标签，默认为空字符串
            current_name (str, optional): 所属元素的名称，默认为 None

        Returns:
            str: 处理后的字符串，包含Job UUID替换标识
        """
        res_str = s
        sub_str_list = split_string(res_str)
        str_list = [res_str] if len(sub_str_list) == 1 else sub_str_list

        def _process_value(v, tag=None, value_index=0):
            if need_translate_str(v):
                # 使用jsonpath_ng风格的路径作为唯一标识
                if key_path:
                    # 转换为标准jsonpath格式，并添加值索引以确保唯一性
                    jsonpath_expr = f'$' + key_path.replace('/', '.')
                    if '/' in key_path:
                        # 如果是复杂路径，添加值索引作为额外标识
                        jsonpath_expr = f"{jsonpath_expr}.{value_index}"
                    uid = jsonpath_expr
                else:
                    # 如果没有key_path，使用动态路径+哈希值
                    uid = f'$._dynamic.{hash(v)}'
                
                # 去除前缀后缀
                sk_without_prefix, prefix = check_prefix(v)
                sk_pure, suffix = check_suffix(sk_without_prefix)
                # 生成Job 如果已经有相同的Job，则更新uuid
                j = self.get_job(sk_pure, tag=tag)
                if j is not None:
                    uid = j.uid
                else:
                    # 如果是新的Job，则添加到Job列表
                    self.set_job(uid, sk_pure, None, tag=tag,
                                   current_names=current_names)

                return f'{prefix}{{!@ {uid}}}{suffix}'
            else:
                return v
        if tag == "filter":
            res_str, handled = process_filter_split_values(
                str_list,
                _process_value,
                support_pages=["items", "spells", "optionalfeatures", "races", "rewards"],
            )
            if handled:
                return res_str
        res_str, handled = process_post_filter_split_values(
            str_list, _process_value, tag=tag, key_path=key_path)
        if handled:
            return res_str
        return replace_split_values(str_list, _process_value, tag=tag)

    def get_job(self, en, tag=""):
        # first_match = None
        for j in self.job_list:
            if j.en_str == en:
                if j.tag == tag:
                    # 优先匹配tag和en都相同的
                    return j
        if self.dictionary.get(en, load_from_sql=False, tag=tag) is not None:
            # 这里说明source表匹配到了en和tag完全相同的，但是还没有在job_list中，所以暂时返回None，让后续逻辑建一个新的
            return None
        else:
            # 这里说明表里没有en和tag完全相同的，则可以尝试匹配只有en相同，但tag为None的
            for j in self.job_list:
                if j.en_str == en and j.tag is None:
                    return j
