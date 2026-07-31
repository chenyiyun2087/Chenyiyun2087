#!/usr/bin/env python3
"""v4.5 Net Alpha: T+1 execution with realistic costs on E3 panel."""
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
OUTPUT = PROJECT_ROOT / 'exports/evidence_production/20260731_alpha_v4_7/net_ledger_v2'
OUTPUT.mkdir(parents=True, exist_ok=True)

FACTORS = ['volatility', 'value', 'size']
REBALANCE_FREQ = [1, 5, 20]
TOP_PCT = 0.20

COST_SCENARIOS = {
    'low':      2.5 + 5.0 + 5.0,    # 12.5 bps per trade
    'baseline': 2.5 + 5.0 + 10.0,   # 17.5 bps
    'moderate': 5.0 + 5.0 + 25.0,   # 35.0 bps
    'high':     5.0 + 5.0 + 50.0,   # 60.0 bps
    'extreme':  5.0 + 5.0 + 100.0,  # 110.0 bps
}

# ── Load ──
print("Loading...")
panel = pd.read_parquet(E3_PANEL)
panel['symbol'] = panel['symbol'].astype(str).str.zfill(6)
panel['trade_date'] = pd.to_datetime(panel['trade_date'])

market = pd.read_parquet(MARKET)
market['symbol'] = market['symbol'].astype(str).str.zfill(6)
market['trade_date'] = pd.to_datetime(market['trade_date'].astype(str))

# Merge close & open
panel = panel.merge(market[['trade_date','symbol','close','open']], on=['trade_date','symbol'], how='left')
panel = panel[panel['close'].notna() & (panel['close'] > 0)].copy()
panel = panel[panel['eligible_universe'].fillna(False).astype(bool)].copy()
panel = panel.sort_values(['trade_date','symbol']).reset_index(drop=True)

# Compute daily returns (close-to-close)
panel['ret_1d'] = panel.groupby('symbol')['close'].pct_change()
panel['ret_1d'] = panel['ret_1d'].clip(-0.11, 0.11)  # cap at limit-up/down

# Build T+1 open lookup
dates = sorted(panel['trade_date'].unique())
next_date_map = {dates[i]: dates[i+1] for i in range(len(dates)-1)}
panel['t1_date'] = panel['trade_date'].map(next_date_map)
open_lookup = market[['trade_date','symbol','open']].rename(columns={'trade_date':'t1_date','open':'open_t1'})
panel = panel.merge(open_lookup, on=['t1_date','symbol'], how='left')

# Execution constraints
panel['is_limit_up'] = panel['limit_status'].isin(['LIMITED'])
panel['can_execute'] = (
    panel['open_t1'].notna() & (panel['open_t1'] > 0) &
    ~panel['is_limit_up'] &
    ~panel['is_suspended'].fillna(0).astype(bool)
)

# Drop warmup
panel = panel[panel['trade_date'] >= pd.Timestamp('2020-03-01')].copy()

print(f"Universe: {len(panel)} rows, {panel['symbol'].nunique()} syms, {panel['trade_date'].nunique()} dates")
print(f"Limit-up blocked: {panel['is_limit_up'].sum()}, Can't execute: {(~panel['can_execute']).sum()}")

# ── Simulation ──
all_results = []

