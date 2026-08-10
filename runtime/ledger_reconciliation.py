"""Cross-ledger comparison with explicit, non-negotiable tolerances."""

from __future__ import annotations

from typing import Any

import pandas as pd

from runtime.contracts import LedgerReconciliationReport, ReconciliationDifference
from runtime.canonical_execution_contract import CANONICAL_KERNEL_ID, CANONICAL_KERNEL_VERSION


def reconcile_ledgers(
    *,
    release_id: str,
    run_id: str,
    primary_trades: pd.DataFrame,
    oracle_trades: pd.DataFrame,
    primary_positions: pd.DataFrame,
    oracle_positions: pd.DataFrame,
    primary_nav: pd.DataFrame,
    oracle_nav: pd.DataFrame,
    primary_metrics: dict[str, float],
    oracle_metrics: dict[str, float],
    primary_rejections: pd.DataFrame | None = None,
    oracle_rejections: pd.DataFrame | None = None,
    cash_tolerance_cny: float = 0.01,
    nav_tolerance_bps: float = 1.0,
) -> LedgerReconciliationReport:
    differences: list[ReconciliationDifference] = []
    first_dates: list[str] = []

    trade_columns = ["order_id", "symbol", "side", "filled_shares", "filled_price", "filled_notional", "fee"]
    for frame, name in ((primary_trades, "primary"), (oracle_trades, "oracle")):
        missing = sorted(set(trade_columns) - set(frame.columns))
        if missing:
            raise ValueError(f"{name}_trades_missing:{','.join(missing)}")
        if frame["order_id"].astype(str).duplicated().any():
            raise ValueError(f"{name}_duplicate_order_id")
        # Trusted frames must identify the economic kernel.  Empty frames are
        # allowed (e.g. an all-rejected strategy) but non-empty legacy frames
        # are diagnostic only and cannot silently pass formal reconciliation.
        if not frame.empty and "canonical_kernel_id" in frame.columns:
            bad = frame[frame["canonical_kernel_id"].astype(str) != CANONICAL_KERNEL_ID]
            if not bad.empty:
                differences.append(ReconciliationDifference(
                    scope="ORDER", key=f"{name}:kernel_id", primary_value=name,
                    oracle_value=CANONICAL_KERNEL_ID, classification="KERNEL_ID_MISMATCH",
                    detail="trusted ledger frame is not produced by canonical kernel",
                ))
        if not frame.empty and "canonical_kernel_version" in frame.columns:
            bad = frame[frame["canonical_kernel_version"].astype(str) != CANONICAL_KERNEL_VERSION]
            if not bad.empty:
                differences.append(ReconciliationDifference(
                    scope="ORDER", key=f"{name}:kernel_version", primary_value=name,
                    oracle_value=CANONICAL_KERNEL_VERSION, classification="KERNEL_VERSION_MISMATCH",
                    detail="trusted ledger frame uses a different kernel version",
                ))
    primary_by_order = primary_trades.set_index("order_id", drop=False)
    oracle_by_order = oracle_trades.set_index("order_id", drop=False)
    for order_id in sorted(set(primary_by_order.index) | set(oracle_by_order.index)):
        if order_id not in primary_by_order.index or order_id not in oracle_by_order.index:
            present = primary_by_order.loc[order_id] if order_id in primary_by_order.index else oracle_by_order.loc[order_id]
            if "trade_date" in present:
                first_dates.append(str(present["trade_date"]))
            differences.append(ReconciliationDifference(
                scope="ORDER", key=str(order_id),
                primary_value="MISSING" if order_id not in primary_by_order.index else "PRESENT",
                oracle_value="MISSING" if order_id not in oracle_by_order.index else "PRESENT",
                classification="ORDER_SET_MISMATCH", detail="order exists in only one ledger",
            ))
            continue
        left, right = primary_by_order.loc[order_id], oracle_by_order.loc[order_id]
        for column in trade_columns[1:]:
            lv, rv = left[column], right[column]
            tolerance = 0.01 if column == "fee" else 0.005 if column == "filled_price" else 0.0
            numeric = column in {"filled_shares", "filled_price", "filled_notional", "fee"}
            mismatch = abs(float(lv) - float(rv)) > tolerance if numeric else str(lv) != str(rv)
            if mismatch:
                differences.append(ReconciliationDifference(
                    scope="ORDER", key=f"{order_id}:{column}", primary_value=lv,
                    oracle_value=rv, difference=(float(lv) - float(rv)) if numeric else None,
                    classification="ORDER_ECONOMICS_MISMATCH", detail=f"order field {column} differs",
                ))
        # Expanded cost components are part of the economic identity.  The
        # legacy ``fee`` field is compared above for old packages; when a
        # canonical costs mapping is available compare every component too.
        if "costs" in left and "costs" in right:
            def _cost_mapping(value: object) -> dict[str, float]:
                if isinstance(value, dict):
                    return {str(k): float(v) for k, v in value.items()}
                if isinstance(value, str):
                    try:
                        import json
                        parsed = json.loads(value)
                        return {str(k): float(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
                    except Exception:
                        return {}
                return {}
            left_costs, right_costs = _cost_mapping(left["costs"]), _cost_mapping(right["costs"])
            for cost_name in sorted(set(left_costs) | set(right_costs)):
                lv, rv = left_costs.get(cost_name, 0.0), right_costs.get(cost_name, 0.0)
                if abs(lv - rv) > 0.01 + 1e-12:
                    differences.append(ReconciliationDifference(
                        scope="ORDER", key=f"{order_id}:cost:{cost_name}", primary_value=lv,
                        oracle_value=rv, difference=lv - rv,
                        classification="COST_COMPONENT_MISMATCH", detail="canonical cost component differs",
                    ))

    if primary_rejections is not None or oracle_rejections is not None:
        left_rejections = primary_rejections if primary_rejections is not None else pd.DataFrame()
        right_rejections = oracle_rejections if oracle_rejections is not None else pd.DataFrame()
        for frame, name in ((left_rejections, "primary"), (right_rejections, "oracle")):
            missing = sorted({"order_id", "reason"} - set(frame.columns))
            if missing and not frame.empty:
                raise ValueError(f"{name}_rejections_missing:{','.join(missing)}")
        left = {str(row.order_id): str(row.reason) for row in left_rejections.itertuples(index=False)}
        right = {str(row.order_id): str(row.reason) for row in right_rejections.itertuples(index=False)}
        for order_id in sorted(set(left) | set(right)):
            left_reason, right_reason = left.get(order_id), right.get(order_id)
            insufficient_equivalent = {left_reason, right_reason} in (
                {"insufficient_cash_or_shares", "insufficient_cash"},
                {"insufficient_cash_or_shares", "insufficient_shares"},
            )
            if left_reason != right_reason and not insufficient_equivalent:
                differences.append(ReconciliationDifference(
                    scope="ORDER", key=f"{order_id}:rejection", primary_value=left.get(order_id),
                    oracle_value=right.get(order_id), classification="REJECTION_SET_MISMATCH",
                    detail="rejection presence or reason differs",
                ))

    position_columns = {"trade_date", "symbol", "shares"}
    for frame, name in ((primary_positions, "primary"), (oracle_positions, "oracle")):
        missing = sorted(position_columns - set(frame.columns))
        if missing:
            raise ValueError(f"{name}_positions_missing:{','.join(missing)}")
    left_positions = primary_positions.groupby(["trade_date", "symbol"], as_index=False)["shares"].sum()
    right_positions = oracle_positions.groupby(["trade_date", "symbol"], as_index=False)["shares"].sum()
    merged_positions = left_positions.merge(right_positions, on=["trade_date", "symbol"], how="outer", suffixes=("_primary", "_oracle")).fillna(0)
    for row in merged_positions.itertuples(index=False):
        if int(row.shares_primary) != int(row.shares_oracle):
            first_dates.append(str(row.trade_date))
            differences.append(ReconciliationDifference(
                scope="POSITION", key=f"{row.trade_date}:{row.symbol}",
                primary_value=int(row.shares_primary), oracle_value=int(row.shares_oracle),
                difference=float(row.shares_primary - row.shares_oracle),
                classification="POSITION_SHARE_MISMATCH", detail="daily share balance differs",
            ))

    required_nav = {"trade_date", "cash", "nav"}
    for frame, name in ((primary_nav, "primary"), (oracle_nav, "oracle")):
        missing = sorted(required_nav - set(frame.columns))
        if missing:
            raise ValueError(f"{name}_nav_missing:{','.join(missing)}")
    merged_nav = primary_nav.merge(oracle_nav, on="trade_date", how="outer", suffixes=("_primary", "_oracle"))
    for row in merged_nav.itertuples(index=False):
        trade_date = str(row.trade_date)
        if any(pd.isna(value) for value in (row.cash_primary, row.cash_oracle, row.nav_primary, row.nav_oracle)):
            first_dates.append(trade_date)
            differences.append(ReconciliationDifference(
                scope="NAV", key=trade_date, primary_value=row.nav_primary, oracle_value=row.nav_oracle,
                classification="NAV_DATE_SET_MISMATCH", detail="daily NAV exists in only one ledger",
            ))
            continue
        cash_diff = float(row.cash_primary) - float(row.cash_oracle)
        if abs(cash_diff) > cash_tolerance_cny + 1e-12:
            first_dates.append(trade_date)
            differences.append(ReconciliationDifference(
                scope="CASH", key=trade_date, primary_value=float(row.cash_primary), oracle_value=float(row.cash_oracle),
                difference=cash_diff, classification="CASH_MISMATCH", detail="cash exceeds CNY tolerance",
            ))
        nav_base = max(abs(float(row.nav_oracle)), 1e-12)
        nav_bps = abs(float(row.nav_primary) - float(row.nav_oracle)) / nav_base * 10_000
        if nav_bps > nav_tolerance_bps + 1e-12:
            first_dates.append(trade_date)
            differences.append(ReconciliationDifference(
                scope="NAV", key=trade_date, primary_value=float(row.nav_primary), oracle_value=float(row.nav_oracle),
                difference=nav_bps, classification="NAV_MISMATCH", detail="NAV exceeds basis-point tolerance",
            ))

    for metric in ("total_return", "max_drawdown", "trade_count"):
        if metric not in primary_metrics or metric not in oracle_metrics:
            differences.append(ReconciliationDifference(
                scope="METRIC", key=metric, primary_value=primary_metrics.get(metric), oracle_value=oracle_metrics.get(metric),
                classification="METRIC_MISSING", detail="required metric missing",
            ))
            continue
        if abs(float(primary_metrics[metric]) - float(oracle_metrics[metric])) > 1e-12:
            differences.append(ReconciliationDifference(
                scope="METRIC", key=metric, primary_value=primary_metrics[metric], oracle_value=oracle_metrics[metric],
                difference=float(primary_metrics[metric]) - float(oracle_metrics[metric]),
                classification="METRIC_MISMATCH", detail="headline metric must match exactly",
            ))

    return LedgerReconciliationReport(
        release_id=release_id, run_id=run_id,
        status="VERIFIED" if not differences else "MISMATCH_BLOCKED",
        cash_tolerance_cny=cash_tolerance_cny, nav_tolerance_bps=nav_tolerance_bps,
        first_divergence_at=min(first_dates) if first_dates else None,
        differences=tuple(differences), primary_metrics=primary_metrics, oracle_metrics=oracle_metrics,
    )
