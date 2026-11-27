"""Month-end valuation screening service for PE/PEG (Forecast version and Disclosed Growth Proxy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd


@dataclass
class ValuationCandidate:
    ts_code: str
    trade_date: int
    pe_ttm: float
    cagr_2y: float
    peg: float
    report_year: int
    ann_date_int: int
    revision_id: str
    snapshot_sha: str
    passed: bool
    exclusion_reason: Optional[str] = None


@dataclass
class MonthEndValuationSnapshot:
    month_end_date: int
    effective_open_date: int
    universe_count: int
    eligible_count: int
    candidates: List[ValuationCandidate] = field(default_factory=list)
    top3_valuation_only: List[str] = field(default_factory=list)
    watchlist_valuation_plus_z: List[str] = field(default_factory=list)
    exclusions_breakdown: Dict[str, int] = field(default_factory=dict)
    forecast_status: str = "UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING"


class ValuationService:
    """Computes month-end PE/PEG screens under Forecast and Historical Proxy specifications."""

    def __init__(
        self,
        pe_lower: float = 0.0,
        pe_upper: float = 60.0,
        peg_lower: float = 0.0,
        peg_upper: float = 1.1,
    ):
        self.pe_lower = pe_lower
        self.pe_upper = pe_upper
        self.peg_lower = peg_lower
        self.peg_upper = peg_upper

    def compute_monthly_snapshots(
        self,
        month_ends: List[int],
        next_open_dates: List[int],
        eligible_symbols_by_date: Dict[int, List[str]],
        pe_df: pd.DataFrame,
        disclosed_cagr_df: pd.DataFrame,
        forecast_audit: Dict[str, Any],
    ) -> Dict[int, MonthEndValuationSnapshot]:
        """Compute month-end valuation snapshots for each month-end date."""
        snapshots: Dict[int, MonthEndValuationSnapshot] = {}

        # Pre-index pe_df by (trade_date, ts_code)
        pe_map = pe_df.set_index(["trade_date", "ts_code"])["pe_ttm"].to_dict()

        for me_date, eff_open in zip(month_ends, next_open_dates):
            eligible_symbols = eligible_symbols_by_date.get(me_date, [])
            universe_count = len(eligible_symbols)

            # Available disclosed annual reports: announced strictly before me_date (ann_date_int < me_date)
            # This ensures only announced records available prior to this day's close are used.
            avail_reports = disclosed_cagr_df[disclosed_cagr_df["ann_date_int"] < me_date]
            latest_reports = (
                avail_reports.sort_values(["ann_date_int", "report_year"])
                .groupby("ts_code")
                .last()
                .to_dict(orient="index")
            )

            candidates: List[ValuationCandidate] = []
            exclusions: Dict[str, int] = {
                "NOT_ELIGIBLE_HISTORY": 0,
                "MISSING_PE_TTM": 0,
                "PE_OUT_OF_BOUNDS": 0,
                "MISSING_ANNUAL_REPORT": 0,
                "NON_POSITIVE_CAGR": 0,
                "PEG_OUT_OF_BOUNDS": 0,
                "PASSED": 0,
            }

            for sym in eligible_symbols:
                pe_val = pe_map.get((me_date, sym))
                if pe_val is None or not np.isfinite(pe_val):
                    exclusions["MISSING_PE_TTM"] += 1
                    candidates.append(
                        ValuationCandidate(
                            ts_code=sym, trade_date=me_date, pe_ttm=np.nan, cagr_2y=np.nan,
                            peg=np.nan, report_year=0, ann_date_int=0, revision_id="",
                            snapshot_sha="", passed=False, exclusion_reason="MISSING_PE_TTM",
                        )
                    )
                    continue

                if not (self.pe_lower < pe_val < self.pe_upper):
                    exclusions["PE_OUT_OF_BOUNDS"] += 1
                    candidates.append(
                        ValuationCandidate(
                            ts_code=sym, trade_date=me_date, pe_ttm=float(pe_val), cagr_2y=np.nan,
                            peg=np.nan, report_year=0, ann_date_int=0, revision_id="",
                            snapshot_sha="", passed=False, exclusion_reason="PE_OUT_OF_BOUNDS",
                        )
                    )
                    continue

                rep = latest_reports.get(sym)
                if rep is None:
                    exclusions["MISSING_ANNUAL_REPORT"] += 1
                    candidates.append(
                        ValuationCandidate(
                            ts_code=sym, trade_date=me_date, pe_ttm=float(pe_val), cagr_2y=np.nan,
                            peg=np.nan, report_year=0, ann_date_int=0, revision_id="",
                            snapshot_sha="", passed=False, exclusion_reason="MISSING_ANNUAL_REPORT",
                        )
                    )
                    continue

                cagr_val = rep["cagr_2y"]
                if not rep["is_positive_growth"] or not np.isfinite(cagr_val) or cagr_val <= 0:
                    exclusions["NON_POSITIVE_CAGR"] += 1
                    candidates.append(
                        ValuationCandidate(
                            ts_code=sym, trade_date=me_date, pe_ttm=float(pe_val), cagr_2y=float(cagr_val) if np.isfinite(cagr_val) else 0.0,
                            peg=np.nan, report_year=int(rep["report_year"]), ann_date_int=int(rep["ann_date_int"]),
                            revision_id=str(rep["revision_id"]), snapshot_sha=str(rep["snapshot_sha"]),
                            passed=False, exclusion_reason="NON_POSITIVE_CAGR",
                        )
                    )
                    continue

                # PEG = PE_TTM / (100 * CAGR)
                peg_val = float(pe_val) / (100.0 * float(cagr_val))
                if not (self.peg_lower < peg_val < self.peg_upper):
                    exclusions["PEG_OUT_OF_BOUNDS"] += 1
                    candidates.append(
                        ValuationCandidate(
                            ts_code=sym, trade_date=me_date, pe_ttm=float(pe_val), cagr_2y=float(cagr_val),
                            peg=peg_val, report_year=int(rep["report_year"]), ann_date_int=int(rep["ann_date_int"]),
                            revision_id=str(rep["revision_id"]), snapshot_sha=str(rep["snapshot_sha"]),
                            passed=False, exclusion_reason="PEG_OUT_OF_BOUNDS",
                        )
                    )
                    continue

                exclusions["PASSED"] += 1
                candidates.append(
                    ValuationCandidate(
                        ts_code=sym, trade_date=me_date, pe_ttm=float(pe_val), cagr_2y=float(cagr_val),
                        peg=peg_val, report_year=int(rep["report_year"]), ann_date_int=int(rep["ann_date_int"]),
                        revision_id=str(rep["revision_id"]), snapshot_sha=str(rep["snapshot_sha"]),
                        passed=True, exclusion_reason=None,
                    )
                )

            passed_candidates = [c for c in candidates if c.passed]
            # Valuation-only: sort by peg asc, pe_ttm asc, ts_code asc, take top 3
            passed_candidates.sort(key=lambda c: (c.peg, c.pe_ttm, c.ts_code))
            top3 = [c.ts_code for c in passed_candidates[:3]]
            # Valuation+Z watchlist: all passed candidates in this month
            watchlist = [c.ts_code for c in passed_candidates]

            snapshots[me_date] = MonthEndValuationSnapshot(
                month_end_date=me_date,
                effective_open_date=eff_open,
                universe_count=universe_count,
                eligible_count=len(passed_candidates),
                candidates=candidates,
                top3_valuation_only=top3,
                watchlist_valuation_plus_z=watchlist,
                exclusions_breakdown=exclusions,
                forecast_status=forecast_audit.get("status", "UNESTIMABLE_DUE_TO_QUALIFIED_DATA_LACKING"),
            )

        return snapshots
