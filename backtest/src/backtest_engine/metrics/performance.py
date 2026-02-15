from __future__ import annotations

from math import sqrt


def _drawdown(nav_values: list[float]) -> list[float]:
    max_nav = nav_values[0]
    dds: list[float] = []
    for v in nav_values:
        max_nav = max(max_nav, v)
        dds.append(v / max_nav - 1.0)
    return dds


def calc_performance(
    nav_series: list[tuple[str, float]],
    daily_turnover: list[tuple[str, float]],
    initial_cash: float,
) -> dict:
    if not nav_series:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "turnover": 0.0,
            "drawdown_series": [],
        }

    nav_values = [x[1] for x in nav_series]
    rets = []
    for i in range(1, len(nav_values)):
        prev = nav_values[i - 1]
        rets.append(nav_values[i] / prev - 1.0 if prev else 0.0)

    total_return = nav_values[-1] / nav_values[0] - 1.0 if nav_values[0] else 0.0

    n = len(rets)
    if n > 0:
        avg = sum(rets) / n
        var = sum((r - avg) ** 2 for r in rets) / n
        vol = var**0.5
        sharpe = avg / vol * sqrt(252) if vol > 0 else 0.0
        annualized_return = (1.0 + total_return) ** (252 / max(1, len(nav_values) - 1)) - 1.0
    else:
        sharpe = 0.0
        annualized_return = 0.0

    drawdowns = _drawdown(nav_values)
    max_dd = min(drawdowns)

    total_turnover = sum(v for _, v in daily_turnover)
    turnover = total_turnover / initial_cash if initial_cash else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annualized_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "turnover": turnover,
        "drawdown_series": list(zip([x[0] for x in nav_series], drawdowns)),
    }
