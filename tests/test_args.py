"""Tests for duho.cli.args module."""

import argparse
import enum
import sys
import typing as ty
import pytest
from duho import Append, Arg, Args, Argument, ArgumentBuilder, Choice, Const, Count, NS, parser as duho_parser
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
    parser = SimpleArgs._parser_()
    args = parser.parse_args(["--name", "Alice"])
    assert args.name == "Alice"
    assert isinstance(args, SimpleArgs)


def test_optional_args():
    """Test optional arguments."""
    parser = OptionalArgs._parser_()

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
    parser = DefaultArgs._parser_()

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
    parser = DefaultArgs._parser_()

    # Flag not provided (default False)
    args = parser.parse_args([])
    assert args.verbose is False

    # Flag provided (becomes True)
    args = parser.parse_args(["--verbose"])
    assert args.verbose is True


def test_type_conversion():
    """Test automatic type conversion."""
    parser = OptionalArgs._parser_()
    args = parser.parse_args(["--name", "test", "--count", "42"])
    assert isinstance(args.count, int)
    assert args.count == 42


def test_union_types():
    """Test union type handling."""
    parser = UnionArgs._parser_()

    # Parse as int if possible
    args = parser.parse_args(["--value", "123"])
    assert args.value == 123
    assert isinstance(args.value, int)

    # Parse as string if int fails
    args = parser.parse_args(["--value", "not_a_number"])
    assert args.value == "not_a_number"
    assert isinstance(args.value, str)


def test_union_enum_resolves_by_name():
    """Union[Enum, str]: an enum-member name short-circuits to the enum
    (resolved by NAME, not value); a non-matching string falls through to
    the str member unchanged."""

    class Color(enum.Enum):
        RED = 1
        GREEN = 2

    class UnionEnumArgs(Args):
        """Arguments with a union of an enum and str."""
        col: ty.Union[Color, str]
        "Can be a Color name or an arbitrary string"
        ("--col",)

    parser = UnionEnumArgs._parser_()

    args = parser.parse_args(["--col", "RED"])
    assert args.col is Color.RED

    args = parser.parse_args(["--col", "freeform"])
    assert args.col == "freeform"


def test_parser_name():
    """Test that parser inherits class name."""
    parser = SimpleArgs._parser_()
    assert parser.prog == "SimpleArgs"


def test_help_from_docstring():
    """Test that class docstring becomes parser description."""
    parser = SimpleArgs._parser_()
    assert parser.description == "A simple argument set."


def test_argument_help_from_docstring():
    """Test that field docstrings become argument help."""
    parser = SimpleArgs._parser_()
    # Find the action for --name
    for action in parser._actions:
        if "--name" in action.option_strings:
            assert action.help == "The name parameter"
            break
    else:
        assert False, "--name action not found"


def test_required_vs_optional():
    """Test required vs optional argument detection."""
    parser = SimpleArgs._parser_()

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
    parser = PositionalArgs._parser_()
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
    parser = ShortFlagsArgs._parser_()
    args = parser.parse_args(["-v"])
    assert args.verbose is True


def test_multiple_flags():
    """Test arguments with multiple flag names."""
    parser = MultiFlag._parser_()

    # Both short and long forms work
    args = parser.parse_args(["-v", "2"])
    assert args.verbose == 2

    args = parser.parse_args(["--verbose", "3"])
    assert args.verbose == 3


