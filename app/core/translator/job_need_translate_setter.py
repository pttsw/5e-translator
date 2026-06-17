from langchain_core.runnables import Runnable
from ..utils.file_work_info import FileWorkInfo
from config import logger
from app.core.utils import Job, replace_cn_pattern, need_translate_str, check_prefix, check_suffix, parse_custom_format, reset_tags_index, format_llm_msg, parse_foundry_items_uuid_format, get_tag_display_text
from datetime import datetime
from typing import Optional


class JobNeedTranslateSetter(Runnable):
    def __init__(self):
        self.done_jobs:list[Job] = []
        self.byhand = False
        self.force = False
    def invoke(self, input, config = None, **kwargs):
        inputs = [input] if isinstance(input, str) else input
        self.byhand = config['metadata'].get('byhand', False)
        self.force = config['metadata'].get('force', False)
        self.force_title = config['metadata'].get('force_title', False)
        for res in input:
            self.done_jobs = res.job_list
            for job in res.job_list:
                if self.__need_translate_job(job):
                    job.need_translate = True
            yield res
            
    def __need_translate_job(self, job: Job) -> (bool):
        """
        检查是否需要翻译
        """
        # cn_str = job.cn_str if job.cn_str else ""
        if getattr(job, "tag_sync_required", False) and job.cn_str is not None and job.sql_id is not None:
            return not self.__can_sync_tag_only_job(job)
        # 1.已经校对过的肯定不需要翻译了
        if job.is_proofread:
            return False
        # 3.如果强制翻译标题，且当前术语是标题，则需要翻译
        # if self.force_title:
        #     if len(job.current_names) > 0 and job.en_str == job.current_names[-1][0]:
        #         # 为了防止原有父级被认为是标题，这里清空原有父级
        #         job.current_names = job.current_names[:-1]
        #         return True
        #     else:
        #         # 此模式下，非标题的都不翻译
        #         return False
        # 2.如果没有中文，说明没有翻译过。如果是手动模式或强制翻译模式，则没校对的且包含术语的，也需要翻译
        if job.cn_str is None or (self.byhand == True or self.force == True):
            logger.debug(f"job.modified_at: {job.modified_at}")
            # 如果modified_at比2025-11-13晚，说明刚翻译过，不需要翻译
            try:
                # 创建参考日期
                # reference_date = datetime(2025, 11, 13)
                # reference_date = datetime(2025, 12, 15)
                reference_date = datetime(2026, 1, 13)
                
                # 确保job.modified_at是datetime对象
                if isinstance(job.modified_at, datetime):
                    if job.modified_at > reference_date:
                        return False
                elif isinstance(job.modified_at, str):
                    # 尝试将字符串转换为datetime对象
                    try:
                        modified_date = datetime.fromisoformat(job.modified_at.replace('Z', '+00:00'))
                        if modified_date > reference_date:
                            return False
                    except ValueError:
                        # 如果日期格式无法解析，继续后续检查
                        logger.debug(f"无法解析日期格式: {job.modified_at}")
            except Exception as e:
                logger.error(f"日期比较错误: {str(e)}")
            
            return need_translate_str(job.en_str) and (job.cn_str is None or job.cn_str != job.en_str)
        # 原来的中文可能存在 {@怪兽 xxx}的情况，需要替换回正确的英文格式{@bestry xxx}
        job.cn_str = replace_cn_pattern(job.cn_str, job.en_str)
        matched_cn_ok = self.__replace_sub_jobs(job.cn_str, job.en_str)
        if matched_cn_ok:
            return False
        
        # print(job.en_str)
        return True

    def __can_sync_tag_only_job(self, job: Job) -> bool:
        old_tags, old_values, old_valid = parse_custom_format(
            getattr(job, "old_en_str", "") or "", False)
        new_tags, new_values, new_valid = parse_custom_format(job.en_str, False)
        cn_tags, cn_values, cn_valid = parse_custom_format(job.cn_str, False)
        if not old_valid or not new_valid or not cn_valid:
            return False

        old_cn_links = []
        if len(cn_values) == len(old_values):
            old_cn_links = [
                {
                    "old_display": get_tag_display_text(old_value, old_tag),
                    "cn_display": get_tag_display_text(cn_value, cn_tag),
                }
                for old_tag, old_value, cn_tag, cn_value in zip(
                    old_tags, old_values, cn_tags, cn_values)
            ]

        old_pairs = list(zip(old_tags, old_values))
        checked = False
        for new_tag, new_value in zip(new_tags, new_values):
            if (new_tag, new_value) in old_pairs:
                old_pairs.remove((new_tag, new_value))
                continue
            checked = True
            display_text = get_tag_display_text(new_value, new_tag)
            if any(link["old_display"] == display_text for link in old_cn_links):
                continue
            if display_text and display_text in job.cn_str:
                continue
            known_cn = self.__get_known_translation(display_text, new_tag)
            if known_cn and known_cn in job.cn_str:
                continue
            logger.info(
                f"仅标签变化条目无法安全同步，降级为LLM翻译: "
                f"sql_id={job.sql_id}, display={display_text}, en={job.en_str}"
            )
            return False
        return checked

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
        return fallback
    
    def __replace_sub_jobs(self, cn_str: str, en_str: Optional[str] = None):
        """
        处理@{creature owlbear|phb}类似的情况
        将@{creature owlbear|phb}替换为中文
        """
        if not isinstance(cn_str, str):
            return False

        # 处理只有中文的情况
        if en_str is None:
            # 处理{@tag value} {@tag}自定义格式
            cn_match_k, cn_match_v, is_valid = parse_custom_format(cn_str)
            return is_valid

        # 处理既有英文又有中文的情况
        # 先分别找到中文和英文的自定义格式
        en_match_k, en_match_v, en_is_valid = parse_custom_format(
            en_str, False)
        cn_match_k, cn_match_v, cn_is_valid = parse_custom_format(
            cn_str, False)
        if (not en_is_valid) or (not cn_is_valid) or (len(cn_match_v) != len(en_match_v)):
            return False

        ok, en_match_k, en_match_v, cn_match_k, cn_match_v = reset_tags_index(
            en_match_k, en_match_v, cn_match_k, cn_match_v)
        if not ok:
            return False
        # en_match_k, en_match_v, cn_match_k, cn_match_v = tag_duplicate_removal(en_match_k, en_match_v, cn_match_k, cn_match_v)
        for i, ck in enumerate(cn_match_k):
            cv = cn_match_v[i]
            ek = en_match_k[i]
            ev = en_match_v[i]
            if ev == "":
                continue
            elif cv == "":
                return False

            # 找出嵌套的子value
            # TODO 这里可以优化，直接输出是否有子value
            # _, sub_en_match_v, _ = parse_custom_format(ev, False)
            # new_v = ev
            # # 如果有嵌套的子value
            # if len(sub_en_match_v) > 0:
            #     ok = self.__replace_sub_jobs(cv, ev)
                # if need_translate_str(ev):
                #     new_v = ev
                #     sek, sev, svalid = parse_custom_format(sub_new_v, False)
                #     cek, cev, cvalid = parse_custom_format(new_v, False)
                #     if svalid and cvalid and len(cev) == len(sev):
                #         ok, sek, sev, cek, cev = reset_tags_index(
                #             sek, sev, cek, cev)
                #         if not ok:
                #             return False
        return True
