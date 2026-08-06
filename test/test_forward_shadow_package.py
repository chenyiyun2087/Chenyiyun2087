"""Forward Shadow Engine v2 package integration tests (v5.5).

Pure-core behavior of the daily Signal Package builder:
  - every candidate runs its OWN score pipeline (no shared scores)
  - different strategies produce different target portfolios
  - TopN is honored (never silently degraded)
  - R2 overlay scales weights without changing selection
  - universe eligibility is respected (non-tradeable names excluded)
  - missing inputs block the package (fail-closed)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    SignalPackageBlocked,
    build_target_portfolios,
    compute_candidate_scores,
    compute_crowding_state,
    compute_raw_factors,
    r2_position_multiplier,
)

RUNTIME = yaml.safe_load(
    (PROJECT_ROOT / "config" / "strategy_runtime" /
     "forward_shadow_v2.yaml").read_text(encoding="utf-8"))


def _raw_frame(n_syms: int = 30, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    symbols = [f"{600000 + i:06d}" for i in range(n_syms)]
    industries = ["银行", "医药", "电子", "食品饮料"]
    return pd.DataFrame({
        "symbol": symbols,
        "trade_date": ["2026-08-05"] * n_syms,
        "size_raw": rng.uniform(1e9, 5e10, n_syms),
        "liquidity_raw": rng.uniform(1e-9, 1e-7, n_syms),
        "momentum_raw": rng.uniform(-0.3, 0.3, n_syms),
        "value_raw": -rng.uniform(0.5, 5.0, n_syms),
        "beta_raw": rng.uniform(0.5, 1.5, n_syms),
        "industry": [industries[i % 4] for i in range(n_syms)],
    })


def _universe(n_syms: int = 30) -> pd.DataFrame:
    symbols = [f"{600000 + i:06d}" for i in range(n_syms)]
    return pd.DataFrame({
        "trade_date": ["2026-08-05"] * n_syms,
        "symbol": symbols,
        "is_listed": [1] * n_syms,
        "is_st": [0] * n_syms,
        "is_suspended": [0] * n_syms,
        "limit_status": ["NORMAL"] * n_syms,
        "security_status_transition": ["NORMAL"] * n_syms,
        "tradeable": [True] * n_syms,
    })


def test_candidates_produce_different_scores():
    """C0 (value factor) and C1 (no value) must NOT rank identically."""
    raw = _raw_frame()
    c0 = compute_candidate_scores(raw, RUNTIME["candidates"]["C0"])
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    top_c0 = set(c0.nlargest(10, "score")["symbol"])
    top_c1 = set(c1.nlargest(10, "score")["symbol"])
    assert top_c0 != top_c1, "C0 and C1 rank the same — factor pipelines overlap"


def test_each_candidate_own_score_no_shared_frame():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    c3 = compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])
    assert "residual_score" in c3.columns and "residual_score" not in c1.columns
    rnd = compute_candidate_scores(raw, RUNTIME["candidates"]["RND"])
    assert "random_score" in rnd.columns
    # C1 vs RND selection must differ (random is not a copy of F1).
    assert set(c1.nlargest(10, "score")["symbol"]) != \
        set(rnd.nlargest(10, "score")["symbol"])


def test_top_n_honored_not_degraded():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    portfolios = build_target_portfolios(
        {"C1": c1}, _universe(), RUNTIME)
    assert len(portfolios["C1"]) == 10, "Top10 must not be degraded"
    assert portfolios["C1"]["target_weight"].sum() == pytest.approx(1.0, abs=1e-9)


def test_universe_eligibility_respected():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    # Block the top-10 ranked names from trading.
    top10 = set(c1.nlargest(10, "score")["symbol"])
    uni = _universe()
    uni.loc[uni["symbol"].isin(top10), "tradeable"] = False
    portfolios = build_target_portfolios({"C1": c1}, uni, RUNTIME)
    picked = set(portfolios["C1"]["symbol"])
    assert not picked.intersection(top10), (
        "non-tradeable names must never be selected")
    # The next-best tradeable names are picked instead (10 names).
    assert len(picked) == 10


def test_r2_overlay_scales_weights_not_selection():
    raw = _raw_frame()
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    state = {"top5_turnover_concentration": 0.32,
             "small_vs_large_20d_rs": 1.30}
    mult = r2_position_multiplier(state)
    assert mult == 0.50
    base = build_target_portfolios({"C2": c1}, _universe(), RUNTIME,
                                   crowding_state=None)["C2"]
    scaled = build_target_portfolios({"C2": c1}, _universe(), RUNTIME,
                                     crowding_state=state)["C2"]
    # Same selection, scaled weights.
    assert set(base["symbol"]) == set(scaled["symbol"])
    assert scaled["target_weight"].max() == pytest.approx(
        base["target_weight"].max() * 0.50, abs=1e-9)


def test_missing_style_input_blocks_c3():
    raw = _raw_frame().drop(columns=["beta_raw"])
    with pytest.raises(SignalPackageBlocked, match="SIGNAL_PACKAGE_BLOCKED"):
        compute_candidate_scores(raw, RUNTIME["candidates"]["C3"])


def test_missing_factor_never_imputed_zero():
    # v5.5.1: a NaN on ANY required factor -> score NaN (drops out of
    # selection), NEVER imputed as 0.0.
    raw = _raw_frame()
    victim = raw["symbol"].iloc[5]
    raw.loc[raw["symbol"] == victim, "size_raw"] = np.nan
    c1 = compute_candidate_scores(raw, RUNTIME["candidates"]["C1"])
    row = c1[c1["symbol"] == victim].iloc[0]
    assert pd.isna(row["score"])
    assert pd.isna(row["score"]) and not np.isclose(row["score"], 0.0)


def test_max_missing_factor_pct_contract_registered():
    # The v5.5.1 missing-factor gate threshold is part of the runtime
    # contract — every candidate must carry it pre-registered.
    for cid in ("C0", "C1", "C2", "C3", "RND"):
        assert "max_missing_factor_pct" in RUNTIME["candidates"][cid], cid


def test_crowding_state_from_bars():
    dates = pd.to_datetime(pd.bdate_range("2026-07-01", periods=30))
    rows = []
    rng = np.random.default_rng(5)
    for d in dates:
        for i in range(20):
            rows.append({
                "trade_date": d.date().isoformat(),
                "symbol": f"{600000 + i:06d}",
                "adj_close": 10.0 + i / 100.0,
                "amount": float(rng.uniform(1e6, 5e6)),
                "circ_mv": float(rng.uniform(1e9, 5e10)),
            })
    bars = pd.DataFrame(rows)
    day = compute_raw_factors(bars, dates[-1].date().isoformat())
    state = compute_crowding_state(day)
    assert state["top5_turnover_concentration"] is not None
    assert state["top5_turnover_concentration"] > 0


def test_dry_run_seals_nothing(monkeypatch, tmp_path, capsys):
    """v5.5.1 --dry-run contract: every stage runs with real data and the
    in-memory SHA preview is produced, but NOTHING formal is created — no
    package dir, no SEALED manifest, no execution state change."""
    import hashlib

    from scripts.ops import build_daily_alpha_signal_package as pkg

    n_syms, n_days = 30, 26
    symbols = [f"{600000 + i:06d}" for i in range(n_syms)]
    ts_codes = [f"{s}.SH" for s in symbols]
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2026-07-01", "2026-08-05")
    rows = []
    for i, s in enumerate(symbols):
        rets = rng.normal(0.0005, 0.01, n_days)
        close = 10.0 * np.exp(np.cumsum(rets))
        for j, d in enumerate(dates):
            rows.append({
                # production shape: ts_code only (no symbol column)
                "trade_date": d.date().isoformat(), "ts_code": f"{s}.SH",
                "adj_close": float(close[j]),
                "amount": float(1e8 + i * 1e6 + j * 1e4),
                "circ_mv": float(5e9 + i * 1e9),
            })
    bars = pd.DataFrame(rows)
    mcap = pd.DataFrame({"ts_code": ts_codes,
                         "circ_mv": [5e9 + i * 1e9 for i in range(n_syms)]})
    basic = pd.DataFrame({"ts_code": ts_codes,
                          "pb": [1.0 + i * 0.1 for i in range(n_syms)],
                          "turnover_rate": [1.0] * n_syms})
    industry = pd.DataFrame({"ts_code": ts_codes,
                             "industry_name": [f"ind{i % 5}" for i in range(n_syms)]})
    labels = pd.DataFrame({"ts_code": ts_codes,
                           "is_st": [0] * n_syms,
                           "is_new": [0] * n_syms,
                           "limit_type": [10] * n_syms,
                           "industry": [f"ind{i % 5}" for i in range(n_syms)]})
    lineage = [{"family": fam,
                "content_sha256": hashlib.sha256(fam.encode()).hexdigest()}
               for fam in ("market", "market_cap", "basic_financial",
                           "industry_scd", "labels", "trade_calendar",
                           "adjustment", "benchmark_index",
                           "status_scd", "dim_stock")]
    basic = basic.assign(ann_date=20260805)
    industry = industry.assign(effective_date=20260805)
    adjustment = pd.DataFrame({"trade_date": [20260805],
                               "ts_code": ts_codes[0],
                               "adj_factor": [1.0]})
    benchmark = pd.DataFrame({"trade_date": [20260805],
                              "ts_code": ["000300.SH"], "close": [4000.0]})
    status_scd = pd.DataFrame({"ts_code": [], "status": [],
                               "effective_date": [], "expire_date": []})
    dim_stock = pd.DataFrame({"ts_code": ts_codes,
                              "list_date": [20200101] * n_syms,
                              "delist_date": [None] * n_syms})

    monkeypatch.setattr(pkg, "fetch_production_inputs", lambda d: {
        "data_quality": {"rows": len(bars)},
        "lineage": lineage,
        "bars": bars, "mcap": mcap, "basic": basic,
        "industry": industry, "labels": labels,
        "adjustment": adjustment, "benchmark": benchmark,
        "status_scd": status_scd, "dim_stock": dim_stock,
        "snapshot_identity": {"consistent_snapshot": True,
                              "server_uuid": "test-uuid"},
    })
    monkeypatch.setattr(pkg, "_next_open_day", lambda d: "2026-08-06")
    monkeypatch.setattr(pkg, "PACKAGES_ROOT", tmp_path)

    rc = pkg.run_package("2026-08-05", dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "DRY_RUN_PASS"' in out
    assert '"would_seal": true' in out
    assert '"package_sha256_preview"' in out
    # the dry run must leave the packages zone completely untouched
    assert list(tmp_path.iterdir()) == [], \
        f"dry-run wrote into the packages zone: {list(tmp_path.iterdir())}"
    assert not (tmp_path / "2026-08-05").exists()


def test_reseal_existing_sealed_package_is_idempotent_noop(
        monkeypatch, tmp_path, capsys):
    """v5.5.3 (2026-08-06): a retried seal job whose subprocess already
    sealed (but whose in-process verifier failed on a tool bug) must NOT
    dead-end on PackageSealedError.  With a SEALED manifest in place,
    run_package reports already_sealed, exits 0, never touches inputs or
    the package, and lets the artifact verifier re-prove the contract."""
    import json as _json

    from scripts.ops import build_daily_alpha_signal_package as pkg

    pkg_dir = tmp_path / "2026-08-05"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "signal_package_manifest.json").write_text(_json.dumps({
        "package_status": "SEALED", "signal_date": "2026-08-05",
        "execution_date": "2026-08-06", "revision": 1,
    }), encoding="utf-8")
    before = (pkg_dir / "signal_package_manifest.json").read_bytes()

    def _explode(d):
        raise AssertionError("fetch_production_inputs must not run on "
                             "an idempotent re-seal")

    monkeypatch.setattr(pkg, "fetch_production_inputs", _explode)
    monkeypatch.setattr(pkg, "PACKAGES_ROOT", tmp_path)

    rc = pkg.run_package("2026-08-05")
    assert rc == 0
    out = capsys.readouterr().out
    assert '"already_sealed": true' in out
    assert '"revision": 1' in out
    # the SEALED package is byte-identical — no rewrite, no revision bump
    assert (pkg_dir / "signal_package_manifest.json").read_bytes() == before
    assert not (tmp_path / "2026-08-05" / "revision_2").exists()


def test_reseal_non_sealed_dir_still_builds(monkeypatch, tmp_path):
    """An existing dir WITHOUT a SEALED manifest (e.g. a defect/partial
    write) is not a no-op — the historical build path must run and the
    package write still fails closed on PackageSealedError."""
    import json as _json

    from scripts.ops import build_daily_alpha_signal_package as pkg

    pkg_dir = tmp_path / "2026-08-05"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "signal_package_manifest.json").write_text(
        _json.dumps({"package_status": "KNOWN_DEFECT"}), encoding="utf-8")

    called = []

    def _fake_fetch(d):
        called.append(d)
        raise SignalPackageBlocked("boom")

    monkeypatch.setattr(pkg, "fetch_production_inputs", _fake_fetch)
    monkeypatch.setattr(pkg, "PACKAGES_ROOT", tmp_path)

    with pytest.raises(SignalPackageBlocked, match="boom"):
        pkg.run_package("2026-08-05")
    assert called == ["2026-08-05"]
