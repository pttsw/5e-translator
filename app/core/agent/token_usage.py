"""Provider-neutral token accounting for file translation runs."""


def empty_token_usage():
    return {
        'input_tokens': 0,
        'output_tokens': 0,
        'total_tokens': 0,
        'cache_read_tokens': 0,
        'cache_creation_tokens': 0,
        'input_available': False,
        'output_available': False,
        'total_available': False,
        'cache_read_available': False,
        'cache_creation_available': False,
    }


def _integer(*values):
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            pass
    return None


def add_response_usage(target, response):
    usage = getattr(response, 'usage_metadata', None) or {}
    metadata = getattr(response, 'response_metadata', None) or {}
    raw = metadata.get('token_usage') or metadata.get('usage') or {}
    usage = usage if isinstance(usage, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    details = usage.get('input_token_details') or {}
    raw_details = raw.get('prompt_tokens_details') or {}
    details = details if isinstance(details, dict) else {}
    raw_details = raw_details if isinstance(raw_details, dict) else {}
    values = {
        'input_tokens': _integer(usage.get('input_tokens'), raw.get('prompt_tokens')),
        'output_tokens': _integer(usage.get('output_tokens'), raw.get('completion_tokens')),
        'total_tokens': _integer(usage.get('total_tokens'), raw.get('total_tokens')),
        'cache_read_tokens': _integer(details.get('cache_read'), raw_details.get('cached_tokens'),
                                      raw.get('prompt_cache_hit_tokens'), raw.get('cache_read_input_tokens')),
        'cache_creation_tokens': _integer(details.get('cache_creation'), raw.get('prompt_cache_miss_tokens'),
                                          raw.get('cache_creation_input_tokens')),
    }
    if values['total_tokens'] is None and values['input_tokens'] is not None and values['output_tokens'] is not None:
        values['total_tokens'] = values['input_tokens'] + values['output_tokens']
    for key, value in values.items():
        if value is not None:
            target[key] += value
            target[key.replace('_tokens', '_available')] = True
    return target


def merge_token_usage(target, source):
    if not isinstance(source, dict):
        return target
    for key in ('input_tokens', 'output_tokens', 'total_tokens', 'cache_read_tokens', 'cache_creation_tokens'):
        available = key.replace('_tokens', '_available')
        if source.get(available):
            target[key] += max(0, int(source.get(key, 0)))
            target[available] = True
    return target


def public_token_usage(usage):
    result = {}
    for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'total_tokens'):
        available = key.replace('_tokens', '_available')
        result[key] = usage.get(key, 0) if usage.get(available) else '未提供'
    return result

