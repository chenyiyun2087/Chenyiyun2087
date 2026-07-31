#!/usr/bin/env python3
"""Produce real, frozen v4.2 evidence inputs without granting promotion.

The benchmark builder retrieves three public index histories and writes the
exact raw responses before constructing a normalized NAV panel.  Factor and
PIT builders inspect available local inputs and fail closed when the source
contracts cannot be met.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.acceptance_config import canonical_sha, load_validation_profile


SHANGHAI = ZoneInfo("Asia/Shanghai")
INDEX_SYMBOLS = {
    "000300.SH": "sh000300",
    "000905.SH": "sh000905",
    "000852.SH": "sh000852",
}
DEFAULT_PROGRAM = PROJECT_ROOT / "config" / "dynamic_champion_live_program.yaml"


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bytes_sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def fetch_url_without_environment_proxy(url: str, timeout: int) -> bytes:
    """Fetch one public source directly; credentials and cookies are forbidden."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--max-time",
            str(timeout),
            "--retry",
            "3",
            "--retry-all-errors",
            "--retry-delay",
            "1",
            url,
        ],
        check=False,
        capture_output=True,
        env=environment,
    )
    if result.returncode != 0:
        raise ConnectionError(
            f"public_source_fetch_failed:curl_exit_{result.returncode}"
        )
    return result.stdout


def _benchmark_url(
    endpoint: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "param": f"{symbol},day,{start_date},{end_date},640",
        }
    )
    return f"{endpoint}?{query}"


