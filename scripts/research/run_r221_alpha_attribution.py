#!/usr/bin/env python3
"""R22.1: 核心Alpha真实账本归因 — realized/unrealized/cost decomposition"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import numpy as np, pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT=Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0,str(PROJECT_ROOT))
from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (
    _safe_float, add_liquidity_derived_features,
    build_market_environment, build_strategy_specs, load_prices, load_scores,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_fsc1_validation import build_anchor_risk_state
from scripts.research.run_b2_r158_cutover import run_exact_backtest

OUT_ROOT=PROJECT_ROOT/"exports"/"signal_research"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start-date",default="2023-01-03"); p.add_argument("--end-date",default="2026-07-01")
    p.add_argument("--output-dir",default=None); p.add_argument("--initial-cash",type=float,default=500000.0)
    args=p.parse_args()

    print("="*60); print("R22.1: 核心Alpha真实账本归因"); print("="*60)

    db_url=build_sqlalchemy_url(); engine=create_engine(db_url)
    cal=_build_calendar(engine,args.start_date,args.end_date); cal=sorted(set(cal))
    s2e,e2s=_build_signal_to_exec_map(cal)
    it=load_index_trends_pit(engine,["000300.SH","399006.SZ"],cal)
    for d in cal:
        if d not in it: it[d]={"000300.SH":0.0,"399006.SZ":0.0}
    prices=load_prices(engine,args.start_date,args.end_date,30)
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
    out_dir=Path(args.output_dir) if args.output_dir else OUT_ROOT/f"r221_{ts}"
    out_dir.mkdir(parents=True,exist_ok=True)

    anchor=build_anchor_risk_state(engine=engine,scores=ss,prices=ps,market_env=me,
                                    calendar=cal,signal_to_exec=s2e,exec_to_signal=e2s,
                                    sdi=sdi,pdi=pdi,it_trends=it,specs=specs,
                                    start_date=args.start_date,end_date=args.end_date,
                                    initial_cash=args.initial_cash)

    # Run S60 with exact tracking
    print("Running S60 exact backtest...")
    s60=run_exact_backtest("S60",anchor,0.60,0.60,engine=engine,scores=ss,prices=ps,
                            market_env=me,calendar=cal,signal_to_exec=s2e,exec_to_signal=e2s,
                            sdi=sdi,pdi=pdi,it_trends=it,specs=specs,
                            start_date=args.start_date,end_date=args.end_date,
                            initial_cash=args.initial_cash)

    ledger=s60["ledger_df"]; hdf=s60["holdings_df"]; snap=s60["snapshot_df"]

    # ══════════════════════════════════════════════════════════
    # Per-stock PnL attribution
    # ══════════════════════════════════════════════════════════
    print(f"\nLedger: {len(ledger)} orders, Holdings: {len(hdf)} rows")

    # Track per-symbol positions and PnL
    positions={}  # symbol -> {shares, total_cost, entry_dates}
    realized_pnl=defaultdict(float)
    total_costs=defaultdict(float)

    for _,o in ledger.iterrows():
        sym=o["symbol"]; side=o["side"]
        sf=int(float(o["shares_filled"])); px=float(o["fill_price"])
        gn=float(o["gross_notional"]); tf=float(o["total_fee"])

        if side=="BUY":
            if sym not in positions: positions[sym]={"shares":0,"cost_basis":0.0,"entries":0}
            positions[sym]["shares"]+=sf
            positions[sym]["cost_basis"]+=gn
            positions[sym]["entries"]+=1
            total_costs[sym]+=tf
        else:
            if sym in positions and positions[sym]["shares"]>0:
                avg_cost=positions[sym]["cost_basis"]/max(positions[sym]["shares"],1)
                sold_shares=min(sf,positions[sym]["shares"])
                realized=sf*px-sold_shares*avg_cost
                realized_pnl[sym]+=realized
                positions[sym]["shares"]-=sf
                positions[sym]["cost_basis"]-=sold_shares*avg_cost
                if positions[sym]["shares"]<=0:
                    del positions[sym]
            total_costs[sym]+=tf

    # Unrealized PnL: mark remaining positions to last close price
    unrealized_pnl=defaultdict(float)
    last_date=sorted(hdf["trade_date"].unique())[-1] if not hdf.empty else None
    if last_date:
        last_holdings=hdf[hdf["trade_date"]==last_date]
        for _,row in last_holdings.iterrows():
            sym=row["symbol"]; sh=row["shares"]; px=row["close_price"]
            if sym in positions:
                avg_cost=positions[sym]["cost_basis"]/max(positions[sym]["shares"],1)
                unrealized_pnl[sym]=(px-avg_cost)*sh

    # Aggregate
    total_realized=sum(realized_pnl.values())
    total_unrealized=sum(unrealized_pnl.values())
    total_cost=sum(total_costs.values())
    total_pnl=total_realized+total_unrealized-total_cost

    # NAV-based verification
    initial_nav=float(snap.iloc[0]["nav"]) if not snap.empty else 1.0
    final_nav=float(snap.iloc[-1]["nav"]) if not snap.empty else 1.0
    nav_pnl=(final_nav/initial_nav-1.0)*args.initial_cash
    residual=abs(total_pnl-nav_pnl)
    residual_pct=residual/max(args.initial_cash,1)*100

    print(f"\n  PnL归因闭合:")
    print(f"    已实现收益: {total_realized:,.0f}")
    print(f"    未实现收益: {total_unrealized:,.0f}")
    print(f"    交易成本:   {total_cost:,.0f}")
    print(f"    净PnL:      {total_pnl:,.0f}")
    print(f"    NAV变化:    {nav_pnl:,.0f}")
    print(f"    残差:       {residual:,.0f} ({residual_pct:.4f}%)")
    print(f"    闭合:       {'✅' if residual_pct<0.01 else '❌'}")

    # Concentration
    all_contrib=defaultdict(float)
    for sym in set(list(realized_pnl.keys())+list(unrealized_pnl.keys())):
        all_contrib[sym]=realized_pnl.get(sym,0)+unrealized_pnl.get(sym,0)-total_costs.get(sym,0)
    sorted_contrib=sorted(all_contrib.items(),key=lambda x:abs(x[1]),reverse=True)
    total_abs=sum(abs(v) for _,v in sorted_contrib)

    top1=abs(sorted_contrib[0][1])/max(total_abs,0.001)*100 if sorted_contrib else 0
    top5=sum(abs(v) for _,v in sorted_contrib[:5])/max(total_abs,0.001)*100 if sorted_contrib else 0
    net_total=sum(v for _,v in sorted_contrib)

    # Net dependency: remove top stock
    if sorted_contrib:
        net_without_top=net_total-sorted_contrib[0][1]
        net_still_positive=net_without_top>0
    else:
        net_without_top=0; net_still_positive=False

    print(f"\n  集中度 (净贡献):")
    print(f"    总净PnL: {net_total:,.0f}")
    print(f"    单一股票: {top1:.0f}% {'✅ <20%' if top1<20 else '⚠️'}")
    print(f"    前五股票: {top5:.0f}% {'✅ <50%' if top5<50 else '⚠️'}")
    print(f"    剔除首位后净PnL: {net_without_top:,.0f} {'✅' if net_still_positive else '❌ 转负'}")
    print(f"  前5: {[(s,round(v,0)) for s,v in sorted_contrib[:5]]}")

    # Save
    contrib_df=pd.DataFrame([{"symbol":s,"realized":realized_pnl.get(s,0),
                               "unrealized":unrealized_pnl.get(s,0),
                               "costs":total_costs.get(s,0),
                               "net_contribution":all_contrib[s]}
                              for s in all_contrib])
    contrib_df.to_csv(out_dir/"r221_security_pnl.csv",index=False)

    report=f"""# R22.1 核心Alpha真实账本归因
## 闭合
- 已实现: {total_realized:,.0f}
- 未实现: {total_unrealized:,.0f}
- 成本: {total_cost:,.0f}
- 净PnL: {total_pnl:,.0f}
- 残差: {residual_pct:.4f}% {'✅' if residual_pct<0.01 else '❌'}

## 集中度
- 单一股票: {top1:.0f}% {'✅' if top1<20 else '⚠️'}
- 前五: {top5:.0f}% {'✅' if top5<50 else '⚠️'}
- 剔除首位净PnL: {net_without_top:,.0f} {'✅ 仍为正' if net_still_positive else '❌ 转负'}
"""
    (out_dir/"r221_report.md").write_text(report)
    print(f"\n报告: {out_dir}/r221_report.md")
    print("Done.")

if __name__=="__main__": main()
