"""Three-layer PIT factor pipeline for research-only ScoreRank validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Mapping

import numpy as np
import pandas as pd


RAW_FACTOR_COLUMNS = (
    "trend_20", "trend_60", "relative_strength_20", "breakout_distance",
    "volume_ratio", "volatility_contraction", "bias_20", "turnover_20",
    "amount_20", "amihud_illiquidity", "limit_state", "industry_momentum",
)


@dataclass(frozen=True)
class FactorModelArtifact:
    factor_model_id: str
    factor_schema_version: str
    train_end: str
    factor_directions: Mapping[str, int]
    factor_weights: Mapping[str, float]
    factor_expiry: Mapping[str, str]
    random_seed: int
    config_sha: str = ""

    def frozen(self) -> "FactorModelArtifact":
        payload = asdict(self)
        payload["config_sha"] = ""
        sha = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return FactorModelArtifact(**{**payload, "config_sha": sha})


def build_raw_pit_factors(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "trade_date", "adj_close", "high", "vol", "amount"}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"raw_factor_missing_columns:{','.join(missing)}")
    out = prices.sort_values(["symbol", "trade_date"]).copy()
    group = out.groupby("symbol", group_keys=False)
    close = pd.to_numeric(out["adj_close"], errors="coerce")
    out["_ret1"] = group["adj_close"].pct_change()
    out["trend_20"] = group["adj_close"].pct_change(20)
    out["trend_60"] = group["adj_close"].pct_change(60)
    out["relative_strength_20"] = out["trend_20"] - out.groupby("trade_date")["trend_20"].transform("median")
    prior_high20 = group["high"].transform(lambda values: pd.to_numeric(values, errors="coerce").shift(1).rolling(20).max())
    out["breakout_distance"] = close / prior_high20 - 1.0
    vol20 = group["vol"].transform(lambda values: pd.to_numeric(values, errors="coerce").shift(1).rolling(20).mean())
    out["volume_ratio"] = pd.to_numeric(out["vol"], errors="coerce") / vol20.replace(0, np.nan)
    vol_short = group["_ret1"].transform(lambda values: values.shift(1).rolling(20).std())
    vol_long = group["_ret1"].transform(lambda values: values.shift(1).rolling(60).std())
    out["volatility_contraction"] = 1.0 - vol_short / vol_long.replace(0, np.nan)
    ma20 = group["adj_close"].transform(lambda values: pd.to_numeric(values, errors="coerce").shift(1).rolling(20).mean())
    out["bias_20"] = close / ma20.replace(0, np.nan) - 1.0
    turnover_source = "turnover_rate" if "turnover_rate" in out else "vol"
    out["turnover_20"] = group[turnover_source].transform(lambda values: pd.to_numeric(values, errors="coerce").shift(1).rolling(20).mean())
    out["amount_20"] = group["amount"].transform(lambda values: pd.to_numeric(values, errors="coerce").shift(1).rolling(20).mean())
    out["_amihud"] = out["_ret1"].abs() / pd.to_numeric(out["amount"], errors="coerce").replace(0, np.nan)
    out["amihud_illiquidity"] = group["_amihud"].transform(lambda values: values.shift(1).rolling(20).mean())
    if {"up_limit", "down_limit"}.issubset(out.columns):
        out["limit_state"] = np.select(
            [close >= pd.to_numeric(out["up_limit"], errors="coerce"), close <= pd.to_numeric(out["down_limit"], errors="coerce")],
            [1.0, -1.0], default=0.0,
        )
    else:
        if "limit_state" in out.columns:
            out["limit_state"] = pd.to_numeric(out["limit_state"], errors="coerce").fillna(0.0)
        else:
            out["limit_state"] = 0.0
    if "industry" in out:
        out["industry_momentum"] = out.groupby(["trade_date", "industry"])["trend_20"].transform("mean")
    else:
        out["industry_momentum"] = np.nan
    return out.drop(columns=["_ret1", "_amihud"])


def normalize_factor_cross_sections(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    out = raw.copy()
    coverage_rows: list[dict[str, object]] = []
    for factor in RAW_FACTOR_COLUMNS:
        values = pd.to_numeric(out[factor], errors="coerce")
        for trade_date, index in out.groupby("trade_date").groups.items():
            section = values.loc[index]
            valid = section.dropna()
            coverage_rows.append({
                "trade_date": trade_date, "factor": factor,
                "row_count": len(section), "non_null_count": len(valid),
                "coverage": len(valid) / max(len(section), 1),
            })
            if valid.empty:
                continue
            lower, upper = valid.quantile([0.01, 0.99])
            clipped = section.clip(lower, upper)
            if "industry" in out:
                clipped = clipped - out.loc[index].assign(_value=clipped).groupby("industry")["_value"].transform("mean")
            if "market_cap" in out:
                cap_rank = pd.to_numeric(out.loc[index, "market_cap"], errors="coerce").rank(pct=True)
                valid_reg = clipped.notna() & cap_rank.notna()
                if valid_reg.sum() >= 3:
                    beta = np.polyfit(cap_rank[valid_reg], clipped[valid_reg], 1)
                    clipped = clipped - (beta[0] * cap_rank + beta[1])
            std = clipped.std(ddof=0)
            out.loc[index, f"{factor}_z"] = (clipped - clipped.mean()) / (std if std and np.isfinite(std) else 1.0)
            out.loc[index, f"{factor}_rank"] = clipped.rank(pct=True)
    return out, pd.DataFrame(coverage_rows)


def combine_frozen_alpha(normalized: pd.DataFrame, artifact: FactorModelArtifact) -> pd.DataFrame:
    frozen = artifact if artifact.config_sha else artifact.frozen()
    if pd.to_datetime(normalized["trade_date"]).min().date() <= pd.Timestamp(frozen.train_end).date():
        raise ValueError("factor_model_application_not_strictly_after_train_end")
    if set(frozen.factor_weights) != set(frozen.factor_directions):
        raise ValueError("factor_model_weight_direction_mismatch")
    result = normalized.copy()
    alpha = pd.Series(0.0, index=result.index)
    for factor, weight in frozen.factor_weights.items():
        column = f"{factor}_z"
        if column not in result:
            raise ValueError(f"factor_model_missing_normalized_factor:{factor}")
        alpha += pd.to_numeric(result[column], errors="coerce").fillna(0.0) * float(weight) * int(frozen.factor_directions[factor])
    result["composite_alpha"] = alpha
    result["factor_model_id"] = frozen.factor_model_id
    result["factor_schema_version"] = frozen.factor_schema_version
    result["factor_model_config_sha"] = frozen.config_sha
    return result
