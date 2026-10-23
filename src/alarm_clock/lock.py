"""One runner at a time, enforced by an OS file lock (ADR-008).

The OS drops the lock when the process dies, however it dies, so there is no stale pidfile to
clean up. The pid written into the file is only for the error message.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

if sys.platform == "win32":
    import msvcrt

    def _try_lock(f: IO[str]) -> bool:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _lock_blocking(f: IO[str]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # retries for ~10s, then OSError

    def _unlock(f: IO[str]) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(f: IO[str]) -> bool:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _lock_blocking(f: IO[str]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock(f: IO[str]) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class AlreadyRunning(Exception):
    pass


def _holder_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):  # Windows refuses to read a locked byte; that's fine
        return None


@contextmanager
def single_instance(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        if not _try_lock(f):
            pid = _holder_pid(path)
            holder = f" (pid {pid})" if pid else ""
            raise AlreadyRunning(f"another `alarm run` is already running{holder}")
        try:
            f.seek(0)
            f.truncate()
            f.write(f"{os.getpid()}\n")
            f.flush()
            yield
        finally:
            _unlock(f)


def runner_active(path: Path) -> bool:
    """Probe without side effects: someone holds the lock right now."""
    if not path.exists():
        return False
    with open(path, "a+", encoding="utf-8") as f:
        if _try_lock(f):
            _unlock(f)
            return False
        return True


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Blocking lock for short critical sections, e.g. a CLI read-modify-write of alarms.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        _lock_blocking(f)
        try:
            yield
        finally:
            _unlock(f)
