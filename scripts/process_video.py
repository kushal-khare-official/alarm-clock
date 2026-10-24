"""Build the narrated process video: docs/process/process.mp4.

    uv run python scripts/process_video.py

Each scene is narrated by macOS text-to-speech and shown as a real terminal session recorded with
VHS: either commands run live (git log, pytest, the mutation check) or a summary card from
process_cards.py. The demo scene reuses docs/demo/demo.mp4 with its alarm sound under the voice.
Every scene's picture is cut to exactly the length of its narration, so voice and picture cannot
drift. Captions go in a soft subtitle track (this ffmpeg has no text-burning filters), and the
script itself is written to docs/process/narration.md.

The narration below was reviewed and approved word for word before any audio was generated.
Needs macOS `say` with the voice installed, vhs and ffmpeg. Takes about 5 minutes; most of it
is VHS rendering and the mutation check that scene 4 runs live.
"""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / ".build" / "process"
OUT = REPO / "docs" / "process"
VOICE = "Jamie (Premium)"
RATE = 175  # words per minute; a touch slower than `say`'s default, for clarity
LEAD, TAIL = 0.4, 0.9  # seconds of quiet before and after each scene's narration
FPS, WIDTH, HEIGHT = 25, 1400, 900
DEMO = REPO / "docs" / "demo" / "demo.mp4"
DEMO_START, DEMO_LENGTH = 22.5, 23.5  # runner starts, both alarms ring, second runner refused
DEMO_VOLUME = 0.5  # the alarm sound sits under the voice
MAX_BYTES = "200M"  # hard cap on every ffmpeg output

# Spellings that make the voice read abbreviations as letters. Captions keep the originals.
SPOKEN = {r"\bCI\b": "C I", r"\bUTC\b": "U T C", r"\bmacOS\b": "mac O S"}

TAPE_SETUP = """\
Output "{output}"
Set Shell bash
Set Theme "Catppuccin Mocha"
Set FontSize 18
Set Width {width}
Set Height {height}
Set Padding 24
Set Framerate {fps}
Set TypingSpeed 35ms
Set WaitTimeout 150s
Hide
Type "export LANG=en_US.UTF-8 GIT_PAGER=cat VIRTUAL_ENV_DISABLE_PROMPT=1"
Enter
Type "source .venv/bin/activate && export PS1='$ ' && clear"
Enter
Sleep 800ms
"""


def card(name: str, marker: str) -> str:
    """A full-screen card, shown only once it has been drawn."""
    return f"""\
Type "clear && python scripts/process_cards.py {name}"
Enter
Wait+Screen /{marker}/
Show
Sleep {{hold}}s
"""


@dataclass(frozen=True)
class Scene:
    key: str
    heading: str
    text: str
    tape: str | None  # VHS commands after the setup; None for the demo clip


