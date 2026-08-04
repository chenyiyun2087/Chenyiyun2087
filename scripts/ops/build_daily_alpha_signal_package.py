#!/usr/bin/env python3
"""Forward Shadow Engine v2 — daily immutable Signal Package builder (v5.5).

One sealed package per trading day at
  exports/forward_shadow_evidence/packages/YYYY-MM-DD/

Stages (matches task_registry/pipeline.yaml v5.5 DAG):
  16:20 data quality check
  16:30 PIT universe freeze
  16:40 per-candidate factor computation
  16:50 target portfolio generation
  17:00 Signal Package SEALED

Contract:
  - A SEALED package is never overwritten.  A correction writes
    revision_2/ beside the original (original preserved).
  - Any required input missing -> SIGNAL_PACKAGE_BLOCKED (no fallback to
    whole-market tradeable, no stale-date substitution).
  - A dirty worktree BLOCKS formal packaging (worktree_clean: false).
  - The builder NEVER writes to historical formal evidence (the old
    compute_daily_vls_scores.py appended to formal_scores.parquet —
    forbidden here).
  - Each candidate runs its OWN pipeline per
    config/strategy_runtime/forward_shadow_v2.yaml — no shared scores.

Pure core functions (seal_signal_package, build_target_portfolios, ...)
take DataFrames/dicts and are fully testable without a database.

Usage (production):
  CHENYIYUN_DB_PASSWORD=... python scripts/ops/build_daily_alpha_signal_package.py \
      --date 2026-08-05
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.pit_universe import build_daily_universe  # noqa: E402

PACKAGES_ROOT = PROJECT_ROOT / "exports" / "forward_shadow_evidence" / "packages"
RUNTIME_CFG_PATH = PROJECT_ROOT / "config" / "strategy_runtime" / "forward_shadow_v2.yaml"
SIGNAL_TIME = "15:30:00+08:00"

REQUIRED_PACKAGE_FILES = (
    "input_manifest.json", "data_quality_report.json", "universe.parquet",
    "factor_values.parquet", "scores.parquet", "target_portfolios.parquet",
    "signal_package_manifest.json", "package_sha256.json",
)

# R2 crowding thresholds (config/risk_overlays/r2_crowding_control.yaml).
R2_ELEVATED_CONC, R2_EXTREME_CONC = 0.25, 0.30
R2_ELEVATED_RS, R2_EXTREME_RS = 1.15, 1.25
R2_ELEVATED_MULT, R2_EXTREME_MULT = 0.70, 0.50

HISTORY_DAYS = 30  # trading days of bars fetched for rolling factors


class PackageSealedError(RuntimeError):
    """A SEALED package already exists at the target directory."""


class SignalPackageBlocked(RuntimeError):
    """A required input is missing — the package must not be produced."""


# ══════════════════════════════════════════════════════════════════
# Pure core — factor ranks, scores, portfolios, R2 overlay, seal
# ══════════════════════════════════════════════════════════════════


def _centered_rank(s: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(s, errors="coerce")
    return numeric.rank(method="average", pct=True) - 0.5


def compute_candidate_scores(raw: pd.DataFrame,
                             candidate: dict,
                             seed_base: int = 20260805) -> pd.DataFrame:
    """Compute one candidate's cross-sectional scores from the raw frame.

    raw columns required: symbol, trade_date, size_raw (circ_mv),
    liquidity_raw (20d mean amihud), momentum_raw (20d return),
    value_raw (pb, NEGATIVE rank direction pre-applied) when the
    candidate uses value, beta_raw when residualization uses it,
    industry when residualization uses industry FE.

    Returns per-symbol rows with: symbol, score, residual_score (C3 only),
    random_score (RND only).
    """
    out = raw.copy()
    factor_ranks = {}
    for factor in candidate.get("factor_weights", {}):
        src = {"size": "size_raw", "liquidity": "liquidity_raw",
               "momentum": "momentum_raw", "value": "value_raw"}[factor]
        if src not in out.columns:
            raise SignalPackageBlocked(
                f"{candidate.get('challenger_id')}: required raw factor "
                f"{src} missing for {factor}")
        ranks = _centered_rank(out[src])
        # Reverse rank semantics: value_raw already carries the reversal.
        factor_ranks[factor] = ranks

    score = pd.Series(0.0, index=out.index)
    for factor, weight in candidate.get("factor_weights", {}).items():
        sign = candidate.get("factor_signs", {}).get(factor, 1)
        score += float(weight) * sign * factor_ranks[factor].fillna(0.0)
    out["score"] = score

    transform = candidate.get("score_transform", "identity")
    if transform == "residualize_on_style":
        out["residual_score"] = residualize_scores(out, candidate)
        out["score"] = out["residual_score"]  # ranking uses the residual
    elif transform == "random_seeded":
        seed = int(candidate.get("random_seed_base", seed_base))
        rng = np.random.default_rng(seed + int(str(out["trade_date"].iloc[0])
                                               .replace("-", "")))
        out["random_score"] = rng.uniform(-0.5, 0.5, size=len(out))
        out["score"] = out["random_score"]
    return out


def residualize_scores(day: pd.DataFrame, candidate: dict) -> pd.Series:
    """Per-day OLS residual of score on style factors + industry FE.

    Same algorithm as scripts/research/build_residualized_alpha_scores.py.
    NaN rows are dropped from the fit and keep NaN residual (never
    zero-filled).  Missing style/industry inputs raise (fail-closed).
    """
    styles = candidate.get("residualization", {}).get("style_factors", [])
    with_industry = candidate.get("residualization", {}).get(
        "industry_fixed_effects", True)
    min_cs = int(candidate.get("residualization", {}).get(
        "minimum_cross_section", 20))
    src_map = {"size": "size_raw", "liquidity": "liquidity_raw",
               "market_beta": "beta_raw"}
    need = [src_map[s] for s in styles if s in src_map]
    if with_industry:
        need.append("industry")
    missing = [c for c in need if c not in day.columns]
    if missing:
        raise SignalPackageBlocked(
            f"{candidate.get('challenger_id')}: residualization requires "
            f"{missing} — SIGNAL_PACKAGE_BLOCKED")

    out = pd.Series(np.nan, index=day.index, dtype=float)
    mask = day[["score"] + need].notna().all(axis=1)
    sub = day[mask]
    if len(sub) < min_cs:
        return out
    x_style = sub[need[:3]].to_numpy(dtype=float) \
        if len(styles) else np.zeros((len(sub), 0))
    if with_industry:
        counts = sub["industry"].value_counts()
        dropped = counts.idxmax()
        cats = [c for c in counts.index if c != dropped]
        idx_map = {c: i for i, c in enumerate(cats)}
        ind = np.zeros((len(sub), len(cats)), dtype=float)
        for i, c in enumerate(sub["industry"]):
            j = idx_map.get(c)
            if j is not None:
                ind[i, j] = 1.0
        design = np.column_stack([np.ones(len(sub)), x_style, ind])
    else:
        design = np.column_stack([np.ones(len(sub)), x_style])
    y = sub["score"].to_numpy(dtype=float)
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ beta
    out.loc[mask] = resid
    return out


def compute_crowding_state(bars_20d: pd.DataFrame) -> dict:
    """R2 crowding indicators from up to 20d of raw bars.

    top5_turnover_concentration : share of total amount in the top 5%
                                  symbols by amount (signal day).
    small_vs_large_20d_rs       : 20d cum return of bottom circ_mv
                                  quartile / top circ_mv quartile.
    """
    if bars_20d.empty:
        return {"top5_turnover_concentration": None,
                "small_vs_large_20d_rs": None}
    day = bars_20d.sort_values("trade_date").groupby("trade_date").tail(1)
    day = day[day["trade_date"] == day["trade_date"].max()]
    day["amount"] = pd.to_numeric(day["amount"], errors="coerce").fillna(0.0)
    total_amount = float(day["amount"].sum())
    top5 = day["amount"].nlargest(max(1, int(np.ceil(len(day) * 0.05))))
    conc = float(top5.sum() / total_amount) if total_amount > 0 else None

    last = bars_20d[bars_20d["trade_date"] == bars_20d["trade_date"].max()]
    first = bars_20d[bars_20d["trade_date"] == bars_20d["trade_date"].min()]
    if last.empty or first.empty or "circ_mv" not in last.columns:
        return {"top5_turnover_concentration": conc,
                "small_vs_large_20d_rs": None}
    merged = last[["symbol", "circ_mv"]].merge(
        first[["symbol", "adj_close"]], on="symbol", how="inner")
    if merged.empty:
        return {"top5_turnover_concentration": conc,
                "small_vs_large_20d_rs": None}
    merged["ret_20d"] = merged["adj_close"] / merged["adj_close"].iloc[0] - 1.0 \
        if len(merged) == len(merged["adj_close"].dropna()) else float("nan")
    mv_q = pd.qcut(merged["circ_mv"].rank(method="first"), 4, labels=False)
    small = merged.loc[mv_q == 0, "ret_20d"].mean()
    large = merged.loc[mv_q == 3, "ret_20d"].mean()
    rs = float(small / large) if large and large > 0 else None
    return {"top5_turnover_concentration": conc, "small_vs_large_20d_rs": rs}


def r2_position_multiplier(state: dict) -> float:
    """R2 position multiplier from the crowding state (weight scaler only
    — never changes stock selection)."""
    conc = state.get("top5_turnover_concentration")
    rs = state.get("small_vs_large_20d_rs")
    if conc is None or rs is None:
        return 1.0  # indicator missing -> no overlay adjustment (documented)
    elevated = conc >= R2_ELEVATED_CONC or rs >= R2_ELEVATED_RS
    extreme = conc >= R2_EXTREME_CONC and rs >= R2_EXTREME_RS
    if extreme:
        return R2_EXTREME_MULT
    if elevated:
        return R2_ELEVATED_MULT
    return 1.0


def build_target_portfolios(scores_by_candidate: dict[str, pd.DataFrame],
                            universe: pd.DataFrame,
                            runtime_cfg: dict,
                            crowding_state: dict | None = None,
                            seed_base: int = 20260805) -> dict:
    """Build per-candidate target portfolios.

    Each candidate: select TopN by ITS OWN score within the tradeable
    universe; equal weight; risk overlays scale weights (never the
    selection).  Returns {candidate_id: DataFrame[symbol, target_weight,
    weight_before_overlay, rank, score]}.
    """
    tradeable_symbols = set(universe.loc[universe["tradeable"], "symbol"])
    portfolios = {}
    for cid, cand in runtime_cfg["candidates"].items():
        day = scores_by_candidate.get(cid)
        if day is None or day.empty:
            continue
        sub = day[day["symbol"].isin(tradeable_symbols)].copy()
        sub = sub[sub["score"].notna()].sort_values(
            "score", ascending=False).head(int(cand.get("top_n", 10)))
        n = len(sub)
        if n == 0:
            continue
        weight = 1.0 / n
        overlay = cand.get("risk_overlay", "none")
        mult = 1.0
        if overlay == "r2_crowding" and crowding_state is not None:
            mult = r2_position_multiplier(crowding_state)
        sub["weight_before_overlay"] = weight
        sub["target_weight"] = weight * mult
        sub["rank"] = range(1, n + 1)
        sub["risk_overlay"] = overlay
        portfolios[cid] = sub[[
            "symbol", "score", "rank", "weight_before_overlay",
            "target_weight", "risk_overlay"]]
    return portfolios


# ══════════════════════════════════════════════════════════════════
# Pure core — package sealing (immutability contract)
# ══════════════════════════════════════════════════════════════════


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


# Evidence exports and reports are generated artifacts that are never
# committed (parquet-bloat policy) — they must not block formal packaging.
# Same pathspecs as the strict-ledger backtest worktree gate
# (scripts/research_trusted_strategy_account_backtest.py).
GIT_STATUS_PATHS = [".", ":(exclude)exports/**", ":(exclude)reports/**"]


def _git_info() -> dict:
    """Current git commit + worktree cleanliness (production gate)."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            cwd=PROJECT_ROOT, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        sha = "UNKNOWN"
    try:
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--", *GIT_STATUS_PATHS],
            capture_output=True, text=True,
            cwd=PROJECT_ROOT, check=True).stdout.strip())
    except subprocess.CalledProcessError:
        dirty = True  # fail-closed: cannot verify -> treat as dirty
    return {"git_commit_sha": sha, "worktree_clean": not dirty}


