"""Alpha-rebuild challenger integrity tests (no database required).

Covers the pre-registration contract enforced on CI (freeze-integrity):
  - all challenger YAMLs parse and carry the required pre-registration fields
  - challenger files match the manifest's pre-registration SHA256 (no drift)
  - score transforms (winsorization / eligibility floor / volatility penalty)
    behave as pre-registered
  - style exposure constraints enforce limits (redistribution + cash-hold)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHALLENGER_DIR = PROJECT_ROOT / "config" / "alpha_challengers"
MANIFEST_PATH = PROJECT_ROOT / "config" / "experiments" / "alpha_rebuild_202608.yaml"

REQUIRED_FIELDS = [
    "schema_version", "challenger_id", "experiment_id", "hypothesis_id",
    "created_at", "parameter_source", "data_snapshot_sha", "selection_window",
    "untouched_evaluation_window", "true_blind_start", "base_strategy",
    "hypothesis",
]


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_all_challengers_parse_and_carry_required_fields():
    manifest = _manifest()
    for challenger_id in sorted(manifest["pre_registration_shas"]):
        path = CHALLENGER_DIR / f"{challenger_id}.yaml"
        assert path.exists(), f"{challenger_id} config missing"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        assert not missing, f"{challenger_id} missing fields: {missing}"
        assert data["challenger_id"] == challenger_id
        if challenger_id != "b_sleeve_independent":
            assert "factor_weights" in data, f"{challenger_id} missing factor_weights"
            assert "factor_signs" in data, f"{challenger_id} missing factor_signs"
            assert "execution" in data, f"{challenger_id} missing execution"


def test_challenger_files_match_preregistration_shas():
    manifest = _manifest()
    for challenger_id, expected in manifest["pre_registration_shas"].items():
        path = CHALLENGER_DIR / f"{challenger_id}.yaml"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, (
            f"{challenger_id} DRIFTED from pre-registration sha — "
            "editing a pre-registered challenger is forbidden without "
            "re-registering (update the manifest sha deliberately)"
        )


def test_manifest_windows_and_holdout_policy():
    manifest = _manifest()
    assert "historical_holdout" in manifest["evaluation_windows"]
    assert "historical_holdout" in manifest["selection_prohibited_on"]
    assert manifest["holdout_usage"] == "REPORT_ONLY_SHOWN_NEVER_SELECTED"
    blind = manifest["evaluation_windows"]["true_forward_blind"]
    # v5.5.1 prestart (2026-08-05): start withdrawn — NOT_STARTED until
    # the correctness fixes merge and the start is re-declared.
    assert blind["start"] is None
    assert blind["end"] is None


def test_pipeline_rejects_drift_fail_closed():
    """A modified challenger config must be BLOCKED by the pipeline loader."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.research.run_vls_oos_validation import (
        _load_challenger_config, _verify_challenger_preregistration,
    )
    cfg = _load_challenger_config(CHALLENGER_DIR / "f1_no_value.yaml")
    with pytest.raises(RuntimeError, match="DRIFTED"):
        # Pass a temp copy with a trailing comment -> sha differs.
        tmp = PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers" \
            / ".drift_test.yaml"
        tmp.write_text(
            (CHALLENGER_DIR / "f1_no_value.yaml").read_text(encoding="utf-8")
            + "\n# drift\n", encoding="utf-8")
        try:
            _verify_challenger_preregistration(cfg, tmp)
        finally:
            tmp.unlink(missing_ok=True)


def test_winsorization_clips_per_date():
    sys_path = [str(PROJECT_ROOT)]
    import sys
    for p in sys_path:
        if p not in sys.path:
            sys.path.insert(0, p)
    from scripts.research.build_formal_scores import _apply_winsorization
    rng = np.random.RandomState(0)
    panel = pd.DataFrame({
        "trade_date": ["2024-01-02"] * 20 + ["2024-01-03"] * 20,
        "symbol": list(range(20)) * 2,
        "liquidity": rng.uniform(0, 1, 40),
    })
    out = _apply_winsorization(panel, {
        "liquidity_winsorization": {"method": "clip",
                                    "lower_percentile": 10.0,
                                    "upper_percentile": 90.0}})
    for day in panel["trade_date"].unique():
        sub = out[out["trade_date"] == day]["liquidity"]
        orig = panel[panel["trade_date"] == day]["liquidity"]
        assert sub.min() >= orig.quantile(0.10) - 1e-12
        assert sub.max() <= orig.quantile(0.90) + 1e-12


def test_volatility_penalty_formula():
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.research.build_formal_scores import _apply_risk_penalty
    panel = pd.DataFrame({"volatility": [0.1, 0.3, 0.5]})
    score = pd.Series([10.0, 10.0, 10.0])
    out = _apply_risk_penalty(score, panel, {
        "volatility_penalty": {"lambda_idvol": 0.15, "beta_downside": 0.10}})
    expected = 10.0 - 0.25 * panel["volatility"]
    np.testing.assert_allclose(out, expected)


def test_exposure_constraints_redistribution_and_cash():
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.research.constrained_weights import (
        PortfolioConstraints, compute_style_exposures, enforce_exposure_constraints,
    )
    symbols = [f"S{i:03d}" for i in range(10)]
    weights = pd.Series([0.25, 0.15, 0.12, 0.10, 0.10, 0.10, 0.08, 0.05, 0.03, 0.02],
                        index=symbols)

    # Mixed signs -> redistribution path, mass conserved.
    mixed = pd.DataFrame({
        "beta": np.zeros(10), "liquidity": np.zeros(10),
        "size": [0.8, 0.7, 0.6, 0.5, 0.1, -0.1, -0.2, -0.4, -0.5, -0.6],
    }, index=symbols)
    c = PortfolioConstraints(single_cap=0.25, target_gross_exposure=1.0,
                             beta_tolerance=0.5, max_size_exposure=0.15,
                             max_liquidity_exposure=0.5)
    adj = enforce_exposure_constraints(weights, mixed, c)
    ex = compute_style_exposures(adj, mixed)
    assert abs(ex["size"]) <= 0.15 + 1e-9
    assert abs(adj.sum() - weights.sum()) < 1e-6

    # All-positive loadings -> cash-hold path, constraint still met.
    all_pos = pd.DataFrame({
        "beta": np.zeros(10), "liquidity": np.zeros(10),
        "size": [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05, 0.02],
    }, index=symbols)
    adj2 = enforce_exposure_constraints(weights, all_pos, c)
    ex2 = compute_style_exposures(adj2, all_pos)
    assert abs(ex2["size"]) <= 0.15 + 1e-9
    assert adj2.sum() < weights.sum()  # cash held
