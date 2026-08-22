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
import uuid
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

# A-share PIT availability conventions (v5.5.3) — the TIME part of each
# *_available_at gate.  Documented in
# config/pit_semantics/ashare_pit_semantics_v1.yaml family semantics and
# implemented identically by scripts/pit/run_snapshot_extract.py
# (financial T08:00, industry T09:00, adjustment T08:00, benchmark/market
# T15:30).  The DATE part always comes from REAL DB fields of actually
# fetched rows (ann_date / effective_date / trade_date) — the convention
# only supplies the intraday knowability time, and the source of every
# gate is recorded in the manifest lineage (available_at_source).
AVAILABLE_AT_CONVENTIONS = {
    "financial": "T08:00:00+08:00",   # announcement-date convention
    "industry": "T09:00:00+08:00",    # SCD effective-date convention
    "adjustment": "T08:00:00+08:00",  # adj-factor daily convention
    "benchmark": "T15:30:00+08:00",   # index-close convention (= cutoff)
    "market": "T15:30:00+08:00",      # bar-close convention (= cutoff)
}

# Real limit_status mapping (tushare dwd_stock_label_daily.limit_type):
# 10 = 10% daily limit (main board), 20 = 20% (ChiNext/STAR).  Unknown
# values pass through as their numeric string; NaN stays NaN and the
# universe contract BLOCKS (status_source_missing:limit_status).
LIMIT_STATUS_MAP = {10: "NORMAL", 20: "20PCT"}

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

R2_OVERLAY_YAML = PROJECT_ROOT / "config" / "risk_overlays" / "r2_crowding_control.yaml"


def _verify_r2_thresholds() -> None:
    """Drift guard (v5.5.1): Python constants must match the pre-registered
    YAML contract (config/risk_overlays/r2_crowding_control.yaml).  The two
    copies are independent by design; this check fails loudly at import if
    they ever diverge."""
    import re
    cfg = yaml.safe_load(R2_OVERLAY_YAML.read_text(encoding="utf-8"))
    rules = {r["id"]: r for r in cfg.get("rules", [])}
    yaml_numbers = []
    for rid in ("crowding_elevated", "crowding_extreme"):
        yaml_numbers += [float(x) for x in
                         re.findall(r"\d+\.\d+", rules[rid]["condition"])]
        yaml_numbers.append(float(rules[rid]["position_multiplier"]))
    expected = sorted([R2_ELEVATED_CONC, R2_ELEVATED_RS,
                       R2_EXTREME_CONC, R2_EXTREME_RS,
                       R2_ELEVATED_MULT, R2_EXTREME_MULT])
    if sorted(yaml_numbers) != expected:
        raise SignalPackageBlocked(
            "SIGNAL_PACKAGE_BLOCKED: r2_crowding_control.yaml thresholds "
            f"drifted from Python constants (yaml={sorted(yaml_numbers)}, "
            f"code={expected}) — edit one source of truth only")


class PackageSealedError(RuntimeError):
    """A SEALED package already exists at the target directory."""


class SignalPackageBlocked(RuntimeError):
    """A required input is missing — the package must not be produced."""


