#!/usr/bin/env python3
"""v5.6.1 alpha-candidate daily diagnostics (H010 / H011 / H012).

Reads the day's production inputs (same fetch as the signal package
builder — v5.5.1 PIT-quality, fail-closed) and writes per-challenger
diagnostics under

    exports/formal_evidence/alpha_challengers/<challenger_id>/
        factor_diagnostics/alpha_candidate_gates/<signal_date>.json

with the v5.6.1 gates from runtime/alpha_candidate_diagnostics.py:

  H010  per-day OLS diagnostics of the residualized-F1 fit: matrix rank,
        condition number, effective cross-section, industry dummy count,
        style R^2, residual mean, residual-vs-F1 correlation, missing
        rate.  Rank-deficient design or effective cross-section below
        the pre-registered minimum -> C3_BLOCKED (the day is never fit
        with a truncated design).

  H011  R2 crowding state from the pre-registered overlay rules:
        top5_turnover_concentration / small_vs_large_20d_rs /
        state / position_multiplier.  ANY missing indicator ->
        state UNKNOWN, multiplier None (R2_INPUT_MISSING) — never the
        normal-state 1.0.

  H012  F1 risk-sized weights from the pre-registered contract
        (1/vol -> caps -> vol target).  Missing forecast volatility for
        a selected name -> RISK_INPUT_MISSING.  Selection is identical
        to F1 (same scores, same top-N) — volatility touches weights
        only.  H012's FORMAL forward start is recorded separately and
        remains NOT_YET_STARTED until the pre-registered params are
        complete AND the PIT covariance input is available (plan 4.3);
        these daily diagnostics are evidence accumulation only.

Usage:
  python scripts/research/run_alpha_candidate_diagnostics.py \
      [--date 2026-08-04] [--inputs-dir <fetched/parquets>] \
      [--output-root exports/formal_evidence/alpha_challengers]

Without --inputs-dir the script reads the live DB (needs
CHENYIYUN_DB_PASSWORD); with --inputs-dir it consumes pre-fetched
parquets (bars, mcap, basic, industry, labels) — hermetic replay mode.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.alpha_candidate_diagnostics import (  # noqa: E402
    diagnose_h010_day,
    resolve_r2_state,
    risk_sized_weights,
)
from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    compute_crowding_state,
    compute_raw_factors,
)

# Config sources — the single sources of truth for every threshold.
R2_OVERLAY_CFG = (PROJECT_ROOT / "config" / "risk_overlays"
                  / "r2_crowding_control.yaml")
H012_CFG = (PROJECT_ROOT / "config" / "alpha_challengers"
            / "h012_f1_risk_sized.yaml")
SHADOW_CFG = (PROJECT_ROOT / "config" / "strategy_runtime"
              / "forward_shadow_v2.yaml")

STYLE_SRC = {"size": "size_raw", "liquidity": "liquidity_raw",
             "market_beta": "beta_raw"}


def _load_r2_rules() -> list[dict]:
    cfg = yaml.safe_load(R2_OVERLAY_CFG.read_text(encoding="utf-8"))
    rules = cfg.get("rules", [])
    # Most severe first (extreme before elevated) — as pre-registered.
    return [{"id": r.get("id"), "condition": r.get("condition"),
             "position_multiplier": r.get("position_multiplier")}
            for r in rules]


def _load_h012_contract() -> dict:
    cfg = yaml.safe_load(H012_CFG.read_text(encoding="utf-8"))
    return dict(cfg.get("portfolio_constructor", {}))


def _load_f1_candidate() -> dict:
    cfg = yaml.safe_load(SHADOW_CFG.read_text(encoding="utf-8"))
    for cand in cfg.get("candidates", {}).values():
        if cand.get("challenger_id") == "f1_no_value":
            return cand
    raise RuntimeError("f1_no_value candidate missing from "
                       f"{SHADOW_CFG.name}")


def fetch_inputs(signal_date: str, inputs_dir: Path | None) -> dict:
    """Production fetch (v5.5.1 fail-closed) or hermetic parquet replay."""
    if inputs_dir is not None:
        out = {}
        for family in ("bars", "mcap", "basic", "industry", "labels"):
            p = inputs_dir / f"{family}.parquet"
            if not p.exists():
                raise RuntimeError(f"inputs-dir missing {p.name}")
            out[family] = pd.read_parquet(p)
        return out
    from scripts.ops.build_daily_alpha_signal_package import (
        fetch_production_inputs)
    return fetch_production_inputs(signal_date)


def _normalize_day(inputs: dict, signal_date: str) -> pd.DataFrame:
    """Per-symbol day with size_raw/liquidity_raw/beta_raw/industry.

    Mirrors the production package-builder flow exactly: _normalize
    merges circ_mv/pb/SCD industry, then size_raw = circ_mv and the
    industry column is replaced by the LABEL table's (PIT-visible on the
    signal date; the SCD is an availability source only).
    """
    from scripts.ops.build_daily_alpha_signal_package import _normalize
    raw = compute_raw_factors(inputs["bars"], signal_date)
    day = _normalize(raw, inputs["mcap"], inputs["basic"],
                     inputs["industry"])
    day["size_raw"] = pd.to_numeric(day["circ_mv"], errors="coerce")
    labels = inputs["labels"].copy()
    labels["symbol"] = (labels["ts_code"].astype(str)
                        .str.replace(r"\.(SH|SZ|BJ)$", "", regex=True)
                        .str.zfill(6))
    label_ind = labels[["symbol", "industry"]]
    day = day.drop(columns=["industry"], errors="ignore").merge(
        label_ind, on="symbol", how="left")
    day["industry"] = day["industry"].astype(str)

    # H012 forecast-volatility proxy: 20d rolling std of daily returns,
    # computed over the FULL bar history (the signal-date slice alone has
    # no window to roll over).
    bv = inputs["bars"].copy()
    bv["symbol"] = (bv["ts_code"].astype(str)
                    .str.replace(r"\.(SH|SZ|BJ)$", "", regex=True)
                    .str.zfill(6))
    bv["ret_1d"] = bv.groupby("symbol")["adj_close"].pct_change(1)
    bv["volatility_raw"] = bv.groupby("symbol")["ret_1d"].transform(
        lambda s: s.rolling(20, min_periods=10).std())
    sig_vol = bv[bv["trade_date"] == signal_date][["symbol", "volatility_raw"]]
    day = day.merge(sig_vol, on="symbol", how="left")
    return day


def _ranked_scores(day: pd.DataFrame, candidate: dict) -> pd.DataFrame:
    """F1 composite scores (pre-registered weights/signs) + centered ranks."""
    from scripts.ops.build_daily_alpha_signal_package import (
        compute_candidate_scores)
    return compute_candidate_scores(day, candidate)


REQUIRED_INPUT_FAMILIES = ("bars", "mcap", "basic", "industry", "labels")


def run_diagnostics(signal_date: str, inputs: dict,
                    output_root: Path) -> dict:
    """Compute the H010/H011/H012 daily diagnostics and write JSON."""
    missing_families = [f for f in REQUIRED_INPUT_FAMILIES
                        if f not in inputs]
    if missing_families:
        raise RuntimeError(
            "diagnostics input family missing: "
            f"{missing_families} — SIGNAL_PACKAGE_BLOCKED")
    rules = _load_r2_rules()
    contract = _load_h012_contract()
    f1_cand = _load_f1_candidate()

    day = _normalize_day(inputs, signal_date)

    # ── H010: residualized-F1 OLS diagnostics ─────────────────────────
    # The F1 composite score is computed first (pre-registered
    # weights/signs) and merged so the day has score + style cols.
    scores = _ranked_scores(day, f1_cand)
    h010_day = day.rename(columns=STYLE_SRC).merge(
        scores[["symbol", "score"]], on="symbol", how="left")
    h010_style = list(STYLE_SRC.values())
    diag = diagnose_h010_day(
        h010_day, h010_style,
        industry_col="industry",
        minimum_cross_section=int(
            f1_cand.get("residualization", {})
            .get("minimum_cross_section", 20)))
    h010 = {k: v for k, v in diag.items() if k != "residual_score"}
    h010["signal_date"] = signal_date
    h010["engine"] = "h010_residualized_f1"

    # ── H011: R2 crowding state (weight-only overlay) ─────────────────
    bars = inputs["bars"].copy()
    bars["symbol"] = (bars["ts_code"].astype(str)
                      .str.replace(r"\.(SH|SZ|BJ)$", "", regex=True)
                      .str.zfill(6))
    # circ_mv at the signal date, merged onto the bars for the RS calc.
    mcap = inputs["mcap"].copy()
    mcap["symbol"] = (mcap["ts_code"].astype(str)
                      .str.replace(r"\.(SH|SZ|BJ)$", "", regex=True)
                      .str.zfill(6))
    bars = bars.merge(mcap[["symbol", "circ_mv"]], on="symbol", how="left")
    crowding = compute_crowding_state(bars)
    r2 = resolve_r2_state(
        crowding.get("top5_turnover_concentration"),
        crowding.get("small_vs_large_20d_rs"), rules)
    h011 = {
        "signal_date": signal_date,
        "engine": "h011_f1_r2",
        "top5_turnover_concentration":
            crowding.get("top5_turnover_concentration"),
        "small_vs_large_20d_rs": crowding.get("small_vs_large_20d_rs"),
        "state": r2["state"],
        "position_multiplier": r2["position_multiplier"],
        "blocked": r2["blocked"],
        "reason": r2["reason"],
    }

    # ── H012: F1 risk-sized weights (evidence accumulation only) ──────
    # Same F1 scores as H010 — selection is identical to F1 by contract.
    if "volatility_raw" not in day.columns:
        vol_map = {}
    else:
        vol_map = dict(zip(day["symbol"].astype(str),
                           pd.to_numeric(day["volatility_raw"],
                                         errors="coerce")))
    h012 = risk_sized_weights(
        scores[["symbol", "score"]], vol_map, contract,
        top_n=int(f1_cand.get("execution", {}).get("top_n", 10)))
    h012["signal_date"] = signal_date
    h012["engine"] = "h012_f1_risk_sized"
    # Plan 4.3: H012 joins formal Forward only when pre-registered params
    # are complete AND the PIT covariance input is available.  Until then
    # the formal start is recorded separately and these diagnostics are
    # evidence accumulation, never formal forward evidence.
    h012["formal_forward_start"] = "NOT_YET_STARTED"

    report = {
        "schema_version": "alpha_candidate_daily_diagnostics_v1",
        "signal_date": signal_date,
        "candidates": {
            "h010_residualized_f1": h010,
            "h011_f1_r2": h011,
            "h012_f1_risk_sized": h012,
        },
        "written_at": datetime.now(timezone_utc()).isoformat(timespec="seconds"),
    }

    for cid, block in report["candidates"].items():
        out_dir = (output_root / cid / "factor_diagnostics"
                   / "alpha_candidate_gates")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{signal_date}.json").write_text(
            json.dumps(block, ensure_ascii=False, indent=2,
                       sort_keys=True),
            encoding="utf-8")
    return report


def timezone_utc():
    from datetime import timezone
    return timezone.utc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Signal date (default: today)")
    parser.add_argument("--inputs-dir", type=Path,
                        help="Replay pre-fetched parquets instead of DB")
    parser.add_argument("--output-root", type=Path,
                        default=PROJECT_ROOT / "exports" / "formal_evidence"
                        / "alpha_challengers")
    args = parser.parse_args()

    signal_date = args.date or datetime.now().strftime("%Y-%m-%d")
    inputs = fetch_inputs(signal_date, args.inputs_dir)
    report = run_diagnostics(signal_date, inputs, args.output_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
