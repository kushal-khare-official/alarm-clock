"""Command-line interface: thin Typer layer over the store and scheduler."""

from __future__ import annotations

from typing import Annotated

import typer

from alarm_clock import __version__

app = typer.Typer(
    name="alarm",
    help="A terminal alarm clock. Save alarms, then keep `alarm run` open to ring them.",
    no_args_is_help=True,
    add_completion=False,
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
