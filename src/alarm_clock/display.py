"""Rendering for `alarm run`. The runner decides what to say; this decides how it looks."""

from __future__ import annotations

from datetime import tzinfo

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from alarm_clock.clock import Clock


class RichDisplay:
    def __init__(self, console: Console, clock: Clock, tz: tzinfo) -> None:
        self._console = console
        self._clock = clock
        self._tz = tz
        self._live: Live | None = None
        self._title = ""
        self._detail = ""

    def event(self, text: str) -> None:
        stamp = self._clock.now().astimezone(self._tz).strftime("%H:%M:%S")
        self._console.print(f"[dim]{stamp}[/dim] {escape(text)}")

    def ring_started(self, title: str, detail: str) -> None:
        self._title, self._detail = title, detail
        if self._console.is_terminal:
            self._live = Live(
                self._panel(""), console=self._console, auto_refresh=False, transient=True
            )
            self._live.start(refresh=True)
        else:  # piped or logged: one plain line, no redraws
            suffix = f" ({detail})" if detail else ""
            self.event(f"RINGING {title}{suffix}")

    def ring_tick(self, prompt: str) -> None:
        if self._live is not None:
            self._live.update(self._panel(prompt), refresh=True)

    def ring_ended(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _panel(self, prompt: str) -> Panel:
        lines: list[Text] = [Text(self._title, style="bold")]
        if self._detail:
            lines.append(Text(self._detail, style="yellow"))
        if prompt:
            lines.append(Text(prompt, style="dim"))
        return Panel(Group(*lines), title="ALARM", border_style="red", expand=False)
