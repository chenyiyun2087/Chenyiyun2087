"""v5.6.1 alpha-candidate daily diagnostics (pure, hermetic — no I/O).

The v5.6.1 challengers (H010 residualized F1, H011 F1+R2 crowding,
H012 F1 risk-sized) are pre-registered with frozen parameters.  This
module is the DAILY DIAGNOSTICS layer that records per-day evidence and
FAILS CLOSED whenever the day's inputs cannot support the pre-registered
contract:

  H010 (residualize_on_style) — every fit day reports OLS matrix rank,
      condition number, effective cross-section, industry dummy count,
      style R^2, residual mean, residual-vs-raw-F1 correlation and the
      missing-input rate.  A rank-deficient design or a cross-section
      below ``minimum_cross_section`` raises the C3_BLOCKED gate — the
      day is NOT silently fit with a truncated design.

  H011 (r2_crowding overlay) — the overlay only scales total position,
      never selection.  ``resolve_r2_state`` maps the two required market
      inputs (top5_turnover_concentration, small_vs_large_20d_rs) onto
      the pre-registered rules and returns the position multiplier.  If
      ANY input is missing or NaN the state is UNKNOWN and the
      multiplier is None — a missing input is NEVER treated as the
      normal-state 1.0.

  H012 (risk_weighted sizing) — selection is identical to F1 (same
      scores, same top-N); volatility only changes weights.  The
      contract's caps (single_name_risk_cap, top2_risk_contribution_cap,
      portfolio_vol_target) are enforced; missing forecast volatility
      for a selected name BLOCKS the day (never a silent equal weight).
      The algorithm is deterministic — weights and risk contributions
      are reproducible from (scores, forecast_volatility).

Rules are passed in (loaded from the pre-registered YAMLs by the
caller) so this module stays pure and the YAMLs stay the single source
of truth — no threshold duplication between YAML and Python.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

# ── Gates ──────────────────────────────────────────────────────────────

C3_BLOCKED = "C3_BLOCKED"
R2_INPUT_MISSING = "R2_INPUT_MISSING"
RISK_INPUT_MISSING = "RISK_INPUT_MISSING"
CAP_VIOLATION = "CAP_VIOLATION"
OK = "OK"


def _finite_float(v: Any) -> Optional[float]:
    """Coerce a value to finite float, or None when missing/NaN/inf."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


# ── H010: residualized F1 daily diagnostics ───────────────────────────


