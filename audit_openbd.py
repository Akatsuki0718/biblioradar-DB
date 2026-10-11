#!/usr/bin/env python3
"""Audit OpenBD metadata availability for a sample of ISBNs.

This script does NOT save book descriptions or raw OpenBD responses. It only
saves aggregate counts so we can estimate how many ISBNs have usable metadata
before designing the long-term storage and embedding pipeline.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

API_URL = "https://api.openbd.jp/v1/get"
ISBN_RE = re.compile(r"^(?:\d{13}|\d{9}[\dXx])$")


def normalize_isbn(value: Any) -> str | None:
    """Return a normalized ISBN-10/13, or None if the value is malformed."""
    if not isinstance(value, (str, int)):
        return None
    isbn = re.sub(r"[\s-]", "", str(value)).upper()
    if not ISBN_RE.fullmatch(isbn):
        return None
    return isbn


def load_isbns(path: Path) -> tuple[list[str], int]:
    """Load the /coverage JSON array and deduplicate ISBNs in source order."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # Also tolerate a simple wrapper format in case the file format changes.
        for key in ("isbns", "coverage", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError("入力JSONはISBN文字列の配列である必要があります。")

    unique: list[str] = []
    seen: set[str] = set()
    invalid_count = 0
    for value in data:
        isbn = normalize_isbn(value)
        if isbn is None:
            invalid_count += 1
            continue
        if isbn not in seen:
            seen.add(isbn)
            unique.append(isbn)

    return unique, invalid_count


def get_batch(session: requests.Session, isbns: list[str], retries: int = 3) -> list[Any]:
    """Fetch one batch (GET is capped at 1,000 ISBNs)."""
    if not isbns:
        return []
    if len(isbns) > 1000:
        raise ValueError("この監査スクリプトでは1リクエスト最大1,000 ISBNです。")

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(
                API_URL,
                params={"isbn": ",".join(isbns)},
                timeout=(10, 45),
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, list):
                raise ValueError("OpenBDの応答が配列ではありません。")
            if len(body) != len(isbns):
                raise ValueError(
                    f"応答件数が要求件数と一致しません（要求={len(isbns)}, 応答={len(body)}）。"
                )
            return body
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                wait = min(2 ** attempt, 8)
                print(f"  通信/応答エラー。{wait}秒後に再試行します: {exc}")
                time.sleep(wait)

    raise RuntimeError(f"バッチ取得に失敗しました: {last_error}")


def text_content_counts(book: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Return flags: has_description, has_table_of_contents, has_any_text."""
    onix = book.get("onix") or {}
    collateral = onix.get("CollateralDetail") or {}
    entries = collateral.get("TextContent") or []

    has_description = False
    has_toc = False
    has_any_text = False

    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            text = entry.get("Text")
            if not isinstance(text, str) or not text.strip():
                continue
            has_any_text = True
            code = str(entry.get("TextType", "")).strip().zfill(2)
            if code in {"02", "03"}:
                has_description = True
            elif code == "04":
                has_toc = True

    # Some records may place a short book description in this field instead.
    hanmoto = book.get("hanmoto") or {}
    short_description = hanmoto.get("kaisetsu105w")
    if isinstance(short_description, str) and short_description.strip():
        has_description = True
        has_any_text = True

    return has_description, has_toc, has_any_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenBD ISBNのサンプルを調査し、書誌情報・内容紹介の取得率を集計します。"
    )
    parser.add_argument(
        "--input", default="openbd_isbns.json", help="/coverageから保存したISBN JSON (default: openbd_isbns.json)"
    )
    parser.add_argument(
        "--limit", type=int, default=1000,
        help="調査するISBN件数。0なら全件（default: 1000）。最初は1000程度を推奨。"
    )
    parser.add_argument(
        "--batch-size", type=int, default=200,
        help="1回のGETで取得するISBN件数（1〜1000、default: 200）。"
    )
    parser.add_argument(
        "--sequential", action="store_true",
        help="先頭から順に調査する（指定しない場合は全体から再現可能なランダムサンプル）。"
    )
    parser.add_argument("--seed", type=int, default=42, help="ランダム抽出のseed (default: 42)")
    parser.add_argument(
        "--pause", type=float, default=0.1,
        help="リクエスト間の待機秒数（default: 0.1）。"
    )
    parser.add_argument(
        "--report", default="openbd_audit_report.json",
        help="集計結果の出力先 (default: openbd_audit_report.json)"
    )
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit は0以上にしてください。")
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size は1〜1000にしてください。")
    if args.pause < 0:
        parser.error("--pause は0以上にしてください。")

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"入力ファイルが見つかりません: {input_path}", file=sys.stderr)
        return 2

    started = time.monotonic()
    try:
        isbns, invalid_count = load_isbns(input_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ISBN JSONの読み込みに失敗しました: {exc}", file=sys.stderr)
        return 2

    unique_count = len(isbns)
    if not isbns:
        print("有効なISBNがありません。", file=sys.stderr)
        return 2

    if args.limit == 0 or args.limit >= unique_count:
        selected = isbns
        sampling = "all"
    elif args.sequential:
        selected = isbns[:args.limit]
        sampling = "first_n"
    else:
        selected = random.Random(args.seed).sample(isbns, args.limit)
        sampling = "random_sample"

    print(f"入力ファイル: {input_path}")
    print(f"元データ件数: {unique_count + invalid_count:,}")
    print(f"有効・重複除去後: {unique_count:,}")
    print(f"形式不正: {invalid_count:,}")
    print(f"今回の調査対象: {len(selected):,} ISBN（方式: {sampling}）")
    print("注意: このスクリプトは書籍本文・内容紹介をファイルに保存せず、件数のみ集計します。")

    counts = {
        "requested_isbns": len(selected),
        "batches_attempted": 0,
        "batches_succeeded": 0,
        "batches_failed": 0,
        "records_found": 0,
        "not_found_or_null": 0,
        "records_with_title": 0,
        "records_with_author": 0,
        "records_with_publisher": 0,
        "records_with_description": 0,
        "records_with_table_of_contents": 0,
        "records_with_any_text_content": 0,
    }
    failed_batches: list[dict[str, Any]] = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": "BiblioRadar-DB-audit/0.1 (metadata availability test)"})
        for start in range(0, len(selected), args.batch_size):
            batch = selected[start : start + args.batch_size]
            batch_number = start // args.batch_size + 1
            counts["batches_attempted"] += 1
            print(
                f"[{batch_number}] ISBN {start + 1:,}〜{start + len(batch):,} / {len(selected):,}",
                flush=True,
            )
            try:
                results = get_batch(session, batch)
                counts["batches_succeeded"] += 1
            except Exception as exc:  # noqa: BLE001 - record failed batch and continue
                counts["batches_failed"] += 1
                failed_batches.append({
                    "batch_number": batch_number,
                    "start_index": start,
                    "batch_size": len(batch),
                    "error": str(exc),
                })
                print(f"  このバッチは失敗しました: {exc}")
                if args.pause:
                    time.sleep(args.pause)
                continue

            for book in results:
                if not isinstance(book, dict):
                    counts["not_found_or_null"] += 1
                    continue
                counts["records_found"] += 1
                summary = book.get("summary") or {}
                if isinstance(summary, dict):
                    if str(summary.get("title") or "").strip():
                        counts["records_with_title"] += 1
                    if str(summary.get("author") or "").strip():
                        counts["records_with_author"] += 1
                    if str(summary.get("publisher") or "").strip():
                        counts["records_with_publisher"] += 1
                has_description, has_toc, has_any_text = text_content_counts(book)
                counts["records_with_description"] += int(has_description)
                counts["records_with_table_of_contents"] += int(has_toc)
                counts["records_with_any_text_content"] += int(has_any_text)

            if args.pause:
                time.sleep(args.pause)

    elapsed = round(time.monotonic() - started, 2)
    successful_requests_records = counts["requested_isbns"] - sum(
        item["batch_size"] for item in failed_batches
    )
    if successful_requests_records:
        denominator = successful_requests_records
        desc_rate = round(100 * counts["records_with_description"] / denominator, 2)
        metadata_rate = round(100 * counts["records_found"] / denominator, 2)
    else:
        desc_rate = None
        metadata_rate = None

    report = {
        "input_file": str(input_path),
        "source": "OpenBD /get; input ISBN list expected from /coverage",
        "sampling_method": sampling,
        "random_seed": args.seed if sampling == "random_sample" else None,
        "unique_valid_isbn_count": unique_count,
        "invalid_isbn_count": invalid_count,
        "summary_counts": counts,
        "rates_percent_over_successfully_queried_isbns": {
            "metadata_found": metadata_rate,
            "description_found": desc_rate,
        },
        "failed_batches": failed_batches,
        "elapsed_seconds": elapsed,
        "note": (
            "Aggregate counts only; no book descriptions or raw API records were saved. "
            "Description availability is a technical count, not a determination that data may legally be embedded/stored."
        ),
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== 調査結果 ===")
    print(f"書誌レコード取得: {counts['records_found']:,}")
    print(f"タイトルあり: {counts['records_with_title']:,}")
    print(f"著者あり: {counts['records_with_author']:,}")
    print(f"内容紹介あり (TextType 02/03等): {counts['records_with_description']:,}")
    print(f"目次あり (TextType 04): {counts['records_with_table_of_contents']:,}")
    print(f"通信失敗バッチ: {counts['batches_failed']:,}")
    print(f"所要時間: {elapsed} 秒")
    print(f"集計レポート: {report_path}")
    print("この結果を見てから全件取得へ進むか判断してください。")
    return 0 if counts["batches_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