_verify_r2_thresholds()


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
        score += float(weight) * sign * factor_ranks[factor]
    # v5.5.1: missing factors are NEVER imputed as zero.  A symbol whose
    # rank is NaN on ANY required factor gets score NaN — it drops out of
    # selection rather than being silently scored as if the factor were
    # neutral.  The package-level missing-rate gate lives in run_package.
    missing_mask = pd.Series(False, index=out.index)
    for factor in candidate.get("factor_weights", {}):
        missing_mask |= factor_ranks[factor].isna()
    score[missing_mask] = np.nan
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
    Rows with missing OR non-finite inputs (incl. inf — the 2026-08-04
    STAR/BSE-listing cohort has no liquidity_raw/beta_raw history and must
    not pollute the fit) are dropped from the fit and keep NaN residual
    (never zero-filled).  Style columns are z-scored before the fit: the
    projection span is unchanged by a linear transform, so residuals are
    identical (to float rounding) whenever the design is full column rank
    in both scalings.  On near-degenerate designs the OLD raw-scale fit
    truncated a singular value below rcond*max (observed 2026-08-04:
    rank 112/113, ratio 7.6e-14) and returned a non-OLS minimum-norm
    solution; the z-scored full-rank fit is the contract's unique OLS.
    The z-score also removes the ill-conditioned lstsq / matmul overflow
    of extreme scale ratios (circ_mv 1e4..1e8).  Missing style/industry
    inputs raise (fail-closed).
    """
    styles = candidate.get("residualization", {}).get("style_factors", [])
    with_industry = candidate.get("residualization", {}).get(
        "industry_fixed_effects", True)
    min_cs = int(candidate.get("residualization", {}).get(
        "minimum_cross_section", 20))
    src_map = {"size": "size_raw", "liquidity": "liquidity_raw",
               "market_beta": "beta_raw"}
    need = [src_map[s] for s in styles if s in src_map]
    # copy, not alias: need gets "industry" appended below and must not
    # leak into the style design matrix / isfinite mask
    style_cols = list(need)
    if with_industry:
        need.append("industry")
    missing = [c for c in need if c not in day.columns]
    if missing:
        raise SignalPackageBlocked(
            f"{candidate.get('challenger_id')}: residualization requires "
            f"{missing} — SIGNAL_PACKAGE_BLOCKED")

    out = pd.Series(np.nan, index=day.index, dtype=float)
    # isfinite (not just notna): a single inf input would otherwise poison
    # lstsq and NaN every residual on the day.
    num = day[["score"] + style_cols].apply(
        lambda s: np.isfinite(pd.to_numeric(s, errors="coerce")))
    mask = num.all(axis=1)
    if with_industry:
        mask &= day["industry"].notna()
    sub = day[mask]
    if len(sub) < min_cs:
        # v5.5.3 (A4): the cross-section is below the pre-registered
        # minimum — C3_BLOCKED, never a silent all-NaN day (the
        # diagnostic layer already blocked this; production must too).
        raise SignalPackageBlocked(
            f"C3_BLOCKED: {candidate.get('challenger_id')} cross-section "
            f"{len(sub)} < minimum_cross_section={min_cs} — residual "
            "design is not computable; no silent NaN day")
    if style_cols:
        x_style = sub[style_cols].to_numpy(dtype=float)
        mu, sd = x_style.mean(axis=0), x_style.std(axis=0)
        sd[sd == 0] = 1.0  # constant column — leave as-is (span unchanged)
        x_style = (x_style - mu) / sd
    else:
        x_style = np.zeros((len(sub), 0))
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
    # v5.5.3 (A4): a rank-deficient design has no unique OLS — the old
    # code returned a minimum-norm lstsq solution silently (observed
    # 2026-08-04: rank 112/113).  That is C3_BLOCKED, never a "best
    # effort" residual: the diagnostic layer already blocks; production
    # must too (with industry fixed effects a full-rank check is exact).
    if design.shape[1] > 0 and np.linalg.matrix_rank(design) < design.shape[1]:
        raise SignalPackageBlocked(
            f"C3_BLOCKED: {candidate.get('challenger_id')} residual design "
            f"is rank-deficient ({np.linalg.matrix_rank(design)}/"
            f"{design.shape[1]}) — no unique OLS, no silent minimum-norm fit")
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    # np.dot, not `design @ beta`: on this build (numpy 2.2.6 + Accelerate)
    # matmul's kernel emits spurious divide-by-zero/overflow warnings on
    # benign float64 data (reproduced with pure standard-normal inputs);
    # np.dot is numerically identical and warning-clean — genuine overflow
    # in the data still warns.
    resid = y - np.dot(design, beta)
    out.loc[mask] = resid
    return out


def compute_crowding_state(bars_20d: pd.DataFrame) -> dict:
    """R2 crowding indicators from up to 20d of raw bars (v5.5.1 rewrite).

    top5_turnover_concentration : share of total amount in the top 5%
                                  symbols by amount (latest day).
    small_vs_large_20d_rs       : 20d cum return of the bottom circ_mv
                                  quartile / top circ_mv quartile — per
                                  symbol PAIRED first/last close.

    v5.5.1 fixes (three defects confirmed 2026-08-04):
      1. bars had no circ_mv column in production -> rs was ALWAYS None.
         fetch_production_inputs now merges the full-window circ_mv.
      2. ret_20d was a cross-sectional price ratio
         (adj_close / adj_close.iloc[0] — the FIRST SYMBOL's price, not
         each symbol's own first close).  Now groupby-symbol first/last.
      3. the old test fed single-day data, which could never exercise the
         20d path.

    Fail-closed contract:
      - empty bars or < 2 symbols -> blocked: True (never fabricate a
        single-symbol 100% concentration value).
      - circ_mv missing on the latest day -> blocked: True (v5.5.3 A4:
        rs is REQUIRED for the R2 overlay; a missing input is
        R2_INPUT_MISSING, never a silent partial state that lets the
        overlay degrade to 1.0).
      - < 20d of history is DEGRADED, not blocked: short_history: True
        marks the row so consumers can decide.
    """
    if bars_20d.empty:
        return {"top5_turnover_concentration": None,
                "small_vs_large_20d_rs": None,
                "blocked": True, "block_reason": "empty_bars",
                "history_days": 0, "short_history": False}
    bars = bars_20d.copy()
    # Live fetch returns ts_code only (no symbol column) — normalize like
    # compute_raw_factors so the production shape works identically.
    if "ts_code" in bars.columns and "symbol" not in bars.columns:
        bars["symbol"] = bars["ts_code"].astype(str).str.replace(
            r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)
    if "symbol" not in bars.columns:
        return {"top5_turnover_concentration": None,
                "small_vs_large_20d_rs": None,
                "blocked": True, "block_reason": "no_symbol_column",
                "history_days": 0, "short_history": False}
    bars["symbol"] = bars["symbol"].astype(str).str.zfill(6)
    if bars["symbol"].dropna().nunique() < 2:
        return {"top5_turnover_concentration": None,
                "small_vs_large_20d_rs": None,
                "blocked": True, "block_reason": "less_than_2_symbols",
                "history_days": int(bars["trade_date"].nunique()),
                "short_history": False}
    history_days = int(bars["trade_date"].nunique())
    short_history = history_days < 20

    latest_date = bars["trade_date"].max()
    latest = bars[bars["trade_date"] == latest_date].copy()
    latest["amount"] = pd.to_numeric(latest["amount"], errors="coerce").fillna(0.0)
    total_amount = float(latest["amount"].sum())
    if total_amount <= 0:
        conc = None
    else:
        n_top5 = max(1, int(np.ceil(len(latest) * 0.05)))
        conc = float(latest["amount"].nlargest(n_top5).sum() / total_amount)

    base = {"top5_turnover_concentration": conc,
            "small_vs_large_20d_rs": None,
            "blocked": False, "block_reason": None,
            "history_days": history_days, "short_history": short_history}
    if "circ_mv" not in latest.columns:
        # v5.5.3 A4: rs cannot be computed -> the R2 overlay's required
        # input is missing.  blocked: True so the package gate refuses
        # instead of silently using concentration alone.
        return {**base, "blocked": True,
                "block_reason": "circ_mv_missing"}

    ordered = bars.sort_values(["symbol", "trade_date"])
    first_close = pd.to_numeric(
        ordered.groupby("symbol")["adj_close"].first(), errors="coerce")
    last_close = pd.to_numeric(
        ordered.groupby("symbol")["adj_close"].last(), errors="coerce")
    returns_20d = last_close / first_close - 1.0
    size_map = pd.to_numeric(
        latest.set_index("symbol")["circ_mv"], errors="coerce")
    ret_with_size = returns_20d.to_frame("ret_20d").join(size_map, how="inner")
    ret_with_size = ret_with_size.replace([np.inf, -np.inf], np.nan).dropna()
    if ret_with_size.empty:
        return {**base, "blocked": True,
                "block_reason": "no_valid_return_size_pairs"}
    size_q = pd.qcut(ret_with_size["circ_mv"].rank(method="first"), 4,
                     labels=False)
    small = ret_with_size.loc[size_q == 0, "ret_20d"].mean()
    large = ret_with_size.loc[size_q == 3, "ret_20d"].mean()
    rs = float(small / large) if large and large > 0 else None
    return {**base, "small_vs_large_20d_rs": rs}


_R2_RULES_CACHE: list[dict] | None = None


def _load_r2_rules() -> list[dict]:
    """Pre-registered R2 rules from config/risk_overlays/r2_crowding_control.yaml.

    Loaded once per process (the file is immutable evidence — never
    expected to change at runtime).  A missing/unparseable rules file
    raises: the overlay must NEVER run on guessed thresholds.
    """
    global _R2_RULES_CACHE
    if _R2_RULES_CACHE is None:
        if not R2_OVERLAY_YAML.exists():
            raise SignalPackageBlocked(
                "SIGNAL_PACKAGE_BLOCKED: R2 rules file missing at "
                f"{R2_OVERLAY_YAML.name} — the overlay refuses to run "
                "without its pre-registered thresholds")
        doc = yaml.safe_load(R2_OVERLAY_YAML.read_text(encoding="utf-8"))
        _R2_RULES_CACHE = list((doc or {}).get("rules") or [])
        if not _R2_RULES_CACHE:
            raise SignalPackageBlocked(
                "SIGNAL_PACKAGE_BLOCKED: R2 rules file has no rules — "
                "the overlay refuses to run with an empty threshold set")
    return _R2_RULES_CACHE


def r2_position_multiplier(state: dict) -> float:
    """R2 position multiplier from the crowding state (weight scaler only
    — never changes stock selection).

    v5.5.3 (A4): the PRODUCTION path shares the diagnostic layer's
    fail-closed gate ``resolve_r2_state`` — a missing crowding input is
    R2_INPUT_MISSING and BLOCKS the package (the old default-1.0 silently
    dropped the overlay; the diagnostic side already blocked).  The
    multiplier is the diagnostic layer's resolution, never a local copy
    of the thresholds.
    """
    from runtime.alpha_candidate_diagnostics import R2_INPUT_MISSING, resolve_r2_state
    resolved = resolve_r2_state(
        state.get("top5_turnover_concentration"),
        state.get("small_vs_large_20d_rs"),
        _load_r2_rules())
    if resolved["blocked"] == R2_INPUT_MISSING:
        raise SignalPackageBlocked(
            f"SIGNAL_PACKAGE_BLOCKED: R2_INPUT_MISSING — "
            f"{resolved['reason']} (no default-1.0 fallback)")
    return float(resolved["position_multiplier"])


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
            if crowding_state.get("blocked"):
                # v5.5.3 A4: a blocked crowding state (e.g. circ_mv
                # missing) must NEVER degrade to "no overlay adjustment"
                # for an R2 candidate — fail closed at the point of use,
                # not only at the run_package call site.
                raise SignalPackageBlocked(
                    f"SIGNAL_PACKAGE_BLOCKED: crowding state unavailable "
                    f"({crowding_state.get('block_reason')}) — R2 overlay "
                    f"cannot be computed; no default-normal fallback")
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
GIT_STATUS_PATHS = [
    ".",
    ":(exclude)exports/**",
    ":(exclude)reports/**",
    ":(exclude)data/pit/**",
    ":(exclude)data/pit",
    ":(exclude)logs/score_backfill/**",
    ":(exclude)logs/score_backfill",
    ":(exclude)logs/web/**",
    ":(exclude)logs/web",
    ":(exclude)sina/bs_detection/SinaAppBS/**",
]


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
    expected_sha = os.environ.get("CHENYIYUN_RELEASE_SHA", "").strip().lower()
    release_sha_match = not expected_sha or sha.lower() == expected_sha
    runtime_release_id = str(
        os.environ.get("CHENYIYUN_RUNTIME_RELEASE_ID")
        or os.environ.get("CHENYIYUN_RELEASE_ID")
        or ""
    ).strip()
    return {
        "git_commit_sha": sha,
        "worktree_clean": not dirty and release_sha_match,
        "release_sha_match": release_sha_match,
        "runtime_release_id": runtime_release_id,
        "release_root": str(PROJECT_ROOT),
    }


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


def next_revision_dir(package_dir: Path) -> Path:
    """Next free revision_N/ (v5.5.1): the original SEALED package is
    revision_1 (the root), corrections are revision_2, revision_3, ...
    Never overwrites an existing revision."""
    existing = []
    for p in package_dir.glob("revision_*"):
        if p.is_dir() and p.name.startswith("revision_"):
            suffix = p.name.split("_", 1)[1]
            if suffix.isdigit():
                existing.append(int(suffix))
    return package_dir / f"revision_{(max(existing) + 1) if existing else 2}"


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
    revision_reason: str | None = None,
) -> dict:
    """Write one immutable Signal Package (v5.5 / v5.5.1 contract).

    Raises PackageSealedError when the package exists and is SEALED
    (unless allow_revision, which writes the next revision_N/ beside it —
    never a hardcoded revision_2).
    Raises SignalPackageBlocked when the worktree is dirty (formal
    packaging requires a clean worktree) or required inputs are empty.

    v5.5.1 atomicity: everything is written into a fresh staging dir and
    moved to the target with a single os.rename (same filesystem).  Any
    failure leaves NO partially-written package behind — staging is
    removed and the error surfaces as SIGNAL_PACKAGE_BLOCKED.
    """
    git = git_info or _git_info()
    if not git.get("worktree_clean", False):
        raise SignalPackageBlocked(
            "worktree is dirty — formal Signal Package BLOCKED "
            "(immutability requires a clean worktree; commit or stash first)")
    if git.get("release_sha_match") is False:
        raise SignalPackageBlocked(
            "release commit mismatch — formal Signal Package BLOCKED "
            "(worker must execute the published release checkout)")
    if os.environ.get("CHENYIYUN_REQUIRE_RELEASE") == "1" and not str(
        git.get("runtime_release_id") or ""
    ).strip():
        raise SignalPackageBlocked(
            "runtime release identity missing — formal Signal Package BLOCKED")

    if package_dir.exists():
        if not allow_revision:
            raise PackageSealedError(
                f"package already exists at {package_dir} — a SEALED "
                "package is never overwritten; use allow_revision=True "
                "for a correction (next revision_N/)")
        out_dir = next_revision_dir(package_dir)
        revision_n = int(out_dir.name.split("_", 1)[1])
    else:
        out_dir = package_dir
        revision_n = 1

    staging = out_dir.parent / ".staging" / \
        f"{out_dir.name}-{uuid.uuid4().hex[:8]}"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=False)  # fresh staging only

    try:
        _write_package_payloads(
            staging, final_dir=out_dir,
            signal_date=signal_date, execution_date=execution_date,
            universe=universe, factor_values=factor_values, scores=scores,
            target_portfolios=target_portfolios, data_quality=data_quality,
            input_manifest=input_manifest, git=git,
            revision_n=revision_n, revision_reason=revision_reason,
            parent_package_sha=(_package_sha_of(package_dir)
                                if revision_n > 1 else None),
        )
        # ── fsync every staged file, then atomically rename the dir ──
        for f in staging.iterdir():
            if f.is_file():
                fd = os.open(str(f), os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        try:
            os.rename(str(staging), str(out_dir))
        except OSError as exc:
            raise SignalPackageBlocked(
                f"SIGNAL_PACKAGE_BLOCKED: atomic rename of staged package "
                f"to {out_dir} failed: {exc} — target may already exist")
        staging = None  # renamed away; nothing to clean up
    finally:
        if staging is not None and staging.exists():
            import shutil
            shutil.rmtree(staging, ignore_errors=True)

    # ── Self-check against the FINAL location (immutability) ──
    verify = verify_package_sha(out_dir)
    if not verify["ok"]:
        raise RuntimeError(
            f"package self-check FAILED at {out_dir}: {verify['errors']}")
    return json.loads((out_dir / "signal_package_manifest.json")
                      .read_text(encoding="utf-8"))


def _package_sha_of(package_dir: Path) -> str | None:
    """The root package's package_sha256 (parent identity for revisions)."""
    root_sha = package_dir / "package_sha256.json"
    if not root_sha.exists():
        return None
    try:
        return str(json.loads(root_sha.read_text(encoding="utf-8"))
                   .get("package_sha256"))
    except (ValueError, OSError):
        return None


def _write_package_payloads(
    out_dir: Path,
    *,
    final_dir: Path,
    signal_date: str,
    execution_date: str,
    universe: pd.DataFrame,
    factor_values: pd.DataFrame,
    scores: pd.DataFrame,
    target_portfolios: dict[str, pd.DataFrame],
    data_quality: dict,
    input_manifest: dict,
    git: dict,
    revision_n: int,
    revision_reason: str | None,
    parent_package_sha: str | None,
) -> None:
    """Write all payloads into a staging dir (sealed by the caller)."""
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
        "revision": revision_n,               # 1 = original SEALED package
        "parent_package_sha256": parent_package_sha,
        "git_commit_sha": git.get("git_commit_sha"),
        "worktree_clean": bool(git.get("worktree_clean")),
        "release_id": git.get("runtime_release_id") or None,
        "runtime_release_id": git.get("runtime_release_id") or None,
        "release_root": git.get("release_root") or str(PROJECT_ROOT),
        "strategy_config_shas": _config_shas(),
        "source_snapshot_shas": input_manifest.get("source_snapshot_shas", {}),
        "pit_contract_sha": input_manifest.get("pit_contract_sha"),
        "universe_sha": file_shas.get("universe.parquet"),
        "factor_values_sha": file_shas.get("factor_values.parquet"),
        "scores_sha": file_shas.get("scores.parquet"),
        "target_portfolio_sha": file_shas.get("target_portfolios.parquet"),
        "candidate_ids": sorted(target_portfolios.keys()),
        "package_status": "SEALED",
        "revision_reason": revision_reason,
        "sealed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out_dir / "signal_package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── package_sha256.json (file-level SHA inventory) ──
    # The record binds the FINAL immutable location (final_dir), not the
    # staging path — the staging dir is renamed away and must never appear
    # in the package's own identity record.
    file_shas["signal_package_manifest.json"] = _sha256_file(
        out_dir / "signal_package_manifest.json")
    try:
        package_dir_str = str(final_dir.relative_to(PROJECT_ROOT))
    except ValueError:
        package_dir_str = str(final_dir)  # test/tmp out-of-repo paths
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

# Required lineage families — each must be present with a real content SHA
# and a non-zero row count (v5.5.1: any missing family BLOCKS the package).
# v5.5.3 adds adjustment/benchmark_index (real data backing the gates) and
# status_scd/dim_stock (real universe status sources).
REQUIRED_LINEAGE_FAMILIES = ("market", "market_cap", "basic_financial",
                             "industry_scd", "labels", "trade_calendar",
                             "adjustment", "benchmark_index",
                             "status_scd", "dim_stock")


def _pit_contract_sha() -> str:
    """SHA of the canonical PIT semantic contract (real identity, never a
    placeholder — v5.5.1)."""
    from runtime.pit_semantic_contract import CONTRACT_PATH
    if not CONTRACT_PATH.exists():
        raise SignalPackageBlocked(
            "SIGNAL_PACKAGE_BLOCKED: pit semantic contract missing at "
            f"{CONTRACT_PATH} — package lineage cannot be bound")
    return _sha256_bytes(CONTRACT_PATH.read_bytes())


def _df_content_sha256(df: pd.DataFrame) -> str:
    """Deterministic content hash of a DataFrame: column-sorted, row-sorted,
    NaN normalized to null, JSON-serialized.  Same data in any row order or
    dtype coercion hashes identically; different data cannot collide."""
    if df.empty:
        return hashlib.sha256(b"empty").hexdigest()
    canon = df.copy().astype(object).where(pd.notna(df), None)
    # Column-sorted + row-sorted -> orient="split" JSON is canonical.
    canon = canon.sort_values(list(canon.columns)).reset_index(drop=True)
    payload = canon.to_json(orient="split", force_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _family_lineage(name: str, df: pd.DataFrame, query: str, params: tuple,
                    provider: str, snapshot_identity: str,
                    date_col: str | None = None,
                    available_at_col: str | None = None) -> dict:
    """One lineage record per input family (v5.5.1 + v5.5.3 contract).

    query/parameter/schema/content SHAs bind WHAT was queried and WHAT
    came back (query_sha256 binds the REAL SQL text executed, never a
    short label — v5.5.3); min/max_available_at come from the DATA's own
    dates (never fabricated); retrieved_at is the read time.

    v5.5.3 provenance: ``available_at_col`` names a REAL DB timestamp
    column when the family has one.  When that column is present AND
    populated, min/max_available_at_ts carry the real timestamps and
    available_at_source is "db_timestamp_column"; when the column is
    absent or all-NULL (the live ingestion does not populate them), the
    record says so honestly — never fabricated.
    """
    date_vals = []
    if date_col and date_col in df.columns and not df.empty:
        date_vals = sorted({str(v)[:10]
                            for v in df[date_col].dropna().unique()})
    params_json = json.dumps(params, sort_keys=True)
    rec = {
        "family": name,
        "provider": provider,
        "query_sha256": hashlib.sha256(
            (query + "|" + params_json).encode("utf-8")).hexdigest(),
        "parameter_sha256": hashlib.sha256(
            params_json.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256(
            json.dumps(sorted(df.columns.tolist()), ensure_ascii=False)
            .encode("utf-8")).hexdigest(),
        "content_sha256": _df_content_sha256(df),
        "row_count": int(len(df)),
        "min_available_at": date_vals[0] if date_vals else None,
        "max_available_at": date_vals[-1] if date_vals else None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_identity": snapshot_identity,
    }
    ts_vals = []
    if available_at_col and available_at_col in df.columns and not df.empty:
        ts_vals = [str(v) for v in df[available_at_col].dropna().tolist()]
    if ts_vals:
        rec["available_at_source"] = "db_timestamp_column"
        rec["min_available_at_ts"] = min(ts_vals)
        rec["max_available_at_ts"] = max(ts_vals)
        rec["row_timestamps_populated"] = True
    else:
        rec["available_at_source"] = "business_date_convention"
        rec["row_timestamps_populated"] = False
    return rec


def _get_conn():
    import pymysql
    return pymysql.connect(
        host="localhost", user="root",
        password=os.environ.get("CHENYIYUN_DB_PASSWORD", ""),
        database="tushare_stock", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _begin_consistent_snapshot(conn) -> dict:
    """Start ONE read-only REPEATABLE READ consistent-snapshot transaction
    and capture its identity markers (v5.5.3).

    Previously the family queries were independent reads with no
    transaction — a write between queries could mix database states inside
    one package.  Now every family is read from the SAME snapshot, and the
    snapshot identity (server_uuid + GTID/binlog position when the server
    provides them) is bound to every lineage record.

    The local MySQL has log_bin=0 (formal E3 requires a binlog-enabled
    replica), so binlog capture is attempted and its absence is recorded
    HONESTLY in the identity — never fabricated.
    """
    with conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION READ ONLY, WITH CONSISTENT SNAPSHOT")
        cur.execute(
            "SELECT @@server_uuid AS server_uuid, "
            "@@transaction_isolation AS tx_isolation, "
            "@@gtid_executed AS gtid_executed")
        row = cur.fetchone() or {}
        identity = {
            "server_uuid": str(row.get("server_uuid") or "unknown"),
            "transaction_isolation": str(row.get("tx_isolation") or "unknown"),
            "gtid_executed": str(row.get("gtid_executed") or ""),
            "binlog_file": None,
            "binlog_position": None,
            "consistent_snapshot": True,
            "snapshot_started_at":
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        try:
            cur.execute("SHOW BINARY LOG STATUS")
            brow = cur.fetchone()
            if brow:
                identity["binlog_file"] = str(brow[0] if brow else "")
                identity["binlog_position"] = int(brow[1] if brow else 0)
        except Exception:
            # log_bin=0 server — record honestly, never fabricate a marker.
            identity["binlog_file"] = "unavailable:log_bin_off"
    return identity


def _snapshot_identity_marker(identity: dict) -> str:
    """Compact snapshot-identity string for lineage records (real, never
    the old hardcoded 'live_mysql:...' description strings)."""
    gtid = identity.get("gtid_executed") or ""
    if gtid:
        return f"consistent_snapshot:gtid={gtid[:40]}"
    if identity.get("binlog_file"):
        return (f"consistent_snapshot:binlog="
                f"{identity['binlog_file']}:{identity['binlog_position']}")
    return (f"consistent_snapshot:{identity.get('server_uuid')}@"
            f"{identity.get('snapshot_started_at')}")


def _max_business_date(df: pd.DataFrame, col: str) -> str | None:
    """The real max business date of a fetched family (v5.5.3 availability
    gate input — never fabricated).  Accepts int (20260805) or ISO
    (2026-08-05) forms; returns ISO so the gate's string comparison against
    trade_date is orderable."""
    if df is None or df.empty or col not in df.columns:
        return None
    vals = []
    for v in pd.to_numeric(df[col], errors="coerce").dropna().tolist():
        if pd.isna(v) or v == 0:
            continue
        s = str(int(v))
        if len(s) == 8 and s.isdigit():
            vals.append(f"{s[:4]}-{s[4:6]}-{s[6:]}")
        elif len(s) >= 10:
            vals.append(s[:10])
    return max(vals) if vals else None


def _build_availability_gates(signal_date: str, inputs: dict) -> dict:
    """REAL *_available_at gates (v5.5.3) — the DATE part comes from real
    DB fields of actually-fetched rows, the TIME part is the documented
    family convention (AVAILABLE_AT_CONVENTIONS).  adjustment/benchmark
    gates are now backed by REAL fetched data (dwd_adj_factor /
    ods_index_daily CSI300) — previously they were fabricated always-pass
    no-ops for families that were never fetched at all.
    """
    fin_date = _max_business_date(inputs.get("basic"), "ann_date")
    ind_date = _max_business_date(inputs.get("industry"), "effective_date")
    adj = inputs.get("adjustment")
    bench = inputs.get("benchmark")
    adj_date = signal_date if adj is not None and not adj.empty else None
    bench_date = (signal_date if bench is not None and not bench.empty
                  else None)
    return {
        "financial_available_at": (
            f"{fin_date}{AVAILABLE_AT_CONVENTIONS['financial']}"
            if fin_date else None),
        "industry_available_at": (
            f"{ind_date}{AVAILABLE_AT_CONVENTIONS['industry']}"
            if ind_date else None),
        "adjustment_available_at": (
            f"{adj_date}{AVAILABLE_AT_CONVENTIONS['adjustment']}"
            if adj_date else None),
        "benchmark_available_at": (
            f"{bench_date}{AVAILABLE_AT_CONVENTIONS['benchmark']}"
            if bench_date else None),
    }


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
    """Fetch + quality-check all live inputs for one signal date.

    v5.5.3:
      - ALL families are read inside ONE REPEATABLE READ consistent-
        snapshot transaction (single connection); the snapshot identity
        (server_uuid + GTID/binlog when the server provides them) is
        bound to every lineage record — the package can no longer mix
        data from different database points in time.
      - query_sha256 binds the REAL SQL text executed (never a short
        label).
      - adjustment / benchmark are now genuinely fetched (dwd_adj_factor /
        ods_index_daily CSI300) — their availability gates are real, not
        fabricated always-pass no-ops.
      - status_scd / dim_stock feed the universe's REAL
        security_status_transition / is_listed (A2).
    """
    conn = _get_conn()
    try:
        snapshot = _begin_consistent_snapshot(conn)
        marker = _snapshot_identity_marker(snapshot)
        dates = _trade_dates(conn, signal_date)
        if not dates or dates[-1] != signal_date:
            # No bars on the requested date -> BLOCKED (no stale fallback).
            raise SignalPackageBlocked(
                f"data_quality: no bars for {signal_date} "
                f"(latest available {dates[-1] if dates else 'none'}) — "
                "stale-date substitution is forbidden")
        date_int = int(signal_date.replace("-", ""))
        bar_range = (date_int, int(dates[0].replace("-", "")))
        market_sql = (
            "SELECT trade_date, ts_code, adj_close, amount "
            "FROM dwd_stock_daily_standard WHERE trade_date <= %s "
            "AND trade_date >= %s")
        bars = _read_sql(conn, market_sql, bar_range)
        bars["trade_date"] = bars["trade_date"].astype(str).apply(
            lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x)
        mcap_sql = ("SELECT ts_code, circ_mv FROM dwd_market_cap_daily "
                    "WHERE trade_date = %s")
        mcap = _read_sql(conn, mcap_sql, (date_int,))
        # circ_mv over the WHOLE bar window (v5.5.1): R2 crowding needs the
        # latest-day size to assign quartiles AND the 20d window to pair
        # first/last closes.  Previously bars carried no circ_mv at all, so
        # small_vs_large_20d_rs was ALWAYS None in production (the guard at
        # compute_crowding_state saw a column that never existed).
        mcap_window_sql = (
            "SELECT ts_code, trade_date, circ_mv FROM dwd_market_cap_daily "
            "WHERE trade_date >= %s AND trade_date <= %s")
        mcap_window = _read_sql(
            conn, mcap_window_sql,
            (int(dates[0].replace("-", "")), date_int))
        mcap_window["trade_date"] = mcap_window["trade_date"].astype(str).apply(
            lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x)
        mcap_window["ts_code"] = mcap_window["ts_code"].astype(str)
        bars["ts_code"] = bars["ts_code"].astype(str)
        bars = bars.merge(
            mcap_window[["ts_code", "trade_date", "circ_mv"]],
            on=["ts_code", "trade_date"], how="left")
        # v5.5.3: the financial family carries the REAL announcement date
        # (dws_fina_pit_daily.ann_date) — the availability gate's DATE part
        # comes from it, never from a fabricated T15:00.
        basic_sql = (
            "SELECT b.ts_code, b.pb, b.turnover_rate, f.ann_date "
            "FROM dwd_daily_basic b "
            "LEFT JOIN dws_fina_pit_daily f "
            "  ON b.ts_code = f.ts_code AND b.trade_date = f.trade_date "
            "WHERE b.trade_date = %s")
        basic = _read_sql(conn, basic_sql, (date_int,))
        industry_sql = (
            # Canonical taxonomy only: the SCD table carries several
            # industry systems (SW2021 L1/L2, TUSHARE_CURRENT L1) that are
            # ALL marked effective on the same date (000001.SZ had 3 rows
            # on 2026-08-04).  labels.industry == TUSHARE_CURRENT L1 at
            # latest updated_at, 100% verified — that is the pipeline's
            # canonical naming.  Per-symbol revision dedupe happens in
            # _normalize (the table keeps overlapping revision intervals
            # even within one system/level).
            "SELECT ts_code, industry_name, effective_date, expire_date, "
            "updated_at FROM dwd_stock_industry_scd WHERE "
            "industry_system = 'TUSHARE_CURRENT' AND industry_level = 'L1' "
            "AND (expire_date IS NULL OR expire_date > %s) "
            "AND effective_date <= %s")
        industry = _read_sql(conn, industry_sql, (date_int, date_int))
        # v5.5.3: REAL limit_type from the label table feeds the universe's
        # limit_status (A2) — never a hardcoded "NORMAL".
        labels_sql = (
            "SELECT ts_code, is_st, is_new, market, industry, limit_type "
            "FROM dwd_stock_label_daily WHERE trade_date = %s")
        labels = _read_sql(conn, labels_sql, (date_int,))
        # v5.5.3: REAL adjustment data (the old adjustment_available_at was
        # a fabricated always-pass no-op for a family never fetched).
        adjustment_sql = (
            "SELECT trade_date, ts_code, adj_factor FROM dwd_adj_factor "
            "WHERE trade_date = %s")
        adjustment = _read_sql(conn, adjustment_sql, (date_int,))
        # v5.5.3: REAL benchmark data (the old benchmark_available_at was
        # the same no-op) — CSI 300 close for the signal date.
        benchmark_sql = (
            "SELECT trade_date, ts_code, close FROM ods_index_daily "
            "WHERE ts_code = '000300.SH' AND trade_date = %s")
        benchmark = _read_sql(conn, benchmark_sql, (date_int,))
        # v5.5.3: REAL ST/lifecycle intervals (A2 security_status_transition)
        # and REAL listing intervals (A2 is_listed).
        status_scd_sql = (
            "SELECT ts_code, status, effective_date, expire_date "
            "FROM dim_stock_status_scd WHERE effective_date <= %s "
            "AND (expire_date IS NULL OR expire_date > %s)")
        status_scd = _read_sql(conn, status_scd_sql, (date_int, date_int))
        dim_stock_sql = "SELECT ts_code, list_date, delist_date FROM dim_stock"
        dim_stock = _read_sql(conn, dim_stock_sql, ())
        quality = {
            "signal_date": signal_date,
            "bar_dates": len(dates),
            "bar_rows": len(bars),
            "bar_symbols": int(bars["ts_code"].nunique()) if not bars.empty else 0,
            "mcap_rows": len(mcap),
            "basic_rows": len(basic),
            "industry_rows": len(industry),
            "label_rows": len(labels),
            "adjustment_rows": len(adjustment),
            "benchmark_rows": len(benchmark),
            "status_scd_rows": len(status_scd),
            "dim_stock_rows": len(dim_stock),
        }
        missing = [k for k, v in quality.items()
                   if k != "signal_date" and v == 0]
        if missing:
            raise SignalPackageBlocked(
                f"data_quality: zero rows for {missing} on {signal_date}")
        # ── v5.5.1/v5.5.3: real PIT lineage — every input family bound to
        # the REAL SQL text (query_sha256), the data's own date extent, and
        # the ONE consistent-snapshot identity shared by all families.
        lineage = [
            _family_lineage(
                "market", bars, market_sql, bar_range,
                "dwd_stock_daily_standard", marker,
                date_col="trade_date"),
            _family_lineage(
                "market_cap", mcap, mcap_sql, (date_int,),
                "dwd_market_cap_daily", marker),
            _family_lineage(
                "basic_financial", basic, basic_sql, (date_int,),
                "dwd_daily_basic+dws_fina_pit_daily", marker),
            _family_lineage(
                "industry_scd", industry, industry_sql, (date_int, date_int),
                "dwd_stock_industry_scd", marker,
                date_col="effective_date"),
            _family_lineage(
                "labels", labels, labels_sql, (date_int,),
                "dwd_stock_label_daily", marker,
                date_col="trade_date"),
            _family_lineage(
                "adjustment", adjustment, adjustment_sql, (date_int,),
                "dwd_adj_factor", marker, date_col="trade_date"),
            _family_lineage(
                "benchmark_index", benchmark, benchmark_sql, (date_int,),
                "ods_index_daily", marker, date_col="trade_date"),
            _family_lineage(
                "status_scd", status_scd, status_scd_sql, (date_int, date_int),
                "dim_stock_status_scd", marker),
            _family_lineage(
                "dim_stock", dim_stock, dim_stock_sql, (),
                "dim_stock", marker),
            _family_lineage(
                "trade_calendar",
                pd.DataFrame({"cal_date": dates}),
                "dim_trade_cal_open_days", (signal_date,),
                "dim_trade_cal", marker,
                date_col="cal_date"),
        ]
        got = {rec["family"] for rec in lineage}
        missing_lineage = [f for f in REQUIRED_LINEAGE_FAMILIES
                           if f not in got or len(
                               [r for r in lineage if r["family"] == f]) == 0]
        if missing_lineage:
            raise SignalPackageBlocked(
                f"SIGNAL_PACKAGE_BLOCKED: lineage families missing "
                f"({missing_lineage}) — the package cannot be bound to a "
                "complete PIT provenance")
        return {"bars": bars, "mcap": mcap, "basic": basic,
                "industry": industry, "labels": labels,
                "adjustment": adjustment, "benchmark": benchmark,
                "status_scd": status_scd, "dim_stock": dim_stock,
                "snapshot_identity": snapshot,
                "data_quality": quality, "lineage": lineage}
    finally:
        conn.rollback()  # end the read-only snapshot
        conn.close()


def _symbol_map(df: pd.DataFrame, col: str = "ts_code") -> pd.Series:
    """Normalize a ts_code column to 6-digit symbol strings."""
    return df[col].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)


def build_live_universe(labels: pd.DataFrame, signal_date: str,
                        bars: pd.DataFrame, status_scd: pd.DataFrame | None = None,
                        dim_stock: pd.DataFrame | None = None) -> pd.DataFrame:
    """Universe snapshot from REAL status sources (v5.5.3).

    - is_listed:            REAL listing intervals from dim_stock
                            list_date/delist_date — never a hardcoded 1.
                            A missing dim_stock leaves NaN and the universe
                            contract BLOCKS (status_source_missing).
    - limit_status:         REAL limit_type from the label table (10 ->
                            NORMAL, 20 -> 20PCT) — never "NORMAL" for all.
    - security_status_transition: REAL ST intervals from
                            dim_stock_status_scd + the REAL listing day
                            (LISTED event) — never "NORMAL" for all.
    - is_suspended:         derived from bar presence (no direct suspension
                            source exists in the live schema — provenance
                            recorded in the manifest; functionally correct:
                            a name with no bar on the signal date cannot be
                            traded).
    - is_st / is_new:       REAL from the label table; NaN stays NaN and
                            the universe contract BLOCKS (never fillna 0).
    """
    out = labels.copy()
    out["symbol"] = _symbol_map(out)
    out["trade_date"] = signal_date
    date_int = int(signal_date.replace("-", ""))

    # ── is_listed: real listing intervals (dim_stock) ──
    listed_map: dict = {}
    if dim_stock is not None and not dim_stock.empty:
        ds = dim_stock.copy()
        ds["symbol"] = _symbol_map(ds)
        ds["list_date"] = pd.to_numeric(ds["list_date"],
                                        errors="coerce").fillna(0)
        ds["delist_date"] = pd.to_numeric(ds["delist_date"],
                                          errors="coerce").fillna(0)
        ds["is_listed"] = (
            (ds["list_date"] <= date_int)
            & ((ds["delist_date"] == 0) | (ds["delist_date"] > date_int))
        ).astype(float)
        listed_map = ds.set_index("symbol")["is_listed"].to_dict()
    out["is_listed"] = out["symbol"].map(listed_map)

    # ── security_status_transition: real ST intervals + listing day ──
    st_symbols: set = set()
    if status_scd is not None and not status_scd.empty:
        sc = status_scd[status_scd["status"] == "st"].copy()
        sc["symbol"] = _symbol_map(sc)
        sc["effective_date"] = pd.to_numeric(sc["effective_date"],
                                             errors="coerce")
        sc["expire_date"] = pd.to_numeric(sc["expire_date"], errors="coerce")
        active = sc[(sc["effective_date"].fillna(0) <= date_int) &
                    (sc["expire_date"].fillna(0).eq(0) |
                     (sc["expire_date"] > date_int))]
        st_symbols = set(active["symbol"].astype(str).str.zfill(6))
    listed_today: set = set()
    if dim_stock is not None and not dim_stock.empty:
        ds = dim_stock.copy()
        ds["symbol"] = _symbol_map(ds)
        ld = pd.to_numeric(ds["list_date"], errors="coerce")
        listed_today = set(
            ds.loc[ld.fillna(0).eq(date_int), "symbol"].astype(str).str.zfill(6))
    out["security_status_transition"] = np.where(
        out["symbol"].isin(st_symbols), "ST",
        np.where(out["symbol"].isin(listed_today), "LISTED", "NORMAL"))

    # ── limit_status: real limit_type mapping ──
    lt = pd.to_numeric(out.get("limit_type"), errors="coerce")
    out["limit_status"] = lt.map(LIMIT_STATUS_MAP)
    unknown = lt.map(LIMIT_STATUS_MAP).isna() & lt.notna()
    out.loc[unknown, "limit_status"] = lt[unknown].astype(int).astype(str)
    out["limit_status"] = out["limit_status"].astype(object)
    out.loc[lt.isna(), "limit_status"] = np.nan

    # ── is_suspended: derived from bar presence (see docstring) ──
    bar_sym = (bars["symbol"] if "symbol" in bars.columns else bars["ts_code"])
    traded_symbols = set(bar_sym.astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))
    out["is_suspended"] = (~out["symbol"].isin(traded_symbols)).astype(float)

    # v5.5.1: REAL status from the label table — never a default of 0.
    # A missing is_st / is_new stays NaN and the universe contract BLOCKS
    # the package (status_source_missing) instead of assuming "normal".
    out["is_st"] = pd.to_numeric(out["is_st"], errors="coerce")
    out["is_new"] = pd.to_numeric(out["is_new"], errors="coerce")
    return out[["trade_date", "symbol", "is_listed", "is_st",
                "is_suspended", "is_new", "limit_status",
                "security_status_transition"]]


def _normalize(day: pd.DataFrame, mcap: pd.DataFrame,
               basic: pd.DataFrame, industry: pd.DataFrame) -> pd.DataFrame:
    day = day.copy()
    day["symbol"] = day["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6)
    # bars now carry a window circ_mv (R2 crowding); the signal-date mcap is
    # authoritative here — drop the window column first to avoid an x/y merge.
    day = day.drop(columns=["circ_mv"], errors="ignore")
    day = day.merge(mcap[["ts_code", "circ_mv"]].assign(
        symbol=lambda d: d["ts_code"].astype(str).str.replace(
            r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))[["symbol", "circ_mv"]],
        on="symbol", how="left")
    basic_syms = basic.assign(symbol=lambda d: d["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))[["symbol", "pb", "turnover_rate"]]
    day = day.merge(basic_syms, on="symbol", how="left")
    ind = industry.assign(symbol=lambda d: d["ts_code"].astype(str).str.replace(
        r"\.(SH|SZ|BJ)$", "", regex=True).str.zfill(6))
    # SCD overlap guard (2026-08-04 defect): the SCD table keeps multiple
    # overlapping effective intervals per symbol (revision rows even within
    # one system/level — 000001.SZ had 3, every symbol 2+).  Merging them
    # exploded the left join to 6054 rows / 526 duplicate symbols and put
    # the same stock in a target portfolio 3 times (603823 x3 in C3 top-10).
    # One row per symbol: the latest revision wins (max updated_at, then
    # effective_date, then expire_date — verified equal to labels.industry
    # 100% on 2026-08-04); rows without the columns sort last.
    sort_cols = [c for c in ("updated_at", "effective_date", "expire_date")
                 if c in ind.columns]
    ind = (ind.sort_values(sort_cols, ascending=False, na_position="last")
           .drop_duplicates("symbol", keep="first"))
    ind_syms = ind[["symbol", "industry_name"]]
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
    b["beta_raw"] = b.groupby("symbol", group_keys=False)[
        ["ret_1d", "market_ret"]
    ].apply(
        _beta_group).reset_index(level=0, drop=True)
    day = b[b["trade_date"] == signal_date].copy()
    if day.empty:
        raise SignalPackageBlocked(f"no bars for {signal_date}")
    return day


def _existing_sealed_dir(signal_date: str) -> Path | None:
    """Package dir when a SEALED manifest already exists for the signal
    date (idempotent re-seal check, v5.5.3).  Returns None for missing
    dirs, unreadable/invalid manifests, or non-SEALED status — those all
    keep the historical build/raise behavior."""
    pkg = PACKAGES_ROOT / signal_date
    manifest_path = pkg / "signal_package_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("package_status") != "SEALED":
        return None
    return pkg


def run_package(signal_date: str | None = None, dry_run: bool = False,
                revision_for: str | None = None) -> int:
    """Production entry: fetch -> quality -> universe -> factors -> seal.

    dry_run=True runs every stage with real data but seals nothing
    (v5.5.1): no package dir, no SEALED manifest, no state change.

    revision_for=<date> regenerates THAT date's package with today's code
    as the next revision_N/ beside the original (v5.5.1 plan 0.4): the
    original SEALED package is preserved and never overwritten.  This is
    an ENGINEERING COMPARISON revision only — its manifest records the
    correction reason, and it is never counted into E4 shadow days.
    """
    from runtime.shadow_execution_state import ALL_STATES  # noqa: F401  (import sanity)
    runtime_cfg = yaml.safe_load(RUNTIME_CFG_PATH.read_text(encoding="utf-8"))
    if signal_date is None:
        signal_date = revision_for or datetime.now().strftime("%Y-%m-%d")

    # v5.5.3 idempotent re-seal: a SEALED package for this signal date is
    # immutable — a retried seal job (e.g. one whose subprocess sealed but
    # whose in-process verifier failed) reports already_sealed and exits 0
    # so the artifact verifier re-proves the contract against the existing
    # manifest.  The package is never rewritten (no revision bump, no
    # overwrite); a contract violation still FAILs the job at verification.
    # --revision-for and --dry-run keep their historical behavior.
    if not dry_run and revision_for is None:
        sealed = _existing_sealed_dir(signal_date)
        if sealed is not None:
            existing_manifest = json.loads(
                (sealed / "signal_package_manifest.json")
                .read_text(encoding="utf-8"))
            print(json.dumps({
                "already_sealed": True,
                "signal_date": signal_date,
                "execution_date": existing_manifest.get("execution_date"),
                "revision": existing_manifest.get("revision"),
                "package_dir": str(sealed),
                "package_sha": True,
            }, ensure_ascii=False, indent=2))
            return 0

    inputs = fetch_production_inputs(signal_date)
    dq = inputs["data_quality"]
    lineage = inputs["lineage"]
    bars = inputs["bars"]
    mcap, basic, industry, labels = (inputs["mcap"], inputs["basic"],
                                     inputs["industry"], inputs["labels"])
    status_scd = inputs.get("status_scd")
    dim_stock = inputs.get("dim_stock")

    # 16:30 — PIT universe freeze.  v5.5.3: the universe snapshot carries
    # REAL status sources (dim_stock listing intervals, label-table
    # limit_type, status-SCD ST intervals + the real listing day) and the
    # availability gates are anchored on REAL data (ann_date /
    # effective_date / fetched adjustment + benchmark rows) — never the
    # fabricated T15:00 always-pass gates.
    universe_snapshot = build_live_universe(labels, signal_date, bars,
                                            status_scd=status_scd,
                                            dim_stock=dim_stock)
    availability = _build_availability_gates(signal_date, inputs)
    uni = build_daily_universe(
        universe_snapshot, signal_date, availability,
        contract=runtime_cfg.get("universe_contract"))
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
    missing_report = {}
    for cid, cand in runtime_cfg["candidates"].items():
        scored = compute_candidate_scores(raw, cand)
        scores_by_candidate[cid] = scored[[
            "symbol", "score", "trade_date"]].copy()
        factor_by_candidate[cid] = scored[[
            "symbol", "score", "trade_date"]].copy()
        # v5.5.1 missing-factor gate: NaN scores (any required factor
        # missing) are counted and must stay under the pre-registered
        # contract threshold — never silently shrink the sample.
        n = len(scored)
        missing = int(scored["score"].isna().sum())
        missing_pct = missing / n if n else 0.0
        missing_report[cid] = {
            "total_universe": n,
            "missing_factor_rows": missing,
            "missing_factor_pct": round(missing_pct, 6),
        }
        threshold = float(cand.get("max_missing_factor_pct", 0.05))
        if missing_pct > threshold:
            raise SignalPackageBlocked(
                f"SIGNAL_PACKAGE_BLOCKED: candidate {cid} missing factors "
                f"on {missing}/{n} rows ({missing_pct:.2%}) exceeds the "
                f"pre-registered max_missing_factor_pct={threshold} — "
                "scores are not imputed; fix the input data")
    dq = {**dq, "missing_factor_report": missing_report}

    # 16:50 — target portfolios (R2 overlay for C2).
    crowding = compute_crowding_state(bars)
    r2_candidates = [cid for cid, cand in runtime_cfg["candidates"].items()
                     if cand.get("risk_overlay") == "r2_crowding"]
    if crowding.get("blocked") and r2_candidates:
        # v5.5.1 fail-closed: a blocked crowding state must NEVER degrade to
        # "no overlay adjustment" (multiplier 1.0) for R2 candidates.
        raise SignalPackageBlocked(
            f"SIGNAL_PACKAGE_BLOCKED: crowding state unavailable "
            f"({crowding.get('block_reason')}) — R2 overlay for "
            f"{r2_candidates} cannot be computed; no default-normal fallback")
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
    input_manifest = {
        "signal_date": signal_date,
        # v5.5.1: REAL per-family content SHAs + full lineage records +
        # the canonical PIT contract SHA — never the old placeholders.
        "source_snapshot_shas": {
            rec["family"]: rec["content_sha256"] for rec in lineage},
        "pit_contract_sha": _pit_contract_sha(),
        "pit_lineage": lineage,
        "availability_gates": availability,
        # v5.5.3: the ONE consistent-snapshot transaction identity shared
        # by every family (server_uuid + GTID/binlog when available).
        "snapshot_identity": inputs.get("snapshot_identity"),
    }
    if dry_run:
        # v5.5.1 --dry-run contract: run every stage with real data, build
        # an in-memory SHA preview, and create NOTHING formal — no package
        # dir, no SEALED manifest, no execution state change.
        preview = _dry_run_preview(
            signal_date, exec_date, uni.universe, factors_all, scores_all,
            portfolios, dq, input_manifest)
        print(json.dumps({
            "status": "DRY_RUN_PASS",
            "would_seal": True,
            "signal_date": signal_date,
            "execution_date": exec_date,
            "candidate_count": len(scores_by_candidate),
            "tradeable_count": uni.n_tradeable,
            "package_sha256_preview": preview,
        }, ensure_ascii=False, indent=2))
        return 0
    manifest = seal_signal_package(
        PACKAGES_ROOT / signal_date,
        signal_date=signal_date, execution_date=exec_date,
        universe=uni.universe, factor_values=factors_all, scores=scores_all,
        target_portfolios=portfolios, data_quality=dq,
        input_manifest=input_manifest,
        allow_revision=revision_for is not None,
        revision_reason=(
            "v5.5.1 corrected engineering comparison revision (plan 0.4) "
            f"for {revision_for} — parent classified "
            "KNOWN_DEFECT_PRESTART_PACKAGE; replay/smoke test only, "
            "NEVER counted into E4 shadow days or round trips"
            if revision_for else None))
    out_dir = PACKAGES_ROOT / signal_date
    rev = int(manifest.get("revision", 1))
    if rev > 1:
        # Construct from the manifest — never re-probe the filesystem
        # (the revision now exists, so a second next_revision_dir() call
        # would skip ahead to the next free number).
        out_dir = PACKAGES_ROOT / signal_date / f"revision_{rev}"
    print(json.dumps({"package_sealed": manifest["signal_date"],
                      "execution_date": exec_date,
                      "revision": manifest.get("revision"),
                      "package_dir": str(out_dir.relative_to(PROJECT_ROOT)),
                      "candidates": manifest["candidate_ids"],
                      "universe": uni.n_tradeable,
                      "package_sha": (out_dir /
                                      "package_sha256.json").exists()},
                     ensure_ascii=False, indent=2))
    return 0


def _dry_run_preview(signal_date: str, execution_date: str,
                     universe: pd.DataFrame,
                     factor_values: pd.DataFrame, scores: pd.DataFrame,
                     target_portfolios: dict, data_quality: dict,
                     input_manifest: dict) -> str:
    """Write the full payload set into a disposable staging dir, return the
    exact package SHA256 it WOULD carry, then delete it.  Never touches the
    formal packages zone or any execution state."""
    import shutil
    preview_dir = PACKAGES_ROOT / ".staging" / \
        f"dryrun-{signal_date}-{uuid.uuid4().hex[:8]}"
    preview_dir.parent.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=False)
    try:
        _write_package_payloads(
            preview_dir, final_dir=PACKAGES_ROOT / signal_date,
            signal_date=signal_date,
            execution_date=execution_date, universe=universe,
            factor_values=factor_values, scores=scores,
            target_portfolios=target_portfolios, data_quality=data_quality,
            input_manifest=input_manifest, git=_git_info(),
            revision_n=1, revision_reason=None, parent_package_sha=None)
        sha_payload = json.loads(
            (preview_dir / "package_sha256.json").read_text(encoding="utf-8"))
        return str(sha_payload.get("package_sha256"))
    finally:
        shutil.rmtree(preview_dir, ignore_errors=True)
        try:
            preview_dir.parent.rmdir()  # drop empty .staging/ leftovers
        except OSError:
            pass  # other concurrent staging dirs still present — keep


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
    parser.add_argument(
        "--revision-for", default=None,
        help="regenerate THIS signal date as the next revision_N/ with "
             "today's code (engineering comparison only — plan 0.4; the "
             "original SEALED package is preserved, revision never counts "
             "into E4)")
    args = parser.parse_args()
    try:
        return run_package(args.date, dry_run=args.dry_run,
                           revision_for=args.revision_for)
    except (SignalPackageBlocked, PackageSealedError) as exc:
        print(json.dumps({"signal_package_blocked": str(exc)},
                         ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
