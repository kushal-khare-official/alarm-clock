"""Pure scheduling logic: when does an alarm fire, and what should happen right now?

Nothing here reads the clock or touches files (ADR-005). Every instant is a UTC-aware datetime.
Wall-clock times are resolved in the `tz` passed in (ADR-009, ADR-012).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Literal

from alarm_clock.model import EVERY_DAY, Alarm, AlarmState


@dataclass(frozen=True)
class Idle:
    next_at: datetime | None  # None: nothing scheduled (disabled, or a finished one-shot)


@dataclass(frozen=True)
class Ring:
    occurrence: datetime
    late_by: timedelta
    skipped: int = 0  # earlier occurrences that fell outside the grace window
    snoozed: bool = False  # this is a snooze coming back, not a fresh occurrence


@dataclass(frozen=True)
class Missed:
    occurrence: datetime  # the most recent one
    count: int


Decision = Idle | Ring | Missed

Interval = tuple[datetime, datetime]

StatusKind = Literal["on", "off", "snoozed", "overdue", "done"]


@dataclass(frozen=True)
class Status:
    kind: StatusKind
    next_at: datetime | None


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed here: {dt!r}")
    return dt.astimezone(UTC)


def resolve(day: date, at: time, tz: tzinfo) -> datetime:
    """Turn a local wall time on `day` into a UTC instant.

    fold=0 gives the policy in ADR-009 for free: a time that does not exist (spring-forward gap)
    is read with the pre-transition offset, i.e. shifted forward by the gap length; a time that
    exists twice (fall-back fold) resolves to its first occurrence.
    """
    return datetime.combine(day, at).replace(tzinfo=tz, fold=0).astimezone(UTC)


def _iter_wall_times(
    at: time, days: frozenset[int], after: datetime, tz: tzinfo
) -> Iterator[datetime]:
    after = to_utc(after)
    # Start a day early: the local date of `after` can differ from the date an instant resolves to.
    day = after.astimezone(tz).date() - timedelta(days=1)
    while True:
        if day.weekday() in days:
            occurrence = resolve(day, at, tz)
            if occurrence > after:
                yield occurrence
        day += timedelta(days=1)


def occurrences(alarm: Alarm, after: datetime, tz: tzinfo) -> Iterator[datetime]:
    """Occurrences strictly after `after`, in order. Infinite for recurring alarms.

    A one-shot alarm has exactly one occurrence: the first `at` after its anchor.
    """
    if alarm.is_recurring:
        yield from _iter_wall_times(alarm.at, alarm.days, after, tz)
        return
    only = next(_iter_wall_times(alarm.at, EVERY_DAY, alarm.anchor, tz))
    if only > to_utc(after):
        yield only


def _floor(alarm: Alarm, state: AlarmState) -> datetime:
    """Occurrences at or before this instant are settled and never fire (ADR-006)."""
    anchor = to_utc(alarm.anchor)
    if state.last_handled is None:
        return anchor
    return max(anchor, to_utc(state.last_handled))


def active_snooze(alarm: Alarm, state: AlarmState) -> tuple[datetime, datetime] | None:
    """(occurrence, snoozed_until) if a snooze is pending and still belongs to this alarm.

    Editing or re-enabling moves the anchor past the snoozed occurrence, which cancels it (ADR-010).
    """
    if state.snoozed_occurrence is None or state.snoozed_until is None:
        return None
    if to_utc(state.snoozed_occurrence) <= to_utc(alarm.anchor):
        return None
    return to_utc(state.snoozed_occurrence), to_utc(state.snoozed_until)


def next_occurrence(
    alarm: Alarm, state: AlarmState, after: datetime, tz: tzinfo
) -> datetime | None:
    if not alarm.enabled:
        return None
    return next(occurrences(alarm, max(_floor(alarm, state), to_utc(after)), tz), None)


def excused_lateness(due: datetime, now: datetime, busy: Sequence[Interval]) -> timedelta:
    """How late `due` is, not counting time the runner spent ringing other alarms (ADR-015)."""
    late = now - due
    for start, end in busy:
        overlap = min(to_utc(end), now) - max(to_utc(start), due)
        if overlap > timedelta(0):
            late -= overlap
    return late


def evaluate(
    alarm: Alarm,
    state: AlarmState,
    now: datetime,
    grace: timedelta,
    tz: tzinfo,
    busy: Sequence[Interval] = (),
) -> Decision:
    """What should the runner do with this alarm at `now`?

    `busy` lists intervals the runner spent ringing; they don't count against the grace window.
    `late_by` in the result is still the real lateness, for display.
    """
    now = to_utc(now)
    if not alarm.enabled:
        return Idle(None)

    due: list[datetime] = []
    upcoming: datetime | None = None
    for occurrence in occurrences(alarm, _floor(alarm, state), tz):
        if occurrence > now:
            upcoming = occurrence
            break
        due.append(occurrence)

    # A fresh occurrence supersedes an older snooze: yesterday's snooze must not ring today.
    if due:
        latest = due[-1]
        if excused_lateness(latest, now, busy) <= grace:
            return Ring(latest, now - latest, skipped=len(due) - 1)
        return Missed(latest, len(due))

    snooze = active_snooze(alarm, state)
    if snooze is not None:
        occurrence, until = snooze
        if now >= until:
            if excused_lateness(until, now, busy) <= grace:
                return Ring(occurrence, now - until, snoozed=True)
            return Missed(occurrence, 1)
        upcoming = until if upcoming is None else min(upcoming, until)

    return Idle(upcoming)


def describe(alarm: Alarm, state: AlarmState, now: datetime, tz: tzinfo) -> Status:
    """Status for `alarm list`. 'overdue' means due but not yet handled: no runner picked it up."""
    now = to_utc(now)
    if not alarm.enabled:
        return Status("off", None)
    # Same precedence as evaluate(): a fresh due occurrence outranks an older snooze.
    first = next(occurrences(alarm, _floor(alarm, state), tz), None)
    if first is not None and first <= now:
        return Status("overdue", first)
    snooze = active_snooze(alarm, state)
    if snooze is not None:
        until = snooze[1]
        return Status("snoozed" if until > now else "overdue", until)
    if first is None:
        return Status("done", None)
    return Status("on", first)


# State transitions. The runner applies these; they are pure so the rules live in one place.


def mark_handled(state: AlarmState, occurrence: datetime) -> AlarmState:
    """Dismissed, timed out or missed: the occurrence is settled and any snooze is gone."""
    occurrence = to_utc(occurrence)
    if state.last_handled is None:
        return AlarmState(last_handled=occurrence)
    return AlarmState(last_handled=max(to_utc(state.last_handled), occurrence))


def mark_snoozed(state: AlarmState, occurrence: datetime, until: datetime) -> AlarmState:
    """The occurrence is settled (it will not fire again by itself); the snooze re-rings it."""
    handled = mark_handled(state, occurrence)
    return replace(handled, snoozed_occurrence=to_utc(occurrence), snoozed_until=to_utc(until))
