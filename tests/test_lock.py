import subprocess
import sys
import time
from pathlib import Path

import pytest

from alarm_clock.lock import AlreadyRunning, runner_active, single_instance


def test_second_instance_is_refused(tmp_path: Path) -> None:
    lock = tmp_path / "runner.lock"
    with (
        single_instance(lock),  # held...
        pytest.raises(AlreadyRunning, match="already running"),
        single_instance(lock),  # ...so this one is refused
    ):
        pass
    with single_instance(lock):  # free again once released
        pass


def test_probe_reports_holder_without_side_effects(tmp_path: Path) -> None:
    lock = tmp_path / "runner.lock"
    assert runner_active(lock) is False  # no file yet
    with single_instance(lock):
        before = lock.read_bytes() if sys.platform != "win32" else b""
        assert runner_active(lock) is True
        if sys.platform != "win32":  # Windows forbids reading a locked byte from another handle
            assert lock.read_bytes() == before
    assert runner_active(lock) is False


HOLD_LOCK = """
import sys, time
from pathlib import Path
from alarm_clock.lock import single_instance
with single_instance(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(60)
"""


def test_lock_is_released_when_the_holder_is_killed(tmp_path: Path) -> None:
    """A real second process, killed without any chance to clean up (SIGKILL / TerminateProcess)."""
    lock = tmp_path / "runner.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(lock)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        assert runner_active(lock) is True
        with pytest.raises(AlreadyRunning), single_instance(lock):
            pass
    finally:
        holder.kill()
        holder.wait()
    # POSIX releases flock locks the instant the process dies. Windows releases them
    # asynchronously ("the time it takes ... depends upon available system resources",
    # LockFileEx docs), which CI showed on windows-latest/py3.13. Wait, but not forever.
    deadline = time.monotonic() + 10
    while runner_active(lock) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runner_active(lock) is False
    with single_instance(lock):
        pass
