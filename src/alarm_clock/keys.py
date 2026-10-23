"""Single keypresses without Enter, with a timeout (ADR-011).

The reader stays open for the runner's whole life so stray keys don't echo over the display.
`flush()` runs at the start of every ring: a key pressed hours earlier must not dismiss it.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import IO, Protocol


class KeyReader(Protocol):
    def read_key(self, timeout: float) -> str | None:
        """One lower-cased character, or None if nothing was pressed within `timeout` seconds."""
        ...

    def flush(self) -> None:
        """Discard anything typed before now."""
        ...


class NullKeyReader:
    """stdin is not a terminal (piped, backgrounded): nobody can press keys, so just wait."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    def read_key(self, timeout: float) -> str | None:
        self._sleep(timeout)
        return None

    def flush(self) -> None:
        pass


class PosixKeyReader:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def read_key(self, timeout: float) -> str | None:
        import select

        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(self._fd, 64).decode(errors="ignore")
        return data[:1].lower() or None

    def flush(self) -> None:
        if sys.platform == "win32":
            raise NotImplementedError("POSIX only")
        import termios

        termios.tcflush(self._fd, termios.TCIFLUSH)


class WindowsKeyReader:
    def __init__(self, msvcrt: ModuleType, poll_interval: float = 0.02) -> None:
        self._msvcrt = msvcrt
        self._poll = poll_interval

    def read_key(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            while self._msvcrt.kbhit():
                ch: str = self._msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # arrow/function key: second half follows
                    self._msvcrt.getwch()
                    continue
                if ch == "\x03":
                    raise KeyboardInterrupt
                return ch.lower()
            if time.monotonic() >= deadline:
                return None
            time.sleep(self._poll)

    def flush(self) -> None:
        while self._msvcrt.kbhit():
            self._msvcrt.getwch()


@contextmanager
def cbreak(fd: int) -> Iterator[PosixKeyReader]:
    """Keys arrive one at a time, unechoed; Ctrl-C still interrupts. Restored on any exit."""
    if sys.platform == "win32":
        raise NotImplementedError("POSIX only")
    import termios
    import tty

    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield PosixKeyReader(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@contextmanager
def open_key_reader(stdin: IO[str]) -> Iterator[KeyReader]:
    if not stdin.isatty():
        yield NullKeyReader()
    elif sys.platform == "win32":
        import msvcrt

        yield WindowsKeyReader(msvcrt)
    else:
        with cbreak(stdin.fileno()) as reader:
            yield reader
