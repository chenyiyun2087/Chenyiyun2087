"""Run the isolated T21:30/T+1 Smart Beta and Pure Alpha research chains.

This module is deterministic and offline. It accepts a qualified PIT panel;
formal runners must validate the path-bound E3 package before calling it.
"""
from __future__ import annotations

import math
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from runtime.canonical_execution_kernel import AccountState, execute_order
from scripts.research.advanced_statistical_validation import (
    benjamini_hochberg, cscv_pbo, permutation_test,
)
from scripts.research.build_capacity_stress_matrix import build_capacity_stress_matrix
from scripts.research.execution_costs import ExecutionCostModel


DECISION_CONTRACT_ID = "ashare_t2130_t1_v1"
CANDIDATES = {
    "equal_3f": ("reversal_20d", "liquidity", "low_vol_20d"),
    "leaveout_reversal": ("liquidity", "low_vol_20d"),
    "leaveout_liquidity": ("reversal_20d", "low_vol_20d"),
    "leaveout_low_vol": ("reversal_20d", "liquidity"),
}


def _zscore(values: pd.Series) -> pd.Series:
    std = float(values.std(ddof=0))
    return (values - float(values.mean())) / std if std > 0 else values * 0.0


def _qr_residual(group: pd.DataFrame, values: pd.Series) -> pd.Series:
    industry = pd.get_dummies(group["industry"].astype(str), prefix="industry", drop_first=True, dtype=float)
    controls = pd.concat([
        pd.Series(1.0, index=group.index, name="intercept"),
        industry,
        np.log(pd.to_numeric(group["circ_mv"], errors="coerce").clip(lower=1.0)).rename("log_circ_mv"),
        pd.to_numeric(group["market_beta"], errors="coerce").rename("market_beta"),
    ], axis=1).replace([np.inf, -np.inf], np.nan)
    valid = controls.notna().all(axis=1) & values.notna()
    result = pd.Series(np.nan, index=group.index, dtype=float)
    if int(valid.sum()) <= controls.shape[1]:
        return result
    x = controls.loc[valid].to_numpy(float)
    y = values.loc[valid].to_numpy(float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    result.loc[valid] = y - x @ beta
    return _zscore(result)


def build_factor_panel(panel: pd.DataFrame, *, pure_alpha: bool) -> pd.DataFrame:
    required = {"trade_date", "symbol", "raw_open", "raw_close", "industry", "circ_mv", "market_beta", "adv20_cny"}
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"pit_panel_missing:{','.join(missing)}")
    frame = panel.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    grouped = frame.groupby("symbol", sort=False)
    returns = grouped["raw_close"].pct_change(fill_method=None)
    frame["reversal_20d_raw"] = -grouped["raw_close"].pct_change(20, fill_method=None)
    amount = pd.to_numeric(frame.get("amount", frame["adv20_cny"]), errors="coerce").clip(lower=1.0)
    frame["liquidity_raw"] = -(returns.abs() / amount).groupby(frame["symbol"]).transform(lambda s: s.rolling(20).mean())
    frame["low_vol_20d_raw"] = -returns.groupby(frame["symbol"]).transform(lambda s: s.rolling(20).std())
    if "eligible_universe" in frame:
        frame = frame[frame["eligible_universe"].fillna(False).astype(bool)].copy()
    for factor in ("reversal_20d", "liquidity", "low_vol_20d"):
        frame[factor] = frame.groupby("trade_date", group_keys=False).apply(
            lambda group, name=factor: _qr_residual(group, group[f"{name}_raw"]),
            include_groups=False,
        ).reset_index(level=0, drop=True).reindex(frame.index)
    for candidate_id, factors in CANDIDATES.items():
        score = frame[list(factors)].mean(axis=1)
        if pure_alpha:
            score = frame.groupby("trade_date", group_keys=False).apply(
                lambda group: _qr_residual(group, score.loc[group.index]),
                include_groups=False,
            ).reset_index(level=0, drop=True).reindex(frame.index)
        frame[f"score_{candidate_id}"] = score
    return frame


def _weights(group: pd.DataFrame, score_column: str) -> pd.Series:
    selected = group.dropna(subset=[score_column, "low_vol_20d_raw"]).nlargest(20, score_column)
    output = pd.Series(0.0, index=group.index)
    if selected.empty:
        return output
    inverse_vol = 1.0 / selected["low_vol_20d_raw"].abs().clip(lower=1e-6)
    raw = inverse_vol / inverse_vol.sum()
    capped = raw.clip(upper=0.10)
    for _ in range(20):
        room = 0.10 - capped
        shortfall = 1.0 - float(capped.sum())
        if shortfall <= 1e-12 or float(room.clip(lower=0).sum()) <= 0:
            break
        capped += room.clip(lower=0) / room.clip(lower=0).sum() * shortfall
        capped = capped.clip(upper=0.10)
    portfolio_vol = float(np.sqrt(np.sum((capped.to_numpy() * selected["low_vol_20d_raw"].abs().to_numpy()) ** 2)) * np.sqrt(252))
    scale = min(1.0, 0.15 / portfolio_vol) if portfolio_vol > 0 else 0.0
    output.loc[selected.index] = capped * scale
    return output


