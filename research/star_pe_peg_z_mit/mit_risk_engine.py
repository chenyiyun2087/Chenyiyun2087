"""MIT Trader risk management engine: GARCH stop-loss, Monte Carlo 1/4 Kelly, and Portfolio VaR."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm


def fit_garch11_sigma(returns: pd.Series | np.ndarray, window: int = 252) -> Tuple[float, Dict[str, Any]]:
    """Fit GARCH(1,1) with SLSQP MLE over the last `window` daily returns.
    Returns 1-day-ahead conditional volatility sigma and fitted parameters."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) > window:
        r = r[-window:]

    T = len(r)
    sample_var = float(np.var(r, ddof=1)) if T > 1 else 1e-4
    if T < 30 or sample_var <= 1e-12:
        return float(np.sqrt(max(sample_var, 1e-6))), {"model": "sample_fallback", "sample_var": sample_var}

    mu0 = float(np.mean(r))
    alpha0 = 0.05
    beta0 = 0.90
    omega0 = sample_var * max(1.0 - alpha0 - beta0, 0.01)

    def neg_loglik(params):
        mu, omega, alpha, beta = params
        eps = r - mu
        sigma2 = np.zeros(T)
        sigma2[0] = sample_var
        for t in range(1, T):
            sigma2[t] = omega + alpha * (eps[t - 1] ** 2) + beta * sigma2[t - 1]
            if sigma2[t] <= 1e-12:
                sigma2[t] = 1e-12
        ll = -0.5 * (np.log(2.0 * np.pi) + np.log(sigma2) + (eps ** 2) / sigma2)
        return -float(np.sum(ll))

    bounds = [(-0.1, 0.1), (1e-6, None), (1e-4, 0.40), (0.50, 0.98)]
    constraints = [{"type": "ineq", "fun": lambda p: 0.999 - p[2] - p[3]}]

    try:
        res = minimize(
            neg_loglik,
            [mu0, omega0, alpha0, beta0],
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 100, "ftol": 1e-6},
        )
        if res.success:
            mu, omega, alpha, beta = res.x
            eps = r - mu
            s2 = sample_var
            for t in range(1, T):
                s2 = omega + alpha * (eps[t - 1] ** 2) + beta * s2
            s2_next = omega + alpha * (eps[-1] ** 2) + beta * s2
            sigma_1d = float(np.sqrt(max(s2_next, 1e-8)))
            return sigma_1d, {
                "model": "garch11",
                "mu": round(float(mu), 6),
                "omega": round(float(omega), 8),
                "alpha": round(float(alpha), 6),
                "beta": round(float(beta), 6),
                "persistence": round(float(alpha + beta), 6),
            }
    except Exception:
        pass

    # Robust fallback: sample standard deviation
    fallback_sigma = float(np.sqrt(sample_var))
    return fallback_sigma, {"model": "sample_fallback", "sigma": round(fallback_sigma, 6)}


def compute_monte_carlo_kelly(
    historical_holding_returns: List[float] | np.ndarray,
    horizon: int = 5,
    stop_pct: float = 0.05,
    cost: float = 0.00075,
    n_paths: int = 10000,
    seed: int = 20261127,
) -> Optional[Dict[str, Any]]:
    """Bootstrap closed Z-trades daily returns into n_paths 5-day holding period paths.
    Touch of -stop_pct closes at stop; computes 1/4 Kelly sizing fraction."""
    pool = np.asarray(historical_holding_returns, dtype=float)
    pool = pool[np.isfinite(pool)]
    if len(pool) < 20:
        return None

    rng = np.random.default_rng(seed)
    draws = rng.choice(pool, size=(n_paths, horizon), replace=True)
    paths = np.cumprod(1.0 + draws, axis=1) - 1.0
    hit = (paths <= -stop_pct).any(axis=1)
    outcome = np.where(hit, -stop_pct, paths[:, -1]) - 2.0 * cost

    wins = outcome[outcome > 0]
    losses = outcome[outcome <= 0]
    p = len(wins) / n_paths
    b = float(wins.mean()) if len(wins) > 0 else 0.0
    a = float(-losses.mean()) if len(losses) > 0 else 1e-9

    if b > 0 and a > 0:
        kelly_full = p / a - (1.0 - p) / b
    elif b == 0:
        kelly_full = -1.0
    else:
        kelly_full = 1.0

    return {
        "p_win": round(float(p), 4),
        "avg_win": round(float(b), 5),
        "avg_loss": round(float(a), 5),
        "stop_hit_rate": round(float(hit.mean()), 4),
        "kelly_full": round(float(kelly_full), 4),
        "kelly_quarter": round(float(max(0.25 * kelly_full, 0.0)), 4),
        "median": round(float(np.median(outcome)), 5),
    }


