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
    def __init__(self, root: Path | str | None = None) -> None:
        configured = root or os.environ.get("CHENYIYUN_EVIDENCE_ROOT") or DEFAULT_ROOT
        self.root = Path(configured).expanduser().resolve()
        self.objects_root = self.root / "sha256"
        self.index_path = self.root / "index.sqlite3"
        self.objects_root.mkdir(parents=True, exist_ok=True)
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
        return {"status": "VERIFIED" if not failures else "CORRUPT", "objects": len(hashes), "failures": failures}

    def remove_orphans(self, referenced_hashes: Iterable[str], *, dry_run: bool = True) -> list[str]:
        referenced = set(referenced_hashes)
        with self._connect() as connection:
            indexed = [row[0] for row in connection.execute("SELECT sha256 FROM evidence_objects")]
            orphans = sorted(set(indexed) - referenced)
            if not dry_run:
                for sha256 in orphans:
                    self._object_path(sha256).unlink(missing_ok=True)
                    connection.execute("DELETE FROM evidence_objects WHERE sha256=?", (sha256,))
        return orphans
