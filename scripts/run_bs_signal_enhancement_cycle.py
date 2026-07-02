from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = PROJECT_ROOT / "exports" / "bs_signal_cycles"
MODEL_ROOT = PROJECT_ROOT / "exports" / "bs_signal_models"


def _run(cmd: list[str]) -> tuple[dict, str]:
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{output}")
    parsed = None
    for line_no in range(len(proc.stdout.splitlines())):
        candidate = "\n".join(proc.stdout.splitlines()[line_no:]).strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if parsed is None:
        parsed = {}
    return parsed, output


def _find_metric(metrics: list[dict], model: str, split: str = "test") -> dict:
    for row in metrics:
        if row.get("model") == model and row.get("split") == split:
            return row
    return {}


def _metric_snapshot(metrics_path: Path, model_kind: str | None = None) -> dict:
    data = json.loads(metrics_path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    model = model_kind or summary.get("model_kind") or "logistic_calibrated"
    test_metric = _find_metric(data.get("metrics", []), str(model), "test")
    out = {"summary": summary}
    if test_metric:
        out["test"] = {
            "model": model,
            "roc_auc": test_metric.get("roc_auc"),
            "average_precision": test_metric.get("average_precision"),
            "brier": test_metric.get("brier"),
            "ece": test_metric.get("ece"),
            "precision_at_10": test_metric.get("precision_at_10"),
            "precision_at_20": test_metric.get("precision_at_20"),
            "precision_at_30": test_metric.get("precision_at_30"),
        }
    return out


def _model_summaries(train_summary: dict) -> list[dict]:
    if isinstance(train_summary.get("models"), list):
        return [m for m in train_summary["models"] if isinstance(m, dict)]
    summary = train_summary.get("summary")
    return [summary] if isinstance(summary, dict) else []


def _metric_value(item: dict, key: str) -> float:
    value = item.get(key)
    if value is None:
        return float("-inf")
    return float(value)


def _select_deploy_model(summaries: list[dict], deploy_model_kind: str, deploy_metric: str) -> dict:
    if not summaries:
        raise RuntimeError("No model summaries found from training output.")
    if deploy_model_kind != "auto":
        for summary in summaries:
            if summary.get("model_kind") == deploy_model_kind:
                return summary
        raise RuntimeError(f"Requested deploy model kind not found: {deploy_model_kind}")

    scored: list[tuple[tuple[float, float, float, float], dict]] = []
    for summary in summaries:
        metrics_path = Path(summary["output_dir"]) / "metrics.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")).get("metrics", [])
        test = _find_metric(metrics, str(summary.get("model_kind")), "test")
        key = (
            _metric_value(test, deploy_metric),
            _metric_value(test, "average_precision"),
            _metric_value(test, "roc_auc"),
            -_metric_value(test, "brier") if test.get("brier") is not None else float("-inf"),
        )
        scored.append((key, summary))
    return max(scored, key=lambda item: item[0])[1]


def _run_optional(cmd: list[str], enabled: bool) -> tuple[dict | None, str]:
    if not enabled:
        return None, ""
    return _run(cmd)


def _run_preflight_tests(run_dir: Path, *, enabled: bool) -> tuple[dict | None, str]:
    if not enabled:
        return None, ""
    cmd = [sys.executable, "-m", "pytest", "-q", "test/ScoreRank"]
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT, text=True, capture_output=True)
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    (run_dir / "tests.log").write_text(output, encoding="utf-8")
    if proc.returncode != 0:
        failed = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "status": "failed",
            "failed_stage": "preflight_tests",
            "activation": {"committed": False},
            "command": cmd,
            "exit_code": proc.returncode,
        }
        (run_dir / "cycle_manifest.json").write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        raise RuntimeError(f"Preflight tests failed ({proc.returncode}); active model unchanged.\n{output}")
    return {"passed": True}, output


