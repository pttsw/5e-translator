#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare trusted xlsx terms with the database term table."""

from __future__ import annotations

import argparse
import csv
import os
import re
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import pymysql

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import DB_CONFIG


NS_MAIN = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
NS_PACKAGE_REL = {
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships"
}
XLSX_CATEGORY_TO_TERM_CATEGORY = {
    "地名": "place",
    "法术": "spell",
    "规则术语": "variantrule",
    "人名": "name",
    "神名": "deity",
    "组织名": "group",
    "位面与位层": "plane",
    "事件和物件名": "object",
    "书名": "book",
    "生物": "bestiary",
    "缩写": None,
    "其他": "",
    "种族": "race",
    "世设名": "book",
    "物品": "item",
}
XLSX_SOURCE_TO_TERM_SOURCE = {
    "《荒洲探险家指南》": "EGW",
    "玩家手册2024": "XPHB",
    "玩家手册2014": "PHB",
    "深水城：龙金劫": "WDH",
    "地下城主指南2024": "XDMG",
    "珊娜萨的万事指南": "XGE",
    "塔莎的万事坩埚": "TCE",
    "德拉肯海姆之墟": "DoD",
    "谦卑林战役设定集": "HWCS",
    "艾奎兹玄有限责任公司": "AI",
    "艾弗瑞斯巢穴集": "LoE",
    "费资本的巨龙宝库": "FTD",
    "斯翠海文：混沌研习": "SCC",
    "万象无常书": "BMT",
    "冰风谷：冰霜少女的雾淞": "IDRotF",
    "夸立许的失落实验室": "-",
    "印记城与外域": "SatO",
    "塔尔多雷战役设定集": "TCSR",
    "星界冒险者指南": "AAG",
    "拉尼卡公会长指南": "GGR",
    "艾伯伦：从终末战争中崛起": "ERLW",
    "地下城主指南2014": "DMG",
    "魔索布莱城": "-",
    "": "",
}


def _norm_text(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\u3000", " ").strip()


def _norm_key(value) -> str:
    return re.sub(r"\s+", " ", _norm_text(value)).casefold()


def _col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref)
    if not letters:
        return 0
    index = 0
    for char in letters.group(0):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    strings = []
    for item in root.findall("m:si", NS_MAIN):
        parts = [text.text or "" for text in item.findall(".//m:t", NS_MAIN)]
        strings.append("".join(parts))
    return strings


def _sheet_paths_by_name(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("pr:Relationship", NS_PACKAGE_REL)
    }
    result = {}
    for sheet in workbook.findall("m:sheets/m:sheet", NS_MAIN):
        rel_id = sheet.attrib[f"{{{NS_REL['r']}}}id"]
        target = rel_targets[rel_id]
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        result[sheet.attrib["name"]] = target
    return result


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("m:v", NS_MAIN)
    if cell_type == "inlineStr":
        return "".join(
            text.text or "" for text in cell.findall(".//m:is//m:t", NS_MAIN)
        )
    if value is None:
        return ""
    raw = value.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)]
    return raw


def _read_sheet(archive: zipfile.ZipFile, sheet_path: str) -> list[list[str]]:
    shared_strings = _load_shared_strings(archive)
    root = ET.fromstring(archive.read(sheet_path))
    rows = []
    for row in root.findall("m:sheetData/m:row", NS_MAIN):
        values = []
        for cell in row.findall("m:c", NS_MAIN):
            index = _col_index(cell.attrib.get("r", "A"))
            while len(values) <= index:
                values.append("")
            values[index] = _norm_text(_cell_value(cell, shared_strings))
        rows.append(values)
    return rows


def _detect_columns(header: list[str]) -> tuple[int, int, int | None]:
    normalized = [_norm_key(value) for value in header]
    en_aliases = {"英文", "英文名", "english", "en", "原文", "名称", "name"}
    cn_aliases = {"中文", "中文名", "译名", "翻译", "cn", "translation"}
    category_aliases = {"分类", "类别", "category", "类型", "type"}

    def find(aliases: set[str]) -> int | None:
        for index, value in enumerate(normalized):
            if value in aliases:
                return index
        for index, value in enumerate(normalized):
            if any(alias in value for alias in aliases):
                return index
        return None

    en_index = find(en_aliases)
    cn_index = find(cn_aliases)
    category_index = find(category_aliases)
    if en_index is None or cn_index is None:
        raise ValueError(f"无法识别英文/中文列，表头为：{header}")
    return en_index, cn_index, category_index


def _find_header_index(header: list[str], name: str) -> int | None:
    normalized_name = _norm_key(name)
    for index, value in enumerate(header):
        if _norm_key(value) == normalized_name:
            return index
    return None


