"""PIT lineage binding tests (v5.5.1 — no database required).

The package manifest must carry REAL per-family provenance: deterministic
content SHAs, query/parameter/schema SHAs, the data's own date extent, and
the canonical PIT contract SHA — never placeholders.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ops.build_daily_alpha_signal_package import (  # noqa: E402
    _df_content_sha256,
    _family_lineage,
    _pit_contract_sha,
)


def test_content_sha_row_order_invariant():
    df = pd.DataFrame({"symbol": ["600000", "600001", "600002"],
                       "value": [1.0, 2.0, 3.0]})
    shuffled = df.sample(frac=1.0, random_state=7)
    assert _df_content_sha256(df) == _df_content_sha256(shuffled)


def test_content_sha_changes_with_data():
    a = pd.DataFrame({"symbol": ["600000"], "value": [1.0]})
    b = pd.DataFrame({"symbol": ["600000"], "value": [1.5]})
    c = pd.DataFrame({"symbol": ["600000"]})
    hashes = {_df_content_sha256(x) for x in (a, b, c)}
    assert len(hashes) == 3, "different data must not collide"


def test_content_sha_nan_stable():
    df1 = pd.DataFrame({"a": [1.0, None], "b": ["x", "y"]})
    df2 = pd.DataFrame({"a": [1.0, pd.NA], "b": ["x", "y"]})
    assert _df_content_sha256(df1) == _df_content_sha256(df2)


def test_family_lineage_carries_all_contract_fields():
    df = pd.DataFrame({"trade_date": ["2026-08-03", "2026-08-04"],
                       "symbol": ["600000", "600001"]})
    rec = _family_lineage("market", df, "SELECT 1", (20260804,),
                          "dwd_stock_daily_standard",
                          "live_mysql:dwd_stock_daily_standard<=20260804",
                          date_col="trade_date")
    for field in ("family", "provider", "query_sha256", "parameter_sha256",
                  "schema_sha256", "content_sha256", "row_count",
                  "min_available_at", "max_available_at", "retrieved_at",
                  "snapshot_identity"):
        assert field in rec, f"lineage missing {field}"
    assert rec["family"] == "market"
    assert rec["row_count"] == 2
    assert rec["min_available_at"] == "2026-08-03"
    assert rec["max_available_at"] == "2026-08-04"
    assert len(rec["content_sha256"]) == 64
    assert rec["provider"] == "dwd_stock_daily_standard"


def test_query_change_alters_query_sha_only():
    df = pd.DataFrame({"trade_date": ["2026-08-04"]})
    base = _family_lineage("labels", df, "SELECT is_st", (20260804,),
                           "dwd_stock_label_daily", "live")
    changed = _family_lineage("labels", df, "SELECT is_st, is_new",
                              (20260804,), "dwd_stock_label_daily", "live")
    assert base["query_sha256"] != changed["query_sha256"]
    assert base["content_sha256"] == changed["content_sha256"]
    assert base["parameter_sha256"] == changed["parameter_sha256"]


def test_pit_contract_sha_binds_real_contract():
    sha = _pit_contract_sha()
    assert len(sha) == 64
    contract = PROJECT_ROOT / "config" / "pit_semantics" / \
        "ashare_pit_semantics_v1.yaml"
    assert sha == hashlib.sha256(contract.read_bytes()).hexdigest()
