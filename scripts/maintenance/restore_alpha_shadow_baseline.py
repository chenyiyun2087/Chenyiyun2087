#!/usr/bin/env python3
"""Restore the Alpha Shadow baseline snapshots from a verified formal package.

Run: .venv/bin/python scripts/maintenance/restore_alpha_shadow_baseline.py
Needs: an immutable PASS package under exports/formal_packages; no credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORMAL_PACKAGES_ROOT = PROJECT_ROOT / "exports" / "formal_packages"
TARGET_ROOT = (
    PROJECT_ROOT / "exports" / "formal_evidence" / "alpha_challengers" / "f1_no_value"
)
REQUIRED_OBJECTS = (
    "trade_calendar.csv",
    "prices.csv",
    "tradable_universe.csv",
    "scores.parquet",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_verified_package(package_id: str | None = None) -> tuple[Path, dict]:
    candidates = [FORMAL_PACKAGES_ROOT / package_id] if package_id else sorted(
        (path.parent for path in FORMAL_PACKAGES_ROOT.glob("*/package_manifest.json")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for package_dir in candidates:
        manifest_path = package_dir / "package_manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "PASS":
            continue
        objects = manifest.get("required_objects") or {}
        if all(name in objects and (package_dir / name).is_file() for name in REQUIRED_OBJECTS):
            return package_dir, manifest
    raise RuntimeError("no verified PASS formal package contains the Alpha Shadow baseline objects")


def verify_sources(package_dir: Path, manifest: dict) -> None:
    objects = manifest["required_objects"]
    for name in REQUIRED_OBJECTS:
        expected = str(objects[name]["sha256"])
        actual = sha256_file(package_dir / name)
        if actual != expected:
            raise RuntimeError(f"formal package hash mismatch: {name}")


def restore(package_dir: Path, *, force: bool = False) -> dict:
    snapshots_dir = TARGET_ROOT / "snapshots"
    scores_dir = TARGET_ROOT / "scores"
    outputs = {
        "trade_calendar": snapshots_dir / "trade_calendar.csv",
        "prices": snapshots_dir / "prices.parquet",
        "universe": snapshots_dir / "tradable_universe.parquet",
        "scores": scores_dir / "formal_scores.parquet",
    }
    existing = [path for path in outputs.values() if path.exists()]
    if len(existing) == len(outputs) and not force:
        prices = pd.read_parquet(outputs["prices"], columns=["trade_date", "symbol"])
        universe = pd.read_parquet(outputs["universe"], columns=["trade_date", "symbol"])
        return {
            "status": "ALREADY_PRESENT",
            "source_package": package_dir.name,
            "outputs": {name: str(path) for name, path in outputs.items()},
            "rows": {"prices": int(len(prices)), "universe": int(len(universe))},
        }
    if existing and not force:
        raise RuntimeError(
            "partial baseline exists; inspect it or rerun with --force: "
            f"{[str(path) for path in existing]}"
        )

    snapshots_dir.mkdir(parents=True, exist_ok=True)
    scores_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package_dir / "trade_calendar.csv", outputs["trade_calendar"])
    shutil.copy2(package_dir / "scores.parquet", outputs["scores"])

    prices = pd.read_csv(
        package_dir / "prices.csv",
        dtype={"symbol": "string", "trade_date": "string"},
    )
    prices["symbol"] = prices["symbol"].str.zfill(6)
    prices = prices.rename(
        columns={"open": "raw_open", "pre_close": "raw_pre_close", "close": "raw_close"}
    )
    prices.to_parquet(outputs["prices"], index=False)

    universe = pd.read_csv(
        package_dir / "tradable_universe.csv",
        dtype={"symbol": "string", "trade_date": "string"},
    )
    universe["symbol"] = universe["symbol"].str.zfill(6)
    universe.to_parquet(outputs["universe"], index=False)
    return {
        "status": "RESTORED",
        "source_package": package_dir.name,
        "outputs": {name: str(path) for name, path in outputs.items()},
        "rows": {
            "prices": int(len(prices)),
            "universe": int(len(universe)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-id", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    package_dir, manifest = select_verified_package(args.package_id)
    verify_sources(package_dir, manifest)
    print(json.dumps(restore(package_dir, force=args.force), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
