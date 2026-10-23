from datetime import time

import pytest

from alarm_clock.model import (
    EVERY_DAY,
    ONCE,
    WEEKDAYS,
    WEEKENDS,
    AlarmError,
    format_days,
    parse_days,
    parse_label,
    parse_time,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7:30", time(7, 30)),
        ("07:30", time(7, 30)),
        ("00:00", time(0, 0)),
        ("23:59", time(23, 59)),
        ("19:05", time(19, 5)),
        (" 7:30 ", time(7, 30)),
        ("7:30pm", time(19, 30)),
        ("7:30 PM", time(19, 30)),
        ("7:30a.m.", time(7, 30)),
        ("7pm", time(19, 0)),
        ("12am", time(0, 0)),
        ("12:15am", time(0, 15)),
        ("12pm", time(12, 0)),
        ("12:45pm", time(12, 45)),
    ],
)
def test_parse_time_accepts(text: str, expected: time) -> None:
    assert parse_time(text) == expected


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("24:00", "hour must be 00-23"),
        ("7:60", "minutes must be 00-59"),
        ("13pm", "hour must be 1-12"),
        ("0am", "hour must be 1-12"),
        ("7:5", "expected HH:MM"),
        ("730", "expected HH:MM"),
        ("7", "expected HH:MM"),
        ("", "expected HH:MM"),
        ("noon", "expected HH:MM"),
        ("7:30:00", "expected HH:MM"),
    ],
)
def test_parse_time_rejects_with_reason(text: str, reason: str) -> None:
    with pytest.raises(AlarmError, match=reason):
        parse_time(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("daily", EVERY_DAY),
        ("Weekdays", WEEKDAYS),
        ("weekends", WEEKENDS),
        ("once", ONCE),
        ("mon", frozenset({0})),
        ("mon,wed,fri", frozenset({0, 2, 4})),
        ("Fri, Mon", frozenset({0, 4})),
        ("sunday,saturday", WEEKENDS),
        ("mon,mon", frozenset({0})),
    ],
)
def test_parse_days_accepts(text: str, expected: frozenset[int]) -> None:
    assert parse_days(text) == expected


@pytest.mark.parametrize("text", ["", "mo", "mon,,wed", "everyday", "mon;wed"])
def test_parse_days_rejects(text: str) -> None:
    with pytest.raises(AlarmError, match="invalid days"):
        parse_days(text)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (ONCE, "Once"),
        (EVERY_DAY, "Daily"),
        (WEEKDAYS, "Mon-Fri"),
        (WEEKENDS, "Sat, Sun"),
        (frozenset({4, 0, 2}), "Mon, Wed, Fri"),
    ],
)
def test_format_days(days: frozenset[int], expected: str) -> None:
    assert format_days(days) == expected


def test_parse_label_collapses_whitespace_and_limits_length() -> None:
    assert parse_label("  Team   standup\n") == "Team standup"
    with pytest.raises(AlarmError, match="too long"):
        parse_label("x" * 61)
