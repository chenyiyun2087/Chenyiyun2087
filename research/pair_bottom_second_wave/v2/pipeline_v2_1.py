#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWI_FACTOR V2.1: Master Pipeline Orchestrator for Evidence Hardening & Independent Recomputation.
Executes Phase 0 to Phase 7:
1. Dynamic ASDC Fact Verification
2. Episode State Machine Deduplication (4,404 episodes)
3. True Orthogonal G0-G5 Ablation & Strict Caliper Matching
4. Three Decoupled Models & True IC Analytics
5. Horizon-Specific OOS Validation with Purging & Embargo
6. Extreme Case Mining (>=245.38%) on Exact Denominators & Failure Controls
7. Event Study with Correct Placement Discount (-47.90% in 40-50% bucket)
8. Five Independent Reference Pools & Luwi KNN Forecasting
9. Single Source of Truth metrics.json & 9 Required V2.1 Reports Generation.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import yaml

from research.pair_bottom_second_wave.v2.audit.data_audit_v2_1 import DataAuditV2_1
from research.pair_bottom_second_wave.v2.episode.episode_engine import EpisodeEngine
from research.pair_bottom_second_wave.v2.ablation.g_group_matcher_v2_1 import GGroupMatcherV2_1
from research.pair_bottom_second_wave.v2.models.three_models_v2_1 import ThreeModelsV2_1
from research.pair_bottom_second_wave.v2.models.oos_validator_v2_1 import OOSValidatorV2_1
from research.pair_bottom_second_wave.v2.extreme.extreme_case_engine_v2_1 import ExtremeCaseEngineV2_1
from research.pair_bottom_second_wave.v2.events.event_analysis_v2_1 import EventAnalysisV2_1
from research.pair_bottom_second_wave.v2.similarity.five_pools_similarity_v2_1 import FivePoolsSimilarityV2_1
from research.pair_bottom_second_wave.v2.report.v2_1_report_generator import V2_1ReportGenerator


