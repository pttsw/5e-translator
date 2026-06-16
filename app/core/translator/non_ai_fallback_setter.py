from langchain_core.runnables import Runnable

from config import logger


class NonAiFallbackSetter(Runnable):
    """Use English text for database misses before JSON tag replacement."""

    def invoke(self, input, config=None, **kwargs):
        for file_info in input:
            fallback_count = 0
            for job in file_info.job_list:
                if job.cn_str is None:
                    job.cn_str = job.en_str
                    fallback_count += 1
            logger.info(
                f"AI已关闭，{file_info.json_path} 使用英文回退的条目数: {fallback_count}"
            )
            yield file_info
