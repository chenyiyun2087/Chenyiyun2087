#!/usr/bin/env python3
"""Configure main branch protection only after two confirmed green runs."""

from __future__ import annotations

import argparse
import json
import subprocess


REQUIRED_CONTEXTS = [
    "fast-gate",
    "production-core",
    "strict-ledger",
    "integration-test",
    "fixtures",
    "full-regression",
    "secret-scan",
]


def payload() -> dict:
    return {
        "required_status_checks": {"strict": True, "contexts": REQUIRED_CONTEXTS},
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 1,
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="chenyiyun2087/Chenyiyun2087")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--confirmed-green-run", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if len(set(args.confirmed_green_run)) < 2:
        raise SystemExit("two distinct confirmed green workflow run IDs are required")
    body = json.dumps(payload(), separators=(",", ":"))
    if not args.execute:
        print(json.dumps(payload(), indent=2))
        return
    subprocess.run(
        ["gh", "api", "--method", "PUT", f"repos/{args.repo}/branches/{args.branch}/protection", "--input", "-"],
        input=body, text=True, check=True,
    )


if __name__ == "__main__":
    main()
