from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = PROJECT_ROOT / "exports" / "bs_signal_cycles"


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


def _metric_snapshot(metrics_path: Path) -> dict:
    data = json.loads(metrics_path.read_text(encoding="utf-8"))
    out = {"summary": data.get("summary", {})}
    for row in data.get("metrics", []):
        if row.get("model") == "logistic_calibrated" and row.get("split") == "test":
            out["test_logistic"] = {
                "roc_auc": row.get("roc_auc"),
                "average_precision": row.get("average_precision"),
                "brier": row.get("brier"),
                "precision_at_10": row.get("precision_at_10"),
                "precision_at_20": row.get("precision_at_20"),
                "precision_at_30": row.get("precision_at_30"),
            }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full B-signal enhancement cycle.")
    parser.add_argument("--target", default="hit_20_10pct")
    parser.add_argument("--skip-import", action="store_true")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = RUN_ROOT / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    export_summary, export_log = _run([sys.executable, "scripts/export_signal_enhancement_dataset.py"])
    dataset_dir = Path(export_summary["output_dir"])

    research_summary, research_log = _run([sys.executable, "scripts/research_signal_enhancement.py"])
    train_summary, train_log = _run(
        [
            sys.executable,
            "scripts/train_bs_signal_model.py",
            "--dataset-dir",
            str(dataset_dir),
            "--target",
            args.target,
        ]
    )
    model_dir = Path(train_summary["summary"]["output_dir"])

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

    metrics = _metric_snapshot(model_dir / "metrics.json")
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "dataset_dir": str(dataset_dir),
        "dataset_zip": export_summary.get("zip_path"),
        "research_dir": research_summary.get("output_dir"),
        "model_dir": str(model_dir),
        "model_import": import_summary,
        "metrics": metrics,
        "status": "completed",
    }
    (run_dir / "cycle_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "export.log").write_text(export_log, encoding="utf-8")
    (run_dir / "research.log").write_text(research_log, encoding="utf-8")
    (run_dir / "train.log").write_text(train_log, encoding="utf-8")
    (run_dir / "import.log").write_text(import_log, encoding="utf-8")
    print(json.dumps({**manifest, "run_dir": str(run_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
