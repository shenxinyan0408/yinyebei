from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from engine.catalog import MINUTE_DATA_DIR
from engine.data import MinuteDataStore

BASE_DIR = Path(__file__).resolve().parent
DATA_CONFIG_FILE = BASE_DIR / "runtime" / "data_sources.json"


def read_saved_data_config() -> dict[str, str]:
    if not DATA_CONFIG_FILE.exists():
        return {}
    try:
        payload = json.loads(DATA_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key in ("minuteDataDir",):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()
    return result


def resolve_minute_data_dir() -> Path:
    env_path = os.environ.get("MINUTE_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path)

    saved_config = read_saved_data_config()
    if saved_config.get("minuteDataDir"):
        return Path(saved_config["minuteDataDir"])

    return Path(MINUTE_DATA_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prewarm local minute cache files.")
    parser.add_argument("--start", dest="start_date", default="", help="Optional start date, YYYY-MM-DD")
    parser.add_argument("--end", dest="end_date", default="", help="Optional end date, YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="Force rebuild existing cache files.")
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("MINUTE_CACHE_DIR", "").strip(),
        help="Optional cache directory override.",
    )
    args = parser.parse_args()

    minute_data_dir = resolve_minute_data_dir()
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    store = MinuteDataStore(data_dir=minute_data_dir, cache_dir=cache_dir)

    print(f"Minute data directory: {store.data_dir}")
    print(f"Minute cache directory: {store.cache_dir}")
    summary_before = store.summarize_cache(args.start_date or None, args.end_date or None)
    print(
        f"Cache before: {summary_before['readyDates']}/{summary_before['totalDates']} ready, "
        f"{summary_before['missingDates']} missing."
    )

    last_printed = {"count": 0}

    def on_progress(progress_value: float, message: str) -> None:
        current = int(progress_value * max(summary_before["totalDates"], 1))
        should_print = (
            current == 1
            or current == summary_before["totalDates"]
            or current - last_printed["count"] >= 25
        )
        if should_print:
            print(message)
            last_printed["count"] = current

    result = store.prewarm_cache(
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        force=args.force,
        progress=on_progress,
    )

    print(
        f"Cache finished: built {result['builtDates']}, skipped {result['skippedDates']}, "
        f"failed {result['failedDates']}."
    )
    if result["failedDates"]:
        print("Failures:")
        for failure in result["failures"][:20]:
            print(f"  {failure['date']}: {failure['message']}")
    print(
        f"Cache after: {result['readyDates']}/{result['totalDates']} ready, "
        f"{result['missingDates']} missing."
    )
    return 1 if result["failedDates"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
