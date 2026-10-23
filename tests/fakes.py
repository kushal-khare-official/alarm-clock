"""Test doubles for the runner's seams (ADR-005, ADR-011)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta


class FakeClock:
    """Time only moves when someone sleeps or the test says so."""

    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(UTC)
        self.on_sleep: Callable[[FakeClock], None] | None = None

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        if self.on_sleep is not None:
            self.on_sleep(self)

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)

    def set(self, instant: datetime) -> None:
        self._now = instant.astimezone(UTC)


INTERRUPT = "^C"


class ScriptedKeys:
    """Keypresses at given instants. Waiting for a key advances the fake clock."""

    def __init__(self, clock: FakeClock, presses: list[tuple[datetime, str]] | None = None) -> None:
        self._clock = clock
        self._presses = sorted((at.astimezone(UTC), key) for at, key in presses or [])
        self.flushed: list[str] = []

    def read_key(self, timeout: float) -> str | None:
        now = self._clock.now()
        end = now + timedelta(seconds=timeout)
        if self._presses and self._presses[0][0] <= end:
            at, key = self._presses.pop(0)
            self._clock.set(max(at, now))
            if key == INTERRUPT:
                raise KeyboardInterrupt
            return key
        self._clock.set(end)
        return None

    def flush(self) -> None:
        now = self._clock.now()
        while self._presses and self._presses[0][0] <= now:
            self.flushed.append(self._presses.pop(0)[1])


class FakeRinger:
    name = "fake"

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.log: list[tuple[str, datetime]] = []

    def start(self) -> None:
        self.log.append(("start", self._clock.now()))

    def stop(self) -> None:
        self.log.append(("stop", self._clock.now()))

    @property
    def starts(self) -> list[datetime]:
        return [at for what, at in self.log if what == "start"]

    @property
    def ringing(self) -> bool:
        events = [what for what, _ in self.log]
        return events.count("start") > events.count("stop")


class RecordingDisplay:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.rings: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self.active = False

    def event(self, text: str) -> None:
        self.events.append(text)

    def ring_started(self, title: str, detail: str) -> None:
        self.rings.append((title, detail))
        self.active = True

    def ring_tick(self, prompt: str) -> None:
        self.prompts.append(prompt)

    def ring_ended(self) -> None:
        self.active = False
