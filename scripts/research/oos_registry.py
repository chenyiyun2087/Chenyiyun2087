"""Immutable, manually extended out-of-sample window registry."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "config" / "oos_registry.yaml"


@dataclass(frozen=True)
class OOSRegistry:
    version: str
    frozen_at: str
    approved_by: str
    windows: tuple[tuple[str, str, str], ...]
    config_sha: str


def load_oos_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> OOSRegistry:
    registry_path = Path(path)
    payload: dict[str, Any] = json.loads(registry_path.read_text(encoding="utf-8"))
    required = {"schema_version", "version", "frozen_at", "approved_by", "append_policy", "windows"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"oos_registry_missing_fields:{','.join(missing)}")
    if payload["append_policy"] != "MANUAL_AFTER_QUARTER_END":
        raise ValueError("oos_registry_must_be_manual")
    frozen_date = datetime.fromisoformat(str(payload["frozen_at"])).date()
    windows: list[tuple[str, str, str]] = []
    previous_end: date | None = None
    labels: set[str] = set()
    for item in payload["windows"]:
        label = str(item["label"])
        start = date.fromisoformat(str(item["start"]))
        end = date.fromisoformat(str(item["end"]))
        if label in labels or start > end:
            raise ValueError(f"oos_registry_invalid_window:{label}")
        if end >= frozen_date:
            raise ValueError(f"oos_registry_window_not_completed_at_freeze:{label}")
        if previous_end is not None and start <= previous_end:
            raise ValueError(f"oos_registry_overlapping_window:{label}")
        labels.add(label)
        previous_end = end
        windows.append((label, start.isoformat(), end.isoformat()))
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return OOSRegistry(
        version=str(payload["version"]),
        frozen_at=str(payload["frozen_at"]),
        approved_by=str(payload["approved_by"]),
        windows=tuple(windows),
        config_sha=hashlib.sha256(canonical).hexdigest(),
    )


def fixed_window_pairs(path: str | Path = DEFAULT_REGISTRY_PATH) -> list[tuple[str, str]]:
    return [(start, end) for _, start, end in load_oos_registry(path).windows]


def fixed_windows(path: str | Path = DEFAULT_REGISTRY_PATH) -> tuple[tuple[str, str, str], ...]:
    return load_oos_registry(path).windows


if __name__ == "__main__":
    registry = load_oos_registry()
    print(json.dumps({
        "status": "PASS", "version": registry.version,
        "window_count": len(registry.windows), "config_sha": registry.config_sha,
    }, ensure_ascii=False, sort_keys=True))