def test_subparser_integration():
    """Test building parsers for subcommands."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    SimpleArgs._parser_(subparsers, name="simple")
    DefaultArgs._parser_(subparsers, name="default")

    # Should not raise
    assert subparsers is not None


def test_module_level_parser():
    """Test module-level parser() function (rename smoke test)."""
    parser = duho_parser(SimpleArgs)
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
    parser = MultiBaseArgs._parser_()
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
    parser = UnderscoreFieldArgs._parser_()
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
    parser = MethodCollisionArgs._parser_()
    for action in parser._actions:
        if "--count" in action.option_strings:
            assert action.required is True
            assert not callable(action.default) or action.default is None
            break
    else:
        assert False, "--count action not found"


def test_typing_optional_not_required():
    """ty.Optional[int] (typing.Union[int, None]) must not be required."""
    parser = OptionalArgs._parser_()
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

    parser = Pep604UnionArgs._parser_()

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

    parser = Pep604OptionalArgs._parser_()
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
    parser = BoolDefaultTrueArgs._parser_()

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
    parser = NoLeakArgs._parser_()
    args = parser.parse_args([])
    assert "#cls" not in vars(args)


# --- implicit flag names derived from the field name ---


class ImplicitFlagFromDocstringArgs(Args):
    """A field with a docstring but no flag tuple derives --count from the name."""
    count: ty.Optional[int] = None
    "Optional count"


def test_implicit_flag_derived_from_name_with_docstring():
    """No flag tuple + docstring present -> flag still derives from field name."""
    parser = ImplicitFlagFromDocstringArgs._parser_()
    args = parser.parse_args(["--count", "5"])
    assert args.count == 5

    args = parser.parse_args([])
    assert args.count is None


class ImplicitFlagNoDocstringArgs(Args):
    """A field with neither docstring nor flag tuple still derives --workers."""
    workers: int = 4


def test_implicit_flag_derived_from_name_no_docstring():
    parser = ImplicitFlagNoDocstringArgs._parser_()
    args = parser.parse_args(["--workers", "8"])
    assert args.workers == 8

    args = parser.parse_args([])
    assert args.workers == 4


class UnderscoreToDashArgs(Args):
    """A field with underscores in its name, no flag tuple, dashes when derived."""
    dry_run: bool = False


def test_implicit_flag_underscore_to_dash():
    parser = UnderscoreToDashArgs._parser_()
    flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--dry-run" in flags
    assert "--dry_run" not in flags

    args = parser.parse_args(["--dry-run"])
    assert args.dry_run is True


# --- full argparse kwargs passthrough via Arg[T, NS(...)] ---


class KwargsOverrideArgs(Args):
    """NS(kwargs={...}) must win over explicit NS(field=...) values."""
    mode: Arg[str, NS(required=True, kwargs={"required": False, "default": "x"})] = None
    "Mode with conflicting required flags"
    ("--mode",)


def test_kwargs_dict_overrides_explicit_field():
    """The raw kwargs dict escape hatch takes precedence over field-derived values."""
    parser = KwargsOverrideArgs._parser_()
    args = parser.parse_args([])
    assert args.mode == "x"


class StoreConstArgs(Args):
    """store_const action requires and forwards const=."""
    mode: Arg[str, Const("fast")] = "slow"
    "Mode flag"
    ("--fast",)


def test_store_const_requires_and_forwards_const():
    parser = StoreConstArgs._parser_()
    args = parser.parse_args(["--fast"])
    assert args.mode == "fast"

    args = parser.parse_args([])
    assert args.mode == "slow"


def test_store_const_without_const_raises():
    """action='store_const' with no const= must fail loudly, not silently pass None."""

    class BadConstArgs(Args):
        """Missing const for store_const."""
        mode: Arg[str, NS(action="store_const")] = None
        "Mode flag missing const"
        ("--fast",)

    with pytest.raises(ValueError):
        BadConstArgs._parser_()


def test_action_version_forwards_version_and_suppresses_type():
    class VersionArgs(Args):
        """Class exercising a manual version action."""
        ver: Arg[str, NS(action="version", version="myprog 1.2.3")] = None
        "Show version"
        ("--show-version",)

    parser = VersionArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--show-version"])


def test_type_incompatible_actions_suppress_type_kwarg():
    """store_true/store_false/count/etc must never receive type= (argparse rejects it)."""
    for action in ("store_true", "store_false", "count"):

        class ActionArgs(Args):
            f"""Class exercising action={action!r}."""
            flag: Arg[int, NS(action=action)] = 0
            "A flag"
            ("--flag",)

        parser = ActionArgs._parser_()
        for a in parser._actions:
            if "--flag" in a.option_strings:
                assert a.type is None
                break
        else:
            assert False, f"--flag action not found for action={action!r}"


# --- positional arguments ---


class RequiredPositionalArgs(Args):
    """A single required positional argument."""
    src: str
    "Source path"
    ("src",)


def test_required_positional():
    parser = RequiredPositionalArgs._parser_()
    args = parser.parse_args(["in.txt"])
    assert args.src == "in.txt"

    with pytest.raises(SystemExit):
        parser.parse_args([])


class OptionalPositionalArgs(Args):
    """A positional with a real default becomes optional (nargs='?')."""
    dst: str = "-"
    "Destination path"
    ("dst",)


def test_optional_positional_uses_nargs_question_mark():
    parser = OptionalPositionalArgs._parser_()
    for action in parser._actions:
        if action.dest == "dst":
            assert action.nargs == "?"
            assert action.required is False
            break
    else:
        assert False, "dst positional action not found"

    args = parser.parse_args([])
    assert args.dst == "-"

    args = parser.parse_args(["out.txt"])
    assert args.dst == "out.txt"


class TwoPositionalsArgs(Args):
    """Two positionals preserve declaration order."""
    src: str
    "Source"
    ("src",)

    dst: str = "-"
    "Destination"
    ("dst",)


def test_two_positionals_preserve_order():
    parser = TwoPositionalsArgs._parser_()
    args = parser.parse_args(["in.txt"])
    assert args.src == "in.txt"
    assert args.dst == "-"

    args = parser.parse_args(["in.txt", "out.txt"])
    assert args.src == "in.txt"
    assert args.dst == "out.txt"


class PositionalNargsPlusArgs(Args):
    """A positional bound to nargs='+' via Arg[list, NS(nargs='+')]."""
    files: Arg[list, NS(nargs="+")]
    "Files to process"
    ("files",)


def test_positional_nargs_plus():
    parser = PositionalNargsPlusArgs._parser_()
    args = parser.parse_args(["a.txt", "b.txt", "c.txt"])
    assert args.files == ["a.txt", "b.txt", "c.txt"]

    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_positional_never_gets_required_kwarg():
    """required= must never be passed for positionals (argparse forbids it)."""
    for action in RequiredPositionalArgs._getargs_():
        kwargs = action._kwargs()
        assert "required" not in kwargs


# --- argument helper factories (Count/Append/Const/Choice) ---


class CountArgs(Args):
    """verbose: Arg[int, Count()] counts repeated -v flags."""
    verbose: Arg[int, Count()] = 0
    "Verbosity"
    ("-v", "--verbose")


def test_count_helper():
    parser = CountArgs._parser_()
    args = parser.parse_args(["-vvv"])
    assert args.verbose == 3

    args = parser.parse_args([])
    assert args.verbose == 0


class AppendArgs(Args):
    """tags: Arg[list, Append()] accumulates repeated --tags flags."""
    tags: Arg[list, Append()] = []
    "Tags"
    ("--tags",)


def test_append_helper():
    parser = AppendArgs._parser_()
    args = parser.parse_args(["--tags", "a", "--tags", "b"])
    assert args.tags == ["a", "b"]


class ConstHelperArgs(Args):
    """mode: Arg[str, Const('fast')] sets the const value on presence."""
    mode: Arg[str, Const("fast")] = "slow"
    "Mode"
    ("--fast",)


def test_const_helper():
    parser = ConstHelperArgs._parser_()
    args = parser.parse_args(["--fast"])
    assert args.mode == "fast"

    args = parser.parse_args([])
    assert args.mode == "slow"


class ChoiceArgs(Args):
    """mode: Arg[str, Choice('a', 'b')] restricts accepted values."""
    mode: Arg[str, Choice("a", "b")] = "a"
    "Mode"
    ("--mode",)


def test_choice_helper():
    parser = ChoiceArgs._parser_()
    args = parser.parse_args(["--mode", "a"])
    assert args.mode == "a"

    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "c"])
