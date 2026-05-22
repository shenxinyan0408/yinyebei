from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from .catalog import DEFAULT_EXPRESSION
from .data import MinuteDataStore
from .expression import ExpressionEngine


class CorrelationError(ValueError):
    pass


ProgressCallback = Callable[[float, str], None]


@dataclass
class CorrelationInputs:
    expression_a: str
    start_date_a: str
    end_date_a: str
    decay_a: int
    expression_b: str
    start_date_b: str
    end_date_b: str
    decay_b: int


class CorrelationEngine:
    def __init__(self, data_store: MinuteDataStore, expression_engine: ExpressionEngine):
        self.data_store = data_store
        self.expression_engine = expression_engine

    def run(
        self,
        expression_a: str,
        start_date_a: str,
        end_date_a: str,
        decay_a: int,
        expression_b: str,
        start_date_b: str,
        end_date_b: str,
        decay_b: int,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        inputs = self._validate_inputs(
            expression_a,
            start_date_a,
            end_date_a,
            decay_a,
            expression_b,
            start_date_b,
            end_date_b,
            decay_b,
        )
        progress = progress or (lambda *_args: None)

        overlap_start = max(inputs.start_date_a, inputs.start_date_b)
        overlap_end = min(inputs.end_date_a, inputs.end_date_b)
        if overlap_start > overlap_end:
            raise CorrelationError("The two factor windows do not overlap in time.")

        signal_dates = self.data_store.resolve_range(overlap_start, overlap_end)
        if not signal_dates:
            raise CorrelationError("No overlapping minute-data dates are available for correlation.")

        warmup_a = self._warmup_dates(signal_dates[0], inputs.decay_a)
        warmup_b = self._warmup_dates(signal_dates[0], inputs.decay_b)
        eval_dates = sorted(set(warmup_a + warmup_b + signal_dates))

        required_fields_a = self.expression_engine.collect_required_raw_fields(inputs.expression_a)
        required_fields_b = self.expression_engine.collect_required_raw_fields(inputs.expression_b)
        requested_fields = required_fields_a | required_fields_b

        signal_series_a: dict[str, pd.Series] = {}
        signal_series_b: dict[str, pd.Series] = {}
        total_eval_dates = len(eval_dates)
        progress(0.04, "Preparing minute data and evaluating the two factors...")

        for index, date in enumerate(eval_dates, start=1):
            day = self.data_store.load_day(date, requested_fields)
            signal_a = self.expression_engine.evaluate(
                inputs.expression_a, day.fields, day.minute_labels
            )
            signal_b = self.expression_engine.evaluate(
                inputs.expression_b, day.fields, day.minute_labels
            )
            signal_a = self._mask_invalid(signal_a, required_fields_a, day.field_invalid_masks)
            signal_b = self._mask_invalid(signal_b, required_fields_b, day.field_invalid_masks)
            signal_series_a[date] = pd.Series(signal_a, index=day.stock_list, dtype=np.float64)
            signal_series_b[date] = pd.Series(signal_b, index=day.stock_list, dtype=np.float64)
            progress(
                0.04 + 0.46 * index / max(total_eval_dates, 1),
                f"Evaluated factor values for {date} ({index}/{total_eval_dates})",
            )

        signal_df_a = pd.DataFrame.from_dict(signal_series_a, orient="index", dtype=np.float64).sort_index()
        signal_df_b = pd.DataFrame.from_dict(signal_series_b, orient="index", dtype=np.float64).sort_index()
        decayed_a = self._compute_decayed_scores(signal_df_a, signal_dates, inputs.decay_a)
        decayed_b = self._compute_decayed_scores(signal_df_b, signal_dates, inputs.decay_b)

        progress(0.70, "Computing daily cross-sectional factor correlation...")
        rows: list[dict[str, Any]] = []
        for index, date in enumerate(signal_dates, start=1):
            factor_a = decayed_a.loc[date].to_numpy(dtype=np.float64, copy=False)
            factor_b = decayed_b.loc[date].to_numpy(dtype=np.float64, copy=False)
            correlation_value, sample_count = self._cross_sectional_correlation(factor_a, factor_b)
            rows.append(
                {
                    "date": date,
                    "correlation": correlation_value,
                    "sampleCount": sample_count,
                }
            )
            progress(
                0.70 + 0.30 * index / max(len(signal_dates), 1),
                f"Computed correlation for {date} ({index}/{len(signal_dates)})",
            )

        correlation_df = pd.DataFrame(rows).set_index("date").sort_index()
        summary = self._build_summary(correlation_df)
        yearly_stats = self._build_yearly_stats(correlation_df)

        return {
            "summary": summary,
            "correlationCurve": [
                {"date": row.Index, "value": round(float(row.correlation), 8)}
                for row in correlation_df.dropna(subset=["correlation"]).itertuples()
            ],
            "yearlyStats": yearly_stats,
            "debug": {
                "factorA": {
                    "expression": inputs.expression_a,
                    "usedRawFields": sorted(required_fields_a),
                    "startDate": inputs.start_date_a,
                    "endDate": inputs.end_date_a,
                    "decay": inputs.decay_a,
                },
                "factorB": {
                    "expression": inputs.expression_b,
                    "usedRawFields": sorted(required_fields_b),
                    "startDate": inputs.start_date_b,
                    "endDate": inputs.end_date_b,
                    "decay": inputs.decay_b,
                },
                "overlapDateRange": {
                    "start": signal_dates[0],
                    "end": signal_dates[-1],
                },
                "validDays": int(correlation_df["correlation"].notna().sum()),
                "totalSignalDays": int(len(signal_dates)),
                "averageSampleCount": summary["averageSampleCount"],
            },
        }

    def _validate_inputs(
        self,
        expression_a: str,
        start_date_a: str,
        end_date_a: str,
        decay_a: int,
        expression_b: str,
        start_date_b: str,
        end_date_b: str,
        decay_b: int,
    ) -> CorrelationInputs:
        clean_expression_a = (expression_a or "").strip() or DEFAULT_EXPRESSION
        clean_expression_b = (expression_b or "").strip() or DEFAULT_EXPRESSION
        self._validate_single_window(start_date_a, end_date_a, decay_a, "factorA")
        self._validate_single_window(start_date_b, end_date_b, decay_b, "factorB")
        return CorrelationInputs(
            expression_a=clean_expression_a,
            start_date_a=start_date_a,
            end_date_a=end_date_a,
            decay_a=decay_a,
            expression_b=clean_expression_b,
            start_date_b=start_date_b,
            end_date_b=end_date_b,
            decay_b=decay_b,
        )

    def _validate_single_window(
        self,
        start_date: str,
        end_date: str,
        decay: int,
        field_prefix: str,
    ) -> None:
        if start_date > end_date:
            raise CorrelationError(f"{field_prefix}.startDate must be on or before {field_prefix}.endDate.")
        if decay < 1:
            raise CorrelationError(f"{field_prefix}.decay must be at least 1.")
        available_dates = self.data_store.available_dates()
        if end_date < available_dates[0] or start_date > available_dates[-1]:
            raise CorrelationError(
                f"{field_prefix} does not overlap the available minute-data range."
            )

    def _warmup_dates(self, first_target_date: str, decay: int) -> list[str]:
        warmup_dates: list[str] = []
        cursor = first_target_date
        for _ in range(max(decay - 1, 0)):
            previous = self.data_store.previous_trading_day(cursor)
            if previous is None:
                break
            warmup_dates.insert(0, previous)
            cursor = previous
        return warmup_dates

    def _mask_invalid(
        self,
        signal: np.ndarray,
        required_fields: set[str],
        invalid_masks: dict[str, np.ndarray],
    ) -> np.ndarray:
        signal_invalid = np.zeros(signal.shape[0], dtype=bool)
        for field_name in required_fields:
            signal_invalid |= invalid_masks[field_name]
        signal = np.where(signal_invalid, np.nan, signal)
        return np.where(np.isfinite(signal), signal, np.nan)

    def _compute_decayed_scores(
        self,
        signal_df: pd.DataFrame,
        target_dates: list[str],
        decay: int,
    ) -> pd.DataFrame:
        signal_index = list(signal_df.index)
        date_position = {date: index for index, date in enumerate(signal_index)}
        decayed_rows: dict[str, pd.Series] = {}

        for date in target_dates:
            position = date_position[date]
            window_start = max(0, position - decay + 1)
            window = signal_df.iloc[window_start : position + 1]
            weights = np.arange(1, len(window) + 1, dtype=np.float64)
            valid_mask = window.notna().all(axis=0)
            weighted = window.mul(weights, axis=0).sum(axis=0) / weights.sum()
            decayed_rows[date] = weighted.where(valid_mask, np.nan).astype(np.float64)

        return pd.DataFrame.from_dict(decayed_rows, orient="index", dtype=np.float64).sort_index()

    def _cross_sectional_correlation(
        self,
        factor_a: np.ndarray,
        factor_b: np.ndarray,
    ) -> tuple[float, int]:
        valid = np.isfinite(factor_a) & np.isfinite(factor_b)
        sample_count = int(valid.sum())
        if sample_count < 2:
            return float("nan"), sample_count

        x = factor_a[valid]
        y = factor_b[valid]
        if np.std(x) == 0 or np.std(y) == 0:
            return float("nan"), sample_count
        return float(np.corrcoef(x, y)[0, 1]), sample_count

    def _build_summary(self, correlation_df: pd.DataFrame) -> dict[str, float]:
        correlation_series = correlation_df["correlation"].dropna()
        average_correlation = float(correlation_series.mean()) if not correlation_series.empty else 0.0
        correlation_std = float(correlation_series.std(ddof=0)) if not correlation_series.empty else 0.0
        correlation_ratio = (
            float(average_correlation / correlation_std) if correlation_std > 0 else 0.0
        )
        positive_ratio = (
            float((correlation_series > 0).mean()) if not correlation_series.empty else 0.0
        )
        average_sample_count = (
            float(correlation_df["sampleCount"].mean()) if not correlation_df.empty else 0.0
        )
        return {
            "averageCorrelation": round(average_correlation, 6),
            "correlationStd": round(correlation_std, 6),
            "correlationRatio": round(correlation_ratio, 6),
            "positiveRatio": round(positive_ratio, 6),
            "averageSampleCount": round(average_sample_count, 2),
            "validDays": int(correlation_series.shape[0]),
            "totalDays": int(correlation_df.shape[0]),
        }

    def _build_yearly_stats(self, correlation_df: pd.DataFrame) -> list[dict[str, Any]]:
        if correlation_df.empty:
            return []

        frame = correlation_df.reset_index().rename(columns={"date": "signalDate"})
        frame["year"] = frame["signalDate"].str.slice(0, 4)
        output: list[dict[str, Any]] = []
        for year, group in frame.groupby("year"):
            correlation_series = group["correlation"].dropna()
            average_correlation = (
                float(correlation_series.mean()) if not correlation_series.empty else 0.0
            )
            correlation_std = (
                float(correlation_series.std(ddof=0)) if not correlation_series.empty else 0.0
            )
            correlation_ratio = (
                float(average_correlation / correlation_std) if correlation_std > 0 else 0.0
            )
            output.append(
                {
                    "year": str(year),
                    "averageCorrelation": round(average_correlation, 6),
                    "correlationStd": round(correlation_std, 6),
                    "correlationRatio": round(correlation_ratio, 6),
                    "averageSampleCount": round(float(group["sampleCount"].mean()), 2),
                    "validDays": int(correlation_series.shape[0]),
                }
            )
        return output
