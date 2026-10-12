import pytest
from scripts.ops.task_worker_service import classify_task_failure


@pytest.mark.parametrize('message,code,expected', [
    ("[verify]\nresult=FAIL; exit_code=3\nSIGNAL_PACKAGE_BLOCKED: universe build failed — ['financial_available_at:missing']", 3, ('DATA_READINESS', True)),
    ("[verify]\nresult=FAIL; exit_code=1\nInterruptedError: [Errno 4] Interrupted system call\nFatal Python error: error evaluating path", 1, ('TRANSIENT', True)),
    ("[verify]\nresult=FAIL; exit_code=1\nFatal Python error: error evaluating path", 1, ('VERIFICATION', False)),
    ("[verify]\nresult=FAIL; SIGNAL_PACKAGE_BLOCKED: financial_available_at:future", 3, ('VERIFICATION', False)),
    ("[verify]\nresult=FAIL; package sha mismatch", 1, ('VERIFICATION', False)),
])
def test_retry_only_recognizes_recoverable_missing_input_or_startup_interruption(message, code, expected):
    assert classify_task_failure('Failed', code, message) == expected
