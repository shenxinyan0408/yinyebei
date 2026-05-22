from __future__ import annotations

import functools
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


class DailyLabelStore:
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"Daily data file not found: {self.file_path}")

        with self.file_path.open("rb") as handle:
            payload = joblib.load(handle)

        self.label = np.asarray(payload["Label"], dtype=np.float64)
        if self.label.ndim != 2:
            raise ValueError("Label must be a 2D array.")

        calendar = payload["CALENDAR_DF"]["Day"].astype(str).tolist()
        self.dates = tuple(calendar[: self.label.shape[0]])
        self._date_index = {date: index for index, date in enumerate(self.dates)}

        stock_frame = payload["STOCKLIST"]
        self.stock_codes = tuple(stock_frame["code"].astype(str).tolist())
        self._stock_index = pd.Index(self.stock_codes)

    def available_dates(self) -> tuple[str, ...]:
        return self.dates

    def resolve_range(self, start_date: str, end_date: str) -> list[str]:
        return [date for date in self.dates if start_date <= date <= end_date]

    @functools.lru_cache(maxsize=32)
    def _column_indexer(self, stock_codes: tuple[str, ...]) -> np.ndarray:
        return self._stock_index.get_indexer(list(stock_codes))

    def load_label_frame(
        self,
        dates: list[str] | tuple[str, ...],
        stock_codes: list[str] | tuple[str, ...],
    ) -> pd.DataFrame:
        normalized_dates = [date for date in dates if date in self._date_index]
        if not normalized_dates:
            return pd.DataFrame(index=[], columns=list(stock_codes), dtype=np.float64)

        row_positions = np.array([self._date_index[date] for date in normalized_dates], dtype=np.int32)
        stock_tuple = tuple(stock_codes)
        column_indexer = self._column_indexer(stock_tuple)

        values = np.full((len(normalized_dates), len(stock_tuple)), np.nan, dtype=np.float64)
        valid_columns = column_indexer >= 0
        if valid_columns.any():
            values[:, valid_columns] = self.label[np.ix_(row_positions, column_indexer[valid_columns])]

        return pd.DataFrame(values, index=normalized_dates, columns=list(stock_tuple), dtype=np.float64)
