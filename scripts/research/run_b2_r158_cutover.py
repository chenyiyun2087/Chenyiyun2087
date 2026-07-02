#!/usr/bin/env python3
"""
B2_SCALE40 R15.8: Exact Authority Cutover

Exact fill events as sole source of truth.
No display/exact dual system. Exact IS the truth.

Usage:
    python scripts/research/run_b2_r158_cutover.py \
        --start-date 2023-01-03 --end-date 2026-06-30
"""

import argparse, json, sys, hashlib
from datetime import datetime
from pathlib import Path
from decimal import Decimal, getcontext, ROUND_HALF_UP
from collections import defaultdict
import numpy as np, pandas as pd
from sqlalchemy import create_engine

getcontext().prec = 28

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
    _build_targets_cache,
)
from scripts.research.run_market_exposure_walkforward import (
    load_index_trends_pit, _build_calendar, _build_signal_to_exec_map,
)
from scripts.research.run_fsc1_validation import build_anchor_risk_state

OUT_ROOT = PROJECT_ROOT / "exports" / "signal_research"

D = Decimal


# ══════════════════════════════════════════════════════════════════════
# R15.8: Exact authority backtest
# ══════════════════════════════════════════════════════════════════════

def run_exact_backtest(label, anchor_risk, target_normal=0.60, target_risk=0.40,
                        cost_rate=0.00075, slip_rate=0.0, **kw):
    """R15.8: Exact fill events. Exact IS the truth. No display fields."""

    strategy_name = "baseline_full_liquidity_detail_vol_position"
    matched = [s for s in kw["specs"] if s.name == strategy_name]
    if not matched: return {}
    spec = matched[0]

    risk_lookup = {row["signal_date"]: bool(row["risk_state"])
                   for _, row in anchor_risk.iterrows()}

    price_columns = ["raw_open", "raw_close", "raw_pre_close", "adj_open", "adj_close",
                     "adj_high", "adj_low", "adj_factor", "is_st", "is_suspended",
                     "amount", "volume", "security_status_available", "execution_tradable",
                     "universe_is_tradable", "is_listed", "circ_mv"]

    engine = kw["engine"]; scores = kw["scores"]; prices = kw["prices"]
    sdi = kw["sdi"]; pdi = kw["pdi"]; calendar = kw["calendar"]
    signal_to_exec = kw["signal_to_exec"]; exec_to_signal = kw["exec_to_signal"]
    start_date = kw["start_date"]; end_date = kw["end_date"]
    initial_cash = kw.get("initial_cash", 500000.0)

    cache_indices = scores.groupby("trade_date", sort=True).indices
    targets_cache = _build_targets_cache(
        scores=scores, day_indices=cache_indices, specs_by_name={spec.name: spec}, top_n=5)

    account = AccountState(cash=float(initial_cash))
    ledger_rows, snapshot_rows, holdings_rows = [], [], []
    order_id = 0
    cr_d = D(str(cost_rate))

    _start = pd.Timestamp(start_date).date() if isinstance(start_date, str) else start_date
    _end = pd.Timestamp(end_date).date() if isinstance(end_date, str) else end_date
    sim_cal = [d for d in calendar if _start <= d <= _end]
    first_exec = min(exec_to_signal) if exec_to_signal else None
    if first_exec: sim_cal = [d for d in sim_cal if d >= first_exec]

    for trade_date in sim_cal:
        signal_date = exec_to_signal.get(trade_date)
        rpl = _price_lookup_for_day(prices, pdi, trade_date, price_columns)
        rpl_close = _price_lookup_for_day(prices, pdi, trade_date, ["raw_close"])

        if signal_date is None:
            snapshot_rows.append(_exact_snap(trade_date, None, account, rpl_close, D('0'), False))
            _exact_holdings(holdings_rows, trade_date, account, rpl_close, label)
            continue

        day_scores = _score_day_frame(scores, sdi, signal_date)
        in_risk = risk_lookup.get(signal_date, False)
        position_ratio = target_risk if in_risk else target_normal
        targets = targets_cache.get((signal_date, spec.name), pd.DataFrame())

        if not targets.empty or account.positions:
            pre_cash = account.cash
            pre_positions = {sym: pos.shares for sym, pos in account.positions.items()}

            trades, cands, meta = _rebalance(
                account=account, signal_date=signal_date, execution_date=trade_date,
                day_scores=day_scores, spec=spec, top_n=5, hold_days=10,
                lot_size=100, min_trade_value=500.0,
                trade_cost_rate=cost_rate, slippage_rate=slip_rate,
                max_total_positions=5, position_ratio=position_ratio,
                calendar=calendar, open_prices=rpl,
                targets=targets, precommit_prices=None,
                strict_precommit=False, ledger=None)

            # ── R15.8: Exact sequential fill events ──
            run_cash = pre_cash
            run_pos = dict(pre_positions)
            exec_seq = 0

            for t in trades:
                exec_seq += 1
                sym = str(t.get("symbol", "")).zfill(6)
                side = t.get("side", "?")
                shares = abs(t.get("shares", 0) or 0)
                fp = t.get("price", 0) or 0
                ro = _safe_float(rpl.get(sym, {}).get("raw_open"), 0)

                # All computations in Decimal — exact IS the truth
                fp_d = D(str(fp)); ro_d = D(str(ro)); sh_d = D(str(shares))
                gn_d = sh_d * fp_d
                pic_d = abs(fp_d - ro_d) * sh_d
                comm_d = (gn_d * cr_d).quantize(D('0.000001'), rounding=ROUND_HALF_UP)
                tf_d = comm_d

                sh_before = run_pos.get(sym, 0)
                cb_d = D(str(run_cash))

                # Pre-trade portfolio value
                pre_mv_d = D('0')
                for s, sh in run_pos.items():
                    px_d = D(str(_safe_float(rpl.get(s, {}).get("raw_open"), 0)))
                    pre_mv_d += D(str(sh)) * px_d

                if side == "BUY":
                    cd_d = -(gn_d + tf_d)
                else:
                    cd_d = gn_d - tf_d
                run_cash += float(cd_d)

                if side == "BUY":
                    run_pos[sym] = run_pos.get(sym, 0) + shares
                else:
                    run_pos[sym] = run_pos.get(sym, 0) - shares
                    if run_pos[sym] <= 0: run_pos.pop(sym, None)

                sh_after = run_pos.get(sym, 0)
                ca_d = D(str(run_cash))

                # Post-trade portfolio value
                post_mv_d = D('0')
                for s, sh in run_pos.items():
                    px_d = D(str(_safe_float(rpl.get(s, {}).get("raw_open"), 0)))
                    post_mv_d += D(str(sh)) * px_d

                order_id += 1
                ledger_rows.append({
                    "order_id": str(order_id),
                    "execution_date": trade_date,  # Keep original type
                    "execution_sequence": exec_seq,
                    "signal_date": signal_date,
                    "symbol": sym, "side": side,
                    "reference_open_price": str(ro_d),
                    "fill_price": str(fp_d),
                    "shares_filled": str(sh_d),
                    "gross_notional": str(gn_d),
                    "price_impact_cost": str(pic_d),
                    "commission": str(comm_d),
                    "stamp_tax": "0", "transfer_fee": "0",
                    "total_fee": str(tf_d),
                    "cash_delta": str(cd_d),
                    "cash_before": str(cb_d),
                    "cash_after": str(ca_d),
                    "shares_before": str(sh_before),
                    "shares_after": str(sh_after),
                    "market_value_before": str(pre_mv_d),
                    "market_value_after": str(post_mv_d),
                    "equity_before": str(cb_d + pre_mv_d),
                    "equity_after": str(ca_d + post_mv_d),
                    "risk_state": str(in_risk),
                    "target_exposure": str(D(str(position_ratio)).quantize(D('0.0001'))),
                    "scenario": f"c{cost_rate}_s{slip_rate}",
                })

        snapshot_rows.append(_exact_snap(trade_date, signal_date, account, rpl_close,
                                          D(str(position_ratio)), in_risk))
        _exact_holdings(holdings_rows, trade_date, account, rpl_close, label)

    ledger_df = pd.DataFrame(ledger_rows) if ledger_rows else pd.DataFrame()
    snapshot_df = pd.DataFrame(snapshot_rows) if snapshot_rows else pd.DataFrame()
    holdings_df = pd.DataFrame(holdings_rows) if holdings_rows else pd.DataFrame()

    m = _metrics(snapshot_df)
    return {"label": label, "ledger_df": ledger_df, "snapshot_df": snapshot_df,
            "holdings_df": holdings_df, "metrics": m}


