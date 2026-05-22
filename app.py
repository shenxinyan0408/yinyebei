from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from engine import (
    BacktestEngine,
    BacktestError,
    ExpressionEngine,
    ExpressionError,
    MinuteDataStore,
    build_catalog_payload,
)
from engine.catalog import (
    DEFAULT_EXPRESSION,
    EXAMPLE_EXPRESSIONS,
    FIXED_RULES,
    MINUTE_DATA_DIR,
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False

    data_dir = Path(os.environ.get("MINUTE_DATA_DIR", MINUTE_DATA_DIR))
    catalog_payload = build_catalog_payload()
    max_workers = max(1, int(os.environ.get("APP_MAX_WORKERS", "5")))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    jobs: dict[str, dict[str, Any]] = {}
    jobs_lock = threading.Lock()
    boot_error: str | None = None
    data_store: MinuteDataStore | None = None
    backtest_engine: BacktestEngine | None = None

    try:
        data_store = MinuteDataStore(data_dir=data_dir)
        expression_engine = ExpressionEngine()
        backtest_engine = BacktestEngine(data_store, expression_engine)
    except Exception as exc:
        boot_error = f"数据初始化失败：{exc}"

    def base_meta() -> dict[str, Any]:
        if data_store is not None:
            dates = data_store.available_dates()
            date_range = {"start": dates[0], "end": dates[-1]}
        else:
            date_range = {"start": "", "end": ""}
        return {
            "dateRange": date_range,
            "fixedRules": FIXED_RULES,
            "exampleExpressions": EXAMPLE_EXPRESSIONS,
            "defaultExpression": DEFAULT_EXPRESSION,
            "dataDirectory": str(data_dir),
            "bootError": boot_error,
            "parallelLimitDefault": max_workers,
            "parallelLimitMax": max_workers,
        }

    def set_job(job_id: str, **updates: Any) -> None:
        with jobs_lock:
            jobs.setdefault(job_id, {})
            jobs[job_id].update(updates)

    def run_backtest_job(job_id: str, payload: dict[str, Any]) -> None:
        def on_progress(progress_value: float, message: str) -> None:
            set_job(
                job_id,
                status="running",
                progress=max(0.0, min(1.0, progress_value)),
                message=message,
            )

        try:
            if backtest_engine is None:
                raise BacktestError(boot_error or "回测引擎尚未就绪。")
            result = backtest_engine.run(
                expression=str(payload.get("expression", "")),
                start_date=str(payload.get("startDate", "")),
                end_date=str(payload.get("endDate", "")),
                decay=int(payload.get("decay", 1)),
                progress=on_progress,
            )
        except (BacktestError, ExpressionError) as exc:
            set_job(
                job_id,
                status="failed",
                progress=1.0,
                message=str(exc),
                errorType=exc.__class__.__name__,
            )
            return
        except Exception as exc:  # pragma: no cover - defensive path
            set_job(
                job_id,
                status="failed",
                progress=1.0,
                message=f"服务端异常：{exc}",
                errorType="ServerError",
            )
            return

        set_job(
            job_id,
            status="succeeded",
            progress=1.0,
            message="回测完成。",
            result=result,
        )

    @app.get("/")
    def index() -> str:
        meta = base_meta()
        return render_template(
            "index.html",
            default_expression=DEFAULT_EXPRESSION,
            default_start=meta["dateRange"]["start"],
            default_end=meta["dateRange"]["end"],
        )

    @app.get("/api/meta")
    def meta() -> Any:
        return jsonify(base_meta())

    @app.get("/api/catalog")
    def catalog() -> Any:
        return jsonify(catalog_payload)

    @app.post("/api/backtests")
    def create_backtest() -> Any:
        if backtest_engine is None:
            return jsonify({"message": boot_error or "回测引擎尚未就绪。"}), 503
        payload = request.get_json(silent=True) or {}
        job_id = uuid.uuid4().hex
        set_job(
            job_id,
            id=job_id,
            status="queued",
            progress=0.0,
            message="已加入队列。",
        )
        executor.submit(run_backtest_job, job_id, payload)
        return jsonify({"jobId": job_id, "status": "queued"}), 202

    @app.get("/api/backtests/<job_id>")
    def get_backtest(job_id: str) -> Any:
        with jobs_lock:
            job = jobs.get(job_id)
        if job is None:
            return jsonify({"message": "未找到对应任务。"}), 404
        return jsonify(job)

    return app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
