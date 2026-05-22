from __future__ import annotations

import math
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
    ):
        self.data_store = data_store
        self.expression_engine = expression_engine
        self.label_store = label_store

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

        signal_series: dict[str, pd.Series] = {}
        coverage_rows: list[dict[str, Any]] = []
        total_signal_days = len(signal_eval_dates)
        progress(0.03, "Preparing minute data and evaluating signals...")

        for index, date in enumerate(signal_eval_dates, start=1):
            day = self.data_store.load_day(date, required_fields)
            signal = self.expression_engine.evaluate(
                inputs.expression, day.fields, day.minute_labels
            )
            signal_invalid = np.zeros(len(day.stock_list), dtype=bool)
            for field_name in required_fields:
                signal_invalid |= day.field_invalid_masks[field_name]
            signal = np.where(signal_invalid, np.nan, signal)
            signal = np.where(np.isfinite(signal), signal, np.nan)
            signal_series[date] = pd.Series(signal, index=day.stock_list, dtype=np.float64)
            coverage_rows.append(
                {
                    "date": date,
                    "coverage": float(np.isfinite(signal).sum() / len(signal))
                    if len(signal)
                    else 0.0,
                }
            )
            progress(
                0.03 + 0.47 * index / max(total_signal_days, 1),
                f"Evaluated signal for {date} ({index}/{total_signal_days})",
            )

        open_dates = sorted(set(executable_trade_dates))
        open_series: dict[str, pd.Series] = {}
        for date in open_dates:
            day = self.data_store.load_day(date, set())
            open_values = np.where(day.trade_open_valid, day.trade_open, np.nan)
            open_series[date] = pd.Series(open_values, index=day.stock_list, dtype=np.float64)

        signal_df = pd.DataFrame.from_dict(signal_series, orient="index", dtype=np.float64).sort_index()
        decayed_df = self._compute_decayed_scores(
            signal_df, unique_signal_dates, inputs.decay, progress
        )
        coverage_df = pd.DataFrame(coverage_rows).set_index("date")
        open_df = pd.DataFrame.from_dict(open_series, orient="index", dtype=np.float64).sort_index()
        label_df = self.label_store.load_label_frame(unique_signal_dates, signal_df.columns.tolist())
        ic_df = self._compute_ic_series(
            decayed_df.reindex(unique_signal_dates),
            label_df.reindex(unique_signal_dates),
        )

        weights_by_trade_date: dict[str, pd.Series] = {}
        turnover_rows: list[float] = []
        previous_weights = pd.Series(dtype=np.float64)
        selected_counts: list[int] = []
        candidate_counts: list[int] = []

        progress(0.60, "Building daily portfolios...")
        for index, trade_date in enumerate(executable_trade_dates, start=1):
            signal_date = self.data_store.previous_trading_day(trade_date)
            if signal_date is None:
                continue
            scores = decayed_df.loc[signal_date]
            entry_open = open_df.loc[trade_date]
            candidates = scores.dropna().index.intersection(entry_open.dropna().index)
            candidate_scores = scores.loc[candidates].dropna()
            candidate_counts.append(int(candidate_scores.shape[0]))
            if candidate_scores.empty:
                weights = pd.Series(dtype=np.float64)
            else:
                top_count = max(1, int(math.ceil(candidate_scores.shape[0] * 0.1)))
                selected = candidate_scores.nlargest(top_count)
                weights = pd.Series(
                    1.0 / top_count, index=selected.index, dtype=np.float64
                )
            weights_by_trade_date[trade_date] = weights
            selected_counts.append(int(weights.shape[0]))
            union_index = previous_weights.index.union(weights.index)
            aligned_previous = previous_weights.reindex(union_index, fill_value=0.0)
            aligned_current = weights.reindex(union_index, fill_value=0.0)
            turnover_rows.append(float(0.5 * np.abs(aligned_current - aligned_previous).sum()))
            previous_weights = weights
            progress(
                0.60 + 0.15 * index / max(len(executable_trade_dates), 1),
                f"Built portfolio for {trade_date}",
            )

        if len(executable_trade_dates) < 2:
            raise BacktestError("Need at least two executable trade dates to compute returns.")

        returns_rows: list[dict[str, Any]] = []
        progress(0.78, "Computing open-to-open returns...")
        for index, (entry_date, exit_date) in enumerate(
            zip(executable_trade_dates[:-1], executable_trade_dates[1:]),
            start=1,
        ):
            weights = weights_by_trade_date.get(entry_date, pd.Series(dtype=np.float64))
            if weights.empty:
                day_return = 0.0
                holding_count = 0
            else:
                entry_open = open_df.loc[entry_date].reindex(weights.index)
                exit_open = open_df.loc[exit_date].reindex(weights.index)
                valid = (
                    entry_open.notna()
                    & exit_open.notna()
                    & (entry_open > 0)
                    & (exit_open > 0)
                )
                if not valid.any():
                    day_return = 0.0
                    holding_count = 0
                else:
                    realized = (exit_open[valid] / entry_open[valid]) - 1.0
                    effective_weights = pd.Series(
                        1.0 / realized.shape[0], index=realized.index, dtype=np.float64
                    )
                    day_return = float((effective_weights * realized).sum())
                    holding_count = int(realized.shape[0])
            returns_rows.append(
                {
                    "date": exit_date,
                    "entryDate": entry_date,
                    "return": day_return,
                    "holdings": holding_count,
                    "turnover": turnover_rows[index - 1],
                }
            )
            progress(
                0.78 + 0.14 * index / max(len(executable_trade_dates) - 1, 1),
                f"Computed returns through {exit_date}",
            )

        if not returns_rows:
            raise BacktestError("No return observations were produced for the selected range.")

        returns_df = pd.DataFrame(returns_rows)
        returns_df["equity"] = (1.0 + returns_df["return"]).cumprod()
        returns_df["peak"] = returns_df["equity"].cummax()
        returns_df["drawdown"] = returns_df["equity"] / returns_df["peak"] - 1.0
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
