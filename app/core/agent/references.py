"""Evidence-backed document extraction and session-local reference storage."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TypedDict
import os
import re

from .commands import CommandError, contained

CJK = r'\u3400-\u9fff'


class TranslationCandidate(TypedDict, total=False):
    en: str
    cn: str
    category: str
    entity_id: str | None
    source_type: str
    source_id: str
    evidence_location: dict
    source_rank: int
    match_type: str
    confidence: float
    confidence_reasons: list[str]
    conflict: bool


class ReferenceEntity(TypedDict, total=False):
    entity_id: str
    entity_type: str
    name_en: str
    name_cn: str
    aliases: list[str]
    description_cn: str
    source_type: str
    source_id: str
    evidence_location: dict
    content_hash: str
    parse_method: str
    source_rank: int
    match_type: str
    confidence: float
    confidence_reasons: list[str]
    complete: bool


def normalize(text):
    return ' '.join(text.casefold().split())


def digest(text):
    return sha256(text.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ReferenceBlock:
    text: str
    start: int
    end: int
    level: int = 0
    location: str = ''


class ReferenceLoader(Protocol):
    def supports(self, path: Path) -> bool: ...
    def load(self, path: Path) -> list[ReferenceBlock]: ...


def text_blocks(text):
    blocks, offset = [], 0
    for line_no, line in enumerate(text.splitlines(keepends=True), 1):
        value = line.strip()
        heading = re.match(r'^(#{1,6})\s+(.+)', value)
        if value:
            start = offset + line.index(value)
            blocks.append(ReferenceBlock(value, start, start + len(value),
                                         len(heading[1]) if heading else 0, f'line:{line_no}'))
        offset += len(line)
    return blocks


class TextReferenceLoader:
    MAX_BYTES = 4 * 1024 * 1024

    def supports(self, path):
        return path.suffix.lower() in {'.txt', '.md', '.markdown'}

    def read(self, path):
        with path.open('rb') as handle:
            raw = handle.read(self.MAX_BYTES + 1)
        if len(raw) > self.MAX_BYTES:
            raise ValueError('reference_too_large')
        return raw.decode('utf-8-sig')

    def load(self, path):
        return text_blocks(self.read(path))


def bilingual_name(value):
    """Only explicit bilingual names, never infer translations from prose."""
    value = re.sub(r'^#{1,6}\s+', '', value).strip().strip('|').strip()
    value = re.sub(r'^\d+级[：:]\s*', '', value)
    if normalize(value).replace('|', ' ').strip() in {'english 中文', '英文 中文', 'en cn', 'name 名称'}:
        return None
    if len(value) > 150 or re.search(r'[。！？；!?;]', value):
        return None
    # Parentheses or table/delimiter forms; match whichever side contains English.
    match = re.fullmatch(r'(.+?)\s*[（(]([^（）()]+)[）)]', value)
    parts = match.groups() if match else re.split(r'\s*[|｜\t]\s*', value)
    if len(parts) == 2:
        a, b = [item.strip() for item in parts]
        if re.search(f'[{CJK}]', a) and not re.search(f'[{CJK}]', b) and re.search('[A-Za-z]', b):
            return b, a
        if re.search(f'[{CJK}]', b) and not re.search(f'[{CJK}]', a) and re.search('[A-Za-z]', a):
            return a, b
    # Chinese followed by English, including source formats with no separator.
    match = re.fullmatch(rf"([{CJK}][{CJK}·、：: —-]*?)\s*([A-Za-z][A-Za-z0-9 ’'(),:+&–—/-]*)", value)
    if match:
        return match[2].strip(), match[1].strip()
    match = re.fullmatch(rf"([A-Za-z][A-Za-z0-9 ’'(),:+&–—/-]*?)\s*([{CJK}][{CJK}·、 —-]*)", value)
    if match:
        return match[1].strip(), match[2].strip()
    return None


def infer_type(body):
    first = '\n'.join(body.splitlines()[1:2])
    if re.search(r'施法时间|[一二三四五六七八九十0-9]+环|(?:咒法|塑能|防护|变化|幻术|死灵|惑控|预言)戏法', first):
        return 'spell'
    if re.search(r'^(?:奇物|武器|护甲|魔杖|法杖|戒指|药水|卷轴)[，,（(]|稀有度', first, re.M):
        return 'item'
    if re.search(r'^(?:微型|小型|中型|大型|巨型|超巨型)|^(?:护甲等级|生命值)[：:]?$', first, re.M):
        return 'monster'
    if '属性值' in first:
        return 'background'
    if re.search(r'(?:起源|通用|传奇恩惠|战斗风格)专长|先决条件', first):
        return 'feat'
    return 'unknown'


class ReferenceContentProcessor:
    def __init__(self, fallback=None):
        # Optional one-shot structured extractor. Every returned span is rechecked.
        self.fallback = fallback

    def process(self, text, *, source_id, source_type='session_reference', blocks=None, complete=True):
        blocks = blocks if blocks is not None else text_blocks(text)
        content_hash = digest(text)
        entities, translations, boundaries = [], [], []
        pairs = {i: bilingual_name(block.text) for i, block in enumerate(blocks)}
        for i, block in enumerate(blocks):
            if re.match(r'^(?:={5,}|--- 帖主|URL:|帖主:|内容:)', block.text):
                boundaries.append((i, -1, False))
                continue
            pair = pairs[i]
            table_category = None
            cells = [cell.strip() for cell in block.text.strip('|').split('|')]
            if len(cells) == 3 and cells[2] in {'spell', 'item', 'monster', 'feat', 'background', 'class', 'unknown'}:
                pair = bilingual_name(' | '.join(cells[:2]))
                pairs[i] = pair
                table_category = cells[2]
            next_text = '\n'.join(b.text for b in blocks[i:i + 5])
            kind = infer_type(next_text)
            # Explicit headings preserve nesting; stat-block signatures delimit flat exports.
            is_entity = bool(block.level or (pair and kind != 'unknown'))
            if pair and not is_entity:
                following = blocks[i + 1].text if i + 1 < len(blocks) else ''
                starts_section = i == 0 or bool(re.match(r'^(?:内容:|--- 帖主|={5,})', blocks[i - 1].text))
                is_entity = bool(len(following) >= 30 and re.search('[。！？]', following)
                                 and starts_section and not re.search('[。.]$', block.text))
            if is_entity:
                boundaries.append((i, block.level or 2, True))
            if pair:
                en, cn = pair
                translations.append(dict(en=en, cn=cn, category=table_category or kind, entity_id=None,
                    source_type=source_type, source_id=source_id, content_hash=content_hash,
                    evidence_location={'start': block.start, 'end': block.end, 'location': block.location,
                                       'quote': block.text}, parse_method='structured' if block.level else 'rule'))
        for index, (i, level, is_entity) in enumerate(boundaries):
            if not is_entity:
                continue
            stop = next((j for j, next_level, _ in boundaries[index + 1:] if next_level <= level), len(blocks))
            start = blocks[i].start
            end = blocks[stop].start if stop < len(blocks) else len(text)
            body = text[start:end].strip()
            # A name/table row alone is not a complete description.
            content = '\n'.join(b.text for b in blocks[i + 1:stop])
            has_body = bool(re.search(f'[{CJK}]', content) and re.search('[。！？]', content))
            pair = pairs[i]
            title = re.sub(r'^#{1,6}\s+', '', blocks[i].text)
            en, cn = pair or (('', title) if re.search(f'[{CJK}]', title) else (title, ''))
            entity_id = digest(f'{source_id}:{content_hash}:{start}:{end}')[:24]
            entity = dict(entity_id=entity_id, entity_type=infer_type(body), name_en=en, name_cn=cn,
                          aliases=[name for name in (en, cn, title) if name], description_cn=body,
                          source_type=source_type, source_id=source_id, content_hash=content_hash,
                          evidence_location={'start': start, 'end': end, 'location': blocks[i].location},
                          parse_method='structured' if blocks[i].level else 'rule', complete=bool(complete and has_body))
            entities.append(entity)
            for candidate in translations:
                if candidate['evidence_location']['start'] == start:
                    candidate.update(entity_id=entity_id, category=entity['entity_type'])
        # Repeated table/title names may borrow a type only when this document
        # contains an unambiguous matching entity (not merely an enclosing entity).
        for candidate in translations:
            matches = [e for e in entities if (e['name_en'], e['name_cn']) == (candidate['en'], candidate['cn'])]
            kinds = {e['entity_type'] for e in matches if e['entity_type'] != 'unknown'}
            if candidate['category'] == 'unknown' and len(kinds) == 1:
                candidate['category'] = next(iter(kinds))
        warnings = []
        if not entities and self.fallback:
            try:
                result = self.fallback(text)
                for item in result.get('entities', [])[:100]:
                    start, end = item['start'], item['end']
                    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
                        continue
                    quote = text[start:end]
                    en, cn = item.get('name_en', ''), item.get('name_cn', '')
                    if not (en or cn) or any(name and name not in quote for name in (en, cn)):
                        continue
                    if item.get('description_cn') != quote:
                        continue
                    entity_id = digest(f'{source_id}:{content_hash}:{start}:{end}')[:24]
                    entities.append(dict(entity_id=entity_id, entity_type='unknown', name_en=en, name_cn=cn,
                        aliases=[name for name in (en, cn) if name], description_cn=quote, source_type=source_type,
                        source_id=source_id, content_hash=content_hash, evidence_location={'start': start, 'end': end},
                        parse_method='llm_fallback', complete=False))
                    # Co-occurrence alone is insufficient evidence for a translation pair.
                    if en and cn and any(bilingual_name(line) == (en, cn) for line in quote.splitlines()):
                        translations.append(dict(en=en, cn=cn, category='unknown', entity_id=entity_id,
                            source_type=source_type, source_id=source_id, content_hash=content_hash,
                            evidence_location={'start': start, 'end': end, 'quote': quote}, parse_method='llm_fallback'))
            except Exception:
                warnings.append('fallback_failed')
        if not any(entity['complete'] for entity in entities):
            warnings.append('uncertain_entity_boundaries')
        unique = {}
        for candidate in translations:
            key = (normalize(candidate['en']), candidate['cn'], candidate['category'])
            evidence = {'source_id': source_id, 'evidence_location': candidate['evidence_location']}
            if key not in unique:
                unique[key] = dict(candidate, evidence_sources=[])
            unique[key]['evidence_sources'].append(evidence)
            if candidate['entity_id'] and not unique[key]['entity_id']:
                unique[key]['entity_id'] = candidate['entity_id']
        translations = list(unique.values())
        for candidate in translations:
            candidate.update(source_rank=2 if source_type == 'session_reference' else 3,
                             confidence=.90 if source_type == 'session_reference' else .77,
                             match_type='extracted', confidence_reasons=['explicit_bilingual_evidence'])
            candidate['conflict'] = len({other['cn'] for other in translations
                                         if normalize(other['en']) == normalize(candidate['en'])}) > 1
        for entity in entities:
            entity.update(source_rank=1 if source_type == 'session_reference' else 2,
                          confidence=.9 if source_type == 'session_reference' else .75,
                          match_type='extracted', confidence_reasons=['source_document_evidence'])
        return {'entities': entities, 'translations': translations, 'warnings': warnings}


class ReferenceStore:
    """One instance per session. Updates replace a file's previous version atomically."""
    def __init__(self, processor=None):
        self.processor = processor or ReferenceContentProcessor()
        self.documents = {}
        self.version = 0

    def add(self, text, source_id, *, source_type='session_reference', blocks=None, complete=True):
        key = (source_type, source_id)
        content_hash = digest(text)
        if key in self.documents and self.documents[key]['content_hash'] == content_hash:
            return False
        size = sum(len(doc['text'].encode('utf-8')) for doc_key, doc in self.documents.items() if doc_key != key)
        if size + len(text.encode('utf-8')) > 32 * 1024 * 1024 or (key not in self.documents and len(self.documents) >= 100):
            raise ValueError('session_reference_budget_exceeded')
        result = self.processor.process(text, source_id=source_id, source_type=source_type,
                                        blocks=blocks, complete=complete)
        self.documents[key] = dict(result, text=text, content_hash=content_hash,
                                   source_id=source_id, source_type=source_type,
                                   index_id=digest(f'{source_type}:{source_id}:{content_hash}')[:24])
        self.version += 1
        return True

    def records(self, kind):
        return [record for document in self.documents.values() for record in document[kind]]

    def summary(self):
        return [dict(index_id=doc['index_id'], source_id=doc['source_id'], source_type=doc['source_type'],
                     content_hash=doc['content_hash'], entities=len(doc['entities']),
                     translations=len(doc['translations']), warnings=doc['warnings'])
                for doc in self.documents.values()]

    def clear(self):
        self.documents.clear()
        self.version += 1

    def load_path(self, value, roots):
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = roots[0] / path
        def validate(candidate):
            resolved = candidate.resolve(strict=True)
            if not any(contained(resolved, root) for root in roots):
                raise CommandError('参考路径超出允许目录')
            if not resolved.is_file() and not resolved.is_dir():
                raise CommandError('参考路径必须是普通文件或目录')
            return resolved
        try:
            path = validate(path)
        except (OSError, RuntimeError) as exc:
            raise CommandError('参考路径不存在或无法解析') from exc
        paths = [path] if path.is_file() else sorted(Path(folder) / name for folder, _, names in os.walk(path) for name in names)
        report = dict(success=0, skipped=0, failed=0, unsupported=[], needs_ocr=[], errors=[])
        loader = TextReferenceLoader()
        for item in paths:
            try:
                item = validate(item)
                if not loader.supports(item):
                    report['unsupported'].append(str(item))
                    report['skipped'] += 1
                    continue
                changed = self.add(loader.read(item), str(item))
                report['success' if changed else 'skipped'] += 1
            except Exception as exc:
                report['failed'] += 1
                report['errors'].append({'source_id': str(item), 'code': type(exc).__name__})
        report['references'] = self.summary()
        report['entities'] = len(self.records('entities'))
        report['translations'] = len(self.records('translations'))
        report['uncertain_boundaries'] = [doc['source_id'] for doc in report['references']
                                          if 'uncertain_entity_boundaries' in doc['warnings']]
        return report
