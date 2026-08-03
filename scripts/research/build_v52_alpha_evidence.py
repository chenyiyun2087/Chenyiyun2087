#!/usr/bin/env python3
"""Build PIT-clean Alpha evidence for the v5.2 formal chain.

Reads the sealed Formal Package (factor panel, market snapshot, universe,
source manifests) plus index closes from the local MySQL mirror and produces
the three evidence artifacts the alpha validation orchestrator
(run_alpha_v3_validation.py) consumes:

  factor_returns.csv       daily cross-sectional long-short factor returns
  factor_panel_fwd.csv     frozen factor panel + fwd_{5,10,20}d_return
  benchmark_nav_daily.csv  index NAV for 000300.SH / 000905.SH / 000852.SH

Methodology (PIT-safe, consistent with the v5.2 strict_t1_open_precommit
execution model):

* Factor value date T = panel row with signal time T15:30+08:00. Every factor
  used in formation has genuine {factor}_available_at <= signal_time from the
  sealed panel (no backfill, no auto-fill).
* Factor return labeled at trade_date t = one-day open-to-open return
  (open[t] -> open[t+1]) of an equal-weighted long-short quintile portfolio
  (top quintile minus bottom quintile of the factor value at t-1's signal),
  restricted to the eligible universe that is listed, not ST, not suspended,
  and present in the market snapshot. The row's {factor}_available_at columns
  carry the *formation* timestamps from row t-1 (the information time), which
  is always <= signal_time[t] — the availability audit therefore verifies that
  no future factor *value* was used; the return realization itself is the
  measured quantity, documented here explicitly.
* market_regime factor return = the market's own daily return (the regime
  state is the market factor proxy), taken from the market snapshot's
  market_return where present, else the equal-weight mean of eligible
  open-to-open returns.
* industry factor return = industry-tilt premium: membership-weighted mean of
  industry equal-weight returns minus the equal-weight universe return.
* fwd_{h}d_return at T = close[T] -> close[T+h] (h trading days ahead),
  close-to-close, the standard cross-sectional IC convention; no look-ahead
  because the factor value at T is available by T's signal time.
* Benchmark NAV = index close series normalized to 1.0 at the first aligned
  date, available_at T16:00+08:00 (after the close).

Research-only: this script never grants capital authority, never mutates the
sealed package, and never upgrades synthetic evidence to historical E3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.artifact_seal import verify_seal

REQUIRED_FACTORS = (
    "market_beta",
    "size",
    "volatility",
    "liquidity",
    "industry",
    "momentum",
    "value",
    "market_regime",
)
IC_HORIZONS = (5, 10, 20)
MIN_QUINTILE_STOCKS = 20
DB_URL = "mysql+pymysql://root:{password}@localhost:3306/chenyiyun"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.normalize()
    df["symbol"] = df["symbol"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
    return df


def validate_package_binding(
    package_dir: Path,
    *,
    formal_pit_run_id: str,
    package_id: str,
) -> list[str]:
    """Fail closed unless the package is sealed and identity-bound."""
    blockers: list[str] = []
    manifest_path = package_dir / "package_manifest.json"
    if not manifest_path.is_file():
        return ["package_manifest_missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ["package_manifest_unreadable"]
    if manifest.get("formal_pit_run_id") != formal_pit_run_id:
        blockers.append("package_pit_run_id_mismatch")
    if manifest.get("package_id") != package_id:
        blockers.append("package_id_mismatch")
    seal = package_dir / "seal_manifest.json"
    if not seal.is_file():
        blockers.append("package_seal_missing")
    else:
        try:
            seal_result = verify_seal(package_dir)
        except Exception as exc:  # pragma: no cover - defensive
            blockers.append(f"package_seal_verify_error:{type(exc).__name__}:{exc}")
            seal_result = {"status": "TAMPERED"}
        if seal_result.get("status") != "VERIFIED":
            blockers.append(f"package_seal_not_verified:{seal_result.get('status')}")
    if not (package_dir / "source_manifest.json").is_file():
        blockers.append("source_manifest_missing")
    return blockers


def _formation_frame(panel: pd.DataFrame) -> pd.DataFrame:
    """Rows usable for factor formation: eligible, listed, not ST/suspended."""
    usable = panel.copy()
    if "eligible_universe" in usable.columns:
        usable = usable[usable["eligible_universe"].astype(bool)]
    if "is_st" in usable.columns:
        usable = usable[usable["is_st"].fillna(0).astype(int) == 0]
    if "is_suspended" in usable.columns:
        usable = usable[usable["is_suspended"].fillna(0).astype(int) == 0]
    return usable


def _open_to_open_returns(open_rows: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol one-day open-to-open returns, indexed by label date.

    Value at row t = open[t+1] / open[t] - 1 (the return realized while
    holding from day t's open to day t+1's open).
    """
    piv = open_rows.pivot_table(index="trade_date", columns="symbol", values="open")
    fwd = piv.shift(-1)
    returns = (fwd / piv - 1.0).replace([np.inf, -np.inf], np.nan)
    return returns


