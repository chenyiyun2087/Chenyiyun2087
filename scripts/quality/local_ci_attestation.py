"""Run a reproducible local CI fallback when hosted runners are unavailable.

This is an evidence-producing runner, not a GitHub Actions impersonator.  It
records the exact commit, Python runtime, commands, return codes, durations and
output hashes.  A failed command always yields a failed attestation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=PROJECT_ROOT, text=True
    ).strip()


def _run(name: str, command: list[str]) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = completed.stdout or ""
    return {
        "name": name,
        "command": command,
        "return_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_sha256": _sha_text(output),
        "output_tail": output[-4000:],
        "passed": completed.returncode == 0,
    }


def build_commands(
    python: str,
    targets: list[str],
    *,
    full: bool,
) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = [
        (
            "compile_production_modules",
            [
                python,
                "-m",
                "compileall",
                "-q",
                "runtime",
                "scripts/ops",
                "scripts/research",
                "web",
            ],
        ),
        (
            "fast_gate",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "test/test_trusted_production_closure.py",
                "test/test_p0_trustworthiness.py",
                "test/test_production_config_consistency.py",
                "test/test_production_health_order_gating.py",
                "test/test_quant_validation_v2.py",
                "regression/tests",
            ],
        ),
        (
            "credential_scan",
            [python, "scripts/ops/check_no_hardcoded_db_credentials.py"],
        ),
    ]
    if targets:
        commands.append(("pr_targeted_tests", [python, "-m", "pytest", "-q", *targets]))
    if full:
        commands.append(
            (
                "full_non_slow_non_integration",
                [
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "-m",
                    "not slow and not integration",
                ],
            )
        )
    return commands


def run_attestation(
    *,
    python: str,
    targets: list[str],
    full: bool,
) -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    tree_status = _git("status", "--porcelain")
    # PR-H3: Capture diff SHA and untracked files
    diff_sha = _sha_text(_git("diff", "HEAD") or "")
    untracked = [
        line[3:] for line in _git("ls-files", "--others", "--exclude-standard").split("\n")
        if line.strip()
    ] if _git("ls-files", "--others", "--exclude-standard").strip() else []
    checks = [
        _run(name, command)
        for name, command in build_commands(python, targets, full=full)
    ]
    # PR-H3: Post-run worktree check
    tree_status_after = _git("status", "--porcelain")
    head_after = _git("rev-parse", "HEAD")
    diff_sha_after = _sha_text(_git("diff", "HEAD") or "")

    all_tests_passed = all(item["passed"] for item in checks)
    worktree_clean_before = not bool(tree_status)
    worktree_clean_after = not bool(tree_status_after)
    head_unchanged = head == head_after
    credential_ok = not any(
        "credential" in item.get("name", "").lower() and not item["passed"]
        for item in checks
    )

    # PR-H3: Classify attestation
    if not all_tests_passed:
        attestation_status = "FAIL"
    elif not (worktree_clean_before and worktree_clean_after and head_unchanged):
        attestation_status = "DEVELOPMENT_PASS"
    else:
        attestation_status = "RELEASE_PASS"

    payload: dict[str, Any] = {
        "schema_version": "local_ci_attestation_v2",
        "status": attestation_status,
        "all_tests_passed": all_tests_passed,
        "scope": "LOCAL_REPRODUCIBLE_FALLBACK",
        "hosted_ci_status": "UNAVAILABLE_BILLING",
        "git_commit_sha_before": head,
        "git_commit_sha_after": head_after,
        "git_tree_clean_before": worktree_clean_before,
        "git_tree_clean_after": worktree_clean_after,
        "git_head_unchanged": head_unchanged,
        "git_diff_sha256_before": diff_sha,
        "git_diff_sha256_after": diff_sha_after,
        "untracked_files": untracked,
        "python_executable": python,
        "python_version": platform.python_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
    payload["attestation_sha256"] = _sha_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_attestation(
        python=args.python,
        targets=args.target,
        full=args.full,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
