"""Tests for Plan 04 features: Literal/Enum choices, list[T], --version, main()/__run__."""

import enum
import logging
import typing as ty

import pytest

import duho
from duho import Args, LoggingArgs


# --- Phase 1: Literal & Enum -> choices --------------------------------


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class LiteralArgs(Args):
    """Arguments with a Literal field."""

    mode: ty.Literal["fast", "slow", "auto"]
    "Execution mode"
    ("--mode",)


class EnumArgs(Args):
    """Arguments with an Enum field."""

    color: Color
    "Favorite color"
    ("--color",)


class MixedLiteralArgs(Args):
    """Arguments with a mixed-type Literal field."""

    value: ty.Literal["auto", 1, 2.5]
    "Mixed literal value"
    ("--value",)


def test_literal_choices_accept():
    """A value matching one of the Literal options parses through unchanged."""
    parser = LiteralArgs._build_parser_()
    args = parser.parse_args(["--mode", "fast"])
    assert args.mode == "fast"


def test_literal_choices_reject():
    """A value outside the Literal options raises SystemExit."""
    parser = LiteralArgs._build_parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "turbo"])


def test_enum_choices_by_name():
    """Enum fields accept the member name and produce the Enum member."""
    parser = EnumArgs._build_parser_()
    args = parser.parse_args(["--color", "RED"])
    assert args.color is Color.RED


def test_enum_choices_reject_bad_name():
    """A name that isn't a declared Enum member raises SystemExit."""
    parser = EnumArgs._build_parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--color", "PURPLE"])


def test_mixed_literal_round_trip():
    """Mixed-type Literal picks the type matching the declared value, not
    just the first type that doesn't raise (e.g. str would swallow "1")."""
    parser = MixedLiteralArgs._build_parser_()

    args = parser.parse_args(["--value", "auto"])
    assert args.value == "auto"
    assert isinstance(args.value, str)

    args = parser.parse_args(["--value", "1"])
    assert args.value == 1
    assert isinstance(args.value, int)

    args = parser.parse_args(["--value", "2.5"])
    assert args.value == 2.5
    assert isinstance(args.value, float)


def test_mixed_literal_rejects_unknown_value():
    """A value that doesn't match any declared literal raises SystemExit."""
    parser = MixedLiteralArgs._build_parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--value", "nope"])


# --- Phase 2: list[T] fields --------------------------------------------


class ListArgs(Args):
    """Arguments with list fields."""

    tags: list
    "Bare list of strings"
    ("--tags",)

    numbers: "list[int]" = None
    "Typed list of ints"
    ("--numbers",)


def test_list_accumulation_repeated_flag():
    """Repeated `--x a --x b` accumulates via the extend action."""
    parser = ListArgs._build_parser_()
    args = parser.parse_args(["--tags", "a", "--tags", "b"])
    assert args.tags == ["a", "b"]


def test_list_accumulation_space_separated():
    """Space-separated `--x a b` accumulates via nargs="*"."""
    parser = ListArgs._build_parser_()
    args = parser.parse_args(["--tags", "a", "b"])
    assert args.tags == ["a", "b"]


def test_list_default_empty_when_undeclared():
    """A list field with no explicit default gets [] rather than None."""
    parser = ListArgs._build_parser_()
    args = parser.parse_args([])
    assert args.tags == []


def test_list_element_type_conversion():
    """list[int] converts each element with the element factory."""
    parser = ListArgs._build_parser_()
    args = parser.parse_args(["--numbers", "1", "2", "--numbers", "3"])
    assert args.numbers == [1, 2, 3]
    assert all(isinstance(n, int) for n in args.numbers)


# --- Phase 3: --version --------------------------------------------------


class VersionedArgs(Args):
    """Arguments with a _version_ attr."""

    _version_ = "1.2.3"

    name: str = "x"
    "Name"
    ("--name",)


def test_version_flag_prints_and_exits_zero(capsys):
    """--version prints the version string and exits with code 0."""
    parser = VersionedArgs._build_parser_()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "1.2.3" in captured.out


def test_no_version_flag_without_version_attr():
    """Classes without _version_ don't get a --version flag."""
    parser = LiteralArgs._build_parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--version" not in flags


# --- Phase 4: duho.main()/__run__ dispatch -------------------------------


class ServeCmd(Args):
    """Start the server."""

    port: int = 8000
    "Port to listen on"
    ("--port",)

    def __run__(self):
        return 11


class BuildCmd(Args):
    """Build the project."""

    output: str = "out"
    "Output path"
    ("--output",)

    def __run__(self):
        return 22


class DispatchApp(Args):
    """App with two subcommands with distinct __run__ return values."""

    _subcommands_ = [ServeCmd, BuildCmd]


def test_main_dispatch_first_subcommand():
    """duho.main dispatches to the selected subcommand's __run__."""
    rc = duho.main(DispatchApp, ["ServeCmd", "--port", "9000"], setup_logging=False)
    assert rc == 11


def test_main_dispatch_second_subcommand():
    """A different subcommand selection dispatches to its own __run__."""
    rc = duho.main(DispatchApp, ["BuildCmd", "--output", "dist"], setup_logging=False)
    assert rc == 22


class InnerCmd(Args):
    """Innermost leaf command."""

    value: int = 0
    "A value"
    ("--value",)

    def __run__(self):
        return 33


class MidCmd(Args):
    """Middle-level command with its own subcommands."""

    _subcommands_ = [InnerCmd]


class NestedApp(Args):
    """App with a two-level nested subcommand tree."""

    _subcommands_ = [MidCmd]


def test_main_dispatch_nested_subcommands():
    """A 2-level nested subcommand tree dispatches to the deepest __run__."""
    rc = duho.main(
        NestedApp, ["MidCmd", "InnerCmd", "--value", "7"], setup_logging=False
    )
    assert rc == 33


class NoRunArgs(Args):
    """Arguments for a class that never implements __run__."""

    x: int = 1
    "A value"
    ("--x",)


def test_main_missing_run_raises_not_implemented():
    """Selecting a class without __run__ raises NotImplementedError naming it."""
    with pytest.raises(NotImplementedError, match="NoRunArgs"):
        duho.main(NoRunArgs, [], setup_logging=False)


def test_main_none_return_maps_to_zero():
    """A __run__ returning None maps to exit code 0."""

    class NoneReturn(Args):
        def __run__(self):
            return None

    rc = duho.main(NoneReturn, [], setup_logging=False)
    assert rc == 0


def test_main_setup_logging_false_leaves_handlers_unchanged():
    """setup_logging=False must not add handlers to the root logger."""
    root = logging.getLogger()
    before = len(root.handlers)

    class LoggedApp(LoggingArgs):
        def __run__(self):
            return None

    rc = duho.main(LoggedApp, [], setup_logging=False)
    assert rc == 0
    assert len(root.handlers) == before


def test_main_systemexit_propagates():
    """SystemExit from argparse (e.g. missing required arg) propagates."""

    class RequiredArgs(Args):
        needed: str
        "Required value"
        ("--needed",)

        def __run__(self):
            return 0

    with pytest.raises(SystemExit):
        duho.main(RequiredArgs, [], setup_logging=False)
