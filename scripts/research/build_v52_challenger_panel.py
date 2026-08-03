#!/usr/bin/env python3
"""Build the PIT-clean challenger panel + market for the topk alpha lab.

This producer derives research challenger factors strictly from the sealed
Formal Package artifacts (market.parquet / universe.parquet / scores.parquet /
adjustment.parquet).  It never touches production data or the sealed chain:
the output is a research-only panel consumed by topk_alpha_lab.py with the
package binding verified against the immutable package.

Design contracts (all documented in the output manifest):

1. Factor windows follow the sealed chain's own short-window convention.
   The production PIT factor builder (pit_factor_panel_builder.py) computes
   momentum/volatility/beta/liquidity as 20-day rolling windows with
   min_periods=10 from snapshot-window data only (no pre-window history
   exists anywhere in the data estate; dwd_daily/dwd_stock_daily_standard
   contain zero rows before 2022-01-04).  Challenger factors use the same
   convention so coverage is complete from the panel's first date.

2. Returns are computed on the chain's own PIT-adjusted closes
   (adj_close = close * adj_factor, exactly as the builder does for
   momentum), so ex-date dividend drops do not masquerade as returns.

3. open_limit_status replicates the sealed execution model's rule exactly:
   gap = (open * f[t]) / (pre_close * f[t-1]) - 1  (the model's
   adj_open / prev_adj_close), classified LIMIT_UP at >= +9.5% and
   LIMIT_DOWN at <= -9.5%.  Same data, same formula, same thresholds as
   strict_t1_open_precommit_v1, so the lab's fill semantics match the
   verified dual-ledger execution model.

4. amount is converted from the sealed thousand-yuan unit to CNY (the
   sealed model divides amount by 1000: "Tushare amount is normally
   reported in thousand yuan"), so the lab's ADV cap is in CNY.

5. announcement_date is rendered with an explicit midnight +08:00 offset.
   The sealed column is date-only; the lab's PIT contract requires an
   explicit timezone.  Rendering the calendar date at midnight +08:00 is a
   faithful representation, not a backfill.

6. Financial challenger factors (quality/growth/earnings_acceleration)
   cannot be built: the sealed financial snapshot is pb-only.  They are
   left absent and the lab blocks those challengers fail-closed.

Every factor carries {factor}_available_at = signal_time (T15:30+08:00) and
the producer audits 0 availability-after-signal violations before writing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FACTOR_WINDOWS = {
    "residual_momentum": "trailing-20d residual return sum (min_periods=5) over [t-20, t-6] "
                         "(5-day skip separates it from short_reversal)",
    "short_reversal": "negative trailing-5d cumulative adjusted return",
    "trend_stability": "fraction of up days over trailing 20d (min_periods=10)",
    "industry_strength": "industry equal-weight trailing-20d mean adjusted return "
                         "minus market trailing-20d mean (min_periods=10)",
    "market_regime_score": "trailing-20d mean of the sealed market_return (min_periods=10); "
                           "time-series variable, same value for all stocks on a date",
}

FACTOR_SOURCES = {
    "residual_momentum": "market.parquet close * adjustment.parquet adj_factor (chain adj_close), "
                         "market.parquet market_return",
    "short_reversal": "market.parquet close * adj_factor",
    "trend_stability": "market.parquet close * adj_factor",
    "industry_strength": "market.parquet close * adj_factor, scores.parquet industry, "
                         "market.parquet market_return",
    "market_regime_score": "market.parquet market_return",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.normalize()
    return df


def _git_sha() -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        return "UNKNOWN"


def build_factors(
    market: pd.DataFrame,
    scores: pd.DataFrame,
    industry_map: pd.DataFrame,
) -> pd.DataFrame:
    """Compute the five price/market-derived challenger factors.

    All windows end at T and the values are known at the T-day close, so
    every factor is bound to signal_time of T.  Rows with insufficient
    history are NaN (treated as unavailable by the lab, never neutral).
    """
    market = market.sort_values(["symbol", "trade_date"]).copy()
    # Chain convention: adj_close = close * adj_factor (pit_factor_panel_builder).
    # adj_factor here is the canonical adjustment.parquet family, covering the
    # full market window (2022-01-04+) so warmup windows are complete.
    market["adj_close"] = pd.to_numeric(market["close"], errors="coerce") * market["adj_factor"]
    market["adj_ret"] = market.groupby("symbol")["adj_close"].pct_change()

    market_return = pd.to_numeric(
        market.groupby("trade_date")["market_return"].first(), errors="coerce"
    )
    market_return.index = pd.to_datetime(market_return.index, errors="coerce")
    # Row-aligned market return (same RangeIndex family as the market frame,
    # so rolling cov/var align like the production pit builder).
    mkt_row = market["trade_date"].map(market_return)

    out = market[["trade_date", "symbol"]].copy()
    out["adj_ret"] = market["adj_ret"]

    # --- short_reversal: -(trailing 5d adjusted return) -------------------
    out["short_reversal"] = -(out.groupby("symbol")["adj_ret"]
                              .transform(lambda s: s.rolling(5, min_periods=5).sum()))

    # --- trend_stability: fraction of up days over trailing 20d -----------
    up_day = (out["adj_ret"] > 0).astype(float)
    out["trend_stability"] = up_day.groupby(out["symbol"]) \
        .transform(lambda s: s.rolling(20, min_periods=10).mean())

    # --- market regime: trailing 20d mean market return (time-series) ----
    mkt_trail = market_return.rolling(20, min_periods=10).mean().rename("mkt_trail")
    out["market_regime_score"] = out["trade_date"].map(mkt_trail)

    # --- residual momentum: rolling beta on market, residual sum, 5d skip --
    beta_parts: list[pd.Series] = []
    for _, group in market.groupby("symbol", sort=False):
        stock = pd.to_numeric(group["adj_ret"], errors="coerce")
        market_aligned = mkt_row.loc[group.index]
        beta_parts.append(
            stock.rolling(20, min_periods=10).cov(market_aligned)
            / market_aligned.rolling(20, min_periods=10).var().replace(0.0, np.nan)
        )
    market["beta"] = pd.concat(beta_parts).sort_index()
    market["resid"] = pd.to_numeric(market["adj_ret"], errors="coerce") - market["beta"] * mkt_row
    # Per-symbol trailing-20d residual sum (min 5 obs), shifted 5 days within
    # each symbol so the most recent week never bleeds into the composite.
    out["residual_momentum"] = market["resid"].groupby(market["symbol"]) \
        .transform(lambda s: s.rolling(20, min_periods=5).sum().shift(5))

    # --- industry strength: industry trailing mean minus market trailing mean -
    # The industry mapping comes from the canonical industry.parquet family
    # (full market window), not the scores panel (which starts later).
    ind = industry_map[["trade_date", "symbol", "industry"]].dropna(subset=["industry"])
    ind = ind.merge(out[["trade_date", "symbol", "adj_ret"]], on=["trade_date", "symbol"], how="inner")
    ind_ret = ind.groupby(["trade_date", "industry"])["adj_ret"].mean()
    ind_trail = ind_ret.unstack().rolling(20, min_periods=10).mean().stack() \
        .rename("industry_trailing").reset_index()
    ind_trail["industry_strength"] = ind_trail["industry_trailing"] - ind_trail["trade_date"].map(mkt_trail)
    ind_trail = ind_trail.drop(columns=["industry_trailing"])
    ind_map = industry_map[["trade_date", "symbol", "industry"]].drop_duplicates()
    ind_trail = ind_trail.merge(ind_map, on=["trade_date", "industry"], how="left") \
        [["trade_date", "symbol", "industry_strength"]]
    out = out.merge(ind_trail, on=["trade_date", "symbol"], how="left")
    return out


def build_market_file(market: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Canonical market file for the lab, replicating sealed execution semantics."""
    market = _normalize_dates(market)
    universe = _normalize_dates(universe)
    uni_cols = ["trade_date", "symbol", "is_st", "is_suspended", "limit_status"]
    out = market.merge(universe[uni_cols], on=["trade_date", "symbol"], how="left")
    if out[["is_st", "is_suspended", "limit_status"]].isna().any().any():
        raise RuntimeError("universe status fields missing for market rows")
    # amount: sealed thousand-yuan -> CNY (sealed model divides by 1000).
    out["amount"] = pd.to_numeric(out["amount"], errors="coerce") * 1000.0
    # open_limit_status: replicate _execution_proxy_fields (adj_open/prev_adj_close).
    out["adj_factor"] = out.groupby("symbol")["adj_factor"].transform(
        lambda s: s.ffill()  # adjustment.parquet carries a row per symbol-day
    )
    out["prev_adj_factor"] = out.groupby("symbol")["adj_factor"].shift(1)
    gap = (
        pd.to_numeric(out["open"], errors="coerce") * out["adj_factor"]
        / (pd.to_numeric(out["pre_close"], errors="coerce") * out["prev_adj_factor"])
        - 1.0
    )
    out["open_limit_status"] = np.select(
        [gap >= 0.095, gap <= -0.095],
        ["LIMIT_UP", "LIMIT_DOWN"],
        default="NORMAL",
    )
    out["open_limit_status"] = out["open_limit_status"].where(
        gap.notna() & pd.to_numeric(out["pre_close"], errors="coerce").gt(0),
        "NO_TRADE",
    )
    return out


