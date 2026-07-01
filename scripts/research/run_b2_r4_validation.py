#!/usr/bin/env python3
"""
B2_SCALE40 R4-R6: 等实际暴露基线 + 持仓级归因 + 状态诊断

R4: STATIC40-45 细粒度等实际暴露对照
R5: 4路径持仓级收益归因 (C0/C1/C2/C3)
R6: 风险状态机制诊断

Usage:
    python scripts/research/run_b2_r4_validation.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys, hashlib
from datetime import datetime, date
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    _safe_float, add_liquidity_derived_features,
    build_market_environment, build_strategy_specs, load_prices, load_scores,
)
from scripts.research_trusted_strategy_account_backtest import (
    AccountState, _rebalance, _price_lookup_for_day, _score_day_frame,
    _build_targets_cache, _equity,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_fsc1_validation import build_anchor_risk_state
from scripts.research.run_fsc1_r7_validation import run_b2_backtest

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"


def _metrics(nav_df):
    if nav_df is None or nav_df.empty or "nav" not in nav_df.columns: return {}
    nav = nav_df["nav"].values
    tr = float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak = np.maximum.accumulate(nav); dd = float(np.min((nav-peak)/peak))
    n = len(nav); ar = float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    dr = np.diff(nav)/nav[:-1] if n>1 else np.array([0])
    cv = float(-np.mean(np.sort(dr)[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul = float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca = float(ar/abs(dd)) if abs(dd)>0 else 0.0
    # DD duration
    ds = (nav-peak)/peak; ti = int(np.argmin(ds))
    dds = ti; [dds:=i+1 for i in range(ti,-1,-1) if ds[i]>-0.001]; dds = dds if dds<ti else 0
    dde = ti; [dde:=i for i in range(ti,n) if ds[i]>-0.001]; dde = dde if dde>ti else n-1
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),
            "cvar95":round(cv,6),"ulcer":round(ul,6),"dd_duration":dde-dds,"n_days":n}


# ══════════════════════════════════════════════════════════════════════
# R5: 4-path counterfactual decomposition
# ══════════════════════════════════════════════════════════════════════

def build_counterfactuals(s60_nav, b2_nav) -> dict:
    """
    R5: Build 4 counterfactual NAV paths.
    C0 = S60
    C1 = S60 holdings return × B2 exposure (same stocks, less risk)
    C2 = B2 holdings return × S60 exposure (different stocks, same risk)
    C3 = B2
    """
    if s60_nav.empty or b2_nav.empty: return {}

    s60 = s60_nav.copy(); b2 = b2_nav.copy()
    n = min(len(s60), len(b2))

    # Extract daily total returns and exposures
    s60_nav_vals = s60["nav"].values[:n]
    b2_nav_vals = b2["nav"].values[:n]
    s60_exp = np.array([s60.iloc[i].get("actual_exposure", s60.iloc[i].get("position_ratio",0.60))
                         for i in range(n)])
    b2_exp = np.array([b2.iloc[i].get("actual_exposure", b2.iloc[i].get("position_ratio",0.60))
                        for i in range(n)])

    # Approximate holdings return = total return / exposure
    # (crude but directionally correct for decomposition)
    s60_daily = np.diff(s60_nav_vals) / s60_nav_vals[:-1] if n>1 else np.array([0])
    b2_daily = np.diff(b2_nav_vals) / b2_nav_vals[:-1] if n>1 else np.array([0])

    s60_holdings_ret = np.zeros(n-1)
    b2_holdings_ret = np.zeros(n-1)
    for i in range(n-1):
        e_s = max(s60_exp[i], 0.01)
        e_b = max(b2_exp[i], 0.01)
        s60_holdings_ret[i] = s60_daily[i] / e_s  # approximate unlevered return
        b2_holdings_ret[i] = b2_daily[i] / e_b

    # Build 4 paths
    c0, c1, c2, c3 = [1.0], [1.0], [1.0], [1.0]
    for i in range(n-1):
        hr_s = s60_holdings_ret[i]
        hr_b = b2_holdings_ret[i]
        e_s = max(s60_exp[i], 0.01)
        e_b = max(b2_exp[i], 0.01)

        c0.append(c0[-1] * (1.0 + hr_s * e_s))  # S60
        c1.append(c1[-1] * (1.0 + hr_s * e_b))  # S60 stocks, B2 exposure
        c2.append(c2[-1] * (1.0 + hr_b * e_s))  # B2 stocks, S60 exposure
        c3.append(c3[-1] * (1.0 + hr_b * e_b))  # B2

    # Pad to n
    for arr in [c0,c1,c2,c3]:
        while len(arr) < n: arr.append(arr[-1])

    c0_m = _metrics(pd.DataFrame({"nav": c0}))
    c1_m = _metrics(pd.DataFrame({"nav": c1}))
    c2_m = _metrics(pd.DataFrame({"nav": c2}))
    c3_m = _metrics(pd.DataFrame({"nav": c3}))

    # Decomposition
    exposure_effect = c1_m.get("total_return",0) - c0_m.get("total_return",0)
    holdings_effect = c2_m.get("total_return",0) - c0_m.get("total_return",0)
    total_effect = c3_m.get("total_return",0) - c0_m.get("total_return",0)
    interaction = total_effect - exposure_effect - holdings_effect

    return {
        "c0_return": c0_m.get("total_return",0), "c0_calmar": c0_m.get("calmar",0),
        "c1_return": c1_m.get("total_return",0), "c1_calmar": c1_m.get("calmar",0),
        "c2_return": c2_m.get("total_return",0), "c2_calmar": c2_m.get("calmar",0),
        "c3_return": c3_m.get("total_return",0), "c3_calmar": c3_m.get("calmar",0),
        "exposure_effect": round(exposure_effect,6),
        "holdings_effect": round(holdings_effect,6),
        "total_effect": round(total_effect,6),
        "interaction": round(interaction,6),
        "exposure_pct": round(abs(exposure_effect)/max(abs(total_effect),0.001)*100,1),
        "holdings_pct": round(abs(holdings_effect)/max(abs(total_effect),0.001)*100,1),
        "c0_nav": c0, "c1_nav": c1, "c2_nav": c2, "c3_nav": c3,
    }


# ══════════════════════════════════════════════════════════════════════
# R6: Risk state diagnostics
# ══════════════════════════════════════════════════════════════════════

def diagnose_risk_state(anchor_risk, s60_nav, b2_nav):
    """R6: Diagnose risk state mechanism."""
    risk_seq = [bool(r) for r in anchor_risk["risk_state"].values]
    signal_dates = anchor_risk["signal_date"].values
    n_total = len(risk_seq)
    n_risk = sum(risk_seq)

    # Find episodes
    episodes = []
    in_ep = False; ep_start = None
    for i in range(len(risk_seq)):
        if risk_seq[i] and not in_ep:
            in_ep = True; ep_start = i
        elif not risk_seq[i] and in_ep:
            episodes.append({"start_idx": ep_start, "end_idx": i-1,
                              "start_date": str(signal_dates[ep_start]),
                              "end_date": str(signal_dates[i-1]),
                              "duration": i - ep_start})
            in_ep = False
    if in_ep:
        episodes.append({"start_idx": ep_start, "end_idx": n_total-1,
                          "start_date": str(signal_dates[ep_start]),
                          "end_date": "ongoing", "duration": n_total - ep_start})

    # Forward returns after risk state changes
    risk_days_mask = np.array(risk_seq)
    normal_days_mask = ~risk_days_mask

    s60_nav_arr = s60_nav["nav"].values if len(s60_nav)>0 else np.array([1])
    b2_nav_arr = b2_nav["nav"].values if len(b2_nav)>0 else np.array([1])
    s60_daily = np.diff(s60_nav_arr)/s60_nav_arr[:-1] if len(s60_nav_arr)>1 else np.array([0])
    b2_daily = np.diff(b2_nav_arr)/b2_nav_arr[:-1] if len(b2_nav_arr)>1 else np.array([0])

    # Truncate to min length
    mlen = min(len(s60_daily), len(risk_days_mask)-1)
    s60_daily = s60_daily[:mlen]; b2_daily = b2_daily[:mlen]
    risk_days_mask = risk_days_mask[:mlen+1]; normal_days_mask = normal_days_mask[:mlen+1]

    risk_s60 = s60_daily[risk_days_mask[1:mlen+1]] if sum(risk_days_mask[1:mlen+1])>0 else np.array([0])
    normal_s60 = s60_daily[normal_days_mask[1:mlen+1]] if sum(normal_days_mask[1:mlen+1])>0 else np.array([0])
    risk_b2 = b2_daily[risk_days_mask[1:mlen+1]] if sum(risk_days_mask[1:mlen+1])>0 else np.array([0])

    return {
        "total_days": n_total, "risk_days": n_risk, "risk_pct": round(n_risk/n_total*100,1),
        "n_episodes": len(episodes),
        "episode_durations": [e["duration"] for e in episodes],
        "avg_episode_duration": round(np.mean([e["duration"] for e in episodes]),0) if episodes else 0,
        "risk_s60_daily_mean": round(float(np.mean(risk_s60))*10000,1) if len(risk_s60)>0 else 0,
        "normal_s60_daily_mean": round(float(np.mean(normal_s60))*10000,1) if len(normal_s60)>0 else 0,
        "risk_b2_daily_mean": round(float(np.mean(risk_b2))*10000,1) if len(risk_b2)>0 else 0,
        "risk_s60_cvar95": round(float(-np.mean(np.sort(risk_s60)[:max(1,int(len(risk_s60)*0.05))])),6) if len(risk_s60)>20 else 0,
        "classification": "DYNAMIC_ASSET_ALLOCATION" if n_risk/n_total > 0.5 else "TAIL_RISK_CONTROLLER",
        "episodes": episodes,
    }


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2023-01-03")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--initial-cash", type=float, default=500000.0)
    args = parser.parse_args()

    print("=" * 60)
    print("B2_SCALE40 R4-R6")
    print("=" * 60)

    db_url = build_sqlalchemy_url(); engine = create_engine(db_url)
    print("Loading...")
    cal = _build_calendar(engine, args.start_date, args.end_date); cal = sorted(set(cal))
    s2e, e2s = _build_signal_to_exec_map(cal)
    it = load_index_trends_pit(engine, ["000300.SH","399006.SZ"], cal)
    for d in cal:
        if d not in it: it[d] = {"000300.SH":0.0,"399006.SZ":0.0}
    prices = load_prices(engine, args.start_date, args.end_date, 30)
    prices["_ds"]=pd.to_datetime(prices["trade_date"]); ps=prices.sort_values("_ds").reset_index(drop=True)
    pdi=ps.groupby("trade_date",sort=True).indices
    scores=load_scores(engine,start_date=args.start_date,end_date=args.end_date)
    scores=add_liquidity_derived_features(scores,ps)
    scores["_ds"]=pd.to_datetime(scores["trade_date"]); ss=scores.sort_values("_ds").reset_index(drop=True)
    sdi=ss.groupby("trade_date",sort=True).indices
    try: me=build_market_environment(ss,ps)
    except: me=pd.DataFrame()
    specs=build_strategy_specs()

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir=OUT_ROOT/f"b2_r4_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True,exist_ok=True)
    print(f"Output: {out_dir}")

    common=dict(engine=engine,scores=ss,prices=ps,market_env=me,calendar=cal,
                signal_to_exec=s2e,exec_to_signal=e2s,sdi=sdi,pdi=pdi,
                it_trends=it,specs=specs,start_date=args.start_date,
                end_date=args.end_date,initial_cash=args.initial_cash)

    # ── Anchor ─────────────────────────────────────────────────
    print("\n=== Anchor ===")
    anchor = build_anchor_risk_state(**common)
    n_risk = int(anchor["risk_state"].sum())
    print(f"  {n_risk}/{len(anchor)} risk days")

    # ══════════════════════════════════════════════════════════
    # R4: True equal-actual-exposure statics
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R4: 真实等实际暴露静态基线 ===")
    results = {}

    for pos_pct in range(38, 47):
        label = f"STATIC{pos_pct}"
        r = run_b2_backtest(label, anchor, pos_pct/100, pos_pct/100, **common)
        results[label] = r

    r = run_b2_backtest("B2", anchor, 0.60, 0.40, **common)
    results["B2"] = r
    b2_m = r["metrics"]
    b2_avg_exp = float(r["nav_df"]["actual_exposure"].mean())

    r = run_b2_backtest("S60", anchor, 0.60, 0.60, **common)
    results["S60"] = r

    # Find closest two statics (one below, one above B2 actual exposure)
    static_exposures = {}
    for pos_pct in range(38, 47):
        avg_e = float(results[f"STATIC{pos_pct}"]["nav_df"]["actual_exposure"].mean())
        static_exposures[pos_pct] = avg_e

    below = [p for p, e in static_exposures.items() if e < b2_avg_exp]
    above = [p for p, e in static_exposures.items() if e > b2_avg_exp]
    closest_below = max(below) if below else None
    closest_above = min(above) if above else None

    print(f"\n  B2 平均实际暴露: {b2_avg_exp:.1%}")
    print(f"  R={b2_m['total_return']:.2%} DD={b2_m['max_drawdown']:.2%} Cal={b2_m['calmar']:.2f}")

    r4_checks = []
    for label, pos in [("下界", closest_below), ("上界", closest_above)]:
        if pos is None: continue
        sm = results[f"STATIC{pos}"]["metrics"]
        se = static_exposures[pos]
        cal_delta = b2_m["calmar"] - sm["calmar"]
        dd_ok = abs(b2_m["max_drawdown"]) <= abs(sm["max_drawdown"]) * 1.02
        cv_ok = b2_m["cvar95"] <= sm["cvar95"] * 1.05
        ul_ok = b2_m["ulcer"] <= sm["ulcer"] * 1.05
        passed = cal_delta >= 0.10 and dd_ok and cv_ok and ul_ok
        print(f"  {label} STATIC{pos} (exp={se:.1%}): CalΔ={cal_delta:+.2f} DD={'✅' if dd_ok else '❌'} CVaR={'✅' if cv_ok else '❌'} Ulcer={'✅' if ul_ok else '❌'} {'✅' if passed else '❌'}")
        r4_checks.append(passed)

    r4_pass = all(r4_checks) if r4_checks else False
    print(f"  R4: {'✅ 通过' if r4_pass else '❌ 未通过'}")

    # Print full frontier
    print(f"\n  静态前沿 (38%-46%):")
    for pos_pct in range(38, 47):
        m = results[f"STATIC{pos_pct}"]["metrics"]
        e = static_exposures[pos_pct]
        print(f"    {pos_pct}% (exp={e:.1%}): R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

    # ══════════════════════════════════════════════════════════
    # R5: Counterfactual decomposition
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R5: 4路径持仓级归因 ===")
    cf = build_counterfactuals(results["S60"]["nav_df"], results["B2"]["nav_df"])
    if cf:
        print(f"  C0 (S60 stocks × S60 exp): R={cf['c0_return']:.2%} Cal={cf['c0_calmar']:.2f}")
        print(f"  C1 (S60 stocks × B2 exp):  R={cf['c1_return']:.2%} Cal={cf['c1_calmar']:.2f}")
        print(f"  C2 (B2 stocks × S60 exp):  R={cf['c2_return']:.2%} Cal={cf['c2_calmar']:.2f}")
        print(f"  C3 (B2 stocks × B2 exp):   R={cf['c3_return']:.2%} Cal={cf['c3_calmar']:.2f}")
        print(f"\n  归因拆解:")
        print(f"    总效果 (C3-C0):     {cf['total_effect']:+.2%}")
        print(f"    暴露效果 (C1-C0):   {cf['exposure_effect']:+.2%} ({cf['exposure_pct']:.0f}%)")
        print(f"    持仓路径 (C2-C0):   {cf['holdings_effect']:+.2%} ({cf['holdings_pct']:.0f}%)")
        print(f"    交互项:             {cf['interaction']:+.2%}")

        # Key interpretation
        if abs(cf['holdings_effect']) > abs(cf['exposure_effect']):
            driver = "持仓路径"
        else:
            driver = "暴露管理"
        print(f"\n  主导因素: {driver}")

        # Save C0-C3 NAVs
        for i, (label, nav) in enumerate([("C0",cf["c0_nav"]),("C1",cf["c1_nav"]),
                                           ("C2",cf["c2_nav"]),("C3",cf["c3_nav"])]):
            pd.DataFrame({"nav": nav}).to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)
        with open(out_dir / "r5_counterfactuals.json", "w") as f:
            json.dump({k: v for k, v in cf.items() if not k.endswith("_nav")}, f, indent=2)

    # ══════════════════════════════════════════════════════════
    # R6: State diagnostics
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R6: 风险状态诊断 ===")
    diag = diagnose_risk_state(anchor, results["S60"]["nav_df"], results["B2"]["nav_df"])
    print(f"  风险占比: {diag['risk_pct']:.0f}% ({diag['risk_days']}/{diag['total_days']}天)")
    print(f"  风险周期数: {diag['n_episodes']}")
    print(f"  周期持续: {diag['episode_durations']}")
    print(f"  风险期S60日均收益: {diag['risk_s60_daily_mean']:.1f}bps")
    print(f"  正常期S60日均收益: {diag['normal_s60_daily_mean']:.1f}bps")
    print(f"  分类: {diag['classification']}")
    print(f"  ⚠️ 风险状态覆盖{diag['risk_pct']:.0f}%样本 → 应定义为{diag['classification']}, 不是短期尾部开关")

    pd.DataFrame(diag["episodes"]).to_csv(out_dir / "r6_risk_episodes.csv", index=False)

    # ══════════════════════════════════════════════════════════
    # Report
    # ══════════════════════════════════════════════════════════
    for label, r in results.items():
        r["nav_df"].to_csv(out_dir / f"nav_{label.lower()}.csv", index=False)

    summary = []
    for label, r in results.items():
        m = r["metrics"]; e = float(r["nav_df"]["actual_exposure"].mean())
        summary.append({"curve": label, "avg_actual_exposure": round(e,4), **m})
    pd.DataFrame(summary).to_csv(out_dir / "b2_r4_summary.csv", index=False)

    report = [
        "# B2_SCALE40 R4-R6 验证报告",
        f"## R4: 真实等实际暴露对照",
        f"- B2 平均暴露: {b2_avg_exp:.1%}",
        f"- R4通过: {'✅' if r4_pass else '❌'}",
        "",
        "## R5: 持仓级归因",
    ]
    if cf:
        report += [
            f"- 总效果: {cf['total_effect']:+.2%}",
            f"- 暴露效果: {cf['exposure_effect']:+.2%} ({cf['exposure_pct']:.0f}%)",
            f"- 持仓路径: {cf['holdings_effect']:+.2%} ({cf['holdings_pct']:.0f}%)",
            f"- 主导: {driver}",
        ]
    report += [
        "",
        "## R6: 状态诊断",
        f"- 风险占比: {diag['risk_pct']:.0f}%",
        f"- 分类: {diag['classification']}",
        f"- 周期数: {diag['n_episodes']}",
    ]
    (out_dir / "b2_r4_report.md").write_text("\n".join(report))

    print(f"\n{'='*60}")
    print(f"R4: {'✅' if r4_pass else '❌'} | R5主导: {driver} | R6: {diag['classification']}")
    print(f"报告: {out_dir}/b2_r4_report.md")
    print("Done.")


if __name__ == "__main__":
    main()
