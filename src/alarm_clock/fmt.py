"""Human-readable formatting shared by the CLI and the runner."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

from alarm_clock.model import Alarm, format_days, format_time


def format_duration(delta: timedelta) -> str:
    """Compact, floor-rounded: `2d3h`, `7h38m`, `45m`, `<1m`."""
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 1:
        return "<1m"
    days, rest = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def format_when(instant: datetime, now: datetime, tz: tzinfo) -> str:
    """`Fri 24 Oct 07:30 (in 7h38m)` in local time; `(... ago)` for the past."""
    stamp = instant.astimezone(tz).strftime("%a %d %b %H:%M")
    if instant >= now:
        return f"{stamp} (in {format_duration(instant - now)})"
    return f"{stamp} ({format_duration(now - instant)} ago)"


def describe_alarm(alarm: Alarm) -> str:
    """`#1  07:30  Mon-Fri  "Standup"`"""
    parts = [f"#{alarm.id}", format_time(alarm.at), format_days(alarm.days)]
    if alarm.label:
        parts.append(f'"{alarm.label}"')
    return "  ".join(parts)
