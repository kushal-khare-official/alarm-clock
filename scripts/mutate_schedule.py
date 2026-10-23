"""Mutation check for the scheduler tests.

Applies one deliberate bug at a time to schedule.py, runs its tests, and restores the file.
Every mutation should be CAUGHT; a SURVIVED line means the tests miss a behavior.

    uv run python scripts/mutate_schedule.py
"""

import pathlib
import subprocess

path = pathlib.Path("src/alarm_clock/schedule.py")
original = path.read_text()
mutations = {
    "keep local tz instead of UTC (same-tzinfo arithmetic)": [
        ("    return dt.astimezone(UTC)\n", "    return dt\n"),
        (
            "    return datetime.combine(day, at).replace(tzinfo=tz, fold=0).astimezone(UTC)",
            "    return datetime.combine(day, at).replace(tzinfo=tz, fold=0)",
        ),
    ],
    "grace is exclusive (< instead of <=)": [
        (
            "        if late_by <= grace:\n            return Ring(latest",
            "        if late_by < grace:\n            return Ring(latest",
        )
    ],
    "watermark ignored (floor = anchor)": [
        (
            "    if state.last_handled is None:\n        return anchor\n",
            "    if True:\n        return anchor\n",
        )
    ],
    "occurrences not strictly after (>=)": [
        ("            if occurrence > after:\n", "            if occurrence >= after:\n")
    ],
    "stale snooze wins over fresh occurrence": [
        (
            "    if due:\n        latest",
            "    if due and active_snooze(alarm, state) is None:\n        latest",
        )
    ],
}
try:
    for name, edits in mutations.items():
        src = original
        for old, new in edits:
            assert src.count(old) == 1, (name, old)
            src = src.replace(old, new)
        path.write_text(src)
        r = subprocess.run(
            ["uv", "run", "pytest", "-q", "tests/test_schedule.py"], capture_output=True, text=True
        )
        failed = [
            line.split("::")[1].split(" ")[0]
            for line in r.stdout.splitlines()
            if line.startswith("FAILED")
        ]
        print(f"{'CAUGHT' if failed else 'SURVIVED'}: {name}\n   " + "\n   ".join(failed[:6]))
finally:
    path.write_text(original)
