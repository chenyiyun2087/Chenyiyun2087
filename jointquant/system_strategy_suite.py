# -*- coding: utf-8 -*-
"""Chenyiyun2087 core strategies ported to JoinQuant.

Modes (set g.strategy_mode in initialize):
- production_governed_vol_position: liquidity-detail score + volatility sizing
- baseline_full_liquidity: pure liquidity ranking
- tiered_liquidity_then_bs_v2_proxy: JoinQuant-native proxy for local B/S score

Signals use context.previous_date and orders run at the next trading-day open.
"""
from jqdata import *
import math
import numpy as np
import pandas as pd


def initialize(context):
    set_benchmark('000905.XSHG')
    set_option('use_real_price', True)
    set_option('avoid_future_data', True)
    set_slippage(FixedSlippage(0.002))
    set_order_cost(OrderCost(open_tax=0, close_tax=0.001,
                             open_commission=0.0003, close_commission=0.0003,
                             close_today_commission=0, min_commission=5), type='stock')
    log.set_level('order', 'error')

    g.strategy_mode = 'production_governed_vol_position'
    # g.strategy_mode = 'baseline_full_liquidity'
    # g.strategy_mode = 'tiered_liquidity_then_bs_v2_proxy'

    g.benchmark = '000905.XSHG'
    g.top_n = 5
    g.hold_days = 10
    g.lookback = 65
    g.min_list_days = 375
    g.min_avg_money20 = 50000000.0
    g.max_single_weight = 0.15
    g.chunk_size = 450
    g.day_count = g.hold_days
    g.force_rebalance = True
    g.regime = 'neutral'
    g.exposure = {'strong_risk_on': 0.50, 'normal_risk_on': 0.45,
                  'neutral': 0.35, 'risk_off': 0.10, 'stress': 0.00}
    run_daily(prepare, time='9:05', reference_security=g.benchmark)
    run_daily(rebalance, time='9:35', reference_security=g.benchmark)
    run_daily(report, time='15:10', reference_security=g.benchmark)


def prepare(context):
    g.day_count += 1
    old = g.regime
    g.regime = market_regime(context.previous_date)
    if g.regime == 'stress' and old != 'stress':
        g.force_rebalance = True
    log.info('market_regime=%s target_exposure=%.0f%%' %
             (g.regime, 100 * target_exposure()))


def market_regime(asof):
    d = get_price(g.benchmark, end_date=asof, count=65, frequency='daily',
                  fields=['close', 'money'], panel=False, fill_paused=True, fq='pre')
    if d is None or len(d) < 25:
        return 'neutral'
    c = pd.to_numeric(d['close'], errors='coerce').dropna()
    m = pd.to_numeric(d['money'], errors='coerce').replace(0, np.nan)
    last, ma20, ma60 = float(c.iloc[-1]), float(c.tail(20).mean()), float(c.tail(60).mean())
    dd20 = last / float(c.tail(20).max()) - 1.0
    amt = float(m.tail(5).mean() / m.tail(20).mean()) if m.tail(20).mean() > 0 else 1.0
    if dd20 <= -0.08 or (last < ma60 and amt < 0.65):
        return 'stress'
    if last < ma60 and last < ma20:
        return 'risk_off'
    if last < ma20 or amt < 0.85:
        return 'neutral'
    if last > ma20 > ma60 and amt >= 1.05:
        return 'strong_risk_on'
    return 'normal_risk_on'


def target_exposure():
    x = float(g.exposure[g.regime])
    if g.strategy_mode == 'tiered_liquidity_then_bs_v2_proxy':
        return min(x, 0.35) if g.regime == 'strong_risk_on' else 0.0
    return x


def rebalance(context):
    if g.day_count < g.hold_days and not g.force_rebalance:
        return
    asof = context.previous_date
    factors = build_factors(asof)
    selected = select_stocks(factors)
    weights = size_positions(selected, target_exposure())
    weights = filter_buyable(context, weights)
    execute_targets(context, weights)
    g.day_count, g.force_rebalance = 0, False
    log.info('signal_date=%s mode=%s targets=%s exposure=%.1f%%' %
             (asof, g.strategy_mode, list(weights), 100 * sum(weights.values())))


def universe(asof):
    sec = get_all_securities(types=['stock'], date=asof)
    cur, out = get_current_data(), []
    for code, row in sec.iterrows():
        raw = code.split('.')[0]
        if code.endswith('.BJ') or raw.startswith(('4', '8')):
            continue
        if (asof - row['start_date']).days < g.min_list_days:
            continue
        name = str(cur[code].name or '')
        if cur[code].is_st or 'ST' in name or '*' in name or '退' in name:
            continue
        out.append(code)
    return out


def history_frame(codes, asof):
    frames = []
    for i in range(0, len(codes), g.chunk_size):
        try:
            d = get_price(codes[i:i + g.chunk_size], end_date=asof, count=g.lookback,
                          frequency='daily', fields=['close', 'high', 'low', 'money'],
                          panel=False, fill_paused=False, fq='pre')
            if d is not None and not d.empty:
                frames.append(d)
        except Exception as e:
            log.warn('get_price chunk failed: %s' % e)
    if not frames:
        return pd.DataFrame()
    d = pd.concat(frames, ignore_index=True)
    if 'code' not in d.columns or 'time' not in d.columns:
        d = d.reset_index()
    return d