def diagnose_h010_day(
    day: pd.DataFrame,
    style_cols: list[str],
    industry_col: Optional[str] = "industry",
    minimum_cross_section: int = 20,
) -> dict:
    """Fit the pre-registered OLS and return per-day diagnostics + gate.

    Model: score ~ 1 + style_cols + industry_FE (most-common industry
    dropped to keep the design full column rank with the intercept —
    same construction as the production residualizer).  Style columns
    are z-scored before the fit (a linear column transform leaves the
    projection span and the residuals unchanged to float rounding).

    Returns a dict with the daily diagnostics and the gate outcome:
      - effective_cross_section: rows that entered the fit
      - universe_rows: rows on the day (before any dropping)
      - missing_rate: fraction of universe rows dropped for NaN/non-finite
        inputs (including missing industry)
      - industry_dummy_count: one-hot columns after dropping the
        most-common industry
      - design_rank / condition_number: of the design matrix (intercept
        + z-scored styles + industry dummies)
      - style_r2: 1 - RSS/TSS of the fitted model on the fit subset
      - residual_mean / residual_f1_corr: mean residual (≈0 by
        construction) and the cross-sectional correlation of the
        residual with the RAW score the fit ran on
      - blocked: C3_BLOCKED when the design is rank-deficient or the
        effective cross-section < minimum_cross_section
      - residual_score: Series over the full day (NaN for dropped rows)
    """
    out: dict = {}
    day = day.copy()
    fit_cols = list(style_cols) + ([industry_col] if industry_col else [])
    missing = [c for c in ["score"] + fit_cols if c not in day.columns]
    if missing:
        return {
            "blocked": C3_BLOCKED,
            "reason": f"required column(s) missing: {missing}",
            "effective_cross_section": 0,
            "universe_rows": len(day),
            "missing_rate": 1.0,
            "industry_dummy_count": 0,
            "design_rank": 0,
            "condition_number": None,
            "style_r2": None,
            "residual_mean": None,
            "residual_f1_corr": None,
        }

    universe = len(day)
    num = day[["score"] + style_cols].apply(
        lambda s: np.isfinite(pd.to_numeric(s, errors="coerce")))
    mask = num.all(axis=1)
    if industry_col:
        mask &= day[industry_col].notna()
    sub = day[mask]
    effective = len(sub)

    residual = pd.Series(np.nan, index=day.index, dtype=float)
    if effective < minimum_cross_section:
        out["residual_score"] = residual
        return {
            "blocked": C3_BLOCKED,
            "reason": (f"effective cross-section {effective} < "
                       f"minimum_cross_section {minimum_cross_section}"),
            "effective_cross_section": effective,
            "universe_rows": universe,
            "missing_rate": 1.0 - effective / universe,
            "industry_dummy_count": 0,
            "design_rank": 0,
            "condition_number": None,
            "style_r2": None,
            "residual_mean": None,
            "residual_f1_corr": None,
            "residual_score": residual,
        }

    # Z-score style columns (span-preserving) — same construction as the
    # production residualizer, which needs it for conditioning.
    x_style = sub[style_cols].to_numpy(dtype=float)
    mu, sd = x_style.mean(axis=0), x_style.std(axis=0)
    sd[sd == 0] = 1.0
    x_style = (x_style - mu) / sd

    dummy_count = 0
    if industry_col:
        counts = sub[industry_col].value_counts()
        cats = [c for c in counts.index if c != counts.idxmax()]
        dummy_count = len(cats)
        idx_map = {c: i for i, c in enumerate(cats)}
        ind = np.zeros((len(sub), dummy_count), dtype=float)
        for i, c in enumerate(sub[industry_col]):
            j = idx_map.get(c)
            if j is not None:
                ind[i, j] = 1.0
        design = np.column_stack([np.ones(len(sub)), x_style, ind])
    else:
        design = np.column_stack([np.ones(len(sub)), x_style])

    y = sub["score"].to_numpy(dtype=float)
    rank = int(np.linalg.matrix_rank(design))
    cond = float(np.linalg.cond(design))
    n_cols = design.shape[1]

    if rank < n_cols:
        residual.loc[mask] = np.nan
        out["residual_score"] = residual
        return {
            "blocked": C3_BLOCKED,
            "reason": (f"rank-deficient design: rank {rank} < {n_cols} "
                       "columns (intercept + styles + industry dummies)"),
            "effective_cross_section": effective,
            "universe_rows": universe,
            "missing_rate": 1.0 - effective / universe,
            "industry_dummy_count": dummy_count,
            "design_rank": rank,
            "condition_number": cond,
            "style_r2": None,
            "residual_mean": None,
            "residual_f1_corr": None,
            "residual_score": residual,
        }

    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = np.dot(design, beta)
    resid = y - fitted
    tss = float(np.sum((y - y.mean()) ** 2))
    style_r2 = (1.0 - float(np.sum(resid ** 2)) / tss
                if tss > 1e-12 else float("nan"))
    residual.loc[mask] = resid
    out["residual_score"] = residual
    out["blocked"] = OK
    out["reason"] = None
    out["effective_cross_section"] = effective
    out["universe_rows"] = universe
    out["missing_rate"] = 1.0 - effective / universe
    out["industry_dummy_count"] = dummy_count
    out["design_rank"] = rank
    out["condition_number"] = cond
    out["style_r2"] = float(style_r2)
    out["residual_mean"] = float(resid.mean())
    out["residual_f1_corr"] = float(
        pd.Series(y).corr(pd.Series(resid))) if len(y) >= 2 else None
    return out


# ── H011: R2 crowding state (weight-only overlay) ─────────────────────


class _UndefinedRatio:
    """Sentinel for an undefined (non-finite) ratio in rule evaluation.

    ``small_vs_large_20d_rs`` is None when the ratio is mathematically
    undefined — the large-quartile 20d return is non-positive, so
    small/large has no value (compute_crowding_state marks this as a
    VALID state, blocked=False).  The pre-registered rules only trigger
    on ``small_vs_large_20d_rs >= X`` (small-cap overheating); an
    undefined ratio cannot exceed any threshold, so every comparison
    against it evaluates to False and the other condition of an OR-rule
    (top5_turnover_concentration) is still evaluated.  This is NOT a
    silent 1.0 fallback: a concentration-triggered rule (crowding_elevated
    at conc >= 0.25) still fires, and a missing concentration still
    blocks.
    """

    def __lt__(self, other: Any) -> bool: return False
    def __le__(self, other: Any) -> bool: return False
    def __gt__(self, other: Any) -> bool: return False
    def __ge__(self, other: Any) -> bool: return False
    def __eq__(self, other: Any) -> bool: return False
    def __bool__(self) -> bool: return False


