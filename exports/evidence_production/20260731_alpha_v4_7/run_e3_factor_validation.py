#!/usr/bin/env python3
"""Direct factor IC/attribution/long-short validation from E3 PIT panel."""
import json, os, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2] if '__file__' in dir() else Path.cwd()
for _ in range(3):
    if not (PROJECT_ROOT / 'runtime').exists():
        PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from runtime.acceptance_config import canonical_sha, load_validation_profile

E3_PANEL = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/pit_builder/factor_panel_daily.parquet'
MARKET = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/adapter/snapshots/market.parquet'
OUTPUT = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/validation_v2'
OUTPUT.mkdir(parents=True, exist_ok=True)

profile = load_validation_profile("alpha_v4_7")
FACTORS = ["size", "volatility", "liquidity", "momentum", "value", "market_beta"]
HORIZONS = [5, 10, 20, 60]

# ── Load & enrich ──
print("Loading E3 panel...")
panel = pd.read_parquet(E3_PANEL)
panel['symbol'] = panel['symbol'].astype(str).str.zfill(6)
panel['trade_date'] = pd.to_datetime(panel['trade_date'])

market = pd.read_parquet(MARKET)
market['symbol'] = market['symbol'].astype(str).str.zfill(6)
market['trade_date'] = pd.to_datetime(market['trade_date'].astype(str))

# Merge close prices
panel = panel.merge(market[['trade_date','symbol','close','pre_close','amount','circ_mv']], on=['trade_date','symbol'], how='left')
panel = panel[panel['close'].notna() & (panel['close'] > 0)].copy()
panel = panel.sort_values(['symbol', 'trade_date']).reset_index(drop=True)

# Compute forward returns
print("Computing forward returns...")
dates = sorted(panel['trade_date'].dropna().unique())
date_to_idx = {d: i for i, d in enumerate(dates)}
panel['_di'] = panel['trade_date'].map(date_to_idx)
max_idx = len(dates) - 1

for h in HORIZONS:
    target_idx = panel['_di'] + h
    target_dates = [dates[min(i, max_idx)] if i <= max_idx else pd.NaT for i in target_idx]
    price_map = panel[['symbol','trade_date','close']].rename(columns={'close': f'_fwd_close_{h}', 'trade_date': '_target_date'})
    temp = panel[['symbol','_di']].copy()
    temp['_target_date'] = target_dates
    temp = temp.merge(price_map, on=['symbol','_target_date'], how='left')
    panel[f'fwd_ret_{h}d'] = temp[f'_fwd_close_{h}'] / panel['close'] - 1.0

panel = panel.drop(columns=['_di'], errors='ignore')

# Filter eligible universe
eligible = panel[
    panel['eligible_universe'].fillna(False).astype(bool)
    & ~panel['is_st'].fillna(True).astype(bool)
    & ~panel['is_suspended'].fillna(True).astype(bool)
].copy()

print(f"Panel: {len(panel)} rows, Eligible: {len(eligible)} rows, {eligible['symbol'].nunique()} symbols, {eligible['trade_date'].nunique()} dates")

# ── Rank IC Analysis ──
print("Computing Rank IC...")
ic_results = []
dates_list = sorted(eligible['trade_date'].dropna().unique())
warmup = dates_list[:20]
qualified = eligible[~eligible['trade_date'].isin(warmup)]

for factor in FACTORS:
    for horizon in HORIZONS:
        fwd_col = f'fwd_ret_{horizon}d'
        sub = qualified[[factor, fwd_col, 'trade_date']].dropna()
        if sub.empty:
            continue
        ic_series = sub.groupby('trade_date').apply(
            lambda g: g[factor].corr(g[fwd_col], method='spearman'), include_groups=False
        )
        ic_series = ic_series.dropna()
        if len(ic_series) < 20:
            continue
        mean_ic = float(ic_series.mean())
        ir = float(mean_ic / ic_series.std()) if ic_series.std() > 0 else 0.0
        pos_ratio = float((ic_series > 0).mean())
        ic_results.append({
            'factor': factor, 'horizon': horizon,
            'mean_rank_ic': round(mean_ic, 6), 'ir': round(ir, 4),
            'positive_ratio': round(pos_ratio, 4),
            'n_dates': len(ic_series), 'n_stocks_mean': round(float(sub.groupby('trade_date').size().mean()), 0),
            'pass_ic_threshold': abs(mean_ic) > 0.02 and ir > 0.3 and len(ic_series) >= 252,
        })

ic_df = pd.DataFrame(ic_results)
ic_df = ic_df.sort_values(['factor', 'horizon'])
print("\n=== Rank IC Summary ===")
print(ic_df.to_string(index=False))

