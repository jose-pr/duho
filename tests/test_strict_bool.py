"""Tests that `bool` is not called directly on CLI text wherever it appears as
a Literal member, a Union member, or a collection/dict element/value type.
Plain ``bool(text)`` is true for almost any non-empty string, so ``--flag
False`` would silently become ``True`` in every one of those shapes, though
a plain top-level ``bool`` field (``store_true`` / ``BooleanOptionalAction``)
and the env/config layer both parse the same text strictly.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import typing as ty

import duho
from duho import Arg, Args, NS


class _LitBoolArgs(Args):
    """A bare ``Literal[True, False]`` field."""

    flag: "ty.Literal[True, False]" = True
    ("--flag",)


def test_literal_bool_false_text_is_false():
    assert duho.parse(_LitBoolArgs, ["--flag", "False"]).flag is False


def test_literal_bool_zero_text_is_false():
    assert duho.parse(_LitBoolArgs, ["--flag", "0"]).flag is False


def test_literal_bool_true_text_is_true():
    assert duho.parse(_LitBoolArgs, ["--flag", "True"]).flag is True


class _LitMixedArgs(Args):
    """A mixed-type Literal with a bool member and a str member."""

    mode: "ty.Literal[True, 'auto']" = True
    ("--mode",)


def test_literal_mixed_bool_and_str_prefers_correct_member():
    # bool('auto') == True would match the bool literal before 'auto'
    # ever got a turn.
    inst = duho.parse(_LitMixedArgs, ["--mode", "auto"])
    assert inst.mode == "auto"
    assert inst.mode is not True


def test_literal_mixed_bool_member_still_works():
    assert duho.parse(_LitMixedArgs, ["--mode", "True"]).mode is True


class _UnionBoolStrArgs(Args):
    """A Union[bool, str] field."""

    v: "ty.Union[bool, str]" = False
    ("--v",)


def test_union_bool_str_false_text_is_false():
    assert duho.parse(_UnionBoolStrArgs, ["--v", "false"]).v is False


def test_union_bool_str_non_bool_text_is_str():
    # bool("hello") == True would match the bool member for anything.
    assert duho.parse(_UnionBoolStrArgs, ["--v", "hello"]).v == "hello"


class _UnionBoolIntArgs(Args):
    """A Union[bool, int] field."""

    v: "ty.Union[bool, int]" = False
    ("--v",)


def test_union_bool_int_non_bool_text_is_int():
    assert duho.parse(_UnionBoolIntArgs, ["--v", "42"]).v == 42


class _ListBoolArgs(Args):
    """A list[bool] field."""

    bs: "list[bool]"
    ("--bs",)


def test_list_bool_elements_parse_strictly():
    inst = duho.parse(_ListBoolArgs, ["--bs", "false", "--bs", "true"])
    assert inst.bs == [False, True]


class _DictBoolArgs(Args):
    """A dict[str, bool] field."""

    d: "dict[str, bool]"
    ("--d",)


def test_dict_bool_values_parse_strictly():
    inst = duho.parse(_DictBoolArgs, ["--d", "a=false", "--d", "b=0"])
    assert inst.d == {"a": False, "b": False}


class _EnvListBoolArgs(Args):
    """A list[bool] field layered from an env var, exercising convert_layered."""

    bs: "Arg[list[bool], NS(env='STRICTBOOL_BS')]" = []
    ("--bs",)


def test_env_list_bool_does_not_collapse_to_scalar(monkeypatch):
    # `self.type is bool` for a list[bool] field's ELEMENT factory (the raw,
    # un-ladder-resolved `bool`) would make convert_layered treat the WHOLE
    # env string as a single bool, collapsing the list to a scalar.
    monkeypatch.setenv("STRICTBOOL_BS", "false")
    inst = duho.parse(_EnvListBoolArgs, [])
    assert inst.bs == [False]
    assert inst.bs != False  # noqa: E712 -- exactly the regression, not a style nit