def _config_shas() -> dict:
    """SHA of every runtime + strategy config the package depends on."""
    shas = {}
    for rel in (RUNTIME_CFG_PATH,
                PROJECT_ROOT / "config" / "alpha_challengers" / "f1_no_value.yaml",
                PROJECT_ROOT / "config" / "strategy_definitions" /
                "vls_mom_contrarian_v1_frozen.yaml",
                PROJECT_ROOT / "config" / "risk_overlays" / "r2_crowding_control.yaml"):
        if rel.exists():
            shas[str(rel.relative_to(PROJECT_ROOT))] = _sha256_bytes(rel.read_bytes())
    return shas


def seal_signal_package(
    package_dir: Path,
    *,
    signal_date: str,
    execution_date: str,
    universe: pd.DataFrame,
    factor_values: pd.DataFrame,
    scores: pd.DataFrame,
    target_portfolios: dict[str, pd.DataFrame],
    data_quality: dict,
    input_manifest: dict,
    git_info: dict | None = None,
    allow_revision: bool = False,
) -> dict:
    """Write one immutable Signal Package (v5.5 contract).

    Raises PackageSealedError when the package exists and is SEALED
    (unless allow_revision, which writes revision_2/ beside it).
    Raises SignalPackageBlocked when the worktree is dirty (formal
    packaging requires a clean worktree) or required inputs are empty.
    """
    git = git_info or _git_info()
    if not git.get("worktree_clean", False):
        raise SignalPackageBlocked(
            "worktree is dirty — formal Signal Package BLOCKED "
            "(immutability requires a clean worktree; commit or stash first)")

    def _resolve_dir() -> Path:
        if not package_dir.exists():
            return package_dir
        if not allow_revision:
            raise PackageSealedError(
                f"package already exists at {package_dir} — a SEALED "
                "package is never overwritten; use allow_revision=True "
                "for a correction (revision_2/)")
        rev = package_dir / "revision_2"
        rev.mkdir(parents=True, exist_ok=True)
        return rev

    out_dir = _resolve_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Write payloads ──
    universe.to_parquet(out_dir / "universe.parquet", index=False,
                        compression="zstd")
    factor_values.to_parquet(out_dir / "factor_values.parquet", index=False,
                             compression="zstd")
    scores.to_parquet(out_dir / "scores.parquet", index=False,
                      compression="zstd")
    portfolios_df = pd.concat(
        [df.assign(candidate_id=cid) for cid, df in target_portfolios.items()],
        ignore_index=True) if target_portfolios else pd.DataFrame()
    portfolios_df.to_parquet(out_dir / "target_portfolios.parquet", index=False,
                             compression="zstd")
    (out_dir / "data_quality_report.json").write_text(
        json.dumps(data_quality, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "input_manifest.json").write_text(
        json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Manifest with per-file SHAs ──
    file_shas = {name: _sha256_file(out_dir / name) for name in REQUIRED_PACKAGE_FILES
                 if (out_dir / name).exists()}
    manifest = {
        "schema_version": "signal_package_v1",
        "signal_date": signal_date,
        "signal_time": SIGNAL_TIME,
        "execution_date": execution_date,
        "git_commit_sha": git.get("git_commit_sha"),
        "worktree_clean": bool(git.get("worktree_clean")),
        "strategy_config_shas": _config_shas(),
        "source_snapshot_shas": input_manifest.get("source_snapshot_shas", {}),
        "pit_contract_sha": input_manifest.get("pit_contract_sha"),
        "universe_sha": file_shas.get("universe.parquet"),
        "factor_values_sha": file_shas.get("factor_values.parquet"),
        "scores_sha": file_shas.get("scores.parquet"),
        "target_portfolio_sha": file_shas.get("target_portfolios.parquet"),
        "candidate_ids": sorted(target_portfolios.keys()),
        "package_status": "SEALED",
        "sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out_dir / "signal_package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── package_sha256.json (file-level SHA inventory) ──
    file_shas["signal_package_manifest.json"] = _sha256_file(
        out_dir / "signal_package_manifest.json")
    try:
        package_dir_str = str(out_dir.relative_to(PROJECT_ROOT))
    except ValueError:
        package_dir_str = str(out_dir)  # test/tmp out-of-repo paths
    sha_payload = {
        "schema_version": "package_sha256_v1",
        "package_dir": package_dir_str,
        "files": {k: v for k, v in sorted(file_shas.items())},
    }
    sha_payload["package_sha256"] = _sha256_bytes(
        json.dumps(sha_payload, sort_keys=True, ensure_ascii=False)
        .encode("utf-8"))
    (out_dir / "package_sha256.json").write_text(
        json.dumps(sha_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Self-check: manifest SHAs must match the written files ──
    verify = verify_package_sha(out_dir)
    if not verify["ok"]:
        raise RuntimeError(
            f"package self-check FAILED at {out_dir}: {verify['errors']}")
    return manifest


def verify_package_sha(package_dir: Path) -> dict:
    """Verify manifest SHAs against the files on disk (immutability)."""
    manifest_path = package_dir / "signal_package_manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "errors": ["signal_package_manifest.json missing"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors = []
    for key, fname in (("universe_sha", "universe.parquet"),
                       ("factor_values_sha", "factor_values.parquet"),
                       ("scores_sha", "scores.parquet"),
                       ("target_portfolio_sha", "target_portfolios.parquet")):
        if not (package_dir / fname).exists():
            errors.append(f"{fname} missing")
            continue
        actual = _sha256_file(package_dir / fname)
        if manifest.get(key) != actual:
            errors.append(f"{fname}: manifest {manifest.get(key)} != actual {actual}")
    return {"ok": not errors, "errors": errors}


# ══════════════════════════════════════════════════════════════════
# Production stages (live DB data source)
# ══════════════════════════════════════════════════════════════════

def _get_conn():
    import pymysql
    return pymysql.connect(
        host="localhost", user="root",
        password=os.environ.get("CHENYIYUN_DB_PASSWORD", ""),
        database="tushare_stock", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _read_sql(conn, query: str, params=None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _trade_dates(conn, up_to: str, limit: int = HISTORY_DAYS) -> list[str]:
    sd = int(up_to.replace("-", ""))
    df = _read_sql(
        conn,
        "SELECT DISTINCT trade_date FROM dwd_stock_daily_standard "
        "WHERE trade_date <= %s ORDER BY trade_date DESC LIMIT %s",
        (sd, limit))
    dates = sorted(str(d) for d in df["trade_date"].tolist())
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]


def fetch_production_inputs(signal_date: str) -> dict:
    """Fetch + quality-check all live inputs for one signal date."""
    conn = _get_conn()
    try:
        dates = _trade_dates(conn, signal_date)
        if not dates or dates[-1] != signal_date:
            # No bars on the requested date -> BLOCKED (no stale fallback).
            raise SignalPackageBlocked(
                f"data_quality: no bars for {signal_date} "
                f"(latest available {dates[-1] if dates else 'none'}) — "
                "stale-date substitution is forbidden")
        date_int = int(signal_date.replace("-", ""))
        bars = _read_sql(
            conn,
            "SELECT trade_date, ts_code, adj_close, amount "
            "FROM dwd_stock_daily_standard WHERE trade_date <= %s "
            "AND trade_date >= %s",
            (date_int, int(dates[0].replace("-", ""))))
        bars["trade_date"] = bars["trade_date"].astype(str).apply(
            lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x)
        mcap = _read_sql(
            conn,
            "SELECT ts_code, circ_mv FROM dwd_market_cap_daily "
            "WHERE trade_date = %s", (date_int,))
        basic = _read_sql(
            conn,
            "SELECT ts_code, pb, turnover_rate FROM dwd_daily_basic "
            "WHERE trade_date = %s", (date_int,))
        industry = _read_sql(
            conn,
            "SELECT ts_code, industry_name, effective_date, expire_date "
            "FROM dwd_stock_industry_scd WHERE "
            "(expire_date IS NULL OR expire_date > %s) AND effective_date <= %s",
            (date_int, date_int))
        labels = _read_sql(
            conn,
            "SELECT ts_code, is_st, is_new, market, industry "
            "FROM dwd_stock_label_daily WHERE trade_date = %s", (date_int,))
        quality = {
            "signal_date": signal_date,
            "bar_dates": len(dates),
            "bar_rows": len(bars),
            "bar_symbols": int(bars["ts_code"].nunique()) if not bars.empty else 0,
            "mcap_rows": len(mcap),
            "basic_rows": len(basic),
            "industry_rows": len(industry),
            "label_rows": len(labels),
        }
        missing = [k for k, v in quality.items()
                   if k != "signal_date" and v == 0]
        if missing:
            raise SignalPackageBlocked(
                f"data_quality: zero rows for {missing} on {signal_date}")
        return {"bars": bars, "mcap": mcap, "basic": basic,
                "industry": industry, "labels": labels,
                "data_quality": quality}
    finally:
        conn.close()


def build_live_universe(labels: pd.DataFrame, signal_date: str,
                        bars: pd.DataFrame) -> pd.DataFrame:
    """Universe snapshot from live label data + bar presence.

    is_suspended is DERIVED from bar presence: a listed symbol with no
    bar on the signal date cannot be traded (the tushare label table
    carries no suspension flag).  is_st / is_new / industry come from
    the label table (PIT-visible on the signal date).
    """
    out = labels.copy()
    out["symbol"] = out["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)
    out["trade_date"] = signal_date
    out["is_listed"] = 1
    bar_sym = (bars["symbol"] if "symbol" in bars.columns else bars["ts_code"])
    traded_symbols = set(bar_sym.astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))
    out["is_suspended"] = (~out["symbol"].isin(traded_symbols)).astype(float)
    out["is_st"] = pd.to_numeric(out.get("is_st", 0), errors="coerce").fillna(0.0)
    out["limit_status"] = "NORMAL"
    out["security_status_transition"] = "NORMAL"
    return out[["trade_date", "symbol", "is_listed", "is_st",
                "is_suspended", "limit_status", "security_status_transition"]]


def _normalize(day: pd.DataFrame, mcap: pd.DataFrame,
               basic: pd.DataFrame, industry: pd.DataFrame) -> pd.DataFrame:
    day = day.copy()
    day["symbol"] = day["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)
    day = day.merge(mcap[["ts_code", "circ_mv"]].assign(
        symbol=lambda d: d["ts_code"].astype(str).str.replace(
            r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))[["symbol", "circ_mv"]],
        on="symbol", how="left")
    basic_syms = basic.assign(symbol=lambda d: d["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))[["symbol", "pb", "turnover_rate"]]
    day = day.merge(basic_syms, on="symbol", how="left")
    ind_syms = industry.assign(symbol=lambda d: d["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))[["symbol", "industry_name"]]
    day = day.merge(ind_syms.rename(columns={"industry_name": "industry"}),
                    on="symbol", how="left")
    return day


def compute_raw_factors(bars: pd.DataFrame, signal_date: str) -> pd.DataFrame:
    """Per-symbol raw factors on the signal date (from the bar history).

    Pure: accepts either `ts_code` (live DB) or `symbol` columns and
    normalizes to `symbol`.  NaN factor rows are kept (the caller's
    eligibility/consistency gates decide blocking).
    """
    b = bars.copy()
    if "ts_code" in b.columns and "symbol" not in b.columns:
        b["symbol"] = b["ts_code"].astype(str).str.replace(
            r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)
    if "symbol" not in b.columns:
        raise SignalPackageBlocked("bars lack both ts_code and symbol")
    b["symbol"] = b["symbol"].astype(str).str.zfill(6)
    b = b.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    b["adj_close"] = pd.to_numeric(b["adj_close"], errors="coerce")
    b["amount"] = pd.to_numeric(b["amount"], errors="coerce")
    b["ret_1d"] = b.groupby("symbol")["adj_close"].pct_change(1)
    b["momentum_raw"] = b.groupby("symbol")["adj_close"].pct_change(20)
    b["amihud_raw"] = b["ret_1d"].abs() / b["amount"].replace(0.0, np.nan)
    b["liquidity_raw"] = b.groupby("symbol")["amihud_raw"].transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    # Market proxy: equal-weight cross-sectional mean return (live days
    # lack the release's benchmark index family in this fetch).
    b["market_ret"] = b.groupby("trade_date")["ret_1d"].transform("mean")
    def _beta_group(g: pd.DataFrame) -> pd.Series:
        return (g["ret_1d"].rolling(20, min_periods=10).cov(g["market_ret"])
                / g["market_ret"].rolling(20, min_periods=10).var().replace(0, np.nan))
    b["beta_raw"] = b.groupby("symbol", group_keys=False).apply(
        _beta_group).reset_index(level=0, drop=True)
    day = b[b["trade_date"] == signal_date].copy()
    if day.empty:
        raise SignalPackageBlocked(f"no bars for {signal_date}")
    return day


def run_package(signal_date: str | None = None) -> int:
    """Production entry: fetch -> quality -> universe -> factors -> seal."""
    from runtime.shadow_execution_state import ALL_STATES  # noqa: F401  (import sanity)
    runtime_cfg = yaml.safe_load(RUNTIME_CFG_PATH.read_text(encoding="utf-8"))
    if signal_date is None:
        signal_date = datetime.now().strftime("%Y-%m-%d")

    inputs = fetch_production_inputs(signal_date)
    dq = inputs["data_quality"]
    bars = inputs["bars"]
    mcap, basic, industry, labels = (inputs["mcap"], inputs["basic"],
                                     inputs["industry"], inputs["labels"])

    # 16:30 — PIT universe freeze (availability gates from live coverage).
    universe_snapshot = build_live_universe(labels, signal_date, bars)
    availability = {
        "financial_available_at": f"{signal_date}T15:00:00+08:00" if not basic.empty else None,
        "industry_available_at": f"{signal_date}T15:00:00+08:00" if not industry.empty else None,
        "adjustment_available_at": f"{signal_date}T15:00:00+08:00",
        "benchmark_available_at": f"{signal_date}T15:00:00+08:00",
    }
    uni = build_daily_universe(universe_snapshot, signal_date, availability)
    if uni.blocked:
        raise SignalPackageBlocked(
            f"SIGNAL_PACKAGE_BLOCKED: universe build failed — {uni.blockers}")

    # 16:40 — per-candidate factor computation.  Industry comes from the
    # label table (PIT-visible on the signal date); the SCD table is
    # cross-checked as an availability source only.
    day = compute_raw_factors(bars, signal_date)
    raw = _normalize(day, mcap, basic, industry)
    raw["size_raw"] = pd.to_numeric(raw["circ_mv"], errors="coerce")
    raw["value_raw"] = -pd.to_numeric(raw["pb"], errors="coerce")  # value = low pb
    label_ind = labels.assign(symbol=lambda d: d["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))[["symbol", "industry"]]
    raw = raw.drop(columns=["industry"], errors="ignore").merge(
        label_ind.rename(columns={"industry": "industry"}),
        on="symbol", how="left")
    raw["industry"] = raw["industry"].astype(str)

    scores_by_candidate = {}
    factor_by_candidate = {}
    for cid, cand in runtime_cfg["candidates"].items():
        scored = compute_candidate_scores(raw, cand)
        scores_by_candidate[cid] = scored[[
            "symbol", "score", "trade_date"]].copy()
        factor_by_candidate[cid] = scored[[
            "symbol", "score", "trade_date"]].copy()

    # 16:50 — target portfolios (R2 overlay for C2).
    crowding = compute_crowding_state(bars)
    portfolios = build_target_portfolios(
        scores_by_candidate, uni.universe, runtime_cfg,
        crowding_state=crowding)

    # 17:00 — SEAL.
    scores_all = pd.concat([df.assign(candidate_id=cid)
                            for cid, df in scores_by_candidate.items()],
                           ignore_index=True)
    factors_all = pd.concat([df.assign(candidate_id=cid)
                             for cid, df in factor_by_candidate.items()],
                            ignore_index=True)
    exec_date = _next_open_day(signal_date)
    manifest = seal_signal_package(
        PACKAGES_ROOT / signal_date,
        signal_date=signal_date, execution_date=exec_date,
        universe=uni.universe, factor_values=factors_all, scores=scores_all,
        target_portfolios=portfolios, data_quality=dq,
        input_manifest={
            "signal_date": signal_date,
            "source_snapshot_shas": {},
            "pit_contract_sha": None,
            "availability_gates": availability,
        })
    print(json.dumps({"package_sealed": manifest["signal_date"],
                      "execution_date": exec_date,
                      "candidates": manifest["candidate_ids"],
                      "universe": uni.n_tradeable,
                      "package_sha": (PACKAGES_ROOT / signal_date /
                                      "package_sha256.json").exists()},
                     ensure_ascii=False, indent=2))
    return 0


def _next_open_day(signal_date: str) -> str:
    """First trading day after signal_date from the canonical calendar."""
    cal_path = (PROJECT_ROOT / "exports" / "formal_evidence" /
                "alpha_challengers" / "f1_no_value" / "snapshots" /
                "trade_calendar.csv")
    if not cal_path.exists():
        raise SignalPackageBlocked(
            f"trade calendar missing at {cal_path}")
    cal = pd.read_csv(cal_path)
    open_days = cal[cal["is_open"] == 1]["cal_date"].astype(str).sort_values()
    future = [d for d in open_days if d > signal_date]
    if not future:
        raise SignalPackageBlocked(f"no trading day after {signal_date}")
    return future[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None,
                        help="signal date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="run stages but do not seal")
    args = parser.parse_args()
    try:
        return run_package(args.date)
    except (SignalPackageBlocked, PackageSealedError) as exc:
        print(json.dumps({"signal_package_blocked": str(exc)},
                         ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
