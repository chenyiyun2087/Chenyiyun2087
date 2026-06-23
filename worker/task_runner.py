"""任务执行器 — 从 app_task_queue 消费并执行任务。

可独立于 Flask 进程运行，也可嵌入 web/app.py 内部调度器。
读取 task_registry/pipeline.yaml 获取任务定义，消费 app_task_queue。

用法:
  PYTHONPATH=. python worker/task_runner.py          # 独立运行
  DISABLE_APP_SCHEDULER_LOOP=1 python worker/task_runner.py  # 开发模式（不启动 Flask）
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("worker.task_runner")

PIPELINE_PATH = PROJECT_ROOT / "task_registry" / "pipeline.yaml"
POLL_INTERVAL_SECONDS = 20
JITTER_SECONDS = 3  # 随机抖动，避免惊群


def load_pipeline_config() -> dict:
    """加载流水线定义。"""
    if not PIPELINE_PATH.exists():
        logger.warning("pipeline.yaml not found at %s", PIPELINE_PATH)
        return {}
    with open(PIPELINE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def is_trade_day(check_date: date, engine) -> bool:
    """检查是否为交易日。"""
    from sqlalchemy import text

    date_int = int(check_date.strftime("%Y%m%d"))
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT is_open FROM chenyiyun.dim_trade_cal "
                "WHERE exchange='SSE' AND cal_date = :d"
            ),
            {"d": str(date_int)},
        ).scalar()
    return bool(result)


def run_script(script_path: str, args: list[str], task_id: str) -> bool:
    """执行一个脚本，返回成功/失败。"""
    import random

    jitter = random.uniform(0, JITTER_SECONDS)
    time.sleep(jitter)

    cmd = [sys.executable, str(PROJECT_ROOT / script_path)] + args
    logger.info("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            logger.error("Task %s failed (rc=%d): %s", task_id, result.returncode, result.stderr[:500])
            return False
        logger.info("Task %s completed successfully", task_id)
        return True
    except subprocess.TimeoutExpired:
        logger.error("Task %s timed out", task_id)
        return False
    except Exception as e:
        logger.error("Task %s exception: %s", task_id, e)
        return False


def main():
    from scoreRank.core.db_config import build_sqlalchemy_url
    from sqlalchemy import create_engine

    engine = create_engine(build_sqlalchemy_url())
    pipeline = load_pipeline_config()

    if not pipeline:
        logger.error("No pipeline config found — exiting")
        return

    logger.info("Worker started. Polling every %ds...", POLL_INTERVAL_SECONDS)

    while True:
        try:
            today = date.today()
            if not is_trade_day(today, engine):
                logger.info("Non-trading day, sleeping...")
                time.sleep(60)
                continue

            # Process daily_close tasks
            daily_tasks = pipeline.get("daily_close", {}).get("tasks", [])
            for task_def in daily_tasks:
                if task_def.get("status") != "enabled":
                    continue
                task_id = task_def["id"]
                script = task_def["script"]
                task_type = task_def.get("type", "script")
                scheduled_time_str = task_def.get("time", "00:00")
                scheduled_h, scheduled_m = map(int, scheduled_time_str.split(":"))
                now = datetime.now()

                # Check if it's time to run (within a window)
                scheduled_dt = now.replace(hour=scheduled_h, minute=scheduled_m, second=0, microsecond=0)
                if now < scheduled_dt or now > scheduled_dt.replace(hour=scheduled_h + 2):
                    continue

                # Check if already run today (via app_task_history)
                with engine.connect() as conn:
                    from sqlalchemy import text as _t
                    already = conn.execute(
                        _t(
                            "SELECT COUNT(*) FROM chenyiyun.app_task_history "
                            "WHERE task_name = :tn AND business_date = :bd AND status = 'SUCCESS'"
                        ),
                        {"tn": task_id, "bd": today.isoformat()},
                    ).scalar()
                if already:
                    continue

                logger.info("Dispatching task: %s", task_id)
                run_script(script, [today.strftime("%Y%m%d")], task_id)

            time.sleep(POLL_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Worker stopped")
            break
        except Exception:
            logger.exception("Worker loop error — retrying in 30s")
            time.sleep(30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
