#!/usr/bin/env python3
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from bs4 import BeautifulSoup

os.environ.setdefault("SKIP_APP_BOOTSTRAP", "1")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.translator.siliconflow_adapter import SiliconFlowAdapter
from app.core.utils.utils import (
    check_skip_key,
    format_llm_msg,
    need_translate_str,
    replace_cn_pattern,
)
from app.core.utils import TranslatorStatus
from config import DS_KEY


TAG_RE = re.compile(r"{@([^ }\n]+) ([^{}]+)}")
ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9'&()\-+,./:; ]{1,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WHITESPACE_RE = re.compile(r"\s+")
TAG_VALUE_FALLBACKS = {
    ("condition", "prone"): "倒地",
    ("condition", "charmed"): "魅惑",
    ("condition", "frightened"): "恐慌",
    ("condition", "stunned"): "震慑",
    ("condition", "poisoned"): "中毒",
    ("condition", "paralyzed"): "麻痹",
    ("condition", "unconscious"): "失能",
    ("skill", "acrobatics"): "杂技",
    ("skill", "animal handling"): "驯兽",
    ("skill", "arcana"): "奥秘",
    ("skill", "athletics"): "运动",
    ("skill", "deception"): "欺瞒",
    ("skill", "history"): "历史",
    ("skill", "insight"): "洞悉",
    ("skill", "intimidation"): "威吓",
    ("skill", "investigation"): "调查",
    ("skill", "medicine"): "医药",
    ("skill", "nature"): "自然",
    ("skill", "perception"): "察觉",
    ("skill", "performance"): "表演",
    ("skill", "persuasion"): "游说",
    ("skill", "religion"): "宗教",
    ("skill", "sleight of hand"): "巧手",
    ("skill", "stealth"): "隐匿",
    ("skill", "survival"): "求生",
}
QUALIFIER_RE = re.compile(
    r"\b(archtempered|tempered|adult|baby|juvenile|young|ancient|old|great|at\.)\b",
    re.IGNORECASE,
)


def norm_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text or "").strip()


def norm_key(text: str) -> str:
    text = norm_text(text).lower()
    text = text.replace("–", "-").replace("—", "-")
    return text.strip(" .,:;!?\"'`()[]{}")


def alias_keys(text: str) -> List[str]:
    base = norm_key(text)
    if not base:
        return []
    aliases = [base]
    stripped = norm_key(QUALIFIER_RE.sub(" ", base))
    if stripped and stripped not in aliases:
        aliases.append(stripped)
    no_paren = norm_key(re.sub(r"\([^)]*\)", " ", base))
    if no_paren and no_paren not in aliases:
        aliases.append(no_paren)
    return aliases


def extract_tag_values(text: str) -> List[str]:
    values = []
    for _, raw in TAG_RE.findall(text or ""):
        values.append(raw.split("|")[0].strip())
    return values


