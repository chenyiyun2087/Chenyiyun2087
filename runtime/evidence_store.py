"""Local content-addressed evidence store.

Large immutable evidence lives outside Git while manifests and summaries stay
small and reviewable.  Writes are atomic and every read re-verifies SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "data" / "evidence_store"


@dataclass(frozen=True)
class EvidenceObject:
    sha256: str
    path: Path
    size_bytes: int
    media_type: str


class EvidenceStore:
    def __init__(self, root: Path | str | None = None, *, replica_root: Path | str | None = None,
                 require_replica: bool = False) -> None:
        configured = root or os.environ.get("CHENYIYUN_EVIDENCE_ROOT") or DEFAULT_ROOT
        self.root = Path(configured).expanduser().resolve()
        self.objects_root = self.root / "sha256"
        self.index_path = self.root / "index.sqlite3"
        configured_replica = replica_root or os.environ.get("CHENYIYUN_EVIDENCE_REPLICA_ROOT")
        self.replica_root = Path(configured_replica).expanduser().resolve() if configured_replica else None
        if require_replica and self.replica_root is None:
            raise RuntimeError("evidence_replica_not_configured")
        if self.replica_root == self.root:
            raise ValueError("evidence_replica_must_be_distinct")
        self.replica_objects_root = self.replica_root / "sha256" if self.replica_root else None
        self.objects_root.mkdir(parents=True, exist_ok=True)
        if self.replica_objects_root:
            self.replica_objects_root.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_objects (
                  sha256 TEXT PRIMARY KEY,
                  relative_path TEXT NOT NULL,
                  size_bytes INTEGER NOT NULL,
                  media_type TEXT NOT NULL,
                  source_name TEXT NOT NULL,
                  release_id TEXT,
                  run_id TEXT,
                  coverage_start TEXT,
                  coverage_end TEXT,
                  created_at TEXT NOT NULL,
                  verified_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_identity
                  ON evidence_objects(release_id, run_id);
                CREATE TABLE IF NOT EXISTS evidence_references (
                  sha256 TEXT NOT NULL,
                  release_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  reference_type TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(sha256,release_id,run_id,reference_type),
                  FOREIGN KEY(sha256) REFERENCES evidence_objects(sha256)
                );
                CREATE TABLE IF NOT EXISTS evidence_audit_log (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_type TEXT NOT NULL,
                  sha256 TEXT,
                  detail TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _hash_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _object_path(self, sha256: str) -> Path:
        return self.objects_root / sha256[:2] / sha256[2:]

    def _replica_path(self, sha256: str) -> Path | None:
        return self.replica_objects_root / sha256[:2] / sha256[2:] if self.replica_objects_root else None

    def _copy_verified(self, source: Path, destination: Path, sha256: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if self._hash_path(destination) != sha256:
                raise RuntimeError("evidence_replica_corruption")
            return
        fd, temporary_name = tempfile.mkstemp(prefix="evidence-replica-", dir=destination.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            if self._hash_path(temporary) != sha256:
                raise RuntimeError("evidence_replica_copy_mismatch")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def put_file(
        self,
        source: Path | str,
        *,
        media_type: str = "application/octet-stream",
        release_id: str = "",
        run_id: str = "",
        coverage_start: str = "",
        coverage_end: str = "",
    ) -> EvidenceObject:
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        sha256 = self._hash_path(source_path)
        destination = self._object_path(sha256)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            fd, temporary_name = tempfile.mkstemp(prefix="evidence-", dir=destination.parent)
            os.close(fd)
            temporary = Path(temporary_name)
            try:
                shutil.copyfile(source_path, temporary)
                if self._hash_path(temporary) != sha256:
                    raise RuntimeError("evidence_hash_changed_during_copy")
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        elif self._hash_path(destination) != sha256:
            raise RuntimeError("evidence_store_corruption")
        replica = self._replica_path(sha256)
        if replica is not None:
            self._copy_verified(destination, replica, sha256)
        now = datetime.now(timezone.utc).isoformat()
        relative = str(destination.relative_to(self.root))
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evidence_objects
                   (sha256,relative_path,size_bytes,media_type,source_name,release_id,run_id,
                    coverage_start,coverage_end,created_at,verified_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(sha256) DO UPDATE SET verified_at=excluded.verified_at""",
                (sha256, relative, destination.stat().st_size, media_type, source_path.name,
                 release_id, run_id, coverage_start, coverage_end, now, now),
            )
            if release_id and run_id:
                connection.execute(
                    "INSERT OR IGNORE INTO evidence_references "
                    "(sha256,release_id,run_id,reference_type,created_at) VALUES (?,?,?,?,?)",
                    (sha256, release_id, run_id, "RUN_ARTIFACT", now),
                )
            connection.execute(
                "INSERT INTO evidence_audit_log(event_type,sha256,detail,created_at) VALUES (?,?,?,?)",
                ("PUT", sha256, source_path.name, now),
            )
        return EvidenceObject(sha256, destination, destination.stat().st_size, media_type)

    def put_json(self, payload: object, **metadata: str) -> EvidenceObject:
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="evidence-json-", suffix=".json", dir=self.root)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            temporary.write_bytes(encoded)
            return self.put_file(temporary, media_type="application/json", **metadata)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, sha256: str, *, verify: bool = True) -> EvidenceObject:
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise ValueError("invalid_sha256")
        path = self._object_path(sha256)
        if not path.is_file():
            raise FileNotFoundError(sha256)
        if verify and self._hash_path(path) != sha256:
            raise RuntimeError("evidence_store_corruption")
        replica = self._replica_path(sha256)
        if verify and replica is not None:
            if not replica.is_file() or self._hash_path(replica) != sha256:
                raise RuntimeError("evidence_replica_missing_or_corrupt")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT size_bytes,media_type FROM evidence_objects WHERE sha256=?", (sha256,)
            ).fetchone()
            if row is None:
                raise RuntimeError("evidence_index_missing")
            connection.execute(
                "UPDATE evidence_objects SET verified_at=? WHERE sha256=?",
                (datetime.now(timezone.utc).isoformat(), sha256),
            )
        return EvidenceObject(sha256, path, int(row[0]), str(row[1]))

    def verify_all(self) -> dict[str, object]:
        with self._connect() as connection:
            hashes = [row[0] for row in connection.execute("SELECT sha256 FROM evidence_objects")]
        failures: list[str] = []
        for sha256 in hashes:
            try:
                self.get(sha256)
            except (OSError, RuntimeError, ValueError):
                failures.append(sha256)
        replica_status = "NOT_CONFIGURED" if self.replica_root is None else ("VERIFIED" if not failures else "CORRUPT")
        return {"status": "VERIFIED" if not failures else "CORRUPT", "objects": len(hashes),
                "failures": failures, "replica_status": replica_status}

    def capacity_status(self, *, warn_free_bytes: int = 20 * 1024**3) -> dict[str, object]:
        roots = {"primary": self.root}
        if self.replica_root:
            roots["replica"] = self.replica_root
        details = {}
        blocked = False
        for name, root in roots.items():
            usage = shutil.disk_usage(root)
            details[name] = {"free_bytes": usage.free, "total_bytes": usage.total,
                             "warning": usage.free < warn_free_bytes}
            blocked = blocked or usage.free <= 0
        return {"status": "BLOCKED" if blocked else "WARNING" if any(v["warning"] for v in details.values()) else "READY",
                "volumes": details}

    def remove_orphans(self, referenced_hashes: Iterable[str], *, dry_run: bool = True,
                       approval_id: str = "") -> list[str]:
        referenced = set(referenced_hashes)
        with self._connect() as connection:
            indexed = [row[0] for row in connection.execute("SELECT sha256 FROM evidence_objects")]
            persistent_refs = {row[0] for row in connection.execute("SELECT DISTINCT sha256 FROM evidence_references")}
            orphans = sorted(set(indexed) - referenced - persistent_refs)
            if not dry_run and not approval_id.strip():
                raise RuntimeError("orphan_cleanup_approval_required")
            if not dry_run:
                for sha256 in orphans:
                    self._object_path(sha256).unlink(missing_ok=True)
                    replica = self._replica_path(sha256)
                    if replica:
                        replica.unlink(missing_ok=True)
                    connection.execute("DELETE FROM evidence_objects WHERE sha256=?", (sha256,))
                    connection.execute(
                        "INSERT INTO evidence_audit_log(event_type,sha256,detail,created_at) VALUES (?,?,?,?)",
                        ("DELETE_APPROVED", sha256, approval_id, datetime.now(timezone.utc).isoformat()),
                    )
        return orphans