def _candidate_daily_returns(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float], pd.DataFrame]:
    ordered = frame.sort_values(["symbol", "trade_date"]).copy()
    ordered["next_return"] = ordered.groupby("symbol")["raw_close"].shift(-1) / ordered.groupby("symbol")["raw_open"].shift(-1) - 1.0
    series: dict[str, pd.Series] = {}
    turnovers: dict[str, float] = {}
    for candidate_id in CANDIDATES:
        weight = ordered.groupby("trade_date", group_keys=False).apply(
            lambda group: _weights(group, f"score_{candidate_id}"), include_groups=False,
        ).reset_index(level=0, drop=True).reindex(ordered.index).fillna(0.0)
        ordered[f"weight_{candidate_id}"] = weight
        gross = (weight * ordered["next_return"].fillna(0.0)).groupby(ordered["trade_date"]).sum()
        daily_turnover = weight.groupby(ordered["symbol"]).diff().abs().groupby(ordered["trade_date"]).sum().fillna(0.0)
        series[candidate_id] = gross - daily_turnover * 0.0015
        turnovers[candidate_id] = float(daily_turnover.mean())
    return pd.DataFrame(series).sort_index(), turnovers, ordered


def nested_select(candidate_returns: pd.DataFrame, turnovers: dict[str, float]) -> dict[str, Any]:
    minimum = 252 + 20 + 63 + 20 + 63
    if len(candidate_returns) < minimum:
        return {"status": "BLOCKED", "reason": f"nested_sample_insufficient:{len(candidate_returns)}<{minimum}"}
    rows = []
    start = 0
    while start + minimum <= len(candidate_returns):
        train = candidate_returns.iloc[start:start + 252]
        validation = candidate_returns.iloc[start + 272:start + 335]
        test = candidate_returns.iloc[start + 355:start + 418]
        scores = {}
        for candidate in candidate_returns.columns:
            values = pd.concat([train[candidate], validation[candidate]]).dropna()
            sharpe = float(values.mean() / values.std(ddof=1) * np.sqrt(252)) if len(values) > 1 and values.std(ddof=1) > 0 else -math.inf
            scores[candidate] = sharpe
        selected = sorted(scores, key=lambda key: (-scores[key], turnovers[key], key))[0]
        rows.append({"start": str(candidate_returns.index[start].date()), "candidate_id": selected,
                     "inner_net_excess_sharpe": scores[selected], "outer_returns": test[selected].tolist()})
        start += 63
    return {"status": "PASS", "folds": rows, "selected_candidate": rows[-1]["candidate_id"],
            "purge_days": 20, "embargo_days": 20, "train_days": 252, "validation_days": 63, "test_days": 63}


def _execute(frame: pd.DataFrame, candidate_id: str, initial_cash: float) -> dict[str, pd.DataFrame]:
    dates = sorted(frame["trade_date"].drop_duplicates())
    state = AccountState(float(initial_cash))
    model = ExecutionCostModel.for_scenario("BASE")
    pending: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    for index, date in enumerate(dates):
        day = frame[frame["trade_date"].eq(date)].set_index("symbol", drop=False)
        for order in pending:
            if order["symbol"] not in day.index:
                rejections.append({**order, "execution_date": date, "reason": "missing_execution_bar"})
                continue
            bar = day.loc[order["symbol"]]
            if isinstance(bar, pd.DataFrame):
                raise ValueError(f"duplicate_panel_bar:{date}:{order['symbol']}")
            planned = min(int(order["shares"]), int(0.10 * float(bar["adv20_cny"]) / float(bar["raw_open"])))
            result = execute_order(state, order_id=order["order_id"], symbol=order["symbol"], side=order["side"],
                                   planned_shares=max(planned, 1), price=float(bar["raw_open"]), tradable=planned > 0,
                                   reject_reason="adv_participation_block" if planned <= 0 else "", cost_model=model)
            record = {**result.as_dict(), "signal_date": order["signal_date"], "execution_date": date}
            (fills if result.filled_shares else rejections).append(record)
        pending = []
        if index % 20 == 0 and index + 1 < len(dates):
            weights = _weights(day, f"score_{candidate_id}")
            close_prices = day["raw_close"].astype(float).to_dict()
            nav = state.cash + sum(shares * close_prices.get(symbol, 0.0) for symbol, shares in state.positions.items())
            targets = {str(day.loc[row_index, "symbol"]): float(weight) for row_index, weight in weights.items() if weight > 0}
            for symbol in sorted(set(state.positions) | set(targets)):
                px = float(day.loc[symbol, "raw_close"]) if symbol in day.index else 0.0
                target = int(nav * targets.get(symbol, 0.0) / px / 100) * 100 if px > 0 else 0
                delta = target - state.positions.get(symbol, 0)
                if delta:
                    order = {"order_id": f"{date.date()}:{symbol}:{'BUY' if delta > 0 else 'SELL'}",
                             "signal_date": date, "execution_date": dates[index + 1], "symbol": symbol,
                             "side": "BUY" if delta > 0 else "SELL", "shares": abs(delta)}
                    orders.append(order); pending.append(order)
        close_prices = day["raw_close"].astype(float).to_dict()
        market_value = sum(shares * close_prices.get(symbol, 0.0) for symbol, shares in state.positions.items())
        benchmark = float(day["benchmark_close"].dropna().iloc[0]) if "benchmark_close" in day and day["benchmark_close"].notna().any() else np.nan
        nav_rows.append({"trade_date": date, "cash": state.cash, "market_value": market_value, "nav": state.cash + market_value, "benchmark_close": benchmark})
    nav = pd.DataFrame(nav_rows)
    nav["return"] = nav["nav"].pct_change(fill_method=None).fillna(0.0)
    nav["benchmark_return"] = nav["benchmark_close"].pct_change(fill_method=None).fillna(0.0)
    nav["excess_return"] = nav["return"] - nav["benchmark_return"]
    return {"orders": pd.DataFrame(orders), "fills": pd.DataFrame(fills), "rejections": pd.DataFrame(rejections), "nav": nav}