def _activate_model(summary: dict) -> tuple[Path, dict | None]:
    model_path = Path(str(summary.get("model_path") or ""))
    active_path = MODEL_ROOT / "active_model.json"
    payload = {
        "activated_at": datetime.now().isoformat(timespec="seconds"),
        "model_dir": str(Path(str(summary["output_dir"]))),
        "model_path": str(model_path),
        "target": summary.get("target"),
        "model_kind": summary.get("model_kind"),
        "risk_target": summary.get("risk_target"),
        "feature_schema_hash": summary.get("feature_schema_hash"),
        "selection_source": "run_bs_signal_enhancement_cycle",
    }
    previous = None
    if active_path.exists():
        try:
            previous = json.loads(active_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=MODEL_ROOT, prefix=".active_model.", suffix=".tmp", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        staged_path = Path(handle.name)
    os.replace(staged_path, active_path)
    return active_path, previous


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full B-signal enhancement cycle.")
    parser.add_argument("--target", default="hit_20_10pct")
    parser.add_argument("--risk-target", default=None)
    parser.add_argument(
        "--model-kind",
        default="all",
        choices=["logistic_calibrated", "random_forest", "hist_gradient_boosting", "all"],
    )
    parser.add_argument(
        "--deploy-model-kind",
        default="auto",
        choices=["auto", "logistic_calibrated", "random_forest", "hist_gradient_boosting"],
        help="Model kind to import and use for follow-up reports. auto selects by --deploy-metric.",
    )
    parser.add_argument("--deploy-metric", default="precision_at_20")
    parser.add_argument("--capital-per-trade", type=float, default=100000.0)
    parser.add_argument("--portfolio-capital", type=float, default=1000000.0)
    parser.add_argument("--capacity-ratio", type=float, default=0.02)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--skip-import", action="store_true")
    parser.add_argument("--skip-research", action="store_true")
    parser.add_argument("--skip-reports", action="store_true")
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUN_ROOT / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    # A deterministic code/test failure must happen before exporting data,
    # training models, importing scores, or changing the active model pointer.
    test_summary, test_log = _run_preflight_tests(run_dir, enabled=not args.skip_tests)

    export_summary, export_log = _run([sys.executable, "scripts/export_signal_enhancement_dataset.py"])
    dataset_dir = Path(export_summary["output_dir"])

    research_summary, research_log = _run_optional(
        [sys.executable, "scripts/research_signal_enhancement.py"],
        enabled=not args.skip_research,
    )
    train_cmd = [
        sys.executable,
        "scripts/train_bs_signal_model.py",
        "--dataset-dir",
        str(dataset_dir),
        "--target",
        args.target,
        "--model-kind",
        args.model_kind,
    ]
    if args.risk_target:
        train_cmd.extend(["--risk-target", args.risk_target])
    train_summary, train_log = _run(train_cmd)
    summaries = _model_summaries(train_summary)
    deploy_summary = _select_deploy_model(summaries, args.deploy_model_kind, args.deploy_metric)
    model_dir = Path(deploy_summary["output_dir"])

    report_outputs: dict[str, Any] = {}
    report_logs: dict[str, str] = {}
    if not args.skip_reports:
        rankers, report_logs["rankers"] = _run(
            [
                sys.executable,
                "scripts/evaluate_bs_signal_rankers.py",
                "--dataset-dir",
                str(dataset_dir),
                "--model-dir",
                str(model_dir),
                "--horizon",
                "20",
                "--capital-per-trade",
                str(args.capital_per_trade),
                "--capacity-ratio",
                str(args.capacity_ratio),
                "--write",
            ]
        )
        holding, report_logs["holding"] = _run(
            [
                sys.executable,
                "scripts/evaluate_bs_holding_policy.py",
                "--dataset-dir",
                str(dataset_dir),
                "--model-dir",
                str(model_dir),
                "--horizon",
                "20",
                "--write",
            ]
        )
        portfolio, report_logs["portfolio"] = _run(
            [
                sys.executable,
                "scripts/evaluate_bs_portfolio_risk.py",
                "--dataset-dir",
                str(dataset_dir),
                "--model-dir",
                str(model_dir),
                "--horizon",
                "20",
                "--top-n",
                str(args.top_n),
                "--capital",
                str(args.portfolio_capital),
                "--capacity-ratio",
                str(args.capacity_ratio),
                "--write",
            ]
        )
        report_outputs = {"rankers": rankers, "holding": holding, "portfolio": portfolio}

    check_summary, check_log = _run_optional(
        [
            sys.executable,
            "scripts/check_bs_signal_pipeline.py",
            "--check-db",
            "--dataset-dir",
            str(dataset_dir),
            "--model-dir",
            str(model_dir),
        ],
        enabled=not args.skip_check,
    )
    import_summary = None
    import_log = ""
    if not args.skip_import:
        import_summary, import_log = _run(
            [
                sys.executable,
                "scripts/import_bs_model_scores.py",
                "--model-dir",
                str(model_dir),
            ]
        )

    metrics = _metric_snapshot(model_dir / "metrics.json", str(deploy_summary.get("model_kind")))
    # Persist all non-activation evidence before the commit point.
    (run_dir / "export.log").write_text(export_log, encoding="utf-8")
    (run_dir / "research.log").write_text(research_log, encoding="utf-8")
    (run_dir / "train.log").write_text(train_log, encoding="utf-8")
    (run_dir / "import.log").write_text(import_log, encoding="utf-8")
    (run_dir / "check.log").write_text(check_log, encoding="utf-8")
    for name, log_text in report_logs.items():
        (run_dir / f"{name}.log").write_text(log_text, encoding="utf-8")

    # Commit is deliberately last and uses atomic rename. All validation and
    # downstream report gates above must succeed first.
    active_model_path, previous_active_model = _activate_model(deploy_summary)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "model_kind": args.model_kind,
        "deploy_model_kind": deploy_summary.get("model_kind"),
        "deploy_metric": args.deploy_metric,
        "dataset_dir": str(dataset_dir),
        "dataset_zip": export_summary.get("zip_path"),
        "research_dir": research_summary.get("output_dir") if research_summary else None,
        "model_dir": str(model_dir),
        "active_model": str(active_model_path),
        "activation": {
            "committed": True,
            "previous_model_dir": (previous_active_model or {}).get("model_dir"),
            "new_model_dir": str(model_dir),
        },
        "model_summaries": summaries,
        "model_import": import_summary,
        "metrics": metrics,
        "reports": {
            name: value.get("files") if isinstance(value, dict) else None
            for name, value in report_outputs.items()
        },
        "pipeline_check": check_summary,
        "tests": {"enabled": not args.skip_tests, "passed": test_summary is not None},
        "status": "completed",
    }
    (run_dir / "cycle_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**manifest, "run_dir": str(run_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
