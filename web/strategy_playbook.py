from math import ceil
from datetime import date, datetime, timedelta

DEFAULT_PARAMS = {
    "pyramid_min_score": 60.0,
    "pyramid_top_pct": 30.0,
    "pyramid_min_claude": 50.0,
    "weighted_profile": "balanced",
    "weight_a": 0.4,
    "weight_b": 0.3,
    "weight_c": 0.3,
    "quadrant_min_score": 60.0,
    "quadrant_opt_cut": 6.0,
    "quadrant_claude_cut": 50.0,
    "weighted_top_n": 30,
}

WEIGHTED_PROFILES = {
    "balanced": (0.4, 0.3, 0.3),
    "aggressive": (0.3, 0.2, 0.5),
    "conservative": (0.5, 0.4, 0.1),
}


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def with_opt_percentile(rows):
    """Convert opt_score into rank percentile (1st=100, last=0)."""
    enriched = [dict(r) for r in rows]
    ranked = sorted(enriched, key=lambda x: float(x.get("opt_score") or 0), reverse=True)
    n = len(ranked)
    if n == 0:
        return []
    if n == 1:
        ranked[0]["opt_rank_pct"] = 100.0
        return ranked

    for idx, row in enumerate(ranked):
        # idx=0 => 100, idx=n-1 => 0
        pct = (1 - idx / (n - 1)) * 100
        row["opt_rank_pct"] = round(pct, 2)
    return ranked


def build_pyramid(rows, min_score, top_pct, min_claude):
    layer1 = [r for r in rows if float(r.get("score") or 0) > min_score]
    layer1_sorted = sorted(layer1, key=lambda x: float(x.get("opt_score") or 0), reverse=True)

    if not layer1_sorted or top_pct <= 0:
        return {
            "layer1": layer1_sorted,
            "layer2": [],
            "layer3": [],
            "top_count": 0,
        }

    top_count = max(1, ceil(len(layer1_sorted) * top_pct / 100))
    layer2 = layer1_sorted[:top_count]
    layer3 = [r for r in layer2 if float(r.get("claude_score") or 0) > min_claude]

    return {
        "layer1": layer1_sorted,
        "layer2": layer2,
        "layer3": layer3,
        "top_count": top_count,
    }


def build_weighted(rows, weight_a, weight_b, weight_c):
    ranked = with_opt_percentile(rows)
    out = []
    for row in ranked:
        r = dict(row)
        score = float(r.get("score") or 0)
        claude = float(r.get("claude_score") or 0)
        opt_pct = float(r.get("opt_rank_pct") or 0)
        final_score = score * weight_a + opt_pct * weight_b + claude * weight_c
        r["weighted_final_score"] = round(final_score, 2)
        out.append(r)
    out.sort(key=lambda x: x["weighted_final_score"], reverse=True)
    return out


def build_quadrants(rows, min_score, opt_cut, claude_cut):
    base = [r for r in rows if float(r.get("score") or 0) > min_score]
    q = {
        "star": [],         # 高opt 高claude
        "potential": [],    # 高opt 低claude
        "speculative": [],  # 低opt 高claude
        "avoid": [],        # 低opt 低claude
    }

    for row in base:
        opt = float(row.get("opt_score") or 0)
        claude = float(row.get("claude_score") or 0)
        if opt >= opt_cut and claude >= claude_cut:
            q["star"].append(row)
        elif opt >= opt_cut and claude < claude_cut:
            q["potential"].append(row)
        elif opt < opt_cut and claude >= claude_cut:
            q["speculative"].append(row)
        else:
            q["avoid"].append(row)

    return q, base


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


import statistics
import math

def _calc_bucket_stats(rows):
    out = {"count": len(rows)}
    for h in (3, 5, 10):
        rets = [_safe_float(r.get(f"ret_{h}")) for r in rows]
        # Filter for finite values only
        rets = [v for v in rets if v is not None and math.isfinite(v)]
        
        hits = [_safe_float(r.get(f"hit_{h}_10pct")) for r in rows]
        hits = [v for v in hits if v is not None and math.isfinite(v)]
        mdds = [_safe_float(r.get(f"mdd_{h}")) for r in rows]
        mdds = [v for v in mdds if v is not None and math.isfinite(v)]

        out[f"avg_ret_{h}"] = round(sum(rets) / len(rets) * 100, 2) if rets else None
        out[f"hit_{h}"] = round(sum(hits) / len(hits) * 100, 2) if hits else None
        out[f"avg_mdd_{h}"] = round(sum(mdds) / len(mdds) * 100, 2) if mdds else None
        
        if len(rets) > 1:
            std_v = statistics.stdev(rets)
            # Avoid division by zero
            out[f"sharpe_{h}"] = round(sum(rets) / len(rets) / (std_v + 1e-9), 4)
        else:
            out[f"sharpe_{h}"] = None

    return out


