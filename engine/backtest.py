from __future__ import annotations

import functools
import math
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Any

import numpy as np
import pandas as pd

from .catalog import DEFAULT_EXPRESSION, FIXED_RULES
from .daily_data import DailyLabelStore
from .data import MinuteDataStore
from .expression import ExpressionEngine, ExpressionError


class BacktestError(ValueError):
    pass


ProgressCallback = Callable[[float, str], None]


@dataclass
class BacktestInputs:
    expression: str
    start_date: str
    end_date: str
    decay: int


class BacktestEngine:
    def __init__(
        self,
        data_store: MinuteDataStore,
        expression_engine: ExpressionEngine,
        label_store: DailyLabelStore,
        raw_alpha_workers: int | None = None,
        decay_workers: int | None = None,
    ):
        self.data_store = data_store
        self.expression_engine = expression_engine
        self.label_store = label_store
        self.stock_codes = tuple(label_store.stock_codes)
        self.stock_index = pd.Index(self.stock_codes)
        default_workers = max(1, min(4, os.cpu_count() or 1))
        self.raw_alpha_workers = max(1, raw_alpha_workers or default_workers)
        self.decay_workers = max(1, decay_workers or default_workers)

    def run(
        self,
        expression: str,
        start_date: str,
        end_date: str,
        decay: int,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        inputs = self._validate_inputs(expression, start_date, end_date, decay)
        progress = progress or (lambda *_args: None)

        trade_dates = self.data_store.resolve_range(inputs.start_date, inputs.end_date)
        if len(trade_dates) < 2:
            raise BacktestError("Selected date range must contain at least two trading days.")

        executable_trade_dates = [
            date for date in trade_dates if self.data_store.previous_trading_day(date) is not None
        ]
        if len(executable_trade_dates) < 2:
            raise BacktestError(
                "Selected date range does not contain enough executable T+1 trades."
            )

        signal_dates = [self.data_store.previous_trading_day(date) for date in executable_trade_dates]
        signal_dates = [date for date in signal_dates if date is not None]
        unique_signal_dates = sorted(set(signal_dates))

        warmup_dates: list[str] = []
        earliest_signal_date = unique_signal_dates[0]
        cursor = earliest_signal_date
        for _ in range(max(inputs.decay - 1, 0)):
            previous = self.data_store.previous_trading_day(cursor)
            if previous is None:
                break
            warmup_dates.insert(0, previous)
            cursor = previous

        signal_eval_dates = warmup_dates + unique_signal_dates
        required_fields = self.expression_engine.collect_required_raw_fields(inputs.expression)

        signal_eval_set = set(signal_eval_dates)
        unique_signal_set = set(unique_signal_dates)
        executable_trade_set = set(executable_trade_dates)
        required_or_open_dates = sorted(signal_eval_set | executable_trade_set)

        coverage_rows: list[dict[str, Any]] = []
        ic_rows: list[dict[str, Any]] = []
        selected_counts: list[int] = []
        candidate_counts: list[int] = []
        returns_rows: list[dict[str, Any]] = []

        label_df = self.label_store.load_label_frame(unique_signal_dates, self.stock_codes)

        previous_weights = np.zeros(len(self.stock_codes), dtype=np.float64)
        previous_entry_open: np.ndarray | None = None
        previous_entry_date: str | None = None
        previous_turnover = 0.0
        signal_position = {date: index for index, date in enumerate(signal_eval_dates)}

        progress(0.03, "Parallel raw alpha stage...")
        with ThreadPoolExecutor(max_workers=self.raw_alpha_workers) as raw_pool:
            raw_futures = {
                date: raw_pool.submit(
                    self._evaluate_day_snapshot,
                    date,
                    required_fields if date in signal_eval_set else set(),
                    date in signal_eval_set,
                    inputs.expression,
                    required_fields,
                    date in unique_signal_set,
                )
                for date in required_or_open_dates
            }

            progress(0.38, "Parallel decay stage...")
            with ThreadPoolExecutor(max_workers=self.decay_workers) as decay_pool:
                decay_futures = {
                    date: decay_pool.submit(
                        self._compute_decayed_snapshot,
                        date,
                        signal_eval_dates,
                        signal_position[date],
                        inputs.decay,
                        raw_futures,
                        label_df.loc[date].to_numpy(dtype=np.float64, copy=False)
                        if date in label_df.index
                        else np.full(len(self.stock_codes), np.nan, dtype=np.float64),
                    )
                    for date in unique_signal_dates
                }

                for index, trade_date in enumerate(executable_trade_dates, start=1):
                    open_snapshot = raw_futures[trade_date].result()
                    trade_open_full = open_snapshot["trade_open"]

                    if previous_entry_open is not None and previous_entry_date is not None:
                        valid = (
                            (previous_weights > 0)
                            & np.isfinite(previous_entry_open)
                            & np.isfinite(trade_open_full)
                            & (previous_entry_open > 0)
                            & (trade_open_full > 0)
                        )
                        if valid.any():
                            realized = trade_open_full[valid] / previous_entry_open[valid] - 1.0
                            day_return = float(realized.mean())
                            holding_count = int(valid.sum())
                        else:
                            day_return = 0.0
                            holding_count = 0
                        returns_rows.append(
                            {
                                "date": trade_date,
                                "entryDate": previous_entry_date,
                                "return": day_return,
                                "holdings": holding_count,
                                "turnover": previous_turnover,
                            }
                        )

                    signal_date = self.data_store.previous_trading_day(trade_date)
                    if signal_date is None:
                        continue

                    decay_snapshot = decay_futures[signal_date].result()
                    scores = decay_snapshot["signal"]
                    ic_rows.append({"date": signal_date, "ic": decay_snapshot["ic"]})

                    raw_snapshot = raw_futures[signal_date].result()
                    if raw_snapshot["coverage"] is not None:
                        coverage_rows.append(
                            {
                                "date": signal_date,
                                "coverage": raw_snapshot["coverage"],
                            }
                        )

                    current_weights = np.zeros(len(self.stock_codes), dtype=np.float64)
                    candidate_mask = (
                        np.isfinite(scores) & np.isfinite(trade_open_full) & (trade_open_full > 0)
                    )
                    candidate_positions = np.flatnonzero(candidate_mask)
                    candidate_counts.append(int(candidate_positions.size))

                    if candidate_positions.size > 0:
                        candidate_scores = scores[candidate_positions]
                        top_count = max(1, int(math.ceil(candidate_positions.size * 0.1)))
                        selected_local = np.argpartition(candidate_scores, -top_count)[-top_count:]
                        selected_positions = candidate_positions[selected_local]
                        current_weights[selected_positions] = 1.0 / top_count
                        selected_counts.append(int(top_count))
                    else:
                        selected_counts.append(0)

                    previous_turnover = float(0.5 * np.abs(current_weights - previous_weights).sum())
                    previous_weights = current_weights
                    previous_entry_open = trade_open_full
                    previous_entry_date = trade_date

                    progress(
                        0.52 + 0.44 * index / max(len(executable_trade_dates), 1),
                        f"Processed {trade_date} ({index}/{len(executable_trade_dates)})",
                    )

        if not returns_rows:
            raise BacktestError("No return observations were produced for the selected range.")

        returns_df = pd.DataFrame(returns_rows)
        returns_df["equity"] = (1.0 + returns_df["return"]).cumprod()
        returns_df["peak"] = returns_df["equity"].cummax()
        returns_df["drawdown"] = returns_df["equity"] / returns_df["peak"] - 1.0
        coverage_df = pd.DataFrame(coverage_rows).set_index("date").sort_index()
        ic_df = pd.DataFrame(ic_rows).set_index("date").sort_index()
        summary = self._build_summary(
            returns_df,
            coverage_df.reindex(unique_signal_dates)["coverage"].dropna(),
            ic_df["ic"].dropna(),
            selected_counts,
            candidate_counts,
        )
        yearly_stats = self._build_yearly_stats(returns_df, ic_df)

        progress(1.0, "Backtest complete.")
        return {
            "summary": summary,
            "equityCurve": [
                {"date": row.date, "value": round(float(row.equity), 8)}
                for row in returns_df.itertuples()
            ],
            "drawdownCurve": [
                {"date": row.date, "value": round(float(row.drawdown), 8)}
                for row in returns_df.itertuples()
            ],
            "icCurve": [
                {"date": row.Index, "value": round(float(row.ic), 8)}
                for row in ic_df.dropna(subset=["ic"]).itertuples()
            ],
            "yearlyStats": yearly_stats,
            "debug": {
                "expression": inputs.expression,
                "usedRawFields": sorted(required_fields),
                "fixedRules": FIXED_RULES,
                "signalDateRange": {
                    "start": unique_signal_dates[0],
                    "end": unique_signal_dates[-1],
                },
                "tradeDateRange": {
                    "start": executable_trade_dates[0],
                    "end": executable_trade_dates[-1],
                },
                "effectiveReturnDays": int(returns_df.shape[0]),
                "averageSignalCoverage": summary["coverage"],
                "averageIC": summary["ic"],
                "icir": summary["icir"],
                "averageCandidateCount": round(float(np.mean(candidate_counts or [0])), 2),
                "averageSelectedCount": round(float(np.mean(selected_counts or [0])), 2),
            },
        }

    def _validate_inputs(
        self, expression: str, start_date: str, end_date: str, decay: int
    ) -> BacktestInputs:
        clean_expression = (expression or "").strip() or DEFAULT_EXPRESSION
        if start_date > end_date:
            raise BacktestError("startDate must be on or before endDate.")
        if decay < 1:
            raise BacktestError("Decay must be at least 1.")
        available_dates = self.data_store.available_dates()
        if end_date < available_dates[0] or start_date > available_dates[-1]:
            raise BacktestError("Selected date range does not overlap the available minute data.")
        return BacktestInputs(clean_expression, start_date, end_date, decay)

    @functools.lru_cache(maxsize=256)
    def _stock_positions(self, stock_list: tuple[str, ...]) -> np.ndarray:
        return self.stock_index.get_indexer(list(stock_list))

    def _align_to_universe(
        self,
        values: np.ndarray,
        stock_list: tuple[str, ...],
    ) -> np.ndarray:
        aligned = np.full(len(self.stock_codes), np.nan, dtype=np.float64)
        positions = self._stock_positions(stock_list)
        valid_positions = positions >= 0
        if np.any(valid_positions):
            aligned[positions[valid_positions]] = values[valid_positions]
        return aligned

    def _mask_invalid_signal(
        self,
        signal: np.ndarray,
        required_fields: set[str],
        field_invalid_masks: dict[str, np.ndarray],
    ) -> np.ndarray:
        signal_invalid = np.zeros(signal.shape[0], dtype=bool)
        for field_name in required_fields:
            signal_invalid |= field_invalid_masks[field_name]
        signal = np.where(signal_invalid, np.nan, signal)
        return np.where(np.isfinite(signal), signal, np.nan)

    def _evaluate_day_snapshot(
        self,
        date: str,
        requested_fields: set[str],
        evaluate_signal: bool,
        expression: str,
        required_fields: set[str],
        capture_coverage: bool,
    ) -> dict[str, Any]:
        day = self.data_store.load_day(date, requested_fields)
        snapshot: dict[str, Any] = {
            "date": date,
            "trade_open": self._align_to_universe(day.trade_open, day.stock_list),
            "raw_signal": None,
            "coverage": None,
        }
        if not evaluate_signal:
            return snapshot

        raw_signal = self.expression_engine.evaluate(expression, day.fields, day.minute_labels)
        raw_signal = self._mask_invalid_signal(raw_signal, required_fields, day.field_invalid_masks)
        snapshot["raw_signal"] = self._align_to_universe(raw_signal, day.stock_list)
        if capture_coverage:
            snapshot["coverage"] = (
                float(np.isfinite(raw_signal).sum() / len(raw_signal)) if len(raw_signal) else 0.0
            )
        return snapshot

    def _compute_decayed_from_arrays(self, raw_signal_arrays: list[np.ndarray]) -> np.ndarray:
        if not raw_signal_arrays:
            return np.full(len(self.stock_codes), np.nan, dtype=np.float64)
        history = np.stack(raw_signal_arrays, axis=0)
        weights = np.arange(1, history.shape[0] + 1, dtype=np.float64)
        valid_mask = np.isfinite(history).all(axis=0)
        weighted_sum = np.where(np.isfinite(history), history * weights[:, None], 0.0).sum(axis=0)
        decayed = np.full(history.shape[1], np.nan, dtype=np.float64)
        decayed[valid_mask] = weighted_sum[valid_mask] / weights.sum()
        return decayed

    def _compute_decayed_snapshot(
        self,
        date: str,
        signal_eval_dates: list[str],
        signal_position: int,
        decay: int,
        raw_futures: dict[str, Any],
        label_row: np.ndarray,
    ) -> dict[str, Any]:
        window_start = max(0, signal_position - decay + 1)
        window_dates = signal_eval_dates[window_start : signal_position + 1]
        raw_signal_arrays = [
            np.asarray(raw_futures[window_date].result()["raw_signal"], dtype=np.float64)
            for window_date in window_dates
        ]
        decayed_signal = self._compute_decayed_from_arrays(raw_signal_arrays)
        return {
            "date": date,
            "signal": decayed_signal,
            "ic": self._cross_sectional_ic(decayed_signal, label_row),
        }

    def _compute_decayed_from_history(self, raw_signal_history: deque[np.ndarray]) -> np.ndarray:
        return self._compute_decayed_from_arrays(list(raw_signal_history))

    def _compute_decayed_scores(
        self,
        signal_df: pd.DataFrame,
        target_dates: list[str],
        decay: int,
        progress: ProgressCallback,
    ) -> pd.DataFrame:
        signal_index = list(signal_df.index)
        date_position = {date: index for index, date in enumerate(signal_index)}
        decayed_rows: dict[str, pd.Series] = {}

        for index, date in enumerate(target_dates, start=1):
            position = date_position[date]
            window_start = max(0, position - decay + 1)
            window = signal_df.iloc[window_start : position + 1]
            weights = np.arange(1, len(window) + 1, dtype=np.float64)
            valid_mask = window.notna().all(axis=0)
            weighted = window.mul(weights, axis=0).sum(axis=0) / weights.sum()
            weighted = weighted.where(valid_mask, np.nan)
            decayed_rows[date] = weighted.astype(np.float64)
            progress(
                0.50 + 0.10 * index / max(len(target_dates), 1),
                f"Applied decay through {date}",
            )

        return pd.DataFrame.from_dict(decayed_rows, orient="index", dtype=np.float64).sort_index()

    def _compute_ic_series(
        self,
        signal_df: pd.DataFrame,
        label_df: pd.DataFrame,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for date in signal_df.index.intersection(label_df.index):
            signal_row = signal_df.loc[date].to_numpy(dtype=np.float64, copy=False)
            label_row = label_df.loc[date].to_numpy(dtype=np.float64, copy=False)
            ic_value = self._cross_sectional_ic(signal_row, label_row)
            rows.append({"date": date, "ic": ic_value})

        if not rows:
            return pd.DataFrame(columns=["ic"], dtype=np.float64)

        return pd.DataFrame(rows).set_index("date").sort_index()

    def _cross_sectional_ic(self, signal_row: np.ndarray, label_row: np.ndarray) -> float:
        valid = np.isfinite(signal_row) & np.isfinite(label_row)
        if int(valid.sum()) < 2:
            return float("nan")

        x = signal_row[valid]
        y = label_row[valid]
        if np.std(x) == 0 or np.std(y) == 0:
            return float("nan")
        return float(np.corrcoef(x, y)[0, 1])

    def _build_summary(
        self,
        returns_df: pd.DataFrame,
        coverage_series: pd.Series,
        ic_series: pd.Series,
        selected_counts: list[int],
        candidate_counts: list[int],
    ) -> dict[str, float]:
        total_return = float(returns_df["equity"].iloc[-1] - 1.0)
        daily_returns = returns_df["return"]
        periods = len(daily_returns)
        annualized_return = float((1.0 + total_return) ** (252 / periods) - 1.0) if periods else 0.0
        volatility = float(daily_returns.std(ddof=0))
        sharpe = float(np.sqrt(252.0) * daily_returns.mean() / volatility) if volatility > 0 else 0.0
        max_drawdown = float(returns_df["drawdown"].min())
        avg_turnover = float(returns_df["turnover"].mean()) if not returns_df.empty else 0.0
        margin = float(daily_returns.mean() / avg_turnover * 10000.0) if avg_turnover > 0 else 0.0
        avg_holdings = float(np.mean(selected_counts or [0]))
        coverage = float(coverage_series.mean()) if not coverage_series.empty else 0.0
        avg_ic = float(ic_series.mean()) if not ic_series.empty else 0.0
        ic_std = float(ic_series.std(ddof=0)) if len(ic_series) else 0.0
        icir = float(avg_ic / ic_std) if ic_std > 0 else 0.0
        return {
            "totalReturn": round(total_return, 6),
            "annualizedReturn": round(annualized_return, 6),
            "sharpe": round(sharpe, 6),
            "maxDrawdown": round(max_drawdown, 6),
            "turnover": round(avg_turnover, 6),
            "averageTurnover": round(avg_turnover, 6),
            "margin": round(margin, 6),
            "ic": round(avg_ic, 6),
            "icir": round(icir, 6),
            "averageHoldings": round(avg_holdings, 2),
            "coverage": round(coverage, 6),
            "averageCandidateCount": round(float(np.mean(candidate_counts or [0])), 2),
        }

    def _build_yearly_stats(
        self,
        returns_df: pd.DataFrame,
        ic_df: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        if returns_df.empty:
            return []

        frame = returns_df.copy()
        frame["year"] = frame["date"].str.slice(0, 4)
        yearly_ic: dict[str, dict[str, float]] = {}
        if not ic_df.empty:
            ic_frame = ic_df.reset_index().rename(columns={"date": "signalDate"})
            ic_frame["year"] = ic_frame["signalDate"].str.slice(0, 4)
            for year, group in ic_frame.groupby("year"):
                ic_mean = float(group["ic"].mean()) if not group.empty else 0.0
                ic_std = float(group["ic"].std(ddof=0)) if len(group) else 0.0
                yearly_ic[str(year)] = {
                    "ic": round(ic_mean, 6),
                    "icir": round(float(ic_mean / ic_std) if ic_std > 0 else 0.0, 6),
                }

        output: list[dict[str, Any]] = []
        for year, group in frame.groupby("year"):
            equity = (1.0 + group["return"]).cumprod()
            drawdown = equity / equity.cummax() - 1.0
            volatility = float(group["return"].std(ddof=0))
            avg_turnover = float(group["turnover"].mean()) if not group.empty else 0.0
            margin = (
                float(group["return"].mean() / avg_turnover * 10000.0)
                if avg_turnover > 0
                else 0.0
            )
            sharpe = (
                float(np.sqrt(252.0) * group["return"].mean() / volatility)
                if volatility > 0
                else 0.0
            )
            output.append(
                {
                    "year": year,
                    "return": round(float(equity.iloc[-1] - 1.0), 6),
                    "sharpe": round(sharpe, 6),
                    "turnover": round(avg_turnover, 6),
                    "margin": round(margin, 6),
                    "ic": yearly_ic.get(str(year), {}).get("ic", 0.0),
                    "icir": yearly_ic.get(str(year), {}).get("icir", 0.0),
                    "maxDrawdown": round(float(drawdown.min()), 6),
                }
            )
        return output
