"""Pure research engine for trusted three-month champion rotation.

The engine consumes saved account-level NAV and evidence.  It never writes to
production tables, creates broker orders, or changes the production route.
Strategy selection is formed after signal-date close and takes effect on the
next available trading date.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


STRATEGY_ID = "trusted_champion_rotation_v1"
VALID_REGIMES = {
    "BROAD_TREND", "NARROW_MOMENTUM", "ROTATION", "NO_EDGE", "RISK_OFF", "FREEZE"
}


@dataclass(frozen=True)
class RotationConfig:
    raw: dict[str, Any]

    @property
    def strategy_ids(self) -> list[str]:
        return [str(item["strategy_id"]) for item in self.raw["strategy_pool"]]

    @property
    def fallback_strategy(self) -> str:
        return self.strategy_ids[0]

    @property
    def allowed_regimes(self) -> dict[str, set[str]]:
        return {
            str(item["strategy_id"]): {str(v) for v in item["allowed_regimes"]}
            for item in self.raw["strategy_pool"]
        }


def load_rotation_config(path: str | Path) -> RotationConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if raw.get("strategy", {}).get("strategy_id") != STRATEGY_ID:
        raise ValueError("strategy identity mismatch")
    if raw.get("strategy", {}).get("production_mutation_enabled") is not False:
        raise ValueError("research rotation must not mutate production")
    if raw.get("strategy", {}).get("order_generation_enabled") is not False:
        raise ValueError("research rotation must not generate orders")
    pool = raw.get("strategy_pool") or []
    ids = [str(item.get("strategy_id") or "") for item in pool]
    if len(ids) != 4 or len(set(ids)) != 4 or any(not value for value in ids):
        raise ValueError("strategy pool must contain four unique explicit strategies")
    for item in pool:
        regimes = set(item.get("allowed_regimes") or [])
        if not regimes or not regimes <= VALID_REGIMES:
            raise ValueError(f"invalid allowed regimes for {item.get('strategy_id')}")
    guards = raw.get("v1_1_guards") or {}
    if guards.get("frozen") is not True:
        raise ValueError("v1.1 guard parameters must be frozen before evaluation")
    return RotationConfig(raw)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def classify_market_regime(row: pd.Series | dict[str, Any]) -> str:
    explicit = str(row.get("market_state") or row.get("opportunity_structure") or "").upper()
    if explicit in VALID_REGIMES:
        return explicit
    amount = pd.to_numeric(pd.Series([row.get("market_amount_ratio_20")]), errors="coerce").iloc[0]
    index_bucket = str(row.get("index_bucket") or "")
    liquidity = str(row.get("market_liquidity_bucket") or "")
    bs_ratio = pd.to_numeric(pd.Series([row.get("market_bs_ratio")]), errors="coerce").iloc[0]
    if pd.notna(amount) and amount < 0.50 and (index_bucket == "index_weak" or liquidity == "low_liquidity"):
        return "FREEZE"
    if index_bucket == "index_weak" or liquidity == "low_liquidity" or (pd.notna(amount) and amount < 0.75):
        return "RISK_OFF"
    if index_bucket == "index_strong" and pd.notna(amount) and amount >= 1.20:
        return "BROAD_TREND"
    if index_bucket == "index_strong" and pd.notna(amount) and amount >= 1.05:
        return "NARROW_MOMENTUM"
    if pd.notna(amount) and amount >= 0.90 and pd.notna(bs_ratio) and bs_ratio >= 0.02:
        return "ROTATION"
    return "NO_EDGE"


def market_regime_confidence(row: pd.Series | dict[str, Any], regime: str) -> float:
    """Return a conservative, point-in-time confidence score in [0, 1]."""
    explicit = pd.to_numeric(pd.Series([row.get("market_state_confidence")]), errors="coerce").iloc[0]
    if pd.notna(explicit):
        return float(np.clip(explicit, 0.0, 1.0))
    amount = pd.to_numeric(pd.Series([row.get("market_amount_ratio_20")]), errors="coerce").iloc[0]
    index_bucket = str(row.get("index_bucket") or "")
    liquidity = str(row.get("market_liquidity_bucket") or "")
    evidence = int(pd.notna(amount)) + int(bool(index_bucket)) + int(bool(liquidity))
    base = evidence / 3.0
    if regime in {"BROAD_TREND", "FREEZE"} and pd.notna(amount):
        base += min(abs(float(amount) - 1.0), 0.30)
    return float(np.clip(base, 0.0, 1.0))


def build_earnings_density(
    trade_dates: pd.Series | list[Any],
    announcements: pd.DataFrame,
    universe: pd.DataFrame,
    lookback_trade_days: int = 5,
) -> pd.DataFrame:
    """Build a point-in-time earnings density feature from ann_date only."""
    dates = pd.DatetimeIndex(pd.to_datetime(pd.Series(trade_dates), errors="coerce").dropna().unique()).sort_values()
    required_ann = {"symbol", "ann_date"}
    if not required_ann <= set(announcements.columns):
        raise ValueError("earnings announcements require symbol, ann_date")
    ann = announcements.copy()
    ann["ann_date"] = pd.to_datetime(ann["ann_date"], errors="coerce")
    ann["symbol"] = ann["symbol"].astype(str).str.extract(r"(\d{6})", expand=False).fillna(ann["symbol"].astype(str))
    if "eligible_universe_count" in universe.columns:
        counts = universe.copy()
        counts["trade_date"] = pd.to_datetime(counts["trade_date"], errors="coerce")
        denominator = counts.groupby("trade_date")["eligible_universe_count"].max()
    elif {"trade_date", "symbol"} <= set(universe.columns):
        counts = universe.copy()
        counts["trade_date"] = pd.to_datetime(counts["trade_date"], errors="coerce")
        denominator = counts.groupby("trade_date")["symbol"].nunique()
    else:
        raise ValueError("eligible universe requires trade_date and symbol or eligible_universe_count")
    rows: list[dict[str, Any]] = []
    for idx, trade_date in enumerate(dates):
        start = dates[max(0, idx - int(lookback_trade_days) + 1)]
        visible = ann[ann["ann_date"].between(start, trade_date, inclusive="both")]
        denom = pd.to_numeric(pd.Series([denominator.get(trade_date, np.nan)]), errors="coerce").iloc[0]
        data_status = "PASS" if pd.notna(denom) and float(denom) > 0 else "BLOCKED_MISSING_UNIVERSE"
        density = float(visible["symbol"].nunique() / denom) if data_status == "PASS" else np.nan
        rows.append({
            "trade_date": trade_date,
            "earnings_announcement_count_5d": int(visible["symbol"].nunique()),
            "eligible_universe_count": float(denom) if pd.notna(denom) else np.nan,
            "earnings_announcement_density": density,
            "earnings_data_status": data_status,
            "earnings_max_ann_date_used": visible["ann_date"].max() if not visible.empty else pd.NaT,
        })
    return pd.DataFrame(rows)


def build_exposure_evidence(positions: pd.DataFrame, strategy_ids: list[str]) -> pd.DataFrame:
    required = {"strategy", "trade_date", "symbol", "industry", "weight"}
    if not required <= set(positions.columns):
        raise ValueError(f"positions missing columns: {sorted(required - set(positions.columns))}")
    frame = positions[positions["strategy"].astype(str).isin(strategy_ids)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce").fillna(0.0)
    grouped = frame.groupby(["strategy", "trade_date"], sort=False)
    max_single = grouped["weight"].max().rename("max_single_position_weight")
    industry = (
        frame.groupby(["strategy", "trade_date", frame["industry"].fillna("UNKNOWN")])["weight"]
        .sum().groupby(level=[0, 1]).max().rename("max_industry_weight")
    )
    return pd.concat([max_single, industry], axis=1).reset_index()


def build_execution_hard_block_evidence(candidates: pd.DataFrame) -> pd.DataFrame:
    """Conservatively classify selected-strategy execution proxy hard blocks."""
    required = {"strategy", "signal_date"}
    if candidates.empty or not required <= set(candidates.columns):
        return pd.DataFrame(columns=["strategy", "signal_date", "execution_evidence_status", "hard_block"])
    frame = candidates.copy()
    frame["signal_date"] = pd.to_datetime(frame["signal_date"], errors="coerce")
    proxy_fields = (
        "open_gap_proxy", "limit_up_buy_ratio", "limit_down_sell_ratio", "estimated_turnover_impact"
    )
    for field in proxy_fields:
        frame[field] = pd.to_numeric(frame.get(field), errors="coerce")
    frame["proxy_complete"] = frame[list(proxy_fields)].notna().all(axis=1)
    frame["hard_block"] = (
        frame["open_gap_proxy"].abs().gt(0.05)
        | frame["limit_up_buy_ratio"].gt(0.20)
        | frame["limit_down_sell_ratio"].gt(0.20)
        | frame["estimated_turnover_impact"].gt(0.03)
    )
    grouped = frame.groupby(["strategy", "signal_date"], as_index=False).agg(
        execution_evidence_status=("proxy_complete", lambda values: "PASS" if bool(values.all()) else "MISSING"),
        hard_block=("hard_block", "max"),
    )
    grouped["hard_block"] = grouped["hard_block"].astype(int)
    return grouped


def _window_metrics(group: pd.DataFrame, as_of: pd.Timestamp, days: int) -> dict[str, float]:
    values = group[group["trade_date"].le(as_of)].tail(int(days))
    if len(values) < int(days):
        return {"count": len(values), "return": np.nan, "max_drawdown": np.nan, "calmar": np.nan, "win_rate": np.nan}
    equity = values["total_equity"].astype(float)
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    max_drawdown = float((equity / equity.cummax() - 1.0).min())
    annualized = float((1.0 + total_return) ** (252.0 / max(len(values) - 1, 1)) - 1.0) if total_return > -1 else -1.0
    calmar = float(annualized / abs(max_drawdown)) if max_drawdown < -1e-9 else (999.0 if annualized > 0 else 0.0)
    return {
        "count": len(values), "return": total_return, "max_drawdown": max_drawdown,
        "calmar": calmar, "win_rate": float((returns > 0).mean()) if not returns.empty else 0.0,
    }


def _last_trade_day_flags(dates: list[pd.Timestamp]) -> dict[pd.Timestamp, bool]:
    flags: dict[pd.Timestamp, bool] = {}
    for idx, date in enumerate(dates):
        if idx == len(dates) - 1:
            flags[date] = False
        else:
            flags[date] = date.isocalendar()[:2] != dates[idx + 1].isocalendar()[:2]
    return flags


def run_rotation_decisions(
    nav: pd.DataFrame,
    market_environment: pd.DataFrame,
    earnings_density: pd.DataFrame,
    exposure_evidence: pd.DataFrame,
    config: RotationConfig,
    *,
    policy: str = "full",
) -> pd.DataFrame:
    """Generate point-in-time daily decisions; no orders are created."""
    if policy not in {"full", "pure_63", "pure_126", "no_earnings"}:
        raise ValueError(f"unsupported policy: {policy}")
    required_nav = {"strategy", "trade_date", "total_equity"}
    if not required_nav <= set(nav.columns):
        raise ValueError(f"nav missing columns: {sorted(required_nav - set(nav.columns))}")
    frame = nav[nav["strategy"].astype(str).isin(config.strategy_ids)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["total_equity"] = pd.to_numeric(frame["total_equity"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "total_equity"])
    available = set(frame["strategy"].unique())
    missing = sorted(set(config.strategy_ids) - available)
    if missing:
        raise ValueError(f"exact strategy NAV missing: {missing}")
    common_dates = sorted(set.intersection(*[
        set(frame.loc[frame["strategy"].eq(strategy), "trade_date"]) for strategy in config.strategy_ids
    ]))
    if not common_dates:
        raise ValueError("no common strategy NAV dates")
    env = market_environment.copy()
    env["trade_date"] = pd.to_datetime(env["trade_date"], errors="coerce")
    env = env.drop_duplicates("trade_date", keep="last").set_index("trade_date")
    earnings = earnings_density.copy()
    earnings["trade_date"] = pd.to_datetime(earnings["trade_date"], errors="coerce")
    earnings = earnings.drop_duplicates("trade_date", keep="last").set_index("trade_date")
    exposure = exposure_evidence.copy()
    exposure["trade_date"] = pd.to_datetime(exposure["trade_date"], errors="coerce")
    exposure = exposure.set_index(["strategy", "trade_date"])
    grouped = {strategy: frame[frame["strategy"].eq(strategy)].sort_values("trade_date") for strategy in config.strategy_ids}
    ranking = config.raw["ranking"]
    risk = config.raw["portfolio_risk"]
    earnings_cfg = config.raw["earnings_season"]
    weekly_flags = _last_trade_day_flags(common_dates)
    incumbent = config.fallback_strategy
    incumbent_days = 0
    cooldown_remaining = 0
    regime_history: list[str] = []
    last_leader: str | None = None
    leader_streak = 0
    rows: list[dict[str, Any]] = []

    for idx, signal_date in enumerate(common_dates):
        execution_date = common_dates[idx + 1] if idx + 1 < len(common_dates) else pd.NaT
        env_row = env.loc[signal_date] if signal_date in env.index else pd.Series(dtype=object)
        regime = classify_market_regime(env_row)
        regime_confidence = market_regime_confidence(env_row, regime)
        regime_history.append(regime)
        guards = config.raw.get("v1_1_guards") or {}
        flip_lookback = int(guards.get("regime_flip_lookback_days", 5))
        recent_regimes = regime_history[-flip_lookback:]
        regime_flips = sum(a != b for a, b in zip(recent_regimes, recent_regimes[1:]))
        if regime_flips >= int(guards.get("regime_flip_threshold", 3)):
            cooldown_remaining = max(cooldown_remaining, int(guards.get("regime_cooldown_days", 5)))
        earn_row = earnings.loc[signal_date] if signal_date in earnings.index else pd.Series(dtype=object)
        earnings_status = str(earn_row.get("earnings_data_status") or "BLOCKED_MISSING_EARNINGS")
        density = pd.to_numeric(pd.Series([earn_row.get("earnings_announcement_density")]), errors="coerce").iloc[0]
        is_earnings = bool(pd.notna(density) and float(density) >= float(earnings_cfg["announcement_density_threshold"]))
        metrics: dict[str, dict[str, Any]] = {}
        preliminary: list[str] = []
        for strategy in config.strategy_ids:
            m63 = _window_metrics(grouped[strategy], signal_date, int(ranking["primary_window_days"]))
            m126 = _window_metrics(grouped[strategy], signal_date, int(ranking["confirmation_window_days"]))
            try:
                exp = exposure.loc[(strategy, signal_date)]
            except KeyError:
                exp = pd.Series(dtype=float)
            max_single = pd.to_numeric(pd.Series([exp.get("max_single_position_weight")]), errors="coerce").iloc[0]
            max_industry = pd.to_numeric(pd.Series([exp.get("max_industry_weight")]), errors="coerce").iloc[0]
            exposure_ok = pd.notna(max_single) and pd.notna(max_industry) and max_single <= float(risk["max_single_position_weight"]) and max_industry <= float(risk["max_industry_weight"])
            regime_ok = regime in config.allowed_regimes[strategy]
            confirmation_ok = (
                m126["count"] >= int(ranking["confirmation_window_days"])
                and pd.notna(m126["return"])
                and m126["return"] > float(ranking["confirmation_return_min"])
                and m126["max_drawdown"] >= float(ranking["confirmation_max_drawdown"])
            )
            if policy == "pure_63":
                confirmation_ok = m63["count"] >= int(ranking["primary_window_days"])
            if policy == "pure_126":
                confirmation_ok = m126["count"] >= int(ranking["confirmation_window_days"])
            metrics[strategy] = {
                "ret_63": m63["return"], "ret_126": m126["return"], "mdd_126": m126["max_drawdown"],
                "calmar_126": m126["calmar"], "win_rate_126": m126["win_rate"],
                "max_single": max_single, "max_industry": max_industry,
                "regime_ok": regime_ok, "exposure_ok": exposure_ok, "confirmation_ok": confirmation_ok,
            }
            if confirmation_ok and regime_ok and exposure_ok:
                preliminary.append(strategy)
        if policy not in {"pure_63", "pure_126"} and preliminary:
            calmars = [metrics[s]["calmar_126"] for s in preliminary if pd.notna(metrics[s]["calmar_126"])]
            calmar_floor = float(np.quantile(calmars, float(ranking["confirmation_calmar_percentile"]))) if calmars else np.inf
            eligible = [s for s in preliminary if pd.notna(metrics[s]["calmar_126"]) and metrics[s]["calmar_126"] >= calmar_floor]
        else:
            calmar_floor = np.nan
            eligible = preliminary
        rank_key = "ret_126" if policy == "pure_126" else "ret_63"
        eligible = sorted(eligible, key=lambda s: (metrics[s][rank_key], s), reverse=True)
        no_eligible = not eligible
        leader = eligible[0] if eligible else config.fallback_strategy
        if leader == last_leader:
            leader_streak += 1
        else:
            last_leader, leader_streak = leader, 1
        incumbent_ret = metrics.get(incumbent, {}).get("ret_63", np.nan)
        leader_ret = metrics.get(leader, {}).get("ret_63", np.nan)
        advantage = float(leader_ret - incumbent_ret) if pd.notna(leader_ret) and pd.notna(incumbent_ret) else np.nan
        apply_earnings_gate = policy == "full" and is_earnings
        required_margin = float(ranking["earnings_advantage_margin"] if apply_earnings_gate else ranking["normal_advantage_margin"])
        required_streak = int(ranking["earnings_confirmation_days"] if apply_earnings_gate else ranking["normal_confirmation_days"])
        if policy in {"pure_63", "pure_126"}:
            required_margin, required_streak = 0.0, 1
        estimated_switch_hurdle = 0.0
        if policy == "full":
            execution = config.raw["execution"]
            switch_ratio = float(risk["switch_notional_ratio"])
            estimated_switch_hurdle = (
                2.0 * (float(execution["base_cost_rate"]) + float(execution["base_slippage_bps"]) / 10_000.0)
                * switch_ratio
                + float(guards.get("estimated_round_trip_impact", 0.0))
                + float(guards.get("cost_safety_buffer", 0.0))
            )
            required_margin = max(required_margin, estimated_switch_hurdle)
            if regime_confidence < float(guards.get("low_regime_confidence_threshold", 0.60)):
                required_margin += float(guards.get("low_confidence_extra_margin", 0.02))
        switch = False
        reason = "hold_incumbent"
        target_exposure_ratio = 1.0
        incumbent_mdd = metrics.get(incumbent, {}).get("mdd_126", np.nan)
        incumbent_63 = metrics.get(incumbent, {}).get("ret_63", np.nan)
        if pd.notna(incumbent_mdd) and incumbent_mdd <= float(guards.get("reduce_exposure_drawdown", -0.15)):
            target_exposure_ratio = float(guards.get("reduced_exposure_ratio", 0.50))
        forced_exit = bool(
            policy == "full" and pd.notna(incumbent_mdd)
            and incumbent_mdd <= float(guards.get("exit_drawdown", -0.20))
            and incumbent != config.fallback_strategy
        )
        block_new_risk = bool(
            policy == "full" and guards.get("negative_63_return_blocks_new_risk", True)
            and leader != config.fallback_strategy and pd.notna(leader_ret) and leader_ret < 0
        )
        data_blocked = bool(earnings_cfg.get("fail_closed_on_missing_data", True) and earnings_status != "PASS" and policy != "no_earnings")
        if forced_exit and not pd.isna(execution_date):
            incumbent = config.fallback_strategy
            incumbent_days = 0
            switch = True
            target_exposure_ratio = 0.0
            reason = "forced_exit_126_drawdown"
        elif data_blocked:
            reason = "blocked_earnings_data"
        elif cooldown_remaining > 0 and leader != incumbent:
            reason = "blocked_regime_cooldown"
        elif block_new_risk and leader != incumbent:
            reason = "blocked_negative_63_return"
        elif leader == incumbent:
            reason = "leader_is_incumbent"
        elif not weekly_flags[signal_date]:
            reason = "blocked_not_week_end"
        elif incumbent_days < int(ranking["minimum_hold_days"]):
            reason = "blocked_minimum_hold"
        elif not no_eligible and leader_streak < required_streak:
            reason = "blocked_confirmation_streak"
        elif not no_eligible and (pd.isna(advantage) or advantage + float(ranking["tie_tolerance"]) < required_margin):
            reason = "blocked_advantage_margin"
        elif pd.isna(execution_date):
            reason = "blocked_no_execution_date"
        else:
            incumbent = leader
            incumbent_days = 0
            switch = True
            reason = "weekly_fallback_switch" if no_eligible else "weekly_champion_switch"
        selected = incumbent
        decision: dict[str, Any] = {
            "strategy": STRATEGY_ID, "policy": policy, "signal_date": signal_date,
            "execution_date": execution_date, "market_regime": regime,
            "market_regime_confidence": regime_confidence, "regime_flips_5d": regime_flips,
            "regime_cooldown_remaining": cooldown_remaining,
            "earnings_announcement_density": density, "earnings_season": int(is_earnings),
            "earnings_data_status": earnings_status, "leader_strategy": leader,
            "selected_strategy": selected, "eligible_strategies": "|".join(eligible),
            "leader_streak": leader_streak, "required_confirmation_days": required_streak,
            "leader_advantage_63": advantage, "required_advantage_margin": required_margin,
            "estimated_switch_hurdle": estimated_switch_hurdle,
            "incumbent_return_63": incumbent_63, "incumbent_drawdown_126": incumbent_mdd,
            "target_exposure_ratio": target_exposure_ratio,
            "calmar_126_floor": calmar_floor, "switch_planned": int(switch),
            "switch_reason": reason, "incumbent_days_after": incumbent_days,
            "completed_history_rule": "account_nav_asof_signal_close; execution_next_trade_date",
            "order_generation_enabled": 0, "production_mutation_enabled": 0,
        }
        for strategy in config.strategy_ids:
            prefix = strategy.replace("baseline_full_", "bfl_").replace("tiered_liquidity_then_", "tiered_")
            for key, value in metrics[strategy].items():
                decision[f"{prefix}_{key}"] = value
        rows.append(decision)
        incumbent_days += 1
        cooldown_remaining = max(0, cooldown_remaining - 1)
    return pd.DataFrame(rows)


def build_rotation_nav(
    nav: pd.DataFrame,
    decisions: pd.DataFrame,
    config: RotationConfig,
) -> pd.DataFrame:
    frame = nav[nav["strategy"].astype(str).isin(config.strategy_ids)].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["total_equity"] = pd.to_numeric(frame["total_equity"], errors="coerce")
    frame = frame.sort_values(["strategy", "trade_date"])
    frame["underlying_daily_return"] = frame.groupby("strategy")["total_equity"].pct_change().fillna(0.0)
    returns = frame.set_index(["strategy", "trade_date"])["underlying_daily_return"]
    execution = decisions.dropna(subset=["execution_date"]).copy()
    execution["execution_date"] = pd.to_datetime(execution["execution_date"])
    execution = execution.drop_duplicates("execution_date", keep="last").set_index("execution_date")
    dates = sorted(frame["trade_date"].unique())
    cost_rate = float(config.raw["execution"]["base_cost_rate"])
    slippage = float(config.raw["execution"]["base_slippage_bps"]) / 10_000.0
    switch_ratio = float(config.raw["portfolio_risk"]["switch_notional_ratio"])
    equity = 1.0
    rows: list[dict[str, Any]] = []
    previous_selected: str | None = None
    for date in dates:
        if date not in execution.index:
            continue
        item = execution.loc[date]
        selected = str(item["selected_strategy"])
        underlying_return = float(returns.get((selected, date), 0.0))
        exposure_ratio = float(item.get("target_exposure_ratio", 1.0))
        exposure_ratio = float(np.clip(exposure_ratio, 0.0, 1.0))
        switched = previous_selected is not None and selected != previous_selected
        extra_switch_cost = 2.0 * (cost_rate + slippage) * switch_ratio if switched else 0.0
        net_return = (1.0 + underlying_return * exposure_ratio) * (1.0 - extra_switch_cost) - 1.0
        equity *= 1.0 + net_return
        rows.append({
            "strategy": STRATEGY_ID, "trade_date": date, "signal_date": item["signal_date"],
            "selected_strategy": selected, "underlying_daily_return": underlying_return,
            "target_exposure_ratio": exposure_ratio,
            "extra_switch_cost": extra_switch_cost, "daily_return": net_return,
            "equity": equity, "nav": equity, "switch_executed": int(switched),
            "evidence_status": "RESEARCH_PROXY_LEDGER_REQUIRED",
        })
        previous_selected = selected
    return pd.DataFrame(rows)


def performance_metrics(nav: pd.DataFrame) -> dict[str, Any]:
    if nav.empty or len(nav) < 2:
        return {"trading_days": len(nav), "total_return": 0.0, "annualized_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "calmar": 0.0}
    equity = nav["equity"].astype(float)
    daily = equity.pct_change().dropna()
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    annualized = float((1.0 + total) ** (252.0 / max(len(equity) - 1, 1)) - 1.0) if total > -1 else -1.0
    mdd = float((equity / equity.cummax() - 1.0).min())
    vol = float(daily.std(ddof=0) * np.sqrt(252.0)) if not daily.empty else 0.0
    sharpe = float(daily.mean() / daily.std(ddof=0) * np.sqrt(252.0)) if not daily.empty and daily.std(ddof=0) > 0 else 0.0
    return {
        "trading_days": int(len(equity)), "total_return": total, "annualized_return": annualized,
        "max_drawdown": mdd, "annualized_volatility": vol, "sharpe": sharpe,
        "calmar": float(annualized / abs(mdd)) if mdd < -1e-9 else 0.0,
        "daily_win_rate": float((daily > 0).mean()) if not daily.empty else 0.0,
        "worst_day": float(daily.min()) if not daily.empty else 0.0,
        "switch_count": int(nav["switch_executed"].sum()),
        "total_extra_switch_cost": float(nav["extra_switch_cost"].sum()),
    }


def build_disabled_shadow_status(
    decisions: pd.DataFrame,
    rotation_nav: pd.DataFrame,
    config: RotationConfig,
    *,
    corporate_action_coverage: float | None,
    strict_ledger_status: str | None,
    t_plus_one_violations: int | None,
    order_conservation_errors: int | None,
    observation_start: str | None = None,
    execution_evidence: pd.DataFrame | None = None,
) -> dict[str, Any]:
    required_days = int(config.raw["acceptance"]["disabled_shadow_days"])
    required_switches = int(config.raw["acceptance"].get("disabled_shadow_min_switches", 2))
    observed = decisions.copy()
    observed["signal_date"] = pd.to_datetime(observed["signal_date"], errors="coerce")
    if observation_start:
        observed = observed[observed["signal_date"].ge(pd.Timestamp(observation_start))]
    else:
        # Historical backtest dates never count as real disabled-shadow days.
        observed = observed.iloc[0:0]
    tail = observed.tail(required_days)
    execution_complete = False
    hard_block_days: int | None = None
    if execution_evidence is not None and not execution_evidence.empty and not tail.empty:
        evidence = execution_evidence.copy()
        evidence["signal_date"] = pd.to_datetime(evidence["signal_date"], errors="coerce")
        merged = tail.merge(
            evidence, left_on=["selected_strategy", "signal_date"],
            right_on=["strategy", "signal_date"], how="left",
        )
        execution_complete = merged["execution_evidence_status"].eq("PASS").all()
        hard_block_days = int(pd.to_numeric(merged["hard_block"], errors="coerce").fillna(1).gt(0).sum())
    data_complete = (
        len(tail) >= required_days and tail["earnings_data_status"].eq("PASS").all()
        and execution_complete
    )
    strict_pass = (
        strict_ledger_status == "VERIFIED"
        and corporate_action_coverage is not None and corporate_action_coverage >= 1.0
        and t_plus_one_violations == 0 and order_conservation_errors == 0
    )
    observed_switches = int(pd.to_numeric(tail.get("switch_planned"), errors="coerce").fillna(0).sum()) if not tail.empty else 0
    enough_switches = observed_switches >= required_switches
    return {
        "strategy_id": STRATEGY_ID,
        "lane": "SHADOW_DISABLED_RESEARCH_ONLY",
        "observed_trade_days": int(len(tail)),
        "required_trade_days": required_days,
        "observed_switches": observed_switches,
        "required_switches": required_switches,
        "data_complete": bool(data_complete),
        "observation_start": observation_start,
        "execution_evidence_complete": execution_complete,
        "incremental_hard_block_days": hard_block_days,
        "strict_ledger_status": strict_ledger_status or "MISSING",
        "corporate_action_coverage": corporate_action_coverage,
        "t_plus_one_violations": t_plus_one_violations,
        "order_conservation_errors": order_conservation_errors,
        "research_nav_available": bool(not rotation_nav.empty),
        "promotion_ready": bool(data_complete and hard_block_days == 0 and strict_pass and enough_switches),
        "blockers": [
            reason for condition, reason in (
                (data_complete, "disabled_shadow_20d_data_incomplete"),
                (execution_complete, "execution_proxy_evidence_incomplete"),
                (hard_block_days == 0, "incremental_execution_hard_block"),
                (strict_pass, "strict_ledger_or_corporate_action_unverified"),
                (enough_switches, "disabled_shadow_switches_insufficient"),
            ) if not condition
        ],
        "order_generation_enabled": False,
        "production_mutation_enabled": False,
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
