import os
import re
import json
import csv
import threading

from langchain_core.runnables import Runnable
from config import logger, DS_KEY, OUT_PATH
from app.core.utils import Job, TranslatorStatus
from app.core.database import DatabaseAdapter
from .siliconflow_adapter import SiliconFlowAdapter
from .mock_adapter import MockAdapter
from .llm_factory import LLMFactory
from app.core.utils import Job, replace_cn_pattern, need_translate_str, check_prefix, check_suffix, parse_custom_format, format_llm_msg, parse_foundry_items_uuid_format
from typing import List, Tuple, Optional
from app.core.bean.term import Term, to_terms


class JobProcessor(Runnable):
    """解释JOB对象
    多线程逐个处理
    对于每个JOB对象：
    1. 检查是否需要翻译
    2. 召回知识库的知识
    3. 翻译
    4. 回调

    Args:
        Runnable (_type_): _description_
    """

    def __init__(self, thread_num: int = 10, update: bool = False):
        self.thread_num = thread_num
        self.update = update
        self.ok = self.__init_dictionary()
        if not self.ok:
            logger.error(f"初始化字典失败")
            return
        self.ok = self.__init_adapter()
        if not self.ok:
            logger.error(f"加载LLM中间件失败")
            return

        self.byhand = False
        self.force = False
        # 临时术语相关变量
        self.temple_terms = set()
        self.cache = True
        self.cache_lock = threading.Lock()
        self.terms_file_path = '/tmp/terms.csv'

        
    def invoke(self, input, config=None, **kwargs):
        inputs = [input] if isinstance(input, str) else input
        self.byhand = config['metadata'].get('byhand', False)
        self.force = config['metadata'].get('force', False)
        self.mode = config['metadata'].get('mode', '5et')
        self.cache = config['metadata'].get('cache', True)
        if self.byhand:
            # 手动模式，串行执行
            self.thread_num = 1
            
        self.ok = self.__init_factory()
        if not self.ok:
            logger.error(f"初始化LLM工厂失败")
            return
        
        for res in inputs:
            logger.info(f"开始处理 {res.json_path} 中的Job")
            # self.rel_path = get_rel_path(res.json_path)
            # self.obj = res['obj']
            self.done_jobs: List[Job] = []
            self.factory.reset()
            if self.cache:
                # 贪心，按照job的en_str长度从短到长排序。
                # 这样短文本中的术语先处理。加速
                res.job_list.sort(key=lambda x: len(x.en_str))
            self.factory.add_jobs(res.job_list)
            self.factory.set_finish(True)
            # 加载临时术语
            self.__load_temple_terms(res.job_list, res.out_path)
            self.factory.start_work()
            # if self.factory.isAllDone():
                # yield res
                # self.write_2_json(res.out_path, res.json_obj)
            # else:
            if not self.factory.isAllDone():
                logger.error(f"处理{res.json_path}中的Job总计{self.factory.job_count}个，成功{self.factory.finish_count}个，失败{self.factory.error_count}个！")
                # 将失败的 job 列表导出到文件，方便人工查看与重试
                failed = []
                try:
                    failed = getattr(self.factory, 'failed_jobs', [])
                except Exception:
                    failed = []

                failed_path = os.path.join(OUT_PATH, res.out_path + '.failed_jobs.json')
                try:
                    os.makedirs(os.path.dirname(failed_path), exist_ok=True)

                    with open(failed_path, 'w') as fh:
                        json.dump([j.to_serializable() for j in failed], fh, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error(f'写出 failed_jobs 文件失败: {e}')

                if len(failed) > 0:
                    logger.warning(f"以下 {len(failed)} 个 Job 处理失败，已保存到: {failed_path}")
                    for j in failed:
                        last = j.last_answer if hasattr(j, 'last_answer') else ''
                        last_short = last if not isinstance(last, str) else (last[:200] + '...' if len(last) > 200 else last)
                        print(f"- uid: {j.uid}, en: {j.en_str}, err_time: {j.err_time}, last_answer: {last_short}")
                    print('\n可用下面命令快速重试这些失败的Job (示例)：')
                    print(f"python3 main.py retry-failed --file \"{failed_path}\" --thread_num {self.thread_num} --byhand {self.byhand} --force {self.force}")
                else:
                    logger.info('没有记录到被丢弃的失败 Job。')
                continue
            yield res

    def __load_temple_terms(self, job_list, out_path):
        self.temple_terms.clear()
        self.terms_file_path = os.path.join(OUT_PATH, out_path+".terms.csv")
        if os.path.exists(self.terms_file_path):
            with open(self.terms_file_path, "r") as terms_file:
                loaded_term_list = csv.reader(terms_file)
                for term in loaded_term_list:
                    # 添加行数据验证，确保至少有3个元素
                    if len(term) >= 3 and term[0] and term[1]:  # 同时确保英文和中文术语不为空
                        try:
                            self.temple_terms.add(Term(en=term[0], cn=term[1], category=term[2]))
                        except Exception as e:
                            logger.warning(f"解析术语行失败: {term}, 错误: {str(e)}")
                    elif term:  # 非空行但格式不正确
                        logger.warning(f"跳过格式不正确的术语行: {term}")
        for job in job_list:
            self.temple_terms.update(to_terms(job.en_str, job.cn_str, job.tag))
    def __dump_temple_terms(self):
        with open(self.terms_file_path, "w") as terms_file:
            term_list = [f"{term.en},{term.cn},{term.category}" for term in self.temple_terms]
            terms_file.write("\n".join(term_list))
    
    def __add_temple_terms(self, en, cn):
        self.cache_lock.acquire()
        en = en.lower()
        term = Term(en=en, cn=cn, category=None)
        if term not in self.temple_terms:
            self.temple_terms.add(term)
            with open(self.terms_file_path, "a") as terms_file:
                terms_file.write(f"\n{en},{cn},{term.category}")
            logger.info(f"添加术语到缓存:{en} -> {cn}")
        self.cache_lock.release()
    
    def __search_temple_terms(self, en_str: str):
        return [term for term in self.temple_terms if term.en.lower() in en_str.lower()]
    
    def __init_dictionary(self):
        """
        初始化字典
        """
        self.dictionary = DatabaseAdapter(source="GPT")
        return self.dictionary.ok

    def __init_adapter(self):
        if os.getenv("TRANSLATOR_LLM_BACKEND", "").lower() == "mock":
            self.adapter = MockAdapter()
            return True
        self.adapter = SiliconFlowAdapter(DS_KEY)
        return True

    def __init_factory(self):
        def work_func(job: Job, worker_id: int) -> (tuple[Job, TranslatorStatus]):
            # 检测是否需要翻译
            if not job.need_translate:
                return job, TranslatorStatus.SUCCESS
            if self.force and job.sql_id == None:
                # force模式下，只更新有的
                return job, TranslatorStatus.SUCCESS
            # 添加缓存中的术语
            if self.cache and self.temple_terms:
                for temp in self.__search_temple_terms(job.en_str):
                    if temp not in job.terms:
                        job.terms.append(temp)
            # 发送给大模型进行翻译
            request, promot = job.to_llm_question()
            if job.cn_str and job.cn_str != "" and self.byhand:
                print(f"请求细节：{request}")
                print(f"原始中文：{job.cn_str}")
                confirm = input("是否需要大模型翻译？(Y/n)")
                if confirm.lower() == 'n':
                    input_str = input(f"请确认(Y/n)：")
                    if input_str.lower() == 'n':
                        return None, TranslatorStatus.FAILURE
                    elif input_str.lower() != 'y' and input_str != '':
                        job.cn_str = input_str
                    return job, TranslatorStatus.SUCCESS
            
            msg, status = self.adapter.sendText(request, promot)
            if status != TranslatorStatus.SUCCESS:
                logger.warning(f'获取结果错误:{msg}')
                return None, status

            # 对回调信息进行解析
            kimi_data = self.__response_msg_to_data(msg)
            if (kimi_data == None):
                logger.warning(f'解析结果错误:{msg}')
                return None, TranslatorStatus.FAILURE

            cstr = kimi_data["trans_str"]

            cn_str = replace_cn_pattern(
                cstr, job.en_str)
            if not isinstance(cn_str, str):
                job.last_answer = cstr
                return None, TranslatorStatus.FAILURE

            if self.byhand:
                # 手动模式，需要用户确认
                print(f"请求细节：{request}")
                print(f"原始中文：{job.cn_str}")
                print(f"翻译结果：{cn_str}")
                input_str = input(f"请确认(Y/n)：")
                if input_str.lower() == 'n':
                    return None, TranslatorStatus.FAILURE
                elif input_str.lower() == 'y' or input_str == '':
                    job.cn_str = cn_str
                else:
                    job.cn_str = input_str
            else:
                # 自动模式，检查替换是否正确
                # 1. 初筛中文的{ 和 }数量与英文相同
                if cn_str.count('{') != job.en_str.count('{') or cn_str.count('}') != job.en_str.count('}'):
                    logger.warning(f"翻译文本替换错误:{job}")
                    job.last_answer = cn_str
                    return None, TranslatorStatus.FAILURE
                # 2. 检查替换是否正确
                _ ,ok=  self.__replace_sub_jobs(cn_str,job.en_str,job.tag)
                if not ok:
                    job.last_answer = cn_str
                    logger.warning(f"翻译文本替换错误:{job}")
                    return None, TranslatorStatus.FAILURE
                job.cn_str = cn_str
                # job.cn_str = replaced_cn
            # 3. 检查是否有新的术语需要添加到字典
            if self.cache:
                job_terms = to_terms(job.en_str, job.cn_str, job.tag)
                if job_terms:
                    for t in job_terms:
                        self.__add_temple_terms(t.en, t.cn)
                    if 'add_terms' in kimi_data.keys() and isinstance(kimi_data['add_terms'], dict):
                        for term, cn in kimi_data['add_terms'].items():
                            self.__add_temple_terms(term, cn)
            
            return job, TranslatorStatus.SUCCESS

        def put_done_job(job: Job):
            """
            处理完成任务
            """
            if job is not None and job.cn_str is not None:
                if self.update and job.need_translate:
                    # 写入数据库,如果手动模式，则默认就是校对过得
                    if job.sql_id is None:
                        logger.info(f"处理完成任务:{job.en_str}, 中文:{job.cn_str}")
                        ok = self.dictionary.put(
                            job.en_str, job.cn_str, job.rel_path, proofread=self.byhand, tag=job.tag)
                        if not ok:
                            logger.error(f"写入字典失败:{job}")
                    else:
                        self.dictionary.update(job.sql_id, job.en_str, job.cn_str, proofread=self.byhand, tag=job.tag)
                self.done_jobs.append(job)
            else:
                logger.error(f"处理完成任务错误:{job}")

        self.factory = LLMFactory(
            work_num=self.thread_num,
            work_func=work_func,
            done_func=put_done_job,
        )
        return True
    def __response_msg_to_data(self, msg):
        # 对回调信息进行解析
        kimi_data, ok = format_llm_msg(msg)
        if (
            (not ok)
            or "trans_str" not in kimi_data.keys()
        ):
            return None
        if isinstance(kimi_data["trans_str"], str):
            return kimi_data
        if isinstance(kimi_data["trans_str"], list) and len(kimi_data["trans_str"]) == 1:
            if isinstance(kimi_data["trans_str"][0], str):
                kimi_data['trans_str'] = kimi_data['trans_str'][0]
                return kimi_data
        return None
    
    def __get(self, en: str, tag="") -> (Tuple[str, bool]):
        for j in self.done_jobs:
            if j.en_str == en and j.tag == tag:
                return j.cn_str, True

        return self.dictionary.get(en, tag=tag)

    def __process_value(self, value, tag=""):
        """
        处理单个值，进行前缀、后缀检查和翻译替换
        """
        if need_translate_str(value):
            value_without_prefix, prefix = check_prefix(value)
            value_pure, suffix = check_suffix(value_without_prefix)
            new_value, ok = self.__get(value_pure,tag)
            if ok and new_value is not None:
                new_value = f'{prefix}{new_value}{suffix}'
                # TODO 临时增加一次校验逻辑，后续删除。校验value和new_value中的@数量是否相同
                if value.count('@') != new_value.count('@'):
                    logger.warning(f"翻译文本替换错误:value={value},new_value={new_value}")
                    return value, False
                return new_value, True
            else:
                return value, False
        return value, True

    def __match_tag_values_by_priority(self, cn_match_k, cn_match_v, en_match_k, en_match_v):
        """按优先级匹配cn/en中的{@tag value}对，避免顺序错位。"""
        cn_items = [{"idx": i, "tag": k, "value": v}
                    for i, (k, v) in enumerate(zip(cn_match_k, cn_match_v))]
        en_items = [{"idx": i, "tag": k, "value": v}
                    for i, (k, v) in enumerate(zip(en_match_k, en_match_v))]

        used_en = set()
        matches = {}
        translated_match_cache = {}

        # 1) tag+value完全一致
        for cn_item in cn_items:
            for en_item in en_items:
                if en_item["idx"] in used_en:
                    continue
                if cn_item["tag"] == en_item["tag"] and cn_item["value"] == en_item["value"]:
                    matches[cn_item["idx"]] = en_item["idx"]
                    used_en.add(en_item["idx"])
                    break

        # 2) tag一致，且英文value替换后的结果与当前中文value一致
        for cn_item in cn_items:
            if cn_item["idx"] in matches:
                continue
            for en_item in en_items:
                if en_item["idx"] in used_en or cn_item["tag"] != en_item["tag"]:
                    continue
                cache_key = (cn_item["idx"], en_item["idx"])
                if cache_key not in translated_match_cache:
                    replaced_value, ok = self.__replace_sub_jobs(
                        cn_item["value"], en_item["value"], tag=en_item["tag"])
                    translated_match_cache[cache_key] = ok and replaced_value == cn_item["value"]
                if translated_match_cache[cache_key]:
                    matches[cn_item["idx"]] = en_item["idx"]
                    used_en.add(en_item["idx"])
                    break

        # 3) tag一致时，按value的|分段位置相似度匹配
        for cn_item in cn_items:
            if cn_item["idx"] in matches:
                continue
            best_en_idx = None
            best_score = 0
            cn_parts = cn_item["value"].split("|")
            for en_item in en_items:
                if en_item["idx"] in used_en or cn_item["tag"] != en_item["tag"]:
                    continue
                en_parts = en_item["value"].split("|")
                score = 0
                max_len = max(len(cn_parts), len(en_parts))
                for pos, (cn_part, en_part) in enumerate(zip(cn_parts, en_parts)):
                    if cn_part == en_part:
                        score += (max_len - pos)
                if score > best_score:
                    best_score = score
                    best_en_idx = en_item["idx"]
            if best_en_idx is not None and best_score > 0:
                matches[cn_item["idx"]] = best_en_idx
                used_en.add(best_en_idx)

        # 4) fallback: 仅按tag顺序匹配
        for cn_item in cn_items:
            if cn_item["idx"] in matches:
                continue
            for en_item in en_items:
                if en_item["idx"] in used_en:
                    continue
                if cn_item["tag"] == en_item["tag"]:
                    matches[cn_item["idx"]] = en_item["idx"]
                    used_en.add(en_item["idx"])
                    break

        if len(matches) != len(cn_items):
            return False, None

        paired = []
        for cn_item in cn_items:
            en_item = en_items[matches[cn_item["idx"]]]
            paired.append((cn_item["tag"], cn_item["value"],
                          en_item["tag"], en_item["value"]))
        return True, paired

    def __replace_sub_jobs(self, cn_str: str, en_str: Optional[str] = None, tag = ""):
        # print(cn_str)
        processed = False
        if en_str is None:
            # 若没有传入en_str，需要从done_jobs中查找
            for j in self.done_jobs:
                if j.cn_str == cn_str:
                    en_str = j.en_str
                    break
        if en_str is None:
            return cn_str, False

        # 初筛
        if "{@" in cn_str:
            # 初筛，包含@{的，需要继续处理
            p_v, ok = self.__process_value(en_str, tag=tag)
            if ok:
                cn_str = p_v
            processed = True
        en_match_k, en_match_v, en_is_valid = parse_custom_format(
            en_str, False)
        cn_match_k, cn_match_v, cn_is_valid = parse_custom_format(
            cn_str, False)
        if (not en_is_valid) or (not cn_is_valid) or (len(cn_match_v) != len(en_match_v)):
            return cn_str, False

        ok, matched_pairs = self.__match_tag_values_by_priority(
            cn_match_k, cn_match_v, en_match_k, en_match_v)
        if not ok:
            return cn_str, False
        check_split_str = en_str
        # 第一步：把cn_str中的所有@{tag value}都替换为英文中的对应的样子
        # if len(cn_match_k) > 0:
        #     processed = True
            
        for ck, cv, ek, ev in matched_pairs:
            check_split_str = check_split_str.replace(f"{{@{ek} {ev}}}", "")

            cn_str = cn_str.replace(f"{{@{ck} {cv}}}", f"{{@{ek} {ev}}}",1)
            # 第二步：逐个解析每个ev
            new_v, ok = self.__replace_sub_jobs(cv, ev, tag = ek)
            if not ok:
                return cn_str, False
            cn_str = cn_str.replace(f"{{@{ek} {ev}}}", f"{{@{ek} {new_v}}}",1)
            
        if '|' in check_split_str:
            if tag == "filter":
                filter_values = en_str.split("|")
                if (len(filter_values) > 2):
                    # 正常至少有3个值
                    cv_page = filter_values[1]
                    cv_name, _ = self.__process_value(filter_values[0], tag=cv_page)
                    if cv_page == "bestiary":
                        cv_conditions = []
                        for eev in filter_values[2:]:
                            if eev.startswith('type='):
                                # 锁定type
                                cv_conditions.append(eev)
                            elif eev.startswith('tag='):
                                en_tags = eev[4:].split(';')
                                cn_tags = []
                                for et in en_tags:
                                    ctag, _ = self.__process_value(et, tag="creature")
                                    cn_tags.append(ctag)
                                cv_conditions.append(f'tag={";".join(cn_tags)}')
                            else:
                                ccv, _ = self.__process_value(eev, tag=cv_page)
                                cv_conditions.append(ccv)
                        cn_str = f"{cv_name}|{cv_page}|{'|'.join(cv_conditions)}"
                        return cn_str, True
                    elif cv_page in ["items", "spells", "optionalfeatures", "races"]:
                        cv_conditions = []
                        for eev in filter_values[2:]:
                            ccv, _ = self.__process_value(eev, tag=cv_page)
                            cv_conditions.append(ccv)
                        cn_str = f"{cv_name}|{cv_page}|{'|'.join(cv_conditions)}"
                        return cn_str, True
            elif tag == "adventure" or tag == "area":
               filter_values = en_str.split("|")
               if (len(filter_values) > 2):
                    # 正常至少有3个值
                    cv_source = filter_values[1]
                    cv_name, _ = self.__process_value(filter_values[0], tag="adventure")
                    cv_conditions = []
                    for eev in filter_values[2:]:
                        ccv, _ = self.__process_value(eev, tag="adventure")
                        cv_conditions.append(ccv)
                    cn_str = f"{cv_name}|{cv_source}|{'|'.join(cv_conditions)}"
                    return cn_str, True

            en_split = en_str.split('|')
            cn_split = cn_str.split('|')
            res_split = []
            for i, eev in enumerate(en_split):
                if len(cn_split) > i:
                    ccv = cn_split[i]
                else:
                    ccv = None
                if "{@" in eev and ccv is not None and ccv != eev:
                    res_split.append(ccv)
                else:
                    p_v, ok = self.__process_value(eev, tag=tag)
                    if ok or ccv is None:
                        res_split.append(p_v)
                    else:
                        res_split.append(ccv)
            cn_str = '|'.join(res_split)
        elif processed == False:
            p_v, ok = self.__process_value(en_str, tag = tag)
            if ok:
                cn_str = p_v
        return cn_str, True

    def write_2_json(self, json_path: str, obj: object):
        """
        将处理后的作业信息写入JSON文件。
        该方法会先调用 __replace_jobs 方法替换作业中的相关内容，
        然后将替换后的内容写入JSON文件。

        Returns:
            bool: 如果写入成功返回True，否则返回False。
        """
        # 调用 __replace_jobs 方法替换作业中的相关内容
        new_obj, ok = self.__replace_jobs(obj)
        # 检查替换操作是否成功
        if not ok:
            # 若替换失败，返回False
            logger.warning(f"write_2_json: {json_path} failed")
            return False
        # 若替换成功，调用 __write_json 方法将替换后的内容写入JSON文件
        json_path = os.path.join(OUT_PATH, json_path)
        job_path = json_path + ".jobs"
        if not (os.path.exists(job_path)):
            return False
        if self.mode == 'homebrew':
            # 删除原始文件
            # os.remove(json_path)
            # 替换文件名中的字段
            eng_name = json_path.split(';')[-1].strip()[:-5]
            cn_name, _ = self.__process_value(eng_name)
            json_path = json_path.replace(f'; {eng_name}', f'; {cn_name}')
        elif self.mode == 'ua':
            # 删除原始文件
            # os.remove(json_path)
            # 替换文件名中的字段
            en_category = json_path[json_path.rfind('/')+1:json_path.rfind('-')].strip()
            cn_category, _ = self.__process_value(en_category)
            eng_name = json_path.split('-')[-1].strip()[:-5]
            cn_name, _ = self.__process_value(eng_name)
            json_path = json_path.replace(f'- {eng_name}', f'- {cn_name}')
            json_path = json_path.replace(f'/{en_category}', f'/{cn_category}')
        try:
            with open(json_path, "w") as file:
                file.write(json.dumps(new_obj, ensure_ascii=False, indent=2))

            # with open(job_path, "w") as file:
            #     file.write(json.dumps(self.done_jobs,
            #                ensure_ascii=False, indent=2))
        except ValueError as e:
            logger.debug(e)
            return False
        return True
    
    # def __replace_first_level_job(self, obj) {
    #     if
    # }

    def __replace_jobs(self, obj):
        """_summary_

        Args:
            obj (_type_): _description_

        Returns:
            _type_: _description_
        """
        if isinstance(obj, str):
            # 通过uuid替换指定的job
            pattern = r'\{!@ ([^\}]+)\}'
            matches = re.findall(pattern, obj)
            if len(matches) > 0:
                for job_id in matches:
                    for j in self.done_jobs:
                        if j.uid == job_id:
                            j.cn_str, ok = self.__replace_sub_jobs(
                                j.cn_str, j.en_str, tag=j.tag)
                            obj = obj.replace(f'{{!@ {job_id}}}', j.cn_str)
                            break

                return obj, True
            else:
                # obj, ok = self.__replace_sub_jobs(obj)
                return obj, False
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k == 'ENG_name':
                    continue
                try:
                    new_v, ok = self.__replace_jobs(v)
                    if ok:
                        # 临时在这里特殊处理foundry-items.json和foundry-optinalfeatures.json中的uuid特殊格式
                        if (k == "uuid"):
                            ev = ''
                            for j in self.done_jobs:
                                if j.cn_str == new_v:
                                    ev = j.en_str
                                    break
                            if ev != '':
                                new_v = ev
                            match_k, match_v,_ = parse_foundry_items_uuid_format(new_v)
                            if (len(match_k) > 0):
                                for mk,mv in zip(match_k, match_v):
                                    cn_v,_ = self.__get(mv, mk)
                                    new_v = new_v.replace(mv, cn_v)
                        
                        obj[k] = new_v
                except Exception as exc:
                    logger.error(f'{k} generated an exception: {exc}')
            return obj, True
        elif isinstance(obj, list):
            for i, o in enumerate(obj):
                new_o, ok = self.__replace_jobs(o)
                if ok:
                    obj[i] = new_o
            return obj, True
        elif isinstance(obj, bool) or isinstance(obj, int) or isinstance(obj, float):
            return obj, True
        elif obj is None:
            return obj, False
        else:
            logger.warning(f"无法解析！{obj}")
            return obj, False
