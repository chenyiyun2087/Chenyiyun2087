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
