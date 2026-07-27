"""Fail-closed readiness gate for the immutable 2013-present formal run.

The command validates an already frozen package.  It never silently queries a
different table, derives a calendar from lifecycle rows, or treats missing
objects as empty.  `READY_FOR_FORMAL_RUN` is the only success status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "formal_readiness.yaml"


@dataclass(frozen=True)
class Check:
    check: str
    passed: bool
    actual: str
    required: str


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"symbol": str, "ts_code": str})
    if frame.empty:
        raise ValueError(f"empty_required_object:{path.name}")
    return frame


def _pit_visible(frame: pd.DataFrame, business_column: str) -> tuple[bool, int]:
    if "available_at" not in frame.columns:
        return False, len(frame)
    business = pd.to_datetime(frame[business_column], errors="coerce")
    available = pd.to_datetime(frame["available_at"], errors="coerce", utc=True)
    invalid = (
        business.isna()
        | available.isna()
        | (available.dt.tz_convert("Asia/Shanghai").dt.date > business.dt.date)
    )
    return not bool(invalid.any()), int(invalid.sum())


def _manifest_checks(
    package: Path, config: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[Check]]:
    path = package / "source_manifest.json"
    if not path.exists():
        return None, [
            Check("source_manifest", False, "missing", "present with object SHA256")
        ]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    objects = manifest.get("objects") or {}
    checks: list[Check] = []
    for filename in config["required_objects"]:
        if filename == "source_manifest.json":
            continue
        target = package / filename
        expected = str((objects.get(filename) or {}).get("sha256") or "")
        actual = _sha(target) if target.is_file() else "MISSING"
        checks.append(
            Check(
                f"object_sha:{filename}",
                bool(expected) and actual == expected,
                actual,
                expected or "declared SHA256",
            )
        )
    checks.append(
        Check(
            "calendar_source_manifest",
            manifest.get("calendar_source")
            == config["authoritative_calendar"]["source"],
            str(manifest.get("calendar_source")),
            str(config["authoritative_calendar"]["source"]),
        )
    )
    checks.append(
        Check(
            "coverage_start_manifest",
            str(manifest.get("coverage_start") or "9999-12-31")
            <= str(config["minimum_start_date"]),
            str(manifest.get("coverage_start")),
            f"<= {config['minimum_start_date']}",
        )
    )
    return manifest, checks


def evaluate_package(package: Path, config: dict[str, Any]) -> dict[str, Any]:
    checks: list[Check] = []
    required = [str(name) for name in config["required_objects"]]
    missing = [name for name in required if not (package / name).is_file()]
    checks.append(
        Check(
            "required_objects",
            not missing,
            "complete" if not missing else ",".join(missing),
            "all required frozen objects present",
        )
    )
    if missing:
        return _result(package, config, checks)

    manifest, manifest_checks = _manifest_checks(package, config)
    checks.extend(manifest_checks)
    if manifest is None:
        return _result(package, config, checks)

    frames = {
        filename: _read_frame(package / filename)
        for filename in required
        if filename.endswith(".csv")
    }
    for filename, primary_key in (config.get("primary_keys") or {}).items():
        frame = frames[filename]
        absent = sorted(set(primary_key) - set(frame.columns))
        duplicate_count = (
            int(frame.duplicated(list(primary_key)).sum()) if not absent else len(frame)
        )
        checks.append(
            Check(
                f"schema_and_duplicates:{filename}",
                not absent and duplicate_count == 0,
                f"missing={absent};duplicates={duplicate_count}",
                "required primary-key columns present; duplicates=0",
            )
        )

    calendar = frames["trade_calendar.csv"].copy()
    expected_calendar = config["authoritative_calendar"]
    source_ok = (
        "source" in calendar.columns
        and calendar["source"].astype(str).eq(expected_calendar["source"]).all()
    )
    exchange_ok = calendar["exchange"].astype(str).eq(expected_calendar["exchange"]).all()
    checks.append(
        Check(
            "authoritative_sse_calendar",
            bool(source_ok and exchange_ok),
            f"source_ok={source_ok};exchange_ok={exchange_ok}",
            f"{expected_calendar['source']} / {expected_calendar['exchange']}",
        )
    )
    calendar["cal_date"] = pd.to_datetime(calendar["cal_date"], errors="coerce")
    open_dates = set(
        calendar.loc[
            pd.to_numeric(calendar["is_open"], errors="coerce").eq(1), "cal_date"
        ].dt.strftime("%Y-%m-%d")
    )
    checks.append(
        Check(
            "calendar_open_dates",
            bool(open_dates),
            str(len(open_dates)),
            "at least one authoritative open date",
        )
    )

    business_columns = {
        "trade_calendar.csv": "cal_date",
        "tradable_universe.csv": "trade_date",
        "scores.csv": "trade_date",
        "prices.csv": "trade_date",
        "adjustment_factors.csv": "trade_date",
        "corporate_actions.csv": "event_date",
        "security_lifecycle.csv": "effective_from",
    }
    for filename, business_column in business_columns.items():
        passed, invalid = _pit_visible(frames[filename], business_column)
        checks.append(
            Check(
                f"pit_visibility:{filename}",
                passed,
                f"invalid_rows={invalid}",
                "available_at date <= business date",
            )
        )

    universe = frames["tradable_universe.csv"].copy()
    universe["trade_date"] = pd.to_datetime(
        universe["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    universe = universe[
        pd.to_numeric(universe["is_tradable"], errors="coerce").eq(1)
    ]
    denominators = universe.groupby("trade_date")["symbol"].nunique()
    scores = frames["scores.csv"].copy()
    scores["trade_date"] = pd.to_datetime(
        scores["trade_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    valid_scores = scores[scores["score"].notna()]
    threshold = float(config["minimum_score_coverage"])
    strategy_dates: list[set[str]] = []
    min_ratio = 1.0
    coverage_failures: list[str] = []
    for strategy in config["strategies"]:
        scoped = valid_scores[valid_scores["strategy"].astype(str).eq(strategy)]
        numerators = scoped.groupby("trade_date")["symbol"].nunique()
        ratios = numerators.reindex(denominators.index, fill_value=0) / denominators
        strategy_dates.append(set(scoped["trade_date"]))
        local_min = float(ratios.min()) if len(ratios) else 0.0
        min_ratio = min(min_ratio, local_min)
        failed_dates = ratios[ratios.lt(threshold)].index.tolist()
        coverage_failures.extend(f"{strategy}:{item}" for item in failed_dates)
    checks.append(
        Check(
            "daily_pit_score_coverage",
            not coverage_failures and min_ratio >= threshold,
            f"minimum_ratio={min_ratio:.6f};failed_dates={len(coverage_failures)}",
            f"valid scored / PIT tradable >= {threshold:.2%} for every strategy/date",
        )
    )
    common_dates = set.intersection(*strategy_dates) if strategy_dates else set()
    missing_common = sorted(open_dates - common_dates)
    checks.append(
        Check(
            "five_strategy_common_dates",
            not missing_common,
            f"common={len(common_dates)};missing_open_dates={len(missing_common)}",
            "all authoritative open dates shared by five strategies",
        )
    )

    lifecycle_symbols = set(frames["security_lifecycle.csv"]["symbol"].astype(str))
    universe_symbols = set(universe["symbol"].astype(str))
    missing_lifecycle = sorted(universe_symbols - lifecycle_symbols)
    checks.append(
        Check(
            "security_lifecycle_coverage",
            not missing_lifecycle,
            f"missing_symbols={len(missing_lifecycle)}",
            "100% of PIT tradable symbols",
        )
    )
    ca_complete = bool(manifest.get("corporate_action_complete"))
    lifecycle_complete = bool(manifest.get("security_lifecycle_complete"))
    checks.extend(
        [
            Check(
                "corporate_action_snapshot_complete",
                ca_complete,
                str(ca_complete),
                "true / source snapshot complete",
            ),
            Check(
                "security_lifecycle_snapshot_complete",
                lifecycle_complete,
                str(lifecycle_complete),
                "true / source snapshot complete",
            ),
        ]
    )
    account = json.loads((package / "initial_account.json").read_text(encoding="utf-8"))
    account_ok = (
        account.get("currency") == "CNY"
        and float(account.get("initial_cash_cny") or 0) > 0
        and not account.get("positions")
    )
    checks.append(
        Check(
            "initial_account_state",
            account_ok,
            json.dumps(account, ensure_ascii=False, sort_keys=True),
            "CNY, positive initial cash, empty positions",
        )
    )
    return _result(package, config, checks)


def _result(
    package: Path, config: dict[str, Any], checks: list[Check]
) -> dict[str, Any]:
    passed = bool(checks) and all(item.passed for item in checks)
    status = config["success_status"] if passed else "BLOCKED"
    payload: dict[str, Any] = {
        "schema_version": config["schema_version"],
        "status": status,
        "ready_for_formal_run": passed,
        "package": str(package),
        "checks": [asdict(item) for item in checks],
        "blocking_checks": [item.check for item in checks if not item.passed],
    }
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    if not args.package.exists():
        result = _result(
            args.package,
            config,
            [
                Check(
                    "frozen_package",
                    False,
                    "missing",
                    "immutable PIT package generated from read-only sources",
                )
            ],
        )
    else:
        result = evaluate_package(args.package, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == config["success_status"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