SCENES = [
    Scene(
        "title",
        "Title",
        "This is how I built an alarm clock as a Python command-line app, for the build "
        "exercise. The brief was deliberately open: no spec, no database, decide what to "
        "build. So the first job wasn't code. It was defining the problem.",
        card("title", "How I built it"),
    ),
    Scene(
        "requirements",
        "Requirements",
        "Before writing anything, I had Claude ask me clarifying questions, in two short "
        "rounds, and I made the decisions that shape everything else. Alarms are saved to a "
        "file, and a foreground runner rings them. The scope is recurring days, snooze and "
        "dismiss, and editing alarms; relative timers are out. If the laptop was asleep, a "
        "late alarm still rings within a fifteen-minute grace window, and is reported as "
        "missed after that. Twice I overrode Claude's recommendation: I wanted sound on all "
        "three operating systems, not just macOS, and I chose Typer and Rich over the "
        "standard library.",
        card("requirements", "Defining the problem"),
    ),
    Scene(
        "plan",
        "Plan and milestones",
        "Claude then wrote an implementation plan, which I approved, and built it in ten "
        "milestones: one commit each, with the tests in the same commit. The core is a pure "
        "scheduler. It never reads the clock, so a whole morning, a closed laptop lid, or a "
        "daylight-saving change can be tested in milliseconds. And each alarm remembers the "
        "last time it handled, so nothing rings twice, even if the clock goes backwards.",
        """\
Show
Type "git log --reverse --format='%ad  %h  %s' --date=format:'%d %b %H:%M'"
Enter
Sleep {hold}s
""",
    ),
    Scene(
        "validation",
        "Validation",
        "I chose how the result would be validated: fakes for anything platform-specific, CI "
        "on three operating systems, and a written validation report. There are a hundred and "
        "eighty-four tests, including property-based tests and fake-clock scenarios, plus two "
        "end-to-end tests that drive the real program through a real terminal. Claude also "
        "added a mutation check that injects fifteen deliberate bugs. When one of them first "
        "slipped through, the weak test was replaced. Now every one is caught.",
        """\
Show
Type "uv run pytest -q"
Enter
Wait+Screen /passed/
Sleep 3.5s
Type "uv run pytest tests/test_runner.py --collect-only -q | head -17"
Enter
Sleep 6s
Type "clear; uv run python scripts/mutate.py"
Enter
Sleep 1.5s
Hide
Wait+Screen /label edit re-arms/
Show
Sleep {hold}s
""",
    ),
    Scene(
        "caught",
        "What the checks caught",
        "The checks found real problems. Some were design pitfalls caught early; others were "
        "mistakes in Claude's first attempts. Python compares two times in the same time zone "
        "by clock reading alone, which breaks across daylight-saving changes, so every time is "
        "stored in UTC. Type-checking the code as Windows caught a failure before CI ever ran. "
        "And the first real CI run failed on two things that only happen there: forced colour "
        "output on GitHub, and Windows releasing file locks a moment late. Both are fixed and "
        "written up.",
        card("caught", "What the checks caught"),
    ),
    Scene(
        "review",
        "Code review",
        "Then Claude ran a structured code review of its own code. It found ten issues. Nine "
        "were fixed, each with a regression test confirmed to fail on the old code. For "
        "example, a grace window of zero meant alarms never rang, and two alarm commands run at "
        "the same moment could lose an alarm; that one failed three times out of three before "
        "the fix. One finding, a time-zone change while the runner is open, was consciously "
        "skipped and documented.",
        card("review", "Code review of Claude"),
    ),
    Scene(
        "demo",
        "Demo",
        "Here's the result. Two alarms ring in the same minute: the first is snoozed, the "
        "second dismissed, and a second runner is refused.",
        None,
    ),
    Scene(
        "closing",
        "Time, next steps, disclosure",
        "On time: from the first question to green CI took about sixty-six minutes, roughly "
        "double the thirty-minute guideline. That was just over twenty minutes defining the "
        "problem and the plan, about thirty-five building, and the rest fixing what CI found. "
        "I chose to spend it on validation rather than features. With more time, I'd fix one "
        "small thing the demo revealed: the runner still counts a finished one-time alarm as "
        "one it's watching. Everything is in the repo: the decision log, the validation "
        "report, and the scripts that build these videos. This narration is a synthetic voice "
        "reading a script Claude drafted from the project history, and that I reviewed.",
        card("closing", "Time and next steps"),
    ),
]


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=REPO).stdout


def duration(path: Path) -> float:
    return float(
        run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
            str(path))
    )  # fmt: skip


def spoken(text: str) -> str:
    for pattern, replacement in SPOKEN.items():
        text = re.sub(pattern, replacement, text)
    return text


def narrate(scene: Scene) -> tuple[Path, float, float]:
    """Narration as 48 kHz stereo WAV. Returns (path, speech seconds, scene seconds)."""
    aiff = BUILD / f"{scene.key}.aiff"
    run("say", "-v", VOICE, "-r", str(RATE), "-o", str(aiff), spoken(scene.text))
    speech = duration(aiff)
    total = LEAD + speech + TAIL
    if scene.tape is None:
        total = max(total, DEMO_LENGTH)
    wav = BUILD / f"{scene.key}.wav"
    graph = (
        f"anullsrc=r=48000:cl=stereo:d={total:.3f}[bed];"
        f"[0:a]aresample=48000,aformat=channel_layouts=stereo,adelay={round(LEAD * 1000)}:all=1[v]"
    )
    inputs = ["-i", str(aiff)]
    mix = "[bed][v]amix=inputs=2:duration=first:normalize=0[out]"
    if scene.tape is None:  # the demo keeps its own alarm sound, quieter, under the voice
        inputs += ["-ss", str(DEMO_START), "-t", str(DEMO_LENGTH), "-i", str(DEMO)]
        graph += f";[1:a]volume={DEMO_VOLUME},aresample=48000[d]"
        mix = "[bed][v][d]amix=inputs=3:duration=first:normalize=0[out]"
    run("ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", f"{graph};{mix}",
        "-map", "[out]", "-t", f"{total:.3f}", "-fs", MAX_BYTES, str(wav))  # fmt: skip
    return wav, speech, total


