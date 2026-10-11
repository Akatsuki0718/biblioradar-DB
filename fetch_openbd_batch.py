#!/usr/bin/env python3
"""Fetch OpenBD records in batches and save them as resumable JSONL.

Safety defaults:
- Only the first 1,000 ISBNs are processed unless --all is specified.
- Uses the existing OpenBD /coverage ISBN list as input.
- Saves the raw OpenBD record as returned, wrapped with requested ISBN/index/timestamp.
- Checkpoints after every successful batch and can resume safely after interruption.

Before large-scale collection, review OpenBD's current terms and ensure your intended
long-term caching and derived-vector use are permitted. This script does not create vectors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_URL = "https://api.openbd.jp/v1/get"
DEFAULT_INPUT = "openbd_isbns_jp.json"
DEFAULT_OUTPUT = "openbd_records.jsonl"
DEFAULT_CHECKPOINT = "openbd_fetch_checkpoint.json"
DEFAULT_BATCH_SIZE = 200
DEFAULT_LIMIT = 1000
MAX_RETRIES = 5

ISBN_RE = re.compile(r"^(?:\d{13}|\d{9}[\dXx])$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_isbn(value: Any) -> str | None:
    """Normalize ISBN representation, retaining digits and optional ISBN-10 X."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[\s-]", "", text)
    if not ISBN_RE.fullmatch(text):
        return None
    return text.upper()


def load_isbns(path: Path) -> tuple[list[str], int, int, str]:
    """Load and deduplicate a JSON list of ISBNs; return normalized list and stats."""
    raw_bytes = path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"ISBN JSONを読み込めません: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("入力JSONの最上位はISBNの配列である必要があります。")

    valid: list[str] = []
    seen: set[str] = set()
    invalid = 0
    duplicates = 0
    for item in payload:
        # The expected format is a list of ISBN strings. For convenience, also accept
        # small dicts containing one of the common ISBN keys.
        if isinstance(item, dict):
            candidate = item.get("isbn") or item.get("ISBN") or item.get("isbn13")
        else:
            candidate = item
        isbn = normalize_isbn(candidate)
        if isbn is None:
            invalid += 1
            continue
        if isbn in seen:
            duplicates += 1
            continue
        seen.add(isbn)
        valid.append(isbn)
    return valid, invalid, duplicates, digest


def text_content_types(record: dict[str, Any]) -> set[str]:
    """Get TextType codes present in an OpenBD record."""
    onix = record.get("onix") or {}
    if not isinstance(onix, dict):
        return set()
    collateral = onix.get("CollateralDetail") or {}
    if not isinstance(collateral, dict):
        return set()
    items = collateral.get("TextContent") or []
    if not isinstance(items, list):
        return set()
    result: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("TextType")
        if value is None:
            continue
        code = str(value).strip()
        result.add(code.zfill(2) if code.isdigit() else code)
    return result


def record_has_content_type(record: dict[str, Any], codes: set[str]) -> bool:
    onix = record.get("onix") or {}
    collateral = onix.get("CollateralDetail") or {} if isinstance(onix, dict) else {}
    items = collateral.get("TextContent") or [] if isinstance(collateral, dict) else []
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        code = item.get("TextType")
        if code is None:
            continue
        code = str(code).strip()
        code = code.zfill(2) if code.isdigit() else code
        text = item.get("Text")
        if code in codes and isinstance(text, str) and text.strip():
            return True
    return False