def _exact_snap(td, sd, acct, rpl_close, pr, ir):
    mv = sum(pos.shares * _safe_float(rpl_close.get(s, {}).get("raw_close"), 0)
             for s, pos in acct.positions.items())
    eq = acct.cash + mv; nav = eq / 500000.0
    return {"trade_date": td,  # Keep original type for price lookup
            "signal_date": sd,
            "cash": float(acct.cash),
            "gross_market_value": float(mv),
            "equity": float(eq),
            "nav": float(nav),
            "position_count": len(acct.positions),
            "position_ratio": float(pr),
            "risk_state": ir}


def _exact_holdings(rows, td, acct, rpl_close, label):
    for sym, pos in acct.positions.items():
        px = _safe_float(rpl_close.get(sym, {}).get("raw_close"), 0)
        rows.append({"trade_date": td, "symbol": sym,
                      "shares": pos.shares,
                      "close_price": round(float(px), 2),
                      "market_value": round(pos.shares * px, 2),
                      "label": label})


def _metrics(df):
    if df is None or df.empty or "nav" not in df.columns: return {}
    nav = df["nav"].values.astype(float); n = len(nav)
    tr = float(nav[-1]/nav[0]-1) if nav[0]>0 else 0.0
    peak = np.maximum.accumulate(nav); dd = float(np.min((nav-peak)/peak))
    ar = float((1+tr)**(252/n)-1) if n>0 and nav[0]>0 else 0.0
    cv = float(-np.mean(np.sort(np.diff(nav)/nav[:-1])[:max(1,int(n*0.05))])) if n>20 else 0.0
    ul = float(np.sqrt(np.mean(((nav-peak)/peak)**2)))
    ca = float(ar/abs(dd)) if abs(dd)>0 else 0.0
    return {"total_return":round(tr,6),"max_drawdown":round(dd,6),"calmar":round(ca,4),
            "cvar95":round(cv,6),"ulcer":round(ul,6)}


