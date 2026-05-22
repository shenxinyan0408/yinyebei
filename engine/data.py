from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import scipy.io

from .catalog import MINUTE_DATA_DIR, PRICE_FIELDS, VOLUME_LIKE_FIELDS


@dataclass
class DayData:
    date: str
    minute_labels: tuple[str, ...]
    stock_list: tuple[str, ...]
    fields: dict[str, np.ndarray]
    field_invalid_masks: dict[str, np.ndarray]
    trade_open: np.ndarray
    trade_open_valid: np.ndarray


class MinuteDataStore:
    def __init__(self, data_dir: Path | None = None, first_valid_cutoff: str = "09:35"):
        self.data_dir = Path(data_dir or MINUTE_DATA_DIR)
        self.first_valid_cutoff = first_valid_cutoff
        self.date_to_path = self._scan_data_dir()
        self.dates = tuple(sorted(self.date_to_path))
        self._date_index = {date: index for index, date in enumerate(self.dates)}

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

    @functools.lru_cache(maxsize=64)
    def _load_day_cached(self, date: str, requested_fields: tuple[str, ...]) -> DayData:
        path = self.date_to_path.get(date)
        if path is None:
            raise FileNotFoundError(f"No minute data file for date: {date}")

        variable_names = list(requested_fields) + ["STOCKLIST", "MinuteShow"]
        mat = scipy.io.loadmat(str(path), variable_names=variable_names)
        minute_labels = tuple(self._matlab_cell_to_text(item) for item in mat["MinuteShow"][0])
        stock_list = tuple(self._matlab_cell_to_text(item) for item in mat["STOCKLIST"][0])
        open_index = minute_labels.index("09:25")
        cutoff_index = minute_labels.index(self.first_valid_cutoff)

        processed_fields: dict[str, np.ndarray] = {}
        invalid_masks: dict[str, np.ndarray] = {}
        raw_open_first_valid = None
        raw_open_all_nan = None

        for field_name in requested_fields:
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
            processed_fields[field_name] = processed
            invalid_masks[field_name] = invalid

        trade_open = processed_fields["MINUTE_OPEN"][open_index].copy()
        if raw_open_first_valid is None or raw_open_all_nan is None:
            raise RuntimeError("MINUTE_OPEN metadata was not prepared for trade validation.")
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
