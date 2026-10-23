"""Making noise, per platform, behind one small interface (ADR-011).

Rule: an alarm must never fail silently. If a sound player is missing or broken at ring time,
the ringer falls back to the terminal bell instead of going quiet.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import IO, Protocol

MAC_SOUND = "/System/Library/Sounds/Glass.aiff"
FREEDESKTOP_SOUND = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
LINUX_PLAYERS = ("paplay", "pw-play")


class Process(Protocol):
    """The slice of subprocess.Popen the ringer uses."""

    def wait(self, timeout: float | None = None) -> int: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


class Ringer(Protocol):
    name: str

    def start(self) -> None: ...

    def stop(self) -> None:
        """Silence. Idempotent, and leaves no child process behind."""
        ...


class BellRinger:
    """Terminal bell every `interval` seconds. Works anywhere there is a terminal."""

    name = "terminal bell"

    def __init__(self, stream: IO[str] | None = None, interval: float = 1.5) -> None:
        self._stream = stream
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bell", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        stream = self._stream or sys.stdout
        while not self._stop.is_set():
            stream.write("\a")
            stream.flush()
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None


class LoopingCommandRinger:
    """Runs a sound player over and over until stopped. `afplay` and `paplay` play once and exit,
    so looping is ours to do. Spawning happens under a lock that `stop` also takes, so no new
    player can start after `stop` has begun."""

    def __init__(
        self,
        argv: list[str],
        *,
        popen: Callable[..., Process] = subprocess.Popen,
        pause: float = 0.3,
        fallback: BellRinger | None = None,
    ) -> None:
        self.name = f"{Path(argv[0]).name} {Path(argv[-1]).name}"
        self._argv = argv
        self._popen = popen
        self._pause = pause
        self._fallback = fallback or BellRinger()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._proc: Process | None = None
        self._thread: threading.Thread | None = None
        self.failed: Exception | None = None  # why we fell back to the bell

    def start(self) -> None:
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, name="ringer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._stopped.is_set():
                    return
                try:
                    proc = self._popen(
                        self._argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                except OSError as e:
                    self.failed = e
                    self._fallback.start()
                    return
                self._proc = proc
            code = proc.wait()
            if self._stopped.is_set():
                return
            if code != 0:  # no audio device, unreadable file...: it made no sound
                with self._lock:
                    if not self._stopped.is_set():
                        self.failed = subprocess.CalledProcessError(code, self._argv)
                        self._fallback.start()
                return
            if self._stopped.wait(self._pause):
                return

    def stop(self) -> None:
        with self._lock:
            self._stopped.set()
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._fallback.stop()


class WinsoundRinger:
    """Windows system sound, looped by the OS itself (SND_LOOP needs SND_ASYNC)."""

    name = "winsound SystemExclamation"

    def __init__(self, winsound: ModuleType | None = None) -> None:
        if winsound is None:
            import winsound as module  # Windows-only stdlib module, so imported lazily

            winsound = module
        self._ws = winsound

    def start(self) -> None:
        ws = self._ws
        ws.PlaySound("SystemExclamation", ws.SND_ALIAS | ws.SND_ASYNC | ws.SND_LOOP)

    def stop(self) -> None:
        self._ws.PlaySound(None, 0)


def select_ringer(
    platform: str = sys.platform,
    *,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = lambda p: Path(p).is_file(),
    prefer_bell: bool = False,
) -> Ringer:
    """Pick the best available backend. Pure given its arguments, so every branch is testable
    on any OS."""
    if prefer_bell:
        return BellRinger()
    if platform == "darwin":
        afplay = which("afplay")
        if afplay and exists(MAC_SOUND):
            return LoopingCommandRinger([afplay, MAC_SOUND])
    elif platform == "win32":
        return WinsoundRinger()
    elif platform.startswith("linux") and exists(FREEDESKTOP_SOUND):
        for player in LINUX_PLAYERS:
            path = which(player)
            if path:
                return LoopingCommandRinger([path, FREEDESKTOP_SOUND])
    return BellRinger()
