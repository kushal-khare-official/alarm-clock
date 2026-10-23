"""Runner scenarios against a fake clock: whole mornings in milliseconds, no real sleeping."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alarm_clock.model import EVERY_DAY, AlarmState
from alarm_clock.runner import DEFAULT_CONFIG, Runner, RunnerConfig
from alarm_clock.store import AlarmStore, StateStore
from tests.fakes import INTERRUPT, FakeClock, FakeRinger, RecordingDisplay, ScriptedKeys

NY = ZoneInfo("America/New_York")


def ny(h: int, m: int = 0, s: int = 0, day: int = 23) -> datetime:
    """Local New York wall time on Fri 23 Oct 2026, as a UTC instant."""
    return datetime(2026, 10, day, h, m, s, tzinfo=NY).astimezone(UTC)


@dataclass
class World:
    clock: FakeClock
    alarms: AlarmStore
    states: StateStore
    ringer: FakeRinger
    display: RecordingDisplay
    keys: ScriptedKeys

    def runner(self, config: RunnerConfig = DEFAULT_CONFIG) -> Runner:
        return Runner(
            self.alarms, self.states, self.clock, NY, self.ringer, self.keys, self.display, config
        )

    def run_until(self, instant: datetime, config: RunnerConfig = DEFAULT_CONFIG) -> None:
        self.runner(config).run(until=lambda: self.clock.now() >= instant)

    def press(self, *presses: tuple[datetime, str]) -> None:
        self.keys = ScriptedKeys(self.clock, list(presses))

    def state(self, alarm_id: int = 1) -> AlarmState:
        return self.states.load().get(alarm_id, AlarmState())


@pytest.fixture
def world(tmp_path: Path) -> World:
    clock = FakeClock(ny(7, 0))
    return World(
        clock=clock,
        alarms=AlarmStore(tmp_path / "alarms.json"),
        states=StateStore(tmp_path / "state.json"),
        ringer=FakeRinger(clock),
        display=RecordingDisplay(),
        keys=ScriptedKeys(clock),
    )


def standup(world: World, at: time = time(7, 30), label: str = "Standup") -> None:
    world.alarms.add(at, EVERY_DAY, label, 9, now=world.clock.now())


def test_rings_on_time_and_dismiss_settles_it(world: World) -> None:
    standup(world)
    world.press((ny(7, 30, 5), "d"))
    world.run_until(ny(7, 32))
    assert world.ringer.starts == [ny(7, 30)]
    assert ("stop", ny(7, 30, 5)) in world.ringer.log
    assert world.display.rings == [("07:30 Standup", "")]
    assert "#1 07:30 Standup: dismissed" in world.display.events
    assert world.state() == AlarmState(last_handled=ny(7, 30))
    assert world.display.prompts[0] == "[s] snooze 9m   [d] dismiss   silences in 10:00"


def test_snooze_rings_again_after_the_snooze_length(world: World) -> None:
    standup(world)
    world.press((ny(7, 30, 3), "s"), (ny(7, 39, 10), "d"))
    world.run_until(ny(7, 45))
    first, second = world.ringer.starts
    assert first == ny(7, 30)
    assert ny(7, 39, 3) <= second <= ny(7, 39, 4)  # within one idle tick of the snooze end
    assert "#1 07:30 Standup: snoozed until 07:39" in world.display.events
    assert world.display.rings[1] == ("07:30 Standup", "snoozed alarm")
    assert world.state() == AlarmState(last_handled=ny(7, 30))  # snooze cleared on dismiss


def test_unanswered_ring_silences_itself_after_the_timeout(world: World) -> None:
    standup(world)
    world.run_until(ny(7, 45))
    assert world.ringer.log[:2] == [("start", ny(7, 30)), ("stop", ny(7, 40))]
    assert "#1 07:30 Standup: no answer, silenced after 10m" in world.display.events
    assert world.state().last_handled == ny(7, 30)


def sleep_through(world: World, wake_at: datetime) -> None:
    """The laptop lid closes after the first idle tick and opens at `wake_at`."""
    slept = False

    def hook(clock: FakeClock) -> None:
        nonlocal slept
        if not slept:
            slept = True
            clock.set(wake_at)

    world.clock.on_sleep = hook


def test_wakes_ten_minutes_late_and_still_rings(world: World) -> None:
    standup(world)
    world.clock.set(ny(7, 25))
    sleep_through(world, ny(7, 40))
    world.press((ny(7, 40, 2), "d"))
    world.run_until(ny(7, 41))
    assert world.ringer.starts == [ny(7, 40)]
    assert world.display.rings == [("07:30 Standup", "10m late")]


def test_wakes_two_hours_late_and_reports_missed(world: World) -> None:
    standup(world)
    world.clock.set(ny(7, 25))
    sleep_through(world, ny(9, 30))
    world.run_until(ny(9, 31))
    assert world.ringer.starts == []
    missed = [e for e in world.display.events if "missed" in e]
    assert missed == [
        "#1 07:30 Standup: missed Fri 23 Oct 07:30 (2h ago). "
        "Too late to ring: the grace window is 15m."
    ]
    assert world.state().last_handled == ny(7, 30)


def test_restart_resumes_a_pending_snooze_and_never_re_rings_a_handled_one(world: World) -> None:
    standup(world)
    world.press((ny(7, 30, 3), "s"))
    world.run_until(ny(7, 31))  # first runner snoozes, then is stopped
    world.press((ny(7, 39, 10), "d"))
    world.run_until(ny(7, 45))  # a brand-new runner over the same files
    assert len(world.ringer.starts) == 2
    assert world.display.rings[1][1] == "snoozed alarm"
    world.clock.set(ny(7, 30))  # and even with the clock wound back, a third runner stays quiet
    world.run_until(ny(7, 35))
    assert len(world.ringer.starts) == 2


def test_alarm_added_by_the_cli_while_running_is_picked_up(world: World) -> None:
    other_process = AlarmStore(world.alarms.path)

    def hook(clock: FakeClock) -> None:
        if clock.now() == ny(7, 10):
            other_process.add(time(7, 15), EVERY_DAY, "Tea", 9, now=clock.now())

    world.clock.on_sleep = hook
    world.press((ny(7, 15, 2), "d"))
    world.run_until(ny(7, 16))
    assert world.ringer.starts == [ny(7, 15)]


def test_editing_the_time_while_snoozed_cancels_the_snooze(world: World) -> None:
    standup(world)
    cli = AlarmStore(world.alarms.path)

    def hook(clock: FakeClock) -> None:
        if clock.now() == ny(7, 35):
            cli.update(1, clock.now(), at=time(8, 0))

    world.clock.on_sleep = hook
    world.press((ny(7, 30, 3), "s"), (ny(8, 0, 2), "d"))
    world.run_until(ny(8, 1))
    assert world.ringer.starts == [ny(7, 30), ny(8, 0)]  # nothing at 07:39


def test_ctrl_c_while_ringing_stops_the_sound_and_dismisses(world: World) -> None:
    standup(world)
    world.press((ny(7, 30, 2), INTERRUPT))
    with pytest.raises(KeyboardInterrupt):
        world.run_until(ny(7, 45))
    assert not world.ringer.ringing
    assert world.state().last_handled == ny(7, 30)
    assert "#1 07:30 Standup: dismissed (runner stopped with Ctrl-C)" in world.display.events


def test_key_pressed_before_the_ring_does_not_answer_it(world: World) -> None:
    standup(world)
    world.press((ny(7, 20), "d"), (ny(7, 30, 30), "s"))  # 'd' typed while idle
    world.run_until(ny(7, 31))
    assert world.keys.flushed == ["d"]
    assert "#1 07:30 Standup: snoozed until 07:39" in world.display.events


def test_corrupt_alarms_file_keeps_the_previous_alarms(world: World) -> None:
    standup(world)

    def hook(clock: FakeClock) -> None:
        if clock.now() == ny(7, 10):
            world.alarms.path.write_text("{half-written by a text editor")

    world.clock.on_sleep = hook
    world.press((ny(7, 30, 2), "d"))
    world.run_until(ny(7, 31))
    complaints = [e for e in world.display.events if e.startswith("could not reload alarms")]
    assert len(complaints) == 1  # once per broken version, not once per second
    assert world.ringer.starts == [ny(7, 30)]


def test_two_alarms_due_together_ring_one_after_the_other(world: World) -> None:
    standup(world)
    standup(world, label="Vitamins")
    world.press((ny(7, 30, 2), "d"), (ny(7, 30, 4), "d"))
    world.run_until(ny(7, 31))
    assert world.ringer.starts == [ny(7, 30), ny(7, 30, 2)]
    assert world.state(1).last_handled == world.state(2).last_handled == ny(7, 30)


def test_watching_line_only_changes_when_the_next_alarm_does(world: World) -> None:
    standup(world)
    world.press((ny(7, 30, 2), "d"))
    world.run_until(ny(7, 45))
    watching = [e for e in world.display.events if e.startswith("Watching")]
    assert watching == [
        "Watching 1 alarm. Next: #1 07:30 Standup at Fri 23 Oct 07:30 (in 30m)",
        "Watching 1 alarm. Next: #1 07:30 Standup at Sat 24 Oct 07:30 (in 23h59m)",
    ]


def test_disabled_alarms_never_ring(world: World) -> None:
    standup(world)
    world.alarms.set_enabled(1, False, world.clock.now())
    world.run_until(ny(7, 45))
    assert world.ringer.starts == []
    assert "Watching 0 alarms." in world.display.events


def test_ring_timeout_and_grace_are_configurable(world: World) -> None:
    standup(world)
    config = RunnerConfig(grace=timedelta(minutes=1), ring_timeout=timedelta(minutes=2))
    world.run_until(ny(7, 35), config)
    assert world.ringer.log[:2] == [("start", ny(7, 30)), ("stop", ny(7, 32))]


# --- regressions from the code review (VALIDATION.md) ---------------------------------------


def test_grace_zero_still_rings_an_alarm_noticed_within_one_tick(world: World) -> None:
    standup(world)
    world.clock.set(ny(7, 29, 59) + timedelta(milliseconds=600))  # ticks land at .6s
    world.press((ny(7, 30, 3), "d"))
    world.run_until(ny(7, 31), RunnerConfig(grace=timedelta(0)))
    assert world.ringer.starts == [ny(7, 30) + timedelta(milliseconds=600)]


@pytest.mark.parametrize("ringing_first", [True, False], ids=["later-in-list", "earlier-in-list"])
def test_alarm_due_while_another_rings_is_not_counted_late(
    world: World, ringing_first: bool
) -> None:
    """Grace 5m, ring timeout 10m: B comes due 2 minutes into A's unanswered ring."""
    world.clock.set(ny(6, 59))
    if ringing_first:
        standup(world, at=time(7, 0), label="A")
        standup(world, at=time(7, 2), label="B")
    else:
        standup(world, at=time(7, 2), label="B")
        standup(world, at=time(7, 0), label="A")
    config = RunnerConfig(grace=timedelta(minutes=5), ring_timeout=timedelta(minutes=10))
    world.run_until(ny(7, 25), config)
    assert [title for title, _ in world.display.rings] == ["07:00 A", "07:02 B"]
    assert world.display.rings[1][1] == "8m late"
    assert not [e for e in world.display.events if "missed" in e]


