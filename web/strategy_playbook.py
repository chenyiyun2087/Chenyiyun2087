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
    "quadrant_opt_cut": 60.0,
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