def looks_like_json_object(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith("{") and stripped.endswith("}")


@dataclass
class PageRef:
    path: str
    title_cn: str
    title_en: str
    text: str
    english_terms: set[str] = field(default_factory=set)
    bilingual_map: Dict[str, str] = field(default_factory=dict)
    search_blob: str = ""


@dataclass
class Job:
    job_id: str
    path: str
    key: str
    en_text: str
    breadcrumbs: List[str]
    page_hint: Optional[str]


class MhmmChmIndex:
    def __init__(self, root: Path):
        self.root = root
        self.pages: List[PageRef] = []
        self.page_by_title_en: Dict[str, PageRef] = {}
        self.global_map: Dict[str, str] = {}

    def build(self) -> None:
        html_files = sorted(
            list(self.root.rglob("*.htm")) + list(self.root.rglob("*.html"))
        )
        for path in html_files:
            page = self._parse_page(path)
            if page is None:
                continue
            self.pages.append(page)
            if page.title_en:
                self.page_by_title_en[norm_key(page.title_en)] = page
            for en, cn in page.bilingual_map.items():
                self.global_map.setdefault(en, cn)

    def _parse_page(self, path: Path) -> Optional[PageRef]:
        raw = path.read_bytes()
        html = raw.decode("gb18030", "ignore")
        soup = BeautifulSoup(html, "html.parser")
        text = "\n".join(x.strip() for x in soup.stripped_strings if x.strip())
        if not text:
            return None

        h1 = soup.find("h1")
        h1_text = norm_text(h1.get_text(" ", strip=True) if h1 else "")
        title_cn, title_en = self._split_h1(h1_text)
        bilingual_map = self._extract_bilingual_map(text)
        english_terms = set(bilingual_map.keys())
        for phrase in ASCII_RE.findall(text):
            phrase = norm_text(phrase)
            if 2 <= len(phrase) <= 120:
                english_terms.add(norm_key(phrase))
        page = PageRef(
            path=str(path.relative_to(self.root)),
            title_cn=title_cn,
            title_en=title_en,
            text=text,
            bilingual_map=bilingual_map,
            english_terms=english_terms,
            search_blob=norm_key(text),
        )
        return page

    def _split_h1(self, h1_text: str) -> tuple[str, str]:
        if not h1_text:
            return "", ""
        matches = list(ASCII_RE.finditer(h1_text))
        if not matches:
            return norm_text(h1_text), ""
        last = matches[-1]
        en = norm_text(h1_text[last.start():])
        cn = norm_text(h1_text[:last.start()])
        return cn, en

    def _extract_bilingual_map(self, text: str) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for raw_line in text.splitlines():
            line = norm_text(raw_line)
            if not line or not CJK_RE.search(line):
                continue
            matches = list(ASCII_RE.finditer(line))
            if not matches:
                continue
            last = matches[-1]
            en = norm_key(line[last.start():])
            cn = norm_text(line[:last.start()]).rstrip(":：")
            if not en or not cn:
                continue
            if len(en) > 120 or len(cn) > 160:
                continue
            if not CJK_RE.search(cn):
                continue
            result.setdefault(en, cn)
        return result

    def resolve_page(self, breadcrumbs: Iterable[str], text: str) -> Optional[PageRef]:
        candidates: List[str] = []
        for raw in breadcrumbs:
            candidates.extend(alias_keys(raw))
        for raw in extract_tag_values(text):
            candidates.extend(alias_keys(raw))
        candidates.extend(alias_keys(text))

        for cand in candidates:
            if cand in self.page_by_title_en:
                return self.page_by_title_en[cand]

        scored: List[tuple[int, PageRef]] = []
        for page in self.pages:
            score = 0
            for cand in candidates:
                if not cand:
                    continue
                if cand == norm_key(page.title_en):
                    score += 100
                elif cand in page.english_terms:
                    score += 30
                elif cand and cand in page.search_blob:
                    score += 10
                else:
                    parts = [p for p in cand.split() if len(p) >= 4]
                    if parts:
                        overlap = sum(1 for p in parts if p in page.search_blob)
                        score += overlap * 4
            if score:
                scored.append((score, page))

        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored[0][0] < 20:
            return None
        return scored[0][1]

    def direct_translate(self, text: str, page: Optional[PageRef]) -> Optional[str]:
        key = norm_key(text)
        if not self._allow_direct(text):
            return None
        if page and key in page.bilingual_map:
            return page.bilingual_map[key]
        return self.global_map.get(key)

    def _allow_direct(self, text: str) -> bool:
        raw = norm_text(text)
        if not raw:
            return False
        if len(raw) <= 4:
            return False
        if re.fullmatch(r"[a-z\-]+", raw):
            return False
        if raw.lower() == raw and len(raw) < 12 and " " not in raw:
            return False
        return True


class JsonWalker:
    def __init__(self, index: MhmmChmIndex):
        self.index = index
        self.jobs: List[Job] = []

    def collect(self, obj: Any) -> List[Job]:
        self.jobs.clear()
        self._walk(obj, path="$", key="", breadcrumbs=[], page_hint=None, root_key="")
        return self.jobs

    def _walk(
        self,
        value: Any,
        path: str,
        key: str,
        breadcrumbs: List[str],
        page_hint: Optional[str],
        root_key: str,
    ) -> None:
        if isinstance(value, dict):
            local_breadcrumbs = breadcrumbs[:]
            local_page_hint = page_hint
            entity_name = value.get("name")
            if isinstance(entity_name, str) and entity_name:
                local_breadcrumbs = breadcrumbs + [entity_name]
                local_page_hint = entity_name
            next_root = root_key
            if path == "$":
                next_root = ""
            for child_key, child_value in value.items():
                child_path = f"{path}.{child_key}" if path != "$" else f"$.{child_key}"
                child_root = next_root or child_key
                child_breadcrumbs = local_breadcrumbs
                child_page_hint = local_page_hint
                if child_key == "shortName" and isinstance(child_value, str):
                    child_breadcrumbs = local_breadcrumbs + [child_value]
                self._walk(
                    child_value,
                    child_path,
                    child_key,
                    child_breadcrumbs,
                    child_page_hint,
                    child_root,
                )
            return

        if isinstance(value, list):
            for idx, item in enumerate(value):
                self._walk(
                    item,
                    f"{path}[{idx}]",
                    key,
                    breadcrumbs,
                    page_hint,
                    root_key,
                )
            return

        if not isinstance(value, str):
            return

        if key == "style":
            return
        if check_skip_key(key, value, path.rsplit(".", 1)[0] if "." in path else ""):
            return
        if not need_translate_str(value):
            return

        self.jobs.append(
            Job(
                job_id=path,
                path=path,
                key=key,
                en_text=value,
                breadcrumbs=breadcrumbs[:],
                page_hint=page_hint,
            )
        )


class ChmTranslator:
    def __init__(
        self,
        index: MhmmChmIndex,
        cache_path: Path,
        chunk_max_chars: int = 1800,
        page_excerpt_chars: int = 5000,
    ):
        self.index = index
        self.cache_path = cache_path
        self.chunk_max_chars = chunk_max_chars
        self.page_excerpt_chars = page_excerpt_chars
        self.cache = self._load_cache()
        self.adapter = SiliconFlowAdapter(DS_KEY)

    def _load_cache(self) -> Dict[str, str]:
        if self.cache_path.exists():
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def translate_jobs(self, jobs: List[Job], sample_limit: int = 0) -> Dict[str, str]:
        pending: List[tuple[Job, PageRef]] = []
        results: Dict[str, str] = {}

        for job in jobs:
            if job.job_id in self.cache:
                results[job.job_id] = self.cache[job.job_id]
                continue
            page = self.index.resolve_page(job.breadcrumbs, job.en_text)
            direct = self.index.direct_translate(job.en_text, page)
            if direct:
                direct = replace_cn_pattern(direct, job.en_text)
                results[job.job_id] = direct
                self.cache[job.job_id] = direct
                continue
            if page is not None:
                pending.append((job, page))

        if sample_limit > 0:
            pending = pending[:sample_limit]

        grouped: Dict[str, List[Job]] = {}
        page_lookup: Dict[str, PageRef] = {}
        for job, page in pending:
            grouped.setdefault(page.path, []).append(job)
            page_lookup[page.path] = page

        for page_path, page_jobs in grouped.items():
            chunks = self._chunk_jobs(page_jobs, max_chars=self.chunk_max_chars)
            page = page_lookup[page_path]
            for idx, chunk in enumerate(chunks, 1):
                batch_res = self._translate_chunk(page, chunk, idx, len(chunks))
                for job_id, cn_text in batch_res.items():
                    results[job_id] = cn_text
                    self.cache[job_id] = cn_text
                self._save_cache()
        return results

    def _chunk_jobs(self, jobs: List[Job], max_chars: int) -> List[List[Job]]:
        chunks: List[List[Job]] = []
        current: List[Job] = []
        current_chars = 0
        for job in jobs:
            size = len(job.en_text) + sum(len(x) for x in job.breadcrumbs[-2:])
            if current and current_chars + size > max_chars:
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(job)
            current_chars += size
        if current:
            chunks.append(current)
        return chunks

    def _translate_chunk(
        self,
        page: PageRef,
        jobs: List[Job],
        chunk_index: int,
        total_chunks: int,
        strict_mode: bool = False,
    ) -> Dict[str, str]:
        page_excerpt = page.text[:self.page_excerpt_chars]
        glossary = {
            en: cn
            for en, cn in page.bilingual_map.items()
            if len(en) <= 80 and len(cn) <= 80
        }
        batch_id = f"{page.path}#{chunk_index}"
        source_hash = hashlib.sha1(
            ("|".join(job.job_id for job in jobs) + page.path).encode("utf-8")
        ).hexdigest()[:12]
        payload = {
            "batch_id": batch_id,
            "source_hash": source_hash,
            "page": {
                "path": page.path,
                "title_cn": page.title_cn,
                "title_en": page.title_en,
            },
            "reference_excerpt": page_excerpt,
            "reference_glossary": glossary,
            "items": [
                {
                    "uid": job.job_id,
                    "breadcrumbs": job.breadcrumbs[-4:],
                    "en_str": job.en_text,
                }
                for job in jobs
            ],
        }
        system_prompt = (
            "你是 D&D 5e 与怪物猎人资料本地化翻译助手。\n"
            "你的唯一翻译依据是用户提供的 CHM 页面摘录与其中的中英对照术语。\n"
            "禁止使用外部词表、禁止自行套用其他项目的既有译法。\n"
            "要求：\n"
            "1. 只翻译 items 里的英文文本。\n"
            "2. 必须遵循 reference_excerpt 和 reference_glossary 的用词。\n"
            "3. 保留所有 {@...} 标签，且标签数量、顺序、tag 名称不变。\n"
            "4. 不要改动数字、骰子、JSON id。\n"
            "5. 必须返回：{\"batch_id\":\"输入值\",\"source_hash\":\"输入值\",\"items\":[{\"uid\":\"...\",\"trans_str\":\"...\"}],\"add_terms\":{}}\n"
            "6. items 里只能返回输入中已有的 uid，不能新增、删除、合并。\n"
            "7. 不要输出解释。"
        )
        if strict_mode:
            system_prompt += (
                "\n额外要求：如果英文里出现 {@condition ...}、{@creature ...}、{@item ...} 等标签，"
                "中文里必须保留完整标签结构，绝不能把标签改成普通文本。"
            )
        print(
            f"[translate] {page.title_en or page.title_cn} "
            f"chunk {chunk_index}/{total_chunks} jobs={len(jobs)}",
            flush=True,
        )
        msg, status = self.adapter.sendText(
            json.dumps(payload, ensure_ascii=False),
            system_prompt,
            structured_output=True,
            response_mode="batch",
        )
        if status != TranslatorStatus.SUCCESS or not msg:
            raise RuntimeError(
                f"LLM failed for {page.path} chunk {chunk_index}/{total_chunks}: {status}"
            )
        data, ok = format_llm_msg(msg)
        if ok and isinstance(data, dict) and not isinstance(data.get("items"), list):
            wrapped = data.get("trans_str")
            if isinstance(wrapped, str):
                inner, inner_ok = format_llm_msg(wrapped)
                if inner_ok and isinstance(inner, dict):
                    data = inner
                    ok = True
        if not ok or not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RuntimeError(f"Invalid LLM response for {page.path}: {msg}")
        if data.get("batch_id") != batch_id or data.get("source_hash") != source_hash:
            raise RuntimeError(f"Batch identity mismatch for {page.path}: {msg}")

        result: Dict[str, str] = {}
        for item in data["items"]:
            if not isinstance(item, dict):
                continue
            job_id = item.get("uid")
            translation = item.get("trans_str")
            if not isinstance(job_id, str) or not isinstance(translation, str):
                continue
            source = next((job.en_text for job in jobs if job.job_id == job_id), None)
            if source is None:
                continue
            translation = replace_cn_pattern(translation, source)
            if translation.count("{@") != source.count("{@"):
                if len(jobs) > 1:
                    continue
                restored = self._restore_missing_tags(page, source, translation)
                if restored is not None:
                    translation = restored
                else:
                    repaired = self._repair_single_item(page, jobs[0], translation)
                    if repaired is None:
                        raise RuntimeError(
                            f"Tag count mismatch for {job_id}: {source} -> {translation}"
                        )
                    translation = repaired
            result[job_id] = translation

        missing = [job.job_id for job in jobs if job.job_id not in result]
        if missing:
            if len(jobs) > 1:
                recovered: Dict[str, str] = {}
                for job in jobs:
                    if job.job_id in result:
                        continue
                    recovered.update(
                        self._translate_chunk(
                            page,
                            [job],
                            chunk_index,
                            total_chunks,
                            strict_mode=True,
                        )
                    )
                result.update(recovered)
                missing = [job.job_id for job in jobs if job.job_id not in result]
            if missing:
                raise RuntimeError(f"Missing translations in batch: {missing[:10]}")
        return result

    def _repair_single_item(
        self,
        page: PageRef,
        job: Job,
        bad_translation: str,
    ) -> Optional[str]:
        payload = {
            "page": {
                "path": page.path,
                "title_cn": page.title_cn,
                "title_en": page.title_en,
            },
            "reference_excerpt": page.text[:3000],
            "source": job.en_text,
            "bad_translation": bad_translation,
        }
        prompt = (
            "你需要修正一条中文译文，使其严格保留英文中的所有 {@...} 标签。\n"
            "只依据提供的 CHM 摘录修正，不要重写无关内容。\n"
            "输出 JSON：{\"translation\":\"...\"}"
        )
        msg, status = self.adapter.sendText(
            json.dumps(payload, ensure_ascii=False),
            prompt,
            structured_output=True,
            response_mode="single",
        )
        if status != TranslatorStatus.SUCCESS or not msg:
            return None
        data, ok = format_llm_msg(msg)
        if ok and isinstance(data, dict) and isinstance(data.get("trans_str"), str):
            wrapped = data["trans_str"]
            if looks_like_json_object(wrapped):
                inner, inner_ok = format_llm_msg(wrapped)
                if inner_ok and isinstance(inner, dict):
                    data = inner
        if not ok or not isinstance(data, dict):
            return None
        translation = data.get("translation")
        if not isinstance(translation, str):
            translation = data.get("trans_str")
        if not isinstance(translation, str):
            return None
        translation = replace_cn_pattern(translation, job.en_text)
        if translation.count("{@") != job.en_text.count("{@"):
            translation = self._restore_missing_tags(page, job.en_text, translation)
        if translation is None or translation.count("{@") != job.en_text.count("{@"):
            return None
        return translation

    def _restore_missing_tags(
        self,
        page: PageRef,
        source: str,
        translation: str,
    ) -> Optional[str]:
        source_tags = TAG_RE.findall(source)
        if not source_tags:
            return translation
        fixed = translation
        if fixed.count("{@") >= source.count("{@"):
            return fixed
        for tag, raw_value in source_tags:
            if fixed.count("{@") >= source.count("{@"):
                break
            translated_value = (
                page.bilingual_map.get(norm_key(raw_value))
                or self.index.global_map.get(norm_key(raw_value))
                or TAG_VALUE_FALLBACKS.get((tag, norm_key(raw_value)))
            )
            if not translated_value:
                continue
            wrapped = f"{{@{tag} {translated_value}}}"
            for candidate in self._tag_plain_candidates(tag, raw_value, translated_value):
                if candidate in fixed:
                    fixed = self._replace_plain_text_once(fixed, candidate, wrapped)
                    break
        if fixed.count("{@") == source.count("{@"):
            return fixed
        return None

    def _tag_plain_candidates(
        self,
        tag: str,
        raw_value: str,
        translated_value: str,
    ) -> List[str]:
        candidates: List[str] = []
        tag_key = norm_key(tag)
        raw_key = norm_key(raw_value)
        if tag_key == "condition" and raw_key == "prone":
            candidates.extend(["被击倒", "击倒地", "击倒", "倒地"])
        candidates.append(translated_value)
        seen = set()
        ordered: List[str] = []
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
        ordered.sort(key=len, reverse=True)
        return ordered

    def _replace_plain_text_once(self, text: str, needle: str, replacement: str) -> str:
        if not needle:
            return text
        depth = 0
        i = 0
        while i <= len(text) - len(needle):
            if text[i:i + 2] == "{@":
                depth += 1
                i += 2
                continue
            if text[i] == "}" and depth > 0:
                depth -= 1
                i += 1
                continue
            if depth == 0 and text.startswith(needle, i):
                return text[:i] + replacement + text[i + len(needle):]
            i += 1
        return text


def set_path_value(root: Any, path: str, value: str) -> None:
    if path == "$":
        raise ValueError("cannot replace root")
    tokens = re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", path)
    cur = root
    for key_token, index_token in tokens[:-1]:
        if key_token:
            cur = cur[key_token]
        else:
            cur = cur[int(index_token)]
    last_key, last_index = tokens[-1]
    if last_key:
        cur[last_key] = value
    else:
        cur[int(last_index)] = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-json",
        default="/data/homebrew-en/collection/Amellwind; Monster Hunter Monster Manual.json",
    )
    parser.add_argument(
        "--chm-root",
        default="/data/5e-translator/tmp/mhmm_chm",
    )
    parser.add_argument(
        "--output-json",
        default="/data/5e-translator/output/collection/Amellwind; Monster Hunter Monster Manual.json",
    )
    parser.add_argument(
        "--cache-json",
        default="/data/5e-translator/output/collection/Amellwind; Monster Hunter Monster Manual.from-chm.cache.json",
    )
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--chunk-max-chars", type=int, default=1800)
    parser.add_argument("--page-excerpt-chars", type=int, default=5000)
    args = parser.parse_args()

    source_path = Path(args.source_json)
    chm_root = Path(args.chm_root)
    output_path = Path(args.output_json)
    cache_path = Path(args.cache_json)

    if not source_path.exists():
        raise FileNotFoundError(source_path)
    if not chm_root.exists():
        raise FileNotFoundError(chm_root)

    print("[index] building CHM index...", flush=True)
    index = MhmmChmIndex(chm_root)
    index.build()
    print(f"[index] pages={len(index.pages)} exact_terms={len(index.global_map)}", flush=True)

    source_obj = json.loads(source_path.read_text(encoding="utf-8"))
    target_obj = copy.deepcopy(source_obj)

    walker = JsonWalker(index)
    jobs = walker.collect(source_obj)
    print(f"[jobs] collected={len(jobs)}", flush=True)

    translator = ChmTranslator(
        index,
        cache_path,
        chunk_max_chars=args.chunk_max_chars,
        page_excerpt_chars=args.page_excerpt_chars,
    )
    results = translator.translate_jobs(jobs, sample_limit=args.sample_limit)
    print(f"[jobs] translated={len(results)}", flush=True)

    for job in jobs:
        cn = results.get(job.job_id)
        if cn is None:
            continue
        set_path_value(target_obj, job.path, cn)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(target_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[done] wrote {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
