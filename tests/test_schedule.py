from datetime import UTC, date, datetime, time, timedelta, tzinfo
from itertools import islice
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from alarm_clock.model import EVERY_DAY, ONCE, WEEKDAYS, Alarm, AlarmState
from alarm_clock.schedule import (
    Idle,
    Missed,
    Ring,
    Status,
    describe,
    evaluate,
    mark_handled,
    mark_snoozed,
    next_occurrence,
    occurrences,
    resolve,
)

NY = ZoneInfo("America/New_York")
GRACE = timedelta(minutes=15)
FRESH = AlarmState()


def at(y: int, mo: int, d: int, h: int, mi: int = 0, s: int = 0, tz: tzinfo = NY) -> datetime:
    """A local wall time as a UTC instant (unambiguous times only)."""
    return datetime(y, mo, d, h, mi, s, tzinfo=tz).astimezone(UTC)


def alarm(
    hhmm: str = "07:30",
    days: frozenset[int] = EVERY_DAY,
    anchor: datetime | None = None,
    enabled: bool = True,
) -> Alarm:
    h, m = map(int, hhmm.split(":"))
    return Alarm(
        id=1,
        at=time(h, m),
        days=days,
        label="",
        enabled=enabled,
        snooze_minutes=9,
        anchor=anchor or at(2026, 10, 23, 0),  # scenario day, unless a test says otherwise
    )


# --- next occurrence -------------------------------------------------------------------------


def test_weekday_alarm_skips_the_weekend() -> None:
    a = alarm(days=WEEKDAYS)
    friday_after = at(2026, 10, 23, 8)  # Fri, already past 07:30
    assert next_occurrence(a, FRESH, friday_after, NY) == at(2026, 10, 26, 7, 30)  # Mon


def test_week_wraps_to_the_same_weekday() -> None:
    a = alarm(days=frozenset({6}))  # Sunday only
    assert next_occurrence(a, FRESH, at(2026, 10, 25, 7, 31), NY) == at(2026, 11, 1, 7, 30)


def test_occurrences_are_strictly_after() -> None:
    a = alarm()
    exactly = at(2026, 10, 23, 7, 30)
    assert next_occurrence(a, FRESH, exactly, NY) == at(2026, 10, 24, 7, 30)


def test_new_alarm_never_fires_for_a_time_already_past_when_created() -> None:
    a = alarm(anchor=at(2026, 10, 23, 8))  # created at 08:00 for 07:30
    assert evaluate(a, FRESH, at(2026, 10, 23, 8, 0, 1), GRACE, NY) == Idle(at(2026, 10, 24, 7, 30))


def test_one_shot_has_exactly_one_occurrence() -> None:
    a = alarm(days=ONCE, anchor=at(2026, 10, 23, 8))
    assert list(occurrences(a, a.anchor, NY)) == [at(2026, 10, 24, 7, 30)]
    handled = mark_handled(FRESH, at(2026, 10, 24, 7, 30))
    assert next_occurrence(a, handled, at(2026, 10, 24, 7, 31), NY) is None


# --- evaluate: ring, grace, missed -----------------------------------------------------------


def test_rings_on_time() -> None:
    occurrence = at(2026, 10, 23, 7, 30)
    assert evaluate(alarm(), FRESH, occurrence, GRACE, NY) == Ring(occurrence, timedelta(0))


def test_grace_boundary_is_inclusive() -> None:
    occurrence = at(2026, 10, 23, 7, 30)
    assert evaluate(alarm(), FRESH, occurrence + GRACE, GRACE, NY) == Ring(occurrence, GRACE)
    one_second_more = occurrence + GRACE + timedelta(seconds=1)
    assert evaluate(alarm(), FRESH, one_second_more, GRACE, NY) == Missed(occurrence, 1)


def test_days_offline_report_one_missed_with_a_count() -> None:
    state = mark_handled(FRESH, at(2026, 10, 20, 7, 30))
    # Runner comes back Fri at noon: Wed, Thu, Fri 07:30 all passed unhandled.
    a = alarm(anchor=at(2026, 10, 1, 0))
    decision = evaluate(a, state, at(2026, 10, 23, 12), GRACE, NY)
    assert decision == Missed(at(2026, 10, 23, 7, 30), 3)


def test_latest_occurrence_within_grace_rings_and_reports_skipped() -> None:
    state = mark_handled(FRESH, at(2026, 10, 20, 7, 30))
    now = at(2026, 10, 23, 7, 35)
    assert evaluate(alarm(anchor=at(2026, 10, 1, 0)), state, now, GRACE, NY) == Ring(
        at(2026, 10, 23, 7, 30), timedelta(minutes=5), skipped=2
    )


