# Validation

This file records what was checked, how, and what each check found. Every result below was
produced on the development machine (macOS 26.5, Python 3.11.13) unless marked otherwise.

## Verified and not verified

| Claim | Status | Evidence |
|---|---|---|
| Scheduling: recurrence, one-shot, grace, missed ×N, snooze, clock moving backwards | **Verified** | 32 tests in `test_schedule.py`: examples plus 3 Hypothesis properties, one of them against an independent minute-by-minute oracle |
| DST: gap, fold, real-time lateness, weekly cadence | **Verified** | Tested in `America/New_York` on the real 2026 transition dates |
| Runner: ring, snooze, timeout, sleep and wake, restart, hot reload, Ctrl-C | **Verified** | 22 fake-clock scenarios in `test_runner.py` |
| Real terminal: keypresses without Enter, typed-ahead keys discarded, terminal restored | **Verified** | `test_keys_posix.py` on a real pty, no mocks |
| Whole program end to end: two alarms, `s`, `d`, then Ctrl-C as a real SIGINT | **Verified** | `tests/e2e/test_pty.py`, which waits for a real minute boundary |
| One runner at a time; the lock is released when the holder is SIGKILLed | **Verified** | `test_lock.py` kills a real lock-holding process |
| No alarms lost to concurrent CLI writes | **Verified** | 8 real processes × 5 `add`s each, all 40 ids present |
| macOS sound plays, and no `afplay` process is left after Ctrl-C mid-ring | **Verified by process** (see Dogfood) | `pgrep afplay` sampled during and after the ring |
| macOS sound is *audible* | **Needs a human** | I can't hear. Run the dogfood steps below. |
| Linux sound (`paplay` / `pw-play`) | **Not heard** | Backend selection and command lines tested with fakes. The suite runs on `ubuntu-latest` in CI, which has no audio device. |
| Windows sound (`winsound`) and key reading (`msvcrt`) | **Not run** | Tested with fake `winsound`/`msvcrt` modules. Type-checked as win32 (`mypy --platform win32`). The CI matrix includes `windows-latest`. |
| CI is green on all 3 OSes | **Not yet run** | The workflow exists in `.github/workflows/ci.yml`. It runs only once the repo is pushed to GitHub. |

## Layers of checking

1. **Static checks.**
   - `ruff check` and `ruff format --check` pass.
   - `mypy --strict` passes over `src`, `tests` and `scripts`, *and* once each for
     `--platform win32|linux|darwin`. A plain `mypy` run only checks the current platform's
     branches. The `--platform` runs caught 6 errors in POSIX-only code that the Windows CI job
     would have hit.
2. **Unit and property tests.** 184 tests run in about 2 seconds. The Hypothesis properties are:
   - `next_occurrence` matches a brute-force minute-by-minute scan in UTC.
   - The invariants hold in a DST zone.
   - No occurrence ever rings twice, whatever order the clock jumps in.
   - No alarm id is ever issued twice, over any sequence of adds and removes.
3. **Fake-clock runner scenarios.** The clock moves only when the runner sleeps or waits for a
   key, so a 10-minute ring timeout or a 2-hour laptop sleep takes milliseconds and is fully
   deterministic.
4. **Tests with real processes, no mocks.**
   - A real child process stands in for the sound player.
   - A real process holding the lock is SIGKILLed.
   - 8 real CLI processes write at the same time.
5. **Real-terminal e2e** (`uv run pytest -m e2e`, 2 tests, about 30-60 seconds). The real CLI runs
   under a pty that is its controlling terminal, so `\x03` is a genuine SIGINT delivered by the
   line discipline.
6. **Mutation testing** (`scripts/mutate.py`). It injects 15 deliberate bugs one at a time into
   the scheduler, runner, ringer and store. **15/15 are caught.** Two of those runs turned up
   gaps that are listed below.
7. **Code review** (`/code-review`, high effort, over `src/`). 10 findings: 9 fixed, 1 skipped.
   Details in a table below.
8. **Dogfooding on macOS** with the real sound and a real terminal.

## Problems the checks found

These are things that were wrong or weak at some point and were caught by a check, not
noticed by luck.

