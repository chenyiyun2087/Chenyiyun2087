from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ops import daily_batch_audit as audit
from scripts.ops import deploy_production_release as deploy
from scripts.ops import release_runtime
from scripts.maintenance.rebuild_forward_shadow_chain import rebuild_chain


def test_tracked_checkout_seed_cannot_shadow_persistent_event_days(tmp_path):
    source, target = tmp_path / "source", tmp_path / "release"
    relative = "exports/forward_shadow_evidence"
    persistent = source / relative
    checkout = target / relative
    (persistent / "execution/events").mkdir(parents=True)
    (checkout / "execution/events").mkdir(parents=True)
    (persistent / "execution/events/2026-10-16.jsonl").write_text("complete")
    (checkout / "execution/events/2026-10-16.jsonl").write_text("old seed")
    (persistent / "execution/events/2026-10-23.jsonl").write_text("new day")

    backup = deploy._share_canonical_runtime_tree(source, target, relative)

    assert checkout.is_symlink() and checkout.resolve() == persistent
    assert (backup / "execution/events/2026-10-16.jsonl").read_text() == "old seed"
    assert (checkout / "execution/events/2026-10-23.jsonl").read_text() == "new day"
    (checkout / "execution/events/2026-10-26.jsonl").write_text("pending only")
    assert (persistent / "execution/events/2026-10-26.jsonl").read_text() == "pending only"
    assert deploy._share_canonical_runtime_tree(source, target, relative) is None


def test_new_manifest_rejects_a_separate_copy_even_with_matching_content(tmp_path):
    source, target = tmp_path / "source", tmp_path / "release"
    relative = "exports/forward_shadow_evidence"
    (source / relative).mkdir(parents=True)
    (target / relative).mkdir(parents=True)
    python = tmp_path / "python"
    python.touch()
    manifest = tmp_path / "manifest.json"
    payload = {"release_id": "test", "commit_sha": "a" * 40,
               "project_root": str(target), "runtime_python": str(python),
               "source_repo": str(source), "canonical_runtime_roots": [relative]}
    manifest.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="runtime_root_diverged"):
        release_runtime.load_runtime_release(manifest)
    deploy._share_canonical_runtime_tree(source, target, relative)
    assert release_runtime.load_runtime_release(manifest).canonical_runtime_roots == (relative,)


@pytest.mark.parametrize("proof_ok,expected_status", [(False, "FAILED"), (True, "OK")])
def test_queue_success_requires_current_shadow_contract(monkeypatch, proof_ok, expected_status):
    monkeypatch.setattr(audit, "_latest_queue", lambda *a: {"id": 1, "status": "SUCCESS"})
    monkeypatch.setattr(audit, "_latest_history", lambda *a: {"status": "Success"})
    monkeypatch.setattr(audit, "_recovered_artifact", lambda *a: None)
    calls = []
    def verify(name, day):
        calls.append((name, day))
        return proof_ok, ["EVENT_LOG_CORRUPT: duplicate BUY"]
    monkeypatch.setattr(audit, "_verify_shadow_artifact", verify)
    task = audit.ExpectedTask("alpha_signal_precommit", "09:25", "shadow", True)
    row = audit.classify_task(None, task, "20261019", True, require_notifications=False)
    assert row["status"] == expected_status
    assert row["queue_status"] == "SUCCESS"
    assert row["replay_required"] == int(not proof_ok)
    assert calls == [("alpha_signal_precommit", "20261019")]


def test_shadow_contract_import_disables_scheduler_and_restores_environment(monkeypatch):
    monkeypatch.setenv("DISABLE_APP_SCHEDULER_LOOP", "original")
    def verify(started, finished, options):
        assert os.environ["DISABLE_APP_SCHEDULER_LOOP"] == "1"
        assert options == {"datestr": "20261023", "historical_safe": True}
        return False, ["missing NAV"]
    monkeypatch.setattr(audit.importlib, "import_module", lambda name: SimpleNamespace(
        _verify_alpha_signal_nav_result=verify))
    assert audit._verify_shadow_artifact("alpha_signal_nav", "20261023") == (False, ["missing NAV"])
    assert os.environ["DISABLE_APP_SCHEDULER_LOOP"] == "original"


def test_staged_rebuild_orders_days_and_never_fabricates_a_missing_signal(tmp_path):
    days = ["2020-01-02", "2020-01-03"]
    packages, zone = tmp_path / "packages", tmp_path / "execution"
    (packages / days[0]).mkdir(parents=True)
    (packages / days[0] / "signal_package_manifest.json").write_text("{}")
    calls, emitted = [], []
    class Shadow:
        def _latest_package_for_execution(self, day, packages):
            return packages if day == days[0] else None
        def precommit(self, **kw):
            day = kw["execution_date"]; calls.append((day, "buy"))
            (zone / day).mkdir(); (zone / day / "orders.json").write_text("[]")
            return {}
        def reconcile_from_package(self, **kw):
            calls.append((kw["execution_date"], "reconcile")); return {}
        def sell_precommit(self, **kw):
            calls.append((kw["business_date"], "sell")); return {}
        def nav(self, **kw):
            calls.append((kw["execution_date"], "nav")); return {}
        def replay_all(self, zone, as_of_date):
            calls.append((as_of_date, "replay"))
            return SimpleNamespace(orders=[], positions={}, completed_round_trips=lambda: 0)
    result = rebuild_chain(Shadow(), days, packages, zone,
                           lambda day, step, value: emitted.append((day, step, value)))
    assert calls[:5] == [(days[0], k) for k in ("buy", "reconcile", "sell", "nav", "replay")]
    assert (days[1], "buy") not in calls and (days[1], "nav") in calls
    assert result["no_execution_signal_dates"] == [days[1]]
    assert not (zone / days[1] / "orders.json").exists()
    assert result["promotion_performed"] is False
    with pytest.raises(ValueError, match="must be empty"):
        rebuild_chain(Shadow(), days, packages, zone, lambda *a: None)
