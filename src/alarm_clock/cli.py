"""Command-line interface: a thin Typer layer over the store and the scheduler."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from alarm_clock import __version__
from alarm_clock.clock import Clock, SystemClock, local_zone
from alarm_clock.fmt import describe_alarm, format_when
from alarm_clock.lock import runner_active
from alarm_clock.model import (
    SNOOZE_DEFAULT,
    SNOOZE_MAX,
    SNOOZE_MIN,
    Alarm,
    AlarmError,
    AlarmState,
    format_days,
    format_time,
    parse_days,
    parse_label,
    parse_time,
)
from alarm_clock.schedule import Status, describe
from alarm_clock.store import AlarmStore, StateStore, StoreError

HOME_ENV = "ALARM_CLOCK_HOME"

app = typer.Typer(
    name="alarm",
    help="A terminal alarm clock. Save alarms, then keep `alarm run` open to ring them.",
    no_args_is_help=True,
    add_completion=False,
)


@dataclass
class Env:
    """Everything a command needs from the outside world. Tests substitute their own."""

    home: Path
    alarms: AlarmStore
    states: StateStore
    clock: Clock
    tz: tzinfo

    @property
    def runner_lock(self) -> Path:
        return self.home / "runner.lock"


def build_env() -> Env:
    home = Path(os.environ.get(HOME_ENV) or typer.get_app_dir("alarm-clock"))
    return Env(
        home=home,
        alarms=AlarmStore(home / "alarms.json"),
        states=StateStore(home / "state.json"),
        clock=SystemClock(),
        tz=local_zone(),
    )


def _out() -> Console:
    return Console(highlight=False)


def _err() -> Console:
    return Console(stderr=True, highlight=False)


@contextmanager
def _user_errors() -> Iterator[None]:
    """Expected failures become one clear line on stderr and exit code 1, not a traceback."""
    try:
        yield
    except (AlarmError, StoreError) as e:
        _err().print(f"[red]error:[/red] {escape(str(e))}")
        raise typer.Exit(1) from e


_STATUS_STYLE = {
    "on": "green",
    "off": "dim",
    "snoozed": "yellow",
    "overdue": "bold red",
    "done": "dim",
}


def _status_text(status: Status, env: Env) -> str:
    if status.next_at is None:
        return status.kind
    when = format_when(status.next_at, env.clock.now(), env.tz)
    return {
        "on": f"next: {when}",
        "snoozed": f"snoozed until {when}",
        "overdue": f"overdue since {when}",
    }.get(status.kind, status.kind)


def _report(verb: str, alarm: Alarm, env: Env, *, check_runner: bool = True) -> None:
    state = env.states.load().get(alarm.id, AlarmState())
    status = describe(alarm, state, env.clock.now(), env.tz)
    out = _out()
    out.print(f"{verb} {escape(describe_alarm(alarm))}")
    out.print(f"  [{_STATUS_STYLE[status.kind]}]{escape(_status_text(status, env))}[/]")
    # The main way this design fails is an alarm saved with nothing running to ring it (ADR-001).
    if check_runner and alarm.enabled and not runner_active(env.runner_lock):
        _err().print(
            "[yellow]warning:[/yellow] no `alarm run` is active, so nothing will ring. "
            "Start one in another terminal: [bold]alarm run[/bold]"
        )


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"alarm-clock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Show version and exit."
        ),
    ] = False,
) -> None:
    """A terminal alarm clock."""


TimeArg = Annotated[str, typer.Argument(metavar="TIME", help="e.g. 07:30, 19:05, 7:30pm")]
DaysOpt = Annotated[
    str | None,
    typer.Option(
        "--days", "-d", help="once (default), daily, weekdays, weekends, or e.g. mon,wed,fri"
    ),
]
LabelOpt = Annotated[str | None, typer.Option("--label", "-l", help="Shown when it rings.")]
SnoozeOpt = Annotated[
    int | None,
    typer.Option(min=SNOOZE_MIN, max=SNOOZE_MAX, help="Snooze length in minutes."),
]
IdArg = Annotated[int, typer.Argument(metavar="ID", help="Alarm id from `alarm list`.")]


@app.command()
def add(
    when: TimeArg, days: DaysOpt = None, label: LabelOpt = None, snooze: SnoozeOpt = None
) -> None:
    """Add an alarm."""
    with _user_errors():
        env = build_env()
        alarm = env.alarms.add(
            parse_time(when),
            parse_days(days or "once"),
            parse_label(label or ""),
            SNOOZE_DEFAULT if snooze is None else snooze,
            now=env.clock.now(),
        )
        _report("Added", alarm, env)


@app.command(name="list")
def list_alarms() -> None:
    """List alarms with their status and next ring time."""
    with _user_errors():
        env = build_env()
        alarms = env.alarms.load()
        out = _out()
        if not alarms:
            out.print("No alarms. Add one with `alarm add 07:30`.")
            return
        states = env.states.load()
        now = env.clock.now()
        table = Table(box=box.SIMPLE_HEAD, pad_edge=False)
        for column in ("ID", "TIME", "REPEAT", "LABEL", "STATUS", "NEXT"):
            table.add_column(column, justify="right" if column == "ID" else "left")
        overdue = False
        for alarm in alarms:
            status = describe(alarm, states.get(alarm.id, AlarmState()), now, env.tz)
            overdue |= status.kind == "overdue"
            next_text = format_when(status.next_at, now, env.tz) if status.next_at else "-"
            table.add_row(
                str(alarm.id),
                format_time(alarm.at),
                format_days(alarm.days),
                escape(alarm.label),
                f"[{_STATUS_STYLE[status.kind]}]{status.kind}[/]",
                next_text,
            )
        out.print(table)
        if overdue:
            out.print("[bold red]Overdue alarms were not handled. Is `alarm run` running?[/]")


@app.command()
def edit(
    alarm_id: IdArg,
    when: Annotated[str | None, typer.Option("--time", "-t", help="New time.")] = None,
    days: DaysOpt = None,
    label: LabelOpt = None,
    snooze: SnoozeOpt = None,
) -> None:
    """Change an alarm. Changing its time or days re-arms it from now."""
    with _user_errors():
        if when is None and days is None and label is None and snooze is None:
            raise AlarmError("nothing to change: pass --time, --days, --label or --snooze")
        env = build_env()
        alarm = env.alarms.update(
            alarm_id,
            env.clock.now(),
            at=None if when is None else parse_time(when),
            days=None if days is None else parse_days(days),
            label=None if label is None else parse_label(label),
            snooze_minutes=snooze,
        )
        _report("Updated", alarm, env)


@app.command()
def enable(alarm_id: IdArg) -> None:
    """Turn an alarm on (re-arms a finished one-shot)."""
    with _user_errors():
        env = build_env()
        _report("Enabled", env.alarms.set_enabled(alarm_id, True, env.clock.now()), env)


@app.command()
def disable(alarm_id: IdArg) -> None:
    """Turn an alarm off without deleting it."""
    with _user_errors():
        env = build_env()
        disabled = env.alarms.set_enabled(alarm_id, False, env.clock.now())
        _report("Disabled", disabled, env, check_runner=False)


@app.command()
def rm(alarm_id: IdArg) -> None:
    """Delete an alarm."""
    with _user_errors():
        env = build_env()
        removed = env.alarms.remove(alarm_id)
        _out().print(f"Removed {escape(describe_alarm(removed))}")
