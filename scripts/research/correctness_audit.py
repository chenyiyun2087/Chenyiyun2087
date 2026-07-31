"""Deterministic correctness checks for Alpha validation artifacts.

Replay proves repeatability.  These checks independently verify bounded
samples and invariants without authorizing trading or capital.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from runtime.acceptance_config import canonical_sha


def _sample_dates(
    nav: pd.DataFrame, size: int, seed: int
) -> tuple[list[str], dict[str, int]]:
    if "trade_date" not in nav.columns:
        return [], {}
    frame = nav.copy()
    frame["_audit_date"] = pd.to_datetime(
        frame["trade_date"], errors="coerce"
    ).dt.date.astype(str)
    frame = frame[frame["_audit_date"].ne("NaT")].drop_duplicates(
        "_audit_date", keep="last"
    )
    frame = frame.sort_values("_audit_date", kind="mergesort")
    dates = frame["_audit_date"].tolist()
    if len(dates) <= size:
        return dates, {"all_available_dates": len(dates)}

    if "daily_return" in frame.columns:
        returns = pd.to_numeric(frame["daily_return"], errors="coerce")
    elif "nav" in frame.columns:
        returns = pd.to_numeric(frame["nav"], errors="coerce").pct_change()
    else:
        returns = pd.Series(np.nan, index=frame.index)
    ranked = pd.DataFrame(
        {"trade_date": dates, "daily_return": returns.to_numpy()}
    ).fillna({"daily_return": 0.0})

    quota = max(1, size // 5)
    selected: set[str] = set()
    strata: dict[str, list[str]] = {}

    def take(name: str, candidates: list[str], count: int) -> None:
        chosen = [value for value in candidates if value not in selected][:count]
        selected.update(chosen)
        strata[name] = chosen

    take(
        "bear_tail",
        ranked.sort_values(
            ["daily_return", "trade_date"], kind="mergesort"
        )["trade_date"].tolist(),
        quota,
    )
    take(
        "rally_tail",
        ranked.sort_values(
            ["daily_return", "trade_date"],
            ascending=[False, True],
            kind="mergesort",
        )["trade_date"].tolist(),
        quota,
    )
    ranked["absolute_return"] = ranked["daily_return"].abs()
    take(
        "extreme_volatility",
        ranked.sort_values(
            ["absolute_return", "trade_date"],
            ascending=[False, True],
            kind="mergesort",
        )["trade_date"].tolist(),
        quota,
    )
    boundary_candidates = (
        frame.assign(
            _month=pd.to_datetime(frame["_audit_date"]).dt.to_period("M")
        )
        .groupby("_month", sort=True)["_audit_date"]
        .agg(["first", "last"])
        .to_numpy()
        .reshape(-1)
        .tolist()
    )
    take("calendar_boundaries", boundary_candidates, quota)
    rng = np.random.default_rng(seed)
    remaining = [value for value in dates if value not in selected]
    random_count = size - len(selected)
    random_dates = sorted(
        rng.choice(remaining, size=random_count, replace=False).tolist()
    )
    take("random_control", random_dates, random_count)
    return sorted(selected), {
        name: len(values) for name, values in strata.items()
    }


def build_research_correctness_report(
    nav: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    sample_size: int,
    seed: int,
    research_production_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit a deterministic sample and fail closed on missing audit fields."""
    blockers: list[str] = []
    violations: list[dict[str, Any]] = []
    dates, strata = _sample_dates(nav, sample_size, seed)
    if not dates:
        blockers.append("correctness_nav_dates_missing")

    scoped_nav = nav.copy()
    if "trade_date" in scoped_nav:
        scoped_nav["_audit_date"] = pd.to_datetime(
            scoped_nav["trade_date"], errors="coerce"
        ).dt.date.astype(str)
        scoped_nav = scoped_nav[scoped_nav["_audit_date"].isin(dates)]
    required_nav = {"nav"}
    for column in sorted(required_nav.difference(scoped_nav.columns)):
        blockers.append(f"correctness_nav_column_missing:{column}")
    if "nav" in scoped_nav:
        invalid = pd.to_numeric(scoped_nav["nav"], errors="coerce")
        if invalid.isna().any() or (invalid <= 0).any():
            violations.append({"invariant": "positive_finite_nav", "count": int((invalid.isna() | (invalid <= 0)).sum())})

    required_trade = {
        "signal_time",
        "execute_time",
        "symbol",
        "side",
        "price",
        "fill_status",
        "limit_status",
        "is_st",
        "is_suspended",
        "is_delisted",
        "financial_available_at",
    }
    for column in sorted(required_trade.difference(trades.columns)):
        blockers.append(f"correctness_trade_column_missing:{column}")
    if {"signal_time", "execute_time"}.issubset(trades.columns):
        signal = pd.to_datetime(trades["signal_time"], errors="coerce", utc=True)
        execute = pd.to_datetime(trades["execute_time"], errors="coerce", utc=True)
        bad = signal.isna() | execute.isna() | (execute <= signal)
        if bad.any():
            violations.append({"invariant": "execution_after_signal", "count": int(bad.sum())})
    if {"signal_time", "financial_available_at"}.issubset(trades.columns):
        signal = pd.to_datetime(trades["signal_time"], errors="coerce", utc=True)
        available = pd.to_datetime(
            trades["financial_available_at"], errors="coerce", utc=True
        )
        bad = available.isna() | (available > signal)
        if bad.any():
            violations.append({"invariant": "financial_available_by_signal", "count": int(bad.sum())})
    if "price" in trades:
        price = pd.to_numeric(trades["price"], errors="coerce")
        bad = price.isna() | (price <= 0)
        if bad.any():
            violations.append({"invariant": "positive_fill_price", "count": int(bad.sum())})
    if {"fill_status", "is_suspended"}.issubset(trades.columns):
        bad = trades["is_suspended"].astype(bool) & trades["fill_status"].astype(str).str.upper().eq("FILLED")
        if bad.any():
            violations.append({"invariant": "no_suspended_fill", "count": int(bad.sum())})
    if {"fill_status", "is_delisted"}.issubset(trades.columns):
        bad = trades["is_delisted"].astype(bool) & trades["fill_status"].astype(str).str.upper().eq("FILLED")
        if bad.any():
            violations.append({"invariant": "no_delisted_fill", "count": int(bad.sum())})
    if {"fill_status", "limit_status", "side"}.issubset(trades.columns):
        side = trades["side"].astype(str).str.upper()
        limit = trades["limit_status"].astype(str).str.upper()
        filled = trades["fill_status"].astype(str).str.upper().eq("FILLED")
        bad = filled & (((side == "BUY") & (limit == "LIMIT_UP")) | ((side == "SELL") & (limit == "LIMIT_DOWN")))
        if bad.any():
            violations.append({"invariant": "no_impossible_limit_fill", "count": int(bad.sum())})

    contract = research_production_contract or {}
    contract_ok = (
        str(contract.get("status") or "").upper() == "PASS"
        and bool(contract.get("research_signal_sha256"))
        and contract.get("research_signal_sha256") == contract.get("production_signal_sha256")
    )
    if not contract_ok:
        blockers.append("research_production_contract_missing_or_mismatch")
    if violations:
        blockers.append("correctness_invariant_violation")
    sample = {
        "sampling_policy": "DETERMINISTIC_STRATIFIED",
        "seed": seed,
        "requested_size": sample_size,
        "actual_size": len(dates),
        "strata": strata,
        "trade_dates": dates,
    }
    return {
        "schema_version": "alpha_v3_9_research_correctness_v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "promotion_eligible": False,
        "blockers": sorted(set(blockers)),
        "sample": sample,
        "sample_sha256": canonical_sha(sample),
        "invariant_violations": violations,
        "research_production_contract": {
            "status": "PASS" if contract_ok else "BLOCKED",
            "research_signal_sha256": contract.get("research_signal_sha256"),
            "production_signal_sha256": contract.get("production_signal_sha256"),
        },
    }


