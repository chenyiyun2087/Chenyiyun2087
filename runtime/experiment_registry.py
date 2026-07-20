"""Pre-registration and immutable Test-view audit for research experiments."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExperimentRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str
    hypothesis: str
    parameter_space: dict[str, Any]
    data_snapshot_sha: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_direction: str
    decision_criteria: dict[str, Any]
    registered_at: datetime
    git_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

    def fingerprint(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


class ExperimentAuditLog:
    """Append-only JSONL audit. Existing registrations cannot be replaced."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def register(self, registration: ExperimentRegistration) -> None:
        existing = [event for event in self._events() if event.get("event") == "REGISTERED" and event.get("experiment_id") == registration.experiment_id]
        if existing:
            if existing[0].get("registration_sha") != registration.fingerprint():
                raise RuntimeError("experiment_registration_immutable_conflict")
            return
        self._append({"event": "REGISTERED", "experiment_id": registration.experiment_id,
                      "registration_sha": registration.fingerprint(), "payload": registration.model_dump(mode="json")})

    def record_test_view(self, experiment_id: str, result_sha: str) -> int:
        if len(result_sha) != 64:
            raise ValueError("test_result_sha_invalid")
        if not any(event.get("event") == "REGISTERED" and event.get("experiment_id") == experiment_id for event in self._events()):
            raise RuntimeError("experiment_not_preregistered")
        count = sum(event.get("event") == "TEST_VIEWED" and event.get("experiment_id") == experiment_id for event in self._events()) + 1
        self._append({"event": "TEST_VIEWED", "experiment_id": experiment_id, "result_sha": result_sha,
                      "view_number": count, "recorded_at": datetime.now(timezone.utc).isoformat()})
        return count

    def conclude(self, experiment_id: str, conclusion: str, evidence_sha: str) -> None:
        self._append({"event": "CONCLUDED", "experiment_id": experiment_id, "conclusion": conclusion,
                      "evidence_sha": evidence_sha, "recorded_at": datetime.now(timezone.utc).isoformat()})

    def _append(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

