"""Full-screen summary cards for the narrated process video (see process_video.py).

    uv run python scripts/process_cards.py title|requirements|caught|review|closing

Every fact here comes from the repo: DECISIONS.md (the requirements Q&A), VALIDATION.md (what the
checks caught, the code review) and the git and CI history (the timeline).
"""

import sys
from collections.abc import Callable

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)


def heading(title: str, subtitle: str) -> Group:
    return Group(Text(title, style="bold"), Text(subtitle, style="dim"), Text())


def title() -> RenderableType:
    return Group(
        Text("alarm-clock", style="bold magenta"),
        Text("How I built it: problem → decisions → plan → validation", style="bold"),
        Text(),
        Text("The brief: an alarm clock as a Python CLI."),
        Text("CLI only · no web UI · no database · no detailed spec."),
        Text("“Decide what to build with the time you have.”", style="italic"),
        Text(),
        Text("Kushal Khare · github.com/kushal-khare-official/alarm-clock", style="dim"),
    )


def requirements() -> RenderableType:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Decision")
    table.add_column("Claude recommended")
    table.add_column("I chose")
    rows = [
        ("How alarms fire", "saved alarms + a runner", "saved alarms + a runner", False),
        ("Scope", "-", "recurring, snooze/dismiss, edit/enable", False),
        ("Alarm sound", "macOS first, bell fallback", "macOS, Linux and Windows", True),
        ("Missed alarms", "ring late within grace", "ring late within 15 min", False),
        ("Dependencies", "standard library + pytest", "Typer + Rich", True),
        ("Validation", "fakes + CI on 3 OSes", "fakes + CI on 3 OSes", False),
    ]
    for decision, recommended, chosen, overrode in rows:
        mine = Text(chosen, style="bold yellow" if overrode else "green")
        if overrode:
            mine.append("  (overrode)", style="yellow")
        table.add_row(decision, recommended, mine)
    return Group(
        heading("Defining the problem", "Two rounds of clarifying questions before any code"),
        table,
        Text("Out of scope: relative timers, background daemons, desktop notifications.", "dim"),
    )


def caught() -> RenderableType:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Problem")
    table.add_column("Found by", style="cyan")
    table.add_column("Fix")
    for row in [
        ("Same-zone datetime maths breaks across DST", "designing the scheduler",
         "every time stored in UTC"),
        ("A test let 'ids reused after delete' slip through", "mutation check",
         "property test over random add/remove"),
        ("POSIX-only code on the Windows path", "mypy --platform win32",
         "explicit platform guards"),
        ("A key typed earlier could dismiss the next alarm", "designing the key reader",
         "flush keys when a ring starts"),
        ("Forced colour output on GitHub Actions", "first CI run", "tests strip styling"),
        ("Windows releases a dead process's lock late", "first CI run",
         "bounded wait, documented"),
        ("Sound fallback happened silently", "dogfooding on macOS", "runner now reports it"),
    ]:  # fmt: skip
        table.add_row(*row)
    return Group(heading("What the checks caught", "Design pitfalls, and mistakes in Claude's "
                         "first attempts"), table)  # fmt: skip


def review() -> RenderableType:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Finding")
    table.add_column("Outcome")
    findings = [
        "--grace 0 meant alarms never rang",
        "A player exiting with an error looped silently",
        "Concurrent CLI writes lost alarms (3 of 3 runs)",
        "An alarm due during another's ring was marked missed",
        "Ctrl-C as the sound started left the ring unsettled",
        "`alarm list` preferred a stale snooze over a fresh alarm",
        "Hand-edited files were loosely validated",
        "Every alarm was evaluated twice per tick",
        "An alarm disabled mid-ring could still ring",
    ]
    for i, finding in enumerate(findings, 1):
        table.add_row(str(i), finding, Text("fixed", style="green"))
    table.add_row(
        "10",
        "A time-zone change while the runner is open is ignored",
        Text("skipped, documented", style="yellow"),
    )
    return Group(
        heading("Code review of Claude's code", "10 findings: 9 fixed, 1 consciously skipped"),
        table,
        Text("Each fix has a regression test, confirmed to fail on the pre-fix code.", "dim"),
    )


def closing() -> RenderableType:
    timeline = Table(box=None, show_header=False, padding=(0, 2))
    timeline.add_column(style="cyan")
    timeline.add_column()
    for when, what in [
        ("23:51", "brief received: questions, then the plan"),
        ("00:14", "project created"),
        ("00:16-00:51", "ten milestones, review fixes, docs"),
        ("00:53", "first CI run fails"),
        ("00:58", "CI green on macOS, Linux and Windows"),
        ("next day", "demo video"),
    ]:
        timeline.add_row(when, what)
    return Group(
        heading("Time and next steps", "Times in IST, from the session log and GitHub"),
        timeline,
        Text(),
        Text("≈ 66 minutes to green CI: about double the 30-minute guideline.", style="bold"),
        Text("Next: the runner still counts a finished one-time alarm as one it is watching."),
        Text("In the repo: DECISIONS.md · VALIDATION.md · docs/demo · docs/process"),
        Text(),
        Text(
            "Narration: a synthetic voice (macOS “Jamie”) reading a script Claude drafted from "
            "the project history, reviewed by me. Visuals: real terminal sessions recorded "
            "with VHS.",
            style="dim italic",
        ),
    )


CARDS: dict[str, Callable[[], RenderableType]] = {
    "title": title,
    "requirements": requirements,
    "caught": caught,
    "review": review,
    "closing": closing,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in CARDS:
        print(f"usage: process_cards.py {'|'.join(CARDS)}", file=sys.stderr)
        return 2
    card = Panel(CARDS[sys.argv[1]](), box=box.ROUNDED, border_style="blue", padding=(1, 3))
    console.print()
    console.print(Align.center(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
