# Decisions

This file has one entry per decision that shaped the design. Each entry gives the context, the
options that were considered, what was chosen, and what it costs. Entries marked *(Q&A)* were
settled in a requirements conversation before any code was written. The rest came up during
design or implementation.

---

## ADR-001: Saved alarms plus a foreground runner *(Q&A)*

**Context.** An alarm clock CLI has to answer one question first: what process is running when
the alarm is due?

**Options.**
1. *One blocking command* (`alarm at 07:30` holds the terminal). This is the simplest option, but
   it gives one alarm per terminal, no recurrence, and nothing is saved.
2. *Saved alarms plus a runner.* CRUD commands write to a file, and `alarm run` stays open in the
   foreground and rings.
3. *Hand off to the OS scheduler* (launchd or cron). This fires even with the terminal closed,
   but it ties the tool to one platform and is hard to test end to end.

**Decision.** Option 2.

**Consequences.** Scheduling becomes a pure function that the runner calls on each tick, which is
what makes the logic testable (ADR-005). The obvious failure mode is "I set an alarm but no
runner was running". `add` and `edit` warn about this explicitly (ADR-008).

## ADR-002: Scope is recurring schedules, snooze/dismiss, and edit/enable/disable *(Q&A)*

**Decision.** In scope: recurring days (`daily`, `weekdays`, `weekends`, `mon,wed,fri`), snooze
and dismiss while ringing, and editing or toggling an alarm in place. Relative timers (`+25m`)
are out of scope.

**Why.** Recurrence is where the hard edge cases live (week wrap, DST, missed occurrences).
Snooze is what separates an alarm from a timer. A countdown timer would have added surface area
without adding new decisions.

## ADR-003: Missed alarms ring late within a grace window *(Q&A)*

**Context.** A laptop can be asleep when an alarm is due, or the runner may not be running at
all.

**Options.** Always ring late, never ring late, or ring late within a grace window.

**Decision.** If an alarm is at most `--grace` minutes late (default 15, inclusive), it rings and
shows how late it is. If it is later than that, it is reported as *missed* and is not rung.
Either way the alarm moves on to its next occurrence. If several occurrences were missed (the
runner was off for days), they are reported once as "missed ×N", not N times.

**Why.** A 07:00 alarm going off at 18:00 when the lid opens is worse than useless. On the other
hand, waking up a few minutes late should still ring.

## ADR-004: Typer and Rich for the CLI, uv for project management *(Q&A)*

**Decision.** The CLI is built with Typer and its output with Rich (tables and the ringing
banner). uv manages the project, and `uv.lock` is committed.

**Cost.** These are runtime dependencies, and Typer's decorator style hides some argument
parsing. The CLI layer is kept thin so that all behavior lives in plain modules that are tested
directly.

## ADR-005: A pure scheduler with an injected clock

**Decision.** `schedule.py` never reads the clock and does no I/O. Given an alarm, its state,
`now` and the grace window, it returns `Idle(next_at)`, `Ring(occurrence, late_by)` or
`Missed(occurrence, count)`. The runner is a thin loop around it, with `Clock`, `Ringer` and
`KeyReader` passed in.

**Why.** A whole simulated day, a closed laptop lid, or a DST transition can then be tested in
milliseconds and deterministically. Tests never sleep in real time.

## ADR-006: A watermark instead of a stored "next fire time"

**Context.** The obvious design stores `next_fire_at` and advances it after each ring. That
breaks in several ways. If the runner was down, it is unclear what "next" means. If the clock
moves backwards, an alarm that already rang can ring again. Editing the alarm requires
recomputing the stored value.

**Decision.** Each alarm's state stores `last_handled`, the most recent occurrence that was rung,
dismissed or marked missed. An occurrence is *due* if it falls after `max(anchor, last_handled)`
and at or before `now`. `anchor` is set when the alarm is added, edited or enabled.

**Consequences.**
- A runner restart picks up exactly where it left off.
- Sleep and wake need no special code.
- An occurrence that was already handled can never fire again, even if the clock moves
  backwards.
- A new alarm never "misses" a time that was already in the past when it was created.
- The next fire time is always derived, never stored, so it can't go stale.

## ADR-007: Two JSON files with one writer each

**Context.** No database. The CLI and the runner are separate processes that both touch
persistent data.

**Decision.**
- `alarms.json` holds the definitions and is written **only by CLI commands**.
- `state.json` holds watermarks and snoozes and is written **only by the runner**.
- Writes are atomic (temp file in the same directory, `fsync`, then `os.replace`), so a reader
  never sees a half-written file.
- Both files carry a `schema_version`.
- A corrupt file or an unknown schema is an error that names the path. The file is never silently
  reset.
- IDs come from a monotonic `next_id` and are never reused.

**Why.** With a single writer per file, no lock is needed around the data. A finished one-shot
alarm shows as `done` by derivation (its watermark is at or past its only occurrence), so the
runner never has to edit definitions. Because IDs are never reused, leftover state can't attach
itself to a new alarm.

## ADR-008: Single runner instance, enforced with an OS file lock

**Decision.** `alarm run` holds an exclusive non-blocking lock on `runner.lock`, using
`fcntl.flock` on POSIX and `msvcrt.locking` on Windows. The OS releases the lock if the process
dies, so there is no stale pidfile to clean up. A second runner exits with a message. `add` and
`edit` probe the same lock to warn when no runner is active.

**Why.** Two runners would ring twice and both write `state.json`, which would break the
one-writer rule in ADR-007.

## ADR-009: Wall-clock semantics, real-time lateness, defined DST behavior

**Decision.** `07:30` means 07:30 local wall-clock time on each matching day. Occurrences are
resolved to absolute instants in the local IANA zone, and lateness is measured in real elapsed
time.
- *Gap* (a time skipped on spring-forward day, e.g. 02:30): the time moves forward by the length
  of the gap and fires at 03:30. java.time and POSIX `mktime` do the same.
- *Fold* (a time that happens twice on fall-back day, e.g. 01:30): it fires once, at the first
  occurrence.

The local zone comes from `tzlocal`, plus `tzdata` on Windows. The standard library has no
portable way to get the local IANA zone name, and a fixed UTC offset would be wrong for half the
year.

## ADR-010: A snooze is runner state, not a new alarm

**Decision.** Snoozing stores `snoozed_until`, together with the occurrence being snoozed, in
`state.json`. A snooze therefore survives a runner restart. Editing or disabling the alarm
cancels the snooze, because the alarm's anchor moves past the snoozed occurrence.

## ADR-011: Each platform behind a small interface, chosen by a pure function *(Q&A: cross-platform sound)*

**Decision.**
- `Ringer` (`start()`/`stop()`) has four implementations:
  - `afplay` on macOS
  - `paplay` with a freedesktop sound on Linux
  - `winsound` on Windows
  - terminal bell as the fallback
- `KeyReader` (`read_key(timeout)`) has two backends: termios + `select` on POSIX, `msvcrt` on
  Windows.
- If stdin is not a TTY, a null key reader is used and the ring runs until its timeout.

Each backend is chosen by a pure `select_*()` function that takes the platform and a `which`
lookup as arguments.

**Validation trade-off.** Only macOS can be heard and run by hand on the development machine.
Linux and Windows are covered by unit tests with fakes and by a GitHub Actions matrix across all
three OSes. VALIDATION.md lists these as *not heard on real hardware*.
