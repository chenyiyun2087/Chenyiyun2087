#!/usr/bin/env python3
"""Build an immutable formal-input package from one READY central v2 snapshot.

The command is read-only with respect to databases and fail-closed. It writes
to ``<output>.building`` first and promotes the directory only when the shared
formal readiness preflight returns ``READY_FOR_FORMAL_RUN``.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.formal_contract import FORMAL_STRATEGIES  # noqa: E402
from scoreRank.core.db_config import require_sqlalchemy_url  # noqa: E402
from scripts.research.formal_readiness_preflight import (  # noqa: E402
    DEFAULT_CONFIG,
    evaluate_package,
)
from scripts.research_full_pool_liquidity_strategies import (  # noqa: E402
    add_dynamic_factor_score,
    add_forward_returns,
    add_liquidity_derived_features,
    load_scores,
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_sql(engine: Any, sql: str, **params: Any) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine, params=params)


def _date_column(frame: pd.DataFrame, column: str) -> None:
    frame[column] = pd.to_datetime(
        frame[column].astype(str), format="%Y%m%d", errors="coerce"
    ).dt.strftime("%Y-%m-%d")


def _snapshot_metadata(engine: Any, snapshot_id: str) -> dict[str, Any]:
    frame = _read_sql(
        engine,
        """
        SELECT snapshot_id, start_date, end_date, status, calendar_source,
               adjustment_anchor_date, source_manifest_sha256, created_at, completed_at
        FROM tushare_stock.meta_formal_data_snapshot_v2
        WHERE snapshot_id=:snapshot_id
        """,
        snapshot_id=snapshot_id,
    )
    if len(frame) != 1:
        raise RuntimeError(f"formal_snapshot_not_found:{snapshot_id}")
    row = frame.iloc[0].to_dict()
    if row["status"] != "READY":
        raise RuntimeError(f"formal_snapshot_not_ready:{snapshot_id}:{row['status']}")
    return row


def _load_central_frames(
    engine: Any, snapshot_id: str, start_date: int, end_date: int
) -> dict[str, pd.DataFrame]:
    calendar = _read_sql(
        engine,
        """
        SELECT cal_date, exchange, is_open,
               'tushare_stock.dim_trade_cal' AS source,
               STR_TO_DATE(CONCAT(cal_date, '000000'), '%%Y%%m%%d%%H%%i%%s') AS available_at
        FROM tushare_stock.dim_trade_cal
        WHERE exchange='SSE' AND cal_date BETWEEN :start_date AND :end_date
        ORDER BY cal_date
        """,
        start_date=start_date,
        end_date=end_date,
    )
    market = _read_sql(
        engine,
        """
        SELECT trade_date, ts_code, `open`, high, low, `close`, pre_close,
               volume_hands, volume_shares, amount_cny, adj_factor,
               adj_open, adj_high, adj_low, adj_close,
               adjustment_anchor_date, circ_mv_cny, adv20_amount_cny,
               available_at, source_complete
        FROM tushare_stock.dwd_equity_daily_bar_v2
        WHERE snapshot_id=:snapshot_id
        ORDER BY trade_date, ts_code
        """,
        snapshot_id=snapshot_id,
    )
    lifecycle = _read_sql(
        engine,
        """
        SELECT trade_date, ts_code, listing_status, security_name, st_status,
               is_listed, is_delisted, is_suspended, market_board AS board, limit_ratio,
               up_limit, down_limit, can_buy, can_sell, reason_codes_json AS reason_codes,
               available_at, source_complete
        FROM tushare_stock.dwd_security_lifecycle_daily_v2
        WHERE snapshot_id=:snapshot_id
        ORDER BY trade_date, ts_code
        """,
        snapshot_id=snapshot_id,
    )
    actions = _read_sql(
        engine,
        """
        SELECT event_id, parent_source_event_id, ts_code, event_type AS action_type,
               announcement_date, implementation_announcement_date, record_date,
               ex_date, effective_date, pay_date, cash_per_share, stock_ratio,
               rights_ratio, rights_price, split_ratio, settlement_price,
               new_ts_code, available_at, source_name, source_record_id,
               source_url, source_payload_sha256 AS event_hash,
               source_complete, source_reason
        FROM tushare_stock.dwd_corporate_action_event_v2
        WHERE snapshot_id=:snapshot_id
        ORDER BY effective_date, ts_code, event_id
        """,
        snapshot_id=snapshot_id,
    )
    return {
        "calendar": calendar,
        "market": market,
        "lifecycle": lifecycle,
        "actions": actions,
    }


def _prepare_scores(
    engine: Any,
    market: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    scores = load_scores(
        engine,
        start_date=start_date,
        end_date=end_date,
        min_pool_size=0,
        require_verified_lineage=True,
    )
    if scores.empty:
        raise RuntimeError("formal_score_source_empty")
    prices = market.copy()
    prices["trade_date"] = pd.to_datetime(
        prices["trade_date"].astype(str), format="%Y%m%d"
    ).dt.date
    prices["symbol"] = prices["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
    prices = prices.rename(columns={"amount_cny": "amount"})
    scores = add_liquidity_derived_features(scores, prices)
    scores = add_forward_returns(scores, prices, hold_days=10)
    scores, _ = add_dynamic_factor_score(scores, lookback_dates=20, top_n=5)

    # These are the ranking paths actually consumed by the governed strategies.
    # Execution-safe and strict-precommit differ at execution/governor time,
    # not by inventing a new cross-sectional alpha score.
    score_columns = {
        FORMAL_STRATEGIES[0]: "liquidity_detail_score",
        FORMAL_STRATEGIES[1]: "dynamic_factor_score",
        FORMAL_STRATEGIES[2]: "liquidity_detail_score",
        FORMAL_STRATEGIES[3]: "liquidity_detail_score",
        FORMAL_STRATEGIES[4]: "liquidity_detail_score",
    }
    rows: list[pd.DataFrame] = []
    forbidden = {
        "forward_ret",
        "forward_entry_open",
        "forward_exit_close",
        "entry_date_for_label",
        "exit_date_for_label",
    }
    base_columns = [column for column in scores.columns if column not in forbidden]
    for strategy, column in score_columns.items():
        if column not in scores:
            raise RuntimeError(f"formal_score_column_missing:{column}")
        scoped = scores[base_columns].copy()
        scoped["source_score"] = scoped["score"]
        scoped["score"] = pd.to_numeric(scoped[column], errors="coerce")
        scoped["strategy"] = strategy
        scoped["score_path"] = column
        scoped["available_at"] = (
            pd.to_datetime(scoped["trade_date"].astype(str))
            .dt.strftime("%Y-%m-%dT15:30:00+08:00")
        )
        rows.append(scoped)
    result = pd.concat(rows, ignore_index=True)
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.strftime("%Y-%m-%d")
    return result.sort_values(["trade_date", "strategy", "symbol"])


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def build(engine: Any, *, snapshot_id: str, output: Path) -> dict[str, Any]:
    metadata = _snapshot_metadata(engine, snapshot_id)
    start_date, end_date = int(metadata["start_date"]), int(metadata["end_date"])
    frames = _load_central_frames(engine, snapshot_id, start_date, end_date)
    if any(frame.empty for key, frame in frames.items() if key != "actions"):
        raise RuntimeError("formal_snapshot_contains_empty_required_dataset")

    building = output.with_name(output.name + ".building")
    if output.exists() or building.exists():
        raise FileExistsError(f"formal_package_target_exists:{output}")
    building.mkdir(parents=True)
    try:
        calendar = frames["calendar"].copy()
        market = frames["market"].copy()
        lifecycle = frames["lifecycle"].copy()
        actions = frames["actions"].copy()
        for frame, column in (
            (calendar, "cal_date"),
            (market, "trade_date"),
            (lifecycle, "trade_date"),
        ):
            _date_column(frame, column)
        for column in (
            "announcement_date",
            "implementation_announcement_date",
            "record_date",
            "ex_date",
            "effective_date",
            "pay_date",
        ):
            if column in actions:
                actions[column] = pd.to_datetime(
                    actions[column].astype("Int64").astype(str),
                    format="%Y%m%d",
                    errors="coerce",
                ).dt.strftime("%Y-%m-%d")

        market["symbol"] = market["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
        lifecycle["symbol"] = (
            lifecycle["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
        )
        actions["symbol"] = (
            actions["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
        )
        actions["source_event_id"] = actions["source_record_id"]
        actions["as_of_timestamp"] = actions["available_at"]
        universe = lifecycle[
            [
                "trade_date",
                "symbol",
                "can_buy",
                "can_sell",
                "is_listed",
                "is_suspended",
                "reason_codes",
                "available_at",
            ]
        ].copy()
        universe["is_tradable"] = (
            pd.to_numeric(universe["can_buy"], errors="coerce").eq(1)
            & pd.to_numeric(universe["can_sell"], errors="coerce").eq(1)
        ).astype(int)
        scores = _prepare_scores(
            engine,
            frames["market"],
            start_date=str(start_date),
            end_date=str(end_date),
        )
        prices = market[
            [
                "trade_date", "symbol", "open", "high", "low", "close",
                "pre_close", "volume_hands", "volume_shares", "amount_cny",
                "adj_open", "adj_high", "adj_low", "adj_close", "circ_mv_cny",
                "adv20_amount_cny", "available_at",
            ]
        ]
        prices = prices.assign(
            amount=market["amount_cny"],
            raw_open=market["open"],
            raw_high=market["high"],
            raw_low=market["low"],
            raw_close=market["close"],
            raw_pre_close=market["pre_close"],
            prev_raw_close=market["pre_close"],
            raw_volume=market["volume_hands"],
            raw_amount=market["amount_cny"],
            circ_mv=market["circ_mv_cny"],
        )
        adjustments = market[
            ["trade_date", "symbol", "adj_factor", "adjustment_anchor_date", "available_at"]
        ]
        lifecycle = lifecycle.drop(columns=["ts_code"])
        actions = actions.drop(columns=["ts_code"])

        objects = {
            "trade_calendar.csv": calendar,
            "tradable_universe.csv": universe,
            "scores.csv": scores,
            "prices.csv": prices,
            "adjustment_factors.csv": adjustments,
            "strict_corporate_actions.csv": actions,
            "strict_security_lifecycle.csv": lifecycle,
        }
        for filename, frame in objects.items():
            _write_csv(frame, building / filename)
        (building / "initial_account.json").write_text(
            json.dumps(
                {"currency": "CNY", "initial_cash_cny": 500_000, "positions": {}},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        strict_manifest = {
            "snapshot_schema_version": "strict_corporate_lifecycle_snapshot_v2",
            "dataset_version": snapshot_id,
            "generated_at": datetime.now().astimezone().isoformat(),
            "source_sha256": str(metadata.get("source_manifest_sha256") or ""),
            "snapshot_sha256": _sha(building / "strict_corporate_actions.csv"),
            "lifecycle_source_sha256": str(metadata.get("source_manifest_sha256") or ""),
            "lifecycle_snapshot_sha256": _sha(
                building / "strict_security_lifecycle.csv"
            ),
        }
        (building / "strict_snapshot_manifest.json").write_text(
            json.dumps(strict_manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        names = [
            *objects,
            "strict_snapshot_manifest.json",
            "initial_account.json",
        ]
        source_manifest = {
            "schema_version": "formal_package_v2",
            "snapshot_id": snapshot_id,
            "calendar_source": str(metadata["calendar_source"]),
            "coverage_start": pd.to_datetime(str(start_date)).strftime("%Y-%m-%d"),
            "coverage_end": pd.to_datetime(str(end_date)).strftime("%Y-%m-%d"),
            "adjustment_anchor_date": int(metadata["adjustment_anchor_date"]),
            "corporate_action_complete": bool(
                not actions.empty
                and actions["source_complete"].astype(bool).all()
            ),
            "security_lifecycle_complete": bool(
                lifecycle["source_complete"].astype(bool).all()
            ),
            "score_paths": {
                strategy: (
                    "dynamic_factor_score"
                    if strategy == FORMAL_STRATEGIES[1]
                    else "liquidity_detail_score"
                )
                for strategy in FORMAL_STRATEGIES
            },
            "objects": {
                name: {"sha256": _sha(building / name)} for name in names
            },
        }
        (building / "source_manifest.json").write_text(
            json.dumps(source_manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        import yaml

        config = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        result = evaluate_package(building, config)
        (building / "readiness_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if result["status"] != "READY_FOR_FORMAL_RUN":
            return {"package": str(building), **result}
        for child in building.iterdir():
            if child.is_file():
                child.chmod(0o444)
        building.chmod(0o555)
        building.rename(output)
        return {"package": str(output), **result}
    except Exception:
        # Keep populated staging directories as diagnostic evidence. Remove
        # only a directory that was never populated enough to be useful.
        if building.exists() and not any(building.iterdir()):
            shutil.rmtree(building)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engine = create_engine(require_sqlalchemy_url())
    result = build(engine, snapshot_id=args.snapshot_id, output=args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "READY_FOR_FORMAL_RUN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