def picture(scene: Scene, total: float) -> Path:
    """The scene's video, exactly `total` seconds long, silent."""
    clip = BUILD / f"{scene.key}.mp4"
    fit = (
        f"fps={FPS},scale={WIDTH}:{HEIGHT}:flags=lanczos,format=yuv420p,"
        f"tpad=stop_mode=clone:stop_duration={total:.3f},trim=duration={total:.3f},"
        "setpts=PTS-STARTPTS"
    )
    if scene.tape is None:
        source = ["-ss", str(DEMO_START), "-t", str(DEMO_LENGTH), "-i", str(DEMO)]
    else:
        raw = BUILD / f"{scene.key}.raw.mp4"
        tape = BUILD / f"{scene.key}.tape"
        hold = f"{total + 2:.1f}"
        tape.write_text(
            TAPE_SETUP.format(output=raw, width=WIDTH, height=HEIGHT, fps=FPS)
            + scene.tape.replace("{hold}", hold)
        )
        run("vhs", str(tape))
        if duration(raw) < total:
            raise SystemExit(f"{scene.key}: recording is shorter than its narration")
        source = ["-i", str(raw)]
    run("ffmpeg", "-v", "error", "-y", *source, "-vf", fit, "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", "-t", f"{total:.3f}", "-fs", MAX_BYTES,
        str(clip))  # fmt: skip
    return clip


def captions(timings: list[tuple[Scene, float, float]]) -> str:
    """SRT: each scene's sentences spread over its speech, in proportion to their length."""

    def stamp(t: float) -> str:
        ms = round(t * 1000)
        return (
            f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
        )

    cues: list[str] = []
    start = 0.0
    for scene, speech, total in timings:
        parts = [p for p in re.split(r"(?<=[.:;])\s+", scene.text) if p]
        chars = sum(len(p) for p in parts)
        t = start + LEAD
        for part in parts:
            length = speech * len(part) / chars
            cues.append(f"{len(cues) + 1}\n{stamp(t)} --> {stamp(t + length)}\n{part}\n")
            t += length
        start += total
    return "\n".join(cues)


def script_markdown(timings: list[tuple[Scene, float, float]]) -> str:
    lines = [
        "# Narration script",
        "",
        f"Read by the macOS voice “{VOICE}” at {RATE} words per minute. Drafted by Claude from",
        "the project history, then reviewed and approved word for word by Kushal Khare before",
        "any audio was generated. Built by `scripts/process_video.py`.",
        "",
    ]
    start = 0.0
    for i, (scene, _, total) in enumerate(timings, 1):
        minutes, seconds = divmod(round(start), 60)
        lines += [f"## {i}. {scene.heading} ({minutes}:{seconds:02d})", "", scene.text, ""]
        start += total
    return "\n".join(lines)


def main() -> int:
    for tool in ("say", "vhs", "ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"missing tool: {tool}")
    if VOICE not in run("say", "-v", "?"):
        raise SystemExit(f"voice not installed: {VOICE} (System Settings > Spoken Content)")
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    timings, clips, audio = [], [], []
    for scene in SCENES:
        wav, speech, total = narrate(scene)
        print(f"{scene.key:13s} speech {speech:5.1f}s  scene {total:5.1f}s", flush=True)
        clips.append(picture(scene, total))
        audio.append(wav)
        timings.append((scene, speech, total))
    length = sum(total for _, _, total in timings)

    concat = BUILD / "clips.txt"
    concat.write_text("".join(f"file '{clip}'\n" for clip in clips))
    srt = OUT / "process.srt"
    srt.write_text(captions(timings))
    (OUT / "narration.md").write_text(script_markdown(timings))

    joined = "".join(f"[{i}:a]" for i in range(len(audio)))
    graph = f"{joined}concat=n={len(audio)}:v=0:a=1,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    track = BUILD / "narration.wav"
    audio_inputs = [arg for wav in audio for arg in ("-i", str(wav))]
    run("ffmpeg", "-v", "error", "-y", *audio_inputs, "-filter_complex", graph, "-map", "[a]",
        "-ar", "48000", "-t", f"{length:.3f}", "-fs", MAX_BYTES, str(track))  # fmt: skip
    video = OUT / "process.mp4"
    run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(track), "-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=eng", "-t", f"{length:.3f}", "-fs", MAX_BYTES,
        str(video))  # fmt: skip
    print(f"wrote {video.relative_to(REPO)} ({length:.1f}s), {srt.relative_to(REPO)}, "
          "docs/process/narration.md")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
