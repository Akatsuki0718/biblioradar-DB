#!/usr/bin/env python3
"""Analyze an existing OpenBD JSONL export without making API requests.

This script reports counts only; it does not print or save description/TOC text.
Expected JSONL format is the wrapper written by fetch_openbd_batch.py:
  {"requested_isbn": "...", "record": {...}}
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def text_entries(record: dict[str, Any]) -> list[dict[str, Any]]:
    onix = as_dict(record.get("onix"))
    collateral = as_dict(onix.get("CollateralDetail"))
    entries = collateral.get("TextContent") or []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def normalize_code(value: Any) -> str:
    raw = str(value or "").strip()
    return raw.zfill(2) if raw.isdigit() else (raw or "unknown")


def inspect(record_value: Any) -> dict[str, Any]:
    record = as_dict(record_value)
    if not record:
        return {
            "record_found": False,
            "title": False,
            "author": False,
            "onix_description": False,
            "hanmoto_description": False,
            "description_either": False,
            "toc": False,
            "text_codes": set(),
            "text_entry_codes": Counter(),
        }

    summary = as_dict(record.get("summary"))
    codes: set[str] = set()
    entry_codes: Counter[str] = Counter()
    onix_description = False
    toc = False
    for entry in text_entries(record):
        text = entry.get("Text")
        if not isinstance(text, str) or not text.strip():
            continue
        code = normalize_code(entry.get("TextType"))
        codes.add(code)
        entry_codes[code] += 1
        if code in {"02", "03"}:
            onix_description = True
        if code == "04":
            toc = True

    hanmoto = as_dict(record.get("hanmoto"))
    short = hanmoto.get("kaisetsu105w")
    hanmoto_description = isinstance(short, str) and bool(short.strip())
    return {
        "record_found": True,
        "title": bool(str(summary.get("title") or "").strip()),
        "author": bool(str(summary.get("author") or "").strip()),
        "onix_description": onix_description,
        "hanmoto_description": hanmoto_description,
        "description_either": onix_description or hanmoto_description,
        "toc": toc,
        "text_codes": codes,
        "text_entry_codes": entry_codes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="保存済みOpenBD JSONLの取得項目を集計します（API通信なし）。")
    parser.add_argument("--input", default="openbd_records.jsonl", help="JSONLファイル")
    parser.add_argument("--report", default="openbd_jsonl_analysis_report.json", help="集計結果JSON")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.is_file():
        print(f"ファイルが見つかりません: {path}", file=sys.stderr)
        return 2

    totals = Counter()
    type_record_counts: Counter[str] = Counter()
    type_entry_counts: Counter[str] = Counter()
    rows = 0
    malformed = 0
    unique_isbns: set[str] = set()

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                wrapper = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(wrapper, dict):
                malformed += 1
                continue
            rows += 1
            isbn = wrapper.get("requested_isbn")
            if isbn:
                unique_isbns.add(str(isbn))
            result = inspect(wrapper.get("record"))
            for key in (
                "record_found", "title", "author", "onix_description",
                "hanmoto_description", "description_either", "toc"
            ):
                totals[key] += int(result[key])
            for code in result["text_codes"]:
                type_record_counts[code] += 1
            type_entry_counts.update(result["text_entry_codes"])

    denom = rows or 1
    report = {
        "input": str(path.resolve()),
        "rows_read": rows,
        "unique_requested_isbns": len(unique_isbns),
        "malformed_lines": malformed,
        "counts": dict(totals),
        "rates_percent": {key: round(value / denom * 100, 3) for key, value in totals.items()},
        "records_with_each_text_type": dict(sorted(type_record_counts.items())),
        "text_content_entry_counts_by_type": dict(sorted(type_entry_counts.items())),
        "note": "このレポートは件数のみ。内容紹介・目次の本文は出力しません。"
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 保存済みJSONLの分析（API通信なし）===")
    print(f"入力行数: {rows:,}; 一意ISBN数: {len(unique_isbns):,}; 不正行: {malformed:,}")
    print(f"書誌レコード: {totals['record_found']:,} ({totals['record_found']/denom:.2%})")
    print(f"タイトルあり: {totals['title']:,} ({totals['title']/denom:.2%})")
    print(f"著者あり: {totals['author']:,} ({totals['author']/denom:.2%})")
    print(f"ONIX TextType 02/03 の本文あり: {totals['onix_description']:,} ({totals['onix_description']/denom:.2%})")
    print(f"hanmoto.kaisetsu105w あり: {totals['hanmoto_description']:,} ({totals['hanmoto_description']/denom:.2%})")
    print(f"いずれかの内容紹介あり: {totals['description_either']:,} ({totals['description_either']/denom:.2%})")
    print(f"目次（TextType 04）あり: {totals['toc']:,} ({totals['toc']/denom:.2%})")
    print("TextType別（該当レコード数）:")
    for code, count in sorted(type_record_counts.items()):
        print(f"  {code}: {count}")
    print(f"レポート: {report_path}")
    print("注意: 件数集計のみを表示し、本文は表示・保存しません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
