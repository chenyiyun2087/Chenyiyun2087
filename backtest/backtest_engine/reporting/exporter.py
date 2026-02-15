from __future__ import annotations

import json
from pathlib import Path

from backtest_engine.core.engine import BacktestResult


def build_report(
    strategy_id: str,
    start: str,
    end: str,
    freq: str,
    universe_size: int,
    result: BacktestResult,
    metrics: dict,
) -> dict:
    return {
        "meta": {
            "strategy_id": strategy_id,
            "start": start,
            "end": end,
            "freq": freq,
            "universe_size": universe_size,
        },
        "metrics": {
            "total_return": metrics["total_return"],
            "annualized_return": metrics["annualized_return"],
            "sharpe": metrics["sharpe"],
            "max_drawdown": metrics["max_drawdown"],
            "turnover": metrics["turnover"],
        },
        "timeseries": {
            "nav": [[ts, nav] for ts, nav in result.nav_series],
            "drawdown": [[ts, dd] for ts, dd in metrics["drawdown_series"]],
        },
        "trades": [
            {
                "ts": t.ts,
                "symbol": t.symbol,
                "side": t.side,
                "qty": t.qty,
                "price": t.price,
            }
            for t in result.trades
        ],
    }


def export_report_json(report: dict, output_path: str) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
