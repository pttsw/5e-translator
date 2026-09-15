"""Two read-only aggregation tools with stable ranking and bounded results."""
from copy import deepcopy
from difflib import SequenceMatcher
from threading import RLock
import json
import time

from .references import ReferenceStore, normalize, digest


class TemporaryTermStore:
    def __init__(self):
        self.terms = {}
        self.processors = []
        self.lock = RLock()

    def clear(self):
        with self.lock:
            self.terms.clear()
            self.processors.clear()

    def bind(self, processor):
        with self.lock:
            self.processors.append(processor)

    def add(self, term):
        with self.lock:
            self.terms[(term.en, term.cn, term.category or '')] = term

    def finish(self, processor):
        with self.lock:
            self.snapshot()
            if processor in self.processors:
                self.processors.remove(processor)

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        for processor in self.processors:
            with processor.cache_lock:
                terms = list(processor.temple_terms)
            for term in terms:
                self.terms[(term.en, term.cn, term.category or '')] = term
        return [dict(en=en, cn=cn, category=category, entity_id=None, source_type='temporary_term',
                     source_id='temporary:' + digest(f'{en}:{cn}:{category}')[:16],
                     evidence_location={'location': 'current_run'}, parse_method='structured')
                for en, cn, category in self.terms]


def category_matches(actual, requested):
    if not requested:
        return True
    aliases = {'bestiary': 'monster', 'creature': 'monster'}
    actual = aliases.get(actual.strip(), actual.strip())
    requested = aliases.get(requested.strip(), requested.strip())
    return actual in {'', 'unknown', 'normal', requested}


def score_record(record, query, *, entity=False, category=None):
    if entity:
        direct = [record['name_en'], record['name_cn']]
        names = direct + record.get('aliases', [])
    else:
        direct = names = [record['en']]
    query = normalize(query)
    if query in [normalize(name) for name in direct if name]:
        quality, match = 1.0, 'exact'
    elif query in [normalize(name) for name in names if name]:
        quality, match = .8, 'alias'
    else:
        similarity = max((SequenceMatcher(None, query, normalize(name)).ratio() for name in names if name), default=0)
        if similarity < .85:
            return None
        quality, match = .5 * similarity, 'fuzzy'
    if record.get('parse_method') == 'llm_fallback':
        quality *= .9
    category_reason = 'category_unspecified'
    if category:
        actual = record.get('entity_type' if entity else 'category', '')
        category_reason = 'category_exact' if actual == category else 'category_unknown_or_alias'
        if actual != category:
            quality *= .95
    if entity:
        ranks = {'session_reference': (1, .85, 1.0), 'buquanshu': (2, .65, .84)}
    else:
        ranks = {'term_table': (1, .95, 1.0), 'session_reference': (2, .85, .94),
                 'buquanshu': (3, .70, .84), 'temporary_term': (4, .55, .69)}
    rank, low, high = ranks[record['source_type']]
    return dict(record, source_rank=rank, confidence=round(low + (high - low) * quality, 4),
                match_type=match, confidence_reasons=[f'source_priority:{rank}', match,
                    'engineering_score_not_probability', category_reason, 'evidence:' + record.get('parse_method', 'structured')])


