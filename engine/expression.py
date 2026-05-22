from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .catalog import FUNCTION_NAMES, RAW_EXPRESSION_FIELDS, resolve_field_dependencies


class ExpressionError(ValueError):
    pass


@dataclass
class EvaluationContext:
    fields: dict[str, np.ndarray]
    minute_index: dict[str, int]
    derived_cache: dict[str, np.ndarray] = field(default_factory=dict)


class _FieldCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> Any:
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Name(self, node: ast.Name) -> Any:
        self.names.add(node.id)


class ExpressionEngine:
    def collect_required_raw_fields(self, expression: str) -> set[str]:
        tree = self._parse(expression)
        collector = _FieldCollector()
        collector.visit(tree.body)
        required: set[str] = set()
        for name in collector.names:
            if name in FUNCTION_NAMES or name in {"True", "False"}:
                continue
            dependencies = resolve_field_dependencies(name)
            if not dependencies and name not in RAW_EXPRESSION_FIELDS:
                raise ExpressionError(f"Unknown field or identifier: {name}")
            required.update(dependencies)
        return required

    def evaluate(
        self,
        expression: str,
        fields: dict[str, np.ndarray],
        minute_labels: tuple[str, ...],
    ) -> np.ndarray:
        tree = self._parse(expression)
        context = EvaluationContext(
            fields=fields,
            minute_index={label: index for index, label in enumerate(minute_labels)},
        )
        result = self._eval(tree.body, context)
        if np.isscalar(result):
            raise ExpressionError("Expression must return a stock vector, not a scalar.")
        array = np.asarray(result, dtype=np.float64)
        if array.ndim != 1:
            raise ExpressionError(
                "Expression must reduce the minute dimension and return one value per stock."
            )
        return np.where(np.isfinite(array), array, np.nan)

    def _parse(self, expression: str) -> ast.Expression:
        try:
            return ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"Invalid expression syntax: {exc.msg}") from exc

    def _eval(self, node: ast.AST, context: EvaluationContext) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id, context)
        if isinstance(node, ast.BinOp):
            left = self._eval(node.left, context)
            right = self._eval(node.right, context)
            return self._apply_binop(node.op, left, right)
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, context)
            return self._apply_unaryop(node.op, value)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError("Only direct function calls are supported.")
            func_name = node.func.id
            args = [self._eval(arg, context) for arg in node.args]
            kwargs = {kw.arg: self._eval(kw.value, context) for kw in node.keywords}
            return self._dispatch_function(func_name, args, kwargs, context)
        if isinstance(node, ast.Compare):
            return self._apply_compare(node, context)
        if isinstance(node, ast.BoolOp):
            values = [self._as_bool_array(self._eval(value, context)) for value in node.values]
            if isinstance(node.op, ast.And):
                return np.logical_and.reduce(values)
            if isinstance(node.op, ast.Or):
                return np.logical_or.reduce(values)
        raise ExpressionError(f"Unsupported expression element: {node.__class__.__name__}")

    def _resolve_name(self, name: str, context: EvaluationContext) -> Any:
        if name in context.fields:
            return context.fields[name]
        if name == "VWAP":
            if name not in context.derived_cache:
                amount = context.fields["MINUTE_AMOUNT"]
                volume = context.fields["MINUTE_VOLUME"]
                context.derived_cache[name] = np.divide(
                    amount,
                    volume,
                    out=np.full_like(amount, np.nan, dtype=np.float64),
                    where=volume != 0,
                )
            return context.derived_cache[name]
        raise ExpressionError(f"Unknown field or identifier: {name}")

    def _apply_binop(self, op: ast.operator, left: Any, right: Any) -> Any:
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            if isinstance(op, ast.Add):
                return left + right
            if isinstance(op, ast.Sub):
                return left - right
            if isinstance(op, ast.Mult):
                return left * right
            if isinstance(op, ast.Div):
                return np.divide(
                    left,
                    right,
                    out=np.full(np.broadcast(left, right).shape, np.nan, dtype=np.float64),
                    where=np.asarray(right) != 0,
                )
            if isinstance(op, ast.Pow):
                return np.power(left, right)
            if isinstance(op, ast.Mod):
                return np.mod(left, right)
            if isinstance(op, ast.BitAnd):
                return np.logical_and(self._as_bool_array(left), self._as_bool_array(right))
            if isinstance(op, ast.BitOr):
                return np.logical_or(self._as_bool_array(left), self._as_bool_array(right))
        raise ExpressionError(f"Unsupported binary operator: {op.__class__.__name__}")

    def _apply_unaryop(self, op: ast.unaryop, value: Any) -> Any:
        if isinstance(op, ast.USub):
            return -value
        if isinstance(op, ast.UAdd):
            return +value
        if isinstance(op, ast.Not):
            return np.logical_not(self._as_bool_array(value))
        raise ExpressionError(f"Unsupported unary operator: {op.__class__.__name__}")

    def _apply_compare(self, node: ast.Compare, context: EvaluationContext) -> Any:
        left = self._eval(node.left, context)
        comparisons = []
        current = left
        for operator, comparator in zip(node.ops, node.comparators):
            right = self._eval(comparator, context)
            if isinstance(operator, ast.Gt):
                comparisons.append(current > right)
            elif isinstance(operator, ast.GtE):
                comparisons.append(current >= right)
            elif isinstance(operator, ast.Lt):
                comparisons.append(current < right)
            elif isinstance(operator, ast.LtE):
                comparisons.append(current <= right)
            elif isinstance(operator, ast.Eq):
                comparisons.append(current == right)
            elif isinstance(operator, ast.NotEq):
                comparisons.append(current != right)
            else:
                raise ExpressionError(
                    f"Unsupported comparison operator: {operator.__class__.__name__}"
                )
            current = right
        return np.logical_and.reduce(comparisons)

    def _dispatch_function(
        self,
        name: str,
        args: list[Any],
        kwargs: dict[str, Any],
        context: EvaluationContext,
    ) -> Any:
        if kwargs:
            raise ExpressionError("Keyword arguments are not supported.")
        if name == "first":
            self._require_arg_count(name, args, 1)
            return self._require_matrix(name, args[0])[0]
        if name == "last":
            self._require_arg_count(name, args, 1)
            return self._require_matrix(name, args[0])[-1]
        if name == "at":
            self._require_arg_count(name, args, 2)
            matrix = self._require_matrix(name, args[1])
            index = self._resolve_minute_index(args[0], context)
            return matrix[index]
        if name == "delta":
            self._require_arg_range(name, args, 1, 2)
            matrix = self._require_matrix(name, args[0])
            periods = self._coerce_positive_int(args[1] if len(args) == 2 else 1, name)
            if periods >= matrix.shape[0]:
                return np.full_like(matrix, np.nan)
            result = np.full_like(matrix, np.nan)
            result[periods:] = matrix[periods:] - matrix[:-periods]
            return result
        if name in {"ts_mean", "ts_std", "ts_sum", "ts_min", "ts_max", "ts_rank"}:
            self._require_arg_count(name, args, 2)
            matrix = self._require_matrix(name, args[0])
            window = self._coerce_positive_int(args[1], name)
            return self._apply_ts_function(name, matrix, window)
        if name == "rank":
            self._require_arg_count(name, args, 1)
            return self._cross_sectional_rank(np.asarray(args[0], dtype=np.float64))
        if name == "zscore":
            self._require_arg_count(name, args, 1)
            return self._cross_sectional_zscore(np.asarray(args[0], dtype=np.float64))
        if name == "scale":
            self._require_arg_range(name, args, 1, 2)
            factor = float(args[1]) if len(args) == 2 else 1.0
            return self._cross_sectional_scale(np.asarray(args[0], dtype=np.float64), factor)
        if name == "winsorize":
            self._require_arg_range(name, args, 1, 2)
            limit = float(args[1]) if len(args) == 2 else 3.0
            return self._cross_sectional_winsorize(
                np.asarray(args[0], dtype=np.float64), limit
            )
        if name == "abs":
            self._require_arg_count(name, args, 1)
            return np.abs(args[0])
        if name == "log":
            self._require_arg_count(name, args, 1)
            array = np.asarray(args[0], dtype=np.float64)
            return np.where(array > 0, np.log(array), np.nan)
        if name == "sqrt":
            self._require_arg_count(name, args, 1)
            array = np.asarray(args[0], dtype=np.float64)
            return np.where(array >= 0, np.sqrt(array), np.nan)
        if name == "sign":
            self._require_arg_count(name, args, 1)
            return np.sign(args[0])
        if name == "where":
            self._require_arg_count(name, args, 3)
            return np.where(self._as_bool_array(args[0]), args[1], args[2])
        raise ExpressionError(f"Unknown function: {name}")

    def _apply_ts_function(self, name: str, matrix: np.ndarray, window: int) -> np.ndarray:
        chunk = matrix[-min(window, matrix.shape[0]) :]
        if name == "ts_mean":
            return self._nanmean_columns(chunk)
        if name == "ts_std":
            return self._nanstd_columns(chunk)
        if name == "ts_sum":
            return np.nansum(chunk, axis=0)
        if name == "ts_min":
            return self._nanmin_columns(chunk)
        if name == "ts_max":
            return self._nanmax_columns(chunk)
        if name == "ts_rank":
            return self._ts_rank(chunk)
        raise ExpressionError(f"Unknown time-series function: {name}")

    def _ts_rank(self, chunk: np.ndarray) -> np.ndarray:
        last_values = chunk[-1]
        output = np.full(chunk.shape[1], np.nan, dtype=np.float64)
        for column in range(chunk.shape[1]):
            series = chunk[:, column]
            valid = series[np.isfinite(series)]
            if valid.size == 0 or not np.isfinite(last_values[column]):
                continue
            rank = (valid <= last_values[column]).sum() / valid.size
            output[column] = rank
        return output

    def _nanmean_columns(self, chunk: np.ndarray) -> np.ndarray:
        valid = np.isfinite(chunk)
        counts = valid.sum(axis=0)
        totals = np.where(valid, chunk, 0.0).sum(axis=0)
        return np.divide(
            totals,
            counts,
            out=np.full(chunk.shape[1], np.nan, dtype=np.float64),
            where=counts != 0,
        )

    def _nanstd_columns(self, chunk: np.ndarray) -> np.ndarray:
        mean = self._nanmean_columns(chunk)
        valid = np.isfinite(chunk)
        counts = valid.sum(axis=0)
        centered = np.where(valid, chunk - mean, 0.0)
        variance = np.divide(
            np.square(centered).sum(axis=0),
            counts,
            out=np.full(chunk.shape[1], np.nan, dtype=np.float64),
            where=counts != 0,
        )
        return np.sqrt(variance)

    def _nanmin_columns(self, chunk: np.ndarray) -> np.ndarray:
        valid = np.isfinite(chunk)
        reduced = np.where(valid, chunk, np.inf).min(axis=0)
        return np.where(valid.any(axis=0), reduced, np.nan)

    def _nanmax_columns(self, chunk: np.ndarray) -> np.ndarray:
        valid = np.isfinite(chunk)
        reduced = np.where(valid, chunk, -np.inf).max(axis=0)
        return np.where(valid.any(axis=0), reduced, np.nan)

    def _cross_sectional_rank(self, array: np.ndarray) -> np.ndarray:
        if array.ndim == 1:
            return pd.Series(array).rank(method="average", pct=True).to_numpy(dtype=np.float64)
        if array.ndim == 2:
            ranked = pd.DataFrame(array).rank(axis=1, method="average", pct=True)
            return ranked.to_numpy(dtype=np.float64)
        raise ExpressionError("rank expects a vector or matrix.")

    def _cross_sectional_zscore(self, array: np.ndarray) -> np.ndarray:
        mean = np.nanmean(array, axis=-1, keepdims=True)
        std = np.nanstd(array, axis=-1, keepdims=True)
        centered = array - mean
        result = np.divide(
            centered,
            std,
            out=np.zeros_like(centered, dtype=np.float64),
            where=std != 0,
        )
        return result

    def _cross_sectional_scale(self, array: np.ndarray, factor: float) -> np.ndarray:
        denominator = np.nansum(np.abs(array), axis=-1, keepdims=True)
        result = np.divide(
            array * factor,
            denominator,
            out=np.full_like(array, np.nan, dtype=np.float64),
            where=denominator != 0,
        )
        return result

    def _cross_sectional_winsorize(self, array: np.ndarray, limit: float) -> np.ndarray:
        mean = np.nanmean(array, axis=-1, keepdims=True)
        std = np.nanstd(array, axis=-1, keepdims=True)
        lower = mean - limit * std
        upper = mean + limit * std
        return np.clip(array, lower, upper)

    def _resolve_minute_index(self, value: Any, context: EvaluationContext) -> int:
        if isinstance(value, str):
            if value not in context.minute_index:
                raise ExpressionError(f"Unknown minute label: {value}")
            return context.minute_index[value]
        if isinstance(value, (int, float)):
            index = int(value)
            minute_count = len(context.minute_index)
            if index < 0:
                index = minute_count + index
            if index < 0 or index >= minute_count:
                raise ExpressionError(f"Minute index out of range: {value}")
            return index
        raise ExpressionError("at expects a minute label string or integer index.")

    def _require_matrix(self, name: str, value: Any) -> np.ndarray:
        array = np.asarray(value, dtype=np.float64)
        if array.ndim != 2:
            raise ExpressionError(f"{name} expects a minute matrix input.")
        return array

    def _coerce_positive_int(self, value: Any, name: str) -> int:
        try:
            integer = int(value)
        except (TypeError, ValueError) as exc:
            raise ExpressionError(f"{name} expects an integer window.") from exc
        if integer < 1:
            raise ExpressionError(f"{name} expects a positive integer.")
        return integer

    def _as_bool_array(self, value: Any) -> np.ndarray:
        return np.asarray(value).astype(bool)

    def _require_arg_count(self, name: str, args: list[Any], expected: int) -> None:
        if len(args) != expected:
            raise ExpressionError(f"{name} expects {expected} arguments, got {len(args)}.")

    def _require_arg_range(
        self, name: str, args: list[Any], minimum: int, maximum: int
    ) -> None:
        if len(args) < minimum or len(args) > maximum:
            raise ExpressionError(
                f"{name} expects between {minimum} and {maximum} arguments, got {len(args)}."
            )