def build_factors(asof):
    d = history_frame(universe(asof), asof)
    if d.empty:
        return d
    d['time'] = pd.to_datetime(d['time'])
    for c in ['close', 'high', 'low', 'money']:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    rows = []
    for code, x in d.sort_values(['code', 'time']).groupby('code'):
        x = x.dropna(subset=['close', 'high', 'low', 'money']).tail(g.lookback).copy()
        if len(x) < 20:
            continue
        ret = x['close'].pct_change()
        ma5m, ma20m = x['money'].tail(5).mean(), x['money'].tail(20).mean()
        money_chg = x['money'].pct_change().replace([np.inf, -np.inf], np.nan)
        row = {'code': code, 'close': x['close'].iloc[-1],
               'avg_money20': ma20m,
               'relative_money': x['money'].iloc[-1] / ma20m if ma20m > 0 else np.nan,
               'money_ratio_5_20': ma5m / ma20m if ma20m > 0 else np.nan,
               'impact': ((x['high'].iloc[-1] - x['low'].iloc[-1]) /
                          x['close'].iloc[-1] / max(x['money'].iloc[-1], 1.0)),
               'stability': 1.0 / (money_chg.tail(20).std() + 1e-9),
               'vol20': ret.tail(20).std(),
               'ret20': x['close'].iloc[-1] / x['close'].iloc[-21] - 1 if len(x) >= 21 else np.nan,
               'ma5': x['close'].tail(5).mean(), 'ma10': x['close'].tail(10).mean(),
               'ma20': x['close'].tail(20).mean(),
               'high20_prev': x['high'].iloc[-21:-1].max() if len(x) >= 21 else np.nan,
               'money_vs_ma5': x['money'].iloc[-1] / ma5m if ma5m > 0 else np.nan}
        if all(pd.notna(row[k]) for k in ['avg_money20', 'relative_money', 'impact', 'vol20']):
            rows.append(row)
    f = pd.DataFrame(rows)
    if f.empty:
        return f
    pct = lambda s: s.rank(pct=True) * 100.0
    f['s_liq'] = pct(f['avg_money20'])
    f.loc[f['avg_money20'] < g.min_avg_money20, 's_liq'] *= 0.3
    f['liq_rank_pct'] = f['avg_money20'].rank(pct=True, ascending=False)
    f['s_rel'], f['s_ratio'] = pct(f['relative_money']), pct(f['money_ratio_5_20'])
    f['s_impact'] = (1.0 - f['impact'].rank(pct=True)) * 100.0
    f['s_stable'] = pct(f['stability'])
    norm_liq = (f['s_liq'] / 30.0 * 100.0).clip(0, 100)
    f['liq_detail'] = (0.40 * norm_liq + 0.20 * f['s_rel'] + 0.15 * f['s_ratio'] +
                       0.15 * f['s_impact'] + 0.10 * f['s_stable']).clip(0, 100)
    f['s_rs'], f['s_vol'] = pct(f['ret20']), pct(f['money_vs_ma5'])
    trend = ((f['close'] > f['ma20']) & (f['ma5'] > f['ma10']) &
             (f['ma10'] > f['ma20'])).astype(int)
    breakout = (f['close'] > f['high20_prev']).astype(int)
    f['bs_proxy'] = (30 * trend + 25 * breakout + 0.20 * f['s_rs'] +
                     0.15 * f['s_vol'] + 0.10 * f['s_liq']).clip(0, 100)
    return f.replace([np.inf, -np.inf], np.nan).dropna(subset=['liq_detail', 'vol20'])


def select_stocks(f):
    if f.empty:
        return f
    if g.strategy_mode == 'baseline_full_liquidity':
        d = f.sort_values(['s_liq', 'avg_money20'], ascending=False)
    elif g.strategy_mode == 'tiered_liquidity_then_bs_v2_proxy':
        pool = (f['liq_rank_pct'] <= 0.10) | ((f['liq_rank_pct'] <= 0.40) & (f['bs_proxy'] >= 68))
        d = f[pool].sort_values(['bs_proxy', 's_liq', 'liq_detail'], ascending=False)
    else:
        d = f.sort_values(['liq_detail', 's_liq'], ascending=False)
    return d.head(g.top_n).copy()


def size_positions(selected, exposure):
    if selected.empty or exposure <= 0:
        return {}
    if g.strategy_mode != 'production_governed_vol_position':
        raw = dict((r['code'], 1.0) for _, r in selected.iterrows())
    else:
        raw = dict((r['code'], min(1.0 / g.top_n,
                   0.005 / max(float(r['vol20']) * math.sqrt(g.hold_days), 0.01)))
                   for _, r in selected.iterrows())
    total = sum(raw.values())
    w = dict((k, exposure * v / total) for k, v in raw.items()) if total > 0 else {}
    return dict((k, min(v, g.max_single_weight)) for k, v in w.items())


def filter_buyable(context, weights):
    cur, out = get_current_data(), {}
    for code, w in weights.items():
        p = float(cur[code].last_price or 0)
        if cur[code].paused or p <= 0:
            continue
        if code not in context.portfolio.positions and p >= float(cur[code].high_limit) - 1e-6:
            continue
        out[code] = w
    return out


def execute_targets(context, weights):
    cur = get_current_data()
    for code, pos in list(context.portfolio.positions.items()):
        if pos.total_amount <= 0 or code in weights:
            continue
        p = float(cur[code].last_price or 0)
        if not cur[code].paused and p > float(cur[code].low_limit) + 1e-6:
            order_target_value(code, 0)
    total = context.portfolio.total_value
    for code, w in weights.items():
        order_target_value(code, total * w)


def report(context):
    pos = ['%s %.1f%%' % (c, 100 * (p.price / p.avg_cost - 1))
           for c, p in context.portfolio.positions.items()
           if p.total_amount > 0 and p.avg_cost > 0]
    log.info('total=%.2f cash=%.2f positions=%s' %
             (context.portfolio.total_value, context.portfolio.available_cash, pos))