def build_benchmark_evidence(
    output_dir: Path,
    profile: dict[str, Any],
    *,
    release_id: str,
    strategy_id: str,
    start_date: str,
    end_date: str,
    retrieved_at: datetime,
    fetcher: Callable[[str, int], bytes] = fetch_url_without_environment_proxy,
) -> dict[str, Any]:
    config = profile["evidence_production"]
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    blockers: list[str] = []
    for code in profile["benchmarks"]["required"]:
        symbol = INDEX_SYMBOLS[str(code)]
        parsed: list[tuple[str, float]] = []
        raw_assets: list[dict[str, Any]] = []
        start_year = int(start_date[:4])
        end_year = int(end_date[:4])
        for year in range(start_year, end_year + 1):
            chunk_start = max(start_date, f"{year}-01-01")
            chunk_end = min(end_date, f"{year}-12-31")
            url = _benchmark_url(
                str(config["benchmark_provider_endpoint"]),
                symbol,
                chunk_start,
                chunk_end,
            )
            try:
                raw = fetcher(url, int(config["request_timeout_seconds"]))
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:
                blockers.append(
                    f"{code}:{year}:provider_fetch_failed:{type(exc).__name__}"
                )
                break
            raw_path = raw_dir / f"{str(code).replace('.', '_')}_{year}.json"
            raw_path.write_bytes(raw)
            klines = (
                ((payload.get("data") or {}).get(symbol) or {}).get("day")
                or []
            )
            if not klines:
                blockers.append(f"{code}:{year}:provider_response_empty")
                break
            raw_assets.append(
                {
                    "year": year,
                    "url": url,
                    "raw_path": str(raw_path),
                    "raw_sha256": _bytes_sha(raw),
                    "rows": len(klines),
                }
            )
            for fields in klines:
                try:
                    parsed.append((str(fields[0]), float(fields[2])))
                except (IndexError, TypeError, ValueError):
                    blockers.append(f"{code}:{year}:provider_row_parse_failed")
                    parsed = []
                    break
            if not parsed:
                break
        if not parsed or any(value <= 0 for _, value in parsed):
            blockers.append(f"{code}:invalid_close_history")
            continue
        parsed = sorted(dict(parsed).items())
        raw_sha = canonical_sha(
            [asset["raw_sha256"] for asset in raw_assets]
        )
        first_close = parsed[0][1]
        rows.extend(
            {
                "benchmark": str(code),
                "trade_date": trade_date,
                "nav": close / first_close,
                "available_at": (
                    f"{trade_date}T{config['benchmark_available_time']}"
                ),
                "close": close,
                "source": str(config["benchmark_provider"]),
                "source_sha256": raw_sha,
                "release_id": release_id,
                "strategy_id": strategy_id,
            }
            for trade_date, close in parsed
        )
        sources.append(
            {
                "benchmark": str(code),
                "raw_assets": raw_assets,
                "source_bundle_sha256": raw_sha,
                "rows": len(parsed),
                "first_trade_date": parsed[0][0],
                "last_trade_date": parsed[-1][0],
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values(["benchmark", "trade_date"]).reset_index(drop=True)
    required_columns = [str(value) for value in config["required_output_columns"]]
    if any(column not in frame for column in required_columns):
        blockers.append("normalized_benchmark_columns_missing")
    required_codes = [str(value) for value in profile["benchmarks"]["required"]]
    present = set(frame.get("benchmark", pd.Series(dtype=str)).astype(str))
    if set(required_codes) != present:
        blockers.append("required_benchmark_codes_incomplete")
    if not frame.empty and frame.duplicated(["benchmark", "trade_date"]).any():
        blockers.append("normalized_benchmark_dates_duplicate")
    date_sets = {
        code: set(frame.loc[frame["benchmark"] == code, "trade_date"])
        for code in required_codes
        if "benchmark" in frame
    }
    aligned = set.intersection(*date_sets.values()) if date_sets else set()
    if len(aligned) < int(profile["alpha_proof"]["min_aligned_trading_days"]):
        blockers.append("normalized_benchmark_alignment_too_short")
    output_path = output_dir / "benchmark_nav_daily.csv"
    frame.to_csv(output_path, index=False)
    deterministic = {
        "release_id": release_id,
        "strategy_id": strategy_id,
        "start_date": start_date,
        "end_date": end_date,
        "source_files": sources,
        "normalized_path": str(output_path),
        "normalized_sha256": _file_sha(output_path),
        "normalized_schema_sha256": canonical_sha(
            {"columns": list(frame.columns), "dtypes": frame.dtypes.astype(str).to_dict()}
        ),
        "row_count": int(len(frame)),
        "aligned_trading_days": len(aligned),
    }
    manifest = {
        "schema_version": "alpha_v4_2_benchmark_builder_v1",
        "status": "PRODUCED" if not blockers else "BLOCKED",
        "evidence_level_candidate": "E3" if not blockers else "E0",
        "retrieved_at": retrieved_at.isoformat(),
        "availability_semantics": (
            "public_daily_index_close_available_after_trade_date_market_close;"
            "retrieval_time_recorded_separately"
        ),
        **deterministic,
        "blockers": sorted(set(blockers)),
        "content_sha256": canonical_sha(deterministic),
        "automatic_promotion_allowed": False,
        "capital_authority": False,
    }
    _write_json(output_dir / "benchmark_evidence_manifest.json", manifest)
    return manifest


def build_factor_evidence_report(
    project_root: Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    paths = sorted((project_root / "data" / "processed").glob(
        "bs_training_dataset*.parquet"
    ))
    rows = []
    for path in paths:
        try:
            frame = pd.read_parquet(path)
        except Exception as exc:
            rows.append(
                {
                    "path": str(path),
                    "status": "BLOCKED",
                    "blockers": [f"unreadable:{type(exc).__name__}"],
                }
            )
            continue
        required_factors = [
            str(value) for value in profile["attribution"]["required_factors"]
        ]
        required_times = ["signal_time"] + [
            f"{factor}_available_at" for factor in required_factors
        ]
        date_column = next(
            (name for name in ("trade_date", "signal_date", "date") if name in frame),
            None,
        )
        unique_dates = (
            int(pd.to_datetime(frame[date_column], errors="coerce").nunique())
            if date_column
            else 0
        )
        blockers = []
        if any(factor not in frame for factor in required_factors):
            blockers.append("formal_factor_columns_missing")
        if any(column not in frame for column in required_times):
            blockers.append("factor_time_contract_missing")
        if unique_dates < 252:
            blockers.append("factor_history_too_short")
        blockers.append("formal_pit_source_not_proven")
        rows.append(
            {
                "path": str(path),
                "file_sha256": _file_sha(path),
                "rows": int(len(frame)),
                "unique_dates": unique_dates,
                "status": "BLOCKED",
                "blockers": blockers,
            }
        )
    return {
        "schema_version": "alpha_v4_2_factor_builder_v1",
        "status": "BLOCKED",
        "evidence_level_candidate": "E0",
        "rows": rows,
        "outputs": [],
        "blockers": ["no_local_source_can_produce_formal_factor_or_ic_evidence"],
        "capital_authority": False,
    }


def build_pit_minimum_report(
    project_root: Path,
    profile: dict[str, Any],
    *,
    release_id: str,
    strategy_id: str,
) -> dict[str, Any]:
    paths = sorted((project_root / "exports" / "pit_forward" / "runs").glob(
        "**/*.json"
    ))
    latest = paths[-1] if paths else None
    source = {}
    if latest is not None:
        try:
            source = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            source = {}
    manifest = source.get("manifest") if isinstance(source, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    components = manifest.get("components") or {}
    text = json.dumps(components, ensure_ascii=False).lower()
    required = [
        str(value) for value in profile["evidence_production"]["pit_minimum_components"]
    ]
    observed = {
        "listing_lifecycle": "listing" in text and "delist" in text,
        "st_status": "st" in text,
        "suspension": "suspension" in text,
        "financial_announcement_time": (
            "financial" in text and ("announcement" in text or "disclosure" in text)
        ),
    }
    blockers = []
    if not bool(manifest.get("formal_pit_eligible")):
        blockers.append("formal_pit_eligible_false")
    if str(manifest.get("release_id") or manifest.get("release") or "") != release_id:
        blockers.append("release_mismatch")
    if str(manifest.get("strategy_id") or manifest.get("strategy") or "") != strategy_id:
        blockers.append("strategy_mismatch")
    blockers.extend(
        f"component_missing:{name}" for name in required if not observed.get(name)
    )
    return {
        "schema_version": "alpha_v4_2_pit_minimum_builder_v1",
        "status": "PRODUCED" if not blockers else "BLOCKED",
        "evidence_level_candidate": "E3" if not blockers else "E0",
        "source_path": str(latest or ""),
        "source_sha256": _file_sha(latest) if latest else "",
        "release_id": release_id,
        "strategy_id": strategy_id,
        "required_components": required,
        "observed_components": observed,
        "blockers": blockers,
        "capital_authority": False,
    }


def write_evidence_production_package(
    output_dir: Path,
    program_path: Path,
    *,
    profile_name: str = "alpha_v4_2",
    start_date: str | None = None,
    end_date: str,
    retrieved_at: datetime | None = None,
    fetcher: Callable[[str, int], bytes] = fetch_url_without_environment_proxy,
) -> dict[str, Any]:
    profile = load_validation_profile(profile_name)
    program = yaml.safe_load(program_path.read_text(encoding="utf-8")) or {}
    release_id = str(program["release_id"])
    strategy_id = str(program["strategy_id"])
    retrieved_at = retrieved_at or datetime.now(SHANGHAI)
    start_date = start_date or str(
        profile["evidence_production"]["benchmark_start_date"]
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark = build_benchmark_evidence(
        output_dir / "benchmark",
        profile,
        release_id=release_id,
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        retrieved_at=retrieved_at,
        fetcher=fetcher,
    )
    factor = build_factor_evidence_report(PROJECT_ROOT, profile)
    pit = build_pit_minimum_report(
        PROJECT_ROOT,
        profile,
        release_id=release_id,
        strategy_id=strategy_id,
    )
    _write_json(output_dir / "factor_evidence_builder_report.json", factor)
    _write_json(output_dir / "pit_minimum_builder_report.json", pit)
    summary_material = {
        "release_id": release_id,
        "strategy_id": strategy_id,
        "benchmark_status": benchmark["status"],
        "benchmark_manifest_sha256": _file_sha(
            output_dir / "benchmark" / "benchmark_evidence_manifest.json"
        ),
        "factor_status": factor["status"],
        "factor_sha256": _file_sha(
            output_dir / "factor_evidence_builder_report.json"
        ),
        "pit_status": pit["status"],
        "pit_sha256": _file_sha(output_dir / "pit_minimum_builder_report.json"),
    }
    summary = {
        "schema_version": "alpha_v4_2_evidence_production_summary_v1",
        "status": (
            "PASS"
            if all(
                status == "PRODUCED"
                for status in (
                    benchmark["status"],
                    factor["status"],
                    pit["status"],
                )
            )
            else "PARTIAL"
        ),
        **summary_material,
        "content_sha256": canonical_sha(summary_material),
        "capital_authority": False,
        "broker_action_allowed": False,
    }
    _write_json(output_dir / "evidence_production_report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    parser.add_argument("--profile", default="alpha_v4_2")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = write_evidence_production_package(
        args.output_dir,
        args.program,
        profile_name=args.profile,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
