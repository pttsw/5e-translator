"""Read-only analyser dictionary with explicit, location-aware reuse decisions."""
from copy import deepcopy


class ConflictDictionary:
    def __init__(self, dictionary):
        self.dictionary = dictionary
        self.conflicts = {}
        self.cache = {}

    def __getattr__(self, name):
        return getattr(self.dictionary, name)

    def get(self, k, rel_f='', load_from_sql=False, ignore_case=False, tag='',
            correct_tag_from_db=False, job_context=None):
        key = (k.casefold(), rel_f)
        if key not in self.cache:
            self.cache[key] = self.dictionary.get_reuse_candidates(k, rel_f)
        rows = self.cache[key]
        rows = [r for r in rows if isinstance(r.get('cn'), str) and
                (r['en'].casefold() == k.casefold() if ignore_case else r['en'] == k)]
        selected = []
        for field in ('uid', 'key_path'):
            value = getattr(job_context, field, '')
            if value:
                selected = [r for r in rows if any(u.get(field) == value for u in r['usages'])]
                if selected:
                    break
        if not selected:
            selected = [r for r in rows if (r.get('category') or '') == (tag or '')]
        if len({r['cn'] for r in selected}) > 1:
            uid = getattr(job_context, 'uid', '')
            if uid:
                self.conflicts[uid] = {'uid': uid, 'key_path': getattr(job_context, 'key_path', ''),
                    'entity_path': getattr(job_context, 'entry_path', '') or getattr(job_context, 'context_key', ''),
                    'candidates': [{k: r.get(k) for k in ('sql_id', 'cn', 'category', 'proofread')} for r in selected]}
            return None
        if not selected:
            return None
        return deepcopy(sorted(selected, key=lambda r: (-bool(r.get('proofread')), r['sql_id']))[0])

    def get_bunch(self, keys, tags, rel_f, ignore_case=False, correct_tag_from_db=False, job_contexts=None):
        result = {}
        for en, tag, job in zip(keys, tags, job_contexts or [None] * len(keys)):
            bean = self.get(en, rel_f, True, ignore_case, tag, correct_tag_from_db, job)
            if bean:
                result[(en, tag, job.uid) if job is not None else (en, tag)] = bean
        return result

    def get_tag_only_update_match(self, en, tag=''):
        # A tag-only fallback must not bypass ambiguity classification.
        return None