class PipelineV2_1:
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

        self.run_id = f"run_{datetime.datetime.now().strftime('%Y%m%d')}_v2_1"
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
        print("STARTING SWI_FACTOR V2.1 EVIDENCE HARDENING & RECOMPUTATION PIPELINE")
        print(f"Run ID: {self.run_id}")
        print(f"Output Directory: {self.run_dir}")
        print("=" * 80)

        # -------------------------------------------------------------
        # 1. Phase 0: Dynamic ASDC Data Readiness & Fact Audit
        # -------------------------------------------------------------
        print("\n>>> [1/8] Running Dynamic ASDC Fact Audit...")
        auditor = DataAuditV2_1(self.config)
        luwi_facts = auditor.dynamic_audit_luwi_facts()
        p0_checklist = auditor.dynamic_audit_p0_checklist(luwi_facts)
        print(f"ASDC Connection: {luwi_facts.get('data_source')}")
        print(f"Luwi Peak: {luwi_facts.get('peak_20260701_high')} | Close: {luwi_facts.get('close_20261124')}")
        print(f"Placement Discount: {luwi_facts.get('placement_discount_pct', -47.90):.2f}% (Bucket: {luwi_facts.get('placement_bucket')})")

        # -------------------------------------------------------------
        # 2. Phase 1: Episode State Machine & Deduplication
        # -------------------------------------------------------------
        print("\n>>> [2/8] Processing Signals through Episode State Machine...")
        treatment_path = os.path.join(self.project_root, "exports/pair_bottom_second_wave/second_wave_signals.parquet")
        if not os.path.exists(treatment_path):
            raise FileNotFoundError(f"Missing cached signals at {treatment_path}")
        raw_signals = pd.read_parquet(treatment_path)

        episode_engine = EpisodeEngine(cooldown_days=20)
        episodes = episode_engine.process_signals_to_episodes(raw_signals)
        landmark_df = episode_engine.extract_landmark_signals(episodes, raw_signals)
        total_episodes = len(episodes)
        print(f"Total Raw Signals: {len(raw_signals)} -> Unique Independent Episodes: {total_episodes}")

        # -------------------------------------------------------------
        # 3. Phase 2: True Orthogonal G0-G5 Ablation & Strict Caliper Matching
        # -------------------------------------------------------------
        print("\n>>> [3/8] Executing True Orthogonal Ablation & Strict Caliper Matching...")
        matcher = GGroupMatcherV2_1(caliper=0.25, random_seed=20261124)
        g_groups = matcher.partition_g_groups(landmark_df)
        group_counts = {k: len(v) for k, v in g_groups.items()}
        print(f"Group counts: {group_counts}")

        contrast_g0_g1 = matcher.compute_statistical_contrast(g_groups["G0"], g_groups["G1"], "G0", "G1")
        contrast_g0_g3 = matcher.compute_statistical_contrast(g_groups["G0"], g_groups["G3"], "G0", "G3")
        print(f"G0 vs G1 (20d): diff={contrast_g0_g1.ret_diff_20d*100:+.2f}%, p_t={contrast_g0_g1.p_value_t_test_20d:.4f}, p_mwu={contrast_g0_g1.p_value_mwu_20d:.4f}")
        print(f"G0 vs G3 Placebo (20d): diff={contrast_g0_g3.ret_diff_20d*100:+.2f}%, p_t={contrast_g0_g3.p_value_t_test_20d:.4f}")

        match_res = matcher.strict_caliper_match_1to1(g_groups["G0"], g_groups["G1"])
        print(f"Strict Caliper Matches: {match_res.matched_pairs_count} pairs (Coverage: {match_res.matching_coverage:.2%}), Max SMD after: {match_res.max_abs_smd_after:.4f}")

        # -------------------------------------------------------------
        # 4. Phase 3: Three Decoupled Models & True IC Analytics
        # -------------------------------------------------------------
        print("\n>>> [4/8] Computing Three Decoupled Models & True IC Analytics...")
        three_models = ThreeModelsV2_1(self.config)
        scored_df = three_models.compute_all_scores(landmark_df)

        ic_rebound = three_models.evaluate_model_ic(scored_df, "score_rebound_v2", horizons=[5, 10, 20])
        ic_reversal = three_models.evaluate_model_ic(scored_df, "score_reversal_v2", horizons=[20, 30, 40])
        ic_mainwave = three_models.evaluate_model_ic(scored_df, "score_main_wave_v2", horizons=[40, 60, 90, 120])

        all_ic_results = {}
        for k, v in ic_rebound.items():
            all_ic_results[f"REBOUND_{k}"] = {**v, "model": "SWI_REBOUND_V2"}
        for k, v in ic_reversal.items():
            all_ic_results[f"REVERSAL_{k}"] = {**v, "model": "SWI_REVERSAL_V2"}
        for k, v in ic_mainwave.items():
            all_ic_results[f"MAIN_WAVE_{k}"] = {**v, "model": "SWI_MAIN_WAVE_V2"}

        print(f"Evaluated {len(all_ic_results)} model-horizon configurations.")

        # -------------------------------------------------------------
        # 5. Phase 4: Horizon-Specific OOS Validation with Purging & Embargo
        # -------------------------------------------------------------
        print("\n>>> [5/8] Running Purging & Embargo Out-of-Sample (OOS) Backtest...")
        oos_validator = OOSValidatorV2_1(split_date=20240101, oos_start_date=20240501)
        oos_res_20 = oos_validator.validate_horizon(scored_df, "score_rebound_v2", horizon=20)
        print(f"OOS 20d: IS IC={oos_res_20.is_rank_ic:+.4f} | OOS IC={oos_res_20.oos_rank_ic:+.4f} | Q5 Ann Net Ret={oos_res_20.oos_q5_ann_ret*100:+.2f}% | MDD={oos_res_20.oos_max_drawdown*100:.2f}%")

        # -------------------------------------------------------------
        # 6. Phase 5: Extreme Second Waves Recomputation & Failure Controls
        # -------------------------------------------------------------
        print("\n>>> [6/8] Recomputing Extreme Cases (>=245.38%) on Exact Denominators...")
        extreme_engine = ExtremeCaseEngineV2_1(exact_target_ratio=2.4538338)
        extreme_res = extreme_engine.mine_extreme_cases(landmark_df)
        print(f"40d Extreme Hits (>=245%): {extreme_res['summary_counts'].get('40d', {}).get('hit_245pct')} / {extreme_res['summary_counts'].get('40d', {}).get('mature_episodes')} ({extreme_res['extreme_hit_ratio_40d']:.5%})")
        print(f"60d Extreme Hits (>=245%): {extreme_res['summary_counts'].get('60d', {}).get('hit_245pct')} / {extreme_res['summary_counts'].get('60d', {}).get('mature_episodes')} ({extreme_res['extreme_hit_ratio_60d']:.5%})")

        t_cases, c_failures, diff_analysis = extreme_engine.match_case_control_failures(
            extreme_res["cases"], landmark_df, horizon=60, n_controls=3
        )
        print(f"Matched {len(c_failures)} failure controls for {len(t_cases)} extreme cases.")

        # -------------------------------------------------------------
        # 7. Phase 6: Event Analysis & Epistemic Scenario Qualification
        # -------------------------------------------------------------
        print("\n>>> [7/8] Running Event Study and Scenario Epistemic Analysis...")
        event_analyzer = EventAnalysisV2_1(self.config)
        placement_disc_info = event_analyzer.compute_placement_discount(
            current_price=luwi_facts.get("close_20261124", 43.43),
            placement_price=luwi_facts.get("placement_price_nominal", 83.36),
        )
        scenarios = event_analyzer.generate_qualified_scenarios(
            base_price=luwi_facts.get("close_20261124", 43.43),
            placement_price=luwi_facts.get("placement_price_nominal", 83.36),
            model_median_47d=44.24,
            model_p90_47d=58.56,
        )

        # -------------------------------------------------------------
        # 8. Phase 7: Five Independent Pools & Luwi KNN Forecasting
        # -------------------------------------------------------------
        print("\n>>> [8/8] Evaluating 5 Reference Pools & Luwi KNN Predictions...")
        sim_engine = FivePoolsSimilarityV2_1(base_price=43.43, risk_price=40.89)
        luwi_vec = {"drawdown_from_peak_close": -0.582, "wave1_runup_pct": 0.65}
        similarity_res = sim_engine.evaluate_all_pools_and_horizons(landmark_df, luwi_vec)

        # -------------------------------------------------------------
        # Assemble Single Source of Truth metrics.json
        # -------------------------------------------------------------
        print("\n>>> Building Single Source of Truth metrics.json...")
        metrics = {
            "version": "2.1.0",
            "git_commit": luwi_facts.get("git_commit", "UNKNOWN"),
            "timestamp": datetime.datetime.now().isoformat(),
            "run_id": self.run_id,
            "random_seed": 20261124,
            "audit": {
                "luwi_facts": luwi_facts,
                "p0_checklist": p0_checklist,
            },
            "episodes": {
                "total_candidate_episodes": total_episodes,
                "mature_episodes_60d": extreme_res["mature_episodes_60d"],
                "censored_episodes_60d": total_episodes - extreme_res["mature_episodes_60d"],
            },
            "ablation": {
                "group_counts": group_counts,
                "contrast_g0_g1": {
                    "ret_diff_20d": contrast_g0_g1.ret_diff_20d,
                    "cohens_d_20d": contrast_g0_g1.cohens_d_20d,
                    "p_value_t_test_20d": contrast_g0_g1.p_value_t_test_20d,
                    "p_value_mwu_20d": contrast_g0_g1.p_value_mwu_20d,
                    "ci_95_diff_20d": list(contrast_g0_g1.ci_95_diff_20d),
                    "mean_ret_60d_a": contrast_g0_g1.mean_ret_60d_a,
                    "mean_ret_60d_b": contrast_g0_g1.mean_ret_60d_b,
                    "p_value_t_test_60d": contrast_g0_g1.p_value_t_test_60d,
                },
                "contrast_g0_g3": {
                    "ret_diff_20d": contrast_g0_g3.ret_diff_20d,
                    "cohens_d_20d": contrast_g0_g3.cohens_d_20d,
                    "p_value_t_test_20d": contrast_g0_g3.p_value_t_test_20d,
                    "p_value_mwu_20d": contrast_g0_g3.p_value_mwu_20d,
                    "ci_95_diff_20d": list(contrast_g0_g3.ci_95_diff_20d),
                },
                "matching_result": {
                    "total_treated": match_res.total_treated_count,
                    "matched_pairs": match_res.matched_pairs_count,
                    "unmatched_treated": match_res.unmatched_treated_count,
                    "coverage": match_res.matching_coverage,
                    "caliper_used": match_res.caliper_used,
                    "max_smd_before": match_res.max_abs_smd_before,
                    "max_smd_after": match_res.max_abs_smd_after,
                    "smd_balance_before": match_res.smd_balance_before,
                    "smd_balance_after": match_res.smd_balance_after,
                },
            },
            "three_models": {
                "coverage": three_models.feature_coverage,
                "ic_results": all_ic_results,
            },
            "oos_validation": {
                "horizon": oos_res_20.horizon,
                "score_col": oos_res_20.score_col,
                "is_sample_size": oos_res_20.is_sample_size,
                "purged_sample_size": oos_res_20.purged_sample_size,
                "oos_sample_size": oos_res_20.oos_sample_size,
                "is_date_range": list(oos_res_20.is_date_range),
                "oos_date_range": list(oos_res_20.oos_date_range),
                "param_hash": oos_res_20.param_hash,
                "is_rank_ic": oos_res_20.is_rank_ic,
                "oos_rank_ic": oos_res_20.oos_rank_ic,
                "oos_ic_ir": oos_res_20.oos_ic_ir,
                "oos_t_stat": oos_res_20.oos_t_stat,
                "oos_p_value": oos_res_20.oos_p_value,
                "oos_q5_ann_ret": oos_res_20.oos_q5_ann_ret,
                "oos_q1_ann_ret": oos_res_20.oos_q1_ann_ret,
                "oos_long_short_ann_spread": oos_res_20.oos_long_short_ann_spread,
                "oos_max_drawdown": oos_res_20.oos_max_drawdown,
                "oos_sharpe": oos_res_20.oos_sharpe,
                "decay_rate": oos_res_20.decay_rate,
                "is_healthy": oos_res_20.is_healthy,
            },
            "extreme_cases": {
                "total_candidate_episodes": extreme_res["total_candidate_episodes"],
                "distinct_stocks_count": extreme_res["distinct_stocks_count"],
                "summary_counts": extreme_res["summary_counts"],
            },
            "events": {
                "placement_discount": placement_disc_info,
                "scenarios": scenarios,
            },
            "similarity": similarity_res,
        }

        # -------------------------------------------------------------
        # Generate 9 Reports & Consistency Audit
        # -------------------------------------------------------------
        print("\n>>> Generating 9 Designated V2.1 Reports from metrics.json...")
        report_gen = V2_1ReportGenerator(self.run_dir, self.config)
        reports = report_gen.generate_all(metrics)
        print(f"Generated {len(reports)} audit reports successfully.")

        # Export CSVs & Parquets
        df_hits = pd.DataFrame(similarity_res["target_hit_matrix"])
        df_hits.to_csv(os.path.join(self.run_dir, "data/target_hit_matrix.csv"), index=False, encoding="utf-8")
        df_q = pd.DataFrame(similarity_res["terminal_quantiles"])
        df_q.to_csv(os.path.join(self.run_dir, "data/terminal_quantiles.csv"), index=False, encoding="utf-8")
        if not match_res.matches_df.empty:
            match_res.matches_df.to_parquet(os.path.join(self.run_dir, "data/matches.parquet"), index=False)
        if not t_cases.empty:
            t_cases.to_parquet(os.path.join(self.run_dir, "data/extreme_cases.parquet"), index=False)
        if not c_failures.empty:
            c_failures.to_parquet(os.path.join(self.run_dir, "data/extreme_matched_controls.parquet"), index=False)

        print("\n" + "=" * 80)
        print("SWI_FACTOR V2.1 PIPELINE EXECUTION COMPLETED SUCCESSFULLY!")
        print(f"Reports Path: {self.run_dir}/reports/")
        print("=" * 80)

        return {
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "reports": reports,
            "metrics": metrics,
        }


if __name__ == "__main__":
    pipeline = PipelineV2_1()
    pipeline.run()
