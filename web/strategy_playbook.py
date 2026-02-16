from math import ceil

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


def _calc_bucket_stats(rows):
    out = {"count": len(rows)}
    for h in (3, 5, 10):
        rets = [_safe_float(r.get(f"ret_{h}")) for r in rows]
        rets = [v for v in rets if v is not None]
        hits = [_safe_float(r.get(f"hit_{h}_10pct")) for r in rows]
        hits = [v for v in hits if v is not None]
        out[f"avg_ret_{h}"] = round(sum(rets) / len(rets) * 100, 2) if rets else None
        out[f"hit_{h}"] = round(sum(hits) / len(hits) * 100, 2) if hits else None
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
