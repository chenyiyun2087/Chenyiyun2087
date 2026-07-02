#!/usr/bin/env python3
"""R21+R22: 静态风险预算验证 + 核心Alpha审计"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
import numpy as np, pandas as pd
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))
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

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"

def _m(df):
    if df is None or df.empty or "nav" not in df.columns: return {}
    nav=df["nav"].values.astype(float); n=len(nav)
    tr=float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak=np.maximum.accumulate(nav); dd=float(np.min((nav-peak)/peak))
    ar=float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    cv=float(-np.mean(np.sort(np.diff(nav)/nav[:-1])[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul=float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca=float(ar/abs(dd)) if abs(dd)>0 else 0.0
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),
            "cvar95":round(cv,6),"ulcer":round(ul,6),"n_days":n}


def run_static_backtest(label, anchor, position, **kw):
    """Run fixed-position backtest with holdings tracking."""
    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in kw["specs"] if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    price_columns = ["raw_open","raw_close","raw_pre_close","adj_open","adj_close",
                     "adj_high","adj_low","adj_factor","is_st","is_suspended",
                     "amount","volume","security_status_available","execution_tradable",
                     "universe_is_tradable","is_listed","circ_mv"]

    engine=kw["engine"]; scores=kw["scores"]; prices=kw["prices"]
    sdi=kw["sdi"]; pdi=kw["pdi"]; calendar=kw["calendar"]
    signal_to_exec=kw["signal_to_exec"]; exec_to_signal=kw["exec_to_signal"]
    start_date=kw.get("start_date"); end_date=kw.get("end_date")
    initial_cash=kw.get("initial_cash",500000.0)

    cache_indices = scores.groupby("trade_date",sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices, specs_by_name={spec.name:spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    nav_rows, holdings_rows = [], []

    _start=pd.Timestamp(start_date).date() if isinstance(start_date,str) else start_date
    _end=pd.Timestamp(end_date).date() if isinstance(end_date,str) else end_date
    sim_cal=[d for d in calendar if _start<=d<=_end]
    first_exec=min(exec_to_signal) if exec_to_signal else None
    if first_exec: sim_cal=[d for d in sim_cal if d>=first_exec]

    for trade_date in sim_cal:
        signal_date=exec_to_signal.get(trade_date)
        rpl=_price_lookup_for_day(prices,pdi,trade_date,price_columns)
        rpl_close=_price_lookup_for_day(prices,pdi,trade_date,["raw_close"])
        if signal_date is None:
            nav_rows.append({"trade_date":trade_date,"nav":1.0,"position_count":0})
            continue
        day_scores=_score_day_frame(scores,sdi,signal_date)
        targets=targets_cache.get((signal_date,spec.name),pd.DataFrame())

        if not targets.empty or account.positions:
            _rebalance(account=account,signal_date=signal_date,execution_date=trade_date,
                       day_scores=day_scores,spec=spec,top_n=5,hold_days=10,
                       lot_size=100,min_trade_value=500.0,
                       trade_cost_rate=0.00075,slippage_rate=0.0,
                       max_total_positions=5,position_ratio=position,
                       calendar=calendar,open_prices=rpl,
                       targets=targets,precommit_prices=None,
                       strict_precommit=False,ledger=None)

        eq=_equity(account,rpl,"raw_close")
        nav=eq/initial_cash if initial_cash>0 else 1.0
        for sym,pos in account.positions.items():
            px=_safe_float(rpl_close.get(sym,{}).get("raw_close"),0)
            holdings_rows.append({"trade_date":trade_date,"symbol":sym,"shares":pos.shares,
                                   "close_price":round(float(px),2),
                                   "market_value":round(pos.shares*px,2),"label":label})
        nav_rows.append({"trade_date":trade_date,"nav":round(nav,8),
                         "equity":round(eq,2),"cash":round(account.cash,2),
                         "position_ratio":position,"position_count":len(account.positions)})

    nav_df=pd.DataFrame(nav_rows); hdf=pd.DataFrame(holdings_rows) if holdings_rows else pd.DataFrame()
    return {"label":label,"nav_df":nav_df,"holdings_df":hdf,"metrics":_m(nav_df)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start-date",default="2023-01-03"); p.add_argument("--end-date",default="2026-07-01")
    p.add_argument("--output-dir",default=None); p.add_argument("--initial-cash",type=float,default=500000.0)
    args=p.parse_args()

    print("="*60); print("R21+R22: 静态风险预算 + 核心Alpha审计"); print("="*60)

    db_url=build_sqlalchemy_url(); engine=create_engine(db_url)
    print("Loading...")
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
    out_dir=Path(args.output_dir) if args.output_dir else OUT_ROOT/f"r21_r22_{ts}"
    out_dir.mkdir(parents=True,exist_ok=True)

    anchor=build_anchor_risk_state(engine=engine,scores=ss,prices=ps,market_env=me,
                                    calendar=cal,signal_to_exec=s2e,exec_to_signal=e2s,
                                    sdi=sdi,pdi=pdi,it_trends=it,specs=specs,
                                    start_date=args.start_date,end_date=args.end_date,
                                    initial_cash=args.initial_cash)

    BASE=dict(engine=engine,scores=ss,prices=ps,market_env=me,calendar=cal,
              signal_to_exec=s2e,exec_to_signal=e2s,sdi=sdi,pdi=pdi,it_trends=it,
              specs=specs,initial_cash=args.initial_cash)

    # ══════════════════════════════════════════════════════════
    # R21: Static low-exposure WF validation
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R21: 静态低暴露风险预算 WF ===")
    candidates=[0.30,0.35,0.40]
    windows=[("W1","2023-01-03","2024-12-31","2025-01-02","2025-06-30"),
             ("W2","2023-07-03","2025-06-30","2025-07-01","2025-12-31"),
             ("W3","2024-01-02","2025-12-31","2026-01-02","2026-06-30")]

    wf_results=[]; s60_wins=0; static_wins=0
    for w_name,tr_sd,tr_ed,va_sd,va_ed in windows:
        print(f"\n  {w_name}: train={tr_sd}->{tr_ed} validate={va_sd}->{va_ed}")

        # Select best static from {30,35,40}
        best_pos,best_cal,best_dd=None,-999,0
        for pos in candidates:
            r=run_static_backtest(f"TRAIN_{int(pos*100)}",anchor,pos,
                                   start_date=tr_sd,end_date=tr_ed,**BASE)
            m=r["metrics"]; cal=m["calmar"]; dd=m["max_drawdown"]
            if cal>best_cal or (abs(cal-best_cal)<0.001 and abs(dd)<abs(best_dd)):
                best_pos,best_cal,best_dd=pos,cal,dd
        print(f"    训练最佳: STATIC_{int(best_pos*100)} (Cal={best_cal:.2f})")

        # Validate
        res={}
        for label,pos in [("S60",0.60),("STATIC_40",0.40),("STATIC_SELECTED",best_pos)]:
            r=run_static_backtest(label,anchor,pos,start_date=va_sd,end_date=va_ed,**BASE)
            m=r["metrics"]; res[label]=m
            print(f"    {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

        sel_m=res["STATIC_SELECTED"]; s60_m=res["S60"]
        static_better=sel_m["calmar"]>=s60_m["calmar"]
        if static_better: static_wins+=1

        dd_ok=abs(sel_m["max_drawdown"])<=abs(s60_m["max_drawdown"])
        cv_ok=sel_m["cvar95"]<=s60_m["cvar95"]
        ul_ok=sel_m["ulcer"]<=s60_m["ulcer"]

        passes=static_better and dd_ok
        wf_results.append({"window":w_name,"selected":best_pos,
                           "sel_calmar":sel_m["calmar"],"s60_calmar":s60_m["calmar"],
                           "sel_return":sel_m["total_return"],"s60_return":s60_m["total_return"],
                           "sel_dd":sel_m["max_drawdown"],"s60_dd":s60_m["max_drawdown"],
                           "passes":passes})
        print(f"    STATIC vs S60: Cal={'✅' if static_better else '❌'} DD={'✅' if dd_ok else '❌'} "
              f"CVaR={'✅' if cv_ok else '❌'} Ulcer={'✅' if ul_ok else '❌'} {'✅ PASS' if passes else '❌'}")

    n_pass=sum(1 for w in wf_results if w["passes"])
    r21_pass=n_pass>=2
    print(f"\n  R21: {n_pass}/3 windows pass {'✅' if r21_pass else '❌'}")

    # ══════════════════════════════════════════════════════════
    # R22: Core alpha audit (full sample)
    # ══════════════════════════════════════════════════════════
    print(f"\n=== R22: 核心Alpha审计 ===")
    s60_full=run_static_backtest("S60_FULL",anchor,0.60,
                                  start_date=args.start_date,end_date=args.end_date,**BASE)
    hdf=s60_full["holdings_df"]

    if not hdf.empty:
        # Per-symbol contribution
        sym_pnl=defaultdict(float)
        sym_dates=defaultdict(int)
        dates=sorted(hdf["trade_date"].unique())
        for i in range(1,len(dates)):
            d_prev,d_curr=dates[i-1],dates[i]
            prev=hdf[hdf["trade_date"]==d_prev]; curr=hdf[hdf["trade_date"]==d_curr]
            for _,row in prev.iterrows():
                sym=row["symbol"]; sh=row["shares"]; px_prev=row["close_price"]
                curr_match=curr[curr["symbol"]==sym]
                px_curr=float(curr_match.iloc[0]["close_price"]) if len(curr_match)>0 else px_prev
                if px_prev>0:
                    pnl=(px_curr/px_prev-1.0)*sh*px_prev
                    sym_pnl[sym]+=pnl; sym_dates[sym]+=1

        sym_contrib=sorted(sym_pnl.items(),key=lambda x:abs(x[1]),reverse=True)
        total_abs=sum(abs(v) for _,v in sym_contrib)
        top1_pct=abs(sym_contrib[0][1])/max(total_abs,0.001)*100 if sym_contrib else 0
        top5_pct=sum(abs(v) for _,v in sym_contrib[:5])/max(total_abs,0.001)*100 if sym_contrib else 0

        print(f"  交易符号数: {len(sym_pnl)}")
        print(f"  单一股票集中度: {top1_pct:.0f}% {'✅ <20%' if top1_pct<20 else '⚠️'} ")
        print(f"  前五股票集中度: {top5_pct:.0f}% {'✅ <50%' if top5_pct<50 else '⚠️'} ")
        print(f"  前5贡献: {[(s,round(v,0)) for s,v in sym_contrib[:5]]}")

        # Total metrics
        s60m=s60_full["metrics"]
        print(f"\n  S60全样本: R={s60m['total_return']:.2%} DD={s60m['max_drawdown']:.2%} "
              f"Cal={s60m['calmar']:.2f} CVaR={s60m['cvar95']:.4f}")
        print(f"  核心Alpha审计: {'✅ 可接受' if top1_pct<20 and top5_pct<50 else '⚠️ 需关注集中度'}")

    # Save
    pd.DataFrame(wf_results).to_csv(out_dir/"r21_wf.csv",index=False)
    m_full=s60_full["metrics"]
    report=f"""# R21+R22 报告
## R21: 静态低暴露 WF — {n_pass}/3 {'✅' if r21_pass else '❌'}
| 窗口 | 选择仓位 | STATIC Cal | S60 Cal | 通过 |
|------|---------|-----------|---------|------|
"""
    for w in wf_results:
        report+=f"| {w['window']} | {int(w['selected']*100)}% | {w['sel_calmar']:.2f} | {w['s60_calmar']:.2f} | {'✅' if w['passes'] else '❌'} |\n"
    report+=f"""
## R22: 核心Alpha
- S60全样本: R={m_full['total_return']:.2%} DD={m_full['max_drawdown']:.2%}
- 单一股票: {top1_pct:.0f}% {'✅' if top1_pct<20 else '⚠️'}
- 前五: {top5_pct:.0f}% {'✅' if top5_pct<50 else '⚠️'}
"""
    (out_dir/"r21_r22_report.md").write_text(report)

    print(f"\n{'='*60}")
    print(f"R21: {'✅' if r21_pass else '❌'} | R22: {'✅' if top1_pct<20 and top5_pct<50 else '⚠️'}")
    print(f"报告: {out_dir}/r21_r22_report.md")
    print("Done.")

if __name__=="__main__": main()
