"""快照缓存 — 本地化存储 ADC 研究快照，避免重复拉取。

每个 snapshot_id 只拉取一次，缓存到 ads_research_snapshots 表。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime


def compute_payload_hash(data: dict) -> str:
    """计算快照 payload 的 SHA256。"""
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def snapshot_exists(engine, snapshot_id: str) -> bool:
    """检查快照是否已缓存。"""
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT 1 FROM chenyiyun.ads_research_snapshots WHERE snapshot_id = :sid"
            ),
            {"sid": snapshot_id},
        ).scalar()
    return bool(result)


def write_snapshot(
    engine,
    snapshot_id: str,
    as_of_date: date,
    feature_version: str,
    label_version: str,
    source_commit: str,
    payload: dict,
) -> None:
    """写入快照记录（不可变，禁止覆盖）。"""
    from sqlalchemy import text

    data_cutoff = datetime.combine(as_of_date, datetime.strptime("15:30", "%H:%M").time())
    generated_at = datetime.now()
    payload_sha = compute_payload_hash(payload)

    if snapshot_exists(engine, snapshot_id):
        raise ValueError(f"Snapshot {snapshot_id} already exists — immutable, cannot overwrite")

    with engine.connect() as conn:
        conn.execute(
            text(
                """
            INSERT INTO chenyiyun.ads_research_snapshots
                (snapshot_id, as_of_date, generated_at, data_cutoff_at,
                 feature_version, label_version, source_commit, payload_sha256)
            VALUES
                (:sid, :aod, :gat, :dca, :fv, :lv, :sc, :ps)
            """
            ),
            {
                "sid": snapshot_id,
                "aod": as_of_date,
                "gat": generated_at,
                "dca": data_cutoff,
                "fv": feature_version,
                "lv": label_version,
                "sc": source_commit,
                "ps": payload_sha,
            },
        )
        conn.commit()
