import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ops.worker_lease import WorkerLease
from scripts.ops.task_worker_service import execute_subprocess


class Connection:
    def __init__(self, owned=1, failure=False):
        self.owned = owned
        self.failure = failure
        self.pings = []

    def ping(self, *, reconnect):
        self.pings.append(reconnect)
        if self.failure:
            raise OSError('connection closed')

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def execute(self, sql):
        assert 'CONNECTION_ID()' in sql

    def fetchone(self):
        return {'owned': self.owned}


@pytest.mark.parametrize('owned,failure', [(None, False), (0, False), (1, True)])
def test_lost_lease_never_reconnects(owned, failure):
    conn = Connection(owned, failure)
    with pytest.raises(RuntimeError, match='worker database lease lost'):
        WorkerLease(conn).assert_owned()
    assert conn.pings == [False]


def test_live_lease_is_kept_on_original_connection():
    conn = Connection()
    lease = WorkerLease(conn)
    lease.assert_owned()
    lease.assert_owned()
    assert conn.pings == [False, False]


def test_lease_loss_terminates_running_task(tmp_path):
    pidfile = tmp_path / 'pid'
    script = 'import os,time;from pathlib import Path;Path(%r).write_text(str(os.getpid()));time.sleep(60)' % str(pidfile)
    def heartbeat():
        raise RuntimeError('worker database lease lost; restart required')
    with pytest.raises(RuntimeError, match='lease lost'):
        execute_subprocess([sys.executable, '-c', script], cwd=tmp_path, env=os.environ,
                           heartbeat=heartbeat, heartbeat_interval_seconds=1)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_lease_is_checked_even_for_short_task(tmp_path):
    seen = []
    result = execute_subprocess([sys.executable, '-c', 'pass'], cwd=tmp_path, env=os.environ,
                                heartbeat=lambda: seen.append(True))
    assert result.exit_code == 0
    assert seen == [True]
