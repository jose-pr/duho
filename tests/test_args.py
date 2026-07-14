"""Tests for duho.cli.args module."""

import argparse
import sys
import typing as ty
import pytest
from duho import Args, Argument, ArgumentBuilder, build_parser
from duho.parsers import prerun_parse


class SimpleArgs(Args):
    """A simple argument set."""
    name: str
    "The name parameter"
    ("--name",)


class OptionalArgs(Args):
    """Arguments with optional fields."""
    name: str
    "Required name"
    ("--name",)

    count: ty.Optional[int] = None
    "Optional count"
    ("--count",)


class DefaultArgs(Args):
    """Arguments with defaults."""
    name: str = "default"
    "Name with default"
    ("--name",)

    verbose: bool = False
    "Verbose flag"
    ("--verbose",)


class UnionArgs(Args):
    """Arguments with union types."""
    value: ty.Union[int, str]
    "Can be int or str"
    ("--value",)


def test_simple_args():
    """Test parsing simple string arguments."""
    parser = SimpleArgs._build_parser_()
    args = parser.parse_args(["--name", "Alice"])
    assert args.name == "Alice"
    assert isinstance(args, SimpleArgs)


def test_optional_args():
    """Test optional arguments."""
    parser = OptionalArgs._build_parser_()

    # With provided value
    args = parser.parse_args(["--name", "Bob", "--count", "5"])
    assert args.name == "Bob"
    assert args.count == 5

    # Without optional value
    args = parser.parse_args(["--name", "Bob"])
    assert args.name == "Bob"
    assert args.count is None


def test_default_args():
    """Test default values."""
    parser = DefaultArgs._build_parser_()

    # Override defaults
    args = parser.parse_args(["--name", "Charlie", "--verbose"])
    assert args.name == "Charlie"
    assert args.verbose is True

    # Use defaults
    args = parser.parse_args([])
    assert args.name == "default"
    assert args.verbose is False


def test_bool_flags():
    """Test boolean flag parsing."""
    parser = DefaultArgs._build_parser_()

    # Flag not provided (default False)
    args = parser.parse_args([])
    assert args.verbose is False

    # Flag provided (becomes True)
    args = parser.parse_args(["--verbose"])
    assert args.verbose is True


def test_type_conversion():
    """Test automatic type conversion."""
    parser = OptionalArgs._build_parser_()
    args = parser.parse_args(["--name", "test", "--count", "42"])
    assert isinstance(args.count, int)
    assert args.count == 42


def test_union_types():
    """Test union type handling."""
    parser = UnionArgs._build_parser_()

    # Parse as int if possible
    args = parser.parse_args(["--value", "123"])
    assert args.value == 123
    assert isinstance(args.value, int)

    # Parse as string if int fails
    args = parser.parse_args(["--value", "not_a_number"])
    assert args.value == "not_a_number"
    assert isinstance(args.value, str)


def test_parser_name():
    """Test that parser inherits class name."""
    parser = SimpleArgs._build_parser_()
    assert parser.prog == "SimpleArgs"


def test_help_from_docstring():
    """Test that class docstring becomes parser description."""
    parser = SimpleArgs._build_parser_()
    assert parser.description == "A simple argument set."


def test_argument_help_from_docstring():
    """Test that field docstrings become argument help."""
    parser = SimpleArgs._build_parser_()
    # Find the action for --name
    for action in parser._actions:
        if "--name" in action.option_strings:
            assert action.help == "The name parameter"
            break
    else:
        assert False, "--name action not found"


def test_required_vs_optional():
    """Test required vs optional argument detection."""
    parser = SimpleArgs._build_parser_()

    # name is required (no default)
    required_found = False
    for action in parser._actions:
        if "--name" in action.option_strings:
            assert action.required is True
            required_found = True
            break
    assert required_found


class PositionalArgs(Args):
    """Test positional arguments."""
    input_file: str
    "Input file to process"
    ("input",)

    output_file: str = "output.txt"
    "Output file"
    ("output",)


def test_positional_arguments():
    """Test positional (non-flag) arguments."""
    parser = PositionalArgs._build_parser_()
    args = parser.parse_args(["input.txt", "output.txt"])
    # Positional args map to action dest, which is the field name
    assert hasattr(args, "input_file") or hasattr(args, "input")
    assert hasattr(args, "output_file") or hasattr(args, "output")


class ShortFlagsArgs(Args):
    """Test short flag syntax."""
    verbose: bool = False
    "Verbose output"
    ("-v",)


class MultiFlag(Args):
    """Arguments with multiple flag names."""
    verbose: int = 0
    "Verbosity level"
    ("-v", "--verbose")


def test_short_flags():
    """Test single-character flags."""
    parser = ShortFlagsArgs._build_parser_()
    args = parser.parse_args(["-v"])
    assert args.verbose is True


def test_multiple_flags():
    """Test arguments with multiple flag names."""
    parser = MultiFlag._build_parser_()

    # Both short and long forms work
    args = parser.parse_args(["-v", "2"])
    assert args.verbose == 2

    args = parser.parse_args(["--verbose", "3"])
    assert args.verbose == 3


