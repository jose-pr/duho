"""Tests for duho.cli.args module."""

import argparse
import datetime
import enum
import sys
import typing as ty
import pytest
from duho import (
    Append,
    Arg,
    Args,
    Choice,
    Const,
    Count,
    Extend,
    NS,
    parse,
    parser as duho_parser,
)


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


class _OptListInt(Args):
    nums: "ty.Optional[ty.List[int]]"
    "Numbers"
    ("--nums",)


def test_optional_list_int_multi():
    # A list-as-option field takes ONE value per occurrence -- repeat the
    # flag for more (`--nums 1 --nums 2`), not space-separated in one
    # occurrence (which is reserved for the POSITIONAL case).
    r = parse(_OptListInt, ["--nums", "1", "--nums", "2"])
    assert r.nums == [1, 2]


def test_optional_list_int_single_token():
    # A naive Optional element factory would char-split "123" -> ['1','2','3'];
    # here it is a single-element list [123].
    r = parse(_OptListInt, ["--nums", "123"])
    assert r.nums == [123]


def test_union_with_collection_member_is_build_error():
    class _BadUnion(Args):
        x: ty.Union[ty.List[int], str]
        ("--x",)

    with pytest.raises(ValueError):
        _BadUnion._parser_()


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
    # The positional's own literal name ("input"/"output") is the dest, not
    # the field name it's declared on (`input_file`/`output_file`).
    assert args.input == "input.txt"
    assert args.output == "output.txt"


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
    subparsers = parser.add_subparsers(dest="cmd")

    SimpleArgs._parser_(subparsers, name="simple")
    DefaultArgs._parser_(subparsers, name="default")

    # Both registered subcommands actually parse via their own class fields.
    args = parser.parse_args(["simple", "--name", "x"])
    assert args.cmd == "simple"
    assert args.name == "x"

    args = parser.parse_args(["default"])
    assert args.cmd == "default"
    assert args.name == "default"


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


class OptionalTypedPositionalNoDefaultArgs(Args):
    """An `Optional[T]` positional with NO explicit default. `Optional[T]`
    means "not required" for an option; a positional with no default used to
    stay required anyway (A026)."""

    target: "ty.Optional[int]"
    "Target"
    ("target",)


def test_optional_positional_without_default_is_not_required():
    parser = OptionalTypedPositionalNoDefaultArgs._parser_()
    args = parser.parse_args([])
    assert args.target is None

    args = parser.parse_args(["3"])
    assert args.target == 3


class OptionalNoDefaultOptionNoAssignArgs(Args):
    """Same shape, declared with no `= None` assignment at all."""

    timeout: "ty.Optional[int]"
    "Timeout"
    ("--timeout",)


def test_effective_default_is_none_for_non_required_option_without_default():
    """`_effective_default_()` used to return NOT_DEFINED here, so a direct
    instance had no attribute at all, although a parsed one gets None
    (A027)."""
    [builder] = [
        b
        for b in OptionalNoDefaultOptionNoAssignArgs._getargs_()
        if b.name == "timeout"
    ]
    assert builder._effective_default_() is None
    inst = OptionalNoDefaultOptionNoAssignArgs()
    assert inst.timeout is None


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


class CountNoDefaultArgs(Args):
    """Same as the README's `Arg[int, Count()]` row, with NO `= 0` default --
    Count/Const/store_false with no declared default used to become a
    mandatory option (A051)."""

    verbose: Arg[int, Count()]
    "Verbosity"
    ("-v", "--verbose")


def test_count_with_no_default_is_not_required():
    parser = CountNoDefaultArgs._parser_()
    args = parser.parse_args([])
    assert args.verbose == 0

    args = parser.parse_args(["-vv"])
    assert args.verbose == 2


class StoreFalseNoDefaultArgs(Args):
    """A `store_false` action with no declared default."""

    keep: Arg[bool, NS(action="store_false")]
    "Keep"
    ("--no-keep",)


def test_store_false_with_no_default_is_not_required():
    parser = StoreFalseNoDefaultArgs._parser_()
    args = parser.parse_args([])
    assert args.keep is True

    args = parser.parse_args(["--no-keep"])
    assert args.keep is False


class ConstNoDefaultArgs(Args):
    """A `store_const` field (via Const()) with no declared default."""

    mode: Arg[str, Const("fast")]
    "Mode"
    ("--fast",)


def test_const_with_no_default_is_not_required():
    parser = ConstNoDefaultArgs._parser_()
    args = parser.parse_args([])
    assert args.mode is None

    args = parser.parse_args(["--fast"])
    assert args.mode == "fast"


class RawKwargsConstArgs(Args):
    """`const=` supplied through the raw NS(kwargs={...}) escape hatch must
    still be seen by the store_const/append_const build-time check (A052)."""

    fast: Arg[int, NS(kwargs={"action": "store_const", "const": 5})] = 0
    "Fast mode"
    ("--fast",)


def test_const_via_raw_kwargs_escape_hatch():
    parser = RawKwargsConstArgs._parser_()
    args = parser.parse_args(["--fast"])
    assert args.fast == 5