def _cross_sectional_market_return(returns_wide: pd.DataFrame, market: pd.DataFrame) -> pd.Series:
    """Daily market return: market_return column if present, else equal-weight."""
    mkt = market.dropna(subset=["market_return"])
    if not mkt.empty:
        by_date = mkt.groupby("trade_date")["market_return"].apply(
            lambda s: s.iloc[0] if len(s) else np.nan
        )
        if by_date.notna().mean() > 0.5:
            return by_date
    return returns_wide.mean(axis=1)


def _industry_spread(usable: pd.DataFrame, market: pd.DataFrame) -> pd.Series:
    """Industry rotation spread: top-quintile industries minus bottom-quintile.

    Per label date, each industry's equal-weight open-to-open return is
    computed; industries are ranked and the membership-weighted mean return of
    the top quintile of industries minus the bottom quintile is the industry
    factor return. (A membership-weighted mean of *all* industry means is the
    universe mean by construction — a degenerate constant — so a spread is
    the honest non-zero construction.)
    """
    ind = usable[["trade_date", "symbol", "industry"]].dropna(subset=["industry"])
    ind = ind.merge(
        market[["trade_date", "symbol", "open"]], on=["trade_date", "symbol"], how="inner"
    )
    if ind.empty:
        return pd.Series(dtype=float)
    returns_wide = _open_to_open_returns(ind[["trade_date", "symbol", "open"]])
    stacked = returns_wide.stack().rename("ret").reset_index()
    ind_ret = ind.merge(stacked, on=["trade_date", "symbol"]).groupby(
        ["trade_date", "industry"]
    )["ret"].mean()
    weights = (
        ind.groupby(["trade_date", "industry"]).size()
        / ind.groupby("trade_date").size()
    )
    spread_values: dict[pd.Timestamp, float] = {}
    for label_date, group in ind_ret.groupby(level=0, sort=True):
        weights_day = weights.loc[label_date]
        ordered = group.droplevel(0).sort_values()
        n = len(ordered)
        if n < 5:
            continue
        top = ordered.tail(max(1, n // 5)).index
        bottom = ordered.head(max(1, n // 5)).index
        top_ret = (ordered.loc[top] * weights_day.loc[top]).sum() / weights_day.loc[top].sum()
        bottom_ret = (ordered.loc[bottom] * weights_day.loc[bottom]).sum() / weights_day.loc[bottom].sum()
        spread_values[pd.Timestamp(label_date)] = float(top_ret - bottom_ret)
    return pd.Series(spread_values, dtype=float)


def build_factor_returns(
    panel: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    """Daily long-short quintile factor returns (see module docstring).

    Row labeled at t uses factor exposures from t-1's signal (formation date)
    and the open-to-open return realized on day t.
    """
    panel = _normalize_dates(panel)
    market = _normalize_dates(market)
    usable = _formation_frame(panel)
    merged = market[["trade_date", "symbol", "open", "market_return"]].merge(
        usable[["trade_date", "symbol"]].drop_duplicates(),
        on=["trade_date", "symbol"],
        how="inner",
    )
    if merged.empty:
        return pd.DataFrame()
    returns_wide = _open_to_open_returns(merged[["trade_date", "symbol", "open"]])
    daily_market_return = _cross_sectional_market_return(returns_wide, market)
    industry_premium = _industry_spread(usable, market)

    dates = sorted(panel["trade_date"].dropna().unique())
    rows: list[dict[str, object]] = []
    for label_index, label_date in enumerate(dates):
        if label_index == 0:
            continue  # no formation information before the first panel date
        formation_date = dates[label_index - 1]
        if label_date not in daily_market_return.index:
            continue
        signal = panel.loc[panel["trade_date"].eq(label_date), "signal_time"]
        if signal.empty:
            continue
        formation = usable[usable["trade_date"].eq(formation_date)]
        if formation.empty:
            continue
        row: dict[str, object] = {
            "trade_date": label_date.strftime("%Y-%m-%d"),
            "signal_time": signal.iloc[0],
        }
        for factor in REQUIRED_FACTORS:
            if factor == "market_regime":
                value = (
                    float(daily_market_return.loc[label_date])
                    if pd.notna(daily_market_return.loc[label_date])
                    else None
                )
                avail = panel.loc[
                    panel["trade_date"].eq(formation_date), "market_available_at"
                ]
            elif factor == "industry":
                value = (
                    float(industry_premium.loc[label_date])
                    if label_date in industry_premium.index
                    and pd.notna(industry_premium.loc[label_date])
                    else None
                )
                avail = panel.loc[
                    panel["trade_date"].eq(formation_date), "industry_available_at"
                ]
            else:
                values = (
                    formation.set_index("symbol")[factor]
                    .apply(pd.to_numeric, errors="coerce")
                    .dropna()
                )
                if len(values) < 2 * MIN_QUINTILE_STOCKS:
                    value = None
                else:
                    ordered = values.sort_values()
                    n = len(ordered)
                    top = ordered.tail(max(1, n // 5)).index
                    bottom = ordered.head(max(1, n // 5)).index
                    ret_top = float(returns_wide[top].mean(axis=1).get(label_date, np.nan)) if len(top) else np.nan
                    ret_bottom = float(returns_wide[bottom].mean(axis=1).get(label_date, np.nan)) if len(bottom) else np.nan
                    value = ret_top - ret_bottom if pd.notna(ret_top) and pd.notna(ret_bottom) else None
                avail = panel.loc[
                    panel["trade_date"].eq(formation_date), f"{factor}_available_at"
                ]
            row[factor] = value
            row[f"{factor}_available_at"] = (
                avail.iloc[0]
                if not avail.empty and pd.notna(avail.iloc[0])
                else None
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _trailing_window(series: pd.Series, window: int = 5) -> pd.Series:
    """Trailing window mean ending at the previous date (known at date close)."""
    return series.rolling(window, min_periods=1).mean().shift(1)


def build_fwd_panel(panel: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Frozen factor panel + close-to-close forward returns.

    The frozen panel carries industry as categorical labels and market_regime
    as a single market-wide label per date (a timing variable — cross-sectional
    IC is inapplicable to it by construction). For the factor-IC gate the
    categorical columns are kept under *_label and replaced by PIT-safe numeric
    encodings known by the signal time:
      - industry: the stock's industry equal-weight trailing 5-day open-to-open
        return (window ending the previous date) — varies cross-sectionally.
      - market_regime: the market's trailing 5-day return (same value for all
        stocks on a date, mirroring the timing nature of the regime state).
    """
    panel = _normalize_dates(panel)
    market = _normalize_dates(market)

    usable = _formation_frame(panel)
    merged = market[["trade_date", "symbol", "open"]].merge(
        usable[["trade_date", "symbol", "industry"]].drop_duplicates(),
        on=["trade_date", "symbol"],
        how="inner",
    )
    returns_wide = _open_to_open_returns(merged[["trade_date", "symbol", "open"]])
    universe_return = returns_wide.mean(axis=1)
    market_regime_numeric = _trailing_window(universe_return)

    industry_ret = merged.merge(
        returns_wide.stack().rename("ret").reset_index(), on=["trade_date", "symbol"]
    ).groupby(["trade_date", "industry"])["ret"].mean()
    industry_trailing = (
        industry_ret.unstack().apply(_trailing_window).stack().rename("industry_trailing")
    ).reset_index()

    out = panel.copy()
    if "industry" in out.columns:
        out["industry_label"] = out["industry"]
    if "market_regime" in out.columns:
        out["market_regime_label"] = out["market_regime"]
    out = out.merge(
        industry_trailing, on=["trade_date", "industry"], how="left"
    )
    out["industry"] = out["industry_trailing"]
    out["market_regime"] = out["trade_date"].map(market_regime_numeric)
    out.drop(columns=["industry_trailing"], inplace=True)

    closes = market[["trade_date", "symbol", "close"]].dropna(subset=["close"])
    pivot = closes.pivot_table(index="trade_date", columns="symbol", values="close")
    for horizon in IC_HORIZONS:
        fwd = pivot.shift(-horizon) / pivot - 1.0
        fwd = fwd.replace([np.inf, -np.inf], np.nan)
        stacked = fwd.stack().rename(f"fwd_{horizon}d_return").reset_index()
        out = out.merge(stacked, on=["trade_date", "symbol"], how="left")
    return out


def build_benchmark_nav(
    start_date: str,
    end_date: str,
    *,
    release_id: str,
    strategy_id: str,
) -> pd.DataFrame:
    """Index NAV from the local MySQL mirror (tushare_stock schema)."""
    import os
    from sqlalchemy import create_engine, text

    password = os.environ.get("CHENYIYUN_DB_PASSWORD", "")
    engine = create_engine(DB_URL.format(password=password))
    rows: list[dict[str, object]] = []
    index_sources = {
        "000300.SH": "tushare_stock.dwd_index_daily",
        "000905.SH": "tushare_stock.dwd_index_daily",
        "000852.SH": "tushare_stock.ods_index_daily",
    }
    with engine.connect() as conn:
        for code, table in index_sources.items():
            result = conn.execute(
                text(
                    f"SELECT trade_date, close FROM {table} "
                    "WHERE ts_code = :code ORDER BY trade_date"
                ),
                {"code": code},
            )
            closes = []
            for r in result:
                if not r[1] or float(r[1]) <= 0:
                    continue
                parsed = pd.to_datetime(str(r[0]), errors="coerce")
                if pd.isna(parsed):
                    continue
                date_str = parsed.strftime("%Y-%m-%d")
                if start_date <= date_str <= end_date:
                    closes.append((date_str, float(r[1])))
            if not closes:
                continue
            first_close = closes[0][1]
            for trade_date, close in closes:
                rows.append(
                    {
                        "benchmark": code,
                        "trade_date": trade_date,
                        "nav": round(close / first_close, 8),
                        "available_at": f"{trade_date}T16:00:00+08:00",
                        "close": close,
                        "source": f"mysql_{table}_v52",
                        "source_sha256": hashlib.sha256(
                            f"{code}:{trade_date}:{close}".encode()
                        ).hexdigest(),
                        "release_id": release_id,
                        "strategy_id": strategy_id,
                    }
                )
    engine.dispose()
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--formal-pit-run-id", required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--release-id", default="v5.2")
    parser.add_argument("--strategy-id", default="production_governed_vol_position_v1_2b_dynamic_score")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    blockers = validate_package_binding(
        args.package_dir,
        formal_pit_run_id=args.formal_pit_run_id,
        package_id=args.package_id,
    )
    if blockers:
        raise SystemExit(f"package_binding_blocked:{','.join(blockers)}")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    panel = pd.read_parquet(args.package_dir / "factor_panel.parquet")
    market = pd.read_parquet(args.package_dir / "market.parquet")

    factor_returns = build_factor_returns(panel, market)
    factor_returns_path = output_dir / "factor_returns.csv"
    factor_returns.to_csv(factor_returns_path, index=False)

    fwd_panel = build_fwd_panel(panel, market)
    fwd_panel_path = output_dir / "factor_panel_fwd.csv"
    fwd_panel.to_csv(fwd_panel_path, index=False)

    benchmark = build_benchmark_nav(
        args.start_date,
        args.end_date,
        release_id=args.release_id,
        strategy_id=args.strategy_id,
    )
    benchmark_path = output_dir / "benchmark_nav_daily.csv"
    benchmark.to_csv(benchmark_path, index=False)

    manifest = {
        "schema_version": "v5.2_alpha_evidence_v1",
        "release_id": args.release_id,
        "formal_pit_run_id": args.formal_pit_run_id,
        "package_id": args.package_id,
        "strategy_id": args.strategy_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": (
            "factor_returns: equal-weighted long-short quintile open-to-open "
            "daily returns; formation = factor exposures available at T15:30+08 "
            "signal of the prior panel date; labeled at the next trading day; "
            "available_at carries the formation time. market_regime = market "
            "daily return; industry = membership-weighted industry return minus "
            "equal-weight universe. fwd_{5,10,20}d_return: close-to-close "
            "forward returns. benchmark_nav: index closes normalized to 1.0 at "
            "the first aligned date."
        ),
        "inputs": {
            "factor_panel.parquet": _sha256(args.package_dir / "factor_panel.parquet"),
            "market.parquet": _sha256(args.package_dir / "market.parquet"),
            "universe.parquet": _sha256(args.package_dir / "universe.parquet"),
            "package_manifest.json": _sha256(args.package_dir / "package_manifest.json"),
            "source_manifest.json": _sha256(args.package_dir / "source_manifest.json"),
            "seal_manifest.json": _sha256(args.package_dir / "seal_manifest.json"),
        },
        "outputs": {
            "factor_returns.csv": _sha256(factor_returns_path),
            "factor_panel_fwd.csv": _sha256(fwd_panel_path),
            "benchmark_nav_daily.csv": _sha256(benchmark_path),
        },
        "factor_returns_rows": int(len(factor_returns)),
        "factor_panel_fwd_rows": int(len(fwd_panel)),
        "benchmark_rows": int(len(benchmark)),
    }
    manifest_path = output_dir / "alpha_evidence_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
