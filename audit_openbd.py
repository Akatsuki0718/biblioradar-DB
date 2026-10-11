#!/usr/bin/env python3
"""Sample OpenBD coverage and description availability, with resumable batches.

This audit script stores aggregate counts and the sampled ISBNs/checkpoint only.
It does NOT save raw OpenBD responses, descriptions, or tables of contents.

Usage:
    python audit_openbd.py --limit 10000 --batch-size 200
    # If stopped, rerun the same command; completed batches will be skipped.
    # To discard the previous sample and start again:
    python audit_openbd.py --limit 10000 --batch-size 200 --fresh
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_URL = "https://api.openbd.jp/v1/get"
CHECKPOINT_VERSION = 2
DEFAULT_INPUT = "openbd_isbns_jp.json"
DEFAULT_CHECKPOINT = "openbd_audit_checkpoint.json"
DEFAULT_REPORT = "openbd_audit_report.json"
ISBN_RE = re.compile(r"^(?:\d{13}|\d{9}[\dXx])$")

COUNTER_KEYS = (
    "records_found",
    "not_found_or_null",
    "records_with_title",
    "records_with_author",
    "records_with_publisher",
    "records_with_description",
    "records_with_table_of_contents",
    "records_with_description_and_toc",
    "records_with_description_only",
    "records_with_toc_only",
    "records_with_any_text_content",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_isbn(value: Any) -> str | None:
    """Normalize ISBN-like strings; this validates format, not check digits."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    isbn = re.sub(r"[\s-]", "", str(value)).upper()
    return isbn if ISBN_RE.fullmatch(isbn) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_isbns(path: Path) -> tuple[list[str], int, int]:
    """Load ISBNs, returning (unique ISBNs, invalid-format count, raw item count)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        for key in ("isbns", "coverage", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError("入力JSONはISBN文字列の配列、または isbns/coverage/items 配列を含むJSONである必要があります。")

    raw_count = len(data)
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
    return unique, invalid_count, raw_count


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically so an interrupted write does not corrupt checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
    temp_path.replace(path)


def get_batch(
    session: requests.Session,
    isbns: list[str],
    retries: int,
    timeout: tuple[float, float],
) -> list[Any]:
    """Fetch one batch. Keep batches small enough to avoid overly long URLs."""
    if not isbns:
        return []
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(
                API_URL,
                params={"isbn": ",".join(isbns)},
                timeout=timeout,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, list):
                raise ValueError("OpenBDの応答がJSON配列ではありません。")
            if len(body) != len(isbns):
                raise ValueError(
                    f"応答件数が要求件数と一致しません（要求={len(isbns)}, 応答={len(body)}）。"
                )
            return body
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                wait = min(2 ** attempt, 8)
                print(f"  API取得エラー。{wait}秒後に再試行します: {exc}", flush=True)
                time.sleep(wait)
    raise RuntimeError(f"バッチ取得に失敗しました: {last_error}")


def get_text_entries(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ONIX TextContent entries without retaining their text in output."""
    onix = book.get("onix") or {}
    collateral = onix.get("CollateralDetail") or {}
    entries = collateral.get("TextContent") or []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def inspect_book(book: dict[str, Any] | None) -> dict[str, Any]:
    """Return count flags for one response record; never return/store text itself."""
    result = {key: 0 for key in COUNTER_KEYS}
    result["text_type_counts"] = {}
    if not isinstance(book, dict):
        result["not_found_or_null"] = 1
        return result

    result["records_found"] = 1
    summary = book.get("summary") or {}
    if isinstance(summary, dict):
        result["records_with_title"] = int(bool(str(summary.get("title") or "").strip()))
        result["records_with_author"] = int(bool(str(summary.get("author") or "").strip()))
        result["records_with_publisher"] = int(bool(str(summary.get("publisher") or "").strip()))

    has_description = False
    has_toc = False
    has_any_text = False
    type_counts: dict[str, int] = {}
    for entry in get_text_entries(book):
        text_value = entry.get("Text")
        if not isinstance(text_value, str) or not text_value.strip():
            continue
        has_any_text = True
        raw_code = str(entry.get("TextType", "")).strip()
        code = raw_code.zfill(2) if raw_code else "unknown"
        type_counts[code] = type_counts.get(code, 0) + 1
        if code in {"02", "03"}:
            has_description = True
        elif code == "04":
            has_toc = True

    # openBD's hanmoto field may also contain a short explanation.
    hanmoto = book.get("hanmoto") or {}
    if isinstance(hanmoto, dict):
        short_description = hanmoto.get("kaisetsu105w")
        if isinstance(short_description, str) and short_description.strip():
            has_description = True
            has_any_text = True

    result["records_with_description"] = int(has_description)
    result["records_with_table_of_contents"] = int(has_toc)
    result["records_with_description_and_toc"] = int(has_description and has_toc)
    result["records_with_description_only"] = int(has_description and not has_toc)
    result["records_with_toc_only"] = int(has_toc and not has_description)
    result["records_with_any_text_content"] = int(has_any_text)
    result["text_type_counts"] = type_counts
    return result