def test_clock_moved_backwards_does_not_ring_twice() -> None:
    occurrence = at(2026, 10, 23, 7, 30)
    state = mark_handled(FRESH, occurrence)
    # User sets the clock back an hour, then time passes 07:30 again.
    decision = evaluate(alarm(), state, occurrence + timedelta(seconds=30), GRACE, NY)
    assert decision == Idle(at(2026, 10, 24, 7, 30))


def test_disabled_alarm_is_idle_with_nothing_scheduled() -> None:
    a = alarm(enabled=False)
    assert evaluate(a, FRESH, at(2026, 10, 23, 7, 30), GRACE, NY) == Idle(None)


# --- snooze ----------------------------------------------------------------------------------


def snoozed_at_0730() -> tuple[datetime, AlarmState]:
    occurrence = at(2026, 10, 23, 7, 30)
    return occurrence, mark_snoozed(FRESH, occurrence, occurrence + timedelta(minutes=9))


def test_snooze_pending_then_rings_again() -> None:
    occurrence, state = snoozed_at_0730()
    until = occurrence + timedelta(minutes=9)
    assert evaluate(alarm(), state, until - timedelta(seconds=1), GRACE, NY) == Idle(until)
    assert evaluate(alarm(), state, until, GRACE, NY) == Ring(
        occurrence, timedelta(0), snoozed=True
    )


def test_snooze_expired_beyond_grace_is_missed() -> None:
    occurrence, state = snoozed_at_0730()
    late = occurrence + timedelta(minutes=9) + GRACE + timedelta(seconds=1)
    assert evaluate(alarm(), state, late, GRACE, NY) == Missed(occurrence, 1)


def test_editing_the_alarm_cancels_its_snooze() -> None:
    occurrence, state = snoozed_at_0730()
    edited = alarm(anchor=occurrence + timedelta(minutes=2))
    until = occurrence + timedelta(minutes=9)
    assert evaluate(edited, state, until, GRACE, NY) == Idle(at(2026, 10, 24, 7, 30))


def test_fresh_occurrence_supersedes_a_stale_snooze() -> None:
    _, state = snoozed_at_0730()
    tomorrow = at(2026, 10, 24, 7, 30)
    assert evaluate(alarm(), state, tomorrow, GRACE, NY) == Ring(tomorrow, timedelta(0))


def test_mark_handled_clears_snooze_and_never_moves_watermark_back() -> None:
    occurrence, state = snoozed_at_0730()
    handled = mark_handled(state, occurrence - timedelta(days=1))
    assert handled == AlarmState(last_handled=occurrence)


# --- DST (ADR-009, ADR-012) ------------------------------------------------------------------


def test_nonexistent_time_shifts_forward_by_the_gap() -> None:
    # 2026-03-08 New York: 02:00 EST jumps to 03:00 EDT. 02:30 does not exist.
    resolved = resolve(date(2026, 3, 8), time(2, 30), NY)
    assert resolved.astimezone(NY).replace(tzinfo=None) == datetime(2026, 3, 8, 3, 30)


def test_ambiguous_time_fires_once_at_first_occurrence() -> None:
    # 2026-11-01 New York: 01:00-02:00 happens twice (EDT, then EST).
    a = alarm("01:30", anchor=at(2026, 10, 31, 12))
    first = datetime(2026, 11, 1, 1, 30, fold=0, tzinfo=NY).astimezone(UTC)
    second = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=NY).astimezone(UTC)
    assert second - first == timedelta(hours=1)
    assert evaluate(a, FRESH, first, GRACE, NY) == Ring(first, timedelta(0))
    after_first = mark_handled(FRESH, first)
    assert evaluate(a, after_first, second, GRACE, NY) == Idle(at(2026, 11, 2, 1, 30))


def test_lateness_is_real_elapsed_time_across_the_jump() -> None:
    # 01:50 EST, and the runner wakes at 03:05 EDT. The wall clock says 75 minutes passed;
    # in reality 15 did. Same-tzinfo datetime subtraction would get this wrong (ADR-012).
    a = alarm("01:50", anchor=at(2026, 3, 7, 12))
    occurrence = at(2026, 3, 8, 1, 50)
    now = datetime(2026, 3, 8, 3, 5, tzinfo=NY)
    assert evaluate(a, FRESH, now, GRACE, NY) == Ring(occurrence, timedelta(minutes=15))


