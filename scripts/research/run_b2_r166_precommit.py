#!/usr/bin/env python3
"""B2_SCALE40 R16.6: 预承诺静态基线 + Walk-Forward"""
import argparse, json, sys
from datetime import datetime
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))
from scoreRank.core.db_config import build_sqlalchemy_url
from scripts.research_full_pool_liquidity_strategies import (add_liquidity_derived_features, build_market_environment, build_strategy_specs, load_prices, load_scores)
from scripts.research.run_market_exposure_walkforward import (load_index_trends_pit, _build_calendar, _build_signal_to_exec_map)
from scripts.research.run_fsc1_validation import build_anchor_risk_state
from scripts.research.run_b2_r161_shapley import run_counterfactual

def _m(df):
    if df is None or df.empty or "nav" not in df.columns: return {}
    nav=df["nav"].values.astype(float); n=len(nav)
    tr=float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak=np.maximum.accumulate(nav); dd=float(np.min((nav-peak)/peak))
    ar=float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    cv=float(-np.mean(np.sort(np.diff(nav)/nav[:-1])[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul=float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca=float(ar/abs(dd)) if abs(dd)>0 else 0.0
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),"cvar95":round(cv,6),"ulcer":round(ul,6),"n_days":n}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--start-date",default="2023-01-03"); p.add_argument("--end-date",default="2026-07-01"); p.add_argument("--output-dir",default=None); p.add_argument("--initial-cash",type=float,default=500000.0)
    args=p.parse_args()
    print("="*60); print("B2_SCALE40 R16.6: 预承诺静态基线 + Walk-Forward"); print("="*60)
    db_url=build_sqlalchemy_url(); engine=create_engine(db_url)
    print("Loading...")
    cal=_build_calendar(engine,args.start_date,args.end_date); cal=sorted(set(cal))
    s2e,e2s=_build_signal_to_exec_map(cal)
    it=load_index_trends_pit(engine,["000300.SH","399006.SZ"],cal)
    for d in cal:
        if d not in it: it[d]={"000300.SH":0.0,"399006.SZ":0.0}
    prices=load_prices(engine,args.start_date,args.end_date,30); prices["_ds"]=pd.to_datetime(prices["trade_date"]); ps=prices.sort_values("_ds").reset_index(drop=True); pdi=ps.groupby("trade_date",sort=True).indices
    scores=load_scores(engine,start_date=args.start_date,end_date=args.end_date); scores=add_liquidity_derived_features(scores,ps); scores["_ds"]=pd.to_datetime(scores["trade_date"]); ss=scores.sort_values("_ds").reset_index(drop=True); sdi=ss.groupby("trade_date",sort=True).indices
    try: me=build_market_environment(ss,ps)
    except: me=pd.DataFrame()
    specs=build_strategy_specs()
    ts=datetime.now().strftime("%Y%m%d_%H%M%S"); out_dir=Path(args.output_dir) if args.output_dir else Path(f"exports/signal_research/b2_r166_{ts}"); out_dir.mkdir(parents=True,exist_ok=True)
    anchor=build_anchor_risk_state(engine=engine,scores=ss,prices=ps,market_env=me,calendar=cal,signal_to_exec=s2e,exec_to_signal=e2s,sdi=sdi,pdi=pdi,it_trends=it,specs=specs,start_date=args.start_date,end_date=args.end_date,initial_cash=args.initial_cash)

    BASE=dict(engine=engine,scores=ss,prices=ps,market_env=me,calendar=cal,signal_to_exec=s2e,exec_to_signal=e2s,sdi=sdi,pdi=pdi,it_trends=it,specs=specs,initial_cash=args.initial_cash)

    windows=[("W1","2023-01-03","2024-12-31","2025-01-02","2025-06-30"),("W2","2023-07-03","2025-06-30","2025-07-01","2025-12-31"),("W3","2024-01-02","2025-12-31","2026-01-02","2026-06-30")]
    wf_results=[]; b2_wins=0

    for w_name,tr_sd,tr_ed,va_sd,va_ed in windows:
        print(f"\n{'='*40}\n  {w_name}: train={tr_sd}->{tr_ed}  validate={va_sd}->{va_ed}\n{'='*40}")

        # Select best static from training (max Calmar)
        candidates=[0.40,0.45,0.50,0.55,0.60]; best_pos,best_cal,best_dd=None,-999,0
        for pos in candidates:
            r=run_counterfactual(f"S{int(pos*100)}",anchor,pos,pos,start_date=tr_sd,end_date=tr_ed,**BASE)
            m=_m(r["nav_df"]); cal=m["calmar"]; dd=m["max_drawdown"]
            if cal>best_cal or (abs(cal-best_cal)<0.001 and abs(dd)<abs(best_dd)):
                best_pos,best_cal,best_dd=pos,cal,dd
        print(f"  训练最佳静态: STATIC_{int(best_pos*100)} (Calmar={best_cal:.2f})")

        # B2 matched exposure from training
        r=run_counterfactual("B2",anchor,0.60,0.40,start_date=tr_sd,end_date=tr_ed,**BASE)
        nav=r["nav_df"]
        if "cash" in nav.columns and "equity" in nav.columns:
            nc=nav.copy(); nc["ae"]=(nc["equity"]-nc["cash"])/nc["equity"].replace(0,np.nan)
            avg_exp=float(nc["ae"].mean())
        else: avg_exp=0.40
        matched=round(avg_exp*20)/20; matched=max(0.30,min(0.65,matched))
        print(f"  B2训练平均暴露 -> STATIC_MATCHED_{int(matched*100)}")

        # Run validation
        res={}
        for label,pn,pr in [("STATIC_SELECTED",best_pos,best_pos),("STATIC_MATCHED",matched,matched),("B2_DYNAMIC",0.60,0.40)]:
            r=run_counterfactual(label,anchor,pn,pr,start_date=va_sd,end_date=va_ed,**BASE)
            m=_m(r["nav_df"]); res[label]={"m":m,"nav":r["nav_df"]}
            print(f"    {label}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

        b2m=res["B2_DYNAMIC"]["m"]; selm=res["STATIC_SELECTED"]["m"]; matm=res["STATIC_MATCHED"]["m"]
        cal_vs_mat=b2m["calmar"]-matm["calmar"]; b2_ok=cal_vs_mat>=0.10
        if b2_ok: b2_wins+=1
        print(f"    B2 vs MATCHED CalDelta={cal_vs_mat:+.2f} {'✅' if b2_ok else '❌'}")
        wf_results.append({"window":w_name,"b2_calmar":b2m["calmar"],"mat_calmar":matm["calmar"],"cal_delta":round(cal_vs_mat,4),"passes":b2_ok})
        for label,r in res.items(): r["nav"].to_csv(out_dir/f"nav_{w_name}_{label.lower()}.csv",index=False)

    wf_df=pd.DataFrame(wf_results); wf_df.to_csv(out_dir/"r166_wf.csv",index=False)
    n_pass=b2_wins; n_tot=len(wf_results); wf_pass=n_pass>=n_tot*0.5
    print(f"\n{'='*60}\nWF: {n_pass}/{n_tot} {'✅' if wf_pass else '❌'}")
    for _,row in wf_df.iterrows(): print(f"  {row['window']}: B2 Cal={row['b2_calmar']:.2f} MATCHED Cal={row['mat_calmar']:.2f} Delta={row['cal_delta']:+.2f} {'✅' if row['passes'] else '❌'}")
    print(f"  结论: {'✅ B2具备动态时点价值' if wf_pass else '❌ B2降级为低仓位风险版本'}")
    (Path(out_dir)/"r166_verdict.md").write_text(f"# R16.6\nWF: {n_pass}/{n_tot} {'✅' if wf_pass else '❌'}")
    print("Done.")

if __name__=="__main__": main()
