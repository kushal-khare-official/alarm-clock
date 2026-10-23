import io
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

from alarm_clock.ringer import (
    FREEDESKTOP_SOUND,
    MAC_SOUND,
    BellRinger,
    LoopingCommandRinger,
    WinsoundRinger,
    select_ringer,
)


def wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.005)


class FakeProc:
    """A player process that runs until the test finishes it or the ringer terminates it."""

    def __init__(self) -> None:
        self._done = threading.Event()
        self.terminated = False

    def finish(self) -> None:
        self._done.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return 0

    def poll(self) -> int | None:
        return 0 if self._done.is_set() else None

    def terminate(self) -> None:
        self.terminated = True
        self._done.set()

    def kill(self) -> None:
        self._done.set()


class FakePopen:
    def __init__(self) -> None:
        self.procs: list[FakeProc] = []
        self.argv: list[str] = []

    def __call__(self, argv: list[str], **_: Any) -> FakeProc:
        self.argv = argv
        proc = FakeProc()
        self.procs.append(proc)
        return proc


# --- selection (pure; every platform tested on every OS) --------------------------------------


def which_only(*available: str) -> Callable[[str], str | None]:
    return lambda name: f"/usr/bin/{name}" if name in available else None


def exists_only(*paths: str) -> Callable[[str], bool]:
    return lambda path: path in paths


@pytest.mark.parametrize(
    ("platform", "which", "exists", "expected"),
    [
        ("darwin", which_only("afplay"), exists_only(MAC_SOUND), "afplay Glass.aiff"),
        ("darwin", which_only(), exists_only(MAC_SOUND), "terminal bell"),
        ("darwin", which_only("afplay"), exists_only(), "terminal bell"),
        ("linux", which_only("paplay", "pw-play"), exists_only(FREEDESKTOP_SOUND), "paplay"),
        ("linux", which_only("pw-play"), exists_only(FREEDESKTOP_SOUND), "pw-play"),
        ("linux", which_only("paplay"), exists_only(), "terminal bell"),
        ("linux", which_only(), exists_only(FREEDESKTOP_SOUND), "terminal bell"),
        ("freebsd14", which_only("paplay"), exists_only(FREEDESKTOP_SOUND), "terminal bell"),
    ],
)
def test_select_ringer(
    platform: str,
    which: Callable[[str], str | None],
    exists: Callable[[str], bool],
    expected: str,
) -> None:
    ringer = select_ringer(platform, which=which, exists=exists)
    assert ringer.name.startswith(expected)


def test_select_ringer_windows_uses_winsound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound([]))
    assert isinstance(
        select_ringer("win32", which=which_only(), exists=exists_only()), WinsoundRinger
    )


def test_prefer_bell_overrides_platform() -> None:
    ringer = select_ringer(
        "darwin", which=which_only("afplay"), exists=exists_only(MAC_SOUND), prefer_bell=True
    )
    assert isinstance(ringer, BellRinger)


# --- looping command ringer -----------------------------------------------------------------


def test_player_is_respawned_until_stopped_then_terminated() -> None:
    popen = FakePopen()
    ringer = LoopingCommandRinger(["/usr/bin/afplay", MAC_SOUND], popen=popen, pause=0)
    ringer.start()
    wait_until(lambda: len(popen.procs) == 1)
    popen.procs[0].finish()  # the sound played to the end
    wait_until(lambda: len(popen.procs) == 2)  # ...so it plays again
    ringer.stop()
    assert popen.procs[1].terminated
    assert popen.argv == ["/usr/bin/afplay", MAC_SOUND]
    time.sleep(0.05)
    assert len(popen.procs) == 2  # nothing spawned after stop


def test_stop_before_first_spawn_and_twice_is_safe() -> None:
    popen = FakePopen()
    ringer = LoopingCommandRinger(["player", "x.oga"], popen=popen, pause=0)
    ringer.stop()
    ringer.start()
    ringer.stop()
    ringer.stop()
    assert all(p.poll() is not None for p in popen.procs)


def test_ringer_can_ring_again_after_stop() -> None:
    popen = FakePopen()
    ringer = LoopingCommandRinger(["player", "x.oga"], popen=popen, pause=0)
    ringer.start()
    wait_until(lambda: len(popen.procs) == 1)
    ringer.stop()
    ringer.start()  # snooze ended: same ringer rings again
    wait_until(lambda: len(popen.procs) == 2)
    ringer.stop()
    assert all(p.terminated for p in popen.procs)


def test_missing_player_falls_back_to_the_bell_instead_of_silence() -> None:
    def broken(*_: Any, **__: Any) -> FakeProc:
        raise FileNotFoundError("afplay")

    stream = io.StringIO()
    ringer = LoopingCommandRinger(
        ["afplay", MAC_SOUND], popen=broken, fallback=BellRinger(stream, interval=0.01)
    )
    ringer.start()
    wait_until(lambda: stream.getvalue().count("\a") >= 2)
    ringer.stop()
    assert isinstance(ringer.failed, FileNotFoundError)
    rung = stream.getvalue().count("\a")
    time.sleep(0.05)
    assert stream.getvalue().count("\a") == rung  # the fallback stopped too


def test_real_child_process_is_gone_after_stop() -> None:
    """No mocks: a real long-running child stands in for the player."""
    spawned: list[subprocess.Popen[Any]] = []

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        proc = subprocess.Popen(*args, **kwargs)
        spawned.append(proc)
        return proc

    ringer = LoopingCommandRinger(
        [sys.executable, "-c", "import time; time.sleep(30)"], popen=recording_popen
    )
    ringer.start()
    wait_until(lambda: len(spawned) == 1)
    ringer.stop()
    assert spawned[0].poll() is not None  # exited, not orphaned


# --- bell and winsound ------------------------------------------------------------------------


def test_bell_rings_repeatedly_and_stops() -> None:
    stream = io.StringIO()
    bell = BellRinger(stream, interval=0.01)
    bell.start()
    wait_until(lambda: stream.getvalue().count("\a") >= 3)
    bell.stop()
    rung = stream.getvalue().count("\a")
    time.sleep(0.05)
    assert stream.getvalue().count("\a") == rung


def fake_winsound(calls: list[tuple[str | None, int]]) -> ModuleType:
    module = ModuleType("winsound")
    module.SND_ALIAS = 0x10000  # type: ignore[attr-defined]
    module.SND_ASYNC = 0x1  # type: ignore[attr-defined]
    module.SND_LOOP = 0x8  # type: ignore[attr-defined]
    module.PlaySound = lambda sound, flags: calls.append((sound, flags))  # type: ignore[attr-defined]
    return module


def test_winsound_loops_asynchronously_and_stops() -> None:
    calls: list[tuple[str | None, int]] = []
    ringer = WinsoundRinger(fake_winsound(calls))
    ringer.start()
    ringer.stop()
    assert calls == [("SystemExclamation", 0x10000 | 0x1 | 0x8), (None, 0)]