def test_weekly_cadence_holds_across_dst() -> None:
    a = alarm("07:30", days=frozenset({6}))
    before, after = islice(occurrences(a, at(2026, 10, 24, 0), NY), 2)  # Sun 25 Oct, Sun 1 Nov
    assert before.astimezone(NY).hour == after.astimezone(NY).hour == 7
    assert after - before == timedelta(days=7, hours=1)  # the fall-back week is 169 hours long


# --- describe (alarm list) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "state", "now", "expected"),
    [
        (alarm(enabled=False), FRESH, at(2026, 10, 23, 6), Status("off", None)),
        (alarm(), FRESH, at(2026, 10, 23, 6), Status("on", at(2026, 10, 23, 7, 30))),
        (alarm(), FRESH, at(2026, 10, 23, 9), Status("overdue", at(2026, 10, 23, 7, 30))),
        (
            alarm(days=ONCE, anchor=at(2026, 10, 23, 6)),
            mark_handled(FRESH, at(2026, 10, 23, 7, 30)),
            at(2026, 10, 23, 9),
            Status("done", None),
        ),
        (
            alarm(),
            snoozed_at_0730()[1],
            at(2026, 10, 23, 7, 31),
            Status("snoozed", at(2026, 10, 23, 7, 39)),
        ),
        (
            alarm(),
            snoozed_at_0730()[1],
            at(2026, 10, 23, 8),
            Status("overdue", at(2026, 10, 23, 7, 39)),
        ),
    ],
    ids=["off", "on", "overdue", "done", "snoozed", "snooze-overdue"],
)
def test_describe(a: Alarm, state: AlarmState, now: datetime, expected: Status) -> None:
    assert describe(a, state, now, NY) == expected


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        evaluate(alarm(), FRESH, datetime(2026, 10, 23, 7, 30), GRACE, NY)


# --- properties ------------------------------------------------------------------------------

instants = st.datetimes(
    min_value=datetime(2020, 1, 1), max_value=datetime(2035, 1, 1), timezones=st.just(UTC)
)
wall_times = st.times().map(lambda t: t.replace(second=0, microsecond=0))
day_sets = st.frozensets(st.integers(0, 6), min_size=1)


@settings(max_examples=60)
@given(after=instants, hhmm=wall_times, days=day_sets)
def test_next_occurrence_matches_brute_force_minute_scan(
    after: datetime, hhmm: time, days: frozenset[int]
) -> None:
    """Independent oracle: walk forward minute by minute in UTC until one matches."""
    a = Alarm(1, hhmm, days, "", True, 9, anchor=datetime(2019, 1, 1, tzinfo=UTC))
    got = next_occurrence(a, FRESH, after, UTC)

    probe = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while not (probe.weekday() in days and probe.time() == hhmm):
        probe += timedelta(minutes=1)
    assert got == probe


@given(after=instants, hhmm=wall_times, days=day_sets)
def test_next_occurrence_invariants_in_a_dst_zone(
    after: datetime, hhmm: time, days: frozenset[int]
) -> None:
    a = Alarm(1, hhmm, days, "", True, 9, anchor=datetime(2019, 1, 1, tzinfo=UTC))
    got = next_occurrence(a, FRESH, after, NY)
    assert got is not None
    assert got > after
    assert got - after <= timedelta(days=7, hours=1)
    assert got.astimezone(NY).weekday() in days
    # Minimal: nothing between `after` and `got`, so asking from just before `got` returns `got`.
    assert next_occurrence(a, FRESH, got - timedelta(microseconds=1), NY) == got


@given(
    offsets=st.lists(st.integers(0, 10 * 24 * 60), min_size=1, max_size=60),
    days=day_sets,
)
def test_no_occurrence_rings_twice_even_when_the_clock_jumps_around(
    offsets: list[int], days: frozenset[int]
) -> None:
    """Simulate a runner whose clock moves forwards and backwards arbitrarily."""
    start = at(2026, 10, 20, 0)
    a = alarm(days=days, anchor=start)
    state = FRESH
    rung: list[datetime] = []
    for minutes in offsets:
        decision = evaluate(a, state, start + timedelta(minutes=minutes), GRACE, NY)
        if isinstance(decision, Ring):
            rung.append(decision.occurrence)
            state = mark_handled(state, decision.occurrence)
        elif isinstance(decision, Missed):
            state = mark_handled(state, decision.occurrence)
    assert len(rung) == len(set(rung))