def resolve_r2_state(
    concentration: Any,
    small_vs_large_rs: Any,
    rules: list[dict],
) -> dict:
    """Map the two market inputs onto the pre-registered R2 rules.

    ``rules`` come from config/risk_overlays/r2_crowding_control.yaml
    (e.g. crowding_extreme -> multiplier 0.50, crowding_elevated -> 0.70),
    most-severe first.  The first rule whose conditions hold wins.

    Fail-closed: a MISSING concentration is R2_INPUT_MISSING and the
    multiplier is None — the overlay NEVER silently degrades to the
    normal-state 1.0 (the 2026-08-04 defect that defaulted R2 to 1.0
    when inputs were absent).

    v5.5.3 fix (2026-08-06): ``small_vs_large_20d_rs=None`` from a
    blocked=False crowding state is NOT an input failure — it is the
    mathematically undefined ratio (large-quartile 20d return <= 0).
    Rules are then evaluated with the rs conditions treated as False via
    the _UndefinedRatio sentinel, so the elevated OR-rule can still fire
    on concentration alone.  NaN/inf/garbage rs (data anomalies) still
    fail closed.

    Returns {state, position_multiplier, blocked, reason}.
    """
    conc = _finite_float(concentration)
    if conc is None:
        return {
            "state": "UNKNOWN",
            "position_multiplier": None,
            "blocked": R2_INPUT_MISSING,
            "reason": (f"required R2 input missing: "
                       f"top5_turnover_concentration={concentration!r}, "
                       f"small_vs_large_20d_rs={small_vs_large_rs!r}"),
        }
    raw_rs = _finite_float(small_vs_large_rs)
    if raw_rs is None:
        if small_vs_large_rs is None:
            # Mathematically undefined ratio — a valid market state, not
            # a missing input (see _UndefinedRatio).
            rs: Any = _UndefinedRatio()
        else:
            # NaN / inf / non-numeric — a data anomaly, fail closed.
            return {
                "state": "UNKNOWN",
                "position_multiplier": None,
                "blocked": R2_INPUT_MISSING,
                "reason": (f"required R2 input missing: "
                           f"small_vs_large_20d_rs={small_vs_large_rs!r} "
                           f"(non-finite data anomaly)"),
            }
    else:
        rs = raw_rs
    for rule in rules:
        cond = rule.get("condition", "")
        if not cond:
            continue
        # condition strings from the pre-registered YAML, e.g.
        #   "top5_turnover_concentration >= 0.30 and small_vs_large_20d_rs >= 1.25"
        # Evaluated with the two allowed names only — anything else in the
        # condition string raises (fail-closed, never silently ignored).
        env: dict = {
            "top5_turnover_concentration": conc,
            "small_vs_large_20d_rs": rs,
        }
        try:
            hit = bool(eval(cond, {"__builtins__": {}}, env))  # noqa: S307
        except Exception as exc:  # pragma: no cover - defensive
            return {
                "state": "UNKNOWN",
                "position_multiplier": None,
                "blocked": R2_INPUT_MISSING,
                "reason": f"unparseable R2 rule condition {cond!r}: {exc}",
            }
        if hit:
            return {
                "state": rule.get("id", "unknown"),
                "position_multiplier": _finite_float(
                    rule.get("position_multiplier")),
                "blocked": OK,
                "reason": None,
            }
    return {
        "state": "normal",
        "position_multiplier": 1.0,
        "blocked": OK,
        "reason": None,
    }


# ── H012: F1 risk-sized weights (weight layer only) ───────────────────