| # | Found by | Problem | Resolution |
|---|---|---|---|
| 1 | Designing the scheduler | Aware datetimes that share a `tzinfo` object compare and subtract by wall-clock fields. DST lateness and fold comparisons would have been wrong. | Everything is UTC internally (ADR-012). A mutation that reintroduces local times fails 4 DST tests. |
| 2 | Scheduler tests | A fixture's anchor was three weeks in the past, so "rings on time" came back as "rang, 22 skipped". The code was right; it showed how the anchor bounds the backlog. | ADR-013. The fixture now anchors on the scenario day. |
| 3 | Scheduler tests | My weekly-DST test used the wrong week (1 Nov 07:30 is already after the 02:00 transition). | Test corrected to the week of 25 Oct to 1 Nov, which really is 169 hours long. |
| 4 | Strict typing of tests | The ringer depended on all of `subprocess.Popen`, but uses only 4 methods. | A narrow `Process` protocol. |
| 5 | `mypy --platform win32` | `termios` calls inside functions weren't excluded on Windows, so the Windows CI would have failed. | Explicit `sys.platform` guards, POSIX-only tests moved to their own module, and CI type-checks every platform. |
| 6 | Real-pty test | The test claimed the terminal settings weren't restored: `PENDIN` differed. That bit is set by the macOS kernel itself on any return to canonical mode, and a bare `setcbreak` + restore shows it too. | The test masks that bit. The restore code was correct. |
| 7 | Designing the key reader | A key typed hours earlier would sit in the buffer and dismiss the next alarm the moment it rang. | `flush()` at the start of every ring (ADR-014), tested on a real pty. |
| 8 | Mutation testing | "IDs reused after delete" *survived*. The test removed only the newest alarm, and a broken counter passes that case. | Replaced with a Hypothesis property over random add/remove sequences. The mutation is now caught. |
| 9 | Lint while writing e2e | The pty drain loop called a blocking `os.read`, which could hang the test forever. | `select`-guarded read. |
| 10 | Reviewing the e2e test | If it started in the last second of a minute, `add` could anchor the alarm to *tomorrow*, giving a flaky timeout. | The test targets the minute after next when 10 seconds or less are left in the minute. |
| 12 | First CI run (all 6 jobs) | `test_snooze_out_of_range_is_a_usage_error` asserted on styled text. Typer forces colour when `GITHUB_ACTIONS` is set, so `--snooze` arrived split by ANSI codes. | The CLI test helper strips styling. The failure was reproduced locally with `GITHUB_ACTIONS=true` before the fix and passes after. |
| 13 | First CI run (windows-latest, py3.13) | The lock-release test assumed the OS frees a killed process's lock immediately. Windows frees it asynchronously. | The test waits up to 10 s for the release. The behavior is documented in ADR-008. |
| 11 | Dogfooding | `pgrep` found no `afplay` while ringing. Investigated before trusting it: 12 samples show 10/12 present (the zeros are the gaps between respawns), and no bells were emitted, so the sound did play. But it showed that if the bell fallback ever happens, *nothing tells the user*. | The runner now reports "sound failed (...); rang the terminal bell instead", with a test. |

## Code review findings

The review ran `/code-review` at high effort over `src/alarm_clock`, after milestone 9. Each
fixed finding has a regression test, and each regression test was **confirmed to fail on the
pre-fix code** by stashing the fix and re-running it.

| Finding | Outcome |
|---|---|
| `--grace 0` made alarms essentially never ring: tick granularity left them 0.6 s "late" | **Fixed.** One tick of slack (ADR-015). |
| A player that exits with an error (e.g. no audio device) respawned silently forever, with no fallback | **Fixed.** Falls back to the bell. Tested with a fake and with a real failing child. |
| Two CLI processes writing at once lost alarms | **Fixed.** Write lock. Reproduced in 3/3 runs before the fix (ADR-007, amended). |
| An alarm due while another rang was judged late, and missed when ring timeout ≥ grace | **Fixed at the root.** Busy time is excused from lateness (ADR-015). Laptop sleep is still not excused, and a test pins that. |
| Ctrl-C just as the sound started skipped settling the ring | **Fixed.** `try` opens before `ringer.start()`. |
| `alarm list` preferred a stale snooze over a fresh due occurrence, disagreeing with the runner | **Fixed.** Same precedence as `evaluate`. |
| Hand-edited `"enabled": "false"` was read as `True`; `snooze_minutes: 0` was accepted | **Fixed.** Strict typed parsing with range checks. |
| Every alarm was evaluated twice per tick | **Fixed.** Decisions are reused. |
| An alarm disabled while another rang could still ring right after | **Fixed.** Reload after each ring, then look the alarm up again. |
| The time zone is captured once; a time-zone change while running is ignored | **Skipped, documented.** Restarting `alarm run` fixes it, and the fix would thread a zone provider through every layer. |

## Dogfood (macOS, real sound, real terminal)

This was scripted through a pty so that it could be repeated.

1. Add an alarm for the next minute.
2. Start `alarm run` with the default sound.
3. Sample `pgrep -x afplay` during the ring.
4. Press Ctrl-C *while it rings*.
5. Check again for an `afplay` process, and check the state.

```
$ alarm add 00:48 -l Dogfood
Added #1  00:48  Once  "Dogfood"
  next: Sat 24 Oct 00:48 (in <1m)
warning: no `alarm run` is active, so nothing will ring. Start one in another
terminal: alarm run
afplay before: []
runner says sound: afplay Glass.aiff
afplay samples while ringing: [1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0]
bell chars seen: 0
exit code: 0
afplay after Ctrl-C mid-ring: []
state.json: {"1": {"last_handled": "2026-10-23T19:18:00+00:00", "snoozed_occurrence": null, "snoozed_until": null}}
$ alarm list
 ID   TIME    REPEAT   LABEL     STATUS   NEXT
  1   00:48   Once     Dogfood   done     -
```

**To confirm by ear**, in two terminals. First:

```bash
uv run alarm add "$(date -v+1M +%H:%M)" -l "Can you hear me" --snooze 1
```

Then, in the second terminal:

```bash
uv run alarm run
```

You should hear the macOS "Glass" sound repeating. Press `s` and it rings again a minute later.
Press `d` to dismiss.
