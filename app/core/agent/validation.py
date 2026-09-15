"""Minimal publication gate for A; isolated 5etools regression belongs to C."""
import json
import re

PLACEHOLDER = re.compile(r'\{!@ ([^}]+)\}')


class ValidationError(ValueError):
    pass


def validate_file(info):
    if info.cn_obj is None:
        raise ValidationError('missing_output')
    jobs = {job.uid: job for job in info.job_list}
    if len(jobs) != len(info.job_list):
        raise ValidationError('duplicate_uid')
    for job in info.job_list:
        if not isinstance(job.cn_str, str):
            raise ValidationError(f'unfinished_uid:{job.uid}')

    def walk(before, after):
        if type(before) is not type(after):
            raise ValidationError('changed_field_type')
        if isinstance(before, dict):
            if before.keys() != after.keys():
                raise ValidationError('changed_keys')
            for key in before:
                walk(before[key], after[key])
        elif isinstance(before, list):
            if len(before) != len(after):
                raise ValidationError('changed_array_length')
            for a, b in zip(before, after):
                walk(a, b)
        elif isinstance(before, str) and PLACEHOLDER.search(before):
            for uid in PLACEHOLDER.findall(before):
                if uid not in jobs:
                    raise ValidationError('unknown_uid')
            if PLACEHOLDER.search(after):
                raise ValidationError('unfilled_uid')
            expected = PLACEHOLDER.sub(lambda match: jobs[match[1]].cn_str, before)
            if after != expected:
                raise ValidationError('uid_translation_mismatch')
        elif before != after:
            raise ValidationError('changed_non_translation_field')

    walk(info.json_obj, info.cn_obj)
    serialized = json.dumps(info.cn_obj, ensure_ascii=False, allow_nan=False)
    if PLACEHOLDER.search(serialized):
        raise ValidationError('unfilled_uid')
    json.loads(serialized)
    return {'passed': True, 'checks': ['json', 'terminal_jobs', 'uid', 'template_structure', 'unchanged_fields']}
