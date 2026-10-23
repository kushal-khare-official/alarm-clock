"""JSON persistence. `alarms.json` is written only by CLI commands, `state.json` only by the
runner, and every write is atomic (ADR-007)."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from typing import Any

from alarm_clock.model import DAY_NAMES, Alarm, AlarmError, AlarmState, format_time, parse_time

SCHEMA_VERSION = 1


class StoreError(Exception):
    """A data file exists but cannot be used. The message names the file; it is never modified."""


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write via a temp file in the same directory, fsync, then rename over the target.

    Readers see either the old file or the new one, never a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    """Parsed document, or None if the file does not exist."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise StoreError(f"{path} is not valid JSON ({e}). It was left untouched.") from e
    if not isinstance(data, dict):
        raise StoreError(f"{path} must contain a JSON object. It was left untouched.")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise StoreError(
            f"{path} has schema_version {version!r}; this version of alarm-clock reads "
            f"{SCHEMA_VERSION}. It was left untouched."
        )
    return data


def _instant(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError(f"timestamp without a UTC offset: {text!r}")
    return value


def _optional_instant(text: str | None) -> datetime | None:
    return None if text is None else _instant(text)


def _alarm_to_json(alarm: Alarm) -> dict[str, Any]:
    return {
        "id": alarm.id,
        "at": format_time(alarm.at),
        "days": [DAY_NAMES[d] for d in sorted(alarm.days)],
        "label": alarm.label,
        "enabled": alarm.enabled,
        "snooze_minutes": alarm.snooze_minutes,
        "anchor": alarm.anchor.isoformat(),
    }


def _alarm_from_json(raw: dict[str, Any]) -> Alarm:
    return Alarm(
        id=int(raw["id"]),
        at=parse_time(raw["at"]),
        days=frozenset(DAY_NAMES.index(d) for d in raw["days"]),
        label=str(raw["label"]),
        enabled=bool(raw["enabled"]),
        snooze_minutes=int(raw["snooze_minutes"]),
        anchor=_instant(raw["anchor"]),
    )


def _find(alarms: list[Alarm], alarm_id: int) -> Alarm:
    for alarm in alarms:
        if alarm.id == alarm_id:
            return alarm
    raise AlarmError(f"no alarm with id {alarm_id} (see `alarm list`)")


def _malformed(path: Path, error: Exception) -> StoreError:
    return StoreError(f"{path} has a malformed entry ({error!r}). It was left untouched.")


class AlarmStore:
    """Alarm definitions. IDs come from a monotonic counter and are never reused."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> tuple[int, list[Alarm]]:
        data = read_json(self.path)
        if data is None:
            return 1, []
        try:
            alarms = [_alarm_from_json(raw) for raw in data["alarms"]]
            next_id = int(data["next_id"])
        except (KeyError, TypeError, ValueError, AlarmError) as e:
            raise _malformed(self.path, e) from e
        return next_id, alarms

    def _write(self, next_id: int, alarms: list[Alarm]) -> None:
        atomic_write_json(
            self.path,
            {
                "schema_version": SCHEMA_VERSION,
                "next_id": next_id,
                "alarms": [_alarm_to_json(a) for a in sorted(alarms, key=lambda a: a.id)],
            },
        )

    def load(self) -> list[Alarm]:
        return self._read()[1]

    def get(self, alarm_id: int) -> Alarm:
        return _find(self.load(), alarm_id)

    def add(
        self, at: time, days: frozenset[int], label: str, snooze_minutes: int, now: datetime
    ) -> Alarm:
        next_id, alarms = self._read()
        alarm = Alarm(next_id, at, days, label, True, snooze_minutes, anchor=now)
        self._write(next_id + 1, [*alarms, alarm])
        return alarm

    def update(
        self,
        alarm_id: int,
        now: datetime,
        *,
        at: time | None = None,
        days: frozenset[int] | None = None,
        label: str | None = None,
        snooze_minutes: int | None = None,
    ) -> Alarm:
        """Changing `at` or `days` re-arms the alarm by resetting its anchor, which also cancels a
        pending snooze. A label or snooze-length change leaves the schedule alone."""

        def change(a: Alarm) -> Alarm:
            b = replace(
                a,
                at=a.at if at is None else at,
                days=a.days if days is None else days,
                label=a.label if label is None else label,
                snooze_minutes=a.snooze_minutes if snooze_minutes is None else snooze_minutes,
            )
            return replace(b, anchor=now) if (b.at, b.days) != (a.at, a.days) else b

        return self._modify(alarm_id, change)

    def set_enabled(self, alarm_id: int, enabled: bool, now: datetime) -> Alarm:
        """Enabling re-arms from now: a disabled alarm must not ring for times that passed while
        it was off, and enabling a finished one-shot schedules it again. Enabling an already
        enabled recurring alarm is a no-op, so it cannot cancel that alarm's snooze."""

        def change(a: Alarm) -> Alarm:
            if not enabled:
                return replace(a, enabled=False)
            if a.enabled and a.is_recurring:
                return a
            return replace(a, enabled=True, anchor=now)

        return self._modify(alarm_id, change)

    def remove(self, alarm_id: int) -> Alarm:
        next_id, alarms = self._read()
        victim = _find(alarms, alarm_id)
        self._write(next_id, [a for a in alarms if a.id != alarm_id])
        return victim

    def _modify(self, alarm_id: int, change: Callable[[Alarm], Alarm]) -> Alarm:
        next_id, alarms = self._read()
        current = _find(alarms, alarm_id)
        updated = change(current)
        if updated != current:
            self._write(next_id, [updated if a.id == alarm_id else a for a in alarms])
        return updated

    def signature(self) -> tuple[int, int, int] | None:
        """Change detector for the runner's hot reload. Every atomic write creates a new inode, so
        this changes even when two writes land within one coarse mtime tick."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size)


class StateStore:
    """Runner bookkeeping keyed by alarm id. Only the runner writes this file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[int, AlarmState]:
        data = read_json(self.path)
        if data is None:
            return {}
        try:
            return {
                int(key): AlarmState(
                    last_handled=_optional_instant(raw["last_handled"]),
                    snoozed_occurrence=_optional_instant(raw["snoozed_occurrence"]),
                    snoozed_until=_optional_instant(raw["snoozed_until"]),
                )
                for key, raw in data["alarms"].items()
            }
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise _malformed(self.path, e) from e

    def save(self, states: dict[int, AlarmState]) -> None:
        def iso(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        atomic_write_json(
            self.path,
            {
                "schema_version": SCHEMA_VERSION,
                "alarms": {
                    str(alarm_id): {
                        "last_handled": iso(s.last_handled),
                        "snoozed_occurrence": iso(s.snoozed_occurrence),
                        "snoozed_until": iso(s.snoozed_until),
                    }
                    for alarm_id, s in sorted(states.items())
                },
            },
        )
