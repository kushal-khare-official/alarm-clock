from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from alarm_clock import __version__, cli
from alarm_clock.lock import single_instance
from alarm_clock.model import AlarmState
from alarm_clock.schedule import mark_handled
from alarm_clock.store import AlarmStore, StateStore
from tests.fakes import FakeClock

NY = ZoneInfo("America/New_York")
FRIDAY_NIGHT = datetime(2026, 10, 23, 23, 52, tzinfo=NY)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(FRIDAY_NIGHT)


@pytest.fixture
def env(tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> cli.Env:
    environment = cli.Env(
        home=tmp_path,
        alarms=AlarmStore(tmp_path / "alarms.json"),
        states=StateStore(tmp_path / "state.json"),
        clock=clock,
        tz=NY,
    )
    monkeypatch.setattr(cli, "build_env", lambda: environment)
    monkeypatch.setenv("COLUMNS", "120")
    return environment


runner = CliRunner()


def alarm(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(cli.app, list(args))
    return result.exit_code, result.stdout, result.stderr


def test_version() -> None:
    code, out, _ = alarm("--version")
    assert (code, out.strip()) == (0, f"alarm-clock {__version__}")


def test_add_one_shot_shows_next_ring(env: cli.Env) -> None:
    code, out, _ = alarm("add", "7:30")
    assert code == 0
    assert out.splitlines() == ["Added #1  07:30  Once", "  next: Sat 24 Oct 07:30 (in 7h38m)"]


def test_add_recurring_with_label(env: cli.Env) -> None:
    code, out, _ = alarm("add", "07:30", "--days", "weekdays", "-l", "Standup")
    assert code == 0
    assert 'Added #1  07:30  Mon-Fri  "Standup"' in out
    assert "next: Mon 26 Oct 07:30 (in 2d7h)" in out


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["add", "25:00"], "invalid time '25:00': hour must be 00-23"),
        (["add", "7:30", "--days", "someday"], "invalid days 'someday'"),
        (["edit", "1"], "nothing to change"),
        (["rm", "9"], "no alarm with id 9"),
        (["enable", "9"], "no alarm with id 9"),
    ],
)
def test_user_errors_are_one_line_on_stderr(env: cli.Env, args: list[str], message: str) -> None:
    code, out, err = alarm(*args)
    assert code == 1
    assert out == ""
    assert err.startswith("error: ")
    assert message in err
    assert "Traceback" not in err


def test_snooze_out_of_range_is_a_usage_error(env: cli.Env) -> None:
    code, _, err = alarm("add", "7:30", "--snooze", "0")
    assert code == 2
    assert "--snooze" in err


def test_list_empty(env: cli.Env) -> None:
    assert alarm("list") == (0, "No alarms. Add one with `alarm add 07:30`.\n", "")


def test_list_shows_status_and_next(env: cli.Env) -> None:
    alarm("add", "07:30", "-d", "weekdays", "-l", "Standup")
    alarm("add", "23:55", "-l", "Bed")
    alarm("add", "9am", "-d", "sat,sun")
    alarm("disable", "3")
    code, out, _ = alarm("list")
    assert code == 0
    rows = [line.split() for line in out.splitlines() if line.strip()[:1].isdigit()]
    assert rows[0][:5] == ["1", "07:30", "Mon-Fri", "Standup", "on"]
    assert rows[1][:5] == ["2", "23:55", "Once", "Bed", "on"]
    assert "(in 3m)" in out
    assert rows[2][-2:] == ["off", "-"]


def test_list_flags_overdue_alarms_when_no_runner_handled_them(
    env: cli.Env, clock: FakeClock
) -> None:
    alarm("add", "23:55")
    clock.advance(minutes=10)
    _, out, _ = alarm("list")
    assert "overdue" in out
    assert "Is `alarm run` running?" in out