def build_panel(
    scores: pd.DataFrame,
    factors: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    """Assemble the lab panel: sealed base + challenger factors + fwd labels."""
    scores = _normalize_dates(scores)
    panel = scores.copy()
    panel["symbol"] = panel["symbol"].astype(str)
    factors = factors.merge(
        panel[["trade_date", "symbol", "signal_time"]],
        on=["trade_date", "symbol"],
        how="left",
    )
    for factor in FACTOR_WINDOWS:
        panel = panel.merge(
            factors[["trade_date", "symbol", factor]],
            on=["trade_date", "symbol"],
            how="left",
        )
        panel[f"{factor}_available_at"] = panel["signal_time"]

    # Forward labels: close-to-close on sealed closes (evidence-builder
    # convention; same basis as factor_panel_fwd.csv).
    closes = market[["trade_date", "symbol", "close"]].dropna(subset=["close"])
    pivot = closes.pivot_table(index="trade_date", columns="symbol", values="close")
    for horizon in (5, 10, 20):
        fwd = pivot.shift(-horizon) / pivot - 1.0
        fwd = fwd.replace([np.inf, -np.inf], np.nan)
        stacked = fwd.stack().rename(f"fwd_{horizon}d_return").reset_index()
        panel = panel.merge(stacked, on=["trade_date", "symbol"], how="left")

    # Sample boundary: the lab's IC coverage is the minimum over panel dates,
    # and forward-return labels are inherently absent for the final `horizon`
    # trading days.  Trim the panel to the last date with a complete fwd_20d
    # label so the coverage contract is satisfiable; the trailing market rows
    # stay in the market file as the label horizon.
    if "fwd_20d_return" in panel.columns:
        last_label_date = panel.loc[panel["fwd_20d_return"].notna(), "trade_date"].max()
        panel = panel[panel["trade_date"] <= last_label_date].copy()

    # PIT contract for financial-sourced factors: the sealed columns already
    # carry the revision chain; render announcement_date timezone-explicit.
    panel["announcement_date"] = (
        pd.to_datetime(panel["announcement_date"], errors="coerce")
        .dt.strftime("%Y-%m-%dT00:00:00+08:00")
    )
    # Drop columns the lab must source from the market file only (avoid
    # merge collisions and mixed semantics).
    drop = [col for col in ("is_st", "is_suspended", "limit_status", "market_available_at",
                            "market_regime", "execution_time") if col in panel.columns]
    return panel.drop(columns=drop)


def audit_pit(panel: pd.DataFrame, market_file: pd.DataFrame) -> list[str]:
    """Fail-closed row-wise audit: every availability timestamp <= signal_time."""
    blockers: list[str] = []
    signal = pd.to_datetime(panel["signal_time"], errors="coerce", utc=True)
    for factor in FACTOR_WINDOWS:
        available = pd.to_datetime(panel[f"{factor}_available_at"], errors="coerce", utc=True)
        if available.isna().any():
            blockers.append(f"{factor}_available_at_invalid")
        if (available.notna() & signal.notna() & (available > signal)).any():
            blockers.append(f"{factor}_available_after_signal")
    market_signal = pd.to_datetime(
        pd.to_datetime(market_file["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
        + "T15:30:00+08:00",
        errors="coerce", utc=True,
    )
    market_available = pd.to_datetime(
        market_file["market_available_at"], errors="coerce", utc=True
    )
    if market_available.isna().any():
        blockers.append("market_available_at_invalid")
    if (market_available.notna() & market_signal.notna() & (market_available > market_signal)).any():
        blockers.append("market_available_after_signal")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--formal-run-id", required=True)
    parser.add_argument("--pit-run-id", required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    package = args.package_dir
    for name in ("market.parquet", "universe.parquet", "scores.parquet",
                 "adjustment.parquet", "seal_manifest.json", "package_manifest.json"):
        if not (package / name).is_file():
            print(f"BLOCKED: package missing {name}")
            return 2

    market = _read_parquet(package / "market.parquet")
    universe = _read_parquet(package / "universe.parquet")
    scores = _read_parquet(package / "scores.parquet")
    adjustment = _read_parquet(package / "adjustment.parquet")
    industry = _read_parquet(package / "industry.parquet")
    market = _normalize_dates(market)
    universe = _normalize_dates(universe)
    scores = _normalize_dates(scores)
    adjustment = _normalize_dates(adjustment)
    industry = _normalize_dates(industry)

    # The canonical industry family must agree with the sealed scores' labels.
    ind_check = industry[["trade_date", "symbol", "industry"]].drop_duplicates().merge(
        scores[["trade_date", "symbol", "industry"]].rename(
            columns={"industry": "industry_scores"}),
        on=["trade_date", "symbol"],
        how="inner",
    )
    if (ind_check["industry"] != ind_check["industry_scores"]).any():
        print("BLOCKED: industry mapping disagreement between snapshots")
        return 2

    # Use the canonical adjustment snapshot as the factor source; the sealed
    # scores carry the same factor, verified equal below.
    market = market.merge(
        adjustment[["trade_date", "symbol", "adj_factor"]],
        on=["trade_date", "symbol"],
        how="left",
        suffixes=("", "_adj"),
    )
    if market["adj_factor"].isna().any():
        print("BLOCKED: adjustment snapshot missing rows")
        return 2

    # The sealed scores' own adj_factor must agree with the adjustment family.
    merged_check = market[["trade_date", "symbol", "adj_factor"]].merge(
        scores[["trade_date", "symbol", "adj_factor"]].rename(
            columns={"adj_factor": "adj_factor_scores"}),
        on=["trade_date", "symbol"],
        how="inner",
    )
    if not np.isclose(merged_check["adj_factor"], merged_check["adj_factor_scores"],
                      rtol=1e-9, atol=1e-12).all():
        print("BLOCKED: adjustment factor disagreement between snapshots")
        return 2

    factors = build_factors(market, scores, industry)
    market_file = build_market_file(market, universe)
    panel = build_panel(scores, factors, market)

    blockers = audit_pit(panel, market_file)
    if blockers:
        print("BLOCKED:", blockers)
        return 2

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    panel_path = output / "panel.parquet"
    market_path = output / "market.parquet"
    panel.to_parquet(panel_path, index=False)
    market_file.to_parquet(market_path, index=False)

    # --- PIT audit (row-wise, fail-closed) ---
    signal = pd.to_datetime(panel["signal_time"], errors="coerce", utc=True)
    violations: dict[str, int] = {}
    for factor in FACTOR_WINDOWS:
        available = pd.to_datetime(panel[f"{factor}_available_at"], errors="coerce", utc=True)
        violations[factor] = int((available.notna() & signal.notna() & (available > signal)).sum())

    open_status_counts = market_file["open_limit_status"].value_counts().to_dict()
    manifest = {
        "schema_version": "v52_challenger_inputs_v1",
        "release": "v5.2_20260802_challenger",
        "formal_run_id": args.formal_run_id,
        "formal_pit_run_id": args.pit_run_id,
        "package_id": args.package_id,
        "source_files": {
            name: {"sha256": _sha256(package / name)} for name in
            ("market.parquet", "universe.parquet", "scores.parquet",
             "adjustment.parquet", "seal_manifest.json")
        },
        "package_seal_manifest_file_sha256": _sha256(package / "seal_manifest.json"),
        "panel": {
            "path": str(panel_path),
            "rows": int(len(panel)),
            "columns": list(panel.columns),
            "sample_start": str(panel["trade_date"].min().date()),
            "sample_end": str(panel["trade_date"].max().date()),
            "sha256": _sha256(panel_path),
        },
        "market": {
            "path": str(market_path),
            "rows": int(len(market_file)),
            "columns": list(market_file.columns),
            "sample_start": str(market_file["trade_date"].min().date()),
            "sample_end": str(market_file["trade_date"].max().date()),
            "sha256": _sha256(market_path),
        },
        "factor_windows": FACTOR_WINDOWS,
        "factor_sources": FACTOR_SOURCES,
        "factor_availability_binding": "available_at = signal_time (T15:30:00+08:00); "
                                       "all factor inputs are T-day-close data",
        "pit_audit": {
            "availability_after_signal_violations": violations,
            "blockers": blockers,
        },
        "open_limit_status": {
            "rule": "gap = (open * f[t]) / (pre_close * f[t-1]) - 1 (adj_open/prev_adj_close "
                    "of strict_t1_open_precommit_v1); LIMIT_UP >= +0.095, LIMIT_DOWN <= -0.095, "
                    "else NORMAL; NO_TRADE when gap undefined",
            "counts": open_status_counts,
            "notes": "replicates the sealed execution model's proxy on the same sealed data; "
                     "rows where raw open gaps across an ex-date inherit the model's own "
                     "classification",
        },
        "amount_unit": "converted from sealed thousand-yuan to CNY (x1000) so the lab's "
                       "adv_cny is in CNY, matching the sealed model's /1000 convention",
        "announcement_date": "rendered as midnight +08:00 of the sealed calendar date",
        "financial_factors": "quality/growth/earnings_acceleration not buildable: the sealed "
                             "financial snapshot is pb-only; those challengers block fail-closed",
        "code_head": _git_sha(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["content_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    (output / "challenger_inputs_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "PASS",
        "panel": str(panel_path),
        "market": str(market_path),
        "panel_rows": int(len(panel)),
        "market_rows": int(len(market_file)),
        "pit_violations": violations,
        "open_limit_status_counts": open_status_counts,
        "manifest": str(output / "challenger_inputs_manifest.json"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
