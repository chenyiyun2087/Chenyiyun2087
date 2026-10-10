import json
import sys
from datetime import datetime, timedelta

import pytest

from scripts import run_bs_signal_enhancement_cycle as cycle
from web import app as web_app


def setup_cycle(monkeypatch, tmp_path, pointer):
    monkeypatch.setattr(cycle, 'RUN_ROOT', tmp_path / 'runs')
    monkeypatch.setattr(cycle, 'MODEL_ROOT', tmp_path / 'models')
    monkeypatch.setattr(cycle, 'PRODUCTION_STABILITY_HOLD_PATH', tmp_path / 'no_hold')
    cycle.MODEL_ROOT.mkdir()
    (cycle.MODEL_ROOT / 'active_model.json').write_text(pointer)
    monkeypatch.setattr(sys, 'argv', ['cycle'])
    def forbidden(*args, **kwargs):
        raise AssertionError('freeze must prevent tests, training and imports')
    monkeypatch.setattr(cycle, '_run_preflight_tests', forbidden)
    monkeypatch.setattr(cycle, '_run', forbidden)


def test_freeze_stops_cycle_before_side_effects(monkeypatch, tmp_path):
    setup_cycle(monkeypatch, tmp_path, json.dumps({'auto_activate': False, 'activation_frozen_reason': 'manual freeze'}))
    before = (cycle.MODEL_ROOT / 'active_model.json').read_bytes()
    cycle.main()
    manifest = json.loads(next(cycle.RUN_ROOT.glob('*/cycle_manifest.json')).read_text())
    assert manifest['reason'] == 'model_activation_frozen'
    assert manifest['activation']['committed'] is False
    assert (cycle.MODEL_ROOT / 'active_model.json').read_bytes() == before


@pytest.mark.parametrize('pointer', ['invalid json', '[]'])
def test_invalid_pointer_remains_failure(monkeypatch, tmp_path, pointer):
    setup_cycle(monkeypatch, tmp_path, pointer)
    with pytest.raises(RuntimeError, match='Activation BLOCKED'):
        cycle.main()


@pytest.mark.parametrize('frozen,committed,expected', [(True, False, True), (False, False, False), (True, True, False)])
def test_verifier_requires_real_freeze_and_no_activation(monkeypatch, tmp_path, frozen, committed, expected):
    models = tmp_path / 'models'
    models.mkdir()
    (models / 'active_model.json').write_text(json.dumps({'auto_activate': not frozen}))
    monkeypatch.setattr(cycle, 'MODEL_ROOT', models)
    monkeypatch.setattr(web_app.app, 'root_path', str(tmp_path / 'web'))
    run = tmp_path / 'exports' / 'bs_signal_cycles' / 'run'
    run.mkdir(parents=True)
    (run / 'cycle_manifest.json').write_text(json.dumps({'status': 'skipped', 'reason': 'model_activation_frozen', 'activation': {'committed': committed}}))
    now = datetime.now()
    ok, _ = web_app._verify_monthly_bs_cycle_result(now-timedelta(seconds=1), now, {'datestr': '20261008'})
    assert ok is expected
