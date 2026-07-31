#!/usr/bin/env python3
"""Formal Package Builder v3 — pure transformer, zero DB access.

Reads exclusively from a sealed formal_pit_run_id directory:
  formal_scores.parquet
  factor_panel_daily.parquet (or factor panel from builder)

Produces a Formal Package manifest binding the run ID to all inputs.
Never accesses current score_rank_daily, dim_stock, or any live table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha
from runtime.fail_closed import blocked_report, fail_closed


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_formal_package_v3(
    *,
    formal_pit_run_id: str,
    scores_path: Path,
    factor_panel_path: Path | None = None,
    output_dir: Path,
) -> dict[str, Any]:
    """Build a Formal Package from sealed formal evidence."""
    if not scores_path.exists():
        return blocked_report("formal_package_v3", "input", "scores_not_found")

    scores_sha = _file_sha(scores_path)
    panel_sha = _file_sha(factor_panel_path) if factor_panel_path and factor_panel_path.exists() else None

    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy scores into package (not symlink — sealed copy)
    import shutil
    pkg_scores = output_dir / "scores.parquet"
    shutil.copy2(scores_path, pkg_scores)

    required_objects = {
        "scores.parquet": _file_sha(pkg_scores),
    }
    if factor_panel_path and factor_panel_path.exists():
        pkg_panel = output_dir / "factor_panel.parquet"
        shutil.copy2(factor_panel_path, pkg_panel)
        required_objects["factor_panel.parquet"] = _file_sha(pkg_panel)

    # Compute artifact tree SHA for the package
    tree_sha = canonical_sha({
        rel: sha for rel, sha in sorted(required_objects.items())
    })

    manifest = {
        "schema_version": "formal_package_v3_0",
        "status": "PASS",
        "formal_pit_run_id": formal_pit_run_id,
        "scores_sha256": scores_sha,
        "factor_panel_sha256": panel_sha,
        "required_objects": required_objects,
        "artifact_tree_sha256": tree_sha,
        "capital_authority": False,
    }
    manifest["content_sha256"] = canonical_sha(
        {k: v for k, v in manifest.items() if k != "content_sha256"}
    )
    (output_dir / "package_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--factor-panel", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_formal_package_v3(
        formal_pit_run_id=args.run_id,
        scores_path=args.scores,
        factor_panel_path=args.factor_panel,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