def risk_sized_weights(
    scores_day: pd.DataFrame,
    forecast_vol: dict,
    contract: dict,
    top_n: int = 10,
) -> dict:
    """H012 risk-weighted position sizing on the F1 Top-N.

    Selection is IDENTICAL to F1 — the caller passes the same ranked
    day; volatility touches weights only.  Algorithm (deterministic,
    reproducible):

      1. raw_i     = 1 / forecast_volatility_i            (selected names)
      2. w_i       = raw_i / sum(raw)                     (normalize)
      3. clamp w_i <= single_name_risk_cap, renormalize, repeat until
         stable (bounded iterations; residual is cash, never levered)
      4. sigma_p   = sqrt(sum w_i^2 * vol_i^2)            (independent risk)
      5. top2 risk contribution sum = (w^2*vol^2 / sigma_p^2) sorted desc,
         top two must be <= top2_risk_contribution_cap -> else CAP_VIOLATION
      6. if sigma_p > portfolio_vol_target: scale all weights by
         target/sigma_p — cash residual = 1 - sum(w) (portfolio-level
         de-risking, relative weights unchanged)

    Missing forecast volatility for any selected name -> RISK_INPUT_MISSING
    (never equal-weight).  Returns {selection, weights, sigma_p,
    top2_risk_contribution, cash_residual, blocked, reason}.
    """
    day = scores_day.copy()
    if "symbol" not in day.columns or "score" not in day.columns:
        return {"blocked": RISK_INPUT_MISSING,
                "reason": "scores_day requires symbol + score columns",
                "selection": [], "weights": {}, "sigma_p": None,
                "top2_risk_contribution": None, "cash_residual": None}
    ranked = (day.dropna(subset=["score"])
              .sort_values("score", ascending=False)
              .head(top_n))
    selection = [str(s) for s in ranked["symbol"]]
    if not selection:
        return {"blocked": RISK_INPUT_MISSING, "reason": "no scored symbols",
                "selection": [], "weights": {}, "sigma_p": None,
                "top2_risk_contribution": None, "cash_residual": None}

    vol = {str(s): _finite_float(forecast_vol.get(str(s)))
           for s in selection}
    missing = [s for s, v in vol.items() if v is None or v <= 0.0]
    if missing:
        return {
            "blocked": RISK_INPUT_MISSING,
            "reason": f"missing/non-positive forecast volatility: {missing}",
            "selection": selection, "weights": {},
            "sigma_p": None, "top2_risk_contribution": None,
            "cash_residual": None,
        }

    single_cap = float(contract.get("single_name_risk_cap", 0.12))
    target = float(contract.get("portfolio_vol_target", 0.18))

    # Steps 1-3: 1/vol weights, then a monotone fair-allocation clamp.
    # Over-cap names are pinned EXACTLY at the cap; the remaining weight
    # (1 - k*cap) is shared by the still-uncapped names in proportion to
    # their RAW weights (closed form — no renormalization of already
    # rounded shares, so the sum stays exactly 1 up to float eps).  Each
    # round pins at least one more name, so the loop terminates in
    # <= len(selection) rounds and capped names are never re-inflated
    # above the cap.
    raw = {s: 1.0 / vol[s] for s in selection}
    capped: set[str] = set()
    for _ in range(len(selection) + 1):
        free = [s for s in selection if s not in capped]
        free_raw = sum(raw[s] for s in free)
        remaining = 1.0 - len(capped) * single_cap
        if remaining < 0 or free_raw <= 0:
            return {"blocked": CAP_VIOLATION,
                    "reason": "degenerate sizing — cap cannot be met",
                    "selection": selection, "weights": {},
                    "sigma_p": None, "top2_risk_contribution": None,
                    "cash_residual": None}
        over = [s for s in free
                if remaining * raw[s] / free_raw > single_cap]
        if not over:
            break
        capped.update(over)
    free = [s for s in selection if s not in capped]
    free_raw = sum(raw[s] for s in free)
    remaining = 1.0 - len(capped) * single_cap
    w = {s: single_cap for s in capped}
    w.update({s: remaining * raw[s] / free_raw for s in free})

    sigma_p = float(np.sqrt(sum(w[s] ** 2 * vol[s] ** 2 for s in selection)))
    if sigma_p <= 0:
        return {"blocked": RISK_INPUT_MISSING,
                "reason": "zero portfolio volatility",
                "selection": selection, "weights": {},
                "sigma_p": None, "top2_risk_contribution": None,
                "cash_residual": None}

    contrib = {s: w[s] ** 2 * vol[s] ** 2 / sigma_p ** 2 for s in selection}
    top2 = sorted(contrib.values(), reverse=True)[:2]
    top2_sum = float(sum(top2))
    top2_cap = float(contract.get("top2_risk_contribution_cap", 0.30))
    if top2_sum > top2_cap:
        return {
            "blocked": CAP_VIOLATION,
            "reason": (f"top2 risk contribution {top2_sum:.4f} > "
                       f"cap {top2_cap}"),
            "selection": selection,
            "weights": dict(w),
            "sigma_p": sigma_p, "top2_risk_contribution": top2_sum,
            "cash_residual": None,
        }

    # Step 6: portfolio-level vol targeting (relative weights unchanged).
    if sigma_p > target:
        scale = target / sigma_p
        w = {s: w[s] * scale for s in selection}
        cash_residual = 1.0 - sum(w.values())
        sigma_p_eff = float(np.sqrt(
            sum(w[s] ** 2 * vol[s] ** 2 for s in selection)))
    else:
        cash_residual = 0.0
        sigma_p_eff = sigma_p

    # Weights are full-precision float64: rounding here would break the
    # conservation invariant (sum(w) + cash_residual == 1).  Consumers
    # round for display; the values themselves stay exact/reproducible.
    return {
        "blocked": OK,
        "reason": None,
        "selection": selection,
        "weights": dict(w),
        "sigma_p": sigma_p_eff,
        "top2_risk_contribution": top2_sum,
        "cash_residual": float(cash_residual),
    }