def _load_xlsx_terms(path: Path, sheet_name: str) -> tuple[list[dict], list[str]]:
    with zipfile.ZipFile(path) as archive:
        sheet_paths = _sheet_paths_by_name(archive)
        if sheet_name not in sheet_paths:
            raise ValueError(
                f"找不到工作表 {sheet_name!r}，可用工作表：{list(sheet_paths)}"
            )
        rows = _read_sheet(archive, sheet_paths[sheet_name])

    header_row_index = None
    detected = None
    for index, row in enumerate(rows[:20]):
        try:
            detected = _detect_columns(row)
        except ValueError:
            continue
        header_row_index = index
        break
    if header_row_index is None or detected is None:
        raise ValueError("前 20 行中未找到可识别表头")

    en_index, cn_index, category_index = detected
    header = rows[header_row_index]
    source_index = _find_header_index(header, "出自")
    note_index = _find_header_index(header, "注释")
    editor_index = _find_header_index(header, "最后编辑人")
    terms = []
    for row_number, row in enumerate(rows[header_row_index + 1 :], header_row_index + 2):
        en = _norm_text(row[en_index] if en_index < len(row) else "")
        cn = _norm_text(row[cn_index] if cn_index < len(row) else "")
        category = (
            _norm_text(row[category_index] if category_index < len(row) else "")
            if category_index is not None
            else ""
        )
        source_name = _norm_text(
            row[source_index] if source_index is not None and source_index < len(row) else ""
        )
        note = _norm_text(
            row[note_index] if note_index is not None and note_index < len(row) else ""
        )
        editor = _norm_text(
            row[editor_index] if editor_index is not None and editor_index < len(row) else ""
        )
        if not en or not cn:
            continue
        term_category = XLSX_CATEGORY_TO_TERM_CATEGORY.get(category, category)
        if term_category is None:
            continue
        term_source = XLSX_SOURCE_TO_TERM_SOURCE.get(source_name, "")
        if source_name and source_name not in XLSX_SOURCE_TO_TERM_SOURCE:
            note = f"{note}；原出自列：{source_name}" if note else f"原出自列：{source_name}"
        terms.append({
            "xlsx_row": row_number,
            "en": en,
            "cn": cn,
            "xlsx_category": category,
            "term_category": term_category,
            "xlsx_source": source_name,
            "term_source": term_source,
            "note": note,
            "editor": editor,
        })
    return terms, rows[header_row_index]


