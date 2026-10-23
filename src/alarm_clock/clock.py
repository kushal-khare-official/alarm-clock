"""Time source. Production reads the system clock; tests inject a fake (ADR-005)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, tzinfo
from typing import Protocol

import tzlocal


class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, UTC-aware."""
        ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def local_zone() -> tzinfo:
    """The machine's IANA zone (honours TZ on POSIX). See ADR-009 for why not a fixed offset."""
    return tzlocal.get_localzone()