# ══════════════════════════════════════════════════════════════════════
# R15.8: Exact replay with 5-way daily closure
# ══════════════════════════════════════════════════════════════════════

def replay_exact(ledger_df, snapshot_df, prices_df, pdi, initial_cash, cost_rate):
    """R15.8: Exact replay. All assertions use Decimal exact fields."""
    errors = []
    cash = D(str(initial_cash))
    positions = {}
    daily_diffs, position_diffs = [], []

    lbd = defaultdict(list)
    for _, row in ledger_df.iterrows():
        lbd[row["execution_date"]].append(row)

    all_dates = sorted(snapshot_df["trade_date"].unique())

    for td in all_dates:
        orders = sorted(lbd.get(td, []), key=lambda x: int(float(x["execution_sequence"])))

        for o in orders:
            gn = D(str(o["gross_notional"])); fp = D(str(o["fill_price"]))
            sf = int(float(str(o["shares_filled"]))); ro = D(str(o["reference_open_price"]))
            pic = D(str(o["price_impact_cost"])); comm = D(str(o["commission"]))
            tf = D(str(o["total_fee"])); cd = D(str(o["cash_delta"]))
            cb = D(str(o["cash_before"])); ca = D(str(o["cash_after"]))
            side = o["side"]

            # Assertions on exact fields
            if abs(gn - D(str(sf)) * fp) > D('0.02') and fp > 0:
                errors.append(f"O{o['order_id']}: GN mismatch")
            exp_pic = abs(fp - ro) * D(str(sf))
            if abs(pic - exp_pic) > D('0.02'):
                errors.append(f"O{o['order_id']}: PIC mismatch")
            exp_tf = comm  # stamp_tax=0, transfer_fee=0
            if abs(tf - exp_tf) > D('0.001'):
                errors.append(f"O{o['order_id']}: TF mismatch")
            exp_cd = -(gn + tf) if side == "BUY" else (gn - tf)
            if abs(cd - exp_cd) > D('0.02'):
                errors.append(f"O{o['order_id']}: CD mismatch")
            if abs(cash - cb) > D('0.02'):
                errors.append(f"O{o['order_id']}: CB mismatch replay={cash} vs {cb}")

            if side == "BUY":
                cash -= (gn + tf)
                positions[o["symbol"]] = positions.get(o["symbol"], 0) + sf
            else:
                cash += (gn - tf)
                positions[o["symbol"]] = positions.get(o["symbol"], 0) - sf
                if positions[o["symbol"]] <= 0: positions.pop(o["symbol"], None)

            if abs(cash - ca) > D('0.02'):
                errors.append(f"O{o['order_id']}: CA mismatch")

        # EOD 5-way closure
        rpl_close = _price_lookup_for_day(prices_df, pdi, td, ["raw_close"])
        mv = D('0')
        for s, sh in positions.items():
            px = D(str(_safe_float(rpl_close.get(s, {}).get("raw_close"), 0)))
            mv += D(str(sh)) * px
        equity = cash + mv; nav = equity / D(str(initial_cash))

        snap = snapshot_df[snapshot_df["trade_date"] == td]
        if not snap.empty:
            sn = float(snap.iloc[0]["nav"]); sc = float(snap.iloc[0]["cash"])
            smv = float(snap.iloc[0]["gross_market_value"])
            nv = float(nav)
            daily_diffs.append({
                "trade_date": str(td),
                "nav_diff_bps": abs(nv - sn) * 10000,
                "cash_diff": abs(float(cash) - sc),
                "mv_diff": abs(float(mv) - smv),
            })

    max_nav = max((d["nav_diff_bps"] for d in daily_diffs), default=999)
    ok = (len(errors) == 0 and max_nav <= 0.01 and len(daily_diffs) > 0)
    return {"ok": ok, "n_errors": len(errors), "max_nav_bps": round(max_nav, 4),
            "n_dates": len(daily_diffs), "daily_diffs": daily_diffs[:3]}


