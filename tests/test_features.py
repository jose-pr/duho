"""Tests for Literal/Enum choices, list[T] fields, --version, and Cmd dispatch."""

import enum
import logging
import typing as ty

import pytest

import duho
from duho import Args, Cmd, LoggingArgs


# --- Literal & Enum -> choices -----------------------------------------


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
    parser = LiteralArgs._parser_()
    args = parser.parse_args(["--mode", "fast"])
    assert args.mode == "fast"


def test_literal_choices_reject():
    """A value outside the Literal options raises SystemExit."""
    parser = LiteralArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "turbo"])


def test_enum_choices_by_name():
    """Enum fields accept the member name and produce the Enum member."""
    parser = EnumArgs._parser_()
    args = parser.parse_args(["--color", "RED"])
    assert args.color is Color.RED


def test_enum_choices_reject_bad_name():
    """A name that isn't a declared Enum member raises SystemExit."""
    parser = EnumArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--color", "PURPLE"])


def test_mixed_literal_round_trip():
    """Mixed-type Literal picks the type matching the declared value, not
    just the first type that doesn't raise (e.g. str would swallow "1")."""
    parser = MixedLiteralArgs._parser_()

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
    parser = MixedLiteralArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--value", "nope"])


# --- list[T] fields ------------------------------------------------------


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
    parser = ListArgs._parser_()
    args = parser.parse_args(["--tags", "a", "--tags", "b"])
    assert args.tags == ["a", "b"]


def test_list_accumulation_space_separated():
    """Space-separated `--x a b` accumulates via nargs="*"."""
    parser = ListArgs._parser_()
    args = parser.parse_args(["--tags", "a", "b"])
    assert args.tags == ["a", "b"]


def test_list_default_empty_when_undeclared():
    """A list field with no explicit default gets [] rather than None."""
    parser = ListArgs._parser_()
    args = parser.parse_args([])
    assert args.tags == []


def test_list_element_type_conversion():
    """list[int] converts each element with the element factory."""
    parser = ListArgs._parser_()
    args = parser.parse_args(["--numbers", "1", "2", "--numbers", "3"])
    assert args.numbers == [1, 2, 3]
    assert all(isinstance(n, int) for n in args.numbers)


# --- --version -----------------------------------------------------------


class VersionedArgs(Args):
    """Arguments with a _version_ attr."""

    _version_ = "1.2.3"

    name: str = "x"
    "Name"
    ("--name",)


def test_version_flag_prints_and_exits_zero(capsys):
    """--version prints the version string and exits with code 0."""
    parser = VersionedArgs._parser_()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "1.2.3" in captured.out


def test_no_version_flag_without_version_attr():
    """Classes without _version_ don't get a --version flag."""
    parser = LiteralArgs._parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--version" not in flags


class DunderVersionArgs(Args):
    """Arguments carrying only a conventional __version__ (no _version_)."""

    __version__ = "3.4.5"

    name: str = "x"
    "Name"
    ("--name",)


class BothVersionArgs(Args):
    """_version_ set alongside __version__ — _version_ must win."""

    _version_ = "1.0.0"
    __version__ = "9.9.9"

    name: str = "x"
    "Name"
    ("--name",)


def test_dunder_version_used_as_fallback(capsys):
    """A class __version__ string is used for --version when _version_ is unset."""
    parser = DunderVersionArgs._parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--version" in flags
    with pytest.raises(SystemExit):
        parser.parse_args(["--version"])
    assert "3.4.5" in capsys.readouterr().out


def test_explicit_version_wins_over_dunder():
    """_version_ takes precedence over a class __version__ fallback."""
    from duho.args import _resolve_version

    assert _resolve_version(BothVersionArgs) == "1.0.0"


# --- _version_ = duho.AUTO ------------------------------------------------


class AutoVersionArgs(Args):
    """Arguments with _version_ = duho.AUTO (no _distribution_ override)."""

    _version_ = duho.AUTO

    name: str = "x"
    "Name"
    ("--name",)


class AutoVersionDistArgs(Args):
    """Arguments with _version_ = duho.AUTO and a _distribution_ override."""

    _version_ = duho.AUTO
    _distribution_ = "some-other-package"

    name: str = "x"
    "Name"
    ("--name",)


def test_auto_version_resolves(monkeypatch, capsys):
    """AUTO resolves via importlib.metadata.version and adds --version."""
    monkeypatch.setattr(
        "importlib.metadata.version", lambda dist: "9.9.9"
    )
    parser = AutoVersionArgs._parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--version" in flags
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "9.9.9" in captured.out


def test_auto_version_uses_distribution_override(monkeypatch):
    """_distribution_ overrides the dist name passed to importlib.metadata.version."""
    calls = []

    def fake_version(dist):
        calls.append(dist)
        return "1.0.0"

    monkeypatch.setattr("importlib.metadata.version", fake_version)
    AutoVersionDistArgs._parser_()
    assert calls == ["some-other-package"]


