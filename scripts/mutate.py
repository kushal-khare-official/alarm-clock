"""Mutation check: do the tests notice when the code is wrong?

Applies one deliberate bug at a time, runs the relevant tests, and restores the file. Every
mutation should be CAUGHT; a SURVIVED line means the tests miss a behavior.

    uv run python scripts/mutate.py
"""

import pathlib
import subprocess

# (file, tests, description, [(original, mutated), ...])
MUTATIONS = [
    (
        "src/alarm_clock/schedule.py",
        "tests/test_schedule.py",
        "keep local tz instead of UTC (same-tzinfo arithmetic)",
        [
            ("    return dt.astimezone(UTC)\n", "    return dt\n"),
            (
                "    return datetime.combine(day, at).replace(tzinfo=tz, fold=0).astimezone(UTC)",
                "    return datetime.combine(day, at).replace(tzinfo=tz, fold=0)",
            ),
        ],
    ),
    (
        "src/alarm_clock/schedule.py",
        "tests/test_schedule.py",
        "grace is exclusive (< instead of <=)",
        [
            (
                "        if late_by <= grace:\n            return Ring(latest",
                "        if late_by < grace:\n            return Ring(latest",
            )
        ],
    ),
    (
        "src/alarm_clock/schedule.py",
        "tests/test_schedule.py",
        "watermark ignored (floor = anchor)",
        [
            (
                "    if state.last_handled is None:\n        return anchor\n",
                "    if True:\n        return anchor\n",
            )
        ],
    ),
    (
        "src/alarm_clock/schedule.py",
        "tests/test_schedule.py",
        "occurrences not strictly after (>=)",
        [("            if occurrence > after:\n", "            if occurrence >= after:\n")],
    ),
    (
        "src/alarm_clock/schedule.py",
        "tests/test_schedule.py",
        "stale snooze wins over fresh occurrence",
        [
            (
                "    if due:\n        latest",
                "    if due and active_snooze(alarm, state) is None:\n        latest",
            )
        ],
    ),
    (
        "src/alarm_clock/runner.py",
        "tests/test_runner.py",
        "typed-ahead keys not flushed before a ring",
        [("        self._keys.flush()  #", "        pass  #")],
    ),
    (
        "src/alarm_clock/runner.py",
        "tests/test_runner.py",
        "Ctrl-C while ringing leaves the occurrence unsettled",
        [("            self._settle(alarm, state, ring, outcome)\n", "            pass\n")],
    ),
    (
        "src/alarm_clock/runner.py",
        "tests/test_runner.py",
        "no hot reload after startup",
        [
            (
                "        if signature == self._signature:\n",
                "        if self._signature is not None:\n",
            )
        ],
    ),
    (
        "src/alarm_clock/runner.py",
        "tests/test_runner.py",
        "snooze ignores the alarm's snooze length",
        [
            (
                "        until = self._clock.now() + timedelta(minutes=alarm.snooze_minutes)",
                "        until = self._clock.now() + timedelta(minutes=5)",
            )
        ],
    ),
    (
        "src/alarm_clock/store.py",
        "tests/test_store.py",
        "ids reused after delete",
        [
            (
                "        self._write(next_id + 1, [*alarms, alarm])",
                "        self._write(len(alarms) + 2, [*alarms, alarm])",
            )
        ],
    ),
    (
        "src/alarm_clock/store.py",
        "tests/test_store.py",
        "label edit re-arms the alarm",
        [
            (
                "            return replace(b, anchor=now)"
                " if (b.at, b.days) != (a.at, a.days) else b",
                "            return replace(b, anchor=now)",
            )
        ],
    ),
]


def main() -> int:
    survived = 0
    for file, tests, name, edits in MUTATIONS:
        path = pathlib.Path(file)
        original = path.read_text()
        mutated = original
        for old, new in edits:
            assert mutated.count(old) == 1, (name, old)
            mutated = mutated.replace(old, new)
        try:
            path.write_text(mutated)
            result = subprocess.run(
                ["uv", "run", "pytest", "-q", "-x", tests], capture_output=True, text=True
            )
        finally:
            path.write_text(original)
        caught = result.returncode != 0
        survived += not caught
        print(f"{'CAUGHT  ' if caught else 'SURVIVED'} {name}")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
