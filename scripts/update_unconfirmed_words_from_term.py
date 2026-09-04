#!/usr/bin/env python3
import argparse
import csv
import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import DB_CONFIG


OUTPUT_DIR = Path("output/term_compare")
AUDIT_PATH = OUTPUT_DIR / "words_term_unconfirmed_updates.csv"
SKIPPED_PATH = OUTPUT_DIR / "words_term_unconfirmed_skipped.csv"


def _connect():
    return pymysql.connect(
        host=DB_CONFIG["HOST"],
        port=DB_CONFIG["PORT"],
        user=DB_CONFIG["USER"],
        password=DB_CONFIG["PASSWORD"],
        database=DB_CONFIG["DATABASE"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _sorted_join(values):
    return " | ".join(sorted(str(value) for value in values if value is not None))


def _load_terms(cursor):
    terms = {}
    cursor.execute("SELECT en, cn, category, source FROM term ORDER BY en, category, cn")
    for row in cursor.fetchall():
        item = terms.setdefault(
            row["en"],
            {"all_cns": set(), "by_category": {}, "categories": set(), "sources": set()},
        )
        cn = row["cn"] or ""
        category = row["category"] or ""
        item["all_cns"].add(cn)
        item["by_category"].setdefault(category, set()).add(cn)
        item["categories"].add(category)
        item["sources"].add(row.get("source") or "")
    return terms


def _load_words(cursor):
    words_by_en = {}
    unconfirmed_rows = []
    cursor.execute(
        "SELECT id, en, cn, category, source, proofread, json_file "
        "FROM words ORDER BY en, id"
    )
    for row in cursor.fetchall():
        item = words_by_en.setdefault(row["en"], {"all_cns": set(), "rows": 0})
        item["all_cns"].add(row["cn"] or "")
        item["rows"] += 1
        if row.get("proofread") == 0:
            unconfirmed_rows.append(row)
    return words_by_en, unconfirmed_rows


def _target_cn(word_row, term_info):
    category = word_row.get("category") or ""
    category_cns = term_info["by_category"].get(category)
    if category_cns and len(category_cns) == 1:
        return next(iter(category_cns)), "category"
    if len(term_info["all_cns"]) == 1:
        return next(iter(term_info["all_cns"])), "unique_en"
    return None, "ambiguous"


def build_changes(cursor):
    terms = _load_terms(cursor)
    words_by_en, unconfirmed_rows = _load_words(cursor)

    changes = []
    skipped = []
    stats = {
        "term_en": len(terms),
        "words_en": len(words_by_en),
        "unconfirmed_words": len(unconfirmed_rows),
        "unconfirmed_with_term_en": 0,
        "skipped_en_has_matching_translation": 0,
        "skipped_ambiguous": 0,
        "skipped_already_target": 0,
        "changes": 0,
    }

    skipped_matching_en = set()
    for en, words_info in words_by_en.items():
        term_info = terms.get(en)
        if term_info and words_info["all_cns"] & term_info["all_cns"]:
            skipped_matching_en.add(en)

    for row in unconfirmed_rows:
        en = row["en"]
        term_info = terms.get(en)
        if not term_info:
            continue
        stats["unconfirmed_with_term_en"] += 1

        if en in skipped_matching_en:
            stats["skipped_en_has_matching_translation"] += 1
            skipped.append(
                {
                    "reason": "same_en_has_matching_words_translation",
                    "words_id": row["id"],
                    "英文": en,
                    "words原译文": row["cn"] or "",
                    "term译文": _sorted_join(term_info["all_cns"]),
                    "words分类": row.get("category") or "",
                    "term分类": _sorted_join(term_info["categories"]),
                }
            )
            continue

        target, match_mode = _target_cn(row, term_info)
        if target is None:
            stats["skipped_ambiguous"] += 1
            skipped.append(
                {
                    "reason": "ambiguous_term_translation",
                    "words_id": row["id"],
                    "英文": en,
                    "words原译文": row["cn"] or "",
                    "term译文": _sorted_join(term_info["all_cns"]),
                    "words分类": row.get("category") or "",
                    "term分类": _sorted_join(term_info["categories"]),
                }
            )
            continue
        if (row["cn"] or "") == target:
            stats["skipped_already_target"] += 1
            continue

        changes.append(
            {
                "words_id": row["id"],
                "英文": en,
                "words原译文": row["cn"] or "",
                "更新后译文": target,
                "匹配方式": match_mode,
                "words分类": row.get("category") or "",
                "term分类": _sorted_join(term_info["categories"]),
                "words来源": row.get("source") or "",
                "term来源": _sorted_join(term_info["sources"]),
                "words文件": row.get("json_file") or "",
            }
        )

    stats["changes"] = len(changes)
    return changes, skipped, stats


def write_csv(path, rows, fieldnames):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="update words.cn")
    args = parser.parse_args()

    conn = _connect()
    try:
        with conn.cursor() as cursor:
            changes, skipped, stats = build_changes(cursor)

            write_csv(
                AUDIT_PATH,
                changes,
                [
                    "words_id",
                    "英文",
                    "words原译文",
                    "更新后译文",
                    "匹配方式",
                    "words分类",
                    "term分类",
                    "words来源",
                    "term来源",
                    "words文件",
                ],
            )
            write_csv(
                SKIPPED_PATH,
                skipped,
                ["reason", "words_id", "英文", "words原译文", "term译文", "words分类", "term分类"],
            )

            updated = 0
            if args.apply and changes:
                for change in changes:
                    cursor.execute(
                        "UPDATE words SET cn = %s, modified_at = NOW() "
                        "WHERE id = %s AND proofread = 0 AND cn = %s",
                        (
                            change["更新后译文"],
                            change["words_id"],
                            change["words原译文"],
                        ),
                    )
                    updated += cursor.rowcount
                conn.commit()
            else:
                conn.rollback()

        for key in sorted(stats):
            print(f"{key}={stats[key]}")
        print(f"updated={updated}")
        print(f"audit_csv={AUDIT_PATH}")
        print(f"skipped_csv={SKIPPED_PATH}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
