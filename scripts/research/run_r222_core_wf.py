#!/usr/bin/env python3
"""R22.2: 核心Alpha Walk-Forward + 安慰剂对照"""
import argparse, sys, numpy as np, pandas as pd
from datetime import datetime; from pathlib import Path; from sqlalchemy import create_engine
PROJECT_ROOT=Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0,str(PROJECT_ROOT))
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

OUT_ROOT=PROJECT_ROOT/"exports"/"signal_research"

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

def run_core_wf_backtest(label, anchor, mode, sdi, **kw):
    """mode: 'core','equal','random','reversed'"""
    strategy_name="baseline_full_liquidity_detail_vol_position"
    spec=None; matched=[s for s in kw["specs"] if s.name==strategy_name]
    if not matched: return {}
    spec=matched[0]

    pc=["raw_open","raw_close","raw_pre_close","adj_open","adj_close","adj_high","adj_low",
        "adj_factor","is_st","is_suspended","amount","volume","security_status_available",
        "execution_tradable","universe_is_tradable","is_listed","circ_mv"]

    engine=kw["engine"]; scores=kw["scores"]; prices=kw["prices"]
    pdi=kw["pdi"]; calendar=kw["calendar"]
    signal_to_exec=kw["signal_to_exec"]; exec_to_signal=kw["exec_to_signal"]
    start_date=kw.get("start_date"); end_date=kw.get("end_date")
    initial_cash=kw.get("initial_cash",500000.0)

    # Build standard targets cache for core/equal
    ci=scores.groupby("trade_date",sort=True).indices
    tc=_build_targets_cache(scores=scores,day_indices=ci,specs_by_name={spec.name:spec},top_n=5)

    # For random/reversed: build custom targets from scores
    rng=np.random.RandomState(42)
    custom_targets={}
    if mode in ("random","reversed"):
        for sd in sorted(scores["trade_date"].unique()):
            ds=_score_day_frame(scores,sdi,sd)
            if ds.empty: continue
            pool=ds[ds["score"]>=40] if "score" in ds.columns else ds
            if mode=="random":
                picks=pool.sample(min(5,len(pool)),random_state=rng)
            else:  # reversed
                picks=pool.nsmallest(5,"score") if "score" in pool.columns else pool.head(5)
            if not picks.empty and "symbol" in picks.columns:
                tdf=pd.DataFrame({"symbol":picks["symbol"].values,"effective_weight":[1.0/len(picks)]*len(picks),"rank":range(1,len(picks)+1)})
                custom_targets[(sd,spec.name)]=tdf

    account=AccountState(cash=float(initial_cash)); nav_rows=[]

    _start=pd.Timestamp(start_date).date() if isinstance(start_date,str) else start_date
    _end=pd.Timestamp(end_date).date() if isinstance(end_date,str) else end_date
    sc=[d for d in calendar if _start<=d<=_end]
    fe=min(exec_to_signal) if exec_to_signal else None
    if fe: sc=[d for d in sc if d>=fe]

    for trade_date in sc:
        signal_date=exec_to_signal.get(trade_date)
        rpl=_price_lookup_for_day(prices,pdi,trade_date,pc)
        if signal_date is None: nav_rows.append({"trade_date":trade_date,"nav":1.0}); continue
        day_scores=_score_day_frame(scores,sdi,signal_date)

        if mode in ("core","equal"): targets=tc.get((signal_date,spec.name),pd.DataFrame())
        else: targets=custom_targets.get((signal_date,spec.name),pd.DataFrame())

        if mode=="equal" and not targets.empty and "effective_weight" in targets.columns:
            n=len(targets); targets["effective_weight"]=1.0/n if n>0 else 0

        if not targets.empty or account.positions:
            _rebalance(account=account,signal_date=signal_date,execution_date=trade_date,
                       day_scores=day_scores,spec=spec,top_n=5,hold_days=10,
                       lot_size=100,min_trade_value=500.0,
                       trade_cost_rate=0.00075,slippage_rate=0.0,
                       max_total_positions=5,position_ratio=0.60,
                       calendar=calendar,open_prices=rpl,
                       targets=targets,precommit_prices=None,
                       strict_precommit=False,ledger=None)
        eq=_equity(account,rpl,"raw_close"); nav=eq/initial_cash if initial_cash>0 else 1.0
        nav_rows.append({"trade_date":trade_date,"nav":round(nav,8)})

    ndf=pd.DataFrame(nav_rows)
    return {"label":label,"nav_df":ndf,"metrics":_m(ndf)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--start-date",default="2023-01-03"); p.add_argument("--end-date",default="2026-07-01")
    p.add_argument("--output-dir",default=None); p.add_argument("--initial-cash",type=float,default=500000.0)
    args=p.parse_args()

    print("="*60); print("R22.2: 核心Alpha WF + 安慰剂"); print("="*60)
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
    out_dir=Path(args.output_dir) if args.output_dir else OUT_ROOT/f"r222_{ts}"
    out_dir.mkdir(parents=True,exist_ok=True)

    anchor_risk=None  # Not needed for core alpha WF
    BASE=dict(engine=engine,scores=ss,prices=ps,market_env=me,calendar=cal,
              signal_to_exec=s2e,exec_to_signal=e2s,sdi=sdi,pdi=pdi,specs=specs,
              initial_cash=args.initial_cash)

    windows=[("W1","2023-01-03","2024-12-31","2025-01-02","2025-06-30"),
             ("W2","2023-07-03","2025-06-30","2025-07-01","2025-12-31"),
             ("W3","2024-01-02","2025-12-31","2026-01-02","2026-06-30")]

    modes=["core","equal","random","reversed"]
    wf_results=[]; core_wins=0

    for w_name,tr_sd,tr_ed,va_sd,va_ed in windows:
        print(f"\n{'='*40}\n  {w_name}: validate {va_sd}->{va_ed}\n{'='*40}")
        res={}
        for mode in modes:
            r=run_core_wf_backtest(mode,anchor_risk,mode,sdi,start_date=va_sd,end_date=va_ed,**{k:v for k,v in BASE.items() if k not in ('start_date','end_date','sdi')})
            m=r["metrics"]; res[mode]=m
            print(f"    {mode}: R={m['total_return']:.2%} DD={m['max_drawdown']:.2%} Cal={m['calmar']:.2f}")

        core_m=res["core"]; eq_m=res["equal"]; rand_m=res["random"]; rev_m=res["reversed"]
        core_beats_eq=core_m["calmar"]>=eq_m["calmar"]
        core_beats_rand=core_m["calmar"]>rand_m["calmar"]
        core_beats_rev=core_m["calmar"]>rev_m["calmar"]
        core_positive=core_m["total_return"]>0

        passes=core_beats_eq and core_beats_rand and core_beats_rev and core_positive
        if core_positive: core_wins+=1

        wf_results.append({"window":w_name,"core_return":core_m["total_return"],
                           "core_calmar":core_m["calmar"],"eq_calmar":eq_m["calmar"],
                           "rand_calmar":rand_m["calmar"],"rev_calmar":rev_m["calmar"],
                           "core_beats_eq":core_beats_eq,"core_beats_rand":core_beats_rand,
                           "core_beats_rev":core_beats_rev,"core_positive":core_positive,
                           "passes":passes})
        flags=" ".join(["E" if core_beats_eq else "e","R" if core_beats_rand else "r",
                        "V" if core_beats_rev else "v","+" if core_positive else "-"])
        print(f"    vs EQ={'✅' if core_beats_eq else '❌'} vs RAND={'✅' if core_beats_rand else '❌'} vs REV={'✅' if core_beats_rev else '❌'} pos={'✅' if core_positive else '❌'} [{flags}] {'✅ PASS' if passes else '❌'}")

    # Aggregate
    wf_df=pd.DataFrame(wf_results); wf_df.to_csv(out_dir/"r222_wf.csv",index=False)
    n_wins=core_wins; n_pass=sum(1 for w in wf_results if w["passes"])
    n_tot=len(wf_results)
    r222_ok=n_wins>=2 and n_pass>=2

    print(f"\n{'='*60}")
    print(f"Core alpha positive: {n_wins}/{n_tot} (need >=2)")
    print(f"Full passes: {n_pass}/{n_tot} (need >=2)")
    print(f"R22.2: {'✅ CORE ALPHA VIABLE' if r222_ok else '❌ CORE ALPHA FAILS'}")

    report=f"""# R22.2 核心Alpha WF
| Window | Core R | Core Cal | EQ Cal | RAND Cal | REV Cal | vsEQ | vsRAND | vsREV | Pos | Pass |
|--------|--------|----------|--------|----------|---------|------|--------|-------|-----|------|
"""
    for w in wf_results:
        report+=f"| {w['window']} | {w['core_return']:.2%} | {w['core_calmar']:.2f} | {w['eq_calmar']:.2f} | {w['rand_calmar']:.2f} | {w['rev_calmar']:.2f} | {'✅' if w['core_beats_eq'] else '❌'} | {'✅' if w['core_beats_rand'] else '❌'} | {'✅' if w['core_beats_rev'] else '❌'} | {'✅' if w['core_positive'] else '❌'} | {'✅' if w['passes'] else '❌'} |\n"
    report+=f"\n## 结论: {'✅ 核心Alpha可行' if r222_ok else '❌ 核心Alpha失败'}\n"
    (out_dir/"r222_report.md").write_text(report)
    print(f"报告: {out_dir}/r222_report.md")
    print("Done.")

if __name__=="__main__": main()
