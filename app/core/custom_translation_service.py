"""Isolated document parsing, matching, export, and conversion helpers."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from config import APP_TEMP_PATH, EN_PATH, HOMEBREW_EN_PATH, UA_EN_PATH

ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx", ".txt"}
SOURCE_SUFFIXES = ALLOWED_SUFFIXES | {".json", ".md"}
CUSTOM_ROOT = Path(APP_TEMP_PATH) / "custom_uploads"
CONVERSION_SKILL_ROOT = Path("/data/5etools-mirror-2.github.io/codex-skills/homebrew-conversion-audit")


def safe_upload_name(filename):
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if not name or suffix not in ALLOWED_SUFFIXES:
        raise ValueError("仅支持 PDF、DOC、DOCX、TXT 文件")
    return name, suffix


def split_sentences(value):
    """Keep sentence-sized editing units without returning empty fragments."""
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])(?=[\"'\])}]*\s+[A-Z0-9])", text)
    return [part.strip() for part in parts if part.strip()]


def _iter_docx_blocks(path):
    import docx

    document = docx.Document(path)
    blocks = []
    for index, paragraph in enumerate(document.paragraphs):
        if paragraph.text.strip():
            cursor = 0
            run_refs = []
            for run_index, run in enumerate(paragraph.runs):
                run_refs.append({"index": run_index, "start": cursor, "end": cursor + len(run.text)})
                cursor += len(run.text)
            blocks.append({
                "type": "paragraph",
                "text": paragraph.text,
                "origin": {"kind": "paragraph", "index": index, "runs": run_refs},
            })
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                for paragraph_index, paragraph in enumerate(cell.paragraphs):
                    if paragraph.text.strip():
                        cursor = 0
                        run_refs = []
                        for run_index, run in enumerate(paragraph.runs):
                            run_refs.append({"index": run_index, "start": cursor, "end": cursor + len(run.text)})
                            cursor += len(run.text)
                        blocks.append({
                            "type": "table",
                            "text": paragraph.text,
                            "origin": {
                                "kind": "table_cell", "table": table_index,
                                "row": row_index, "cell": cell_index,
                                "paragraph": paragraph_index, "runs": run_refs,
                            },
                        })
    return blocks


def _iter_pdf_blocks(path):
    import fitz

    blocks = []
    document = fitz.open(path)
    try:
        for page_index, page in enumerate(document):
            for block_index, block in enumerate(page.get_text("blocks")):
                content = (block[4] or "").strip()
                if content:
                    blocks.append({
                        "type": "paragraph", "text": content,
                        "origin": {"kind": "pdf_block", "page": page_index, "block": block_index},
                    })
    finally:
        document.close()
    return blocks


def _iter_txt_blocks(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return [
            {"type": "paragraph", "text": line.strip(), "origin": {"kind": "line", "index": index}}
            for index, line in enumerate(handle)
            if line.strip()
        ]


def _fallback_blocks(path):
    # Reuse the application's existing loader as a compatibility fallback for old DOC files.
    from app.core.transform.engine.pdf_file_loader import AdventureFileLoader

    return [
        {"type": document.metadata.get("type", "paragraph"), "text": document.page_content,
         "origin": {"kind": "fallback", "index": index}}
        for index, document in enumerate(AdventureFileLoader(str(path)).load())
        if (document.page_content or "").strip()
    ]


def extract_blocks(path):
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            return _iter_docx_blocks(path)
        if suffix == ".pdf":
            return _iter_pdf_blocks(path)
        if suffix == ".txt":
            return _iter_txt_blocks(path)
        return _fallback_blocks(path)
    except Exception as exc:
        raise ValueError("无法解析上传文件：%s" % exc) from exc


def build_segments(blocks):
    segments = []
    for block_index, block in enumerate(blocks):
        for sentence_index, sentence in enumerate(split_sentences(block["text"])):
            origin = dict(block["origin"])
            origin["sentence"] = sentence_index
            segments.append({
                "seq": len(segments) + 1,
                "block_index": block_index,
                "block_type": block["type"],
                "en_text": sentence,
                "origin_ref_json": json.dumps(origin, ensure_ascii=False),
            })
    return segments


def prefill_exact_matches(segments, words_model):
    unique_texts = list({item["en_text"] for item in segments})
    if not unique_texts:
        return
    matches = {}
    for offset in range(0, len(unique_texts), 500):
        rows = (words_model.query.filter(words_model.en.in_(unique_texts[offset:offset + 500]))
                .order_by(words_model.proofread.desc(), words_model.is_key.desc(), words_model.id.asc()).all())
        for row in rows:
            matches.setdefault(row.en, row.cn or "")
    for segment in segments:
        segment["cn_text"] = matches.get(segment["en_text"], "")


def _json_strings(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            _json_strings(nested, out)
    elif isinstance(value, list):
        for nested in value:
            _json_strings(nested, out)


def read_source_text(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            values = []
            _json_strings(data, values)
            return " ".join(values)
        except (OSError, ValueError):
            return ""
    if path.suffix.lower() == ".md":
        return path.read_text(encoding="utf-8", errors="replace")
    try:
        return " ".join(block["text"] for block in extract_blocks(path))
    except ValueError:
        return ""


def _shingles(value, size=3):
    tokens = re.findall(r"[a-z0-9]+", (value or "").lower())
    if len(tokens) < size:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[index:index + size]) for index in range(len(tokens) - size + 1)}


def find_similar_sources(upload_path, limit=5):
    needles = _shingles(read_source_text(upload_path))
    if not needles:
        return []
    roots = [("en", EN_PATH), ("homebrew", HOMEBREW_EN_PATH), ("ua", UA_EN_PATH)]
    candidates = []
    for root_name, root_value in roots:
        root = Path(root_value or "")
        if not root.is_dir():
            continue
        for source_path in root.rglob("*"):
            if not source_path.is_file() or source_path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            source_text = read_source_text(source_path)
            source_shingles = _shingles(source_text)
            if not source_shingles:
                continue
            score = len(needles & source_shingles) / float(len(needles | source_shingles))
            if score < 0.08:
                continue
            relative = str(source_path.relative_to(root)).replace(os.sep, "/")
            candidates.append({
                "source_root": root_name, "source_file": relative,
                "existing_file_path": relative, "display_name": source_path.name,
                "score": round(score, 4), "preview": re.sub(r"\s+", " ", source_text)[:240],
            })
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]


def _segment_text(segment):
    return (segment.cn_text or "").strip() or segment.en_text


def translated_blocks(segments):
    grouped = {}
    for segment in segments:
        grouped.setdefault(segment.block_index, []).append(segment)
    return [(index, " ".join(_segment_text(item) for item in items)) for index, items in sorted(grouped.items())]


def _paragraph_from_origin(document, origin):
    if origin.get("kind") == "paragraph":
        return document.paragraphs[int(origin["index"])]
    if origin.get("kind") == "table_cell":
        return document.tables[int(origin["table"])].rows[int(origin["row"])].cells[int(origin["cell"])].paragraphs[int(origin["paragraph"])]
    return None


def _replace_paragraph_preserving_runs(paragraph, replacement):
    runs = list(paragraph.runs)
    if not runs:
        paragraph.add_run(replacement)
        return
    lengths = [max(len(run.text), 1) for run in runs]
    total = sum(lengths)
    cursor = 0
    for index, run in enumerate(runs):
        if index == len(runs) - 1:
            run.text = replacement[cursor:]
            continue
        width = round(len(replacement) * lengths[index] / total)
        run.text = replacement[cursor:cursor + width]
        cursor += width


def export_docx(project, segments):
    from docx import Document

    export_dir = CUSTOM_ROOT / str(project.id) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    output = export_dir / (Path(project.original_filename).stem + "-translated.docx")
    report = []
    if project.file_type == ".docx":
        shutil.copy2(project.stored_path, output)
        document = Document(output)
        grouped = {}
        for segment in segments:
            origin = json.loads(segment.origin_ref_json or "{}")
            origin.pop("sentence", None)
            grouped.setdefault(json.dumps(origin, sort_keys=True), []).append(segment)
        for origin_json, items in grouped.items():
            paragraph = _paragraph_from_origin(document, json.loads(origin_json))
            if paragraph is not None:
                _replace_paragraph_preserving_runs(paragraph, " ".join(_segment_text(item) for item in items))
        document.save(output)
        report.append("DOCX 已基于原文件替换文本，保留段落、表格、图片与 run 样式。")
    else:
        document = Document()
        document.add_heading(project.title, 0)
        for _, content in translated_blocks(segments):
            document.add_paragraph(content)
        document.save(output)
        report.append("该源格式已导出为近似 DOCX；原始页面布局、图片与字体无法完全保留。")
    return str(output), "\n".join(report)


def write_conversion_input(project, segments):
    conversion_dir = CUSTOM_ROOT / str(project.id) / "conversion"
    conversion_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = conversion_dir / "proofread.md"
    markdown_path.write_text("\n\n".join(text for _, text in translated_blocks(segments)), encoding="utf-8")
    return markdown_path


def run_5etools_conversion(project, segments, metadata):
    required = ["published", "storyline", "version", "source", "author", "title"]
    missing = [key for key in required if not str(metadata.get(key) or "").strip()]
    if missing:
        return {"requires_metadata": True, "missing_fields": missing}
    if not CONVERSION_SKILL_ROOT.is_dir():
        raise ValueError("5etools 转换工具未安装")
    input_path = write_conversion_input(project, segments)
    conversion_dir = input_path.parent
    output_path = conversion_dir / "collection.json"
    report_path = conversion_dir / "audit-summary.json"
    command = [
        "node", str(CONVERSION_SKILL_ROOT / "scripts/build-adventure-collection-cli.js"),
        "--input", str(input_path), "--format", "md", "--output", str(output_path),
        "--audit-summary", str(report_path), "--source", str(metadata["source"]),
        "--name", str(metadata["title"]), "--authors", str(metadata["author"]),
        "--published", str(metadata["published"]), "--storyline", str(metadata["storyline"]),
        "--version", str(metadata["version"]), "--translator", str(metadata.get("translator") or ""),
    ]
    completed = subprocess.run(command, cwd=str(CONVERSION_SKILL_ROOT), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    report = completed.stdout[-12000:]
    if completed.returncode:
        raise ValueError("5etools 转换失败：%s" % report)
    homebrew_dir = Path("/data/homebrew")
    validator = CONVERSION_SKILL_ROOT / "scripts" / "validate-homebrew-json-cli.js"
    if homebrew_dir.is_dir() and validator.is_file():
        validation = subprocess.run(
            ["node", str(validator), "--file", str(output_path), "--homebrew-dir", str(homebrew_dir)],
            cwd=str(CONVERSION_SKILL_ROOT), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=180,
        )
        report += "\n\nExternal schema validation:\n" + validation.stdout[-12000:]
        if validation.returncode:
            raise ValueError("5etools JSON 校验失败：%s" % validation.stdout[-12000:])
    return {"requires_metadata": False, "json_path": str(output_path), "report": report}
