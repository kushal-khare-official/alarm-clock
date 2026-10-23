import io
from types import ModuleType

import pytest

from alarm_clock.keys import NullKeyReader, WindowsKeyReader, open_key_reader


def test_null_reader_waits_out_the_timeout() -> None:
    slept: list[float] = []
    reader = NullKeyReader(sleep=slept.append)
    assert reader.read_key(0.25) is None
    reader.flush()
    assert slept == [0.25]


def test_non_terminal_stdin_gets_the_null_reader() -> None:
    with open_key_reader(io.StringIO()) as reader:
        assert isinstance(reader, NullKeyReader)


# --- Windows: fake msvcrt ---------------------------------------------------------------------


def fake_msvcrt(typed: str) -> ModuleType:
    queue = list(typed)
    module = ModuleType("msvcrt")
    module.kbhit = lambda: bool(queue)  # type: ignore[attr-defined]
    module.getwch = lambda: queue.pop(0)  # type: ignore[attr-defined]
    return module


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("S", "s"), ("\xe0Hd", "d"), ("\x00;s", "s"), ("", None)],
    ids=["lowercased", "arrow-key-skipped", "f-key-skipped", "timeout"],
)
def test_windows_reader(typed: str, expected: str | None) -> None:
    reader = WindowsKeyReader(fake_msvcrt(typed), poll_interval=0.001)
    assert reader.read_key(0.01) == expected


def test_windows_ctrl_c_interrupts() -> None:
    with pytest.raises(KeyboardInterrupt):
        WindowsKeyReader(fake_msvcrt("\x03")).read_key(0.01)


def test_windows_flush_drains_the_buffer() -> None:
    reader = WindowsKeyReader(fake_msvcrt("dds"), poll_interval=0.001)
    reader.flush()
    assert reader.read_key(0.01) is None
