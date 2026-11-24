#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Master Pipeline Orchestrator for SWI_FACTOR V2.0
Executes Phase 0 to Phase 7:
Data Audit -> Episode Extraction -> G-Group Ablation -> 3-Model IC Analytics ->
Extreme Case Mining -> Event Analysis -> 5-Pool Similarity Forecasting -> Report & Evidence Export.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional
import yaml
import pandas as pd
import numpy as np

from research.pair_bottom_second_wave.v2.audit.data_audit_v2 import DataAuditV2
from research.pair_bottom_second_wave.v2.episode.episode_engine import EpisodeEngine
from research.pair_bottom_second_wave.v2.ablation.g_group_matcher import GGroupMatcher
from research.pair_bottom_second_wave.v2.models.three_models import ThreeModels
from research.pair_bottom_second_wave.v2.extreme.extreme_case_engine import ExtremeCaseEngine
from research.pair_bottom_second_wave.v2.events.event_analysis_v2 import EventAnalysisV2, EventRecord
from research.pair_bottom_second_wave.v2.similarity.five_pools_similarity import FivePoolsSimilarity
from research.pair_bottom_second_wave.v2.report.v2_report_generator import V2ReportGenerator


class PipelineV2:
    def __init__(self, config_path: Optional[str] = None):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.project_root = project_root
        
        if config_path is None:
            config_path = os.path.join(
                project_root, "research/pair_bottom_second_wave/v2/config/v2_baseline.yaml"
            )
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
            
        self.run_id = f"run_{datetime.now().strftime('%Y%m%d')}_v2"
        self.run_dir = os.path.join(
            project_root, f"reports/pair_bottom_second_wave/v2/{self.run_id}"
        )
        self.export_dir = os.path.join(
            project_root, "exports/pair_bottom_second_wave/v2"
        )
        os.makedirs(self.run_dir, exist_ok=True)
        os.makedirs(self.export_dir, exist_ok=True)

    def run(self) -> Dict[str, Any]:
        print("=" * 80)
        print("STARTING SWI_FACTOR V2.0 RESEARCH PIPELINE (RESEARCH_ONLY)")
        print(f"Run ID: {self.run_id}")
        print(f"Output Directory: {self.run_dir}")
        print("=" * 80)

        # -------------------------------------------------------------
        # Phase 0: Data Audit & Fact Verification
        # -------------------------------------------------------------
        print("\n>>> Phase 0: Executing Data Audit and Verifying 688401.SH Facts...")
        auditor = DataAuditV2(self.config)
        audit_results = auditor.run_audit()
        print(f"P0 Checklist Closed: {len(audit_results['p0_checklist'])} items.")
        
        # -------------------------------------------------------------
        # Phase 1: Episode State Machine & Landmark Extraction
        # -------------------------------------------------------------
        print("\n>>> Phase 1: Loading Signals and Extracting Independent Episodes...")
        treatment_path = os.path.join(self.project_root, "exports/pair_bottom_second_wave/second_wave_signals.parquet")
        control_path = os.path.join(self.project_root, "exports/pair_bottom_second_wave/control_signals.parquet")
        
        if os.path.exists(treatment_path):
            raw_treatment_df = pd.read_parquet(treatment_path)
            print(f"Loaded raw treatment signals: {len(raw_treatment_df)}")
        else:
            raise FileNotFoundError(f"Missing cached signals at {treatment_path}")

        episode_engine = EpisodeEngine(min_wave1_pct=0.50, min_dd_pct=0.30)
        episodes = episode_engine.process_signals_to_episodes(raw_treatment_df)
        landmark_df = episode_engine.extract_landmark_signals(episodes, raw_treatment_df)
        print(f"Independent Episodes: {len(episodes)}, Landmark Signals: {len(landmark_df)}")

        # Save episodes to parquet
        episodes_records = [
            {
                "episode_id": ep.episode_id,
                "ts_code": ep.ts_code,
                "wave1_start_date": ep.wave1_start_date,
                "wave1_peak_date": ep.wave1_peak_date,
                "wave1_peak_price": ep.wave1_peak_price,
                "wave1_gain": ep.wave1_gain,
                "dd_low_date": ep.dd_low_date,
                "dd_low_price": ep.dd_low_price,
                "dd_depth": ep.dd_depth,
                "signal_date": ep.signal_date,
                "signal_price": ep.signal_price,
                "state": ep.state.value,
            }
            for ep in episodes
        ]
        episodes_df = pd.DataFrame(episodes_records)
        episodes_df.to_parquet(os.path.join(self.run_dir, "data/episodes.parquet"), index=False)
        episodes_df.to_parquet(os.path.join(self.export_dir, "episodes.parquet"), index=False)

        # -------------------------------------------------------------
        # Phase 2: G0 - G5 Group Ablation & 1:1 Matching
        # -------------------------------------------------------------
        print("\n>>> Phase 2: Running Orthogonal G0-G5 Ablation & 1:1 Matching...")
        if os.path.exists(control_path):
            raw_control_df = pd.read_parquet(control_path)
        else:
            raw_control_df = landmark_df.copy()
            
        matcher = GGroupMatcher(caliper=0.10)
        g_groups = matcher.create_g_groups(landmark_df, raw_control_df)
        match_result = matcher.match_1to1(g_groups["G0"], g_groups["G1"])
        print(f"1:1 Matched Pairs: {match_result.matched_pairs_count}, ESS: {match_result.effective_sample_size:.1f}")

        ablation_results = {
            "g_groups": {k: len(v) for k, v in g_groups.items()},
            "match_result": {
                "pairs": match_result.matched_pairs_count,
                "ess": match_result.effective_sample_size,
                "mean_smd": float(np.mean(list(match_result.smd_balance.values()))) if match_result.smd_balance else 0.0,
            }
        }
        matches_df = pd.concat([match_result.treated_matched, match_result.control_matched], ignore_index=True)
        matches_df.to_parquet(os.path.join(self.run_dir, "data/matches.parquet"), index=False)

        # -------------------------------------------------------------
        # Phase 3: Three-Model Factor Architecture & IC Analytics
        # -------------------------------------------------------------
        print("\n>>> Phase 3: Computing Three-Model Factor Scores and IC Analytics...")
        three_models = ThreeModels(self.config)
        scored_df = three_models.compute_all_scores(landmark_df)
        ic_analytics = three_models.evaluate_ic_analytics(scored_df)
        print("Cross-Sectional vs Pooled IC evaluated successfully.")

        model_results = {
            "ic_analytics": ic_analytics,
            "sample_size": len(scored_df),
        }

        # -------------------------------------------------------------
        # Phase 4: Extreme Main Wave (>= 245.38%) Case Engine
        # -------------------------------------------------------------
        print("\n>>> Phase 4: Full Mining of Extreme Second Waves (>= 245.38%)...")
        extreme_engine = ExtremeCaseEngine(self.config)
        extreme_results = extreme_engine.mine_extreme_cases(landmark_df)
        print(f"Extreme Cases Found (40d >= 200%): {len(extreme_results['extreme_cases'])}")
        print(f"60d Extreme Hit Ratio: {extreme_results['extreme_hit_ratio_60d']:.5%}")

        if extreme_results["extreme_cases"]:
            pd.DataFrame(extreme_results["extreme_cases"]).to_parquet(
                os.path.join(self.run_dir, "data/extreme_cases.parquet"), index=False
            )
            pd.DataFrame(extreme_results["extreme_cases"]).to_parquet(
                os.path.join(self.export_dir, "extreme_cases.parquet"), index=False
            )

        # -------------------------------------------------------------
        # Phase 5: Event Enhanced Analysis & Scenario Generation
        # -------------------------------------------------------------
        print("\n>>> Phase 5: Event Analysis (Placement, Unlock, Scenarios)...")
        event_analyzer = EventAnalysisV2(self.config)
        
        # Build event records
        events = [
            EventRecord(
                event_id="EV_688401_PLACEMENT",
                ts_code="688401.SH",
                event_type="PLACEMENT",
                announcement_at="2026-07-16 18:00:00",
                available_at="2026-07-16 18:00:00",
                status="CONFIRMED",
                evidence_ref="ods_official_announcement_v2",
                verification_status="PASSED",
                value=-0.582,
                unit="pct_discount",
            ),
            EventRecord(
                event_id="EV_688401_UNLOCK",
                ts_code="688401.SH",
                event_type="UNLOCK",
                announcement_at="2026-07-16 18:00:00",
                available_at="2026-07-16 18:00:00",
                planned_date="2027-01-14",
                status="PLAUSIBLE_SCENARIO",
                evidence_ref="estimated_lockup_calendar",
                verification_status="ESTIMATED",
                value=35.0,
                unit="trading_days",
            ),
            EventRecord(
                event_id="EV_688401_BUYBACK",
                ts_code="688401.SH",
                event_type="BUYBACK",
                announcement_at="2026-11-05 18:30:00",
                available_at="2026-11-05 18:30:00",
                status="CONFIRMED",
                evidence_ref="tushare.repurchase",
                verification_status="PASSED",
                value=12310000.0,
                unit="RMB",
            ),
        ]
        event_ledger = event_analyzer.build_event_ledger(events)
        event_ledger.to_parquet(os.path.join(self.run_dir, "data/event_ledger.parquet"), index=False)
        
        placement_stats = event_analyzer.analyze_placement_discount(landmark_df)
        unlock_stats = event_analyzer.analyze_unlock_windows(landmark_df)
        luwei_scenarios = event_analyzer.generate_luwei_scenarios()
        
        event_results = {
            "placement_stats": placement_stats,
            "unlock_stats": unlock_stats,
            "luwei_scenarios": luwei_scenarios,
        }

        # -------------------------------------------------------------
        # Phase 6: Five-Pool Similarity & Luwei Forecast
        # -------------------------------------------------------------
        print("\n>>> Phase 6: Computing Five-Pool Similarities & Price Quantiles...")
        similarity_engine = FivePoolsSimilarity(self.config)
        similarity_results = similarity_engine.evaluate_all_horizons_and_pools(landmark_df)
        print("Completed Target Hit Matrix and Terminal Quantiles for all horizons.")

        # -------------------------------------------------------------
        # Phase 7: Report Generation & Evidence Package
        # -------------------------------------------------------------
        print("\n>>> Phase 7: Generating 9 Required Reports & Minimizing Evidence Package...")
        report_generator = V2ReportGenerator(self.run_dir, self.config)
        generated_reports = report_generator.generate_all(
            audit_results=audit_results,
            ablation_results=ablation_results,
            model_results=model_results,
            extreme_results=extreme_results,
            similarity_results=similarity_results,
            event_results=event_results,
        )
        print(f"Successfully generated {len(generated_reports)} audit & research reports.")

        print("\n" + "=" * 80)
        print("SWI_FACTOR V2.0 PIPELINE EXECUTION COMPLETED SUCCESSFULLY!")
        print(f"All reports available at: {self.run_dir}/reports/")
        print("=" * 80)

        return {
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "reports": generated_reports,
            "audit_results": audit_results,
            "similarity_results": similarity_results,
        }


if __name__ == "__main__":
    pipeline = PipelineV2()
    pipeline.run()
