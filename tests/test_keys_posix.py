"""Key reading through a real pseudo-terminal, no mocks. POSIX only: conftest.py skips this
file on Windows, and the assert below tells mypy the same."""

import os
import sys
import termios
from collections.abc import Iterator
from typing import Any

import pytest

from alarm_clock.keys import PosixKeyReader, open_key_reader

assert sys.platform != "win32"


def settings(fd: int) -> list[Any]:
    """Terminal attributes minus PENDIN, a state bit BSD/macOS kernels set on their own whenever
    a terminal returns to canonical mode (a bare setcbreak + restore shows it too)."""
    attrs = termios.tcgetattr(fd)
    lflag = attrs[3]
    assert isinstance(lflag, int)
    attrs[3] = lflag & ~getattr(termios, "PENDIN", 0)
    return attrs


@pytest.fixture
def pty_pair() -> Iterator[tuple[int, int]]:
    master, slave = os.openpty()
    yield master, slave
    os.close(master)
    os.close(slave)


def test_keys_arrive_without_enter_and_terminal_is_restored(pty_pair: tuple[int, int]) -> None:
    master, slave = pty_pair
    original = settings(slave)
    with os.fdopen(slave, "r", closefd=False) as stream, open_key_reader(stream) as reader:
        assert isinstance(reader, PosixKeyReader)
        assert not termios.tcgetattr(slave)[3] & termios.ICANON  # line buffering off
        assert reader.read_key(0.05) is None
        os.write(master, b"S")
        assert reader.read_key(1) == "s"
    assert settings(slave) == original


def test_terminal_is_restored_even_on_ctrl_c(pty_pair: tuple[int, int]) -> None:
    _, slave = pty_pair
    original = settings(slave)
    with (
        pytest.raises(KeyboardInterrupt),
        os.fdopen(slave, "r", closefd=False) as stream,
        open_key_reader(stream),
    ):
        raise KeyboardInterrupt
    assert settings(slave) == original


def test_flush_discards_keys_pressed_before_the_ring(pty_pair: tuple[int, int]) -> None:
    master, slave = pty_pair
    with os.fdopen(slave, "r", closefd=False) as stream, open_key_reader(stream) as reader:
        os.write(master, b"d")  # pressed an hour ago, while idle
        reader.flush()
        assert reader.read_key(0.05) is None
        os.write(master, b"s")
        assert reader.read_key(1) == "s"


def test_escape_sequences_yield_a_harmless_first_character(pty_pair: tuple[int, int]) -> None:
    master, slave = pty_pair
    with os.fdopen(slave, "r", closefd=False) as stream, open_key_reader(stream) as reader:
        os.write(master, b"\x1b[A")  # arrow up
        assert reader.read_key(1) == "\x1b"
