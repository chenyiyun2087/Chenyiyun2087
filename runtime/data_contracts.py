"""Point-in-time table contracts and fail-closed validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class DataContract:
    name: str
    primary_key: tuple[str, ...]
    business_date_column: str
    visible_at_column: str
    required_columns: frozenset[str]
    source_column: str = "data_source"
    version_column: str = "data_version"
    updated_at_column: str = "updated_at"
    adjustment_column: str = "adjustment_method"
    backfill_allowed_column: str = "backfill_allowed"
    historical_use_allowed_column: str = "historical_use_allowed"

    @property
    def all_required_columns(self) -> frozenset[str]:
        return self.required_columns | frozenset(self.primary_key) | frozenset({
            self.business_date_column, self.visible_at_column, self.source_column,
            self.version_column, self.updated_at_column, self.adjustment_column,
            self.backfill_allowed_column, self.historical_use_allowed_column,
        })


CORE_CONTRACTS: dict[str, DataContract] = {
    "score_rank_daily": DataContract("score_rank_daily", ("trade_date", "ts_code"), "trade_date", "visible_at", frozenset({"score"})),
    "corporate_actions": DataContract("corporate_actions", ("symbol", "event_type", "ex_date"), "ex_date", "visible_at", frozenset({"event_type", "value"})),
    "trade_calendar": DataContract("trade_calendar", ("exchange", "cal_date"), "cal_date", "visible_at", frozenset({"is_open"})),
    "security_lifecycle": DataContract("security_lifecycle", ("symbol", "trade_date"), "trade_date", "visible_at", frozenset({"exchange", "is_listed", "is_suspended"})),
    "bs_signals": DataContract("bs_signals", ("symbol", "signal_date", "signal_id"), "signal_date", "visible_at", frozenset({"signal_type"})),
    "financials": DataContract("financials", ("symbol", "report_period", "metric"), "report_period", "visible_at", frozenset({"announcement_date", "value"})),
    "ai_scores": DataContract("ai_scores", ("symbol", "trade_date", "request_id"), "trade_date", "visible_at", frozenset({"model_id", "prompt_sha", "input_sha", "response_sha", "score"})),
    "ashare_features": DataContract("ashare_features", ("symbol", "trade_date", "feature_name"), "trade_date", "visible_at", frozenset({"feature_value"})),
}


def validate_frame(frame: pd.DataFrame, contract: DataContract, cutoff: datetime | str) -> None:
    missing = sorted(contract.all_required_columns - set(frame.columns))
    if missing:
        raise ValueError(f"data_contract_missing_columns:{contract.name}:{','.join(missing)}")
    if frame.empty:
        raise ValueError(f"data_contract_empty:{contract.name}")
    if frame.duplicated(list(contract.primary_key)).any():
        raise ValueError(f"data_contract_duplicate_primary_key:{contract.name}")
    visible_at = pd.to_datetime(frame[contract.visible_at_column], errors="coerce", utc=True)
    cutoff_ts = pd.Timestamp(cutoff)
    cutoff_ts = cutoff_ts.tz_localize("UTC") if cutoff_ts.tzinfo is None else cutoff_ts.tz_convert("UTC")
    if visible_at.isna().any():
        raise ValueError(f"data_contract_invalid_visible_at:{contract.name}")
    if (visible_at > cutoff_ts).any():
        raise ValueError(f"pit_future_visibility:{contract.name}")
    historical_allowed = frame[contract.historical_use_allowed_column]
    normalized = historical_allowed.astype(str).str.lower().isin({"1", "true", "yes"})
    if not normalized.all():
        raise ValueError(f"historical_use_blocked:{contract.name}")
    if frame[contract.source_column].astype(str).str.strip().eq("").any():
        raise ValueError(f"data_contract_missing_source:{contract.name}")


def reject_forbidden_backfilled_fields(columns: Iterable[str]) -> None:
    forbidden = sorted(name for name in columns if str(name).startswith("bs_model_"))
    if forbidden:
        raise ValueError(f"unproven_backfilled_fields_forbidden:{','.join(forbidden)}")
