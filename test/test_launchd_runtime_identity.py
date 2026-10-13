import os
import stat
import pytest
from scripts.ops.launchd_runtime import record_service_pid


def test_direct_launcher_does_not_require_supervisor(monkeypatch):
    monkeypatch.delenv('CHENYIYUN_SERVICE_PID_FILE', raising=False)
    record_service_pid()


def test_supervisor_receives_private_current_process_identity(monkeypatch, tmp_path):
    target = tmp_path / 'service.pid'
    monkeypatch.setenv('CHENYIYUN_SERVICE_PID_FILE', str(target))
    record_service_pid()
    assert target.read_text().strip() == str(os.getpid())
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_launcher_cannot_overwrite_another_process_identity(monkeypatch, tmp_path):
    target = tmp_path / 'service.pid'
    target.write_text('12345\n')
    monkeypatch.setenv('CHENYIYUN_SERVICE_PID_FILE', str(target))
    with pytest.raises(FileExistsError):
        record_service_pid()
    assert target.read_text() == '12345\n'