def build_correctness_gap_report(
    correctness: dict[str, Any],
    promotion: dict[str, Any],
) -> dict[str, Any]:
    """Translate fail-closed blockers into deterministic remediation work."""
    gaps: list[dict[str, Any]] = []
    for blocker in sorted(set(correctness.get("blockers") or [])):
        if blocker.startswith("correctness_trade_column_missing:"):
            field = blocker.split(":", 1)[1]
            action = "add_trade_lifecycle_field"
            source_node = "TRADE_LIFECYCLE_FIELDS"
        elif blocker.startswith("correctness_nav_column_missing:"):
            field = blocker.split(":", 1)[1]
            action = "add_nav_audit_field"
            source_node = "NAV_AUDIT_FIELDS"
        elif blocker == "research_production_contract_missing_or_mismatch":
            field = "research_production_signal_sha256"
            action = "publish_release_scoped_signal_contract"
            source_node = "RESEARCH_PRODUCTION_SIGNAL_CONTRACT"
        elif blocker == "correctness_invariant_violation":
            field = "correctness_invariants"
            action = "repair_source_rows_and_regenerate_evidence"
            source_node = "CORRECTNESS_INVARIANTS"
        else:
            field = blocker
            action = "inspect_and_supply_correctness_evidence"
            source_node = "CORRECTNESS_EVIDENCE"
        gap_id = "RC-" + canonical_sha(
            {
                "gate": "research_correctness",
                "blocker": blocker,
                "missing_field": field,
            }
        )[:12].upper()
        gaps.append(
            {
                "gap_id": gap_id,
                "gate": "research_correctness",
                "blocker": blocker,
                "missing_field": field,
                "severity": "P1",
                "owner": "RESEARCH_PLATFORM",
                "fix_action": action,
                "recommended_action": action,
                "verification": {
                    "required_gate_status": "PASS",
                    "require_release_scoped_evidence": True,
                    "require_evidence_sha256": True,
                },
                "verification_status": "NOT_RUN",
                "fix_commit": None,
                "verification_run": None,
                "resolved_at": None,
                "source_node": source_node,
            }
        )

    correctness_blocked = str(correctness.get("status")) != "PASS"
    return {
        "schema_version": "alpha_v3_9_research_gap_v1",
        "status": "BLOCKED" if correctness_blocked else "PASS",
        "promotion_eligible": False,
        "capital_authority": False,
        "missing_fields": sorted(
            {
                str(row["missing_field"])
                for row in gaps
                if row.get("missing_field")
            }
        ),
        "gaps": gaps,
        "blocking_gates": list(promotion.get("blocking_gates") or []),
        "recommended_action_count": len(gaps),
    }


