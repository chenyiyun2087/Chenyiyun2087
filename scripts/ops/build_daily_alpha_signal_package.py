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
        return out
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
      - circ_mv missing on the latest day -> concentration still reported,
        rs explicitly None (never a silently wrong ratio).
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
        return {**base, "block_reason": "circ_mv_missing"}

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
        return {**base, "block_reason": "no_valid_return_size_pairs"}
    size_q = pd.qcut(ret_with_size["circ_mv"].rank(method="first"), 4,
                     labels=False)
    small = ret_with_size.loc[size_q == 0, "ret_20d"].mean()
    large = ret_with_size.loc[size_q == 3, "ret_20d"].mean()
    rs = float(small / large) if large and large > 0 else None
    return {**base, "small_vs_large_20d_rs": rs}


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
REQUIRED_LINEAGE_FAMILIES = ("market", "market_cap", "basic_financial",
                             "industry_scd", "labels", "trade_calendar")


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
                    date_col: str | None = None) -> dict:
    """One lineage record per input family (v5.5.1 contract).

    query/parameter/schema/content SHAs bind WHAT was queried and WHAT
    came back; min/max_available_at come from the DATA's own dates (never
    fabricated); retrieved_at is the read time.
    """
    date_vals = []
    if date_col and date_col in df.columns and not df.empty:
        date_vals = sorted({str(v)[:10]
                            for v in df[date_col].dropna().unique()})
    params_json = json.dumps(params, sort_keys=True)
    return {
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
        bar_range = (date_int, int(dates[0].replace("-", "")))
        bars = _read_sql(
            conn,
            "SELECT trade_date, ts_code, adj_close, amount "
            "FROM dwd_stock_daily_standard WHERE trade_date <= %s "
            "AND trade_date >= %s",
            bar_range)
        bars["trade_date"] = bars["trade_date"].astype(str).apply(
            lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x)
        mcap = _read_sql(
            conn,
            "SELECT ts_code, circ_mv FROM dwd_market_cap_daily "
            "WHERE trade_date = %s", (date_int,))
        # circ_mv over the WHOLE bar window (v5.5.1): R2 crowding needs the
        # latest-day size to assign quartiles AND the 20d window to pair
        # first/last closes.  Previously bars carried no circ_mv at all, so
        # small_vs_large_20d_rs was ALWAYS None in production (the guard at
        # compute_crowding_state saw a column that never existed).
        mcap_window = _read_sql(
            conn,
            "SELECT ts_code, trade_date, circ_mv FROM dwd_market_cap_daily "
            "WHERE trade_date >= %s AND trade_date <= %s",
            (int(dates[0].replace("-", "")), date_int))
        mcap_window["trade_date"] = mcap_window["trade_date"].astype(str).apply(
            lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(x) == 8 else x)
        mcap_window["ts_code"] = mcap_window["ts_code"].astype(str)
        bars["ts_code"] = bars["ts_code"].astype(str)
        bars = bars.merge(
            mcap_window[["ts_code", "trade_date", "circ_mv"]],
            on=["ts_code", "trade_date"], how="left")
        basic = _read_sql(
            conn,
            "SELECT ts_code, pb, turnover_rate FROM dwd_daily_basic "
            "WHERE trade_date = %s", (date_int,))
        industry = _read_sql(
            conn,
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
            "AND effective_date <= %s",
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
        # ── v5.5.1: real PIT lineage — every input family bound to its own
        # query/parameter/schema/content SHAs + the data's own date extent.
        lineage = [
            _family_lineage(
                "market", bars, "bars", bar_range,
                "dwd_stock_daily_standard",
                f"live_mysql:dwd_stock_daily_standard<={date_int}",
                date_col="trade_date"),
            _family_lineage(
                "market_cap", mcap, "mcap_signal_date", (date_int,),
                "dwd_market_cap_daily",
                f"live_mysql:dwd_market_cap_daily={date_int}"),
            _family_lineage(
                "basic_financial", basic, "basic_signal_date", (date_int,),
                "dwd_daily_basic",
                f"live_mysql:dwd_daily_basic={date_int}"),
            _family_lineage(
                "industry_scd", industry, "industry_scd", (date_int, date_int),
                "dwd_stock_industry_scd",
                f"live_mysql:dwd_stock_industry_scd<=TUSHARE_CURRENT_L1"
                f"@{date_int}",
                date_col="effective_date"),
            _family_lineage(
                "labels", labels, "labels_signal_date", (date_int,),
                "dwd_stock_label_daily",
                f"live_mysql:dwd_stock_label_daily={date_int}",
                date_col="trade_date"),
            _family_lineage(
                "trade_calendar",
                pd.DataFrame({"cal_date": dates}),
                "dim_trade_cal_open_days", (signal_date,),
                "dim_trade_cal",
                f"live_mysql:dim_trade_cal@SSE<={signal_date}",
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
                "data_quality": quality, "lineage": lineage}
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
    # v5.5.1: REAL status from the label table — never a default of 0.
    # A missing is_st / is_new stays NaN and the universe contract BLOCKS
    # the package (status_source_missing) instead of assuming "normal".
    out["is_st"] = pd.to_numeric(out["is_st"], errors="coerce")
    out["is_new"] = pd.to_numeric(out["is_new"], errors="coerce")
    out["limit_status"] = "NORMAL"
    out["security_status_transition"] = "NORMAL"
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
    b["beta_raw"] = b.groupby("symbol", group_keys=False).apply(
        _beta_group).reset_index(level=0, drop=True)
    day = b[b["trade_date"] == signal_date].copy()
    if day.empty:
        raise SignalPackageBlocked(f"no bars for {signal_date}")
    return day


def run_package(signal_date: str | None = None, dry_run: bool = False) -> int:
    """Production entry: fetch -> quality -> universe -> factors -> seal.

    dry_run=True runs every stage with real data but seals nothing
    (v5.5.1): no package dir, no SEALED manifest, no state change.
    """
    from runtime.shadow_execution_state import ALL_STATES  # noqa: F401  (import sanity)
    runtime_cfg = yaml.safe_load(RUNTIME_CFG_PATH.read_text(encoding="utf-8"))
    if signal_date is None:
        signal_date = datetime.now().strftime("%Y-%m-%d")

    inputs = fetch_production_inputs(signal_date)
    dq = inputs["data_quality"]
    lineage = inputs["lineage"]
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
        input_manifest=input_manifest)
    print(json.dumps({"package_sealed": manifest["signal_date"],
                      "execution_date": exec_date,
                      "candidates": manifest["candidate_ids"],
                      "universe": uni.n_tradeable,
                      "package_sha": (PACKAGES_ROOT / signal_date /
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
    args = parser.parse_args()
    try:
        return run_package(args.date, dry_run=args.dry_run)
    except (SignalPackageBlocked, PackageSealedError) as exc:
        print(json.dumps({"signal_package_blocked": str(exc)},
                         ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
