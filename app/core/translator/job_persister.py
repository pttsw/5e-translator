"""Shared persistence rule for completed translations."""


def persist_completed_job(dictionary, job, *, proofread=False):
    if job is None or not isinstance(job.cn_str, str):
        return False
    if job.sql_id is None:
        result = dictionary.put(
            job.en_str, job.cn_str, job.rel_path,
            proofread=proofread, tag=job.tag, job_context=job,
        )
        if isinstance(result, int) and not isinstance(result, bool):
            job.sql_id = result
    else:
        result = dictionary.update(
            job.sql_id, job.en_str, job.cn_str, proofread=proofread,
            tag=job.tag, rel_f=job.rel_path, job_context=job,
        )
    return bool(result)