def build_correctness_synthetic_suite(
    *,
    sample_size: int,
    seed: int,
) -> dict[str, Any]:
    """Run deterministic market-rule fixtures without claiming live evidence."""
    nav = pd.DataFrame(
        {
            "trade_date": pd.date_range("2025-01-02", periods=140, freq="B"),
            "nav": np.linspace(1.0, 1.2, 140),
        }
    )
    base = {
        "signal_time": "2026-01-05T16:00:00+08:00",
        "execute_time": "2026-01-06T09:30:00+08:00",
        "financial_available_at": "2026-01-05T15:00:00+08:00",
        "symbol": "000001.SZ",
        "side": "BUY",
        "price": 10.0,
        "fill_status": "FILLED",
        "limit_status": "NORMAL",
        "is_st": False,
        "is_suspended": False,
        "is_delisted": False,
    }
    contract = {
        "status": "PASS",
        "research_signal_sha256": "a" * 64,
        "production_signal_sha256": "a" * 64,
    }
    scenarios = [
        ("baseline", {}, "PASS", None),
        ("st_explicitly_classified", {"is_st": True}, "PASS", None),
        (
            "financial_delay",
            {"financial_available_at": "2026-01-06T08:00:00+08:00"},
            "BLOCKED",
            "financial_available_by_signal",
        ),
        (
            "same_day_execution",
            {"execute_time": "2026-01-05T16:00:00+08:00"},
            "BLOCKED",
            "execution_after_signal",
        ),
        (
            "suspended_fill",
            {"is_suspended": True},
            "BLOCKED",
            "no_suspended_fill",
        ),
        (
            "delisted_fill",
            {"is_delisted": True},
            "BLOCKED",
            "no_delisted_fill",
        ),
        (
            "limit_up_buy_fill",
            {"limit_status": "LIMIT_UP"},
            "BLOCKED",
            "no_impossible_limit_fill",
        ),
        (
            "limit_down_sell_fill",
            {"side": "SELL", "limit_status": "LIMIT_DOWN"},
            "BLOCKED",
            "no_impossible_limit_fill",
        ),
        (
            "nonpositive_price",
            {"price": 0.0},
            "BLOCKED",
            "positive_fill_price",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for name, overrides, expected_status, expected_invariant in scenarios:
        report = build_research_correctness_report(
            nav,
            pd.DataFrame([{**base, **overrides}]),
            sample_size=sample_size,
            seed=seed,
            research_production_contract=contract,
        )
        invariants = {
            str(row.get("invariant"))
            for row in report.get("invariant_violations", [])
        }
        detected = (
            report["status"] == expected_status
            and (
                expected_invariant is None
                or expected_invariant in invariants
            )
        )
        rows.append(
            {
                "scenario": name,
                "expected_status": expected_status,
                "observed_status": report["status"],
                "expected_invariant": expected_invariant,
                "status": "PASS" if detected else "FAIL",
            }
        )
    passed = all(row["status"] == "PASS" for row in rows)
    return {
        "schema_version": "alpha_v3_9_correctness_synthetic_suite_v1",
        "status": "PASS" if passed else "BLOCKED",
        "promotion_eligible": False,
        "evidence_layer": "SYNTHETIC_CI",
        "capital_authority": False,
        "scenario_count": len(rows),
        "passed_scenario_count": sum(
            row["status"] == "PASS" for row in rows
        ),
        "blockers": [] if passed else [
            f"synthetic_scenario_failed:{row['scenario']}"
            for row in rows
            if row["status"] != "PASS"
        ],
        "scenarios": rows,
    }