def test_lateness_from_the_laptop_sleeping_is_still_not_excused(world: World) -> None:
    standup(world)
    world.clock.set(ny(7, 25))
    sleep_through(world, ny(7, 50))  # 20 minutes late, and not because we were ringing
    world.run_until(ny(7, 51))
    assert world.ringer.starts == []


def test_alarm_disabled_while_another_rings_does_not_ring_after_it(world: World) -> None:
    standup(world, label="A")
    standup(world, label="B")
    cli = AlarmStore(world.alarms.path)

    class DisablesBOnFirstRing(FakeRinger):
        def start(self) -> None:
            if not self.starts:
                cli.set_enabled(2, False, world.clock.now())
            super().start()

    world.ringer = DisablesBOnFirstRing(world.clock)
    world.press((ny(7, 30, 2), "d"), (ny(7, 30, 4), "d"))
    world.run_until(ny(7, 31))
    assert [title for title, _ in world.display.rings] == ["07:30 A"]


def test_ctrl_c_as_the_sound_starts_still_settles_the_ring(world: World) -> None:
    standup(world)

    class InterruptedOnStart(FakeRinger):
        def start(self) -> None:
            super().start()
            raise KeyboardInterrupt

    world.ringer = InterruptedOnStart(world.clock)
    with pytest.raises(KeyboardInterrupt):
        world.run_until(ny(7, 31))
    assert world.state().last_handled == ny(7, 30)
    assert not world.ringer.ringing


def test_sound_failure_is_reported_once_not_hidden(world: World) -> None:
    """Found while dogfooding: the bell fallback worked, but nothing told the user."""
    standup(world)
    standup(world, at=time(7, 40))
    world.ringer.failed = OSError("afplay: no output device")
    world.press((ny(7, 30, 2), "d"), (ny(7, 40, 2), "d"))
    world.run_until(ny(7, 41))
    reports = [e for e in world.display.events if e.startswith("sound failed")]
    assert reports == ["sound failed (afplay: no output device); rang the terminal bell instead"]