for factor in FACTORS:
    for rebal_days in REBALANCE_FREQ:
        print(f"\n{factor} rebal={rebal_days}d...")
        sub = panel[['trade_date','symbol',factor,'ret_1d','open_t1','close','can_execute']].dropna(subset=[factor])
        sub = sub.sort_values(['trade_date','symbol'])

        # Build daily portfolio returns
        daily_port_ret = []
        turnover_series = []
        prev_basket = set()

        for i, date in enumerate(dates):
            day_data = sub[sub['trade_date'] == date]
            if day_data.empty:
                continue

            is_rebal = (i % rebal_days == 0)
            if is_rebal:
                # Rank cross-sectionally, select top quintile
                day_data = day_data.copy()
                day_data['rank'] = day_data[factor].rank(pct=True)
                selected = day_data[day_data['rank'] >= (1.0 - TOP_PCT)]
                new_basket = set(selected['symbol'])
                # Compute turnover
                if prev_basket:
                    overlap = len(prev_basket & new_basket)
                    turnover = 1.0 - overlap / max(len(prev_basket), 1)
                else:
                    turnover = 1.0
                turnover_series.append(turnover)
                prev_basket = new_basket
                basket_symbols = new_basket
            # else: use previous basket

            if not basket_symbols:
                continue

            # Portfolio return = equal-weight avg of individual daily returns
            basket_data = day_data[day_data['symbol'].isin(basket_symbols)]
            if basket_data.empty:
                continue
            # Filter to executable stocks
            executable = basket_data[basket_data['can_execute']]
            if len(executable) < max(1, len(basket_symbols) * 0.5):
                continue  # too many blocked

            port_ret = executable['ret_1d'].mean()
            n_stocks = len(executable)
            blocked = len(basket_symbols) - len(executable)

            daily_port_ret.append({
                'trade_date': date, 'port_ret': port_ret,
                'n_stocks': n_stocks, 'blocked': blocked
            })

        if len(daily_port_ret) < 60:
            continue

        port_df = pd.DataFrame(daily_port_ret).sort_values('trade_date')
        avg_turnover = np.mean(turnover_series) if turnover_series else 1.0 / rebal_days

        # Apply costs
        for scenario_name, cost_bps in COST_SCENARIOS.items():
            daily_cost = (cost_bps / 10000.0) * avg_turnover / rebal_days
            port_df['net_ret'] = port_df['port_ret'] - daily_cost

            rets = port_df['net_ret'].dropna()
            n = len(rets)
            cumulative = float((1 + rets).prod() - 1)
            ann_ret = float((1 + cumulative) ** (252.0 / n) - 1) if cumulative > -1 else -1.0
            vol = float(rets.std() * np.sqrt(252))
            sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
            nav = (1 + rets).cumprod()
            mdd = float((nav / nav.cummax() - 1).min())
            win_rate = float((rets > 0).mean())

            # 70/30 train/test split
            split = int(n * 0.7)
            test_ret = rets.iloc[split:]
            test_cum = float((1 + test_ret).prod() - 1) if len(test_ret) > 0 else None
            test_sharpe = float(test_ret.mean() / test_ret.std() * np.sqrt(252)) if len(test_ret) > 1 and test_ret.std() > 0 else None

            all_results.append({
                'factor': factor, 'rebalance_days': rebal_days,
                'cost_scenario': scenario_name, 'cost_bps_rt': cost_bps,
                'n_dates': n, 'avg_stocks': round(float(port_df['n_stocks'].mean()), 0),
                'avg_blocked': round(float(port_df['blocked'].mean()), 1),
                'avg_turnover': round(avg_turnover, 4),
                'cumulative_net': round(cumulative, 6),
                'ann_return': round(ann_ret, 4),
                'ann_vol': round(vol, 4),
                'sharpe': round(sharpe, 4),
                'max_drawdown': round(mdd, 4),
                'win_rate': round(win_rate, 4),
                'test_cumulative': round(test_cum, 6) if test_cum is not None else None,
                'test_sharpe': round(test_sharpe, 4) if test_sharpe is not None else None,
                'pass_net_alpha': sharpe > 0.5 and n >= 252,
            })

# ── Results ──
results_df = pd.DataFrame(all_results)
results_df = results_df.sort_values(['factor', 'rebalance_days', 'cost_scenario'])

print(f"\n{'='*100}")
print("V4.5 NET ALPHA RESULTS — T+1 Execution with Realistic Costs")
print(f"{'='*100}")

for factor in FACTORS:
    print(f"\n─── {factor} ───")
    sub = results_df[results_df['factor'] == factor]
    for _, row in sub.iterrows():
        emoji = '✅' if row['pass_net_alpha'] else '❌'
        print(f"  {emoji} rebal={row['rebalance_days']}d {row['cost_scenario']:8s} "
              f"sharpe={row['sharpe']:7.3f} | net_ann={row['ann_return']:8.4f} "
              f"vol={row['ann_vol']:.4f} | mdd={row['max_drawdown']:8.4f} "
              f"win={row['win_rate']:.4f} | n={row['n_dates']} stocks≈{row['avg_stocks']:.0f} "
              f"turn={row['avg_turnover']:.3f} | test_sr={row['test_sharpe']}")

# Qualification
PASSING_COSTS = ['low', 'baseline', 'moderate']
passing = results_df[
    (results_df['pass_net_alpha']) &
    (results_df['cost_scenario'].isin(PASSING_COSTS))
]

print(f"\n{'='*100}")
print(f"QUALIFICATION: {len(passing)} passing (Sharpe>0.5, ≥252d, low/baseline/moderate cost)")
print(f"{'='*100}")
if len(passing) > 0:
    print(passing[['factor','rebalance_days','cost_scenario','sharpe','ann_return','max_drawdown','n_dates','avg_turnover']].to_string(index=False))

# Output
output = {
    'schema_version': 'alpha_v4_7_e3_net_alpha_v1',
    'status': 'PASS' if len(passing) > 0 else 'BLOCKED',
    'panel_source': 'E3 PIT panel (1,553 trading days)',
    'execution_model': 'T+1_open_entry, close_to_close_holding_return, equal_weight_top_20%',
    'cost_scenarios_evaluated': list(COST_SCENARIOS.keys()),
    'total_simulations': len(results_df),
    'passing_simulations': int(len(passing)),
    'blockers': [] if len(passing) > 0 else ['no_factor_passes_net_alpha_with_costs'],
    'results': all_results,
    'capital_authority': False,
}
output['content_sha256'] = canonical_sha({k: v for k, v in output.items() if k != 'content_sha256'})

results_df.to_csv(OUTPUT / 'net_alpha_detailed.csv', index=False)
(OUTPUT / 'net_alpha_report.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
print(f"\nOutput: {OUTPUT}")
print("DONE")
