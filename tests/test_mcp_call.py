"""Tests for ``duho.mcp.call_tool``: argv synthesis + return convention.

The return convention: ``None``/``0`` -> success with captured
stdout; a non-zero int -> ``isError: true`` + captured stdout + a trailing
``exit code: N`` line; a JSON-serialisable object/list -> passed through as
one text block holding its JSON dump.

Fixtures at module level (AST-based introspection needs a real source file).
"""

import enum
import os
import typing as ty

import pytest

from duho import Arg, Cli, Cmd, NS
from duho.mcp import InvalidArgumentsError, UnknownToolError, call_tool


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


class FailWithStderr(Cmd):
    """Exits non-zero after writing to BOTH stdout and stderr."""

    def __call__(self):
        import sys

        print("stdout line")
        print("stderr line", file=sys.stderr)
        return 5


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
    """Has a required field, called with none supplied -> a missing required
    property is caught by schema validation, before argparse ever runs."""

    required_field: str
    "no default"
    ("--required-field",)

    def __call__(self):  # pragma: no cover
        return 0


class Toolbox(Cli):
    """Root."""

    _subcommands_ = [
        Greet,
        Fail,
        FailWithStderr,
        Structured,
        ListReturn,
        Boom,
        BadArgs,
    ]


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


def test_enum_field_value_outside_the_published_enum_raises():
    # A value that is schema-VALID by JSON type (a string) but not one of
    # the published member names is a malformed request, caught before
    # dispatch rather than left to argparse's own "invalid choice" error.
    with pytest.raises(InvalidArgumentsError, match="PURPLE"):
        _call("Greet", {"name": "ada", "color": "PURPLE"})


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


def test_non_zero_return_includes_captured_stderr_too():
    # A plain non-zero RETURN (not `sys.exit`) used to drop captured stderr
    # entirely, unlike the SystemExit path (`_systemexit_result`), which
    # always included it.
    result = _call("FailWithStderr")
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "stdout line" in text
    assert "stderr line" in text
    assert text.strip().endswith("exit code: 5")


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


def test_unknown_tool_name_raises_unknown_tool_error():
    # An unknown tool name is a malformed REQUEST, not a broken command --
    # call_tool raises so `serve()` can map it to a JSON-RPC -32602 error
    # response instead of a tool result.
    with pytest.raises(UnknownToolError, match="unknown tool"):
        call_tool(Toolbox, "Toolbox.NoSuchTool", {})


def test_calling_a_namespace_node_raises_unknown_tool_error():
    # Toolbox itself always requires a subcommand -- it can never dispatch,
    # so it is refused the same way an unknown name is.
    with pytest.raises(UnknownToolError):
        call_tool(Toolbox, "Toolbox", {})


def test_calling_an_excluded_command_raises_unknown_tool_error():
    # A per-command `_mcp_ = False` (Plan 35 Phase 3) is refused the same
    # way an unknown name is -- no existence disclosed either way.
    class Secret(Cmd):
        """A leaf opted out of MCP."""

        _mcp_ = False

        def __call__(self):  # pragma: no cover
            return 0

    class Root(Cli):
        """Root."""

        _subcommands_ = [Secret]

    with pytest.raises(UnknownToolError, match="unknown tool"):
        call_tool(Root, "Root.Secret", {})


# --------------------------------------------------------------------------
# Exceptions during dispatch
# --------------------------------------------------------------------------


def test_raised_exception_is_error_not_a_crash():
    result = _call("Boom")
    assert result["isError"] is True
    assert "kaboom" in result["content"][0]["text"]


# --------------------------------------------------------------------------
# A missing required property is a malformed REQUEST, not a broken command --
# call_tool raises so `serve()` can map it to a JSON-RPC -32602 error
# response instead of a tool result (same classification as an unknown tool).
# --------------------------------------------------------------------------


def test_missing_required_argument_raises_invalid_arguments_error():
    with pytest.raises(InvalidArgumentsError, match="required_field"):
        _call("BadArgs", {})


# --------------------------------------------------------------------------
# Bool synthesis branches on the REAL registered action, not a guess --
# a real parser action can be store_true, store_false, or BooleanOptionalAction
# --------------------------------------------------------------------------


class BoolShapes(Cmd):
    """Exercises every bool-flag shape MCP has to synthesize argv for."""

    plain: bool = False
    "plain store_true"
    ("--plain",)

    use_cache: "Arg[bool, NS(action='store_false', flags=('--no-cache',))]" = True
    "explicit store_false under its OWN flag (no separate positive flag)"

    no_verify: bool = True
    "implicit store_false: True-default field whose own flag already reads as a negation"
    ("--no-verify",)

    always: bool = True
    "True-default -> BooleanOptionalAction (--always/--no-always)"
    ("--always",)

    env_flag: "Arg[bool, NS(env='BOOLSHAPES_ENV_FLAG')]" = False
    "env-layered bool -> BooleanOptionalAction even though its own default is False"
    ("--env-flag",)

    def __call__(self):
        return {
            "plain": self.plain,
            "use_cache": self.use_cache,
            "no_verify": self.no_verify,
            "always": self.always,
            "env_flag": self.env_flag,
        }


class BoolRoot(Cli):
    """Root."""

    _subcommands_ = [BoolShapes]


def _call_bool(arguments):
    result = call_tool(BoolRoot, "BoolRoot.BoolShapes", arguments)
    assert result.get("isError") is not True, result
    import json

    return json.loads(result["content"][0]["text"])


def test_explicit_store_false_field_can_be_set_both_ways():
    assert _call_bool({"use_cache": False})["use_cache"] is False
    assert _call_bool({"use_cache": True})["use_cache"] is True