def _load_db_terms() -> list[dict]:
    connection = pymysql.connect(
        host=DB_CONFIG["HOST"],
        port=DB_CONFIG["PORT"],
        user=DB_CONFIG["USER"],
        password=DB_CONFIG["PASSWORD"],
        database=DB_CONFIG["DATABASE"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT en, cn, category, source, note, modified_at "
                "FROM term ORDER BY en, category"
            )
            return list(cursor.fetchall())
    finally:
        connection.close()


def _connect_db():
    return pymysql.connect(
        host=DB_CONFIG["HOST"],
        port=DB_CONFIG["PORT"],
        user=DB_CONFIG["USER"],
        password=DB_CONFIG["PASSWORD"],
        database=DB_CONFIG["DATABASE"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _index_terms(rows: Iterable[dict]) -> dict[str, list[dict]]:
    indexed = defaultdict(list)
    for row in rows:
        indexed[_norm_key(row["en"])].append(row)
    return indexed


def _index_terms_by_entry(rows: Iterable[dict]) -> dict[tuple[str, str], list[dict]]:
    indexed = defaultdict(list)
    for row in rows:
        indexed[(_norm_key(row["en"]), _norm_key(row.get("category", "")))].append(row)
    return indexed


def _compare(xlsx_terms: list[dict], db_terms: list[dict]) -> tuple[list[dict], list[dict]]:
    db_by_en = _index_terms(db_terms)
    db_by_entry = _index_terms_by_entry(db_terms)
    wrong = []
    missing = []
    seen_missing = set()

    for xlsx in xlsx_terms:
        key = _norm_key(xlsx["en"])
        entry_key = (key, _norm_key(xlsx["term_category"]))
        matches = db_by_en.get(key, [])
        exact_matches = db_by_entry.get(entry_key, [])
        has_correct_cn = any(_norm_text(db_row["cn"]) == xlsx["cn"] for db_row in matches)
        if not exact_matches and has_correct_cn:
            missing_key = entry_key
            if missing_key not in seen_missing:
                missing.append({
                    **xlsx,
                    "term_existing_categories": "; ".join(
                        sorted({_norm_text(row["category"]) for row in matches})
                    ),
                })
                seen_missing.add(missing_key)
            continue
        if not matches:
            missing_key = entry_key
            if missing_key not in seen_missing:
                missing.append({
                    **xlsx,
                    "term_existing_categories": "",
                })
                seen_missing.add(missing_key)
            continue
        if has_correct_cn:
            continue
        for db_row in matches:
            wrong.append({
                "en": db_row["en"],
                "term_cn": db_row["cn"],
                "xlsx_cn": xlsx["cn"],
                "term_category": db_row["category"],
                "xlsx_category": xlsx["xlsx_category"],
                "term_source": db_row.get("source", ""),
                "term_note": db_row.get("note", ""),
                "term_modified_at": db_row.get("modified_at", ""),
                "xlsx_row": xlsx["xlsx_row"],
            })

    return wrong, missing


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _excel_col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(ord("A") + rem) + name
    return name


def _xml_escape(value) -> str:
    return (
        _norm_text(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _sheet_xml(rows: list[dict], fields: list[str]) -> str:
    all_rows = [fields] + [[row.get(field, "") for field in fields] for row in rows]
    xml_rows = []
    for row_index, row in enumerate(all_rows, 1):
        cells = []
        for col_index, value in enumerate(row):
            ref = f"{_excel_col_name(col_index)}{row_index}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{_xml_escape(value)}</t></is></c>'
            )
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    dimension = f"A1:{_excel_col_name(len(fields) - 1)}{max(len(all_rows), 1)}"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dimension}"/><sheetData>{"".join(xml_rows)}</sheetData>'
        "</worksheet>"
    )


def _write_xlsx(path: Path, sheets: list[tuple[str, list[dict], list[str]]]) -> None:
    sheet_entries = []
    workbook_rels = []
    content_overrides = []
    for index, (name, _rows, _fields) in enumerate(sheets, 1):
        sheet_entries.append(
            f'<sheet name="{_xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        )
        workbook_rels.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        content_overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_entries)}</sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(workbook_rels)}</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f'{"".join(content_overrides)}</Types>'
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        for index, (_name, rows, fields) in enumerate(sheets, 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _sheet_xml(rows, fields))


def _insert_missing_terms(rows: list[dict], dry_run: bool = True) -> int:
    if dry_run:
        return len(rows)

    connection = _connect_db()
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO term "
                "(en, cn, category, source, note, modified_reson, modified_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1)",
                [
                    (
                        row["en"],
                        row["cn"],
                        row["term_category"],
                        row["term_source"],
                        row["note"],
                        f"从大译名表导入，编辑人：{row['editor']}",
                    )
                    for row in rows
                ],
            )
        connection.commit()
        return len(rows)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", default="大译名表.xlsx")
    parser.add_argument("--sheet", default="大译名表")
    parser.add_argument("--out-dir", default="output/term_compare")
    parser.add_argument(
        "--import-missing",
        action="store_true",
        help="Insert currently missing xlsx terms into the database term table.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --import-missing, report insert count without writing.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    xlsx_terms, header = _load_xlsx_terms(Path(args.xlsx), args.sheet)
    db_terms = _load_db_terms()
    wrong, missing = _compare(xlsx_terms, db_terms)

    wrong_fields = [
        "en", "term_cn", "xlsx_cn", "term_category", "xlsx_category",
        "term_source", "term_note", "term_modified_at", "xlsx_row",
    ]
    missing_fields = [
        "en", "cn", "term_category", "xlsx_category",
        "term_source", "xlsx_source", "note", "editor",
        "term_existing_categories", "xlsx_row",
    ]
    _write_csv(out_dir / "错译表.csv", wrong, wrong_fields)
    _write_csv(out_dir / "缺失表.csv", missing, missing_fields)
    _write_xlsx(
        out_dir / "term_vs_大译名表_对比结果.xlsx",
        [
            ("错译表", wrong, wrong_fields),
            ("缺失表", missing, missing_fields),
        ],
    )

    print(f"Excel sheet header: {header}")
    print(f"xlsx terms: {len(xlsx_terms)}")
    print(f"db terms: {len(db_terms)}")
    print(f"wrong translations: {len(wrong)}")
    print(f"missing terms: {len(missing)}")
    if args.import_missing:
        inserted_count = _insert_missing_terms(missing, dry_run=args.dry_run)
        action = "would insert" if args.dry_run else "inserted"
        print(f"{action} missing terms: {inserted_count}")
    print(f"generated at: {datetime.now().isoformat(timespec='seconds')}")
    print(f"output dir: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