class RawKwargsVersionArgs(Args):
    """`version=` supplied through the raw NS(kwargs={...}) escape hatch."""

    dummy: Arg[str, NS(kwargs={"action": "version", "version": "9.9"})] = None
    "Version"
    ("--ver",)


def test_version_via_raw_kwargs_escape_hatch(capsys):
    parser = RawKwargsVersionArgs._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--ver"])
    assert "9.9" in capsys.readouterr().out


class AppendArgs(Args):
    """tags: Arg[list, Append()] accumulates repeated --tags flags."""

    tags: Arg[list, Append()] = []
    "Tags"
    ("--tags",)


def test_append_helper():
    parser = AppendArgs._parser_()
    args = parser.parse_args(["--tags", "a", "--tags", "b"])
    assert args.tags == ["a", "b"]


class ExtendArgs(Args):
    """opts: Arg[list, Extend(',')] splits each occurrence on `,` and flattens."""

    opts: Arg[list, Extend(",")] = []
    "Options"
    ("--opts",)


def test_extend_helper_splits_and_flattens_single_occurrence():
    # Regression: nargs="*" (the list[str] default) plus a type that SPLITS
    # one token into several used to double-collect -- a single occurrence's
    # split result (itself a list) was appended as ONE nested element instead
    # of being flattened. Extend() now overrides nargs=None so a single
    # occurrence's split result becomes the flat list directly.
    parser = ExtendArgs._parser_()
    args = parser.parse_args(["--opts", "a,b"])
    assert args.opts == ["a", "b"]


def test_extend_helper_flattens_across_repeated_occurrences():
    parser = ExtendArgs._parser_()
    args = parser.parse_args(["--opts", "a,b", "--opts", "c"])
    assert args.opts == ["a", "b", "c"]


class ExtendNonEmptyDefaultArgs(Args):
    """A declared non-empty default used to be silently discarded regardless
    of whether the flag was ever given (A007): Extend() put its own
    `default=[]` into the raw kwargs= escape hatch, which always wins."""

    paths: Arg[list, Extend(":")] = ["/usr/bin"]
    "Search path"
    ("--path",)


def test_extend_keeps_declared_default_when_flag_absent():
    inst = parse(ExtendNonEmptyDefaultArgs, [])
    assert inst.paths == ["/usr/bin"]
    # A direct instance sees the same default (Args.__init__ seeds it too).
    assert ExtendNonEmptyDefaultArgs().paths == ["/usr/bin"]


def test_extend_cli_value_replaces_declared_default():
    inst = parse(ExtendNonEmptyDefaultArgs, ["--path", "a:b"])
    assert inst.paths == ["a", "b"]


class ExtendIntArgs(Args):
    """Extend() on a typed list[int] must convert the split parts through
    the element factory instead of leaving them as strings (A020)."""

    nums: Arg["list[int]", Extend(",")] = []
    "Numbers"
    ("--nums",)


def test_extend_composes_with_element_factory():
    inst = parse(ExtendIntArgs, ["--nums", "1,2"])
    assert inst.nums == [1, 2]
    assert all(isinstance(n, int) for n in inst.nums)


class ExtendSetArgs(Args):
    """Extend() on a set field must produce a set, not a list (A020)."""

    tags: Arg["set[str]", Extend(",")] = set()
    "Tags"
    ("--tags",)


def test_extend_on_set_field_produces_a_set_and_dedups():
    inst = parse(ExtendSetArgs, ["--tags", "a,b", "--tags", "a"])
    assert inst.tags == {"a", "b"}
    assert isinstance(inst.tags, set)


class ExtendEnvArgs(Args):
    """An Extend() field layered from an env var (A020c)."""

    paths: Arg[list, Extend(":"), NS(env="DUHO_TEST_A020_PATH")] = []
    "Search path"
    ("--path",)