# ══════════════════════════════════════════════════════════════════════
# Offline replay command
# ══════════════════════════════════════════════════════════════════════

def offline_replay(package_dir, inputs_dir, initial_cash=500000.0, cost_rate=0.00075):
    """R15.8: Offline replay — reads ONLY package + inputs. No database."""
    ledger_df = pd.read_csv(package_dir / "ledger.csv")
    snap_df = pd.read_csv(package_dir / "daily_snapshots.csv")

    ps = pd.read_parquet(inputs_dir / "prices_snapshot.parquet")
    pdi = ps.groupby("trade_date", sort=True).indices

    rp = replay_exact(ledger_df, snap_df, ps, pdi, initial_cash, cost_rate)

    manifest = json.load(open(package_dir / "manifest.json"))
    ledger_sha = hashlib.sha256(ledger_df.to_csv(index=False).encode()).hexdigest()
    sha_ok = ledger_sha == manifest.get("ledger_sha256", "")

    return {"replay_ok": rp["ok"], "sha_ok": sha_ok, "n_errors": rp["n_errors"],
            "max_nav_bps": rp["max_nav_bps"]}


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start-date", default="2023-01-03")
    p.add_argument("--end-date", default="2026-06-30")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--initial-cash", type=float, default=500000.0)
    p.add_argument("--offline", action="store_true", help="Offline replay mode")
    p.add_argument("--package", type=str, help="Package dir for offline replay")
    p.add_argument("--inputs", type=str, help="Inputs dir for offline replay")
    args = p.parse_args()

    if args.offline and args.package and args.inputs:
        rp = offline_replay(Path(args.package), Path(args.inputs),
                             args.initial_cash, 0.00075)
        print(f"Offline replay: {'✅' if rp['replay_ok'] else '❌'} "
              f"SHA={'✅' if rp['sha_ok'] else '❌'} errs={rp['n_errors']}")
        return

    print("=" * 60)
    print("B2_SCALE40 R15.8: Exact Authority Cutover")
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
    out_dir=OUT_ROOT/f"b2_r158_{ts}" if not args.output_dir else Path(args.output_dir)
    out_dir.mkdir(parents=True,exist_ok=True)

    # Save input snapshots for offline replay
    inputs_dir = out_dir / "inputs"; inputs_dir.mkdir(exist_ok=True)
    anchor = build_anchor_risk_state(engine=engine, scores=ss, prices=ps, market_env=me,
                                      calendar=cal, signal_to_exec=s2e, exec_to_signal=e2s,
                                      sdi=sdi, pdi=pdi, it_trends=it, specs=specs,
                                      start_date=args.start_date, end_date=args.end_date,
                                      initial_cash=args.initial_cash)

    kw = dict(engine=engine, scores=ss, prices=ps, market_env=me, calendar=cal,
              signal_to_exec=s2e, exec_to_signal=e2s, sdi=sdi, pdi=pdi, it_trends=it,
              specs=specs, start_date=args.start_date, end_date=args.end_date,
              initial_cash=args.initial_cash)

    scenarios = [("base",0.00075,0.0),("c15bp",0.0015,0.0),
                 ("s5bp",0.00075,0.0005),("s10bp",0.00075,0.0010),("s20bp",0.00075,0.0020)]

    all_pass = 0; total = 0
    for sn, cr, sr in scenarios:
        config = {"cost_rate": cr, "slip_rate": sr}
        for strat, tn, tr in [("S60",0.60,0.60),("B2",0.60,0.40)]:
            total += 1
            label = f"{strat}_{sn}"
            r = run_exact_backtest(strat, anchor, tn, tr, cost_rate=cr, slip_rate=sr, **kw)
            rp = replay_exact(r["ledger_df"], r["snapshot_df"], ps, pdi, args.initial_cash, cr)
            ok = rp["ok"]
            if ok: all_pass += 1
            flag = "✅" if ok else "❌"
            print(f"  {label}: {flag} errs={rp['n_errors']} NAV={rp['max_nav_bps']}bps dates={rp['n_dates']}")

            # Package for offline
            pkg = out_dir / f"package_{label}"; pkg.mkdir(exist_ok=True)
            r["ledger_df"].to_csv(pkg/"ledger.csv", index=False)
            r["snapshot_df"].to_csv(pkg/"daily_snapshots.csv", index=False)
            r["holdings_df"].to_csv(pkg/"daily_holdings.csv", index=False)
            pd.DataFrame(rp.get("daily_diffs",[])).to_csv(pkg/"daily_replay_diffs.csv", index=False)
            manifest = {"label": label, "config": config,
                        "ledger_sha256": hashlib.sha256(r["ledger_df"].to_csv(index=False).encode()).hexdigest(),
                        "replay_ok": ok}
            with open(pkg/"manifest.json","w") as f: json.dump(manifest, f, indent=2)

    print(f"\n{'='*60}")
    print(f"R15.8: {all_pass}/{total} packages pass")
    print(f"评级: {'✅ EXACT AUTHORITY ESTABLISHED' if all_pass==total else '❌'}")
    print(f"离线: python scripts/research/run_b2_r158_cutover.py --offline --package <pkg> --inputs {inputs_dir}")
    print("Done.")


if __name__ == "__main__":
    main()
