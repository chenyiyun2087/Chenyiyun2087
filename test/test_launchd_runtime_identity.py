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


def test_supervisor_waits_for_runtime_after_open_returns_and_forwards_stop(tmp_path):
    import subprocess
    import time
    from pathlib import Path

    def wait_until(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('supervisor did not reach expected state')

    fake_open = tmp_path / 'open'
    fake_open.write_text('''#!/bin/bash
for argument in "$@"; do
  case "$argument" in
    CHENYIYUN_SERVICE_PID_FILE=*) pid_file="${argument#*=}" ;;
  esac
done
sleep 60 >/dev/null 2>&1 &
echo "$!" > "$pid_file"
exit 0
''')
    fake_open.chmod(0o700)
    fake_plutil = tmp_path / 'plutil'
    fake_plutil.write_text('#!/bin/bash\necho "' + str(tmp_path) + '"\n')
    fake_plutil.chmod(0o700)
    source = Path('scripts/ops/launchd_service_supervisor.sh').read_text()
    source = source.replace('service_home="$HOME/Library/Application Support/Chenyiyun2087"', f'service_home="{tmp_path}"')
    source = source.replace('log_dir="$HOME/Library/Logs/Chenyiyun2087"', f'log_dir="{tmp_path / "logs"}"')
    source = source.replace('/usr/bin/open', str(fake_open)).replace('/usr/bin/plutil', str(fake_plutil))
    supervisor = tmp_path / 'supervisor.sh'
    supervisor.write_text(source)
    child_pid = None
    process = subprocess.Popen(['/bin/bash', str(supervisor), 'web'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        wait_until(lambda: bool(list((tmp_path / 'runtime').glob('*.pid'))))
        pid_file = next((tmp_path / 'runtime').glob('*.pid'))
        wait_until(lambda: bool(pid_file.read_text().strip()))
        child_pid = int(pid_file.read_text())
        time.sleep(0.2)
        assert process.poll() is None, 'open returning must not end supervision of the application'
        process.terminate()
        process.wait(timeout=8)
        assert not pid_file.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if child_pid:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                pass
            else:
                # The orphan can briefly be a zombie before launchd reaps it.
                status = subprocess.run(['/bin/ps', '-p', str(child_pid), '-o', 'stat='], text=True, capture_output=True)
                if status.returncode == 0 and not status.stdout.strip().startswith('Z'):
                    import signal
                    os.kill(child_pid, signal.SIGTERM)
                    raise AssertionError('termination must reach the application process')
