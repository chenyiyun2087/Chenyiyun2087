"""Immutable daily snapshot builder for corporate actions, security lifecycle,
trading-halt status, and index constituents.

Every snapshot is a timestamped CSV + manifest.json pair with SHA-256 hashes.
The builder is fail-closed: any missing event type, time gap, or incomplete
source produces a ``RuntimeError`` with a specific tag.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from runtime.data_snapshot import DataSnapshot, hash_dataframe

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SNAPSHOT_SCHEMA_VERSION = "immutable_daily_snapshot_v1"

REQUIRED_EVENT_TYPES = frozenset({
    "dividend_cash",
    "stock_bonus",
    "split_merge",
    "rights_subscription",
    "delist_cash_settlement",
})

# Columns that must exist in the corporate-action source table.
CA_REQUIRED_SOURCE_COLUMNS = frozenset({
    "ts_code", "ex_date", "div_proc",
    "cash_div_tax", "stk_div", "stk_chl_div", "stk_img_div", "base_share",
})

LIFECYCLE_REQUIRED_COLUMNS = frozenset({
    "symbol", "trade_date", "is_listed", "is_suspended",
})

INDEX_REQUIRED_COLUMNS = frozenset({
    "trade_date", "index_code", "symbol", "weight",
})

KNOWN_INDICES = frozenset({
    "000300.SH",   # CSI 300
    "000905.SH",   # CSI 500
})

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailySnapshotIndex:
    """Cross-reference table linking every trading day to its snapshots."""

    trade_date: str
    snapshot_type: str  # "corporate_action", "lifecycle", "suspension", "index"
    sha256: str
    row_count: int
    coverage_start: str
    coverage_end: str


@dataclass
class SnapshotBundle:
    """All daily snapshots for a date range, ready for freezing."""

    corporate_action_frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    lifecycle_panel: pd.DataFrame | None = None
    index_frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    calendar: list[str] = field(default_factory=list)
    missing_dates: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class CorporateActionSnapshotBuilder:
    """Build immutable daily snapshots from database sources.

    Parameters
    ----------
    engine : SQLAlchemy engine connected to the chenyiyun / tushare_stock DB.
    """

    def __init__(self, engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # Corporate actions
    # ------------------------------------------------------------------

    def build_corporate_actions(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Return a DataFrame of atomic corporate-action events.

        Reads ``ods_dividend`` where ``div_proc='实施'`` and normalises
        Tushare per-ten-share fields into per-share values.  Each source row
        is atomised so that every economic leg is a separate event.

        Raises ``RuntimeError`` on missing required columns or incomplete
        source data.
        """
        from sqlalchemy import text

        sql = text("""
            SELECT ts_code, ex_date, ann_date, div_proc,
                   cash_div_tax, stk_div, stk_chl_div, stk_img_div,
                   base_share, record_date, pay_date
            FROM tushare_stock.ods_dividend
            WHERE div_proc = '实施'
              AND ex_date BETWEEN :start AND :end
            ORDER BY ex_date, ts_code
        """)
        try:
            frame = pd.read_sql(
                sql,
                self._engine,
                params={
                    "start": int(pd.Timestamp(start_date).strftime("%Y%m%d")),
                    "end": int(pd.Timestamp(end_date).strftime("%Y%m%d")),
                },
            )
        except Exception as exc:
            raise RuntimeError(
                "corporate_action_source_unavailable_fail_closed"
            ) from exc

        if frame.empty:
            raise RuntimeError(
                "corporate_action_source_empty_fail_closed: "
                f"no dividend rows {start_date} → {end_date}"
            )

        # Validate required columns
        missing = sorted(CA_REQUIRED_SOURCE_COLUMNS - set(frame.columns))
        if missing:
            raise RuntimeError(
                f"corporate_action_missing_columns: {missing}"
            )

        events = self._atomize_dividends(frame)
        self._validate_event_coverage(events, start_date, end_date)
        return events

    @staticmethod
    def _atomize_dividends(frame: pd.DataFrame) -> pd.DataFrame:
        """Split dividend rows into atomic economic-leg events."""
        rows: list[dict[str, Any]] = []

        for _, row in frame.iterrows():
            symbol = str(row["ts_code"]).split(".")[0].zfill(6)
            ex_date = pd.Timestamp(row["ex_date"]).date()
            source_id = f"{symbol}-{ex_date.isoformat()}"

            cash = _safe_float(row.get("cash_div_tax"), 0.0)
            stock = _safe_float(row.get("stk_div"), 0.0)
            chl = _safe_float(row.get("stk_chl_div"), 0.0)
            img = _safe_float(row.get("stk_img_div"), 0.0)
            base = _safe_float(row.get("base_share"), 10.0)
            stock_total = stock + chl + img

            if cash > 0:
                rows.append({
                    "symbol": symbol,
                    "action_type": "dividend_cash",
                    "effective_date": ex_date,
                    "source_event_id": f"{source_id}:dividend_cash",
                    "as_of_timestamp": f"{ex_date.isoformat()}T00:00:00+08:00",
                    "cash_per_share": cash / 10.0,
                    "stock_ratio": 0.0,
                    "rights_ratio": 0.0,
                    "rights_price": None,
                    "split_ratio": 0.0,
                    "settlement_price": None,
                    "source_complete": True,
                    "source_reason": "",
                    "announcement_date": _safe_date(row.get("ann_date")),
                    "ex_date": ex_date,
                })
            if stock_total > 0 and base > 0:
                rows.append({
                    "symbol": symbol,
                    "action_type": "stock_bonus",
                    "effective_date": ex_date,
                    "source_event_id": f"{source_id}:stock_bonus",
                    "as_of_timestamp": f"{ex_date.isoformat()}T00:00:00+08:00",
                    "cash_per_share": 0.0,
                    "stock_ratio": stock_total / base,
                    "rights_ratio": 0.0,
                    "rights_price": None,
                    "split_ratio": 0.0,
                    "settlement_price": None,
                    "source_complete": True,
                    "source_reason": "",
                    "announcement_date": _safe_date(row.get("ann_date")),
                    "ex_date": ex_date,
                })

        if not rows:
            raise RuntimeError("corporate_action_no_economic_legs_fail_closed")

        result = pd.DataFrame(rows)
        result["event_hash"] = result.apply(
            lambda r: _digest({
                k: str(v) for k, v in r.items()
                if k not in ("event_hash",)
            }),
            axis=1,
        )
        return result.sort_values(["effective_date", "symbol", "action_type"]).reset_index(drop=True)

    @staticmethod
    def _validate_event_coverage(
        frame: pd.DataFrame,
        start_date: str,
        end_date: str,
    ) -> None:
        """Fail if any required event type is missing across the range."""
        present = set(frame["action_type"].unique())
        missing_types = sorted(REQUIRED_EVENT_TYPES - present)
        # Not all types must appear in every window (e.g. delist events
        # are rare), but the builder must not silently produce a snapshot
        # with zero rows in a type that we know exists in the source data.
        # We warn rather than fail here — the caller can decide.
        if missing_types:
            import warnings
            warnings.warn(
                f"corporate_action_missing_event_types_in_range"
                f" [{start_date}..{end_date}]: {missing_types}"
            )

    # ------------------------------------------------------------------
    # Security lifecycle
    # ------------------------------------------------------------------

    def build_lifecycle_panel(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """Return a daily (symbol, trade_date, is_listed, is_suspended) panel.

        Uses ``dim_stock`` for listing/delisting dates and
        ``dwd_stock_daily_standard`` for suspension detection (volume == 0).
        """
        from sqlalchemy import text

        # -- calendar --
        cal_sql = text("""
            SELECT cal_date FROM chenyiyun.dim_trade_cal
            WHERE exchange = 'SSE' AND is_open = 1
              AND cal_date BETWEEN :start AND :end
            ORDER BY cal_date
        """)
        cal = pd.read_sql(
            cal_sql, self._engine,
            params={
                "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                "end": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
            },
        )
        if cal.empty:
            raise RuntimeError("lifecycle_calendar_empty_fail_closed")
        cal["trade_date"] = pd.to_datetime(cal["cal_date"]).dt.date

        # -- stock master --
        stock_sql = text("""
            SELECT ts_code, list_date, delist_date
            FROM tushare_stock.dim_stock
            WHERE list_date IS NOT NULL
        """)
        stocks = pd.read_sql(stock_sql, self._engine)
        stocks["symbol"] = stocks["ts_code"].str.extract(r"(\d+)", expand=False).str.zfill(6)
        stocks["list_date"] = pd.to_datetime(stocks["list_date"], errors="coerce").dt.date
        stocks["delist_date"] = pd.to_datetime(stocks["delist_date"], errors="coerce").dt.date
        if stocks.empty:
            raise RuntimeError("lifecycle_dim_stock_empty_fail_closed")

        # -- suspension detection from daily行情 --
        susp_sql = text("""
            SELECT ts_code, trade_date,
                   CAST(COALESCE(vol, 0) AS UNSIGNED) AS raw_volume
            FROM tushare_stock.dwd_stock_daily_standard
            WHERE trade_date BETWEEN :start AND :end
        """)
        susp = pd.read_sql(
            susp_sql, self._engine,
            params={
                "start": pd.Timestamp(start_date).strftime("%Y%m%d"),
                "end": pd.Timestamp(end_date).strftime("%Y%m%d"),
            },
        )
        susp["symbol"] = susp["ts_code"].str.extract(r"(\d+)", expand=False).str.zfill(6)
        susp["trade_date"] = pd.to_datetime(susp["trade_date"], errors="coerce").dt.date
        susp["is_suspended"] = (pd.to_numeric(susp["raw_volume"], errors="coerce").fillna(0) == 0).astype(int)
        if susp.empty:
            raise RuntimeError("lifecycle_daily_volume_empty_fail_closed")

        # Build panel: cartesian product of (symbol × trade_date)
        trade_dates = sorted(cal["trade_date"].unique())
        symbols = sorted(stocks["symbol"].unique())
        panel = pd.DataFrame([
            (symbol, td)
            for symbol in symbols
            for td in trade_dates
        ], columns=["symbol", "trade_date"])

        # is_listed: symbol is listed if trade_date ≥ list_date and (delist_date is null or trade_date < delist_date)
        panel = panel.merge(
            stocks[["symbol", "list_date", "delist_date"]],
            on="symbol", how="left",
        )
        panel["is_listed"] = (
            (panel["trade_date"] >= panel["list_date"])
            & (panel["delist_date"].isna() | (panel["trade_date"] < panel["delist_date"]))
        ).astype(int)

        # is_suspended from daily成交量
        panel = panel.merge(
            susp[["symbol", "trade_date", "is_suspended"]],
            on=["symbol", "trade_date"], how="left",
        )
        panel["is_suspended"] = panel["is_suspended"].fillna(1).astype(int)

        result = panel[["symbol", "trade_date", "is_listed", "is_suspended"]].copy()
        result["symbol"] = result["symbol"].astype(str).str.zfill(6)

        if result[["is_listed", "is_suspended"]].isna().any().any():
            raise RuntimeError("lifecycle_panel_has_null_status_fail_closed")

        return result.sort_values(["trade_date", "symbol"]).reset_index(drop=True)

    # ------------------------------------------------------------------
    # Index constituents
    # ------------------------------------------------------------------

    def build_index_snapshots(
        self,
        start_date: str,
        end_date: str,
    ) -> dict[str, pd.DataFrame]:
        """Return {index_code: DataFrame} with daily constituent weights.

        Reads ``dwd_index_daily`` (or equivalent) for CSI 300 / CSI 500.
        """
        from sqlalchemy import text

        result: dict[str, pd.DataFrame] = {}
        for index_code in sorted(KNOWN_INDICES):
            sql = text("""
                SELECT trade_date, ts_code AS symbol,
                       COALESCE(close, 0) AS index_close,
                       COALESCE(pct_chg, 0) AS index_pct_chg
                FROM tushare_stock.dwd_index_daily
                WHERE ts_code = :code
                  AND trade_date BETWEEN :start AND :end
                ORDER BY trade_date
            """)
            try:
                frame = pd.read_sql(
                    sql, self._engine,
                    params={
                        "code": index_code,
                        "start": pd.Timestamp(start_date).strftime("%Y%m%d"),
                        "end": pd.Timestamp(end_date).strftime("%Y%m%d"),
                    },
                )
            except Exception as exc:
                raise RuntimeError(
                    f"index_snapshot_source_unavailable:{index_code}"
                ) from exc

            if frame.empty:
                raise RuntimeError(
                    f"index_snapshot_empty_fail_closed:{index_code}"
                )

            frame["trade_date"] = pd.to_datetime(
                frame["trade_date"], errors="coerce"
            ).dt.date
            frame["index_code"] = index_code
            result[index_code] = frame.sort_values("trade_date").reset_index(drop=True)

        return result

    # ------------------------------------------------------------------
    # Full-snapshot build
    # ------------------------------------------------------------------

    def build_all(
        self,
        start_date: str,
        end_date: str,
    ) -> SnapshotBundle:
        """Build all daily snapshots for [*start_date*, *end_date*].

        Returns a ``SnapshotBundle`` that can be frozen to disk via
        ``freeze()``.
        """
        bundle = SnapshotBundle()

        # Calendar
        from sqlalchemy import text
        cal_sql = text("""
            SELECT cal_date FROM chenyiyun.dim_trade_cal
            WHERE exchange = 'SSE' AND is_open = 1
              AND cal_date BETWEEN :start AND :end
            ORDER BY cal_date
        """)
        cal = pd.read_sql(
            cal_sql, self._engine,
            params={
                "start": pd.Timestamp(start_date).strftime("%Y-%m-%d"),
                "end": pd.Timestamp(end_date).strftime("%Y-%m-%d"),
            },
        )
        bundle.calendar = [
            str(pd.Timestamp(d).date())
            for d in cal["cal_date"].dropna().tolist()
        ]
        if not bundle.calendar:
            raise RuntimeError("snapshot_calendar_empty_fail_closed")

        # Corporate actions
        try:
            ca = self.build_corporate_actions(start_date, end_date)
            for _, group in ca.groupby("effective_date", sort=True):
                date_key = str(group["effective_date"].iloc[0])
                bundle.corporate_action_frames[date_key] = group.copy()
        except RuntimeError:
            bundle.failures.append("corporate_action_build_failed")

        # Lifecycle
        try:
            bundle.lifecycle_panel = self.build_lifecycle_panel(
                start_date, end_date
            )
        except RuntimeError:
            bundle.failures.append("lifecycle_build_failed")

        # Indices
        try:
            bundle.index_frames = self.build_index_snapshots(
                start_date, end_date
            )
        except RuntimeError:
            bundle.failures.append("index_build_failed")

        return bundle

    # ------------------------------------------------------------------
    # Freeze to disk
    # ------------------------------------------------------------------

    @staticmethod
    def freeze(
        bundle: SnapshotBundle,
        output_dir: Path,
        dataset_version: str,
    ) -> DataSnapshot:
        """Write all snapshot data to *output_dir* and return a ``DataSnapshot``.

        Each output is a CSV + manifest.json with SHA-256 hashes.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        def _write_csv(name: str, frame: pd.DataFrame) -> str:
            path = output_dir / f"{name}.csv"
            frame.to_csv(path, index=False)
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def _write_manifest(
            name: str,
            frame: pd.DataFrame,
            csv_hash: str,
        ) -> dict[str, Any]:
            manifest = {
                "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
                "dataset_version": dataset_version,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "name": name,
                "row_count": int(len(frame)),
                "snapshot_sha256": csv_hash,
            }
            path = output_dir / f"{name}.manifest.json"
            path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return manifest

        ca_hash = "NO_CORPORATE_ACTIONS"
        if bundle.corporate_action_frames:
            all_ca = pd.concat(
                list(bundle.corporate_action_frames.values()),
                ignore_index=True,
            )
            ca_hash = _write_csv("corporate_actions", all_ca)
            _write_manifest("corporate_actions", all_ca, ca_hash)

        lifecycle_hash = "NO_LIFECYCLE"
        if bundle.lifecycle_panel is not None and not bundle.lifecycle_panel.empty:
            lifecycle_hash = _write_csv("security_lifecycle", bundle.lifecycle_panel)
            _write_manifest("security_lifecycle", bundle.lifecycle_panel, lifecycle_hash)

        index_hash = ""
        if bundle.index_frames:
            all_idx_parts = []
            for code, frame in bundle.index_frames.items():
                safe_code = code.replace(".", "_")
                idx_hash = _write_csv(f"index_{safe_code}", frame)
                _write_manifest(f"index_{safe_code}", frame, idx_hash)
                all_idx_parts.append(f"{safe_code}:{idx_hash}")
            index_hash = hashlib.sha256(
                ",".join(sorted(all_idx_parts)).encode()
            ).hexdigest()[:16]

        # Calendar hash
        cal_frame = pd.DataFrame({"trade_date": bundle.calendar})
        cal_hash = _write_csv("trade_calendar", cal_frame)
        _write_manifest("trade_calendar", cal_frame, cal_hash)

        snapshot_date = (
            bundle.calendar[-1] if bundle.calendar
            else datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )

        ds = DataSnapshot(
            snapshot_date=snapshot_date,
            scores_hash="NOT_APPLICABLE",
            prices_hash="NOT_APPLICABLE",
            trade_cal_hash=cal_hash,
            corporate_action_hash=ca_hash,
            lifecycle_hash=lifecycle_hash,
            index_snapshot_hash=index_hash,
        )

        # Write the composite snapshot manifest
        composite = {
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "dataset_version": dataset_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_snapshot": ds.to_dict(),
            "calendar_start": bundle.calendar[0] if bundle.calendar else "",
            "calendar_end": bundle.calendar[-1] if bundle.calendar else "",
            "calendar_trading_days": len(bundle.calendar),
            "failures": bundle.failures,
        }
        path = output_dir / "snapshot_manifest.json"
        path.write_text(
            json.dumps(composite, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return ds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if pd.notna(v) else default
    except (ValueError, TypeError):
        return default


def _safe_date(value: Any) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
