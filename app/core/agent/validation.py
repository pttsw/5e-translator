"""Fast, deterministic publication checks against the analyser template."""
from collections import Counter
import json
import re

PLACEHOLDER = re.compile(r'\{!@ ([^}]+)\}')


class ValidationError(ValueError):
    pass


def validate_translation(job):
    from app.core.utils import parse_custom_format
    en, cn = job.en_str, job.cn_str
    en_tags, _, en_ok = parse_custom_format(en, False)
    cn_tags, _, cn_ok = parse_custom_format(cn, False)
    if not en_ok or not cn_ok or Counter(en_tags) != Counter(cn_tags):
        raise ValidationError(f'tag_mismatch:{job.uid}')
    for text in (en, cn):
        depth = 0
        for char in text:
            depth += (char == '{') - (char == '}')
            if depth < 0:
                raise ValidationError(f'unbalanced_braces:{job.uid}')
        if depth:
            raise ValidationError(f'unbalanced_braces:{job.uid}')
    if en.count('{') != cn.count('{'):
        raise ValidationError(f'brace_mismatch:{job.uid}')
    if Counter(re.findall(r'\d+(?:\.\d+)?', en)) != Counter(re.findall(r'\d+(?:\.\d+)?', cn)):
        raise ValidationError(f'number_mismatch:{job.uid}')
    dice = r'(?i)(?<![a-z0-9_])\d*d\d+(?:\s*[+-]\s*\d+)?'
    normalize = lambda text: Counter(re.sub(r'\s+', '', d).lower() for d in re.findall(dice, text))
    if normalize(en) != normalize(cn):
        raise ValidationError(f'dice_mismatch:{job.uid}')


def validate_file(info):
    if info.cn_obj is None:
        raise ValidationError('missing_output')
    jobs = {job.uid: job for job in info.job_list}
    if len(jobs) != len(info.job_list):
        raise ValidationError('duplicate_uid')
    for job in info.job_list:
        if not isinstance(job.cn_str, str):
            raise ValidationError(f'unfinished_uid:{job.uid}')
        validate_translation(job)

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
    return {'passed': True, 'checks': ['json', 'terminal_jobs', 'uid', 'template_structure', 'unchanged_fields', 'tags', 'braces', 'numbers', 'dice']}


def remove_generated_names(source, target):
    """Native schema output omits analyser-added ENG_name; original fields survive."""
    if isinstance(source, dict) and isinstance(target, dict):
        if 'ENG_name' not in source:
            target.pop('ENG_name', None)
        for key in source.keys() & target.keys():
            remove_generated_names(source[key], target[key])
    elif isinstance(source, list) and isinstance(target, list):
        for before, after in zip(source, target):
            remove_generated_names(before, after)
