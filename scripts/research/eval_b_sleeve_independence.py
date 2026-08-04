#!/usr/bin/env python3
"""B-sleeve independence gate — E0-diagnostic retrospective evaluation (H009).

Pre-registered 2026-08-04 (b_sleeve_independent, H009): the B-point model
runs as an independent event-driven sleeve; combination is permitted only
when the sleeve gates pass (correlation < 0.5, incremental Sharpe > 0, MDD
reduction > 0, permutation p <= 0.05).

DATA-AVAILABILITY DEVIATION (documented): B-point features exist only from
2025-08-11 (Sina B/S pipeline start) — the pre-registered 2020-2022
selection window is inapplicable.  The sleeve NAV below is built from ALL
first-buy events (2025-08-11..2026-08-03) and is therefore IN-SAMPLE for
the active model (trained on these events) — E0-diagnostic, report-only,
never used for selection.  The clean test-split window (2026-06-11..
2026-07-06, 17 trading days) is below the gate's 30-day overlap minimum;
the E4 shadow (>= 60 trading days, >= 30 round trips, from 2026-08-05) is
the formal forward test.

Construction (mirrors the pre-registered execution block):
  - score every first-buy event with the ACTIVE model bundle (07-01 RF,
    apply_bs_model_scores, only_candidates=False; rank = 70*prob +
    30*(bs_score_v2/100), risk model removed per the v5.3 P0 freeze)
  - per event_date: take top_n=10 events by rank score, hold 20 trading
    days, equal weight across active positions, daily-rebalanced
  - daily returns from first_buy_price_paths_60d (rel_ret_dk - rel_ret_d{k-1})
    mapped onto the SSE trade calendar
  - evaluate_sleeve_gate vs the VLS Sleeve A blind-window NAV

Usage:
  python scripts/research/eval_b_sleeve_independence.py \
      --export-dir exports/signal_enhancement/20260803_220411 \
      --vls-nav exports/formal_evidence/vls_oos/runs/blind_2025_2026/trusted_account_backtest_nav.csv \
      --trade-calendar exports/formal_evidence/alpha_challengers/f1_no_value/snapshots/trade_calendar.csv \
      --output exports/formal_evidence/alpha_challengers/b_sleeve_independent/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scoreRank.core.bs_model_infer import (  # noqa: E402
    load_latest_bs_model, apply_bs_model_scores,
)
from scripts.research.alpha_sleeve_combiner import (  # noqa: E402
    load_nav_series, daily_returns, annualized_sharpe, max_drawdown,
    evaluate_sleeve_gate,
)

TOP_N, HOLD_DAYS = 10, 20
EXPORT_DIR = "exports/signal_enhancement/20260803_220411"
OUTPUT_ROOT = "exports/formal_evidence/alpha_challengers/b_sleeve_independent"


def build_sleeve_nav(events: pd.DataFrame, paths: pd.DataFrame,
                     cal: pd.DataFrame) -> pd.Series:
    """Equal-weight top-10 hold-20 NAV from B-event price paths.

    events: event_uid, event_date, bs_model_rank_score
    paths:  event_uid, rel_ret_d0..d59 (cumulative return vs d0 close)
    cal:    SSE trade calendar (cal_date, is_open)
    """
    trading = cal[cal["is_open"] == 1]["cal_date"].sort_values().reset_index(drop=True)
    day_idx = {d: i for i, d in enumerate(trading)}

    paths = paths.set_index("event_uid")
    ret_cols = [f"rel_ret_d{k}" for k in range(61)]
    daily = paths[ret_cols].diff(axis=1).fillna(0.0)
    daily.columns = [f"ret_d{k}" for k in range(61)]

    rows = []
    for _, ev in events.iterrows():
        uid = ev["event_uid"]
        if uid not in daily.index:
            continue
        d = ev["event_date"]
        if d not in day_idx:
            continue
        i0 = day_idx[d]
        for k in range(1, HOLD_DAYS + 1):
            if i0 + k < len(trading):
                rows.append({"trade_date": trading[i0 + k],
                             "event_uid": uid,
                             "ret": float(daily.loc[uid, f"ret_d{k}"])})
    pos = pd.DataFrame(rows)
    if pos.empty:
        return pd.Series(dtype=float)
    port = pos.groupby("trade_date")["ret"].mean()
    port = port.sort_index()
    nav = (1.0 + port).cumprod()
    return nav


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-dir", type=Path,
                        default=PROJECT_ROOT / EXPORT_DIR)
    parser.add_argument("--vls-nav", type=Path, required=True)
    parser.add_argument("--trade-calendar", type=Path, required=True)
    parser.add_argument("--output", type=Path,
                        default=PROJECT_ROOT / OUTPUT_ROOT)
    args = parser.parse_args()

    events = pd.read_csv(args.export_dir / "first_buy_events_labeled.csv")
    paths = pd.read_csv(args.export_dir / "first_buy_price_paths_60d.csv")
    cal = pd.read_csv(args.trade_calendar)
    cal["cal_date"] = pd.to_datetime(cal["cal_date"], errors="coerce")

    bundle = load_latest_bs_model()
    if bundle is None:
        print("FATAL: no active B-signal model bundle")
        return 2
    print(f"model: {bundle.get('version')} target={bundle.get('target')} "
          f"features={len(bundle.get('feature_cols') or [])}", flush=True)

    scored = apply_bs_model_scores(events, bundle, only_candidates=False)
    scored["event_date"] = pd.to_datetime(scored["event_date"], errors="coerce")
    use = scored.dropna(subset=["bs_model_rank_score"])
    print(f"events scored: {len(use)}/{len(scored)}", flush=True)

    # Top-10 selection per event date (pre-registered execution block).
    selected = (use.sort_values(["event_date", "bs_model_rank_score"],
                                ascending=[True, False])
                .groupby("event_date").head(TOP_N))
    print(f"selected positions: {len(selected)} across "
          f"{selected['event_date'].nunique()} dates", flush=True)

    b_nav = build_sleeve_nav(selected, paths, cal)
    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    b_nav.to_csv(out_dir / "b_sleeve_nav.csv", header=["nav"])

    vls_nav = load_nav_series(args.vls_nav)
    vls_nav.index = pd.to_datetime(vls_nav.index)

    overlap_start = max(b_nav.index.min(), vls_nav.index.min())
    overlap_end = min(b_nav.index.max(), vls_nav.index.max())
    vls_ov = vls_nav.loc[overlap_start:overlap_end]
    b_ov = b_nav.loc[overlap_start:overlap_end]
    print(f"overlap window: {overlap_start.date()} .. {overlap_end.date()} "
          f"({len(vls_ov)} days)", flush=True)

    gate = evaluate_sleeve_gate(vls_ov, b_ov, seed=20260804)
    b_ret = daily_returns(b_ov)
    report = {
        "challenger": "b_sleeve_independent",
        "hypothesis_id": "H009_b_point_sleeve",
        "evidence_tier": "E0_DIAGNOSTIC_IN_SAMPLE_RETROSPECTIVE",
        "label": ("IN-SAMPLE for the active model (trained on these events); "
                  "report-only, never selection. Formal forward test = E4 "
                  "shadow from 2026-08-05 (>=60 trading days, >=30 round trips)."),
        "deviation_note": ("Pre-registered 2020-2022 selection window "
                           "inapplicable: B features exist only from 2025-08-11 "
                           "(Sina B/S pipeline start). Clean test-split window "
                           "(2026-06-11..2026-07-06, 17 trading days) is below "
                           "the 30-day gate minimum."),
        "model": {"version": str(bundle.get("version")), "target": bundle.get("target")},
        "sleeve": {"top_n": TOP_N, "hold_days": HOLD_DAYS,
                   "n_events_scored": int(len(use)),
                   "n_positions": int(len(selected))},
        "overlap": {"start": str(overlap_start.date()), "end": str(overlap_end.date()),
                    "trading_days": int(len(vls_ov))},
        "b_sleeve": {"annualized_return": float((1.0 + b_ret).prod() ** (252 / max(len(b_ret), 1)) - 1.0),
                     "max_drawdown": float(max_drawdown(b_ov)),
                     "annualized_sharpe": round(annualized_sharpe(b_ret), 3)},
        "gate": gate,
        "holdout_usage": "REPORT_ONLY_SHOWN_NEVER_SELECTED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report_path = out_dir / "b_sleeve_independence_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nB_SLEEVE_INDEPENDENCE_DONE -> {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