def test_implicit_no_prefixed_store_false_field_can_be_set_both_ways():
    assert _call_bool({"no_verify": False})["no_verify"] is False
    assert _call_bool({"no_verify": True})["no_verify"] is True


def test_true_default_boolean_optional_field_can_be_set_both_ways():
    assert _call_bool({"always": False})["always"] is False
    assert _call_bool({"always": True})["always"] is True


def test_env_layered_bool_can_still_be_forced_false_over_mcp():
    os.environ["BOOLSHAPES_ENV_FLAG"] = "1"
    try:
        assert _call_bool({})["env_flag"] is True  # picked up from the env
        assert _call_bool({"env_flag": False})["env_flag"] is False
        assert _call_bool({"env_flag": True})["env_flag"] is True
    finally:
        del os.environ["BOOLSHAPES_ENV_FLAG"]


# --------------------------------------------------------------------------
# A dict field with a CUSTOM whole-string type= override (LoggingArgs'
# `loglevels`, the only one in duho) must not use the generic KEY=VALUE form
# --------------------------------------------------------------------------


def test_loglevels_dict_field_uses_its_own_name_colon_level_grammar():
    from duho import LoggingArgs

    class Works(LoggingArgs, Cmd):
        """Reports its own resolved loglevels."""

        def __call__(self):
            return {"loglevels": self.loglevels}

    class LogToolbox(Cli):
        """Root."""

        _subcommands_ = [Works]

    result = call_tool(
        LogToolbox, "LogToolbox.Works", {"loglevels": {"synapp": "DEBUG"}}
    )
    assert result.get("isError") is not True, result
    import json

    payload = json.loads(result["content"][0]["text"])
    import logging

    assert payload["loglevels"]["synapp"] == logging.DEBUG


# --------------------------------------------------------------------------
# duho's own idempotent stderr log handler must be rebound to EACH call's
# capture, not stuck on the first call's now-dead one
# --------------------------------------------------------------------------


def test_logging_handler_is_rebound_to_each_calls_own_capture():
    import logging

    from duho import LoggingArgs

    class Loud(LoggingArgs, Cmd):
        """Logs a warning every call."""

        def __call__(self):
            self._logger_.warning("warn-from-call")
            print("done")
            return 0

    class LoudToolbox(Cli):
        """Root."""

        _subcommands_ = [Loud]

    # duho only installs its own stderr handler when the root logger has no
    # OTHER handler of its own (restored 0.5.4 guard) -- pytest's own logging
    # plugin always leaves capture handlers on the root logger, which would
    # otherwise make duho skip installing its handler here and this test
    # would be exercising nothing. Clear them for this real-standalone-server
    # simulation (`_isolate_logging_globals` in conftest.py restores root's
    # actual handlers afterwards).
    root_logger = logging.getLogger()
    root_logger.handlers[:] = []

    call_tool(LoudToolbox, "LoudToolbox.Loud", {"verbose": 1})
    call_tool(LoudToolbox, "LoudToolbox.Loud", {"verbose": 1})

    from duho.logging import _STDERR_HANDLER_TAG

    tagged = [
        h
        for h in root_logger.handlers
        if getattr(h, _STDERR_HANDLER_TAG, False) and hasattr(h, "stream")
    ]
    assert tagged, "expected duho's own stderr handler to be installed"
    # Once every call returns, the handler must be pointed at the SERVER's
    # real (idle) stderr again -- never left on a dead StringIO from a call
    # that already finished, which used to swallow later server-side errors.
    import io

    for handler in tagged:
        assert not isinstance(handler.stream, io.StringIO)


# --------------------------------------------------------------------------
# A negative counting-flag value is rejected before dispatch, not left to
# synthesize a nonsensical argv
# --------------------------------------------------------------------------


def test_negative_count_raises_invalid_arguments_error():
    from duho import LoggingArgs

    class Loud(LoggingArgs, Cmd):
        """Exposes -v/-q counting flags."""

        def __call__(self):  # pragma: no cover
            return 0

    class LoudToolbox(Cli):
        """Root."""

        _subcommands_ = [Loud]

    with pytest.raises(InvalidArgumentsError, match="verbose"):
        call_tool(LoudToolbox, "LoudToolbox.Loud", {"verbose": -1})


# --------------------------------------------------------------------------
# Passthrough over MCP: a dedicated "--" array property, appended as a
# literal `--` token at the very end of the synthesized argv, restores what
# a positional value embedding a literal "--" used to do before that became
# unsafe to allow (see the module docstring's "Documented v1 limitations").
# --------------------------------------------------------------------------


class Passer(Cmd):
    """Forwards trailing args."""

    def __call__(self):
        return {"passthrough": self._passthrough_}


class PasserToolbox(Cli):
    """Root."""

    _subcommands_ = [Passer]


def test_passthrough_key_reaches_the_command_as_passthrough():
    result = call_tool(
        PasserToolbox, "PasserToolbox.Passer", {"--": ["-k", "test_foo", "-x"]}
    )
    assert result.get("isError") is not True
    import json

    payload = json.loads(result["content"][0]["text"])
    assert payload["passthrough"] == ["-k", "test_foo", "-x"]


def test_omitted_passthrough_key_is_an_empty_list():
    result = call_tool(PasserToolbox, "PasserToolbox.Passer", {})
    import json

    assert json.loads(result["content"][0]["text"])["passthrough"] == []


def test_passthrough_key_is_published_on_every_tool_and_never_required():
    from duho.mcp import describe_tools

    for tool in describe_tools(PasserToolbox):
        schema = tool["inputSchema"]
        assert schema["properties"]["--"]["type"] == "array"
        assert "--" not in schema["required"]


def test_passthrough_items_must_be_strings():
    with pytest.raises(InvalidArgumentsError):
        call_tool(PasserToolbox, "PasserToolbox.Passer", {"--": [1, 2]})
