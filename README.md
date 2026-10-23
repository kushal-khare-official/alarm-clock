# alarm-clock

A terminal alarm clock. Save alarms with a few commands, keep `alarm run` open, and it rings
them: on time, after the laptop wakes, across DST changes, with snooze.

```
$ alarm add 07:30 --days weekdays -l Standup
Added #1  07:30  Mon-Fri  "Standup"
  next: Mon 26 Oct 07:30 (in 2d7h)

$ alarm list
 ID   TIME    REPEAT     LABEL      STATUS   NEXT
 ────────────────────────────────────────────────────────────────
  1   07:30   Mon-Fri    Standup    on       Mon 26 Oct 07:30 (in 2d7h)
  2   19:00   Once       Call mum   on       Sat 24 Oct 19:00 (in 18h33m)
  3   09:15   Sat, Sun              off      -

$ alarm run
00:41:12 alarm run: sound afplay Glass.aiff, grace 15m, rings for up to 10m. Ctrl-C to stop.
00:41:12 Watching 2 alarms. Next: #1 07:30 Standup at Mon 26 Oct 07:30 (in 2d6h)
```

When an alarm is due, a panel shows until you answer it. Only keys pressed *after* it starts
ringing count:

```
╭──────────────────── ALARM ─────────────────────╮
│ 07:30 Standup                                  │
│ 4m late                                        │
│ [s] snooze 9m   [d] dismiss   silences in 9:57 │
╰────────────────────────────────────────────────╯
```

## Install and run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

```bash
uv run alarm --help
```

Or install it as a tool with `uv tool install .` and then use `alarm` directly.

## Commands

| Command | What it does |
|---|---|
| `alarm add TIME [-d DAYS] [-l LABEL] [--snooze N]` | `TIME` accepts `07:30`, `19:05`, `7:30pm` or `7pm`. `DAYS` accepts `once` (the default), `daily`, `weekdays`, `weekends`, or a list like `mon,wed,fri`. Snooze length is 1-60 minutes (default 9). |
| `alarm list` | Status is `on`, `off`, `snoozed`, `done` (a one-shot alarm that already rang) or `overdue` (due, but no runner handled it). |
| `alarm edit ID [--time] [--days] [--label] [--snooze]` | Changing the time or days re-arms the alarm from now. Changing the label or snooze length does not. |
| `alarm enable ID` / `alarm disable ID` / `alarm rm ID` | Enabling a finished one-shot schedules it again. IDs are never reused. |
| `alarm run [--grace 15] [--ring-timeout 10] [--sound auto\|bell]` | The foreground runner. Only one can run at a time. |

`add`, `edit` and `enable` print a warning if no runner is active, because a saved alarm with
nothing running to ring it is the main way this design can fail.

## Behavior in the cases that matter

- **Laptop asleep or runner stopped.** An alarm that is at most `--grace` minutes late still
  rings and says how late it is. Later than that, it is reported once as *missed* ("missed 3
  times, most recently ..."), never rung N times.
- **Snooze** survives a runner restart. Changing the alarm's time or days, or disabling it,
  cancels the snooze. A fresh occurrence outranks an old snooze.
- **Ring timeout.** An unanswered ring silences itself after `--ring-timeout` minutes. Time spent
  ringing one alarm doesn't count against the grace window of the next one.
- **DST.** A time that is skipped on spring-forward day (02:30) rings at 03:30. A time that
  happens twice on fall-back day (01:30) rings once. Lateness is measured in real elapsed time.
- **Clock set backwards.** An occurrence that already rang never rings again.
- **Ctrl-C** while ringing stops the sound and counts as dismissing the alarm.
- **Sound.** macOS uses `afplay`, Linux uses `paplay` or `pw-play`, Windows uses `winsound`, and
  anything else uses the terminal bell. If the player is missing or fails, it falls back to the
  bell and says so.

Data lives in the platform's app directory (`~/Library/Application Support/alarm-clock` on
macOS). Set `ALARM_CLOCK_HOME` to use a different directory.

## How it works

```
 alarm add/list/edit/...                         alarm run
 ───────────────────────                         ─────────
        cli.py ──────────────┐            ┌────────── runner.py ── tick every second
          │ writes           │ reads      │ reads/writes   │
          ▼                  ▼            ▼                │ asks: Idle | Ring | Missed?
   alarms.json  (CLI only)   state.json  (runner only)     ▼
                                                     schedule.py   (pure: no clock, no I/O)
   store.py: atomic writes, schema_version,              ▲
             strict parsing, write lock                  │ alarm + state + now + grace + busy
                                                         │
   runner seams:  clock.py (Clock)   ringer.py (Ringer)   keys.py (KeyReader)   display.py
                  lock.py: one runner at a time (OS file lock, released on crash)
```

- **The scheduler is a pure function.** `evaluate(alarm, state, now, grace, tz, busy)` decides
  what to do, and the runner only applies the answer. That is why whole mornings, a closed laptop
  lid and DST transitions are tested in milliseconds.
- **The watermark is the core idea.** The state records the last occurrence that was handled,
  and nothing at or before it ever fires again. Restarts, sleep, clocks moving backwards and
  missed backlogs all follow from that one rule.
- **Each file has one writer.** CLI processes write `alarms.json` (holding a lock among
  themselves), the runner writes `state.json`, and the runner picks up CLI changes while it
  runs.

Every one of these decisions, with the alternatives and their costs, is in
[DECISIONS.md](DECISIONS.md). What was verified, and how, is in [VALIDATION.md](VALIDATION.md).

## Development

```bash
uv run pytest
```

```bash
uv run pytest -m e2e
```

```bash
uv run python scripts/mutate.py
```

- `uv run pytest` runs the unit, CLI and fake-clock runner tests (about 2 seconds).
- `-m e2e` runs the real program in a pseudo-terminal (POSIX only, about a minute).
- `scripts/mutate.py` injects 15 deliberate bugs, one at a time. The tests must catch every one.

The project also runs `uv run ruff check`, `uv run ruff format --check` and `uv run mypy`
(strict, over src, tests and scripts). CI runs all of it on macOS, Linux and Windows with Python
3.11 and 3.13, and type-checks each platform's view of the code (`mypy --platform`).

## Known limitations

- **Foreground only.** Nothing rings unless `alarm run` is open. That was the chosen trade-off
  (ADR-001). launchd/systemd integration is not built.
- **Time zone is read once at startup.** If the system time zone changes while `alarm run` is
  open (travel), restart the runner.
- **Alarms due together ring one after another.** They are not merged into one ring.
- **Not built:** relative timers (`+25m`), desktop notifications, and per-alarm sounds.
- **Linux and Windows sound** are tested with fakes and in CI, but have not been heard on real
  hardware. See VALIDATION.md.
