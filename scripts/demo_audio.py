"""Add the alarm's sound to the recorded demo video.

Terminal recorders capture no audio, so this puts back the sound the runner really played:
the same Glass.aiff `afplay` loops (with the same 0.3 s pause between plays), placed exactly
where the ALARM panel is on screen. The timing comes from the video itself, not from guesses:
frames are classified by whether the runner pane shows the panel's red border, and rings are
told apart by where the panel sits: rings can follow each other with no gap, but the runner
prints an event line in between ("snoozed until ..."), so the next panel starts a line lower.

    uv run python scripts/demo_audio.py [docs/demo/demo.mp4]

Needs ffmpeg and macOS's system sound. Rewrites the MP4 in place; safe to re-run.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SOUND = Path("/System/Library/Sounds/Glass.aiff")  # ringer.MAC_SOUND
PAUSE = 0.3  # LoopingCommandRinger's default pause between plays
FPS = 10
# The runner is in the bottom tmux pane (demo.tape): look at the bottom 47% of the frame,
# halved with nearest-neighbour scaling so 2px border lines keep their colour.
RUNNER_PANE_SHARE = 0.47
# Catppuccin Mocha red is #f38ba8. H.264 chroma subsampling washes thin lines out, so match
# "clearly red" rather than the exact value; the theme's pink, yellow and peach fail this test.
IS_RED = "if(gt(r(X,Y),120)*gt(r(X,Y)-g(X,Y),40)*gt(r(X,Y)-b(X,Y),20),255,0)"
MIN_RED_PIXELS = 50  # the panel border measured ~110-250 at this scale; noise is ~0
NEW_RING_ROWS = 4  # a panel moving by at least this many (half-scale) rows is a new ring
EXPECTED_RINGS = 2  # Tea (snoozed) and Stretch (dismissed)


def red_frames(video: Path) -> list[tuple[int, int]]:
    """Per frame: (red pixel count, row of the topmost red pixel, or -1)."""
    width, height = frame_size(video)
    crop_h = int(height * RUNNER_PANE_SHARE) // 4 * 4  # even after halving
    crop_w = width // 4 * 4
    out_w, out_h = crop_w // 2, crop_h // 2
    mask = f"geq=r='{IS_RED}':g='{IS_RED}':b='{IS_RED}'"
    filters = (
        f"fps={FPS},crop={crop_w}:{crop_h}:0:{height - crop_h},"
        f"scale={out_w}:{out_h}:flags=neighbor,format=gbrp,{mask},format=gray"
    )
    cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vf", filters, "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    size = out_w * out_h
    if len(raw) % size:
        raise SystemExit(f"unexpected frame size: {len(raw)} bytes is not a multiple of {size}")
    frames = []
    for i in range(0, len(raw), size):
        frame = raw[i : i + size]
        count = size - frame.count(0)
        top = (size - len(frame.lstrip(b"\x00"))) // out_w if count else -1
        frames.append((count, top))
    return frames


def frame_size(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    width, height = (int(v) for v in out.strip().split(","))
    return width, height


def ring_intervals(frames: list[tuple[int, int]]) -> list[tuple[float, float]]:
    """(start, end) seconds of each ring: a run of panel-visible frames at one position."""
    intervals: list[tuple[float, float]] = []
    start: int | None = None
    top = -1
    for i, (count, row) in enumerate([*frames, (0, -1)]):
        visible = count >= MIN_RED_PIXELS
        moved = visible and start is not None and abs(row - top) >= NEW_RING_ROWS
        if start is not None and (not visible or moved):
            intervals.append((start / FPS, i / FPS))
            start = None
        if visible and start is None:
            start, top = i, row
    return intervals


def duration_of(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(video)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return float(out)


def add_sound(video: Path, intervals: list[tuple[float, float]]) -> None:
    # Every stream here is bounded by the video's own length. The first version used an
    # unbounded `apad` with `-shortest`, and ffmpeg ran on past the end of the video until it
    # failed.
    total = duration_of(video)
    with tempfile.TemporaryDirectory() as tmp:
        unit = Path(tmp) / "unit.wav"  # one play plus the runner's pause, to be looped
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(SOUND), "-af", f"apad=pad_dur={PAUSE}",
             "-ar", "48000", "-ac", "2", str(unit)],
            check=True,
        )  # fmt: skip
        parts = []
        split = "".join(f"[s{i}]" for i in range(len(intervals)))
        graph = [f"[1:a]asplit={len(intervals)}{split}"]
        for i, (start, end) in enumerate(intervals):
            duration = end - start
            ms = round(start * 1000)
            graph.append(
                f"[s{i}]atrim=0:{duration:.3f},afade=t=out:st={max(duration - 0.05, 0):.3f}:d=0.05,"
                f"adelay={ms}:all=1[a{i}]"
            )
            parts.append(f"[a{i}]")
        # A silent bed exactly as long as the video sets the track's length (duration=first).
        graph.append(f"anullsrc=r=48000:cl=stereo:d={total:.3f}[bed]")
        mix = f"[bed]{''.join(parts)}amix=inputs={len(parts) + 1}:duration=first:normalize=0[aout]"
        graph.append(mix)
        out = video.with_suffix(".with-audio.mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(unit),
             "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[aout]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}",
             "-fs", "64M", str(out)],  # a hard size cap too, whatever else goes wrong
            check=True,
        )  # fmt: skip
        os.replace(out, video)


def main() -> int:
    video = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo/demo.mp4")
    intervals = ring_intervals(red_frames(video))
    for start, end in intervals:
        print(f"ringing {start:6.1f}s - {end:6.1f}s  ({end - start:.1f}s)")
    if len(intervals) != EXPECTED_RINGS or not all(2 <= end - s <= 8 for s, end in intervals):
        print(f"expected {EXPECTED_RINGS} rings of 2-8 s; not touching {video}", file=sys.stderr)
        return 1
    add_sound(video, intervals)
    print(f"added {SOUND.name} to {video}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
