#!/usr/bin/env python3
"""Verify, package, restore, and garbage-collect the local evidence store."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.evidence_store import EvidenceStore


def _referenced_hashes(manifest_paths: list[Path]) -> set[str]:
    values: set[str] = set()
    for path in manifest_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        encoded = json.dumps(payload)
        import re
        values.update(re.findall(r"\b[0-9a-f]{64}\b", encoded))
    return values


def package_store(store: EvidenceStore, destination: Path) -> None:
    result = store.verify_all()
    if result["status"] != "VERIFIED":
        raise RuntimeError("cannot package corrupt evidence store")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        archive.add(store.root, arcname="evidence_store", recursive=True)


def restore_store(store: EvidenceStore, archive_path: Path) -> None:
    verification = store.verify_all()
    object_files = [path for path in store.objects_root.rglob("*") if path.is_file()]
    if verification["objects"] or object_files:
        raise RuntimeError("restore target must be empty")
    store.index_path.unlink(missing_ok=True)
    shutil.rmtree(store.objects_root, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix="evidence-restore-") as temporary_name:
        temporary = Path(temporary_name)
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                resolved = (temporary / member.name).resolve()
                if temporary.resolve() not in resolved.parents and resolved != temporary.resolve():
                    raise RuntimeError("unsafe archive member")
            archive.extractall(temporary)
        source = temporary / "evidence_store"
        if not source.is_dir():
            raise RuntimeError("archive missing evidence_store root")
        shutil.copytree(source, store.root, dirs_exist_ok=True)
    verified = EvidenceStore(store.root).verify_all()
    if verified["status"] != "VERIFIED":
        raise RuntimeError("restored evidence store failed verification")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    package = sub.add_parser("package")
    package.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--input", type=Path, required=True)
    gc = sub.add_parser("gc")
    gc.add_argument("--manifest", type=Path, action="append", default=[])
    gc.add_argument("--execute", action="store_true")
    gc.add_argument("--approval-id", default="")
    args = parser.parse_args()
    store = EvidenceStore(args.root)
    if args.command == "verify":
        result = store.verify_all()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "VERIFIED":
            raise SystemExit(2)
    elif args.command == "package":
        package_store(store, args.output)
    elif args.command == "restore":
        restore_store(store, args.input)
    elif args.command == "gc":
        orphans = store.remove_orphans(_referenced_hashes(args.manifest), dry_run=not args.execute,
                                       approval_id=args.approval_id)
        print(json.dumps({"dry_run": not args.execute, "orphans": orphans}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
