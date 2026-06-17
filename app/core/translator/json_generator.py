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
from .llm_factory import LLMFactory
from app.core.utils import Job, replace_cn_pattern, need_translate_str, check_prefix, check_suffix, parse_custom_format, format_llm_msg, parse_foundry_items_uuid_format, split_string, process_filter_split_values, process_post_filter_split_values, get_tag_display_text
from typing import List, Tuple, Optional
from app.core.bean.term import Term, to_terms


class JsonGenerator(Runnable):
    """根据原始英文Json和处理好的Job生成中文Json

    Args:
        Runnable (_type_): _description_
    """

    def __init__(self, thread_num: int = 10):
        self.thread_num = thread_num
        self.ok = self.__init_dictionary()
        
        if not self.ok:
            logger.error(f"初始化字典失败")
            return

        self.done_jobs:List[Job] = []
        
    def invoke(self, input, config=None, **kwargs):
        inputs = [input] if isinstance(input, str) else input
        self.mode = config['metadata'].get('mode', '5et')
            
        for res in inputs:
            logger.info(f"开始将 {res.json_path} 转换为中文")
            self.done_jobs = res.job_list
            # 调用 __replace_jobs 方法替换英文Json中的相关内容
            cn_obj, ok = self.__replace_jobs(res.json_obj)
        
            # 检查替换操作是否成功
            if not ok:
                # 若替换失败，返回False
                logger.warning(f"生成中文Json失败: {res.out_path}")
                continue
            
            res.cn_obj = cn_obj
            # self.write_2_json(res.out_path, cn_obj)
            logger.info(f"生成中文Json成功: {res.out_path}")
            
            yield res
    
    def __init_dictionary(self):
        """
        初始化字典
        """
        self.dictionary = DatabaseAdapter(source="GPT")
        return self.dictionary.ok

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
            if len(split_string(value)) > 1:
                return value, False
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
            str_list = check_split_str.split("|")

            def _process_pipe_value(value, value_tag, _value_index):
                cn_value, _ = self.__process_value(value, tag=value_tag)
                return cn_value

            if tag == "filter":
                filter_values = en_str.split("|")
                cn_str, handled = process_filter_split_values(
                    filter_values,
                    _process_pipe_value,
                    support_pages=["items", "spells", "optionalfeatures", "races"],
                    condition_tag_resolver=lambda page, _idx, _value: page[:-1] if page.endswith("s") else page,
                    bestiary_tag_resolver=lambda _idx, _value: "creature",
                )
                if handled:
                    return cn_str, True
            elif tag == "adventure" or tag == "area":
               filter_values = en_str.split("|")
               if (len(filter_values) > 2):
                    # 正常至少有3个值
                    cv_source = filter_values[1]
                    cv_name, _ = self.__process_value(filter_values[0], tag=tag)
                    cv_conditions = []
                    for eev in filter_values[2:]:
                        ccv, _ = self.__process_value(eev, tag=tag)
                        cv_conditions.append(ccv)
                    cn_str = f"{cv_name}|{cv_source}|{'|'.join(cv_conditions)}"
                    return cn_str, True
            cn_str, handled = process_post_filter_split_values(
                str_list, _process_pipe_value, tag=tag)
            if handled:
                return cn_str, True
            en_split = split_string(en_str)
            cn_split = split_string(cn_str)
            res_split = []
            for i, eev in enumerate(en_split):
                if len(cn_split) > i:
                    ccv = cn_split[i]
                else:
                    ccv = None
                if "{@" in eev and ccv is not None and ccv != eev:
                    res_split.append(ccv)
                elif "{@" in eev:
                    p_v, ok = self.__replace_sub_jobs(eev, tag=tag)
                    res_split.append(p_v)
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

    def __sync_tag_only_cn(self, cn_str: str, old_en_str: Optional[str], new_en_str: str, tag=""):
        """Best-effort update of existing CN when English only gained tag wrappers."""
        if not isinstance(cn_str, str) or not isinstance(new_en_str, str):
            return cn_str
        old_tags, old_values, old_valid = parse_custom_format(old_en_str or "", False)
        new_tags, new_values, new_valid = parse_custom_format(new_en_str, False)
        if not old_valid or not new_valid:
            return cn_str

        cn_tags, cn_values, cn_valid = parse_custom_format(cn_str, False)
        old_cn_links = []
        if cn_valid and len(cn_values) == len(old_values):
            old_cn_links = [
                {
                    "old_tag": old_tag,
                    "old_value": old_value,
                    "old_display": get_tag_display_text(old_value, old_tag),
                    "cn_tag": cn_tag,
                    "cn_value": cn_value,
                    "cn_display": get_tag_display_text(cn_value, cn_tag),
                }
                for old_tag, old_value, cn_tag, cn_value in zip(
                    old_tags, old_values, cn_tags, cn_values)
            ]

        old_pairs = list(zip(old_tags, old_values))
        replaced_existing_tag = False
        inserted_plain_tag = False
        for new_tag, new_value in zip(new_tags, new_values):
            if (new_tag, new_value) in old_pairs:
                old_pairs.remove((new_tag, new_value))
                continue
            display_text = get_tag_display_text(new_value, new_tag)
            old_cn_link = self.__find_old_cn_link(old_cn_links, display_text)
            if old_cn_link is not None:
                old_cn_tag = f"{{@{old_cn_link['cn_tag']} {old_cn_link['cn_value']}}}"
                new_cn_value = self.__build_cn_tag_value(
                    new_value, new_tag, old_cn_link["cn_display"])
                cn_str = cn_str.replace(old_cn_tag, f"{{@{new_tag} {new_cn_value}}}", 1)
                replaced_existing_tag = True
                continue
            if not display_text or display_text not in cn_str:
                known_cn = self.__get_known_translation(display_text, new_tag)
                if not known_cn or known_cn not in cn_str:
                    continue
                display_text = known_cn
            cn_str, replaced = self.__replace_plain_outside_tags(
                cn_str,
                display_text,
                f"{{@{new_tag} {self.__build_cn_tag_value(new_value, new_tag, display_text)}}}",
            )
            if not replaced:
                continue
            inserted_plain_tag = True

        if replaced_existing_tag and not inserted_plain_tag:
            return cn_str
        synced, ok = self.__replace_sub_jobs(cn_str, new_en_str, tag=tag)
        return synced if ok else cn_str

    def __find_old_cn_link(self, old_cn_links: List[dict], display_text: str):
        for link in old_cn_links:
            if link["old_display"] == display_text:
                return link
        return None

    def __get_known_translation(self, en: str, tag: str = "") -> Optional[str]:
        if not en:
            return None
        en_key = en.strip().lower()
        fallback = None
        for job in self.done_jobs:
            if not isinstance(job.en_str, str) or not isinstance(job.cn_str, str):
                continue
            if job.en_str.strip().lower() != en_key:
                continue
            if job.cn_str == job.en_str:
                continue
            if job.tag == tag:
                return job.cn_str
            if fallback is None:
                fallback = job.cn_str
        if self.dictionary is not None:
            cn, ok = self.dictionary.get(en, tag=tag)
            if ok and cn is not None and cn != en:
                return cn
        return fallback

    def __build_cn_tag_value(self, new_value: str, new_tag: str, cn_display: str):
        parts = new_value.split("|")
        if not parts:
            return new_value
        if new_tag in ("adventure", "area", "book", "filter"):
            display_index = 0
        elif len(parts) >= 3 and parts[-1] != "":
            display_index = len(parts) - 1
        else:
            display_index = 0
        parts[display_index] = cn_display
        return "|".join(parts)

    def __format_tag(self, tag: str, value: str):
        return f"{{@{tag}}}" if value == "" else f"{{@{tag} {value}}}"

    def __align_cn_tags_to_en_by_position(self, cn_str: str, en_str: str):
        en_tags, en_values, en_valid = parse_custom_format(en_str, False)
        cn_tags, cn_values, cn_valid = parse_custom_format(cn_str, False)
        if (
            not en_valid
            or not cn_valid
            or len(en_values) == 0
            or len(en_values) != len(cn_values)
        ):
            return cn_str, False

        changed = False
        for en_tag, en_value, cn_tag, cn_value in zip(en_tags, en_values, cn_tags, cn_values):
            cn_display = get_tag_display_text(cn_value, cn_tag)
            aligned_value = self.__build_cn_tag_value(en_value, en_tag, cn_display)
            old_tag = self.__format_tag(cn_tag, cn_value)
            new_tag = self.__format_tag(en_tag, aligned_value)
            if old_tag == new_tag:
                continue
            cn_str = cn_str.replace(old_tag, new_tag, 1)
            changed = True
        return cn_str, changed

    def __replace_plain_outside_tags(self, text: str, old: str, new: str):
        if not old:
            return text, False
        index = 0
        while index < len(text):
            tag_index = text.find("{@", index)
            match_index = text.find(old, index)
            if match_index == -1:
                return text, False
            if tag_index == -1 or match_index < tag_index:
                return text[:match_index] + new + text[match_index + len(old):], True
            end_index = text.find("}", tag_index + 2)
            if end_index == -1:
                return text, False
            index = end_index + 1
        return text, False

    def write_2_json(self, json_path: str, obj: object):
        """
        将处理后的作业信息写入JSON文件。

        Returns:
            bool: 如果写入成功返回True，否则返回False。
        """

        # 若替换成功，调用 __write_json 方法将替换后的内容写入JSON文件
        json_path = os.path.join(OUT_PATH, json_path)
        job_path = json_path + ".jobs"
        if not (os.path.exists(json_path) and os.path.exists(job_path)):
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

        except ValueError as e:
            logger.debug(e)
            return False
        return True

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
                        if j.uid == job_id and j.cn_str is not None:
                            if getattr(j, "tag_sync_required", False):
                                j.cn_str = self.__sync_tag_only_cn(
                                    j.cn_str,
                                    getattr(j, "old_en_str", None),
                                    j.en_str,
                                    tag=j.tag,
                                )
                                if j.sql_id is not None and self.dictionary is not None:
                                    self.dictionary.update(
                                        j.sql_id,
                                        j.en_str,
                                        j.cn_str,
                                        proofread=bool(j.is_proofread),
                                        tag=j.tag,
                                    )
                                    j.tag_sync_required = False
                            else:
                                aligned_cn, aligned = self.__align_cn_tags_to_en_by_position(
                                    j.cn_str,
                                    j.en_str,
                                )
                                if aligned:
                                    j.cn_str = aligned_cn
                                    if j.sql_id is not None and self.dictionary is not None:
                                        self.dictionary.update(
                                            j.sql_id,
                                            j.en_str,
                                            j.cn_str,
                                            proofread=bool(j.is_proofread),
                                            tag=j.tag,
                                        )
                                    obj = obj.replace(f'{{!@ {job_id}}}', j.cn_str)
                                    break
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
                # try:
                if (k != "uuid"):
                    new_v, ok = self.__replace_jobs(v)
                    if ok:
                        obj[k] = new_v
                else:
                    # 临时在这里特殊处理foundry-items.json和foundry-optinalfeatures.json中的uuid特殊格式
                    # ev = ''
                    # for j in self.done_jobs:
                    #     if j.cn_str == new_v:
                    #         ev = j.en_str
                    #         break
                    # if ev != '':
                    #     new_v = ev
                    new_v = v
                    match_k, match_v,_ = parse_foundry_items_uuid_format(new_v)
                    if (len(match_k) > 0):
                        for mk,mv in zip(match_k, match_v):
                            cn_v,ok = self.__get(mv, mk)
                            if not ok:
                                logger.error(f'{new_v} generated an exception: {mv}')
                                continue
                            new_v = new_v.replace(mv, cn_v)
                    obj[k] = new_v
                    
                # except Exception as exc:
                    # logger.error(f'{k} generated an exception: {exc}')
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
