from typer.testing import CliRunner

from alarm_clock import __version__
from alarm_clock.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"alarm-clock {__version__}"