def empty_batch_counts() -> dict[str, Any]:
    result: dict[str, Any] = {key: 0 for key in COUNTER_KEYS}
    result["text_type_counts"] = {}
    return result


def summarize_batch(records: list[Any]) -> dict[str, Any]:
    total = empty_batch_counts()
    for record in records:
        item = inspect_book(record if isinstance(record, dict) else None)
        for key in COUNTER_KEYS:
            total[key] += item[key]
        for code, count in item["text_type_counts"].items():
            total["text_type_counts"][code] = total["text_type_counts"].get(code, 0) + count
    return total


def aggregate_completed_batches(checkpoint: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    totals = empty_batch_counts()
    successful_isbns = 0
    completed = 0
    for batch in checkpoint["batches"]:
        if batch.get("status") != "done":
            continue
        completed += 1
        successful_isbns += batch["size"]
        batch_counts = batch["counts"]
        for key in COUNTER_KEYS:
            totals[key] += batch_counts.get(key, 0)
        for code, count in batch_counts.get("text_type_counts", {}).items():
            totals["text_type_counts"][code] = totals["text_type_counts"].get(code, 0) + count
    return totals, successful_isbns, completed


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denom
    margin = z * ((p * (1 - p) / total + z2 / (4 * total * total)) ** 0.5) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def rate_and_projection(successes: int, sample_n: int, population_n: int) -> dict[str, Any]:
    if sample_n <= 0:
        return {"rate_percent": None, "estimated_population_count": None, "estimated_count_95pct_interval": None}
    rate = successes / sample_n
    interval = wilson_interval(successes, sample_n)
    return {
        "rate_percent": round(rate * 100, 3),
        "estimated_population_count": round(rate * population_n),
        "estimated_count_95pct_interval": (
            [round(interval[0] * population_n), round(interval[1] * population_n)] if interval else None
        ),
    }


def checkpoint_config_matches(saved: dict[str, Any], expected: dict[str, Any]) -> bool:
    keys = ("input_sha256", "unique_valid_isbn_count", "limit", "seed", "sequential", "batch_size")
    return all(saved.get(key) == expected.get(key) for key in keys)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenBDのISBNサンプルを調査し、書誌情報・内容紹介・目次の取得率を集計します。"
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"/coverageから保存したISBN JSON (default: {DEFAULT_INPUT})")
    parser.add_argument("--limit", type=int, default=10000, help="調査するISBN件数（default: 10000）")
    parser.add_argument("--batch-size", type=int, default=200, help="1回のGETで取得するISBN件数（1〜1000、default: 200）")
    parser.add_argument("--sequential", action="store_true", help="先頭から調査。デフォルトは再現可能なランダムサンプル")
    parser.add_argument("--seed", type=int, default=42, help="ランダム抽出のseed（default: 42）")
    parser.add_argument("--pause", type=float, default=0.1, help="バッチ間の待機秒数（default: 0.1）")
    parser.add_argument("--retries", type=int, default=3, help="失敗バッチの再試行回数（default: 3）")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help=f"途中経過ファイル（default: {DEFAULT_CHECKPOINT}）")
    parser.add_argument("--report", default=DEFAULT_REPORT, help=f"集計レポート（default: {DEFAULT_REPORT}）")
    parser.add_argument("--fresh", action="store_true", help="既存チェックポイントを使わず、新しいサンプルで最初から実行")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit は1以上にしてください（全件調査ではなくサンプル調査を想定しています）。")
    if not 1 <= args.batch_size <= 1000:
        parser.error("--batch-size は1〜1000にしてください。URLが長くなりすぎる場合は200程度を推奨します。")
    if args.pause < 0:
        parser.error("--pause は0以上にしてください。")
    if args.retries < 0:
        parser.error("--retries は0以上にしてください。")

    input_path = Path(args.input)
    checkpoint_path = Path(args.checkpoint)
    report_path = Path(args.report)
    if not input_path.is_file():
        print(f"入力ファイルが見つかりません: {input_path}", file=sys.stderr)
        return 2

    started = time.monotonic()
    try:
        isbns, invalid_count, raw_count = load_isbns(input_path)
        input_hash = sha256_file(input_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ISBN JSONの読み込みに失敗しました: {exc}", file=sys.stderr)
        return 2

    if not isbns:
        print("有効なISBNがありません。", file=sys.stderr)
        return 2

    if args.limit >= len(isbns):
        selected = isbns
        sampling = "all_because_limit_at_least_population"
    elif args.sequential:
        selected = isbns[: args.limit]
        sampling = "first_n"
    else:
        selected = random.Random(args.seed).sample(isbns, args.limit)
        sampling = "random_sample"

    expected_config = {
        "input_file": str(input_path),
        "input_sha256": input_hash,
        "raw_item_count": raw_count,
        "unique_valid_isbn_count": len(isbns),
        "invalid_format_count": invalid_count,
        "limit": len(selected),
        "seed": args.seed,
        "sequential": bool(args.sequential),
        "sampling_method": sampling,
        "batch_size": args.batch_size,
    }

    checkpoint: dict[str, Any]
    if checkpoint_path.exists() and not args.fresh:
        try:
            with checkpoint_path.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") != CHECKPOINT_VERSION or not checkpoint_config_matches(saved.get("config", {}), expected_config):
                print(
                    f"既存チェックポイント {checkpoint_path} の設定が今回と一致しません。\n"
                    "同じ設定で再開するか、別の --checkpoint を指定するか、--fresh を付けて新規開始してください。",
                    file=sys.stderr,
                )
                return 2
            checkpoint = saved
            print(f"既存チェックポイントから再開: {checkpoint_path}")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"チェックポイントを読み込めません: {exc}。--fresh で作り直してください。", file=sys.stderr)
            return 2
    else:
        batch_specs = []
        for start in range(0, len(selected), args.batch_size):
            batch_specs.append({
                "batch_number": len(batch_specs) + 1,
                "start_index": start,
                "size": len(selected[start : start + args.batch_size]),
                "status": "pending",
                "attempts": 0,
                "last_error": None,
                "counts": None,
            })
        checkpoint = {
            "version": CHECKPOINT_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "config": expected_config,
            "sample_isbns": selected,
            "batches": batch_specs,
            "note": "Checkpoint stores sample ISBNs and aggregate counts only; raw OpenBD responses/descriptions are not stored.",
        }
        atomic_write_json(checkpoint_path, checkpoint)

    pending = [batch for batch in checkpoint["batches"] if batch.get("status") != "done"]
    print(f"入力ファイル: {input_path}")
    print(f"元データ件数: {raw_count:,}")
    print(f"有効・重複除去後: {len(isbns):,}")
    print(f"形式不正: {invalid_count:,}")
    print(f"今回の調査対象: {len(selected):,} ISBN（方式: {sampling}、seed: {args.seed}）")
    print(f"バッチ数: {len(checkpoint['batches']):,}、未完了: {len(pending):,}")
    print("保存するのはサンプルISBN・集計件数・進捗のみです。書誌レコードや内容紹介の本文は保存しません。")

    with requests.Session() as session:
        session.headers.update({"User-Agent": "BiblioRadar-DB-audit/0.2 (metadata availability test)"})
        for batch in pending:
            batch_num = batch["batch_number"]
            start = batch["start_index"]
            batch_isbns = checkpoint["sample_isbns"][start : start + batch["size"]]
            print(
                f"[{batch_num}/{len(checkpoint['batches'])}] ISBNサンプル {start + 1:,}〜{start + len(batch_isbns):,} / {len(selected):,}",
                flush=True,
            )
            batch["attempts"] = int(batch.get("attempts", 0)) + 1
            try:
                records = get_batch(session, batch_isbns, args.retries, timeout=(10, 45))
                batch["counts"] = summarize_batch(records)
                batch["status"] = "done"
                batch["last_error"] = None
            except Exception as exc:  # record error and leave batch resumable
                batch["status"] = "pending"
                batch["last_error"] = str(exc)
                print(f"  バッチ失敗（次回実行時に再試行）: {exc}", flush=True)
            checkpoint["updated_at"] = utc_now()
            atomic_write_json(checkpoint_path, checkpoint)
            if args.pause:
                time.sleep(args.pause)

    total_counts, successful_isbns, completed_batches = aggregate_completed_batches(checkpoint)
    failed_batches = [
        {"batch_number": b["batch_number"], "start_index": b["start_index"], "size": b["size"], "attempts": b["attempts"], "last_error": b["last_error"]}
        for b in checkpoint["batches"] if b.get("status") != "done"
    ]
    population_n = len(isbns)
    metrics = {
        "metadata_found": rate_and_projection(total_counts["records_found"], successful_isbns, population_n),
        "title_found": rate_and_projection(total_counts["records_with_title"], successful_isbns, population_n),
        "author_found": rate_and_projection(total_counts["records_with_author"], successful_isbns, population_n),
        "description_found": rate_and_projection(total_counts["records_with_description"], successful_isbns, population_n),
        "table_of_contents_found": rate_and_projection(total_counts["records_with_table_of_contents"], successful_isbns, population_n),
        "description_and_toc_both": rate_and_projection(total_counts["records_with_description_and_toc"], successful_isbns, population_n),
        "description_only": rate_and_projection(total_counts["records_with_description_only"], successful_isbns, population_n),
        "toc_only": rate_and_projection(total_counts["records_with_toc_only"], successful_isbns, population_n),
        "any_text_content": rate_and_projection(total_counts["records_with_any_text_content"], successful_isbns, population_n),
    }
    elapsed = round(time.monotonic() - started, 2)
    report = {
        "input_file": str(input_path),
        "source": "OpenBD /get; input ISBN list expected from /coverage",
        "sampling_method": sampling,
        "random_seed": args.seed if sampling == "random_sample" else None,
        "population": {
            "raw_items": raw_count,
            "unique_valid_isbns": population_n,
            "invalid_format_items": invalid_count,
        },
        "sample": {
            "requested_isbns": len(selected),
            "successfully_queried_isbns": successful_isbns,
            "completed_batches": completed_batches,
            "total_batches": len(checkpoint["batches"]),
            "pending_or_failed_batches": len(failed_batches),
        },
        "counts": total_counts,
        "rates_and_full_population_estimates": metrics,
        "failed_batches": failed_batches,
        "elapsed_seconds_this_run": elapsed,
        "checkpoint_file": str(checkpoint_path),
        "note": (
            "Estimates extrapolate from the successfully queried sample and are not actual full-corpus counts. "
            "95% intervals use Wilson binomial intervals and do not correct for any sampling bias. "
            "Text bodies/raw API records were not saved. Technical availability does not establish permission to store/embed data."
        ),
    }
    atomic_write_json(report_path, report)

    denominator = successful_isbns
    def display_rate(key: str) -> str:
        value = metrics[key]["rate_percent"]
        return "N/A" if value is None else f"{value:.3f}%"

    print("\n=== 調査結果（成功したバッチのみ）===")
    print(f"ISBN照会成功: {successful_isbns:,} / {len(selected):,}")
    print(f"書誌レコード取得: {total_counts['records_found']:,} ({display_rate('metadata_found')})")
    print(f"タイトルあり: {total_counts['records_with_title']:,} ({display_rate('title_found')})")
    print(f"著者あり: {total_counts['records_with_author']:,} ({display_rate('author_found')})")
    print(f"内容紹介あり: {total_counts['records_with_description']:,} ({display_rate('description_found')})")
    print(f"目次あり: {total_counts['records_with_table_of_contents']:,} ({display_rate('table_of_contents_found')})")
    print(f"内容紹介と目次の両方: {total_counts['records_with_description_and_toc']:,}")
    print(f"内容紹介のみ: {total_counts['records_with_description_only']:,}")
    print(f"目次のみ: {total_counts['records_with_toc_only']:,}")
    print(f"内容紹介ありの全体推定: {metrics['description_found']['estimated_population_count']}")
    print(f"内容紹介ありの95%推定区間: {metrics['description_found']['estimated_count_95pct_interval']}")
    print(f"未完了バッチ: {len(failed_batches):,}")
    print(f"今回の実行時間: {elapsed} 秒")
    print(f"チェックポイント: {checkpoint_path}")
    print(f"集計レポート: {report_path}")
    if failed_batches:
        print("一部バッチが未完了です。同じコマンドを再実行すると、完了済みバッチを飛ばして未完了分を再試行します。")
        return 1
    print("調査完了。別のサンプルを取り直す場合は --fresh を指定してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())