# ── Factor Long-Short (Top/Bottom 20%) ──
print("\n=== Factor Long-Short (Top/Bottom 20%) ===")
ls_results = []
for factor in FACTORS:
    for horizon in HORIZONS:
        fwd_col = f'fwd_ret_{horizon}d'
        sub = qualified[[factor, fwd_col, 'trade_date', 'symbol']].dropna()
        if sub.empty:
            continue
        # Cross-sectional ranking per date
        sub['rank'] = sub.groupby('trade_date')[factor].rank(pct=True)
        top = sub[sub['rank'] >= 0.8]
        bot = sub[sub['rank'] <= 0.2]
        # Equal-weight daily returns
        top_ret = top.groupby('trade_date')[fwd_col].mean()
        bot_ret = bot.groupby('trade_date')[fwd_col].mean()
        ls_ret = top_ret - bot_ret  # long top, short bottom
        common_dates = top_ret.index.intersection(bot_ret.index)
        ls_ret = ls_ret.loc[common_dates].dropna()

        if len(ls_ret) < 20:
            continue
        cumulative = float((1 + ls_ret).prod() - 1)
        ann_ret = float((1 + cumulative) ** (252.0 / len(ls_ret)) - 1) if cumulative > -1 else -1.0
        vol = float(ls_ret.std() * np.sqrt(252))
        sharpe = float(ls_ret.mean() / ls_ret.std() * np.sqrt(252)) if ls_ret.std() > 0 else 0.0
        nav = (1 + ls_ret).cumprod()
        mdd = float((nav / nav.cummax() - 1).min())

        ls_results.append({
            'factor': factor, 'horizon': horizon,
            'cumulative_ls': round(cumulative, 6),
            'ann_return': round(ann_ret, 4),
            'ann_vol': round(vol, 4),
            'sharpe_zero_rf': round(sharpe, 4),
            'max_drawdown': round(mdd, 4),
            'n_dates': len(ls_ret),
            'avg_top_symbols': round(float(top.groupby('trade_date').size().mean()), 0),
            'pass_economic': sharpe > 0.5 and len(ls_ret) >= 252,
        })

ls_df = pd.DataFrame(ls_results)
ls_df = ls_df.sort_values(['factor', 'horizon'])
print(ls_df.to_string(index=False))

# ── Summary ──
print("\n=== Qualification Summary ===")
n_dates = qualified['trade_date'].nunique()
print(f"Total qualified dates: {n_dates}")
print(f"Above 252-day minimum: {n_dates >= 252}")
print(f"Above 504-day target: {n_dates >= 504}")

passing_ic = ic_df[ic_df['pass_ic_threshold']]
print(f"\nFactors passing IC thresholds (|IC|>0.02, IR>0.3, ≥252d): {len(passing_ic)}")
if len(passing_ic) > 0:
    print(passing_ic[['factor', 'horizon', 'mean_rank_ic', 'ir', 'n_dates']].to_string(index=False))

passing_ls = ls_df[ls_df['pass_economic']]
print(f"\nFactors passing economic thresholds (Sharpe>0.5, ≥252d): {len(passing_ls)}")
if len(passing_ls) > 0:
    print(passing_ls[['factor', 'horizon', 'cumulative_ls', 'sharpe_zero_rf', 'max_drawdown', 'n_dates']].to_string(index=False))

# ── Output ──
output = {
    'schema_version': 'alpha_v4_7_e3_factor_validation_v1',
    'status': 'PASS' if n_dates >= 252 and len(passing_ic) > 0 else 'BLOCKED',
    'panel_source': 'E3 PIT builder output',
    'qualified_dates': n_dates,
    'qualified_symbols': int(eligible['symbol'].nunique()),
    'above_252_minimum': n_dates >= 252,
    'above_504_target': n_dates >= 504,
    'ic_summary': ic_results,
    'long_short_summary': ls_results,
    'passing_ic_factors': [
        {'factor': r['factor'], 'horizon': r['horizon'], 'mean_ic': r['mean_rank_ic'], 'ir': r['ir']}
        for r in ic_results if r['pass_ic_threshold']
    ],
    'passing_ls_factors': [
        {'factor': r['factor'], 'horizon': r['horizon'], 'sharpe': r['sharpe_zero_rf'], 'cumulative': r['cumulative_ls']}
        for r in ls_results if r['pass_economic']
    ],
    'blockers': [],
    'capital_authority': False,
}

if n_dates < 252:
    output['blockers'].append(f'history_below_252_days:{n_dates}')
if len(passing_ic) == 0:
    output['blockers'].append('no_factor_passes_ic_threshold')

output['content_sha256'] = canonical_sha(
    {k: v for k, v in output.items() if k != 'content_sha256'}
)

output_path = OUTPUT / 'e3_factor_validation_report.json'
output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
print(f"\nReport written to: {output_path}")

# Also save detailed CSVs
ic_df.to_csv(OUTPUT / 'rank_ic_detailed.csv', index=False)
ls_df.to_csv(OUTPUT / 'long_short_detailed.csv', index=False)
print("Detailed CSVs saved")
print("DONE")
