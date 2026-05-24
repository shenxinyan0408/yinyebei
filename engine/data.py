from __future__ import annotations

import functools
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import scipy.io

from .catalog import MINUTE_DATA_DIR, PRICE_FIELDS, RAW_EXPRESSION_FIELDS, VOLUME_LIKE_FIELDS

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MINUTE_CACHE_DIR = BASE_DIR / "runtime" / "minute_cache"


@dataclass
class DayData:
    date: str
    minute_labels: tuple[str, ...]
    stock_list: tuple[str, ...]
    fields: dict[str, np.ndarray]
    field_invalid_masks: dict[str, np.ndarray]
    trade_open: np.ndarray
    trade_open_valid: np.ndarray


CacheProgressCallback = Callable[[float, str], None]


class MinuteDataStore:
    def __init__(
        self,
        data_dir: Path | None = None,
        first_valid_cutoff: str = "09:35",
        cache_dir: Path | None = None,
    ):
        self.data_dir = Path(data_dir or MINUTE_DATA_DIR)
        self.first_valid_cutoff = first_valid_cutoff
        self.cache_dir = Path(
            cache_dir or os.environ.get("MINUTE_CACHE_DIR") or DEFAULT_MINUTE_CACHE_DIR
        )
        self.date_to_path = self._scan_data_dir()
        self.dates = tuple(sorted(self.date_to_path))
        self._date_index = {date: index for index, date in enumerate(self.dates)}
        self._cache_dir_ready = False
        self._cache_lock_guard = threading.Lock()
        self._cache_locks: dict[str, threading.Lock] = {}

    def _scan_data_dir(self) -> dict[str, Path]:
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Minute data directory not found: {self.data_dir}")
        mapping: dict[str, Path] = {}
        for path in sorted(self.data_dir.glob("Minute*.mat")):
            raw = path.stem.replace("Minute", "")
            date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            mapping[date] = path
        if not mapping:
            raise FileNotFoundError(f"No .mat files found under: {self.data_dir}")
        return mapping

    def available_dates(self) -> tuple[str, ...]:
        return self.dates

    def resolve_range(self, start_date: str, end_date: str) -> list[str]:
        return [date for date in self.dates if start_date <= date <= end_date]

    def previous_trading_day(self, date: str) -> str | None:
        index = self._date_index.get(date)
        if index is None or index == 0:
            return None
        return self.dates[index - 1]

    def next_trading_day(self, date: str) -> str | None:
        index = self._date_index.get(date)
        if index is None or index == len(self.dates) - 1:
            return None
        return self.dates[index + 1]

    def load_day(self, date: str, requested_fields: set[str] | tuple[str, ...] | list[str]) -> DayData:
        normalized_fields = tuple(sorted(set(requested_fields) | {"MINUTE_OPEN"}))
        return self._load_day_cached(date, normalized_fields)

    def summarize_cache(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        dates = self._resolve_cache_dates(start_date, end_date)
        ready_dates = 0
        for date in dates:
            source_path = self.date_to_path[date]
            cache_path = self._cache_path_for_date(date)
            if self._is_cache_usable(cache_path, source_path):
                ready_dates += 1
        return {
            "cacheDir": str(self.cache_dir),
            "totalDates": len(dates),
            "readyDates": ready_dates,
            "missingDates": max(len(dates) - ready_dates, 0),
            "startDate": dates[0] if dates else "",
            "endDate": dates[-1] if dates else "",
        }

    def prewarm_cache(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        force: bool = False,
        progress: CacheProgressCallback | None = None,
    ) -> dict[str, Any]:
        progress = progress or (lambda *_args: None)
        dates = self._resolve_cache_dates(start_date, end_date)
        if not dates:
            raise ValueError("No trading dates available for cache prewarm.")

        built_dates = 0
        skipped_dates = 0
        failed_dates = 0
        failures: list[dict[str, str]] = []
        progress(0.0, f"Preparing minute cache for {len(dates)} trading days...")

        for index, date in enumerate(dates, start=1):
            source_path = self.date_to_path[date]
            cache_path = self._cache_path_for_date(date)
            try:
                if not force and self._is_cache_usable(cache_path, source_path):
                    skipped_dates += 1
                    action = "already cached"
                else:
                    self._rebuild_cache_for_date(date, source_path)
                    built_dates += 1
                    action = "cached"
            except Exception as exc:
                failed_dates += 1
                failures.append({"date": date, "message": str(exc)})
                action = f"failed: {exc}"

            progress(
                index / max(len(dates), 1),
                f"{date} {action} ({index}/{len(dates)})",
            )

        result = self.summarize_cache(start_date, end_date)
        result.update(
            {
                "builtDates": built_dates,
                "skippedDates": skipped_dates,
                "failedDates": failed_dates,
                "failures": failures,
                "force": force,
            }
        )
        return result

    @functools.lru_cache(maxsize=256)
    def _load_day_cached(self, date: str, requested_fields: tuple[str, ...]) -> DayData:
        source_path = self.date_to_path.get(date)
        if source_path is None:
            raise FileNotFoundError(f"No minute data file for date: {date}")

        cached_payload = self._load_or_build_cached_payload(date, source_path)
        minute_labels = tuple(str(item) for item in cached_payload["minute_labels"].tolist())
        stock_list = tuple(str(item) for item in cached_payload["stock_list"].tolist())
        open_index = minute_labels.index("09:25")
        cutoff_index = minute_labels.index(self.first_valid_cutoff)

        processed_fields: dict[str, np.ndarray] = {}
        invalid_masks: dict[str, np.ndarray] = {}
        for field_name in requested_fields:
            processed_fields[field_name] = np.asarray(
                cached_payload[f"field::{field_name}"],
                dtype=np.float64,
            )
            invalid_masks[field_name] = np.asarray(
                cached_payload[f"invalid::{field_name}"],
                dtype=bool,
            )

        raw_open_first_valid = np.asarray(cached_payload["meta::raw_open_first_valid"], dtype=np.int32)
        raw_open_all_nan = np.asarray(cached_payload["meta::raw_open_all_nan"], dtype=bool)
        trade_open = processed_fields["MINUTE_OPEN"][open_index].copy()
        timely_open = (~raw_open_all_nan) & (raw_open_first_valid <= cutoff_index)
        trade_open_valid = (
            ~invalid_masks["MINUTE_OPEN"]
            & timely_open
            & np.isfinite(trade_open)
            & (trade_open > 0)
        )
        trade_open = np.where(trade_open_valid, trade_open, np.nan)

        return DayData(
            date=date,
            minute_labels=minute_labels,
            stock_list=stock_list,
            fields=processed_fields,
            field_invalid_masks=invalid_masks,
            trade_open=trade_open,
            trade_open_valid=trade_open_valid,
        )

    def _load_or_build_cached_payload(self, date: str, source_path: Path) -> dict[str, np.ndarray]:
        cache_path = self._cache_path_for_date(date)
        cache_lock = self._cache_lock_for_date(date)
        with cache_lock:
            if self._is_cache_usable(cache_path, source_path):
                try:
                    return self._read_cache_file(cache_path)
                except Exception:
                    pass

            payload = self._build_cache_payload(source_path)
            self._write_cache_file(cache_path, payload)
            return payload

    def _rebuild_cache_for_date(self, date: str, source_path: Path) -> None:
        cache_path = self._cache_path_for_date(date)
        cache_lock = self._cache_lock_for_date(date)
        with cache_lock:
            payload = self._build_cache_payload(source_path)
            self._write_cache_file(cache_path, payload)

    def _cache_path_for_date(self, date: str) -> Path:
        return self.cache_dir / f"{date.replace('-', '')}.npz"

    def _resolve_cache_dates(self, start_date: str | None, end_date: str | None) -> list[str]:
        start = (start_date or "").strip()
        end = (end_date or "").strip()
        dates = list(self.dates)
        if start:
            dates = [date for date in dates if date >= start]
        if end:
            dates = [date for date in dates if date <= end]
        return dates

    def _cache_lock_for_date(self, date: str) -> threading.Lock:
        with self._cache_lock_guard:
            lock = self._cache_locks.get(date)
            if lock is None:
                lock = threading.Lock()
                self._cache_locks[date] = lock
            return lock

    def _ensure_cache_dir(self) -> None:
        if self._cache_dir_ready:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir_ready = True

    def _is_cache_usable(self, cache_path: Path, source_path: Path) -> bool:
        if not cache_path.exists():
            return False
        try:
            return cache_path.stat().st_mtime >= source_path.stat().st_mtime
        except OSError:
            return False

    def _read_cache_file(self, cache_path: Path) -> dict[str, np.ndarray]:
        with np.load(cache_path, allow_pickle=False) as archive:
            return {name: archive[name].copy() for name in archive.files}

    def _write_cache_file(self, cache_path: Path, payload: dict[str, np.ndarray]) -> None:
        self._ensure_cache_dir()
        with cache_path.open("wb") as handle:
            np.savez(handle, **payload)

    def _build_cache_payload(self, source_path: Path) -> dict[str, np.ndarray]:
        variable_names = sorted(RAW_EXPRESSION_FIELDS) + ["STOCKLIST", "MinuteShow"]
        mat = scipy.io.loadmat(str(source_path), variable_names=variable_names)
        minute_labels = np.asarray(
            [self._matlab_cell_to_text(item) for item in mat["MinuteShow"][0]],
            dtype="<U16",
        )
        stock_list = np.asarray(
            [self._matlab_cell_to_text(item) for item in mat["STOCKLIST"][0]],
            dtype="<U32",
        )

        payload: dict[str, np.ndarray] = {
            "minute_labels": minute_labels,
            "stock_list": stock_list,
        }
        raw_open_first_valid: np.ndarray | None = None
        raw_open_all_nan: np.ndarray | None = None

        for field_name in sorted(RAW_EXPRESSION_FIELDS):
            array = np.asarray(mat[field_name], dtype=np.float64)
            if array.ndim == 3:
                array = array[:, :, 0]
            if field_name in PRICE_FIELDS:
                if field_name == "MINUTE_OPEN":
                    raw_open_first_valid = self._first_valid_positions(array)
                    raw_open_all_nan = np.isnan(array).all(axis=0)
                processed, invalid = self._process_price_field(array)
            elif field_name in VOLUME_LIKE_FIELDS:
                processed, invalid = self._process_volume_like_field(array)
            else:
                processed = array.copy()
                invalid = np.isnan(processed).all(axis=0)

            payload[f"field::{field_name}"] = processed.astype(np.float64, copy=False)
            payload[f"invalid::{field_name}"] = invalid.astype(bool, copy=False)

        if raw_open_first_valid is None or raw_open_all_nan is None:
            raise RuntimeError("MINUTE_OPEN metadata was not prepared for caching.")

        payload["meta::raw_open_first_valid"] = raw_open_first_valid.astype(np.int32, copy=False)
        payload["meta::raw_open_all_nan"] = raw_open_all_nan.astype(bool, copy=False)
        return payload

    def _process_price_field(self, array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        processed = array.copy()
        invalid = np.isnan(processed).all(axis=0)

        for column in range(processed.shape[1]):
            if invalid[column]:
                continue
            series = processed[:, column]
            valid_positions = np.flatnonzero(np.isfinite(series))
            if valid_positions.size == 0:
                invalid[column] = True
                continue
            first_valid = int(valid_positions[0])
            if first_valid > 0:
                series[:first_valid] = series[first_valid]
            last_value = series[first_valid]
            for index in range(first_valid + 1, len(series)):
                if np.isfinite(series[index]):
                    last_value = series[index]
                else:
                    series[index] = last_value
            processed[:, column] = series

        return processed, invalid

    def _process_volume_like_field(self, array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        invalid = np.isnan(array).all(axis=0)
        processed = np.where(np.isnan(array), 0.0, array)
        return processed.astype(np.float64, copy=False), invalid

    def _first_valid_positions(self, array: np.ndarray) -> np.ndarray:
        valid = np.isfinite(array)
        first_valid = valid.argmax(axis=0)
        first_valid = first_valid.astype(np.int32, copy=False)
        first_valid[~valid.any(axis=0)] = -1
        return first_valid

    def _matlab_cell_to_text(self, value: Any) -> str:
        if isinstance(value, np.ndarray):
            if value.size == 0:
                return ""
            return self._matlab_cell_to_text(value.flat[0])
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
