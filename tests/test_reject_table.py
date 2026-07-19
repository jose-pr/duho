"""Regression tests for Plan 04 'Rejected candidates' behaviors.

These candidates were rejected as first-class features because argparse /
duho already cover them; these tests lock the existing behavior in so a future
change cannot silently break it.

* **Negative-number handling** -- argparse's ``_negative_number_matcher`` lets a
  negative number be a value; no special support is needed.
* **Exit-code enum** -- an ``IntEnum`` returned from ``__call__`` propagates
  through ``0 if result is None else result`` unchanged (it *is* an int).
"""

import enum

import duho
from duho import Args, Cmd


class NumbersArgs(Args):
    """Fields that accept negative numeric values."""

    temp: int = 0
    "temperature"
    ("--temp",)

    offset: float = 0.0
    "offset"
    ("--offset",)

    count: int
    "a required positional int"
    ("count",)


def test_negative_number_option_values():
    result = duho.parse(NumbersArgs, ["--temp", "-5", "--offset", "-2.5", "7"])
    assert result.temp == -5
    assert result.offset == -2.5
    assert result.count == 7


def test_negative_number_positional_value():
    result = duho.parse(NumbersArgs, ["-3"])
    assert result.count == -3
    assert result.temp == 0


class ExitCode(enum.IntEnum):
    OK = 0
    WARN = 1
    FAIL = 3


class IntEnumCmd(Cmd):
    """A command returning an IntEnum as its exit code."""

    code: int = 3
    ("--code",)

    def __call__(self):
        return ExitCode(self.code)


def test_intenum_return_is_exit_code():
    assert duho.main(IntEnumCmd, ["--code", "3"]) == 3
    assert duho.main(IntEnumCmd, ["--code", "0"]) == 0


class NoneCmd(Cmd):
    """A command returning None -> exit code 0."""

    def __call__(self):
        return None


def test_none_return_is_zero():
    assert duho.main(NoneCmd, []) == 0
