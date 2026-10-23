"""Domain types and parsing of user input. No I/O, no clock."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

# Weekday numbers match datetime.weekday(): Monday is 0.
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_FULL_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

EVERY_DAY = frozenset(range(7))
WEEKDAYS = frozenset(range(5))
WEEKENDS = frozenset({5, 6})
ONCE: frozenset[int] = frozenset()

_PRESETS = {"daily": EVERY_DAY, "weekdays": WEEKDAYS, "weekends": WEEKENDS, "once": ONCE}

SNOOZE_MIN, SNOOZE_MAX, SNOOZE_DEFAULT = 1, 60, 9
LABEL_MAX = 60


class AlarmError(ValueError):
    """Invalid user input or an operation that cannot be applied. Message is user-facing."""


@dataclass(frozen=True)
class Alarm:
    """An alarm definition. Written only by CLI commands (see DECISIONS.md ADR-007)."""

    id: int
    at: time
    days: frozenset[int]  # empty means one-shot
    label: str
    enabled: bool
    snooze_minutes: int
    # Occurrences at or before the anchor never fire. Set on add, edit and enable (ADR-006).
    anchor: datetime

    @property
    def is_recurring(self) -> bool:
        return bool(self.days)


@dataclass(frozen=True)
class AlarmState:
    """Runner-owned bookkeeping for one alarm (ADR-006, ADR-010)."""

    last_handled: datetime | None = None
    snoozed_occurrence: datetime | None = None
    snoozed_until: datetime | None = None


_TIME_24H = re.compile(r"(\d{1,2}):(\d{2})")
_TIME_12H = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.IGNORECASE)


def parse_time(text: str) -> time:
    """Parse `7:30`, `07:30`, `19:05`, `7:30pm` or `7pm` into a wall-clock time."""
    raw = text.strip()
    hint = "expected HH:MM (24-hour) or H:MM am/pm, e.g. 07:30 or 7:30pm"
    if m := _TIME_12H.fullmatch(raw):
        hour, minute = int(m[1]), int(m[2] or 0)
        if not 1 <= hour <= 12:
            raise AlarmError(f"invalid time {text!r}: hour must be 1-12 with am/pm")
        if not 0 <= minute <= 59:
            raise AlarmError(f"invalid time {text!r}: minutes must be 00-59")
        hour = hour % 12 + (12 if m[3].lower() == "p" else 0)
        return time(hour, minute)
    if m := _TIME_24H.fullmatch(raw):
        hour, minute = int(m[1]), int(m[2])
        if not 0 <= hour <= 23:
            raise AlarmError(f"invalid time {text!r}: hour must be 00-23")
        if not 0 <= minute <= 59:
            raise AlarmError(f"invalid time {text!r}: minutes must be 00-59")
        return time(hour, minute)
    raise AlarmError(f"invalid time {text!r}: {hint}")


def parse_days(text: str) -> frozenset[int]:
    """Parse `daily`, `weekdays`, `weekends`, `once`, or a list like `mon,wed,fri`."""
    raw = text.strip().lower()
    if raw in _PRESETS:
        return _PRESETS[raw]
    days: set[int] = set()
    for part in raw.split(","):
        name = part.strip()
        if name in DAY_NAMES:
            days.add(DAY_NAMES.index(name))
        elif name in _FULL_DAY_NAMES:
            days.add(_FULL_DAY_NAMES.index(name))
        else:
            valid = ", ".join([*_PRESETS, "or days like mon,wed,fri"])
            raise AlarmError(f"invalid days {text!r}: unknown day {name!r} (use {valid})")
    return frozenset(days)


def parse_label(text: str) -> str:
    label = " ".join(text.split())  # collapse whitespace; keeps tables on one line
    if len(label) > LABEL_MAX:
        raise AlarmError(f"label is too long ({len(label)} > {LABEL_MAX} characters)")
    return label


def format_days(days: frozenset[int]) -> str:
    if not days:
        return "Once"
    if days == EVERY_DAY:
        return "Daily"
    if days == WEEKDAYS:
        return "Mon-Fri"
    if days == WEEKENDS:
        return "Sat, Sun"
    return ", ".join(DAY_NAMES[d].capitalize() for d in sorted(days))


def format_time(at: time) -> str:
    return at.strftime("%H:%M")
