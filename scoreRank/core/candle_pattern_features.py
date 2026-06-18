"""Build daily candle-pattern features for ScoreRank."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from scoreRank.core.candle_patterns.diagnosis.engine import DiagnoseEngine
from scoreRank.core.logging_utils import get_score_rank_logger


logger = get_score_rank_logger(__name__)

PATTERN_FEATURE_COLUMNS = [
    "pattern_score",
    "pattern_sentiment",
    "pattern_risk_level",
    "pattern_pass_count",
    "pattern_candidate_count",
    "bullish_pattern_count",
    "bearish_pattern_count",
    "top_pattern_ids",
    "top_pattern_names",
    "ashare_signal_keys",
    "pattern_diagnosis",
]


def _clip_text(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _join_limited(values: list[Any], limit: int = 255) -> str:
    out: list[str] = []
    total = 0
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        next_total = total + len(text) + (1 if out else 0)
        if next_total > limit:
            break
        out.append(text)
        total = next_total
    return ",".join(out)


def _empty_feature_row(symbol: str, diagnosis: str = "") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "pattern_score": 0.0,
        "pattern_sentiment": "neutral",
        "pattern_risk_level": "low",
        "pattern_pass_count": 0,
        "pattern_candidate_count": 0,
        "bullish_pattern_count": 0,
        "bearish_pattern_count": 0,
        "top_pattern_ids": "",
        "top_pattern_names": "",
        "ashare_signal_keys": "",
        "pattern_diagnosis": _clip_text(diagnosis, 512),
    }


def _normalize_symbol(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else text


def _prepare_symbol_bars(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "date" not in work.columns:
        if "trade_date" in work.columns:
            work["date"] = pd.to_datetime(work["trade_date"]).dt.strftime("%Y-%m-%d")
        else:
            work["date"] = [str(i) for i in range(len(work))]
    if "pct_chg" not in work.columns:
        work["pct_chg"] = work["close"].pct_change().fillna(0.0) * 100.0
    if "turnover" not in work.columns:
        work["turnover"] = 0.0

    keep = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]
    for col in keep:
        if col not in work.columns:
            work[col] = 0.0 if col != "date" else ""
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["open", "high", "low", "close"])
    return work[keep].sort_values("date").reset_index(drop=True)


def _name_lookup(names: pd.DataFrame | None) -> dict[str, str]:
    if names is None or names.empty or "symbol" not in names.columns:
        return {}
    name_col = "name" if "name" in names.columns else None
    if not name_col:
        return {}
    work = names[["symbol", name_col]].copy()
    work["symbol"] = work["symbol"].map(_normalize_symbol)
    work[name_col] = work[name_col].fillna("").astype(str)
    return dict(zip(work["symbol"], work[name_col]))


def diagnose_symbol_patterns(symbol: str, df: pd.DataFrame, name: str = "") -> dict[str, Any]:
    """Diagnose one symbol and return compact ScoreRank feature fields."""
    symbol = _normalize_symbol(symbol)
    if df is None or df.empty or len(df) < 5:
        return _empty_feature_row(symbol, "数据不足，无法诊断")

    bars = _prepare_symbol_bars(df)
    if len(bars) < 5:
        return _empty_feature_row(symbol, "数据不足，无法诊断")

    try:
        result = DiagnoseEngine().diagnose(symbol, name=name, df=bars)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Candle pattern diagnosis failed for %s: %s", symbol, exc)
        return _empty_feature_row(symbol, f"图形识别异常: {exc}")

    signals = result.signals or []
    passed = [s for s in signals if s.get("signal_state") == "pass"]
    candidates = [s for s in signals if s.get("signal_state") == "candidate"]
    ranked = sorted(
        passed + candidates,
        key=lambda s: (
            1 if s.get("signal_state") == "pass" else 0,
            float(s.get("score") or 0.0),
            float(s.get("confidence") or 0.0),
        ),
        reverse=True,
    )

    bullish_count = sum(1 for p in result.patterns if p) + sum(
        1 for s in signals if s.get("direction") == "bullish" and s.get("signal_state") in {"pass", "candidate"}
    )
    bearish_count = len(result.ashare_signals) + sum(
        1 for s in signals if s.get("direction") == "bearish" and s.get("signal_state") in {"pass", "candidate"}
    )

    row = _empty_feature_row(symbol)
    row.update(
        {
            "pattern_score": float(result.score if math.isfinite(float(result.score or 0)) else 0.0),
            "pattern_sentiment": result.sentiment or "neutral",
            "pattern_risk_level": result.risk_level or "low",
            "pattern_pass_count": len(passed),
            "pattern_candidate_count": len(candidates),
            "bullish_pattern_count": int(max(0, bullish_count)),
            "bearish_pattern_count": int(max(0, bearish_count)),
            "top_pattern_ids": _join_limited([s.get("pattern_id") for s in ranked[:6]], 255),
            "top_pattern_names": _join_limited(result.pattern_names[:6] + result.ashare_signal_names[:6], 255),
            "ashare_signal_keys": _join_limited(result.ashare_signals[:8], 255),
            "pattern_diagnosis": _clip_text(result.diagnosis, 512),
        }
    )
    return row


def build_candle_pattern_features(bars: pd.DataFrame, names: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build compact candle-pattern feature rows from long-form OHLCV bars."""
    columns = ["symbol", *PATTERN_FEATURE_COLUMNS]
    if bars is None or bars.empty or "symbol" not in bars.columns:
        return pd.DataFrame(columns=columns)

    work = bars.copy()
    work["symbol"] = work["symbol"].map(_normalize_symbol)
    work = work[work["symbol"].astype(str).str.len() > 0].copy()
    lookup = _name_lookup(names)

    rows: list[dict[str, Any]] = []
    for symbol, group in work.groupby("symbol", sort=False):
        rows.append(diagnose_symbol_patterns(symbol, group, name=lookup.get(symbol, "")))

    out = pd.DataFrame(rows)
    for col in columns:
        if col not in out.columns:
            out[col] = None
    return out[columns]
