"""The foreground loop that rings alarms (ADR-001).

Thin by design: each tick asks the pure scheduler what to do (ADR-005), and applies the answer
through the injected Clock, Ringer, KeyReader and Display. The runner owns the wording of every
message; the Display only renders it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Literal, Protocol

from alarm_clock.clock import Clock
from alarm_clock.fmt import format_duration, format_when
from alarm_clock.keys import KeyReader
from alarm_clock.model import Alarm, AlarmState, format_time
from alarm_clock.ringer import Ringer
from alarm_clock.schedule import (
    Decision,
    Idle,
    Interval,
    Missed,
    Ring,
    evaluate,
    mark_handled,
    mark_snoozed,
)
from alarm_clock.store import AlarmStore, StateStore, StoreError

Outcome = Literal["dismissed", "snoozed", "unanswered", "interrupted"]


@dataclass(frozen=True)
class RunnerConfig:
    grace: timedelta = timedelta(minutes=15)
    ring_timeout: timedelta = timedelta(minutes=10)
    tick: float = 1.0  # seconds between checks while idle
    key_poll: float = 0.25  # seconds between countdown refreshes while ringing


DEFAULT_CONFIG = RunnerConfig()


class Display(Protocol):
    def event(self, text: str) -> None:
        """A line of history: started, dismissed, missed..."""
        ...

    def ring_started(self, title: str, detail: str) -> None: ...

    def ring_tick(self, prompt: str) -> None: ...

    def ring_ended(self) -> None: ...


class Runner:
    def __init__(
        self,
        alarms: AlarmStore,
        states: StateStore,
        clock: Clock,
        tz: tzinfo,
        ringer: Ringer,
        keys: KeyReader,
        display: Display,
        config: RunnerConfig = DEFAULT_CONFIG,
    ) -> None:
        self._alarm_store = alarms
        self._state_store = states
        self._clock = clock
        self._tz = tz
        self._ringer = ringer
        self._keys = keys
        self._display = display
        self._config = config
        self._alarms: list[Alarm] = []
        self._signature: tuple[int, int, int] | None = None
        self._states = states.load()
        self._last_watching: tuple[int, int | None, datetime | None] | None = None
        self._busy: list[Interval] = []  # recent ring windows, excused from lateness (ADR-015)

    # --- loop ----------------------------------------------------------------------------------

    def run(self, until: Callable[[], bool] = lambda: False) -> None:
        """Run until `until()` is true (tests) or KeyboardInterrupt (people)."""
        try:
            while not until():
                self.tick()
                self._clock.sleep(self._config.tick)
        finally:
            self._ringer.stop()

    def tick(self) -> None:
        self._reload_if_changed()
        upcoming: list[tuple[datetime, Alarm]] = []
        for alarm_id in [a.id for a in self._alarms]:
            # Look the alarm up afresh: a ring before this one may have taken minutes, during
            # which the CLI could have edited, disabled or removed it.
            alarm = next((a for a in self._alarms if a.id == alarm_id), None)
            if alarm is None:
                continue
            decision = self._evaluate(alarm)
            if isinstance(decision, Ring):
                self._ring(alarm, self._state(alarm), decision)
                self._reload_if_changed()
            elif isinstance(decision, Missed):
                self._record(alarm.id, mark_handled(self._state(alarm), decision.occurrence))
                self._display.event(self._missed_text(alarm, decision))
            if not isinstance(decision, Idle):  # handled now; what's its next time?
                refreshed = next((a for a in self._alarms if a.id == alarm_id), None)
                if refreshed is None:
                    continue
                alarm, decision = refreshed, self._evaluate(refreshed)
            if isinstance(decision, Idle) and decision.next_at is not None:
                upcoming.append((decision.next_at, alarm))
        self._show_watching(upcoming)

    def _state(self, alarm: Alarm) -> AlarmState:
        return self._states.get(alarm.id, AlarmState())

    def _evaluate(self, alarm: Alarm) -> Decision:
        now = self._clock.now()
        # Noticing an alarm within one tick counts as on time, so `--grace 0` still rings.
        grace = self._config.grace + timedelta(seconds=self._config.tick)
        horizon = now - grace - self._config.ring_timeout - timedelta(days=1)
        self._busy = [(start, end) for start, end in self._busy if end > horizon]
        return evaluate(alarm, self._state(alarm), now, grace, self._tz, self._busy)

    # --- ringing -------------------------------------------------------------------------------

    def _ring(self, alarm: Alarm, state: AlarmState, ring: Ring) -> None:
        self._keys.flush()  # a key pressed before the ring must not answer it (ADR-014)
        started = self._clock.now()
        outcome: Outcome = "unanswered"
        try:  # opened before the sound starts, so Ctrl-C at any point still settles the ring
            self._ringer.start()
            deadline = started + self._config.ring_timeout
            self._display.ring_started(self._ring_title(alarm), self._ring_detail(ring))
            while (remaining := deadline - self._clock.now()) > timedelta(0):
                seconds = int(remaining.total_seconds())
                self._display.ring_tick(
                    f"[s] snooze {alarm.snooze_minutes}m   [d] dismiss   "
                    f"silences in {seconds // 60}:{seconds % 60:02d}"
                )
                wait = min(self._config.key_poll, remaining.total_seconds())
                key = self._keys.read_key(wait)
                if key == "s":
                    outcome = "snoozed"
                    break
                if key == "d":
                    outcome = "dismissed"
                    break
        except KeyboardInterrupt:
            outcome = "interrupted"
            raise
        finally:
            self._ringer.stop()
            self._display.ring_ended()
            self._busy.append((started, self._clock.now()))
            self._settle(alarm, state, ring, outcome)

    def _settle(self, alarm: Alarm, state: AlarmState, ring: Ring, outcome: Outcome) -> None:
        name = self._name(alarm)
        if outcome == "snoozed":
            until = self._clock.now() + timedelta(minutes=alarm.snooze_minutes)
            self._record(alarm.id, mark_snoozed(state, ring.occurrence, until))
            self._display.event(f"{name}: snoozed until {self._local(until)}")
            return
        self._record(alarm.id, mark_handled(state, ring.occurrence))
        text = {
            "dismissed": "dismissed",
            "unanswered": (
                f"no answer, silenced after {format_duration(self._config.ring_timeout)}"
            ),
            "interrupted": "dismissed (runner stopped with Ctrl-C)",
        }[outcome]
        self._display.event(f"{name}: {text}")

    # --- state and reload ----------------------------------------------------------------------

    def _record(self, alarm_id: int, state: AlarmState) -> None:
        self._states[alarm_id] = state
        live = {a.id for a in self._alarms}
        self._states = {k: v for k, v in self._states.items() if k in live}
        self._state_store.save(self._states)

    def _reload_if_changed(self) -> None:
        signature = self._alarm_store.signature()
        if signature == self._signature:
            return
        self._signature = signature
        try:
            self._alarms = self._alarm_store.load()
        except StoreError as e:
            # Keep ringing what we had. Reported once per broken version of the file.
            self._display.event(f"could not reload alarms, keeping the previous set: {e}")

    # --- text ----------------------------------------------------------------------------------

    def _local(self, instant: datetime) -> str:
        return instant.astimezone(self._tz).strftime("%H:%M")

    def _name(self, alarm: Alarm) -> str:
        label = f" {alarm.label}" if alarm.label else ""
        return f"#{alarm.id} {format_time(alarm.at)}{label}"

    def _ring_title(self, alarm: Alarm) -> str:
        return " ".join(filter(None, [format_time(alarm.at), alarm.label]))

    def _ring_detail(self, ring: Ring) -> str:
        parts = []
        if ring.snoozed:
            parts.append("snoozed alarm")
        if ring.late_by >= timedelta(minutes=1):
            parts.append(f"{format_duration(ring.late_by)} late")
        if ring.skipped:
            parts.append(f"also missed {ring.skipped} earlier")
        return ", ".join(parts)

    def _missed_text(self, alarm: Alarm, missed: Missed) -> str:
        now = self._clock.now()
        what = "missed" if missed.count == 1 else f"missed {missed.count} times, most recently"
        return (
            f"{self._name(alarm)}: {what} {format_when(missed.occurrence, now, self._tz)}. "
            f"Too late to ring: the grace window is {format_duration(self._config.grace)}."
        )

    def _show_watching(self, upcoming: list[tuple[datetime, Alarm]]) -> None:
        now = self._clock.now()
        enabled = sum(a.enabled for a in self._alarms)
        first = min(upcoming, key=lambda pair: pair[0], default=None)
        key = (enabled, first[1].id if first else None, first[0] if first else None)
        if key == self._last_watching:
            return
        self._last_watching = key
        plural = "" if enabled == 1 else "s"
        text = f"Watching {enabled} alarm{plural}."
        if first:
            text += f" Next: {self._name(first[1])} at {format_when(first[0], now, self._tz)}"
        self._display.event(text)