def compute_portfolio_var(
    book_notionals: Dict[str, float],
    returns_matrix: pd.DataFrame,
    confidence: float = 0.99,
    window: int = 500,
) -> Tuple[float, float]:
    """Computes Historical-simulation and Parametric 1-day VaR (lecture 7: Value At Risk Models).
    Returns (historical_var, parametric_var)."""
    active_syms = [s for s, notional in book_notionals.items() if notional > 0 and s in returns_matrix.columns]
    if not active_syms:
        return 0.0, 0.0

    r_df = returns_matrix[active_syms].dropna().iloc[-window:]
    if len(r_df) < 30:
        # If insufficient joint history, use marginal volatilities assuming zero correlation
        w = np.array([book_notionals[s] for s in active_syms])
        stds = np.array([returns_matrix[s].dropna().iloc[-window:].std(ddof=1) if s in returns_matrix else 0.02 for s in active_syms])
        param_var = float(norm.ppf(confidence) * np.sqrt(np.sum((w * stds) ** 2)))
        return param_var, param_var

    w = np.array([book_notionals[s] for s in active_syms])
    pnl = r_df.values @ w
    hist_var = float(-np.quantile(pnl, 1.0 - confidence))

    cov = r_df.cov().values
    sd = float(np.sqrt(w @ cov @ w))
    param_var = float(norm.ppf(confidence) * sd - pnl.mean())

    return max(hist_var, 0.0), max(param_var, 0.0)


class MitRiskEngine:
    """Orchestrates GARCH stop-loss, 1/4 Kelly, single-loss budget, and VaR scaling."""

    def __init__(
        self,
        garch_window: int = 252,
        kelly_fraction: float = 0.25,
        max_account_fraction: float = 0.10,
        single_loss_budget_pct: float = 0.01,
        max_portfolio_var_pct: float = 0.02,
        daily_loss_limit_pct: float = 0.03,
        holding_horizon: int = 5,
        var_confidence: float = 0.99,
    ):
        self.garch_window = garch_window
        self.kelly_fraction = kelly_fraction
        self.max_account_fraction = max_account_fraction
        self.single_loss_budget_pct = single_loss_budget_pct
        self.max_portfolio_var_pct = max_portfolio_var_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.holding_horizon = holding_horizon
        self.var_confidence = var_confidence

    def evaluate_entry_ticket(
        self,
        symbol: str,
        target_sleeve_cash: float,
        account_nav: float,
        stock_returns: pd.Series,
        historical_closed_z_returns: List[float],
        current_book_notionals: Dict[str, float],
        returns_matrix: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Evaluates whether to enter and determines exact position sizing."""
        # 1. GARCH 1-day volatility and stop-loss distance: 2 * sigma * sqrt(5)
        sigma, gparams = fit_garch11_sigma(stock_returns, self.garch_window)
        stop_pct = min(max(2.0 * sigma * math.sqrt(self.holding_horizon), 0.02), 0.20)

        # 2. Monte Carlo 1/4 Kelly sizing
        # Use closed Z-trade daily returns if available >= 20, else stock trailing daily returns
        if len(historical_closed_z_returns) >= 20:
            kelly_pool = historical_closed_z_returns
        else:
            stock_clean = stock_returns.dropna().values
            kelly_pool = stock_clean[-252:] if len(stock_clean) >= 20 else stock_clean

        mc = compute_monte_carlo_kelly(
            kelly_pool,
            horizon=self.holding_horizon,
            stop_pct=stop_pct,
            cost=0.00075,
            n_paths=10000,
        )
        if mc is None or mc["kelly_full"] <= 0:
            return {
                "approved": False,
                "reason": f"Kelly non-positive ({mc['kelly_full'] if mc else 'None'})",
                "stop_pct": stop_pct,
                "sigma_1d": sigma,
                "gparams": gparams,
                "mc": mc,
            }

        # Kelly fraction of account
        f_kelly = self.kelly_fraction * mc["kelly_full"]

        # Budget constraints:
        # a. 10% NAV cap
        cap_notional = self.max_account_fraction * account_nav
        # b. 1% NAV stop-loss budget cap: notional * stop_pct <= 0.01 * account_nav
        budget_notional = (self.single_loss_budget_pct * account_nav) / stop_pct
        # c. Kelly notional
        kelly_notional = f_kelly * account_nav
        # d. Sleeve cash
        sleeve_notional = target_sleeve_cash

        # Initial candidate notional
        candidate_notional = min(cap_notional, budget_notional, kelly_notional, sleeve_notional)
        if candidate_notional <= 0:
            return {"approved": False, "reason": "Candidate notional <= 0", "stop_pct": stop_pct}

        # 3. Portfolio VaR constraint
        trial_book = dict(current_book_notionals)
        trial_book[symbol] = candidate_notional
        var_h, var_p = compute_portfolio_var(trial_book, returns_matrix, self.var_confidence)
        var_cap = self.max_portfolio_var_pct * account_nav

        # Scale down if VaR exceeds cap
        scale = 1.0
        while max(var_h, var_p) > var_cap and scale > 0.05:
            scale -= 0.05
            trial_book[symbol] = candidate_notional * scale
            var_h, var_p = compute_portfolio_var(trial_book, returns_matrix, self.var_confidence)

        if max(var_h, var_p) > var_cap:
            return {
                "approved": False,
                "reason": f"Portfolio VaR ({max(var_h, var_p):.2f}) exceeds cap ({var_cap:.2f})",
                "stop_pct": stop_pct,
                "var_h": var_h,
                "var_p": var_p,
                "var_cap": var_cap,
            }

        final_notional = candidate_notional * scale
        return {
            "approved": True,
            "final_notional": final_notional,
            "stop_pct": stop_pct,
            "sigma_1d": sigma,
            "gparams": gparams,
            "mc": mc,
            "var_1d": {"historical": var_h, "parametric": var_p, "cap": var_cap},
            "scale": scale,
        }