def test_extend_env_value_is_split_not_nested(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_A020_PATH", "a:b")
    inst = parse(ExtendEnvArgs, [])
    assert inst.paths == ["a", "b"]


class ExtendConfigArgs(Args):
    """An Extend() field sourced from a TOML value (A020c)."""

    paths: Arg[list, Extend(",")] = []
    "Search path"
    ("--path",)


@pytest.mark.requires_toml
def test_extend_config_string_value_is_split(tmp_path):
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('paths = "a,b"\n')
    inst = parse(ExtendConfigArgs, [], config=cfg)
    assert inst.paths == ["a", "b"]


@pytest.mark.requires_toml
def test_extend_config_array_values_are_split_and_flattened(tmp_path):
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('paths = ["a,b", "c"]\n')
    inst = parse(ExtendConfigArgs, [], config=cfg)
    assert inst.paths == ["a", "b", "c"]


class AppendSetArgs(Args):
    """Append() forces argparse's stdlib list-only "append" action, which
    does not compose with a set field's own collection action (A020)."""

    tags: Arg["set[str]", Append()] = set()
    "Tags"
    ("--tags",)


def test_append_on_set_field_raises_at_build_time():
    with pytest.raises(ValueError, match="set"):
        AppendSetArgs._parser_()


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


# --- direct instance construction (Args()) ---


class DirectListDefaultArgs(Args):
    """A field with an explicit CLASS-level mutable default."""

    files: "list[str]" = []
    "Files"
    ("--file",)


def test_direct_instance_does_not_share_class_level_mutable_default():
    """Mutating a directly-built instance's list field used to mutate the
    CLASS ATTRIBUTE itself (A022): `hasattr(self, name)` is already True for
    a field with a class-level default, so `Args.__init__` skipped seeding a
    fresh copy onto the instance, and the instance just read the class
    attribute by inheritance."""
    a = DirectListDefaultArgs()
    assert "files" in vars(a)
    assert a.files is not DirectListDefaultArgs.files

    a.files.append("leak")
    assert DirectListDefaultArgs.files == []
    assert DirectListDefaultArgs().files == []
    assert parse(DirectListDefaultArgs, []).files == []


class _NoDefaultListArgs(Args):
    """A field with NO declared default at all (required, list[int])."""

    items: "ty.List[int]"
    "Items"
    ("--items",)


def test_no_default_list_field_is_not_shared_across_parses():
    """Two separate parse() calls of a required list field must never share
    the same underlying list object."""
    a = parse(_NoDefaultListArgs, [])
    a.items.append(99)
    b = parse(_NoDefaultListArgs, [])
    assert b.items == []
    assert _NoDefaultListArgs().items == []


# --- shared positional/bare-bool detection (A068) ---


class IsPositionalArgs(Args):
    option_field: str = "x"
    "An option"
    ("--option-field",)

    positional_field: str
    "A positional"
    ("positional_field",)

    bare_bool: bool = False
    "A bare bool -- a store_true flag"
    ("--bare-bool",)

    literal_bool: "ty.Literal[True, False]" = True
    "A Literal[True, False] field -- NOT a bare bool flag (carries choices)"
    ("--literal-bool",)


def test_is_positional_property_matches_flags():
    builders = {b.name: b for b in IsPositionalArgs._getargs_()}
    assert builders["option_field"].is_positional is False
    assert builders["positional_field"].is_positional is True


def test_is_bare_bool_flag_excludes_literal_bool():
    """A `Literal[True, False]` field carries `choices` and must go through
    type=+choices= like any other Literal -- it is NOT a bare store_true/
    BooleanOptionalAction flag, even though its declared type is `bool`-ish
    (A068 -- this is the exact disagreement duho.mcp independently re-derived
    and got wrong)."""
    builders = {b.name: b for b in IsPositionalArgs._getargs_()}
    assert builders["bare_bool"].is_bare_bool_flag is True
    assert builders["literal_bool"].is_bare_bool_flag is False


class _LiteralBoolArgs(Args):
    flag: "ty.Literal[True, False]" = False
    "Flag"
    ("--flag",)


def test_literal_bool_builds_and_roundtrips_end_to_end():
    # A naive store_true + choices= combination is an argparse TypeError at
    # build time; the field must go through type=+choices= instead.
    parser = _LiteralBoolArgs._parser_()
    assert parser is not None
    r = parse(_LiteralBoolArgs, ["--flag", "True"])
    assert r.flag is True


# --- ClassVar / Final are skipped, never a flag --------------------------


class _WithClassVar(Args):
    count: ty.ClassVar[int] = 0
    active: bool = False
    "Active"
    ("--active",)


def test_classvar_field_is_not_a_flag():
    help_text = _WithClassVar._parser_().format_help()
    assert "--count" not in help_text
    r = parse(_WithClassVar, [])
    assert r.count == 0
    assert r.active is False


# --- date/datetime factories -----------------------------------------------


class _WhenArgs(Args):
    when: datetime.date
    "When"
    ("--when",)


def test_date_factory():
    r = parse(_WhenArgs, ["--when", "2026-07-19"])
    assert r.when == datetime.date(2026, 7, 19)


def test_date_bad_value_is_argparse_error():
    with pytest.raises(SystemExit):
        parse(_WhenArgs, ["--when", "not-a-date"])


class _OptWhenArgs(Args):
    when: "ty.Optional[datetime.datetime]"
    "When"
    ("--when",)


def test_optional_datetime_factory():
    r = parse(_OptWhenArgs, ["--when", "2026-07-19T10:30:00"])
    assert r.when == datetime.datetime(2026, 7, 19, 10, 30, 0)


# --- declaration-shape build errors -----------------------------------------


def test_set_flags_container_is_build_error():
    class _SetFlags(Args):
        verbose: int = 0
        {"-v"}  # noqa: B018 - deliberate misuse under test

    with pytest.raises(ValueError):
        _SetFlags._parser_()


class _SuppressSecond(Args):
    hidden: Arg[int, NS(help="x"), argparse.SUPPRESS] = 0
    ("--hidden",)

    shown: int = 1
    "Shown"
    ("--shown",)


def test_suppress_honored_as_second_metadata_item():
    names = {b.name for b in _SuppressSecond._getargs_()}
    assert "hidden" not in names
    assert "shown" in names
