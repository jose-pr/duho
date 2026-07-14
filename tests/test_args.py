"""Tests for duho.cli.args module."""

import argparse
import typing as ty
from duho import Args, Argument, ArgumentBuilder


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
    parser = SimpleArgs.build_parser()
    args = parser.parse_args(["--name", "Alice"])
    assert args.name == "Alice"
    assert isinstance(args, SimpleArgs)


def test_optional_args():
    """Test optional arguments."""
    parser = OptionalArgs.build_parser()

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
    parser = DefaultArgs.build_parser()

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
    parser = DefaultArgs.build_parser()

    # Flag not provided (default False)
    args = parser.parse_args([])
    assert args.verbose is False

    # Flag provided (becomes True)
    args = parser.parse_args(["--verbose"])
    assert args.verbose is True


def test_type_conversion():
    """Test automatic type conversion."""
    parser = OptionalArgs.build_parser()
    args = parser.parse_args(["--name", "test", "--count", "42"])
    assert isinstance(args.count, int)
    assert args.count == 42


def test_union_types():
    """Test union type handling."""
    parser = UnionArgs.build_parser()

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
    parser = SimpleArgs.build_parser()
    assert parser.prog == "SimpleArgs"


def test_help_from_docstring():
    """Test that class docstring becomes parser description."""
    parser = SimpleArgs.build_parser()
    assert parser.description == "A simple argument set."


def test_argument_help_from_docstring():
    """Test that field docstrings become argument help."""
    parser = SimpleArgs.build_parser()
    # Find the action for --name
    for action in parser._actions:
        if "--name" in action.option_strings:
            assert action.help == "The name parameter"
            break
    else:
        assert False, "--name action not found"


def test_required_vs_optional():
    """Test required vs optional argument detection."""
    parser = SimpleArgs.build_parser()

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
    parser = PositionalArgs.build_parser()
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
    parser = ShortFlagsArgs.build_parser()
    args = parser.parse_args(["-v"])
    assert args.verbose is True


def test_multiple_flags():
    """Test arguments with multiple flag names."""
    parser = MultiFlag.build_parser()

    # Both short and long forms work
    args = parser.parse_args(["-v", "2"])
    assert args.verbose == 2

    args = parser.parse_args(["--verbose", "3"])
    assert args.verbose == 3


def test_subparser_integration():
    """Test building parsers for subcommands."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    SimpleArgs.build_parser(subparsers, name="simple")
    DefaultArgs.build_parser(subparsers, name="default")

    # Should not raise
    assert subparsers is not None
