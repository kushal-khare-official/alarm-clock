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
