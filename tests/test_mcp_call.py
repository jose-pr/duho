"""Tests for ``duho.mcp.call_tool``: argv synthesis + return convention.

Decision 4's return convention: ``None``/``0`` -> success with captured
stdout; a non-zero int -> ``isError: true`` + captured stdout + a trailing
``exit code: N`` line; a JSON-serialisable object/list -> passed through as
one text block holding its JSON dump.

Fixtures at module level (AST-based introspection needs a real source file).
"""

import enum
import typing as ty

import pytest

from duho import Arg, Cli, Cmd, NS
from duho.mcp import call_tool


class Color(enum.Enum):
    RED = 1
    GREEN = 2


class Greet(Cmd):
    """Print a greeting."""

    name: str
    "Who to greet"
    ("--name",)

    times: int = 1
    "How many times"
    ("--times",)

    shout: bool = False
    "Shout it"
    ("--shout",)

    tags: "ty.List[str]"
    "Repeatable tag"
    ("--tag",)

    color: Color = Color.RED
    "A color"
    ("--color",)

    target: str = "."
    "Positional target"
    ("target",)

    def __call__(self):
        text = self.name.upper() if self.shout else self.name
        for _ in range(self.times):
            print("hello", text, self.target, self.color.name, list(self.tags))
        return 0


class Fail(Cmd):
    """Always exits non-zero."""

    def __call__(self):
        print("about to fail")
        return 3


class Structured(Cmd):
    """Returns a JSON-serialisable object."""

    def __call__(self):
        return {"ok": True, "items": [1, 2, 3]}


class ListReturn(Cmd):
    """Returns a plain list."""

    def __call__(self):
        return ["a", "b"]


class Boom(Cmd):
    """Raises an exception."""

    def __call__(self):
        raise RuntimeError("kaboom")


class BadArgs(Cmd):
    """Has a required field, called with none supplied -> argparse usage error."""

    required_field: str
    "no default"
    ("--required-field",)

    def __call__(self):  # pragma: no cover
        return 0


class Toolbox(Cli):
    """Root."""

    _subcommands_ = [Greet, Fail, Structured, ListReturn, Boom, BadArgs]


def _call(name, arguments=None):
    return call_tool(Toolbox, "Toolbox." + name, arguments or {})


# --------------------------------------------------------------------------
# Successful call -> stdout captured
# --------------------------------------------------------------------------


def test_successful_call_returns_captured_stdout():
    result = _call("Greet", {"name": "ada", "target": "world"})
    assert result.get("isError") is not True
    text = result["content"][0]["text"]
    assert text == "hello ada world RED []\n"


def test_bool_true_emits_bare_flag():
    result = _call("Greet", {"name": "ada", "shout": True})
    text = result["content"][0]["text"]
    assert "ADA" in text


def test_bool_false_is_omitted_and_still_false():
    result = _call("Greet", {"name": "ada", "shout": False})
    text = result["content"][0]["text"]
    assert "ada" in text and "ADA" not in text


def test_repeatable_field_synthesizes_repeated_flags():
    result = _call("Greet", {"name": "ada", "tags": ["x", "y"]})
    text = result["content"][0]["text"]
    assert "['x', 'y']" in text


def test_enum_field_synthesizes_member_name():
    result = _call("Greet", {"name": "ada", "color": "GREEN"})
    text = result["content"][0]["text"]
    assert "GREEN" in text


def test_int_field_synthesized_and_repeats_output():
    result = _call("Greet", {"name": "ada", "times": 2})
    text = result["content"][0]["text"]
    assert text.count("hello ada") == 2


# --------------------------------------------------------------------------
# Non-zero exit -> isError
# --------------------------------------------------------------------------


def test_non_zero_return_is_error_with_exit_code_line():
    result = _call("Fail")
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "about to fail" in text
    assert text.strip().endswith("exit code: 3")


# --------------------------------------------------------------------------
# Structured object/list return -> JSON passthrough
# --------------------------------------------------------------------------


def test_dict_return_is_passed_through_as_json():
    import json

    result = _call("Structured")
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"ok": True, "items": [1, 2, 3]}


def test_list_return_is_passed_through_as_json():
    import json

    result = _call("ListReturn")
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload == ["a", "b"]


# --------------------------------------------------------------------------
# Unknown tool
# --------------------------------------------------------------------------


def test_unknown_tool_name_is_error():
    result = call_tool(Toolbox, "Toolbox.NoSuchTool", {})
    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"]


# --------------------------------------------------------------------------
# Exceptions during dispatch
# --------------------------------------------------------------------------


def test_raised_exception_is_error_not_a_crash():
    result = _call("Boom")
    assert result["isError"] is True
    assert "kaboom" in result["content"][0]["text"]


# --------------------------------------------------------------------------
# Argument-parsing failure (SystemExit from argparse)
# --------------------------------------------------------------------------


def test_missing_required_argument_is_error():
    result = _call("BadArgs", {})
    assert result["isError"] is True