def run_t2130_pipeline(panel: pd.DataFrame, *, strategy_id: str, initial_cash: float = 500_000.0,
                       n_permutations: int = 999, seed: int = 20260810) -> dict[str, Any]:
    pure = strategy_id == "pure_alpha_residual_v1_t2130"
    if strategy_id not in {"smart_beta_v1_t2130", "pure_alpha_residual_v1_t2130"}:
        raise ValueError("unsupported_t2130_strategy")
    factors = build_factor_panel(panel, pure_alpha=pure)
    candidate_returns, turnovers, weighted = _candidate_daily_returns(factors)
    selection = nested_select(candidate_returns, turnovers)
    if selection["status"] != "PASS":
        return {"status": "BLOCKED", "reason": selection["reason"], "strategy_id": strategy_id,
                "decision_contract_id": DECISION_CONTRACT_ID, "panel": panel.copy(), "factors": factors,
                "weights": weighted[["trade_date", "symbol"] + [f"weight_{key}" for key in CANDIDATES]],
                "candidate_returns": candidate_returns}
    p_values = {candidate: permutation_test(candidate_returns[candidate].dropna(), n_permutations=n_permutations,
                                             seed=seed, block_size=20)["p_value"] for candidate in CANDIDATES}
    fdr = benjamini_hochberg(p_values)
    pbo = cscv_pbo(candidate_returns.to_numpy().T)
    execution = _execute(weighted, selection["selected_candidate"], initial_cash)
    observations = []
    for row in execution["orders"].to_dict("records"):
        match = weighted[(weighted["trade_date"].eq(row["signal_date"])) & (weighted["symbol"].eq(row["symbol"]))]
        if not match.empty:
            observations.append({"planned_notional": row["shares"] * float(match.iloc[0]["raw_close"]),
                                 "adv20_cny": float(match.iloc[0]["adv20_cny"]), "base_capital_cny": initial_cash})
    capacity = build_capacity_stress_matrix(observations) if observations else pd.DataFrame()
    return {"status": "RESEARCH_COMPLETE", "formal": False, "capital_cny": 0.0, "strategy_id": strategy_id,
            "decision_contract_id": DECISION_CONTRACT_ID, "panel": panel.copy(), "factors": factors,
            "weights": weighted[["trade_date", "symbol"] + [f"weight_{key}" for key in CANDIDATES]],
            "candidate_returns": candidate_returns,
            "selection": selection, "statistics": {"permutation_p": p_values, "fdr": fdr, "pbo": pbo},
            "capacity": capacity, **execution}


def write_research_bundle(result: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """Write one auditable research bundle, or only a BLOCKED manifest."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema_version": "t2130_research_bundle_v1",
        "strategy_id": result.get("strategy_id"),
        "decision_contract_id": result.get("decision_contract_id", DECISION_CONTRACT_ID),
        "status": result.get("status", "BLOCKED"),
        "formal": False,
        "capital_cny": 0.0,
        "artifacts": {},
    }
    if result.get("status") != "RESEARCH_COMPLETE":
        manifest["reason"] = result.get("reason", "formal_e3_not_available")
    else:
        for name in ("panel", "factors", "weights", "orders", "fills", "rejections", "nav", "candidate_returns", "capacity"):
            value = result.get(name)
            if not isinstance(value, pd.DataFrame):
                continue
            path = root / f"{name}.csv"
            value.to_csv(path, index=True if name == "candidate_returns" else False)
            manifest["artifacts"][name] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": len(value)}
        for name in ("selection", "statistics"):
            path = root / f"{name}.json"
            path.write_text(json.dumps(result[name], ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
            manifest["artifacts"][name] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


__all__ = ["DECISION_CONTRACT_ID", "CANDIDATES", "build_factor_panel", "nested_select", "run_t2130_pipeline", "write_research_bundle"]