def test_auto_version_not_found_skips_flag(monkeypatch):
    """AUTO that can't be resolved (PackageNotFoundError) skips --version silently."""

    import importlib.metadata as _importlib_metadata

    def raise_not_found(dist):
        raise _importlib_metadata.PackageNotFoundError(dist)

    monkeypatch.setattr("importlib.metadata.version", raise_not_found)
    parser = AutoVersionArgs._parser_()  # must not raise

    assert "version" not in {action.dest for action in parser._actions}


# --- duho.main()/__call__ dispatch ----------------------------------------


class ServeCmd(Cmd):
    """Start the server."""

    port: int = 8000
    "Port to listen on"
    ("--port",)

    def __call__(self):
        return 11


class BuildCmd(Cmd):
    """Build the project."""

    output: str = "out"
    "Output path"
    ("--output",)

    def __call__(self):
        return 22


class DispatchApp(Args):
    """App with two subcommands with distinct return values."""

    _subcommands_ = [ServeCmd, BuildCmd]


def test_main_dispatch_first_subcommand():
    """duho.main dispatches to the selected subcommand's __call__()."""
    rc = duho.main(DispatchApp, ["ServeCmd", "--port", "9000"], setup_logging=False)
    assert rc == 11


def test_main_dispatch_second_subcommand():
    """A different subcommand selection dispatches to its own __call__."""
    rc = duho.main(DispatchApp, ["BuildCmd", "--output", "dist"], setup_logging=False)
    assert rc == 22


class AliasedCmd(Cmd):
    """A subcommand registered under a name plus short aliases."""

    _parsername_ = "create"
    _parseraliases_ = ["c", "cr"]

    tag: str = "none"
    "A tag value"
    ("--tag",)

    def __call__(self):
        return self.tag


class AliasApp(Args):
    """App whose subcommand carries `_parseraliases_`."""

    _subcommands_ = [AliasedCmd]


def test_subcommand_canonical_name_dispatches():
    """The canonical `_parsername_` still selects the subcommand."""
    rc = duho.main(AliasApp, ["create", "--tag", "x"], setup_logging=False)
    assert rc == "x"


def test_subcommand_alias_dispatches_to_same_run():
    """Each `_parseraliases_` entry dispatches to the same command."""
    assert duho.main(AliasApp, ["c", "--tag", "y"], setup_logging=False) == "y"
    assert duho.main(AliasApp, ["cr", "--tag", "z"], setup_logging=False) == "z"


def test_subcommand_without_aliases_still_works():
    """Absence of `_parseraliases_` is the unchanged default (no aliases added)."""
    rc = duho.main(DispatchApp, ["ServeCmd", "--port", "9000"], setup_logging=False)
    assert rc == 11


class InnerCmd(Cmd):
    """Innermost leaf command."""

    value: int = 0
    "A value"
    ("--value",)

    def __call__(self):
        return 33


class MidCmd(Args):
    """Middle-level command with its own subcommands."""

    _subcommands_ = [InnerCmd]


class NestedApp(Args):
    """App with a two-level nested subcommand tree."""

    _subcommands_ = [MidCmd]


def test_main_dispatch_nested_subcommands():
    """A 2-level nested subcommand tree dispatches to the deepest command."""
    rc = duho.main(
        NestedApp, ["MidCmd", "InnerCmd", "--value", "7"], setup_logging=False
    )
    assert rc == 33


class NoRunArgs(Args):
    """A bare data Args: no `__call__`, so not runnable."""

    x: int = 1
    "A value"
    ("--x",)


def test_main_bare_args_not_runnable_raises_not_implemented():
    """Dispatching a bare data Args raises NotImplementedError naming it.

    Since the Plan-13 Args/Cmd split, `duho.main` expects a runnable `Cmd`;
    a data-only `Args` (no `__call__`) fails loud rather than
    silently no-op'ing.
    """
    with pytest.raises(NotImplementedError, match="NoRunArgs"):
        duho.main(NoRunArgs, [], setup_logging=False)


def test_main_none_return_maps_to_zero():
    """A command whose __call__ returns None maps to exit code 0."""

    class NoneReturn(Cmd):
        def __call__(self):
            return None

    rc = duho.main(NoneReturn, [], setup_logging=False)
    assert rc == 0


def test_main_setup_logging_false_leaves_handlers_unchanged():
    """setup_logging=False must not add handlers to the root logger."""
    root = logging.getLogger()
    before = len(root.handlers)

    class LoggedApp(LoggingArgs, Cmd):
        def __call__(self):
            return None

    rc = duho.main(LoggedApp, [], setup_logging=False)
    assert rc == 0
    assert len(root.handlers) == before


def test_main_systemexit_propagates():
    """SystemExit from argparse (e.g. missing required arg) propagates."""

    class RequiredArgs(Cmd):
        needed: str
        "Required value"
        ("--needed",)

        def __call__(self):
            return 0

    with pytest.raises(SystemExit):
        duho.main(RequiredArgs, [], setup_logging=False)