def record_flags(record: Any) -> dict[str, bool]:
    if not isinstance(record, dict):
        return {
            "record_found": False,
            "title": False,
            "author": False,
            "description": False,
            "toc": False,
        }
    summary = record.get("summary") or {}
    if not isinstance(summary, dict):
        summary = {}
    author = summary.get("author")
    return {
        "record_found": True,
        "title": bool(str(summary.get("title") or "").strip()),
        "author": bool(str(author or "").strip()),
        # Match the audit's current classification: TextType 02/03 as description;
        # TextType 04 as table of contents. The raw record is still preserved unchanged.
        "description": record_has_content_type(record, {"02", "03"}),
        "toc": record_has_content_type(record, {"04"}),
    }


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def fetch_batch(session: requests.Session, isbns: list[str], timeout: float) -> list[Any]:
    """Fetch one batch, retrying transient errors. Raises after retries are exhausted."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(API_URL, params={"isbn": ",".join(isbns)}, timeout=timeout)
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise requests.HTTPError(f"Retryable HTTP status: {response.status_code}")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("OpenBD API response is not a JSON array")
            if len(data) != len(isbns):
                raise ValueError(
                    f"OpenBD response count mismatch: requested={len(isbns)}, returned={len(data)}"
                )
            return data
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES - 1:
                break
            delay = min(30.0, (2 ** attempt) + random.uniform(0, 0.5))
            print(
                f"  一時的な取得失敗 ({attempt + 1}/{MAX_RETRIES}): {exc}; "
                f"{delay:.1f}秒後に再試行",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError(f"バッチ取得に失敗しました: {last_error}") from last_error


def empty_stats() -> dict[str, int]:
    return {
        "isbn_requests_succeeded": 0,
        "records_found": 0,
        "records_with_title": 0,
        "records_with_author": 0,
        "records_with_description": 0,
        "records_with_toc": 0,
        "records_with_description_and_toc": 0,
        "records_with_description_or_toc": 0,
        "null_records": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenBDのISBN一覧から書誌レコードを一括取得し、JSONLに保存します。"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"ISBN一覧JSON (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"出力JSONL (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help=f"進捗ファイル (default: {DEFAULT_CHECKPOINT})")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"処理件数。既定値は{DEFAULT_LIMIT}件")
    parser.add_argument("--all", action="store_true", help="ISBN一覧の全件を処理（利用条件を確認してから実行）")
    parser.add_argument("--offset", type=int, default=0, help="入力ISBN一覧の開始位置（0始まり）")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"1回に問い合わせるISBN数 (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--pause", type=float, default=0.1, help="成功バッチ間の待機秒数 (default: 0.1)")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTPタイムアウト秒数 (default: 30)")
    parser.add_argument("--fresh", action="store_true", help="出力とチェックポイントを作り直して最初から実行")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    checkpoint_path = Path(args.checkpoint)

    if args.batch_size < 1 or args.batch_size > 1000:
        print("--batch-size は1〜1000の範囲で指定してください。", file=sys.stderr)
        return 2
    if args.offset < 0 or args.limit < 1 or args.pause < 0 or args.timeout <= 0:
        print("offset/limit/pause/timeout の指定が不正です。", file=sys.stderr)
        return 2
    if not input_path.is_file():
        print(f"入力ファイルがありません: {input_path}", file=sys.stderr)
        return 2

    try:
        isbns, invalid_count, duplicate_count, input_sha256 = load_isbns(input_path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.offset >= len(isbns):
        print(f"--offset が有効ISBN件数を超えています: {args.offset} >= {len(isbns)}", file=sys.stderr)
        return 2

    selected = isbns[args.offset:]
    if not args.all:
        selected = selected[:args.limit]
    if not selected:
        print("処理対象のISBNがありません。", file=sys.stderr)
        return 2

    run_config = {
        "input_path": str(input_path.resolve()),
        "input_sha256": input_sha256,
        "output_path": str(output_path.resolve()),
        "offset": args.offset,
        "target_count": len(selected),
        "batch_size": args.batch_size,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    if args.fresh:
        for path in (output_path, checkpoint_path):
            if path.exists():
                path.unlink()

    if checkpoint_path.exists():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"チェックポイントを読めません: {exc}; --fresh で作り直してください。", file=sys.stderr)
            return 2
        old_config = checkpoint.get("config")
        if old_config != run_config:
            print("既存チェックポイントの入力・件数・バッチ設定が今回と異なります。--fresh を指定してください。", file=sys.stderr)
            return 2
        next_index = int(checkpoint.get("next_index", 0))
        stats = checkpoint.get("stats") or empty_stats()
        output_bytes = int(checkpoint.get("output_bytes", 0))
        if output_path.exists():
            current_size = output_path.stat().st_size
            if current_size < output_bytes:
                print("出力ファイルがチェックポイントより短いため、安全に再開できません。--fresh を指定してください。", file=sys.stderr)
                return 2
            # Discard any batch that reached disk but whose checkpoint didn't commit.
            with output_path.open("r+b") as handle:
                handle.truncate(output_bytes)
        elif output_bytes != 0:
            print("チェックポイントに記録された出力ファイルがありません。--fresh を指定してください。", file=sys.stderr)
            return 2
        print(f"既存チェックポイントから再開: {next_index}/{len(selected)} ISBN")
    else:
        if output_path.exists() and output_path.stat().st_size > 0:
            print(f"出力ファイルが既に存在します: {output_path}; --fresh または別の --output を指定してください。", file=sys.stderr)
            return 2
        next_index = 0
        stats = empty_stats()
        output_bytes = 0
        checkpoint = {
            "config": run_config,
            "created_at": utc_now(),
            "next_index": 0,
            "output_bytes": 0,
            "stats": stats,
            "last_updated_at": utc_now(),
        }
        atomic_write_json(checkpoint_path, checkpoint)

    total_batches = (len(selected) + args.batch_size - 1) // args.batch_size
    done_batches = (next_index + args.batch_size - 1) // args.batch_size
    print(f"入力ファイル: {input_path}")
    print(f"有効ISBN: {len(isbns):,}; 形式不正: {invalid_count:,}; 重複除去数: {duplicate_count:,}")
    print(f"今回の対象: {len(selected):,} ISBN (offset={args.offset}, all={args.all})")
    print(f"バッチサイズ: {args.batch_size}; 完了位置: {next_index}; 出力: {output_path}")
    print("出力にはOpenBDの返却レコードが含まれます。大量取得・長期保存・ベクトル化の前に利用条件を確認してください。")

    session = requests.Session()
    start_time = time.monotonic()
    try:
        with output_path.open("ab") as output:
            while next_index < len(selected):
                batch_start = next_index
                batch_isbns = selected[batch_start : batch_start + args.batch_size]
                batch_number = batch_start // args.batch_size + 1
                print(f"[{batch_number}/{total_batches}] ISBN {batch_start + 1}〜{batch_start + len(batch_isbns)} / {len(selected)}")

                records = fetch_batch(session, batch_isbns, args.timeout)
                fetched_at = utc_now()
                batch_delta = empty_stats()
                lines: list[str] = []
                for relative_idx, (isbn, record) in enumerate(zip(batch_isbns, records)):
                    flags = record_flags(record)
                    batch_delta["isbn_requests_succeeded"] += 1
                    if flags["record_found"]:
                        batch_delta["records_found"] += 1
                    else:
                        batch_delta["null_records"] += 1
                    if flags["title"]:
                        batch_delta["records_with_title"] += 1
                    if flags["author"]:
                        batch_delta["records_with_author"] += 1
                    if flags["description"]:
                        batch_delta["records_with_description"] += 1
                    if flags["toc"]:
                        batch_delta["records_with_toc"] += 1
                    if flags["description"] and flags["toc"]:
                        batch_delta["records_with_description_and_toc"] += 1
                    if flags["description"] or flags["toc"]:
                        batch_delta["records_with_description_or_toc"] += 1
                    row = {
                        "input_index": args.offset + batch_start + relative_idx,
                        "requested_isbn": isbn,
                        "fetched_at": fetched_at,
                        "record": record,
                    }
                    lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

                payload = ("\n".join(lines) + "\n").encode("utf-8")
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
                output_bytes += len(payload)

                for key, value in batch_delta.items():
                    stats[key] = int(stats.get(key, 0)) + value
                next_index += len(batch_isbns)
                checkpoint = {
                    "config": run_config,
                    "created_at": checkpoint.get("created_at", utc_now()),
                    "next_index": next_index,
                    "output_bytes": output_bytes,
                    "stats": stats,
                    "last_updated_at": utc_now(),
                }
                atomic_write_json(checkpoint_path, checkpoint)
                if args.pause:
                    time.sleep(args.pause)
    except KeyboardInterrupt:
        print("\n中断されました。保存済みのチェックポイントから再開できます。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"取得処理を停止しました: {exc}", file=sys.stderr)
        print("成功済みバッチは保存済みです。同じコマンドを再実行すると再開できます。", file=sys.stderr)
        return 1
    finally:
        session.close()

    elapsed = time.monotonic() - start_time
    report = {
        "config": run_config,
        "finished_at": utc_now(),
        "elapsed_seconds": round(elapsed, 2),
        "stats": stats,
        "output_path": str(output_path.resolve()),
        "checkpoint_path": str(checkpoint_path.resolve()),
    }
    report_path = output_path.with_name("openbd_fetch_report.json")
    atomic_write_json(report_path, report)

    requested = stats["isbn_requests_succeeded"] or 1
    print("\n=== 取得結果 ===")
    print(f"ISBN照会成功: {stats['isbn_requests_succeeded']:,} / {len(selected):,}")
    print(f"書誌レコード取得: {stats['records_found']:,} ({stats['records_found'] / requested:.2%})")
    print(f"タイトルあり: {stats['records_with_title']:,} ({stats['records_with_title'] / requested:.2%})")
    print(f"著者あり: {stats['records_with_author']:,} ({stats['records_with_author'] / requested:.2%})")
    print(f"内容紹介あり (TextType 02/03): {stats['records_with_description']:,} ({stats['records_with_description'] / requested:.2%})")
    print(f"目次あり (TextType 04): {stats['records_with_toc']:,} ({stats['records_with_toc'] / requested:.2%})")
    print(f"内容紹介と目次の両方: {stats['records_with_description_and_toc']:,}")
    print(f"内容紹介または目次あり: {stats['records_with_description_or_toc']:,}")
    print(f"nullレコード: {stats['null_records']:,}")
    print(f"所要時間: {elapsed:.2f}秒")
    print(f"生データ(JSONL): {output_path}")
    print(f"レポート: {report_path}")
    print("注意: この取得率は技術的な取得率です。ベクトル化・長期保存の利用条件を別途確認してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
