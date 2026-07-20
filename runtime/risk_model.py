"""PIT portfolio risk model with shrinkage covariance and contribution reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioRiskReport:
    portfolio_volatility: float
    beta: float
    top2_risk_contribution: float
    industry_risk_contribution: dict[str, float]
    symbol_risk_contribution: dict[str, float]
    factor_exposure: dict[str, float]
    liquidity_risk: float
    extreme_downside_exposure: float
    correlated_theme_exposure: dict[str, float]


def ledoit_wolf_covariance(returns: pd.DataFrame) -> pd.DataFrame:
    """Shrink the sample covariance toward a scaled identity target."""
    clean = returns.apply(pd.to_numeric, errors="coerce").dropna(how="all").fillna(0.0)
    if clean.shape[0] < 3 or clean.shape[1] == 0:
        raise ValueError("risk_model_insufficient_returns")
    sample = clean.cov().to_numpy(dtype=float)
    target = np.eye(sample.shape[0]) * float(np.trace(sample) / max(sample.shape[0], 1))
    demeaned = clean.to_numpy(dtype=float) - clean.to_numpy(dtype=float).mean(axis=0)
    phi = 0.0
    for row in demeaned:
        outer = np.outer(row, row)
        phi += float(((outer - sample) ** 2).sum())
    phi /= max(len(demeaned), 1)
    rho = float(((sample - target) ** 2).sum())
    shrinkage = min(1.0, max(0.0, phi / max(rho * len(demeaned), 1e-18)))
    covariance = shrinkage * target + (1.0 - shrinkage) * sample
    return pd.DataFrame(covariance, index=clean.columns, columns=clean.columns)


def build_risk_report(
    *, weights: Mapping[str, float], returns: pd.DataFrame,
    industries: Mapping[str, str], themes: Mapping[str, str],
    factor_loadings: pd.DataFrame, benchmark_returns: pd.Series,
    liquidity_scores: Mapping[str, float], downside_scores: Mapping[str, float],
) -> PortfolioRiskReport:
    symbols = list(weights)
    if not symbols or any(symbol not in returns.columns for symbol in symbols):
        raise ValueError("risk_model_symbol_returns_missing")
    covariance = ledoit_wolf_covariance(returns[symbols]) * 252.0
    w = np.array([float(weights[symbol]) for symbol in symbols])
    variance = float(w @ covariance.to_numpy() @ w)
    volatility = float(np.sqrt(max(variance, 0.0)))
    marginal = covariance.to_numpy() @ w
    contributions = w * marginal / max(variance, 1e-18)
    symbol_rc = {symbol: float(value) for symbol, value in zip(symbols, contributions)}
    industry_rc: dict[str, float] = {}
    theme_exposure: dict[str, float] = {}
    for symbol in symbols:
        industry = str(industries.get(symbol) or "")
        theme = str(themes.get(symbol) or "")
        if not industry or not theme:
            raise ValueError(f"risk_model_classification_missing:{symbol}")
        industry_rc[industry] = industry_rc.get(industry, 0.0) + symbol_rc[symbol]
        theme_exposure[theme] = theme_exposure.get(theme, 0.0) + float(weights[symbol])
    portfolio_returns = returns[symbols].fillna(0.0).to_numpy() @ w
    benchmark = pd.to_numeric(benchmark_returns, errors="coerce").fillna(0.0).to_numpy()
    n = min(len(portfolio_returns), len(benchmark))
    beta = float(np.cov(portfolio_returns[-n:], benchmark[-n:])[0, 1] / max(np.var(benchmark[-n:]), 1e-18)) if n >= 3 else 0.0
    exposures = {}
    for factor in ("size", "beta", "volatility", "liquidity", "momentum", "value", "specific"):
        if factor in factor_loadings.columns:
            exposures[factor] = float(sum(float(weights[s]) * float(factor_loadings.loc[s, factor]) for s in symbols))
    top2 = sum(sorted((abs(value) for value in symbol_rc.values()), reverse=True)[:2])
    return PortfolioRiskReport(
        portfolio_volatility=volatility, beta=beta, top2_risk_contribution=float(top2),
        industry_risk_contribution=industry_rc, symbol_risk_contribution=symbol_rc,
        factor_exposure=exposures,
        liquidity_risk=float(sum(float(weights[s]) * float(liquidity_scores.get(s, 1.0)) for s in symbols)),
        extreme_downside_exposure=float(sum(float(weights[s]) * float(downside_scores.get(s, 0.0)) for s in symbols)),
        correlated_theme_exposure=theme_exposure,
    )