def test_finished_one_shot_is_done_and_enable_re_arms_it(env: cli.Env, clock: FakeClock) -> None:
    alarm("add", "23:55")
    rung = datetime(2026, 10, 23, 23, 55, tzinfo=NY)
    env.states.save({1: mark_handled(AlarmState(), rung)})
    clock.advance(minutes=10)
    assert "done" in alarm("list")[1]
    code, out, _ = alarm("enable", "1")
    assert code == 0
    assert "next: Sat 24 Oct 23:55" in out


def test_edit_time_re_arms_but_label_does_not(env: cli.Env, clock: FakeClock) -> None:
    alarm("add", "07:30", "-d", "daily")
    original_anchor = env.alarms.get(1).anchor
    clock.advance(minutes=1)
    assert alarm("edit", "1", "--label", "Gym")[0] == 0
    assert env.alarms.get(1).anchor == original_anchor
    code, out, _ = alarm("edit", "1", "--time", "6:45")
    assert code == 0
    assert 'Updated #1  06:45  Daily  "Gym"' in out
    assert env.alarms.get(1).anchor == clock.now()


def test_rm(env: cli.Env) -> None:
    alarm("add", "7:30", "-l", "Old")
    assert alarm("rm", "1") == (0, 'Removed #1  07:30  Once  "Old"\n', "")
    assert env.alarms.load() == []


def test_label_is_printed_literally_not_as_markup(env: cli.Env) -> None:
    _, out, _ = alarm("add", "7:30", "-l", "[bold]wake[/bold]")
    assert '"[bold]wake[/bold]"' in out


def test_corrupt_store_is_reported_with_its_path(env: cli.Env) -> None:
    env.alarms.path.write_text("{oops")
    code, _, err = alarm("list")
    assert code == 1
    assert "alarms.json is not valid JSON" in err.replace("\n", "")


def test_add_warns_when_no_runner_will_ring_it(env: cli.Env) -> None:
    code, _, err = alarm("add", "7:30")
    assert code == 0
    assert "no `alarm run` is active" in err


def test_no_warning_while_a_runner_holds_the_lock(env: cli.Env) -> None:
    with single_instance(env.runner_lock):
        code, _, err = alarm("add", "7:30")
        assert (code, err) == (0, "")
        assert alarm("edit", "1", "-l", "x")[2] == ""


def test_disable_does_not_warn(env: cli.Env) -> None:
    alarm("add", "7:30")
    assert alarm("disable", "1")[2] == ""


def test_run_refuses_to_start_a_second_runner(env: cli.Env) -> None:
    with single_instance(env.runner_lock):
        code, _, err = alarm("run", "--sound", "bell")
    assert code == 1
    assert "another `alarm run` is already running" in err


def test_run_end_to_end_without_a_terminal(env: cli.Env, clock: FakeClock) -> None:
    """Real Runner, BellRinger and RichDisplay; only the clock is fake. With no TTY there are no
    keys, so the ring runs to its timeout. Ctrl-C arrives at 00:10."""
    alarm("add", "23:55", "-l", "Bed")
    stop_at = datetime(2026, 10, 24, 0, 10, tzinfo=NY)

    def ctrl_c_later(c: FakeClock) -> None:
        if c.now() >= stop_at:
            raise KeyboardInterrupt

    clock.on_sleep = ctrl_c_later
    code, out, _ = alarm("run", "--sound", "bell", "--ring-timeout", "3")
    assert code == 0
    lines = out.replace("\a", "").splitlines()
    assert lines[0].endswith(
        "alarm run: sound terminal bell, grace 15m, rings for up to 3m. Ctrl-C to stop."
    )
    assert any(
        line.endswith("Watching 1 alarm. Next: #1 23:55 Bed at Fri 23 Oct 23:55 (in 3m)")
        for line in lines
    )
    assert "23:55:00 RINGING 23:55 Bed" in out
    assert "23:58:00 #1 23:55 Bed: no answer, silenced after 3m" in out
    assert lines[-1] == "Stopped."
    assert env.states.load()[1].last_handled == datetime(2026, 10, 23, 23, 55, tzinfo=NY)