class ReferenceTools:
    MAX_QUERIES = 10

    def __init__(self, store=None, *, term_reader=None, searcher_factory=None,
                 max_chars=24000, min_confidence=.55, max_pages=3):
        from app.core.database.term_reader import TermTableReader
        from .buquanshu_search import BuquanshuSearcher
        self.store = store if store is not None else ReferenceStore()
        self.term_reader = term_reader if term_reader is not None else TermTableReader()
        self.searcher_factory = searcher_factory or BuquanshuSearcher
        self.temporary = TemporaryTermStore()
        self.max_chars = max_chars
        self.min_confidence = min_confidence
        self.max_pages = max_pages
        self._remote_cache = {}
        self._term_cache = {}
        self._index_version = -1
        self._indexes = {}
        self.audit = []
        self.lock = RLock()

    def start_run(self):
        with self.lock:
            self.temporary.clear()
            self.audit.clear()
            self._term_cache.clear()

    def end_run(self):
        with self.lock:
            self.temporary.clear()

    def clear(self):
        with self.lock:
            self.store.clear()
            self._remote_cache.clear()
            self._term_cache.clear()
            self.temporary.clear()
            self.audit.clear()

    def _indexed(self, kind, query):
        if self._index_version != self.store.version:
            self._indexes = {'entities': {}, 'translations': {}}
            for record_kind in self._indexes:
                for record in self.store.records(record_kind):
                    names = ([record['name_en'], record['name_cn'], *record['aliases']]
                             if record_kind == 'entities' else [record['en']])
                    for name in set(normalize(name) for name in names if name):
                        self._indexes[record_kind].setdefault(name, []).append(record)
            self._index_version = self.store.version
        found = self._indexes[kind].get(normalize(query))
        return found if found is not None else self.store.records(kind)

    def _remote(self, query, errors, deadline):
        key = normalize(query)
        if key in self._remote_cache:
            errors.extend(self._remote_cache[key])
            return
        local_errors = []
        try:
            with self.searcher_factory() as searcher:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('tool_budget_exceeded')
                result = searcher.search(query, page_size=10, timeout=min(10, remaining))
                # Prefer descriptions over quick-reference lists; never invent a detail URL.
                pages = sorted(result['results'], key=lambda item: (
                    '速查' in item.get('category', '') or '列表' in item.get('source_path', ''),
                    '详述' not in item.get('source_path', ''), item.get('url', '')))
                for page in pages[:self.max_pages]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        local_errors.append({'source_type': 'buquanshu', 'code': 'tool_budget_exceeded'})
                        break
                    url = page.get('url')
                    if not url:
                        continue
                    if ('buquanshu', url) in self.store.documents:
                        continue
                    try:
                        content = searcher.fetch_page(url, timeout=min(10, remaining))
                        if not content.get('complete'):
                            local_errors.append({'source_type': 'buquanshu', 'source_id': url, 'code': 'preview_only'})
                            continue
                        self.store.add(content['text'], url, source_type='buquanshu',
                                       blocks=content.get('blocks'), complete=True)
                    except Exception as exc:
                        local_errors.append({'source_type': 'buquanshu', 'source_id': url, 'code': type(exc).__name__})
        except Exception as exc:
            local_errors.append({'source_type': 'buquanshu', 'code': type(exc).__name__})
        self._remote_cache[key] = local_errors
        errors.extend(local_errors)

    def _validate(self, queries, limit):
        if (not isinstance(queries, list) or not 1 <= len(queries) <= self.MAX_QUERIES
                or any(not isinstance(q, str) or not q.strip() or len(q) > 200 for q in queries)):
            raise ValueError('queries_must_be_1_to_10_nonempty_strings_up_to_200_chars')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('limit_must_be_1_to_10')
        return list(dict.fromkeys(q.strip() for q in queries))

    def _finish(self, response, tool, started):
        # Do not cut an entity's body; drop whole results when the tool budget is exhausted.
        while response['results'] and len(json.dumps(response, ensure_ascii=False)) > self.max_chars:
            response['results'].pop()
            response['truncated'] = True
        while response['errors'] and len(json.dumps(response, ensure_ascii=False)) > self.max_chars:
            response['errors'].pop()
            response['truncated'] = True
        self.audit.append({'tool': tool, 'query': response['query'],
                           'source_ids': sorted({r['source_id'] for r in response['results']}),
                           'elapsed_ms': round((time.monotonic() - started) * 1000),
                           'truncated': response['truncated'], 'errors': deepcopy(response['errors'])})
        del self.audit[:-1000]
        return response

    @staticmethod
    def _dedupe(records, entity=False):
        unique = {}
        for record in records:
            key = ((record['entity_id'],) if entity else (normalize(record['en']), record['cn'],
                   record['category'], record['source_type']))
            if key not in unique:
                unique[key] = dict(record, evidence_sources=list(record.get('evidence_sources', [])))
            evidence = {'source_id': record['source_id'], 'evidence_location': record['evidence_location']}
            if evidence not in unique[key]['evidence_sources'] and len(unique[key]['evidence_sources']) < 8:
                unique[key]['evidence_sources'].append(evidence)
        return list(unique.values())

    def lookup_translations(self, en_list, category=None, entity_id=None, limit_per_term=5):
        queries = self._validate(en_list, limit_per_term)
        response = dict(query=queries, results=[], truncated=False, errors=[])
        started = time.monotonic()
        with self.lock:
            for query in queries:
                if time.monotonic() - started > 30:
                    response['errors'].append({'code': 'tool_budget_exceeded'})
                    response['truncated'] = True
                    break
                candidates = []
                try:
                    if normalize(query) not in self._term_cache:
                        self._term_cache[normalize(query)] = self.term_reader(query)
                    rows = self._term_cache[normalize(query)]
                    response['truncated'] |= len(rows) > 200
                    for row in rows[:200]:
                        if not isinstance(row.get('cn'), str) or not row['cn'].strip():
                            continue
                        candidates.append(dict(en=row['en'], cn=row['cn'], category=row.get('category') or '',
                            entity_id=None, source_type='term_table', source_id='term:' + digest(json.dumps(row, sort_keys=True))[:20],
                            evidence_location={'table': 'term', 'en': row['en'], 'source': row.get('source', '')}, parse_method='structured'))
                except Exception as exc:
                    response['errors'].append({'source_type': 'term_table', 'code': type(exc).__name__})
                self._remote(query, response['errors'], started + 30)
                candidates.extend(self._indexed('translations', query))
                candidates.extend(self.temporary.snapshot())
                ranked = []
                for record in self._dedupe(candidates):
                    if not category_matches(record['category'], category):
                        continue
                    if entity_id and record.get('entity_id') not in (None, entity_id):
                        continue
                    scored = score_record(record, query, category=category)
                    if scored and scored['confidence'] >= self.min_confidence:
                        ranked.append(scored)
                variants = {}
                for record in ranked:
                    variants.setdefault(normalize(record['en']), set()).add(record['cn'])
                for record in ranked:
                    record.update(query=query, conflict=len(variants[normalize(record['en'])]) > 1)
                ranked.sort(key=lambda r: (-r['confidence'], r['match_type'] != 'exact', r['source_id']))
                response['truncated'] |= len(ranked) > limit_per_term
                response['results'].extend(ranked[:limit_per_term])
            return self._finish(response, 'lookup_translations', started)

    def lookup_entities(self, names, entity_type=None, limit_per_name=3):
        queries = self._validate(names, limit_per_name)
        response = dict(query=queries, results=[], truncated=False, errors=[])
        started = time.monotonic()
        with self.lock:
            for query in queries:
                if time.monotonic() - started > 30:
                    response['errors'].append({'code': 'tool_budget_exceeded'})
                    response['truncated'] = True
                    break
                def collect():
                    found = []
                    for record in self._dedupe(self._indexed('entities', query), entity=True):
                        if not record['complete'] or not category_matches(record['entity_type'], entity_type):
                            continue
                        scored = score_record(record, query, entity=True, category=entity_type)
                        if scored and scored['confidence'] >= self.min_confidence:
                            found.append(dict(scored, query=query))
                    return found
                ranked = collect()
                if len(ranked) < limit_per_name:
                    self._remote(query, response['errors'], started + 30)
                    ranked = collect()
                ranked.sort(key=lambda r: (-r['confidence'], r['match_type'] != 'exact', r['source_id']))
                response['truncated'] |= len(ranked) > limit_per_name
                response['results'].extend(ranked[:limit_per_name])
            return self._finish(response, 'lookup_entities', started)
