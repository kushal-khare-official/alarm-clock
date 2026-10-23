"""The real program in a real terminal: nothing faked, so it waits for an actual minute boundary.

    uv run pytest -m e2e

POSIX only (pty). conftest.py skips this directory on Windows; the assert tells mypy.
"""

import contextlib
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

assert sys.platform != "win32"

pytestmark = pytest.mark.e2e

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][0-9A-Za-z]|\a|\r")


class Terminal:
    """`alarm` running under a pseudo-terminal that is its controlling terminal, so Ctrl-C
    (`\\x03`) is turned into SIGINT by the line discipline exactly as for a person."""

    def __init__(self, args: list[str], env: dict[str, str]) -> None:
        pid, fd = pty.fork()
        if pid == 0:  # child
            os.execve(sys.executable, [sys.executable, "-m", "alarm_clock", *args], env)
        self.pid, self.fd = pid, fd
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        self.screen = ""

    def expect(self, pattern: str, timeout: float) -> re.Match[str]:
        deadline = time.monotonic() + timeout
        compiled = re.compile(pattern)
        while not (match := compiled.search(self.screen)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"timed out waiting for {pattern!r}; output:\n{self.screen}")
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if ready:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:  # child exited and the pty closed
                    chunk = b""
                if not chunk:
                    raise AssertionError(f"program exited before {pattern!r}:\n{self.screen}")
                self.screen += ANSI.sub("", chunk.decode(errors="replace"))
        self.screen = self.screen[match.end() :]  # consume, so the next expect looks forward
        return match

    def press(self, keys: bytes) -> None:
        os.write(self.fd, keys)

    def exit_code(self, timeout: float = 10) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            # Keep draining so the child never blocks on a full pty buffer.
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                with contextlib.suppress(OSError):
                    os.read(self.fd, 4096)
        raise AssertionError("program did not exit")

    def kill(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(self.fd)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def env(home: Path) -> dict[str, str]:
    return {**os.environ, "ALARM_CLOCK_HOME": str(home), "PYTHONUNBUFFERED": "1"}


@pytest.fixture
def terminal(env: dict[str, str]) -> Iterator[Terminal]:
    term = Terminal(["run", "--sound", "bell"], env)
    yield term
    term.kill()


def cli(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alarm_clock", *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_snooze_dismiss_and_ctrl_c_through_a_real_terminal(env: dict[str, str], home: Path) -> None:
    now = datetime.now()
    # Near the end of a minute, `add` could land after the target minute starts and anchor the
    # alarm to tomorrow; aim one minute further out instead.
    due = (now + timedelta(minutes=2 if now.second >= 50 else 1)).strftime("%H:%M")
    cli(env, "add", due, "-l", "First")
    cli(env, "add", due, "-l", "Second")

    term = Terminal(["run", "--sound", "bell"], env)
    try:
        term.expect(r"Watching 2 alarms\. Next: #1", timeout=15)

        term.expect(r"\[s\] snooze 9m", timeout=135)  # the real minute boundary
        term.press(b"s")
        term.expect(rf"#1 {due} First: snoozed until \d\d:\d\d", timeout=10)

        term.expect(r"\[s\] snooze 9m", timeout=10)  # the second alarm, right after
        term.press(b"d")
        term.expect(rf"#2 {due} Second: dismissed", timeout=10)

        term.press(b"\x03")  # Ctrl-C, via the terminal's line discipline
        term.expect(r"Stopped\.", timeout=10)
        assert term.exit_code() == 0
    finally:
        term.kill()

    state = json.loads((home / "state.json").read_text())["alarms"]
    assert state["1"]["snoozed_until"] is not None
    assert state["2"]["last_handled"] is not None
    assert state["2"]["snoozed_until"] is None
    listing = cli(env, "list")
    assert "snoozed" in listing


def test_second_runner_is_refused_while_the_first_runs(
    terminal: Terminal, env: dict[str, str]
) -> None:
    terminal.expect(r"Watching 0 alarms", timeout=15)
    result = subprocess.run(
        [sys.executable, "-m", "alarm_clock", "run"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "already running" in result.stderr
    added = subprocess.run(
        [sys.executable, "-m", "alarm_clock", "add", "7:30"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert "no `alarm run` is active" not in added.stderr  # the CLI sees the live runner
