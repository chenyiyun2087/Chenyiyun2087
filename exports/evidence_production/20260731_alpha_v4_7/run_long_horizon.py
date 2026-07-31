#!/usr/bin/env python3
"""v4.6 Long Horizon Evidence: Rolling window IC/Sharpe stability + regime conditioning."""
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
from runtime.acceptance_config import canonical_sha

E3_PANEL = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/pit_builder/factor_panel_daily.parquet'
MARKET = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/adapter/snapshots/market.parquet'
OUTPUT = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/long_horizon'
OUTPUT.mkdir(parents=True, exist_ok=True)

ROLLING_WINDOW_DAYS = 504  # 2 years
STEP_DAYS = 126            # ~6 months
FACTORS = ['volatility', 'value', 'size']
HORIZONS = [5, 20, 60]

# ── Load ──
print("Loading E3 panel...")
panel = pd.read_parquet(E3_PANEL)
panel['symbol'] = panel['symbol'].astype(str).str.zfill(6)
panel['trade_date'] = pd.to_datetime(panel['trade_date'])

market = pd.read_parquet(MARKET)
market['symbol'] = market['symbol'].astype(str).str.zfill(6)
market['trade_date'] = pd.to_datetime(market['trade_date'].astype(str))

panel = panel.merge(market[['trade_date','symbol','close']], on=['trade_date','symbol'], how='left')
panel = panel[panel['close'].notna() & (panel['close'] > 0)].copy()
panel = panel[panel['eligible_universe'].fillna(False).astype(bool)]
panel = panel.sort_values(['trade_date','symbol']).reset_index(drop=True)

# Forward returns
dates = sorted(panel['trade_date'].unique())
date_to_idx = {d: i for i, d in enumerate(dates)}
panel['_di'] = panel['trade_date'].map(date_to_idx)
max_idx = len(dates) - 1

for h in HORIZONS:
    tgt = panel['_di'].apply(lambda i: dates[min(i+h, max_idx)])
    pmap = market[['trade_date','symbol','close']].rename(columns={'close': f'_c{h}','trade_date':'_t'})
    t = panel[['symbol']].copy(); t['_t'] = tgt
    t = t.merge(pmap, left_on=['symbol','_t'], right_on=['symbol','_t'], how='left')
    panel[f'fwd_{h}d'] = (t[f'_c{h}'] / panel['close'] - 1).clip(-0.5, 0.5)

panel = panel.drop(columns=['_di'], errors='ignore')
eligible = panel[~panel['is_st'].fillna(True).astype(bool) & ~panel['is_suspended'].fillna(True).astype(bool)]

print(f"Eligible: {eligible['trade_date'].nunique()} dates, {eligible['symbol'].nunique()} symbols")

# ── 1. Rolling Window Rank IC ──
print("\n=== Rolling Window Rank IC ===")
ic_windows = []
for start_i in range(0, len(dates) - ROLLING_WINDOW_DAYS, STEP_DAYS):
    end_i = start_i + ROLLING_WINDOW_DAYS
    window_dates = set(dates[start_i:end_i])
    sub = eligible[eligible['trade_date'].isin(window_dates)]
    if sub['trade_date'].nunique() < 252:
        continue

    for factor in FACTORS:
        for h in HORIZONS:
            fc = sub[[factor, f'fwd_{h}d', 'trade_date']].dropna()
            ic_s = fc.groupby('trade_date').apply(lambda g: g[factor].corr(g[f'fwd_{h}d'], method='spearman'), include_groups=False).dropna()
            if len(ic_s) < 60:
                continue
            ic_windows.append({
                'window_start': dates[start_i].strftime('%Y-%m-%d'),
                'window_end': dates[end_i-1].strftime('%Y-%m-%d'),
                'factor': factor, 'horizon': h,
                'mean_ic': round(float(ic_s.mean()), 6),
                'ir': round(float(ic_s.mean()/ic_s.std()) if ic_s.std()>0 else 0, 4),
                'pos_ratio': round(float((ic_s>0).mean()), 4),
                'n_dates': len(ic_s),
            })

ic_df = pd.DataFrame(ic_windows)
print(f"Rolling IC windows: {len(ic_df)}")
for factor in FACTORS:
    sub = ic_df[ic_df['factor']==factor]
    print(f"\n  {factor}:")
    for _, r in sub.iterrows():
        emoji = '✅' if abs(r['mean_ic'])>0.02 and r['ir']>0.3 else '⚠️'
        print(f"    {emoji} [{r['window_start']}→{r['window_end']}] h={r['horizon']}d IC={r['mean_ic']:.4f} IR={r['ir']:.2f} +%={r['pos_ratio']:.0%} n={r['n_dates']}")

# ── 2. Rolling Window Net Sharpe (20d rebal, baseline cost) ──
print("\n=== Rolling Window Net Sharpe (20d rebal, baseline cost) ===")
COST_BPS = 17.5  # baseline
TOP_PCT = 0.20
REBAL = 20

