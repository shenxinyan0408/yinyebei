from __future__ import annotations

import json
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
    CorrelationEngine,
    CorrelationError,
    DailyLabelStore,
    ExpressionEngine,
    ExpressionError,
    MinuteDataStore,
    build_catalog_payload,
)
from engine.catalog import (
    DAILY_DATA_FILE,
    DEFAULT_EXPRESSION,
    EXAMPLE_EXPRESSIONS,
    FIXED_RULES,
    MINUTE_DATA_DIR,
)

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
DATA_CONFIG_FILE = RUNTIME_DIR / "data_sources.json"


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False

    catalog_payload = build_catalog_payload()
    max_workers = max(1, int(os.environ.get("APP_MAX_WORKERS", "5")))
    executor = ThreadPoolExecutor(max_workers=max_workers)
    cache_executor = ThreadPoolExecutor(max_workers=1)
    jobs: dict[str, dict[str, Any]] = {}
    jobs_lock = threading.Lock()
    cache_job_state = {
        "activeJobId": None,
    }
    cache_job_lock = threading.Lock()

    runtime_state: dict[str, Any] = {
        "data_dir": Path(MINUTE_DATA_DIR),
        "daily_data_file": Path(DAILY_DATA_FILE),
        "path_sources": {
            "minuteDataDir": "default",
            "dailyDataFile": "default",
        },
        "boot_error": None,
        "data_store": None,
        "backtest_engine": None,
        "correlation_engine": None,
    }

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
        for key in ("minuteDataDir", "dailyDataFile"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
        return result

    def write_saved_data_config(payload: dict[str, str]) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        DATA_CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def resolve_data_paths() -> tuple[Path, Path, dict[str, str]]:
        saved_config = read_saved_data_config()

        minute_data_dir = os.environ.get("MINUTE_DATA_DIR")
        daily_data_file = os.environ.get("DAILY_DATA_FILE")

        if minute_data_dir and minute_data_dir.strip():
            minute_path = Path(minute_data_dir.strip())
            minute_source = "env"
        elif saved_config.get("minuteDataDir"):
            minute_path = Path(saved_config["minuteDataDir"])
            minute_source = "saved"
        else:
            minute_path = Path(MINUTE_DATA_DIR)
            minute_source = "default"

        if daily_data_file and daily_data_file.strip():
            daily_path = Path(daily_data_file.strip())
            daily_source = "env"
        elif saved_config.get("dailyDataFile"):
            daily_path = Path(saved_config["dailyDataFile"])
            daily_source = "saved"
        else:
            daily_path = Path(DAILY_DATA_FILE)
            daily_source = "default"

        return minute_path, daily_path, {
            "minuteDataDir": minute_source,
            "dailyDataFile": daily_source,
        }

    def build_boot_error(exc: Exception) -> str:
        return (
            f"数据初始化失败：{exc}。"
            "请在页面里的数据路径设置区填写本机分钟数据目录和标签文件路径，"
            "或者通过 MINUTE_DATA_DIR / DAILY_DATA_FILE 环境变量指定。"
        )

    def initialize_services() -> None:
        data_dir, daily_data_file, path_sources = resolve_data_paths()
        runtime_state.update(
            {
                "data_dir": data_dir,
                "daily_data_file": daily_data_file,
                "path_sources": path_sources,
                "boot_error": None,
                "data_store": None,
                "backtest_engine": None,
                "correlation_engine": None,
            }
        )

        try:
            data_store = MinuteDataStore(data_dir=data_dir)
            label_store = DailyLabelStore(daily_data_file)
            expression_engine = ExpressionEngine()
            runtime_state["data_store"] = data_store
            runtime_state["backtest_engine"] = BacktestEngine(
                data_store,
                expression_engine,
                label_store,
            )
            runtime_state["correlation_engine"] = CorrelationEngine(
                data_store,
                expression_engine,
            )
        except Exception as exc:
            runtime_state["boot_error"] = build_boot_error(exc)

    initialize_services()

    def cache_summary_payload() -> dict[str, Any]:
        data_store: MinuteDataStore | None = runtime_state["data_store"]
        if data_store is None:
            return {
                "cacheDir": "",
                "totalDates": 0,
                "readyDates": 0,
                "missingDates": 0,
                "startDate": "",
                "endDate": "",
            }
        return data_store.summarize_cache()

    def base_meta() -> dict[str, Any]:
        data_store: MinuteDataStore | None = runtime_state["data_store"]
        if data_store is not None:
            dates = data_store.available_dates()
            date_range = {"start": dates[0], "end": dates[-1]}
            minute_cache_dir = str(data_store.cache_dir)
        else:
            date_range = {"start": "", "end": ""}
            minute_cache_dir = ""

        return {
            "dateRange": date_range,
            "fixedRules": FIXED_RULES,
            "exampleExpressions": EXAMPLE_EXPRESSIONS,
            "defaultExpression": DEFAULT_EXPRESSION,
            "dataDirectory": str(runtime_state["data_dir"]),
            "dailyDataFile": str(runtime_state["daily_data_file"]),
            "minuteCacheDirectory": minute_cache_dir,
            "bootError": runtime_state["boot_error"],
            "parallelLimitDefault": max_workers,
            "parallelLimitMax": max_workers,
            "cacheSummary": cache_summary_payload(),
            "dataSetup": {
                "minuteDataDir": str(runtime_state["data_dir"]),
                "dailyDataFile": str(runtime_state["daily_data_file"]),
                "minuteCacheDir": minute_cache_dir,
                "pathSources": runtime_state["path_sources"],
                "configFile": str(DATA_CONFIG_FILE),
            },
        }

    def set_job(job_id: str, **updates: Any) -> None:
        with jobs_lock:
            jobs.setdefault(job_id, {})
            jobs[job_id].update(updates)

    def get_job(job_id: str) -> dict[str, Any] | None:
        with jobs_lock:
            return jobs.get(job_id)

    def run_backtest_job(job_id: str, payload: dict[str, Any]) -> None:
        def on_progress(progress_value: float, message: str) -> None:
            set_job(
                job_id,
                status="running",
                progress=max(0.0, min(1.0, progress_value)),
                message=message,
            )

        try:
            backtest_engine: BacktestEngine | None = runtime_state["backtest_engine"]
            if backtest_engine is None:
                raise BacktestError(runtime_state["boot_error"] or "回测引擎尚未就绪。")
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
        except Exception as exc:  # pragma: no cover
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

    def run_correlation_job(job_id: str, payload: dict[str, Any]) -> None:
        def on_progress(progress_value: float, message: str) -> None:
            set_job(
                job_id,
                status="running",
                progress=max(0.0, min(1.0, progress_value)),
                message=message,
            )

        try:
            correlation_engine: CorrelationEngine | None = runtime_state["correlation_engine"]
            if correlation_engine is None:
                raise CorrelationError(runtime_state["boot_error"] or "因子相关性引擎尚未就绪。")
            factor_a = payload.get("factorA", {}) or {}
            factor_b = payload.get("factorB", {}) or {}
            result = correlation_engine.run(
                expression_a=str(factor_a.get("expression", "")),
                start_date_a=str(factor_a.get("startDate", "")),
                end_date_a=str(factor_a.get("endDate", "")),
                decay_a=int(factor_a.get("decay", 1)),
                expression_b=str(factor_b.get("expression", "")),
                start_date_b=str(factor_b.get("startDate", "")),
                end_date_b=str(factor_b.get("endDate", "")),
                decay_b=int(factor_b.get("decay", 1)),
                progress=on_progress,
            )
        except (CorrelationError, ExpressionError) as exc:
            set_job(
                job_id,
                status="failed",
                progress=1.0,
                message=str(exc),
                errorType=exc.__class__.__name__,
            )
            return
        except Exception as exc:  # pragma: no cover
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
            message="相关性计算完成。",
            result=result,
        )

    def run_cache_prewarm_job(job_id: str, payload: dict[str, Any]) -> None:
        def on_progress(progress_value: float, message: str) -> None:
            set_job(
                job_id,
                status="running",
                progress=max(0.0, min(1.0, progress_value)),
                message=message,
            )

        try:
            data_store: MinuteDataStore | None = runtime_state["data_store"]
            if data_store is None:
                raise RuntimeError(runtime_state["boot_error"] or "分钟数据尚未连接。")
            result = data_store.prewarm_cache(
                start_date=str(payload.get("startDate", "")).strip() or None,
                end_date=str(payload.get("endDate", "")).strip() or None,
                force=bool(payload.get("force", False)),
                progress=on_progress,
            )
            result["cacheSummary"] = data_store.summarize_cache(
                str(payload.get("startDate", "")).strip() or None,
                str(payload.get("endDate", "")).strip() or None,
            )
        except Exception as exc:
            set_job(
                job_id,
                status="failed",
                progress=1.0,
                message=str(exc),
                errorType=exc.__class__.__name__,
            )
            with cache_job_lock:
                if cache_job_state["activeJobId"] == job_id:
                    cache_job_state["activeJobId"] = None
            return

        set_job(
            job_id,
            status="succeeded",
            progress=1.0,
            message="缓存预热完成。",
            result=result,
        )
        with cache_job_lock:
            if cache_job_state["activeJobId"] == job_id:
                cache_job_state["activeJobId"] = None

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

    @app.post("/api/data-config")
    def update_data_config() -> Any:
        payload = request.get_json(silent=True) or {}
        minute_data_dir = str(payload.get("minuteDataDir", "")).strip()
        daily_data_file = str(payload.get("dailyDataFile", "")).strip()
        if not minute_data_dir and not daily_data_file:
            return jsonify({"message": "请至少填写一个数据路径。"}), 400

        saved_config = read_saved_data_config()
        config_payload: dict[str, str] = dict(saved_config)
        if minute_data_dir:
            config_payload["minuteDataDir"] = minute_data_dir
        if daily_data_file:
            config_payload["dailyDataFile"] = daily_data_file

        try:
            write_saved_data_config(config_payload)
            initialize_services()
        except Exception as exc:
            return jsonify({"message": f"保存数据路径失败：{exc}"}), 500

        return jsonify(base_meta())

    @app.post("/api/cache-prewarm")
    def create_cache_prewarm() -> Any:
        data_store: MinuteDataStore | None = runtime_state["data_store"]
        if data_store is None:
            return jsonify({"message": runtime_state["boot_error"] or "分钟数据尚未连接。"}), 503

        with cache_job_lock:
            active_job_id = cache_job_state["activeJobId"]
            active_job = get_job(active_job_id) if active_job_id else None
            if active_job and active_job.get("status") in {"queued", "running"}:
                return (
                    jsonify(
                        {
                            "message": "已有缓存预热任务在运行，请等待当前任务完成。",
                            "jobId": active_job_id,
                            "status": active_job.get("status"),
                        }
                    ),
                    409,
                )

            job_id = uuid.uuid4().hex
            cache_job_state["activeJobId"] = job_id

        payload = request.get_json(silent=True) or {}
        set_job(
            job_id,
            id=job_id,
            type="cachePrewarm",
            status="queued",
            progress=0.0,
            message="已加入缓存预热队列。",
        )
        cache_executor.submit(run_cache_prewarm_job, job_id, payload)
        return jsonify({"jobId": job_id, "status": "queued"}), 202

    @app.get("/api/cache-prewarm/<job_id>")
    def get_cache_prewarm(job_id: str) -> Any:
        job = get_job(job_id)
        if job is None:
            return jsonify({"message": "未找到对应任务。"}), 404
        return jsonify(job)

    @app.post("/api/backtests")
    def create_backtest() -> Any:
        backtest_engine: BacktestEngine | None = runtime_state["backtest_engine"]
        if backtest_engine is None:
            return jsonify({"message": runtime_state["boot_error"] or "回测引擎尚未就绪。"}), 503
        payload = request.get_json(silent=True) or {}
        job_id = uuid.uuid4().hex
        set_job(
            job_id,
            id=job_id,
            type="backtest",
            status="queued",
            progress=0.0,
            message="已加入队列。",
        )
        executor.submit(run_backtest_job, job_id, payload)
        return jsonify({"jobId": job_id, "status": "queued"}), 202

    @app.get("/api/backtests/<job_id>")
    def get_backtest(job_id: str) -> Any:
        job = get_job(job_id)
        if job is None:
            return jsonify({"message": "未找到对应任务。"}), 404
        return jsonify(job)

    @app.post("/api/correlations")
    def create_correlation() -> Any:
        correlation_engine: CorrelationEngine | None = runtime_state["correlation_engine"]
        if correlation_engine is None:
            return jsonify({"message": runtime_state["boot_error"] or "因子相关性引擎尚未就绪。"}), 503
        payload = request.get_json(silent=True) or {}
        job_id = uuid.uuid4().hex
        set_job(
            job_id,
            id=job_id,
            type="correlation",
            status="queued",
            progress=0.0,
            message="已加入队列。",
        )
        executor.submit(run_correlation_job, job_id, payload)
        return jsonify({"jobId": job_id, "status": "queued"}), 202

    @app.get("/api/correlations/<job_id>")
    def get_correlation(job_id: str) -> Any:
        job = get_job(job_id)
        if job is None:
            return jsonify({"message": "未找到对应任务。"}), 404
        return jsonify(job)

    return app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