def test_subparser_integration():
    """Test building parsers for subcommands."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    SimpleArgs._build_parser_(subparsers, name="simple")
    DefaultArgs._build_parser_(subparsers, name="default")

    # Should not raise
    assert subparsers is not None


def test_module_level_build_parser():
    """Test module-level build_parser function."""
    parser = build_parser(SimpleArgs)
    args = parser.parse_args(["--name", "test"])
    assert args.name == "test"
    assert isinstance(args, SimpleArgs)


class GrandparentArgs(Args):
    """Grandparent docstring."""
    shared: str = "gp"
    "Shared field from grandparent"
    ("--shared",)


class FirstMixin(GrandparentArgs):
    """First mixin."""


class SecondMixin(Args):
    """Second mixin."""


class MultiBaseArgs(FirstMixin, SecondMixin):
    """Class with two mixin bases; first mixin's parent defines `shared`."""


def test_multi_base_ancestry_docstring():
    """A field's docstring from a grandparent reached via the FIRST mixin's
    ancestry must surface, exercising cls.__mro__ (not just direct bases)."""
    parser = MultiBaseArgs._build_parser_()
    for action in parser._actions:
        if "--shared" in action.option_strings:
            assert action.help == "Shared field from grandparent"
            break
    else:
        assert False, "--shared action not found"


class UnderscoreFieldArgs(Args):
    """Underscore-prefixed annotated names are skipped from discovery."""
    _secret: str = "x"
    "Should never become a flag"
    ("--secret",)

    name: str = "ok"
    "Normal field"
    ("--name",)


def test_underscore_prefixed_field_skipped():
    """`_secret` must produce neither `--secret` nor `--_secret`."""
    parser = UnderscoreFieldArgs._build_parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--secret" not in flags
    assert "--_secret" not in flags


class MethodNameMixin(Args):
    """Defines a plain method named `count`."""

    def count(self):
        return 42


class MethodCollisionArgs(MethodNameMixin):
    """Field name collides with an inherited plain method."""
    count: int
    "Count field colliding with inherited method"
    ("--count",)


def test_field_name_colliding_with_inherited_method_stays_required():
    """A field whose name matches an inherited method must stay required,
    not silently adopt the bound method as its default."""
    parser = MethodCollisionArgs._build_parser_()
    for action in parser._actions:
        if "--count" in action.option_strings:
            assert action.required is True
            assert not callable(action.default) or action.default is None
            break
    else:
        assert False, "--count action not found"


def test_typing_optional_not_required():
    """ty.Optional[int] (typing.Union[int, None]) must not be required."""
    parser = OptionalArgs._build_parser_()
    for action in parser._actions:
        if "--count" in action.option_strings:
            assert action.required is False
            break
    else:
        assert False, "--count action not found"


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="PEP 604 unions require Python 3.10+"
)
def test_pep604_union_type_conversion():
    """`int | str` field converts '5' -> int and 'x' -> str."""

    class Pep604UnionArgs(Args):
        """Arguments with a PEP 604 union type."""
        value: eval("int | str")
        "Can be int or str"
        ("--value",)

    parser = Pep604UnionArgs._build_parser_()

    args = parser.parse_args(["--value", "5"])
    assert args.value == 5
    assert isinstance(args.value, int)

    args = parser.parse_args(["--value", "x"])
    assert args.value == "x"
    assert isinstance(args.value, str)


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="PEP 604 unions require Python 3.10+"
)
def test_pep604_optional_not_required():
    """`int | None` field must not be required."""

    class Pep604OptionalArgs(Args):
        """Arguments with a PEP 604 optional type."""
        count: eval("int | None") = None
        "Optional count"
        ("--count",)

    parser = Pep604OptionalArgs._build_parser_()
    for action in parser._actions:
        if "--count" in action.option_strings:
            assert action.required is False
            break
    else:
        assert False, "--count action not found"

    args = parser.parse_args([])
    assert args.count is None


class BoolDefaultTrueArgs(Args):
    """Arguments with a bool field defaulting to True."""
    flag: bool = True
    "A flag defaulting to True"
    ("--flag",)


def test_bool_default_true_round_trip():
    """bool field with default True gets --flag/--no-flag via BooleanOptionalAction."""
    parser = BoolDefaultTrueArgs._build_parser_()

    args = parser.parse_args([])
    assert args.flag is True

    args = parser.parse_args(["--flag"])
    assert args.flag is True

    args = parser.parse_args(["--no-flag"])
    assert args.flag is False


def test_prerun_parse_restores_patches_on_systemexit():
    """prerun_parse must restore _HelpAction/_SubParsersAction.__call__ even
    when the underlying parse raises SystemExit (e.g. bad args)."""
    help_call_before = argparse._HelpAction.__call__
    subparsers_call_before = argparse._SubParsersAction.__call__

    parser = argparse.ArgumentParser()
    parser.add_argument("--required-thing", required=True)

    with pytest.raises(SystemExit):
        # parse_known_args raises SystemExit for unrecognized/invalid options
        # in some configurations; force one via an invalid choice-style parser.
        sub_parser = argparse.ArgumentParser()
        sub_parser.add_argument("--num", type=int, required=True)
        prerun_parse(sub_parser, ["--num", "not-a-number"])

    assert argparse._HelpAction.__call__ is help_call_before
    assert argparse._SubParsersAction.__call__ is subparsers_call_before


class NoLeakArgs(Args):
    """Arguments used to confirm #cls does not leak into the instance."""
    name: str = "x"
    "Name"
    ("--name",)


def test_no_hash_cls_leak_in_parsed_instance():
    """The internal '#cls' bookkeeping key must not survive into vars(instance)."""
    parser = NoLeakArgs._build_parser_()
    args = parser.parse_args([])
    assert "#cls" not in vars(args)