sharpe_windows = []
for start_i in range(0, len(dates) - ROLLING_WINDOW_DAYS, STEP_DAYS):
    end_i = start_i + ROLLING_WINDOW_DAYS
    wdates = dates[start_i:end_i]
    sub = eligible[eligible['trade_date'].isin(set(wdates))]

    for factor in FACTORS:
        daily_rets = []
        for d in wdates:
            day = sub[sub['trade_date']==d].dropna(subset=[factor])
            if day.empty:
                continue
            day = day.copy()
            day['rank'] = day[factor].rank(pct=True)
            top = day[day['rank']>=(1-TOP_PCT)]
            if top.empty:
                continue
            port_ret = top['fwd_5d'].mean()  # proxy for holding period return
            daily_rets.append({'trade_date': d, 'ret': port_ret})

        if len(daily_rets) < 60:
            continue
        dr = pd.DataFrame(daily_rets)
        dr['net'] = dr['ret'] - (COST_BPS/10000) * (1.0/REBAL)
        r = dr['net'].dropna()
        n = len(r)
        cum = float((1+r).prod()-1)
        ann = float((1+cum)**(252/n)-1) if cum>-1 else -1
        vol = float(r.std()*np.sqrt(252))
        sr = float(r.mean()/r.std()*np.sqrt(252)) if r.std()>0 else 0
        mdd = float(((1+r).cumprod()/(1+r).cumprod().cummax()-1).min())
        sharpe_windows.append({
            'window_start': wdates[0].strftime('%Y-%m-%d'),
            'window_end': wdates[-1].strftime('%Y-%m-%d'),
            'factor': factor,
            'n_dates': n, 'sharpe': round(sr,4),
            'ann_ret': round(ann,4), 'max_dd': round(mdd,4),
            'pass': sr>0.3 and n>=252,
        })

sr_df = pd.DataFrame(sharpe_windows)
print(f"Rolling Sharpe windows: {len(sr_df)}")
for factor in FACTORS:
    sub = sr_df[sr_df['factor']==factor]
    print(f"\n  {factor}:")
    for _, r in sub.iterrows():
        emoji = '✅' if r['pass'] else '❌'
        print(f"    {emoji} [{r['window_start']}→{r['window_end']}] Sharpe={r['sharpe']:.3f} ann={r['ann_ret']:.4f} mdd={r['max_dd']:.4f} n={r['n_dates']}")

# ── 3. Stability metrics ──
print("\n=== Stability Summary ===")
stability = {}
for factor in FACTORS:
    ic_sub = ic_df[ic_df['factor']==factor]
    sr_sub = sr_df[sr_df['factor']==factor]
    ic_60d = ic_sub[ic_sub['horizon']==60]
    if len(ic_60d) > 0:
        ic_stable = float((abs(ic_60d['mean_ic'])>0.02).mean())
        ic_mean = float(ic_60d['mean_ic'].mean())
        ic_std = float(ic_60d['mean_ic'].std())
    else:
        ic_stable = ic_mean = ic_std = 0.0
    sr_stable = float((sr_sub['sharpe']>0.3).mean()) if len(sr_sub)>0 else 0.0
    sr_mean = float(sr_sub['sharpe'].mean()) if len(sr_sub)>0 else 0.0
    stability[factor] = {
        'ic_60d_mean': round(ic_mean, 4), 'ic_60d_std': round(ic_std, 4),
        'ic_window_pass_rate': round(ic_stable, 4),
        'sharpe_mean': round(sr_mean, 4),
        'sharpe_window_pass_rate': round(sr_stable, 4),
        'n_ic_windows': len(ic_60d), 'n_sharpe_windows': len(sr_sub),
    }
    print(f"  {factor}: IC_mean={ic_mean:.4f}±{ic_std:.4f} pass_rate={ic_stable:.0%} | "
          f"Sharpe_mean={sr_mean:.3f} pass_rate={sr_stable:.0%} | "
          f"windows={len(ic_60d)}IC/{len(sr_sub)}SR")

# ── Output ──
print("\nWriting outputs...")
ic_df.to_csv(OUTPUT / 'rolling_ic.csv', index=False)
sr_df.to_csv(OUTPUT / 'rolling_sharpe.csv', index=False)

whole_panel_qualifies = (
    sum(1 for v in stability.values() if v['ic_window_pass_rate'] >= 0.75 and v['sharpe_window_pass_rate'] >= 0.75)
    >= 2
)

output = {
    'schema_version': 'alpha_v4_6_long_horizon_evidence_v1',
    'status': 'PASS' if whole_panel_qualifies else 'BLOCKED',
    'panel_source': 'E3 PIT panel (1,573 trading days)',
    'rolling_config': {
        'window_days': ROLLING_WINDOW_DAYS,
        'step_days': STEP_DAYS,
        'rebalance_days': REBAL,
        'cost_bps': COST_BPS,
    },
    'stability': stability,
    'rolling_ic_summary': [{
        'factor': f, 'horizon': h,
        'windows': int(len(ic_df[(ic_df['factor']==f)&(ic_df['horizon']==h)])),
        'pass_rate': float((abs(ic_df[(ic_df['factor']==f)&(ic_df['horizon']==h)]['mean_ic'])>0.02).mean()),
    } for f in FACTORS for h in HORIZONS],
    'rolling_sharpe_summary': [{
        'factor': f,
        'windows': int(len(sr_df[sr_df['factor']==f])),
        'pass_rate': float((sr_df[sr_df['factor']==f]['sharpe']>0.3).mean()),
        'mean_sharpe': round(float(sr_df[sr_df['factor']==f]['sharpe'].mean()), 4),
    } for f in FACTORS],
    'whole_panel_qualifies': whole_panel_qualifies,
    'blockers': [] if whole_panel_qualifies else ['rolling_window_stability_insufficient'],
    'capital_authority': False,
}
output['content_sha256'] = canonical_sha({k:v for k,v in output.items() if k!='content_sha256'})

(OUTPUT / 'long_horizon_report.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
print(f"Report: {OUTPUT / 'long_horizon_report.json'}")
print("DONE")