def evaluate_m2_presets(rows):
    """Evaluate strategy presets using M1 event+kpi merged rows.

    Input row keys expected:
    score/opt_score/claude_score/is_eligible + ret_3/5/10 + hit_3_10pct/5/10
    """
    eligible = [r for r in rows if int(r.get("is_eligible") or 0) == 1]

    py = build_pyramid(eligible, 60.0, 30.0, 50.0)
    py_rows = py["layer3"]

    w = build_weighted(eligible, 0.4, 0.3, 0.3)
    wd_rows = w[: max(1, len(w) // 3)] if w else []

    q, q_base = build_quadrants(eligible, 60.0, 6.0, 50.0)
    q_rows = q["star"]

    results = [
        {
            "strategy": "pyramid_default",
            "description": "总分>60 + 因子前30% + Claude>50",
            **_calc_bucket_stats(py_rows),
        },
        {
            "strategy": "weighted_balanced_top33pct",
            "description": "A/B/C=0.4/0.3/0.3，取前33%",
            **_calc_bucket_stats(wd_rows),
        },
        {
            "strategy": "quadrant_star_only",
            "description": "四象限仅明星股",
            **_calc_bucket_stats(q_rows),
        },
    ]

    # rank by 10-day avg return, then 10-day hit
    results.sort(
        key=lambda x: (
            x.get("avg_ret_10") if x.get("avg_ret_10") is not None else -10**9,
            x.get("hit_10") if x.get("hit_10") is not None else -10**9,
        ),
        reverse=True,
    )

    return {
        "eligible_total": len(eligible),
        "quadrant_base_total": len(q_base),
        "results": results,
    }



def _strategy_sort_key(item):
    return (
        item.get("avg_ret_10") if item.get("avg_ret_10") is not None else -10**9,
        item.get("hit_10") if item.get("hit_10") is not None else -10**9,
    )


def evaluate_m3_optimizer(rows):
    """Grid-search simple parameter sets and return best config per strategy family."""
    eligible = [r for r in rows if int(r.get("is_eligible") or 0) == 1]

    candidates = []

    # Pyramid grid
    for min_score in (55.0, 60.0, 65.0):
        for top_pct in (20.0, 30.0, 40.0):
            for min_claude in (45.0, 50.0, 60.0):
                py = build_pyramid(eligible, min_score, top_pct, min_claude)
                item = {
                    "family": "pyramid",
                    "params": f"score>{min_score}, top={top_pct}%, claude>{min_claude}",
                    **_calc_bucket_stats(py["layer3"]),
                }
                candidates.append(item)

    # Weighted grid
    for a, b, c in ((0.5, 0.2, 0.3), (0.4, 0.3, 0.3), (0.3, 0.2, 0.5), (0.6, 0.2, 0.2)):
        ranked = build_weighted(eligible, a, b, c)
        pick = ranked[: max(1, len(ranked) // 3)] if ranked else []
        item = {
            "family": "weighted",
            "params": f"A/B/C={a}/{b}/{c}, top33%",
            **_calc_bucket_stats(pick),
        }
        candidates.append(item)

    # Quadrant grid
    for min_score in (55.0, 60.0, 65.0):
        for opt_cut in (5.5, 6.0, 7.0):
            for claude_cut in (45.0, 50.0, 60.0):
                q, _ = build_quadrants(eligible, min_score, opt_cut, claude_cut)
                item = {
                    "family": "quadrant",
                    "params": f"score>{min_score}, opt>={opt_cut}, claude>={claude_cut}",
                    **_calc_bucket_stats(q["star"]),
                }
                candidates.append(item)

    winners = []
    for family in ("pyramid", "weighted", "quadrant"):
        fam_items = [x for x in candidates if x["family"] == family]
        fam_items.sort(key=_strategy_sort_key, reverse=True)
        if fam_items:
            winners.append(fam_items[0])

    winners.sort(key=_strategy_sort_key, reverse=True)

    return {
        "eligible_total": len(eligible),
        "searched_total": len(candidates),
        "winners": winners,
    }



def evaluate_m4_allocation(rows, max_positions=5):
    """Build M4 suggested allocation from M1 event rows.

    Rule: blend three strategy-family votes into a single m4_score.
    """
    eligible = [r for r in rows if int(r.get("is_eligible") or 0) == 1]
    scored = []

    for row in eligible:
        score = _safe_float(row.get("score")) or 0.0
        opt = _safe_float(row.get("opt_score")) or 0.0
        claude = _safe_float(row.get("claude_score")) or 0.0

        # family votes (0/1)
        vote_pyramid = 1 if (score > 60 and claude > 50) else 0
        vote_weighted = 1 if (0.4 * score + 0.3 * (opt * 10) + 0.3 * claude) >= 65 else 0
        vote_quadrant = 1 if (opt >= 6 and claude >= 50 and score > 60) else 0

        consensus = vote_pyramid + vote_weighted + vote_quadrant

        # blended score (0-100-ish)
        m4_score = 0.35 * score + 0.25 * (opt * 10) + 0.30 * claude + 10 * consensus

        scored.append(
            {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "score": round(score, 2),
                "opt_score": round(opt, 2),
                "claude_score": round(claude, 2),
                "consensus": consensus,
                "vote_pyramid": vote_pyramid,
                "vote_weighted": vote_weighted,
                "vote_quadrant": vote_quadrant,
                "m4_score": round(m4_score, 2),
                "ret_3": _safe_float(row.get("ret_3")),
                "ret_5": _safe_float(row.get("ret_5")),
                "ret_10": _safe_float(row.get("ret_10")),
                "hit_3_10pct": _safe_float(row.get("hit_3_10pct")),
                "hit_5_10pct": _safe_float(row.get("hit_5_10pct")),
                "hit_10_10pct": _safe_float(row.get("hit_10_10pct")),
            }
        )

    scored.sort(key=lambda x: (x["consensus"], x["m4_score"]), reverse=True)
    picks = scored[: max(1, int(max_positions or 5))] if scored else []

    total = len(picks)
    allocations = []
    if total > 0:
        # linear-decay weights then normalize
        raw = [max(total - i, 1) for i in range(total)]
        rs = sum(raw)
        for i, item in enumerate(picks):
            w = round(raw[i] / rs * 100, 2)
            alloc = dict(item)
            alloc["weight_pct"] = w
            allocations.append(alloc)

        # adjust rounding residue to first position
        residue = round(100 - sum(x["weight_pct"] for x in allocations), 2)
        allocations[0]["weight_pct"] = round(allocations[0]["weight_pct"] + residue, 2)

    return {
        "eligible_total": len(eligible),
        "candidates_total": len(scored),
        "picked_total": len(allocations),
        "allocations": allocations,
    }



def _calc_dispersion(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "std": None, "min": None, "max": None}
    n = len(vals)
    mean_v = sum(vals) / n
    var = sum((x - mean_v) ** 2 for x in vals) / n
    return {
        "mean": round(mean_v, 2),
        "std": round(var ** 0.5, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
    }


def evaluate_m5_rolling(rows, window_size=5, max_positions=5):
    """Rolling-window validation based on recent event_date slices."""
    eligible = [r for r in rows if int(r.get("is_eligible") or 0) == 1]
    by_date = {}
    for r in eligible:
        d = str(r.get("event_date") or "")
        if not d:
            continue
        by_date.setdefault(d, []).append(r)

    dates = sorted(by_date.keys())
    windows = []

    if window_size < 1:
        window_size = 1

    for end in range(window_size - 1, len(dates)):
        d_slice = dates[end - window_size + 1: end + 1]
        pool = []
        for d in d_slice:
            pool.extend(by_date.get(d, []))

        alloc = evaluate_m4_allocation(pool, max_positions=max_positions)
        picks = alloc.get("allocations") or []

        # compute realized stats using available horizon fields from selected picks
        stats = _calc_bucket_stats(picks)
        windows.append(
            {
                "end_date": d_slice[-1],
                "start_date": d_slice[0],
                "window_dates": len(d_slice),
                "sample_events": len(pool),
                "pick_count": len(picks),
                "avg_ret_10": stats.get("avg_ret_10"),
                "hit_10": stats.get("hit_10"),
                "avg_ret_5": stats.get("avg_ret_5"),
                "hit_5": stats.get("hit_5"),
            }
        )

    ret_series = [w.get("avg_ret_10") for w in windows]
    hit_series = [w.get("hit_10") for w in windows]

    return {
        "eligible_total": len(eligible),
        "window_size": int(window_size),
        "windows_total": len(windows),
        "summary_ret_10": _calc_dispersion(ret_series),
        "summary_hit_10": _calc_dispersion(hit_series),
        "windows": windows,
    }



def _calc_max_drawdown(nav_points):
    if not nav_points:
        return None
    peak = nav_points[0]["net_nav"]
    mdd = 0.0
    for p in nav_points:
        v = p["net_nav"]
        if v > peak:
            peak = v
        dd = (v / peak - 1.0) if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd
    return round(mdd * 100, 2)


M7_RULE_VERSION_V1 = "v1"
M7_RULE_VERSION_V21 = "m7_sell_v2.1"
M7_SELL_RULE_PRIORITY = [
    "BS_REVERSAL",
    "HARD_STOP",
    "LIMIT_DOWN_EXIT",
    "TRAILING_STOP",
    "TIME_STOP",
    "SCORE_EXIT",
    "REBALANCE_SELL",
]
M7_FORCED_REASON_CODES = {
    "BS_REVERSAL",
    "HARD_STOP",
    "LIMIT_DOWN_EXIT",
    "TRAILING_STOP",
    "TIME_STOP",
    "SCORE_EXIT",
}


def _coerce_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except Exception:
            return None
    return None


def _to_ymd(v):
    d = _coerce_date(v)
    return d.strftime("%Y%m%d") if d else None


def _normalize_stop_loss_pct(stop_loss_pct):
    v = float(stop_loss_pct or 0.0)
    # 兼容旧入参：曾经传 0.07 表示 7%
    if 0 < v <= 1:
        v = v * 100.0
    return max(0.0, v)


def _calc_m4_score(score, opt, claude):
    score = float(score or 0.0)
    opt = float(opt or 0.0)
    claude = float(claude or 0.0)
    vote_pyramid = 1 if (score > 60 and claude > 50) else 0
    vote_weighted = 1 if (0.4 * score + 0.3 * (opt * 10.0) + 0.3 * claude) >= 65 else 0
    vote_quadrant = 1 if (opt >= 6 and claude >= 50 and score > 60) else 0
    consensus = vote_pyramid + vote_weighted + vote_quadrant
    return round(0.35 * score + 0.25 * (opt * 10.0) + 0.30 * claude + 10.0 * consensus, 2)


def _calc_limit_ratio(symbol, is_st=0):
    if int(is_st or 0) == 1:
        return 0.05
    raw = str(symbol or "").zfill(6)
    if raw.startswith(("8", "4")):
        return 0.30
    if raw.startswith(("300", "301", "688")):
        return 0.20
    return 0.10


def _fetch_trade_day_index(conn, asof_date=None, lookback_days=160):
    out = {"ordered": [], "index": {}, "asof_trade_day": None}
    if conn is None:
        return out
    lookback_days = max(30, int(lookback_days or 160))
    asof_ymd = _to_ymd(asof_date)
    try:
        with conn.cursor() as cursor:
            if asof_ymd:
                cursor.execute(
                    """
                    SELECT cal_date
                    FROM chenyiyun.dim_trade_cal
                    WHERE exchange = 'SSE'
                      AND is_open = 1
                      AND cal_date <= %s
                    ORDER BY cal_date DESC
                    LIMIT %s
                    """,
                    (asof_ymd, lookback_days),
                )
            else:
                cursor.execute(
                    """
                    SELECT cal_date
                    FROM chenyiyun.dim_trade_cal
                    WHERE exchange = 'SSE'
                      AND is_open = 1
                    ORDER BY cal_date DESC
                    LIMIT %s
                    """,
                    (lookback_days,),
                )
            rows = cursor.fetchall()
    except Exception as e:
        print(f"Failed to fetch trade calendar for M7: {e}")
        return out

    ordered = sorted({_to_ymd(r.get("cal_date")) for r in rows if _to_ymd(r.get("cal_date"))})
    idx = {d: i for i, d in enumerate(ordered)}

    asof_trade_day = None
    if ordered:
        if asof_ymd and asof_ymd in idx:
            asof_trade_day = asof_ymd
        elif asof_ymd:
            # 如果 asof 不是交易日，取 <= asof 的最近交易日
            for d in reversed(ordered):
                if d <= asof_ymd:
                    asof_trade_day = d
                    break
        if asof_trade_day is None:
            asof_trade_day = ordered[-1]

    out["ordered"] = ordered
    out["index"] = idx
    out["asof_trade_day"] = asof_trade_day
    return out


def _trade_days_between(trade_day_index, start_date, end_date=None):
    if not trade_day_index:
        return None
    idx_map = trade_day_index.get("index") or {}
    ordered = trade_day_index.get("ordered") or []
    if not idx_map or not ordered:
        return None

    s = _to_ymd(start_date)
    e = _to_ymd(end_date) or trade_day_index.get("asof_trade_day")
    if not s or not e:
        return None

    if s not in idx_map:
        # 建仓日可能是非交易日，向后取下一个交易日
        for d in ordered:
            if d >= s:
                s = d
                break
    if e not in idx_map:
        # 评估日可能是非交易日，向前取上一个交易日
        for d in reversed(ordered):
            if d <= e:
                e = d
                break

    if s not in idx_map or e not in idx_map:
        return None
    diff = idx_map[e] - idx_map[s]
    return diff if diff >= 0 else None


def _calc_holding_trade_days(trade_day_index, entry_date, asof_trade_day):
    d = _trade_days_between(trade_day_index, entry_date, asof_trade_day)
    return int(d + 1) if d is not None else None


def _fetch_latest_bs_signal_state(conn, symbols):
    """
    Return latest B/S snapshot per symbol.
    has_exit_signal=True means latest sell date is newer/equal to latest buy date.
    """
    state = {}
    if conn is None:
        return state

    symbol_list = [str(s).zfill(6) for s in (symbols or []) if str(s or "").strip()]
    if not symbol_list:
        return state

    placeholders = ",".join(["%s"] * len(symbol_list))
    sql = f"""
        SELECT
            stock_code,
            MAX(CASE WHEN has_buy_signal = 1 THEN batch_date END) AS latest_buy_date,
            MAX(CASE WHEN has_sell_signal = 1 THEN batch_date END) AS latest_sell_date
        FROM bs_detection_results
        WHERE stock_code IN ({placeholders})
        GROUP BY stock_code
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, tuple(symbol_list))
            for row in cursor.fetchall():
                symbol = str(row.get("stock_code") or "").zfill(6)
                if not symbol:
                    continue
                latest_buy = _coerce_date(row.get("latest_buy_date"))
                latest_sell = _coerce_date(row.get("latest_sell_date"))
                has_exit = bool(latest_sell and ((not latest_buy) or latest_sell >= latest_buy))
                state[symbol] = {
                    "latest_buy_date": latest_buy,
                    "latest_sell_date": latest_sell,
                    "has_exit_signal": has_exit,
                }
    except Exception as e:
        print(f"Failed to fetch B/S state for M7 exits: {e}")

    return state


def _fetch_latest_market_state(conn, symbols, asof_date=None):
    state = {}
    if conn is None:
        return state
    symbol_list = [str(s).zfill(6) for s in (symbols or []) if str(s or "").strip()]
    if not symbol_list:
        return state

    placeholders = ",".join(["%s"] * len(symbol_list))
    asof_ymd = _to_ymd(asof_date)
    try:
        with conn.cursor() as cursor:
            if asof_ymd:
                sql = f"""
                    SELECT
                        s.symbol,
                        t.trade_date,
                        t.close,
                        t.pre_close,
                        t.low,
                        t.high,
                        t.vol,
                        t.amount
                    FROM tushare_stock.ods_daily t
                    JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
                    WHERE s.symbol IN ({placeholders})
                      AND t.trade_date = (
                          SELECT MAX(trade_date)
                          FROM tushare_stock.ods_daily
                          WHERE trade_date <= %s
                      )
                """
                params = tuple(symbol_list + [asof_ymd])
            else:
                sql = f"""
                    SELECT
                        s.symbol,
                        t.trade_date,
                        t.close,
                        t.pre_close,
                        t.low,
                        t.high,
                        t.vol,
                        t.amount
                    FROM tushare_stock.ods_daily t
                    JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
                    WHERE s.symbol IN ({placeholders})
                      AND t.trade_date = (
                          SELECT MAX(trade_date)
                          FROM tushare_stock.ods_daily
                      )
                """
                params = tuple(symbol_list)

            cursor.execute(sql, params)
            rows = cursor.fetchall()
    except Exception as e:
        print(f"Failed to fetch market state for M7 exits: {e}")
        rows = []

    for row in rows:
        symbol = str(row.get("symbol") or "").zfill(6)
        if not symbol:
            continue
        close = _safe_float(row.get("close"))
        pre_close = _safe_float(row.get("pre_close"))
        low = _safe_float(row.get("low"))
        high = _safe_float(row.get("high"))
        vol = _safe_float(row.get("vol"))
        amount = _safe_float(row.get("amount"))
        ratio = _calc_limit_ratio(symbol, is_st=0)
        down_line = (pre_close * (1.0 - ratio)) if (pre_close and pre_close > 0) else None
        is_limit_down = bool(
            close is not None
            and down_line is not None
            and close <= down_line + 0.01
            and (low is None or low <= down_line + 0.01)
        )
        is_suspended = bool(
            close is None
            or close <= 0
            or (
                vol is not None
                and amount is not None
                and vol <= 0
                and amount <= 0
            )
        )
        state[symbol] = {
            "trade_date": _to_ymd(row.get("trade_date")),
            "close": close,
            "pre_close": pre_close,
            "low": low,
            "high": high,
            "vol": vol,
            "amount": amount,
            "is_limit_down": is_limit_down,
            "is_suspended": is_suspended,
            "tradable": bool((not is_limit_down) and (not is_suspended)),
        }
    return state


def _fetch_market_index_daily_change(conn, asof_date=None, index_ts_code="000300.SH"):
    if conn is None:
        return None
    asof_ymd = _to_ymd(asof_date)
    queries = [
        (
            """
            SELECT pct_chg
            FROM tushare_stock.ods_index_daily
            WHERE ts_code = %s
              AND trade_date = (
                  SELECT MAX(trade_date)
                  FROM tushare_stock.ods_index_daily
                  WHERE ts_code = %s
                    AND (%s IS NULL OR trade_date <= %s)
              )
            LIMIT 1
            """,
            (index_ts_code, index_ts_code, asof_ymd, asof_ymd),
        ),
        (
            """
            SELECT pct_chg
            FROM tushare_stock.dwd_index_daily
            WHERE ts_code = %s
              AND trade_date = (
                  SELECT MAX(trade_date)
                  FROM tushare_stock.dwd_index_daily
                  WHERE ts_code = %s
                    AND (%s IS NULL OR trade_date <= %s)
              )
            LIMIT 1
            """,
            (index_ts_code, index_ts_code, asof_ymd, asof_ymd),
        ),
    ]
    for sql, params in queries:
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone() or {}
            pct = _safe_float(row.get("pct_chg"))
            if pct is not None:
                return float(pct)
        except Exception:
            continue
    return None


def _fetch_recent_score_state(conn, symbols, recent_trade_days):
    out = {}
    symbol_list = [str(s).zfill(6) for s in (symbols or []) if str(s or "").strip()]
    if not symbol_list:
        return out
    for s in symbol_list:
        out[s] = {"rows_desc": [], "by_trade_day": {}, "score_date": None}
    if conn is None:
        return out

    dates = [d for d in (recent_trade_days or []) if d]
    if not dates:
        return out
    date_objs = [_coerce_date(d) for d in dates]
    date_objs = [d for d in date_objs if d]
    if not date_objs:
        return out

    sym_placeholders = ",".join(["%s"] * len(symbol_list))
    dt_placeholders = ",".join(["%s"] * len(date_objs))

    rows = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'b_event_fact'")
            has_fact = cursor.fetchone() is not None
            if has_fact:
                cursor.execute(
                    f"""
                    SELECT event_date, symbol, score, COALESCE(opt_score, 0) AS opt_score, COALESCE(claude_score, 0) AS claude_score
                    FROM b_event_fact
                    WHERE symbol IN ({sym_placeholders})
                      AND event_date IN ({dt_placeholders})
                    """,
                    tuple(symbol_list + date_objs),
                )
                rows = cursor.fetchall()
    except Exception as e:
        print(f"Failed to fetch b_event_fact score state for M7: {e}")
        rows = []

    # 回退：若 b_event_fact 不可用，退化到 score_rank_daily（m4_score 用 score 近似）
    if not rows:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT trade_date AS event_date, symbol, score, 0 AS opt_score, COALESCE(claude_score, 0) AS claude_score
                    FROM score_rank_daily
                    WHERE symbol IN ({sym_placeholders})
                      AND trade_date IN ({dt_placeholders})
                    """,
                    tuple(symbol_list + date_objs),
                )
                rows = cursor.fetchall()
        except Exception as e:
            print(f"Failed to fetch score_rank_daily state for M7: {e}")
            rows = []

    for row in rows:
        symbol = str(row.get("symbol") or "").zfill(6)
        if symbol not in out:
            continue
        td = _to_ymd(row.get("event_date"))
        if not td:
            continue
        claude = _safe_float(row.get("claude_score")) or 0.0
        score = _safe_float(row.get("score")) or 0.0
        opt = _safe_float(row.get("opt_score")) or 0.0
        m4_score = _calc_m4_score(score, opt, claude) if opt or "opt_score" in row else float(score)
        rec = {"trade_date": td, "claude_score": float(claude), "m4_score": float(m4_score)}
        out[symbol]["by_trade_day"][td] = rec

    for symbol in out.keys():
        by_day = out[symbol]["by_trade_day"]
        rows_desc = [by_day[d] for d in dates if d in by_day]
        out[symbol]["rows_desc"] = rows_desc
        out[symbol]["score_date"] = rows_desc[0]["trade_date"] if rows_desc else None

    return out


def _fetch_rebuy_cooldown_map(conn, asof_date, rebuy_cooldown_days):
    out = {}
    days = int(rebuy_cooldown_days or 0)
    if conn is None or days <= 0:
        return out
    asof = _coerce_date(asof_date)
    if not asof:
        return out
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'm7_sell_signals'")
            if cursor.fetchone() is None:
                return out
            forced_codes = tuple(sorted(M7_FORCED_REASON_CODES))
            placeholders = ",".join(["%s"] * len(forced_codes))
            cursor.execute(
                f"""
                SELECT symbol, MAX(signal_date) AS latest_signal_date
                FROM m7_sell_signals
                WHERE reason_code IN ({placeholders})
                  AND COALESCE(pending_flag, 0) = 0
                  AND signal_date <= %s
                GROUP BY symbol
                """,
                tuple(list(forced_codes) + [asof]),
            )
            rows = cursor.fetchall()
        for row in rows:
            symbol = str(row.get("symbol") or "").zfill(6)
            sig_date = _coerce_date(row.get("latest_signal_date"))
            if not symbol or not sig_date:
                continue
            out[symbol] = sig_date + timedelta(days=days)
    except Exception as e:
        print(f"Failed to fetch rebuy cooldown map for M7: {e}")
    return out


def build_position_context(
    symbol,
    target_info,
    current_info,
    total_capital,
    asof_trade_day,
    trade_day_index,
    recent_trade_days,
    bs_state_map,
    market_state_map,
    score_state_map,
    rebuy_cooldown_map,
    index_daily_change_pct=None,
):
    t_info = target_info or {}
    c_info = current_info or {}
    bs_state = (bs_state_map or {}).get(symbol) or {}
    market = (market_state_map or {}).get(symbol) or {}
    score_state = (score_state_map or {}).get(symbol) or {"rows_desc": [], "by_trade_day": {}, "score_date": None}

    raw_target_weight = float(t_info.get("target_weight") or 0.0)
    current_weight = float(c_info.get("current_weight") or 0.0)
    delta_w = round(raw_target_weight - current_weight, 4)
    trade_notional = abs(float(total_capital or 0.0) * delta_w / 100.0)

    shares = int(c_info.get("shares") or 0)
    avg_cost = _safe_float(c_info.get("avg_cost"))
    price = _safe_float(market.get("close"))
    if price is None or price <= 0:
        price = _safe_float(c_info.get("current_price"))
    current_price = float(price or 0.0)

    highest_since_entry = _safe_float(c_info.get("highest_since_entry"))
    if highest_since_entry is None or highest_since_entry <= 0:
        highest_since_entry = current_price if current_price > 0 else None
    elif current_price > 0:
        highest_since_entry = max(highest_since_entry, current_price)

    holding_trade_days = _safe_float(c_info.get("holding_trade_days"))
    if holding_trade_days is None or holding_trade_days <= 0:
        holding_trade_days = _calc_holding_trade_days(
            trade_day_index=trade_day_index,
            entry_date=c_info.get("entry_date"),
            asof_trade_day=asof_trade_day,
        )
    holding_trade_days = int(holding_trade_days or 0)

    current_return_pct = None
    if avg_cost and avg_cost > 0 and current_price > 0:
        current_return_pct = (current_price / avg_cost - 1.0) * 100.0

    relative_return_vs_index = _safe_float(c_info.get("relative_return_vs_index"))
    if relative_return_vs_index is None and current_return_pct is not None and index_daily_change_pct is not None:
        relative_return_vs_index = current_return_pct - float(index_daily_change_pct)

    latest_buy_date = _coerce_date(bs_state.get("latest_buy_date"))
    latest_sell_date = _coerce_date(bs_state.get("latest_sell_date"))
    bs_sell_trade_days = _trade_days_between(trade_day_index, latest_sell_date, asof_trade_day) if latest_sell_date else None

    cooldown_until = _coerce_date(c_info.get("rebuy_cooldown_until"))
    external_cd = _coerce_date((rebuy_cooldown_map or {}).get(symbol))
    if external_cd and (not cooldown_until or external_cd > cooldown_until):
        cooldown_until = external_cd
    asof_date_obj = _coerce_date(asof_trade_day)
    in_rebuy_cooldown = bool(cooldown_until and asof_date_obj and asof_date_obj <= cooldown_until)

    return {
        "symbol": symbol,
        "name": t_info.get("name") or c_info.get("name") or symbol,
        "has_position": bool(c_info),
        "shares": shares,
        "entry_date": _coerce_date(c_info.get("entry_date")),
        "avg_cost": avg_cost,
        "current_price": current_price,
        "highest_since_entry": highest_since_entry,
        "holding_trade_days": holding_trade_days,
        "current_weight": current_weight,
        "raw_target_weight": raw_target_weight,
        "delta_weight": delta_w,
        "trade_notional": trade_notional,
        "m4_score": _safe_float(t_info.get("m4_score")),
        "current_return_pct": current_return_pct,
        "relative_return_vs_index": relative_return_vs_index,
        "pending_forced_exit": int(c_info.get("pending_forced_exit") or 0),
        "pending_exit_reason": str(c_info.get("pending_exit_reason") or "").strip() or None,
        "cooldown_until": cooldown_until,
        "in_rebuy_cooldown": in_rebuy_cooldown,
        "bs_has_exit_signal": bool(bs_state.get("has_exit_signal")),
        "bs_latest_buy_date": latest_buy_date,
        "bs_latest_sell_date": latest_sell_date,
        "bs_sell_trade_days": bs_sell_trade_days,
        "market_state": market,
        "score_state": score_state,
        "score_date": score_state.get("score_date"),
        "score_rows_desc": score_state.get("rows_desc") or [],
        "score_rows_by_trade_day": score_state.get("by_trade_day") or {},
        "recent_trade_days": list(recent_trade_days or []),
        "asof_trade_day": _to_ymd(asof_trade_day),
        "index_daily_change_pct": index_daily_change_pct,
    }


def _build_rule_hit(
    code,
    reason,
    detail,
    forced_exit,
    pending_flag=0,
    pending_reason=None,
    exec_status="NEW",
    protect_window_hit=0,
    market_risk_gate_hit=0,
):
    return {
        "reason_code": code,
        "reason": reason,
        "reason_detail_json": detail or {},
        "forced_exit": bool(forced_exit),
        "pending_flag": int(pending_flag or 0),
        "pending_reason": pending_reason,
        "exec_status": str(exec_status or "NEW"),
        "protect_window_hit": int(protect_window_hit or 0),
        "market_risk_gate_hit": int(market_risk_gate_hit or 0),
    }


def _rule_bs_reversal(ctx, params):
    if not ctx.get("has_position"):
        return None
    latest_sell = ctx.get("bs_latest_sell_date")
    latest_buy = ctx.get("bs_latest_buy_date")
    if not latest_sell:
        return None
    if latest_buy and latest_sell < latest_buy:
        return None
    fresh_days = int(params.get("bs_fresh_trade_days") or 3)
    sell_trade_days = ctx.get("bs_sell_trade_days")
    if sell_trade_days is None:
        asof = _coerce_date(ctx.get("asof_trade_day"))
        if asof and latest_sell:
            sell_trade_days = max((asof - latest_sell).days, 0)
    if sell_trade_days is None or sell_trade_days > fresh_days:
        return None
    return _build_rule_hit(
        code="BS_REVERSAL",
        reason="B/S反转卖出",
        detail={
            "latest_buy_date": latest_buy.isoformat() if latest_buy else None,
            "latest_sell_date": latest_sell.isoformat() if latest_sell else None,
            "sell_trade_days": int(sell_trade_days),
            "fresh_trade_days": int(fresh_days),
        },
        forced_exit=True,
    )


def _rule_hard_stop(ctx, params):
    if not ctx.get("has_position"):
        return None
    avg_cost = _safe_float(ctx.get("avg_cost"))
    current_price = _safe_float(ctx.get("current_price"))
    stop_loss_pct = float(params.get("stop_loss_pct") or 6.0)
    if not avg_cost or avg_cost <= 0 or not current_price or current_price <= 0:
        return None
    threshold_price = avg_cost * (1.0 - stop_loss_pct / 100.0)
    if current_price > threshold_price:
        return None
    loss_pct = (1.0 - current_price / avg_cost) * 100.0
    return _build_rule_hit(
        code="HARD_STOP",
        reason="硬止损卖出",
        detail={
            "avg_cost": round(avg_cost, 4),
            "current_price": round(current_price, 4),
            "loss_pct": round(loss_pct, 4),
            "threshold_pct": round(stop_loss_pct, 4),
        },
        forced_exit=True,
    )


def _rule_limit_down_exit(ctx, _params):
    if not ctx.get("has_position"):
        return None
    market = ctx.get("market_state") or {}
    is_limit_down = bool(market.get("is_limit_down"))
    is_suspended = bool(market.get("is_suspended"))

    pending_prev = bool(ctx.get("pending_forced_exit")) and str(ctx.get("pending_exit_reason") or "") in {"LIMIT_DOWN", "SUSPENDED"}
    if is_limit_down or (pending_prev and is_suspended):
        pending_reason = "LIMIT_DOWN" if is_limit_down else "SUSPENDED"
        return _build_rule_hit(
            code="LIMIT_DOWN_EXIT",
            reason="跌停/停牌强制退出",
            detail={
                "trade_date": market.get("trade_date"),
                "close": market.get("close"),
                "pre_close": market.get("pre_close"),
                "is_limit_down": int(is_limit_down),
                "is_suspended": int(is_suspended),
                "pending_retry": int(pending_prev),
            },
            forced_exit=True,
            pending_flag=1,
            pending_reason=pending_reason,
            exec_status="PENDING",
        )

    if pending_prev:
        # 次日优先重试：不再跌停/停牌时，直接转为可执行强制卖出
        return _build_rule_hit(
            code="LIMIT_DOWN_EXIT",
            reason="挂起强制卖出重试",
            detail={
                "trade_date": market.get("trade_date"),
                "close": market.get("close"),
                "pre_close": market.get("pre_close"),
                "retry_from_pending": 1,
                "last_pending_reason": ctx.get("pending_exit_reason"),
            },
            forced_exit=True,
            pending_flag=0,
            pending_reason=None,
            exec_status="NEW",
        )
    return None


def _rule_trailing_stop(ctx, params):
    if not ctx.get("has_position"):
        return None
    avg_cost = _safe_float(ctx.get("avg_cost"))
    highest = _safe_float(ctx.get("highest_since_entry"))
    current_price = _safe_float(ctx.get("current_price"))
    if not avg_cost or avg_cost <= 0 or not highest or highest <= 0 or not current_price or current_price <= 0:
        return None
    activate_pct = float(params.get("trail_activate_pct") or 12.0)
    drawdown_pct = float(params.get("trail_drawdown_pct") or 4.0)
    peak_return_pct = (highest / avg_cost - 1.0) * 100.0
    pullback_pct = (highest - current_price) / highest * 100.0
    if peak_return_pct < activate_pct or pullback_pct < drawdown_pct:
        return None
    return _build_rule_hit(
        code="TRAILING_STOP",
        reason="移动止损卖出",
        detail={
            "avg_cost": round(avg_cost, 4),
            "highest_since_entry": round(highest, 4),
            "current_price": round(current_price, 4),
            "peak_return_pct": round(peak_return_pct, 4),
            "drawdown_pct": round(pullback_pct, 4),
            "trail_activate_pct": round(activate_pct, 4),
            "trail_drawdown_pct": round(drawdown_pct, 4),
        },
        forced_exit=True,
    )


def _rule_time_stop(ctx, params):
    if not ctx.get("has_position"):
        return None

    holding_trade_days = int(ctx.get("holding_trade_days") or 0)
    min_hold_protect_days = int(params.get("min_hold_protect_days") or 5)
    protect_window_hit = 1 if holding_trade_days > 0 and holding_trade_days < min_hold_protect_days else 0
    if protect_window_hit:
        return None

    if bool(params.get("enable_market_risk_gate")) and bool(ctx.get("market_risk_gate_hit")):
        return None

    time_stop_days = int(params.get("time_stop_days") or 8)
    min_ret = float(params.get("time_stop_min_return_pct") or 1.0)
    rel_vs_idx_cut = float(params.get("time_stop_rel_index_pct") or -3.0)
    current_ret = _safe_float(ctx.get("current_return_pct"))
    rel_vs_idx = _safe_float(ctx.get("relative_return_vs_index"))
    if current_ret is None or rel_vs_idx is None:
        return None
    if holding_trade_days < time_stop_days:
        return None
    if current_ret >= min_ret:
        return None
    if rel_vs_idx > rel_vs_idx_cut:
        return None

    return _build_rule_hit(
        code="TIME_STOP",
        reason="时间止损卖出",
        detail={
            "holding_trade_days": holding_trade_days,
            "time_stop_days": time_stop_days,
            "current_return_pct": round(current_ret, 4),
            "time_stop_min_return_pct": round(min_ret, 4),
            "relative_return_vs_index": round(rel_vs_idx, 4),
            "time_stop_rel_index_pct": round(rel_vs_idx_cut, 4),
            "min_hold_protect_days": min_hold_protect_days,
        },
        forced_exit=True,
        protect_window_hit=0,
        market_risk_gate_hit=1 if bool(ctx.get("market_risk_gate_hit")) else 0,
    )


def _rule_score_exit(ctx, params):
    if not ctx.get("has_position"):
        return None
    if not bool(params.get("is_post_close", True)):
        return None
    if bool(params.get("enable_market_risk_gate")) and bool(ctx.get("market_risk_gate_hit")):
        return None

    score_date = ctx.get("score_date")
    asof_trade_day = ctx.get("asof_trade_day")
    if not score_date or not asof_trade_day or score_date != asof_trade_day:
        return None

    claude_floor = float(params.get("claude_floor") or 45.0)
    score_floor = float(params.get("score_floor") or 60.0)
    confirm_days = max(1, int(params.get("score_confirm_days") or 2))
    recent_trade_days = list(ctx.get("recent_trade_days") or [])
    by_trade_day = ctx.get("score_rows_by_trade_day") or {}
    if len(recent_trade_days) < confirm_days:
        return None

    checks = []
    needed = recent_trade_days[:confirm_days]
    for td in needed:
        row = by_trade_day.get(td)
        if not row:
            return None
        claude = _safe_float(row.get("claude_score"))
        m4_score = _safe_float(row.get("m4_score"))
        if claude is None or m4_score is None:
            return None
        ok = bool(claude < claude_floor and m4_score < score_floor)
        checks.append(
            {
                "trade_date": td,
                "claude_score": round(float(claude), 4),
                "m4_score": round(float(m4_score), 4),
                "ok": int(ok),
            }
        )
        if not ok:
            return None

    return _build_rule_hit(
        code="SCORE_EXIT",
        reason="评分退场卖出",
        detail={
            "score_date": score_date,
            "asof_trade_day": asof_trade_day,
            "claude_floor": round(claude_floor, 4),
            "score_floor": round(score_floor, 4),
            "score_confirm_days": confirm_days,
            "confirm_checks": checks,
        },
        forced_exit=True,
        market_risk_gate_hit=1 if bool(ctx.get("market_risk_gate_hit")) else 0,
    )


def _rule_rebalance_sell(ctx, params):
    if not ctx.get("has_position"):
        return None
    delta_w = float(ctx.get("delta_weight") or 0.0)
    if delta_w >= 0:
        return None
    min_trade_weight = float(params.get("min_trade_weight") or 1.0)
    min_trade_notional = float(params.get("min_trade_notional") or 5000.0)
    trade_notional = float(ctx.get("trade_notional") or 0.0)
    if abs(delta_w) < min_trade_weight:
        return None
    if trade_notional < min_trade_notional:
        return None
    return _build_rule_hit(
        code="REBALANCE_SELL",
        reason="目标权重下调",
        detail={
            "delta_weight": round(delta_w, 4),
            "min_trade_weight": round(min_trade_weight, 4),
            "trade_notional": round(trade_notional, 2),
            "min_trade_notional": round(min_trade_notional, 2),
        },
        forced_exit=False,
    )


def debug_rule_hits(symbol, context, rule_checks, hit):
    return {
        "symbol": symbol,
        "asof_trade_day": context.get("asof_trade_day"),
        "pending_forced_exit": int(context.get("pending_forced_exit") or 0),
        "in_rebuy_cooldown": int(context.get("in_rebuy_cooldown") or 0),
        "checked_rules": rule_checks,
        "hit_reason_code": hit.get("reason_code") if hit else None,
    }


def _apply_buy_cash_budget(buy_orders, cash_available):
    kept = []
    cash = float(cash_available or 0.0)
    for o in sorted(buy_orders, key=lambda x: x.get("notional", 0), reverse=True):
        notional = float(o.get("notional") or 0.0)
        price = float(o.get("price") or 0.0)
        shares = int(o.get("shares") or 0)
        if notional <= 0 or price <= 0 or shares <= 0:
            continue
        if cash >= notional:
            kept.append(o)
            cash -= notional
            continue

        # 资金不足时按 100 股向下缩减，允许保留现金，不强行凑满目标买单
        lot_shares = int(cash // (price * 100.0)) * 100
        if lot_shares <= 0:
            continue
        o2 = dict(o)
        o2["shares"] = lot_shares
        o2["notional"] = round(lot_shares * price, 2)
        o2["reason"] = str(o2.get("reason") or "目标权重提升") + "；资金约束下调"
        kept.append(o2)
        cash -= o2["notional"]
    return kept


def _evaluate_m7_rebalance_v21(
    target_allocations,
    current_positions,
    total_capital=100000.0,
    min_trade_weight=1.0,
    min_trade_notional=5000.0,
    conn=None,
    stop_loss_pct=6.0,
    asof_date=None,
    bs_fresh_trade_days=3,
    trail_activate_pct=12.0,
    trail_drawdown_pct=4.0,
    time_stop_days=8,
    time_stop_min_return_pct=1.0,
    time_stop_rel_index_pct=-3.0,
    min_hold_protect_days=5,
    enable_market_risk_gate=False,
    market_risk_gate_drop_pct=-2.0,
    claude_floor=45.0,
    score_floor=60.0,
    score_confirm_days=2,
    is_post_close=True,
    rebuy_cooldown_days=5,
    bs_state_override=None,
    market_state_override=None,
    score_state_override=None,
    trade_day_index_override=None,
    rebuy_cooldown_override=None,
):
    total_capital = float(total_capital or 0.0)
    if total_capital <= 0:
        total_capital = 100000.0

    normalized_stop_loss_pct = _normalize_stop_loss_pct(stop_loss_pct)
    params = {
        "stop_loss_pct": normalized_stop_loss_pct,
        "min_trade_weight": float(min_trade_weight or 1.0),
        "min_trade_notional": float(min_trade_notional or 5000.0),
        "bs_fresh_trade_days": int(bs_fresh_trade_days or 3),
        "trail_activate_pct": float(trail_activate_pct or 12.0),
        "trail_drawdown_pct": float(trail_drawdown_pct or 4.0),
        "time_stop_days": int(time_stop_days or 8),
        "time_stop_min_return_pct": float(time_stop_min_return_pct or 1.0),
        "time_stop_rel_index_pct": float(time_stop_rel_index_pct or -3.0),
        "min_hold_protect_days": int(min_hold_protect_days or 5),
        "enable_market_risk_gate": bool(enable_market_risk_gate),
        "market_risk_gate_drop_pct": float(market_risk_gate_drop_pct if market_risk_gate_drop_pct is not None else -2.0),
        "claude_floor": float(claude_floor or 45.0),
        "score_floor": float(score_floor or 60.0),
        "score_confirm_days": int(score_confirm_days or 2),
        "is_post_close": bool(is_post_close),
    }

    target_map = {}
    for t in target_allocations or []:
        symbol = str(t.get("symbol") or "").zfill(6) if t.get("symbol") else ""
        if not symbol:
            continue
        target_map[symbol] = {
            "symbol": symbol,
            "name": t.get("name"),
            "target_weight": float(_safe_float(t.get("weight_pct")) or 0.0),
            "m4_score": _safe_float(t.get("m4_score")),
        }

    current_map = {}
    current_market_value = 0.0
    for p in current_positions or []:
        symbol = str(p.get("symbol") or "").zfill(6) if p.get("symbol") else ""
        if not symbol:
            continue
        market_value = _safe_float(p.get("market_value"))
        weight_pct = _safe_float(p.get("weight_pct"))
        if weight_pct is None and market_value is not None and total_capital > 0:
            weight_pct = market_value / total_capital * 100.0
        shares = int(p.get("shares") or 0)
        current_price = _safe_float(p.get("current_price")) or 0.0
        if market_value is None:
            market_value = shares * current_price
        current_market_value += float(market_value or 0.0)
        current_map[symbol] = {
            "symbol": symbol,
            "name": p.get("name"),
            "current_weight": float(weight_pct or 0.0),
            "shares": shares,
            "avg_cost": _safe_float(p.get("avg_cost")),
            "current_price": _safe_float(p.get("current_price")),
            "entry_date": _coerce_date(p.get("entry_date")),
            "highest_since_entry": _safe_float(p.get("highest_since_entry")),
            "holding_trade_days": _safe_float(p.get("holding_trade_days")),
            "pending_forced_exit": int(p.get("pending_forced_exit") or 0),
            "pending_exit_reason": p.get("pending_exit_reason"),
            "rebuy_cooldown_until": _coerce_date(p.get("rebuy_cooldown_until")),
            "relative_return_vs_index": _safe_float(p.get("relative_return_vs_index")),
            "market_value": float(market_value or 0.0),
        }

    all_symbols = sorted(set(target_map.keys()) | set(current_map.keys()))
    lookback_days = max(
        120,
        int(params["time_stop_days"]) + int(params["score_confirm_days"]) + int(params["bs_fresh_trade_days"]) + 20,
    )
    trade_day_index = trade_day_index_override or _fetch_trade_day_index(conn, asof_date=asof_date, lookback_days=lookback_days)
    asof_trade_day = trade_day_index.get("asof_trade_day") or _to_ymd(asof_date) or _to_ymd(date.today())
    ordered_trade_days = trade_day_index.get("ordered") or []
    idx_map = trade_day_index.get("index") or {}
    recent_trade_days = []
    if ordered_trade_days and asof_trade_day in idx_map:
        end_idx = idx_map[asof_trade_day]
        window = ordered_trade_days[max(0, end_idx - 20): end_idx + 1]
        recent_trade_days = list(reversed(window))
    elif ordered_trade_days:
        recent_trade_days = list(reversed(ordered_trade_days[-21:]))
    else:
        recent_trade_days = [_to_ymd(asof_trade_day)] if _to_ymd(asof_trade_day) else []

    bs_state_map = bs_state_override or (_fetch_latest_bs_signal_state(conn, list(current_map.keys())) if current_map else {})
    market_state_map = market_state_override or (_fetch_latest_market_state(conn, list(all_symbols), asof_date=asof_trade_day) if all_symbols else {})
    score_state_map = score_state_override or (_fetch_recent_score_state(conn, list(current_map.keys()), recent_trade_days) if current_map else {})
    rebuy_cooldown_map = rebuy_cooldown_override or _fetch_rebuy_cooldown_map(conn, asof_trade_day, rebuy_cooldown_days)
    index_daily_change_pct = _fetch_market_index_daily_change(conn, asof_trade_day)
    market_risk_gate_hit = bool(
        params["enable_market_risk_gate"]
        and index_daily_change_pct is not None
        and float(index_daily_change_pct) <= float(params["market_risk_gate_drop_pct"])
    )

    rule_handlers = {
        "BS_REVERSAL": _rule_bs_reversal,
        "HARD_STOP": _rule_hard_stop,
        "LIMIT_DOWN_EXIT": _rule_limit_down_exit,
        "TRAILING_STOP": _rule_trailing_stop,
        "TIME_STOP": _rule_time_stop,
        "SCORE_EXIT": _rule_score_exit,
        "REBALANCE_SELL": _rule_rebalance_sell,
    }

    candidate_sell_orders = []
    candidate_buy_orders = []
    debug_rows = []

    for symbol in all_symbols:
        t_info = target_map.get(symbol, {})
        c_info = current_map.get(symbol, {})
        context = build_position_context(
            symbol=symbol,
            target_info=t_info,
            current_info=c_info,
            total_capital=total_capital,
            asof_trade_day=asof_trade_day,
            trade_day_index=trade_day_index,
            recent_trade_days=recent_trade_days,
            bs_state_map=bs_state_map,
            market_state_map=market_state_map,
            score_state_map=score_state_map,
            rebuy_cooldown_map=rebuy_cooldown_map,
            index_daily_change_pct=index_daily_change_pct,
        )
        context["market_risk_gate_hit"] = 1 if market_risk_gate_hit else 0

        rule_checks = []
        hit = None
        if context.get("has_position"):
            for code in M7_SELL_RULE_PRIORITY:
                handler = rule_handlers.get(code)
                if handler is None:
                    continue
                res = handler(context, params)
                rule_checks.append({"rule": code, "hit": 1 if res else 0})
                if res:
                    hit = res
                    break

        debug_rows.append(debug_rule_hits(symbol, context, rule_checks, hit))

        raw_tw = float(context.get("raw_target_weight") or 0.0)
        cw = float(context.get("current_weight") or 0.0)
        delta_w = round(raw_tw - cw, 4)
        price = float(context.get("current_price") or 0.0)
        name = context.get("name") or symbol

        if hit and context.get("has_position"):
            reason_code = hit["reason_code"]
            forced_exit = bool(hit.get("forced_exit"))
            if forced_exit:
                sell_shares = int(context.get("shares") or 0)
                if sell_shares <= 0:
                    continue
                notional = abs(sell_shares * price) if price > 0 else abs(float(context.get("trade_notional") or 0.0))
                candidate_sell_orders.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "action": "SELL",
                        "price": price,
                        "current_weight": round(cw, 2),
                        "target_weight": 0.0,
                        "delta_weight": round(0.0 - cw, 2),
                        "shares": sell_shares,
                        "notional": round(notional, 2),
                        "status": "SIMULATED",
                        "reason": hit.get("reason"),
                        "reason_code": reason_code,
                        "reason_detail_json": hit.get("reason_detail_json") or {},
                        "sell_signal": "FORCED_EXIT",
                        "forced_exit": 1,
                        "pending_flag": int(hit.get("pending_flag") or 0),
                        "pending_reason": hit.get("pending_reason"),
                        "exec_status": str(hit.get("exec_status") or "NEW"),
                        "protect_window_hit": int(hit.get("protect_window_hit") or 0),
                        "market_risk_gate_hit": int(hit.get("market_risk_gate_hit") or 0),
                        "m4_score": t_info.get("m4_score"),
                    }
                )
                continue

            if reason_code == "REBALANCE_SELL":
                shares = int(context.get("shares") or 0)
                if shares <= 0 or price <= 0:
                    continue
                desired_notional = abs(float(total_capital) * delta_w / 100.0)
                raw_shares = desired_notional / price if price > 0 else 0.0
                # 普通卖出：向上取整到 100 股，且不超过当前持仓
                sell_shares = int(ceil(raw_shares / 100.0) * 100) if raw_shares > 0 else 0
                sell_shares = min(sell_shares, shares)
                if sell_shares <= 0:
                    continue
                sell_notional = sell_shares * price
                if sell_notional < float(params["min_trade_notional"]):
                    continue
                candidate_sell_orders.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "action": "SELL",
                        "price": price,
                        "current_weight": round(cw, 2),
                        "target_weight": round(raw_tw, 2),
                        "delta_weight": round(delta_w, 2),
                        "shares": int(sell_shares),
                        "notional": round(abs(sell_notional), 2),
                        "status": "SIMULATED",
                        "reason": hit.get("reason") or "目标权重下调",
                        "reason_code": "REBALANCE_SELL",
                        "reason_detail_json": hit.get("reason_detail_json") or {},
                        "sell_signal": "REBALANCE",
                        "forced_exit": 0,
                        "pending_flag": 0,
                        "pending_reason": None,
                        "exec_status": "NEW",
                        "protect_window_hit": int(hit.get("protect_window_hit") or 0),
                        "market_risk_gate_hit": int(hit.get("market_risk_gate_hit") or 0),
                        "m4_score": t_info.get("m4_score"),
                    }
                )
                continue

        if delta_w <= 0:
            continue
        if abs(delta_w) < float(params["min_trade_weight"]):
            continue
        if bool(context.get("in_rebuy_cooldown")):
            continue
        if price <= 0:
            continue

        buy_notional = float(total_capital) * delta_w / 100.0
        raw_buy_shares = buy_notional / price
        buy_shares = int(round(raw_buy_shares / 100.0) * 100) if raw_buy_shares > 0 else 0
        if buy_shares <= 0:
            continue
        final_buy_notional = buy_shares * price
        if final_buy_notional < 100:
            continue
        candidate_buy_orders.append(
            {
                "symbol": symbol,
                "name": name,
                "action": "BUY",
                "price": price,
                "current_weight": round(cw, 2),
                "target_weight": round(raw_tw, 2),
                "delta_weight": round(delta_w, 2),
                "shares": int(buy_shares),
                "notional": round(abs(final_buy_notional), 2),
                "status": "SIMULATED",
                "reason": "目标权重提升",
                "reason_code": "REBALANCE_BUY",
                "reason_detail_json": {},
                "sell_signal": "REBALANCE_BUY",
                "forced_exit": 0,
                "pending_flag": 0,
                "pending_reason": None,
                "exec_status": "NEW",
                "protect_window_hit": 0,
                "market_risk_gate_hit": 0,
                "m4_score": t_info.get("m4_score"),
            }
        )

    sell_cash_released = sum(float(o.get("notional") or 0.0) for o in candidate_sell_orders if str(o.get("exec_status") or "NEW").upper() != "PENDING")
    start_cash = max(float(total_capital) - float(current_market_value), 0.0)
    buy_orders = _apply_buy_cash_budget(candidate_buy_orders, start_cash + sell_cash_released)

    candidate_sell_orders.sort(
        key=lambda x: (
            0 if int(x.get("forced_exit") or 0) == 1 else 1,
            0 if str(x.get("exec_status") or "NEW").upper() != "PENDING" else 1,
            -float(x.get("notional") or 0.0),
            x.get("symbol") or "",
        )
    )
    buy_orders.sort(key=lambda x: (-float(x.get("notional") or 0.0), x.get("symbol") or ""))
    orders = candidate_sell_orders + buy_orders

    for idx, order in enumerate(orders, start=1):
        order["order_id"] = f"SIM-{idx:04d}"
        cmd_action = "buy" if order.get("action") == "BUY" else "sell"
        if (order.get("price") or 0) > 0 and (order.get("shares") or 0) > 0 and str(order.get("exec_status") or "NEW").upper() != "PENDING":
            order["cli_cmd"] = f"python sina/live_tracker/run_live_tracker.py {cmd_action} -s {order['symbol']} -p {float(order['price']):.2f} -n {int(order['shares'])}"
        else:
            order["cli_cmd"] = ""

    buy_total = sum(1 for o in orders if o.get("action") == "BUY")
    sell_total = sum(1 for o in orders if o.get("action") == "SELL")
    forced_sell_total = sum(1 for o in orders if o.get("action") == "SELL" and int(o.get("forced_exit") or 0) == 1)

    return {
        "rule_version": M7_RULE_VERSION_V21,
        "asof_trade_day": asof_trade_day,
        "target_count": len(target_map),
        "current_count": len(current_map),
        "orders_total": len(orders),
        "buy_total": buy_total,
        "sell_total": sell_total,
        "forced_sell_total": forced_sell_total,
        "turnover_notional": round(sum(float(o.get("notional") or 0.0) for o in orders), 2),
        "orders": orders,
        "debug_rule_hits": debug_rows,
        "market_index_daily_change_pct": index_daily_change_pct,
        "market_risk_gate_hit": 1 if market_risk_gate_hit else 0,
    }


def _evaluate_m7_rebalance_v1(
    target_allocations,
    current_positions,
    total_capital=100000.0,
    min_trade_weight=1.0,
    conn=None,
    stop_loss_pct=0.07,
):
    """Legacy M7 logic (v1): B/S reversal + hard stop + regular rebalance."""
    total_capital = float(total_capital or 0)
    if total_capital <= 0:
        total_capital = 100000.0

    stop_loss_ratio = _normalize_stop_loss_pct(stop_loss_pct) / 100.0

    target_map = {}
    for t in target_allocations or []:
        symbol = str(t.get("symbol") or "").zfill(6) if t.get("symbol") else ""
        if not symbol:
            continue
        target_map[symbol] = {
            "symbol": symbol,
            "name": t.get("name"),
            "target_weight": float(_safe_float(t.get("weight_pct")) or 0.0),
            "m4_score": _safe_float(t.get("m4_score")),
        }

    current_map = {}
    for p in current_positions or []:
        symbol = str(p.get("symbol") or "").zfill(6) if p.get("symbol") else ""
        if not symbol:
            continue

        market_value = _safe_float(p.get("market_value"))
        weight_pct = _safe_float(p.get("weight_pct"))
        if weight_pct is None and market_value is not None and total_capital > 0:
            weight_pct = market_value / total_capital * 100

        current_map[symbol] = {
            "symbol": symbol,
            "name": p.get("name"),
            "current_weight": float(weight_pct or 0.0),
            "shares": int(p.get("shares") or 0),
            "avg_cost": _safe_float(p.get("avg_cost")),
            "current_price": _safe_float(p.get("current_price")),
        }

    all_symbols = sorted(set(target_map.keys()) | set(current_map.keys()))

    prices = {}
    if conn and all_symbols:
        try:
            placeholders = ",".join([f"'{s}'" for s in all_symbols])
            sql = f"""
                SELECT s.symbol, t.close
                FROM tushare_stock.ods_daily t
                JOIN tushare_stock.dim_stock s ON t.ts_code = s.ts_code
                WHERE s.symbol IN ({placeholders})
                  AND t.trade_date = (SELECT MAX(trade_date) FROM tushare_stock.ods_daily)
            """
            with conn.cursor() as cursor:
                cursor.execute(sql)
                for row in cursor.fetchall():
                    prices[row["symbol"]] = float(row["close"])
        except Exception as e:
            print(f"Failed to fetch prices for M7 rounding: {e}")

    bs_state_map = _fetch_latest_bs_signal_state(conn, list(current_map.keys())) if current_map else {}

    orders = []
    forced_sell_total = 0

    for idx, symbol in enumerate(all_symbols, start=1):
        t_info = target_map.get(symbol, {})
        c_info = current_map.get(symbol, {})

        raw_tw = float(t_info.get("target_weight", 0.0))
        cw = float(c_info.get("current_weight", 0.0))

        forced_exit_reasons = []
        bs_state = bs_state_map.get(symbol) or {}
        if bool(bs_state.get("has_exit_signal")) and c_info:
            forced_exit_reasons.append("B/S反转卖出")

        price = float(prices.get(symbol) or 0.0)
        if price <= 0:
            price = float(c_info.get("current_price") or 0.0)

        avg_cost = _safe_float(c_info.get("avg_cost"))
        if avg_cost and avg_cost > 0 and price > 0 and c_info:
            stop_loss_line = avg_cost * (1.0 - float(stop_loss_ratio or 0.0))
            if price <= stop_loss_line:
                forced_exit_reasons.append(f"硬止损({float(stop_loss_ratio) * 100:.1f}%)")

        is_forced_exit = bool(forced_exit_reasons and c_info)
        tw = 0.0 if is_forced_exit else raw_tw
        delta_w = round(tw - cw, 2)

        if abs(delta_w) < float(min_trade_weight or 0) and not is_forced_exit:
            continue

        notional_diff = total_capital * delta_w / 100.0

        if is_forced_exit:
            shares_delta = -int(c_info.get("shares") or 0)
            final_notional = shares_delta * price if price > 0 else notional_diff
        else:
            shares_delta = 0
            if price > 0:
                raw_shares = notional_diff / price
                shares_delta = int(round(raw_shares / 100.0) * 100)
                final_notional = shares_delta * price
            else:
                final_notional = notional_diff

        if shares_delta == 0 and abs(final_notional) < 100:
            continue

        action = "BUY" if final_notional > 0 else "SELL"
        if action == "SELL" and is_forced_exit:
            reason = " + ".join(forced_exit_reasons)
            forced_sell_total += 1
        else:
            reason = "目标权重提升" if action == "BUY" else "目标权重下调"

        abs_shares = abs(shares_delta)
        abs_amt = abs(final_notional)
        name = t_info.get("name") or c_info.get("name") or symbol
        cmd_action = "buy" if action == "BUY" else "sell"
        cli_cmd = ""
        if price > 0 and abs_shares > 0:
            cli_cmd = f"python sina/live_tracker/run_live_tracker.py {cmd_action} -s {symbol} -p {price:.2f} -n {abs_shares}"

        orders.append(
            {
                "order_id": f"SIM-{idx:04d}",
                "symbol": symbol,
                "name": name,
                "action": action,
                "price": price,
                "current_weight": round(cw, 2),
                "target_weight": round(tw, 2),
                "delta_weight": delta_w,
                "shares": abs_shares,
                "notional": round(abs_amt, 2),
                "status": "SIMULATED",
                "reason": reason,
                "m4_score": t_info.get("m4_score"),
                "reason_code": "FORCED_EXIT" if (action == "SELL" and is_forced_exit) else ("REBALANCE_SELL" if action == "SELL" else "REBALANCE_BUY"),
                "reason_detail_json": {},
                "sell_signal": "FORCED_EXIT" if (action == "SELL" and is_forced_exit) else ("REBALANCE" if action == "SELL" else "REBALANCE_BUY"),
                "forced_exit": 1 if (action == "SELL" and is_forced_exit) else 0,
                "pending_flag": 0,
                "pending_reason": None,
                "exec_status": "NEW",
                "protect_window_hit": 0,
                "market_risk_gate_hit": 0,
                "rule_version": M7_RULE_VERSION_V1,
                "cli_cmd": cli_cmd,
            }
        )

    orders.sort(key=lambda x: (x["action"] != "SELL", -x["notional"]))
    buy_orders = [o for o in orders if o["action"] == "BUY"]
    sell_orders = [o for o in orders if o["action"] == "SELL"]

    return {
        "rule_version": M7_RULE_VERSION_V1,
        "target_count": len(target_map),
        "current_count": len(current_map),
        "orders_total": len(orders),
        "buy_total": len(buy_orders),
        "sell_total": len(sell_orders),
        "forced_sell_total": forced_sell_total,
        "turnover_notional": round(sum(o["notional"] for o in orders), 2),
        "orders": orders,
        "debug_rule_hits": [],
    }


def evaluate_m7_rebalance(
    target_allocations,
    current_positions,
    total_capital=100000.0,
    min_trade_weight=1.0,
    min_trade_notional=5000.0,
    conn=None,
    stop_loss_pct=6.0,
    rule_version=M7_RULE_VERSION_V1,
    asof_date=None,
    bs_fresh_trade_days=3,
    trail_activate_pct=12.0,
    trail_drawdown_pct=4.0,
    time_stop_days=8,
    time_stop_min_return_pct=1.0,
    time_stop_rel_index_pct=-3.0,
    min_hold_protect_days=5,
    enable_market_risk_gate=False,
    market_risk_gate_drop_pct=-2.0,
    claude_floor=45.0,
    score_floor=60.0,
    score_confirm_days=2,
    is_post_close=True,
    rebuy_cooldown_days=5,
    bs_state_override=None,
    market_state_override=None,
    score_state_override=None,
    trade_day_index_override=None,
    rebuy_cooldown_override=None,
):
    """Generate simulated rebalance orders from target vs current weights."""
    rv = str(rule_version or M7_RULE_VERSION_V1).strip()
    if rv == M7_RULE_VERSION_V21:
        return _evaluate_m7_rebalance_v21(
            target_allocations=target_allocations,
            current_positions=current_positions,
            total_capital=total_capital,
            min_trade_weight=min_trade_weight,
            min_trade_notional=min_trade_notional,
            conn=conn,
            stop_loss_pct=stop_loss_pct,
            asof_date=asof_date,
            bs_fresh_trade_days=bs_fresh_trade_days,
            trail_activate_pct=trail_activate_pct,
            trail_drawdown_pct=trail_drawdown_pct,
            time_stop_days=time_stop_days,
            time_stop_min_return_pct=time_stop_min_return_pct,
            time_stop_rel_index_pct=time_stop_rel_index_pct,
            min_hold_protect_days=min_hold_protect_days,
            enable_market_risk_gate=enable_market_risk_gate,
            market_risk_gate_drop_pct=market_risk_gate_drop_pct,
            claude_floor=claude_floor,
            score_floor=score_floor,
            score_confirm_days=score_confirm_days,
            is_post_close=is_post_close,
            rebuy_cooldown_days=rebuy_cooldown_days,
            bs_state_override=bs_state_override,
            market_state_override=market_state_override,
            score_state_override=score_state_override,
            trade_day_index_override=trade_day_index_override,
            rebuy_cooldown_override=rebuy_cooldown_override,
        )
    return _evaluate_m7_rebalance_v1(
        target_allocations=target_allocations,
        current_positions=current_positions,
        total_capital=total_capital,
        min_trade_weight=min_trade_weight,
        conn=conn,
        stop_loss_pct=stop_loss_pct,
    )


def evaluate_m6_nav(rows, cost_bps=20, slippage_bps=10, max_positions=5):
    """Build gross/net NAV series with transaction cost & slippage."""
    eligible = [r for r in rows if int(r.get("is_eligible") or 0) == 1]
    by_date = {}
    for r in eligible:
        d = str(r.get("event_date") or "")
        if not d:
            continue
        by_date.setdefault(d, []).append(r)

    dates = sorted(by_date.keys())
    nav_points = []
    gross_nav = 1.0
    net_nav = 1.0

    # roundtrip cost approximation: buy + sell
    rt_cost = (float(cost_bps or 0) + float(slippage_bps or 0)) * 2 / 10000.0

    for d in dates:
        alloc = evaluate_m4_allocation(by_date[d], max_positions=max_positions)
        picks = alloc.get("allocations") or []

        if not picks:
            gross_ret = 0.0
        else:
            gross_ret = sum((float(x.get("ret_10") or 0.0) * float(x.get("weight_pct") or 0.0) / 100.0) for x in picks)

        net_ret = gross_ret - rt_cost if picks else gross_ret

        gross_nav *= (1.0 + gross_ret)
        net_nav *= (1.0 + net_ret)

        nav_points.append(
            {
                "event_date": d,
                "pick_count": len(picks),
                "gross_ret_pct": round(gross_ret * 100, 2),
                "net_ret_pct": round(net_ret * 100, 2),
                "gross_nav": round(gross_nav, 4),
                "net_nav": round(net_nav, 4),
            }
        )

    gross_final = round((gross_nav - 1.0) * 100, 2)
    net_final = round((net_nav - 1.0) * 100, 2)
    max_dd = _calc_max_drawdown(nav_points)

    return {
        "eligible_total": len(eligible),
        "dates_total": len(dates),
        "cost_bps": float(cost_bps or 0),
        "slippage_bps": float(slippage_bps or 0),
        "gross_final_ret_pct": gross_final,
        "net_final_ret_pct": net_final,
        "max_drawdown_pct": max_dd,
        "nav_points": nav_points,
    }

