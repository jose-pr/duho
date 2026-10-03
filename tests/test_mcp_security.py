"""Regression tests for duho's MCP security hardening.

Covers argv-injection safety, env/config layering, dispatch through the root
parser (root globals reachable from a nested tool), JSON-RPC envelope
hardening, protocol negotiation, and the stdio isolation that must be proven
against a REAL ``python -m duho.mcp <app>`` subprocess (a StringIO-only test
cannot catch a stray write to the real file descriptor 1, or the process
default text encoding).

Fixtures at module level: AST-based flags/docstring introspection needs a
real source file (same convention as every other ``test_mcp_*.py``).
"""

import io
import json
import os
import subprocess
import sys
import typing as ty

import pytest

from duho import Arg, Cli, Cmd, LoggingArgs, NS
from duho.mcp import (
    InvalidArgumentsError,
    _tree_for,
    call_tool,
    describe_tools,
    serve,
)

# --------------------------------------------------------------------------
# Argv-injection safety (tool arguments are untrusted, LLM-controlled input)
# --------------------------------------------------------------------------


class Rm(Cmd):
    """Remove files."""

    files: "list"
    "Files to remove"
    ("files",)

    force: bool = False
    "Force removal"
    ("--force",)

    def __call__(self):
        return {
            "files": self.files,
            "force": self.force,
            "passthrough": self._passthrough_,
        }


class Note(Cmd):
    """Write a note."""

    text: str
    "Body"
    ("text",)

    title: str = ""
    "Title"
    ("--title",)

    def __call__(self):
        return {"text": self.text, "title": self.title}


class LabelSet(Cmd):
    """Set labels."""

    labels: "dict"
    "key=value labels"
    ("--label",)

    def __call__(self):
        return dict(self.labels)


class InjectRoot(Cli):
    """Root."""

    _subcommands_ = [Rm, Note, LabelSet]


def test_positional_value_that_looks_like_a_flag_is_refused():
    # The value is schema-valid (a list of strings) -- the danger is purely
    # in how argv synthesis would encode it, so this is discovered while
    # building argv, not by `_validate_arguments` alone. It is still a
    # malformed-REQUEST problem (a value that cannot be safely encoded at
    # all), so `call_tool` raises `InvalidArgumentsError` rather than
    # returning a tool result.
    with pytest.raises(InvalidArgumentsError):
        call_tool(InjectRoot, "inject-root.rm", {"files": ["a", "--force"]})
    result = call_tool(InjectRoot, "inject-root.rm", {"files": ["a"], "force": True})
    assert result.get("isError") is not True


def test_positional_double_dash_does_not_leak_into_passthrough():
    with pytest.raises(InvalidArgumentsError):
        call_tool(InjectRoot, "inject-root.rm", {"files": ["a", "--"], "force": False})


def test_option_value_starting_with_dash_is_not_reparsed_as_a_flag():
    result = call_tool(InjectRoot, "inject-root.note", {"text": "a", "title": "-x"})
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload["title"] == "-x"


def test_option_value_equal_to_double_dash_is_refused():
    with pytest.raises(InvalidArgumentsError):
        call_tool(InjectRoot, "inject-root.note", {"text": "a", "title": "--"})


def test_dict_field_key_and_value_survive_verbatim():
    result = call_tool(
        InjectRoot, "inject-root.label-set", {"labels": {"env": "prod-1", "tier": "-x"}}
    )
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"env": "prod-1", "tier": "-x"}


# --------------------------------------------------------------------------
# An unbounded list/dict argument is refused before it is ever synthesized
# into argv or dispatched -- an LLM-controlled collection has no other
# bound, and a huge one can stall the single-threaded stdio server.
# --------------------------------------------------------------------------


def test_oversized_list_argument_is_refused_before_dispatch():
    with pytest.raises(InvalidArgumentsError, match="maxItems"):
        call_tool(InjectRoot, "inject-root.rm", {"files": ["a"] * 1001})


def test_oversized_dict_argument_is_refused_before_dispatch():
    with pytest.raises(InvalidArgumentsError, match="maxProperties"):
        call_tool(
            InjectRoot,
            "inject-root.label-set",
            {"labels": {str(i): "v" for i in range(1001)}},
        )


def test_list_argument_at_exactly_the_cap_is_accepted():
    result = call_tool(InjectRoot, "inject-root.rm", {"files": ["a"] * 1000})
    assert result.get("isError") is not True


# --------------------------------------------------------------------------
# Subcommand dispatch-identity hijack: an ancestor's own optional/variadic
# positional must never let a client-controlled value select a DIFFERENT
# sibling than the one the tool name asked for.
# --------------------------------------------------------------------------


class HijackLeaf(Cmd):
    """Harmless leaf that must be the ONLY thing this tool name can run."""

    name: str
    "a value"
    ("name",)

    def __call__(self):
        return {"ran": "HijackLeaf", "name": self.name}


class HijackDanger(Cmd):
    """A sibling that must never run unless explicitly named."""

    def __call__(self):
        return {"ran": "HijackDanger"}


class HijackOptRoot(Cli):
    """Root with an OPTIONAL positional ahead of its subcommands."""

    path: str = "."
    "an optional positional"
    ("path",)

    _subcommands_ = [HijackLeaf, HijackDanger]


class HijackTagsRoot(Cli):
    """Root with a VARIADIC positional ahead of its subcommands."""

    tags: "list" = []
    "a variadic positional"
    ("tags",)

    _subcommands_ = [HijackLeaf, HijackDanger]


class NestedDangerLeaf(Cmd):
    """A NESTED leaf deliberately sharing its name with a top-level sibling
    ('HijackDanger'), to prove the collision guard is scoped to one level's
    own choices, not confused by the same name living elsewhere."""

    _parsername_ = "HijackDanger"

    def __call__(self):
        return {"ran": "NestedDangerLeaf"}


class HijackMid(Cli):
    """A namespace sibling with its own nested subcommand named the same as
    a DIFFERENT top-level sibling."""

    _subcommands_ = [NestedDangerLeaf]


class HijackCollisionRoot(Cli):
    """Root with an optional positional, one sibling of which ALSO has a
    nested subcommand sharing that same sibling's name."""

    path: str = "."
    "an optional positional"
    ("path",)

    _subcommands_ = [HijackLeaf, HijackDanger, HijackMid]


def test_optional_ancestor_positional_cannot_hijack_dispatch_to_a_sibling():
    # An omitted optional positional
    # ("path") used to absorb the "HijackLeaf" separator token, shifting
    # "HijackDanger" (the client's OWN "name" value) into the root's
    # subparsers slot and actually running HijackDanger instead. Caught here
    # by the ancestor-sibling-name value guard, which raises
    # `InvalidArgumentsError` outright (before parsing) rather than letting
    # it reach the dispatch-identity check.
    with pytest.raises(InvalidArgumentsError):
        call_tool(
            HijackOptRoot, "hijack-opt-root.hijack-leaf", {"name": "hijack-danger"}
        )


def test_variadic_ancestor_positional_cannot_hijack_dispatch_to_a_sibling():
    # A variadic ("tags") positional
    # greedily ate the separator token the same way, dispatching
    # HijackDanger (with ITS OWN default field) instead of HijackLeaf.
    with pytest.raises(InvalidArgumentsError):
        call_tool(
            HijackTagsRoot, "hijack-tags-root.hijack-leaf", {"name": "hijack-danger"}
        )


def test_ancestor_positional_explicitly_set_to_a_sibling_name_is_refused():
    # The client explicitly sets the ancestor's own optional positional to a
    # value that collides with one of ITS OWN sibling names -- refused
    # outright, before parsing, even though the same string ALSO happens to
    # be a nested subcommand name elsewhere in the tree (HijackMid's own
    # child), proving the guard is scoped to this one level's choices.
    with pytest.raises(InvalidArgumentsError):
        call_tool(
            HijackCollisionRoot,
            "hijack-collision-root.hijack-leaf",
            {"path": "hijack-danger", "name": "x"},
        )


def test_normal_dispatch_through_an_optional_ancestor_positional_still_works():
    # The fix must not break the ordinary, non-adversarial case: supplying a
    # harmless value for the ancestor's own optional positional still
    # dispatches the requested leaf correctly.
    result = call_tool(
        HijackOptRoot,
        "hijack-opt-root.hijack-leaf",
        {"path": "somewhere", "name": "ok"},
    )
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"ran": "HijackLeaf", "name": "ok"}


# --------------------------------------------------------------------------
# The SAME class reachable from two different places in the tree defeats a
# `type(instance) is node.cls` check alone -- it is true either way. Only a
# dispatch-path marker recorded on the actual subparser reached (not just
# the requested one) can tell a hijack apart from a legitimate dispatch,
# including when NO value collides with any subcommand name at all (the
# hijack comes purely from an omitted optional positional).
# --------------------------------------------------------------------------


class SharedLeaf(Cmd):
    """Registered under both a nested group AND directly at the root."""

    def __call__(self):
        return {"ran": "SharedLeaf"}


class SharedGroup(Cmd):
    """A namespace node nesting the shared leaf."""

    gflag: str = "group-default"
    "g"
    ("--gflag",)

    _subcommands_ = [SharedLeaf]


class SharedClassRoot(Cli):
    """Root with an optional positional ahead of subcommands, one of which
    nests the SAME leaf class the root ALSO exposes directly."""

    path: str = "."
    "an optional positional"
    ("path",)

    _subcommands_ = [SharedGroup, SharedLeaf]


def test_shared_class_reached_via_the_wrong_nesting_is_refused():
    # No value collides with any subcommand name here (`arguments` is
    # empty) -- omitting "path" lets the literal "SharedGroup" token this
    # module inserts get swallowed by the root's own optional positional,
    # shifting the ROOT'S OWN direct "SharedLeaf" subcommand into the slot
    # instead of descending into SharedGroup first. `type(instance) is
    # node.cls` alone would pass (SharedLeaf either way); only the
    # dispatch-path marker distinguishes "ran via SharedGroup" from "ran
    # directly at the root".
    result = call_tool(
        SharedClassRoot, "shared-class-root.shared-group.shared-leaf", {}
    )
    assert result.get("isError") is True
    assert "did not resolve to the requested command" in result["content"][0]["text"]


def test_shared_class_reached_via_the_right_nesting_still_works():
    # The fix must not break the legitimate path to the SAME shared class.
    result = call_tool(
        SharedClassRoot,
        "shared-class-root.shared-group.shared-leaf",
        {"gflag": "x"},
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"ran": "SharedLeaf"}


# --------------------------------------------------------------------------
# A field name declared at several ancestor levels binds ONLY at the
# deepest one -- a shared JSON `arguments` dict must never ALSO flip an
# unrelated ancestor's same-named flag.
# --------------------------------------------------------------------------


class ShadowChild(Cmd):
    """Child whose OWN 'force' is a string, unrelated to the root's bool."""

    force: str = ""
    "child's own force (string)"
    ("--mode",)

    def __call__(self):
        return {"force_child": self.force}


class ShadowRoot(Cli):
    """Root whose 'force' is a bool, shadowed by the child's own field."""

    force: bool = False
    "root's own force (bool)"
    ("--force",)

    _subcommands_ = [ShadowChild]


def test_shadowed_ancestor_bool_is_not_flipped_by_the_childs_own_field():
    result = call_tool(ShadowRoot, "shadow-root.shadow-child", {"force": "no"})
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    # The child's own string field received the value...
    assert payload == {"force_child": "no"}


def test_synthesize_argv_skip_omits_a_shadowed_fields_own_level_entirely():
    # The exact mechanism `call_tool` uses: for the chain [ShadowRoot,
    # ShadowChild], "force" is owned by the DEEPER level (ShadowChild), so
    # the ROOT level's own synthesis is called with "force" in `skip` --
    # without this, a truthy-but-falsy-MEANING string like "no" would still
    # set the unrelated root bool (`if value: ...` sees any non-empty str as
    # truthy), which is exactly the leak this guards against.
    from duho.mcp import _synthesize_argv

    _, nodes = _tree_for(ShadowRoot)
    root_node = nodes["shadow-root"]
    root_argv = _synthesize_argv(
        ShadowRoot, {"force": "no"}, root_node.parser, skip=frozenset({"force"})
    )
    assert root_argv == []


# --------------------------------------------------------------------------
# An unbounded counting flag must not be able to stall the server
# --------------------------------------------------------------------------


class Loud(LoggingArgs, Cmd):
    """A command exposing LoggingArgs' -v/-q counting flags."""

    def __call__(self):
        return {"verbose": self.verbose}


class LoudRoot(Cli):
    """Root."""

    _subcommands_ = [Loud]


def test_count_schema_publishes_a_maximum():
    tools = {t["name"]: t for t in describe_tools(LoudRoot)}
    assert (
        tools["loud-root.loud"]["inputSchema"]["properties"]["verbose"]["maximum"] == 10
    )


def test_count_value_over_the_maximum_is_rejected_not_synthesized():
    with pytest.raises(InvalidArgumentsError):
        call_tool(LoudRoot, "loud-root.loud", {"verbose": 1000000})


def test_count_value_at_the_maximum_still_dispatches():
    result = call_tool(LoudRoot, "loud-root.loud", {"verbose": 10})
    assert result.get("isError") is not True


# --------------------------------------------------------------------------
# Bool negation must use a LONG flag (a short-flag-last tuple is common)
# --------------------------------------------------------------------------


class LongFirst(Cmd):
    """Color output."""

    color: bool = True
    "Colorize"
    ("--color", "-c")

    def __call__(self):
        return {"color": self.color}


class ShortOnly(Cmd):
    """Color output, short flag only."""

    color: bool = True
    "Colorize"
    ("-c",)

    def __call__(self):
        return {"color": self.color}


class BoolRoot(Cli):
    """Root."""

    _subcommands_ = [LongFirst, ShortOnly]


def test_false_for_true_default_bool_with_long_flag_last_is_honored():
    result = call_tool(BoolRoot, "bool-root.long-first", {"color": False})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"color": False}


def test_false_for_true_default_bool_with_no_long_flag_is_a_clear_error():
    result = call_tool(BoolRoot, "bool-root.short-only", {"color": False})
    assert result["isError"] is True
    assert "long flag" in result["content"][0]["text"]


# --------------------------------------------------------------------------
# Argv synthesis honors the field's EFFECTIVE action (count, Literal[bool])
# --------------------------------------------------------------------------


class Verbosity(LoggingArgs, Cmd):
    """Reports its own effective log level."""

    def __call__(self):
        return {"level": self._logger_.getEffectiveLevel()}


class LitBool(Cmd):
    """A Literal[True, False] field (NOT a bare bool flag)."""

    strict: "ty.Literal[True, False]" = False
    "Strict mode"
    ("--strict",)

    def __call__(self):
        return {"strict": self.strict}


class ActionRoot(Cli):
    """Root."""

    _subcommands_ = [Verbosity, LitBool]


def test_count_action_field_repeats_the_flag():
    import logging

    result = call_tool(ActionRoot, "action-root.verbosity", {"verbose": 2})
    assert result.get("isError") is not True
    payload = json.loads(result["content"][0]["text"])
    assert payload["level"] <= logging.INFO  # -vv makes it more verbose than default


def test_count_action_field_of_zero_emits_nothing_and_still_succeeds():
    result = call_tool(ActionRoot, "action-root.verbosity", {"verbose": 0})
    assert result.get("isError") is not True


def test_literal_true_false_field_is_not_forced_through_bare_flag_synthesis():
    result = call_tool(ActionRoot, "action-root.lit-bool", {"strict": True})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"strict": True}


# --------------------------------------------------------------------------
# Arguments are validated against the tool's own inputSchema
# --------------------------------------------------------------------------


def test_unknown_argument_key_is_rejected():
    with pytest.raises(InvalidArgumentsError, match="unknown argument"):
        call_tool(InjectRoot, "inject-root.note", {"text": "a", "dry-run": True})


def test_wrong_json_type_for_a_boolean_field_is_rejected():
    with pytest.raises(InvalidArgumentsError):
        call_tool(BoolRoot, "bool-root.long-first", {"color": "false"})


def test_wrong_json_type_for_a_dict_field_is_rejected():
    with pytest.raises(InvalidArgumentsError):
        call_tool(InjectRoot, "inject-root.label-set", {"labels": "a=b"})


def test_null_argument_means_field_omitted_not_the_string_none():
    class Owner(Cmd):
        """Has an owner field."""

        owner: str = "root"
        "Owner"
        ("--owner",)

        def __call__(self):
            return {"owner": self.owner}

    result = call_tool(Owner, "owner", {"owner": None})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"])["owner"] == "root"


# --------------------------------------------------------------------------
# Dispatch goes through the shared root parser: nested tools see root globals
# --------------------------------------------------------------------------


class ProfileRoot(Cli):
    """Root with a global field."""

    profile: str = "default"
    "Active profile"
    ("--profile",)

    _subcommands_ = [Note]


def test_nested_tool_schema_includes_ancestor_fields():
    tools = {t["name"]: t for t in describe_tools(ProfileRoot)}
    note = tools["profile-root.note"]
    assert "profile" in note["inputSchema"]["properties"]
    assert "text" in note["inputSchema"]["properties"]


def test_nested_tool_call_can_supply_a_root_global():
    result = call_tool(
        ProfileRoot, "profile-root.note", {"text": "hi", "profile": "staging"}
    )
    assert result.get("isError") is not True


class ReadsRootProfile(Cmd):
    """Reads the root's own field off the shared parsed instance."""

    def __call__(self):
        return {"profile": self.profile}


class ProfileReaderRoot(Cli):
    """Root with a global field a leaf reads back."""

    profile: str = "default"
    "Active profile"
    ("--profile",)

    _subcommands_ = [ReadsRootProfile]


def test_leaf_command_can_read_a_root_field_without_raising():
    result = call_tool(
        ProfileReaderRoot, "profile-reader-root.reads-root-profile", {"profile": "prod"}
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"profile": "prod"}


# --------------------------------------------------------------------------
# Env/config layering reaches an MCP-dispatched command like duho.main
# --------------------------------------------------------------------------


class EnvTool(Cmd):
    """A field satisfiable from the process environment."""

    token: "Arg[str, NS(env='DUHO_MCP_TEST_TOKEN')]"
    "Secret token"
    ("--token",)

    def __call__(self):
        return {"token": self.token}


def test_env_bound_required_field_is_satisfied_without_an_explicit_argument(
    monkeypatch,
):
    monkeypatch.setenv("DUHO_MCP_TEST_TOKEN", "from-env")
    result = call_tool(EnvTool, "env-tool", {})
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"token": "from-env"}


def test_env_bound_field_is_dropped_from_required_when_env_is_set(monkeypatch):
    monkeypatch.setenv("DUHO_MCP_TEST_TOKEN", "from-env")
    tools = {t["name"]: t for t in describe_tools(EnvTool)}
    assert "token" not in tools["env-tool"]["inputSchema"]["required"]


class ConfigTool(Cmd):
    """A field satisfiable from the class's _config_ file."""

    _config_ = None

    region: str = "class-default"
    "Region"
    ("--region",)

    def __call__(self):
        return {"region": self.region}


@pytest.mark.requires_toml
def test_config_bound_field_is_satisfied_without_an_explicit_argument(tmp_path):
    from duho.mcp import _TREE_CACHE

    cfg = tmp_path / "config_tool.toml"
    cfg.write_text('region = "from-config"\n', encoding="utf-8")
    ConfigTool._config_ = str(cfg)
    _TREE_CACHE.pop(ConfigTool, None)
    try:
        result = call_tool(ConfigTool, "config-tool", {})
    finally:
        ConfigTool._config_ = None
        _TREE_CACHE.pop(ConfigTool, None)
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]) == {"region": "from-config"}


# --------------------------------------------------------------------------
# A field's own type= factory can raise ANYTHING -- an env-bound secret must
# never surface in the MCP result text, whichever exception the factory
# chooses to raise.
# --------------------------------------------------------------------------

_LEAK_REGIONS = {"us": "us-east-1"}


class KeyErrorFactoryTool(Cmd):
    """A field whose ``type=`` is a mapping lookup -- raises ``KeyError``."""

    region: "Arg[str, NS(env='DUHO_MCP_TEST_KEYERROR_REGION', type=_LEAK_REGIONS.__getitem__)]" = ("us")
    "Region"
    ("--region",)

    def __call__(self):
        return {"region": self.region}


def _leak_parse_token(value: str) -> str:
    import argparse

    if not value.startswith("tok_"):
        raise argparse.ArgumentTypeError("%r is not a valid token" % value)
    return value


class ArgumentTypeErrorFactoryTool(Cmd):
    """A ``type=`` factory raising ``argparse.ArgumentTypeError`` -- NOT a
    ``ValueError`` subclass."""

    token: (
        "Arg[str, NS(env='DUHO_MCP_TEST_ARGTYPEERROR_TOKEN', type=_leak_parse_token)]"
    ) = "tok_default"
    "API token"
    ("--token",)

    def __call__(self):
        return {"token": self.token}


def test_mcp_result_never_leaks_a_secret_from_a_keyerror_factory(monkeypatch):
    # Before the fix, the
    # deferred KeyError propagated uncaught out of `root_parser.parse_args`,
    # was caught only by `call_tool`'s own generic `except Exception`, and
    # THAT handler formatted the raw exception (secret included) straight
    # into the `isError` text.
    monkeypatch.setenv("DUHO_MCP_TEST_KEYERROR_REGION", "hunter2-PASSWORD")
    result = call_tool(KeyErrorFactoryTool, "key-error-factory-tool", {})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "hunter2-PASSWORD" not in text
    assert "Traceback" not in text
    assert "environment variable 'DUHO_MCP_TEST_KEYERROR_REGION'" in text


def test_mcp_result_never_leaks_a_secret_from_an_argumenttypeerror_factory(
    monkeypatch,
):
    # Same secret-leak guard as above, with an ArgumentTypeError-raising
    # factory instead of KeyError.
    monkeypatch.setenv("DUHO_MCP_TEST_ARGTYPEERROR_TOKEN", "S3CRET-ENV-VALUE")
    result = call_tool(
        ArgumentTypeErrorFactoryTool, "argument-type-error-factory-tool", {}
    )
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "S3CRET-ENV-VALUE" not in text
    assert "Traceback" not in text
    assert "environment variable 'DUHO_MCP_TEST_ARGTYPEERROR_TOKEN'" in text


# --------------------------------------------------------------------------
# A command's own sys.exit()/SystemExit never kills the server
# --------------------------------------------------------------------------


class ExitsInt(Cmd):
    """Exits with an integer code."""

    def __call__(self):
        print("bye")
        sys.exit(4)


class ExitsZero(Cmd):
    """Exits cleanly."""

    def __call__(self):
        print("done")
        sys.exit(0)


class ExitsMessage(Cmd):
    """Exits with a message."""

    def __call__(self):
        raise SystemExit("config file missing")


class ExitRoot(Cli):
    """Root."""

    _subcommands_ = [ExitsInt, ExitsZero, ExitsMessage]


def test_command_sys_exit_int_becomes_an_error_result_not_a_crash():
    result = call_tool(ExitRoot, "exit-root.exits-int", {})
    assert result["isError"] is True
    assert "bye" in result["content"][0]["text"]
    assert result["content"][0]["text"].strip().endswith("exit code: 4")


def test_command_sys_exit_zero_is_a_success_result():
    result = call_tool(ExitRoot, "exit-root.exits-zero", {})
    assert result.get("isError") is not True
    assert "done" in result["content"][0]["text"]


def test_command_system_exit_with_message_is_an_error_result():
    result = call_tool(ExitRoot, "exit-root.exits-message", {})
    assert result["isError"] is True
    assert "config file missing" in result["content"][0]["text"]


def test_serve_survives_a_command_that_calls_sys_exit():
    stdin = io.StringIO(
        "\n".join(
            json.dumps(r)
            for r in (
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "exit-root.exits-int", "arguments": {}},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
        )
        + "\n"
    )
    stdout = io.StringIO()
    rc = serve(ExitRoot, stdin=stdin, stdout=stdout)
    assert rc == 0
    responses = [
        json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()
    ]
    assert [r["id"] for r in responses] == [1, 2]


# --------------------------------------------------------------------------
# JSON-RPC envelope hardening: malformed-but-valid JSON must never crash serve()
# --------------------------------------------------------------------------


class Echo(Cmd):
    """Echoes a value."""

    def __call__(self):
        print("ok")
        return 0


class EchoRoot(Cli):
    """Root."""

    _subcommands_ = [Echo]


def _serve_lines(*raw_lines):
    stdin = io.StringIO("\n".join(raw_lines) + "\n")
    stdout = io.StringIO()
    rc = serve(EchoRoot, stdin=stdin, stdout=stdout)
    responses = [
        json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()
    ]
    return rc, responses


@pytest.mark.parametrize("raw_line", ["5", '"hello"', "null"])
def test_non_object_json_line_gets_invalid_request_not_a_crash(raw_line):
    ok_request = json.dumps(
        {"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}}
    )
    rc, responses = _serve_lines(raw_line, ok_request)
    assert rc == 0
    # The malformed line never answers with a result, and the well-formed
    # request right after it still gets served.
    assert any(r.get("id") == 99 and "result" in r for r in responses)


def test_non_object_params_gets_invalid_params():
    rc, responses = _serve_lines(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]})
    )
    assert responses[0]["error"]["code"] == -32602


# --------------------------------------------------------------------------
# JSON-RPC 2.0 batch requests (a line whose top-level JSON value is an array)
# --------------------------------------------------------------------------


def test_empty_batch_array_gets_a_single_invalid_request():
    ok_request = json.dumps(
        {"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}}
    )
    rc, responses = _serve_lines("[]", ok_request)
    assert rc == 0
    assert len(responses) == 2
    assert responses[0]["error"]["code"] == -32600
    assert responses[1]["id"] == 99 and "result" in responses[1]


def test_batch_of_malformed_items_returns_one_batch_of_errors():
    # Every element (a bare int) is itself an invalid request -- the whole
    # line still comes back as ONE combined array of error responses (JSON-
    # RPC 2.0's own batch convention), not a single flat -32600 that used to
    # swallow the fact this was a batch at all.
    rc, responses = _serve_lines("[1, 2]")
    assert rc == 0
    assert len(responses) == 1
    batch = responses[0]
    assert isinstance(batch, list) and len(batch) == 2
    assert all(item["error"]["code"] == -32600 for item in batch)


def test_batch_dispatches_each_request_and_collects_the_responses():
    batch = json.dumps(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "echo-root.echo", "arguments": {}},
            },
        ]
    )
    rc, responses = _serve_lines(batch)
    assert rc == 0
    assert len(responses) == 1
    items = responses[0]
    # The notification produced NOTHING in the batch reply -- only the two
    # requests that carried an id do.
    assert [item["id"] for item in items] == [1, 2]
    assert items[1]["result"]["content"][0]["text"] == "ok\n"


def test_batch_of_only_notifications_gets_no_reply_at_all():
    batch = json.dumps([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    ok_request = json.dumps(
        {"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}}
    )
    rc, responses = _serve_lines(batch, ok_request)
    assert rc == 0
    # No line at all for the all-notification batch; the request right
    # after it still gets served normally.
    assert len(responses) == 1
    assert responses[0]["id"] == 99


def test_non_string_tool_name_is_reported_not_crashed():
    rc, responses = _serve_lines(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": ["EchoRoot.Echo"], "arguments": {}},
            }
        )
    )
    assert rc == 0
    assert responses[0]["error"]["code"] == -32602


def test_non_object_arguments_is_reported_not_crashed():
    rc, responses = _serve_lines(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo-root.echo", "arguments": "word"},
            }
        )
    )
    assert rc == 0
    assert responses[0]["error"]["code"] == -32602


def test_unknown_tool_name_over_json_rpc_is_invalid_params():
    rc, responses = _serve_lines(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "EchoRoot.NoSuchTool", "arguments": {}},
            }
        )
    )
    assert responses[0]["error"]["code"] == -32602


def test_ping_is_answered_with_an_empty_result():
    rc, responses = _serve_lines(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    )
    assert responses[0]["result"] == {}


@pytest.mark.parametrize(
    "requested,expected",
    [
        ("2024-11-05", "2024-11-05"),
        ("2025-03-26", "2025-03-26"),
        ("2099-01-01", "2025-06-18"),
        (None, "2025-06-18"),
    ],
)
def test_initialize_negotiates_a_supported_protocol_version(requested, expected):
    params = {"protocolVersion": requested} if requested else {}
    rc, responses = _serve_lines(
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
        )
    )
    assert responses[0]["result"]["protocolVersion"] == expected


def test_serverinfo_version_is_empty_when_app_declares_none():
    # Item 8: `EchoRoot` declares no `_version_` of its own, so `version`
    # must be the empty string -- NOT duho's own version.
    rc, responses = _serve_lines(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    )
    assert responses[0]["result"]["serverInfo"]["version"] == ""


# --------------------------------------------------------------------------
# The command tree is built (and layered) once per root class
# --------------------------------------------------------------------------


def test_tree_is_cached_across_repeated_describe_tools_calls():
    class CacheRoot(Cli):
        """Root."""

        _subcommands_ = [Echo]

    root_parser_1, nodes_1 = _tree_for(CacheRoot)
    root_parser_2, nodes_2 = _tree_for(CacheRoot)
    assert root_parser_1 is root_parser_2
    assert nodes_1 is nodes_2


# --------------------------------------------------------------------------
# python -m duho.mcp <app> end-to-end: real stdio isolation (not just StringIO)
# --------------------------------------------------------------------------


def _run_subprocess_app(
    app_path,
    argv_app_spec,
    request_lines,
    *,
    extra_env=None,
    remove_env=(),
    input_bytes=None,
):
    env = dict(os.environ)
    for key in remove_env:
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    env["PYTHONPATH"] = str(app_path.parent) + os.pathsep + env.get("PYTHONPATH", "")
    stdin_payload = (
        input_bytes
        if input_bytes is not None
        else ("\n".join(json.dumps(r) for r in request_lines) + "\n").encode("utf-8")
    )
    proc = subprocess.run(
        [sys.executable, "-m", "duho.mcp", argv_app_spec],
        input=stdin_payload,
        capture_output=True,
        env=env,
        timeout=30,
    )
    return proc


def test_subprocess_child_output_does_not_corrupt_the_protocol_stream(tmp_path):
    app_file = tmp_path / "noisy_app.py"
    app_file.write_text(
        "import subprocess, sys\n"
        "from duho import Cli, Cmd\n"
        "\n"
        "class Noisy(Cmd):\n"
        '    """Spawns a child that prints without capturing it."""\n'
        "    def __call__(self):\n"
        "        subprocess.run([sys.executable, '-c', \"print('CHILD-STDOUT-LINE')\"])\n"
        '        print("via-stdout")\n'
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Noisy app."""\n'
        "    _subcommands_ = [Noisy]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "noisy_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "app.noisy", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    # Every stdout line must be valid JSON -- the child's stray output must
    # have landed on stderr, never spliced into the protocol stream.
    responses = [json.loads(ln) for ln in lines]
    ids = [r["id"] for r in responses]
    assert ids == [1, 2]
    assert b"CHILD-STDOUT-LINE" in proc.stderr


def test_subprocess_command_reading_stdin_gets_eof_not_the_next_request(tmp_path):
    app_file = tmp_path / "reader_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class ReadsStdin(Cmd):\n"
        '    """Reads a line from stdin."""\n'
        "    def __call__(self):\n"
        "        import sys\n"
        "        print(repr(sys.stdin.readline()))\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Reader app."""\n'
        "    _subcommands_ = [ReadsStdin]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "reader_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "app.reads-stdin", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    responses = [json.loads(ln) for ln in lines]
    ids = [r["id"] for r in responses]
    # Both requests must still be answered -- the command's stdin read must
    # NOT have consumed the client's next (tools/list) request line.
    assert ids == [1, 2]
    read_text = responses[0]["result"]["content"][0]["text"]
    assert "tools/list" not in read_text


def test_subprocess_non_ascii_argument_round_trips_as_utf8(tmp_path):
    app_file = tmp_path / "utf8_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class Show(Cmd):\n"
        '    """Shows a value: caf\\u00e9."""\n'
        "    text: str\n"
        '    "Text to show"\n'
        "    ('--text',)\n"
        "\n"
        "    def __call__(self):\n"
        "        print(self.text)\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """UTF-8 app."""\n'
        "    _subcommands_ = [Show]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "utf8_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "app.show", "arguments": {"text": "café"}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
        remove_env=("PYTHONUTF8", "PYTHONIOENCODING"),
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    responses = [json.loads(ln) for ln in lines]
    assert responses[0]["result"]["content"][0]["text"].strip() == "café"
    # The docstring's own non-ASCII character must not have crashed tools/list.
    tools_result = responses[1]["result"]["tools"]
    assert any("caf" in (t["description"] or "") for t in tools_result)


def test_subprocess_import_time_output_does_not_corrupt_the_protocol_stream(tmp_path):
    # Distinct from `test_subprocess_child_output_does_not_corrupt_the_protocol_stream`
    # above: THIS write happens at MODULE IMPORT time, before `serve()` ever
    # runs -- `main()` resolves (imports) `<app>` before it takes over the
    # real stdio fds, so a stray module-level `print` used to land straight
    # on the client-facing pipe as a non-JSON first line.
    app_file = tmp_path / "import_noise_app.py"
    app_file.write_text(
        'print("IMPORT-TIME-NOISE")\n'
        "from duho import Cli, Cmd\n"
        "\n"
        "class Hi(Cmd):\n"
        '    """Says hi."""\n'
        "    def __call__(self):\n"
        '        print("hi")\n'
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Noisy-at-import app."""\n'
        "    _subcommands_ = [Hi]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "import_noise_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "app.hi", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ],
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    # Every stdout line must be valid JSON -- the import-time print must have
    # landed on stderr, never spliced in ahead of the protocol stream.
    responses = [json.loads(ln) for ln in lines]
    ids = [r["id"] for r in responses]
    assert ids == [1, 2]
    assert b"IMPORT-TIME-NOISE" in proc.stderr


def test_subprocess_thread_still_running_after_import_does_not_corrupt_the_protocol_stream(
    tmp_path,
):
    # Distinct from `test_subprocess_import_time_output_does_not_corrupt_the_
    # protocol_stream` above: that write happens DURING import and is caught
    # by redirecting fd 1 for the duration of resolution. THIS write comes
    # from a background thread STARTED at import time that keeps running
    # AFTER import returns -- an earlier fix resolved `<app>` behind a
    # temporary redirect that was restored right after import finished,
    # before `main()` handed off to `serve()`, which only THEN took real
    # stdio over for the protocol channel. A thread still running in that
    # gap could write straight into the client-facing pipe ahead of the
    # first protocol response.
    # The daemon thread runs for well under the time the command itself
    # takes to return, so it has always finished on its own by the time the
    # process starts shutting down -- CPython's interpreter finalization
    # racing an ACTIVELY WRITING daemon thread is its own (unrelated, and on
    # Windows sometimes fatal) hazard that would otherwise make this test
    # flaky for a reason that has nothing to do with the regression it is
    # checking for.
    app_file = tmp_path / "racy_import_app.py"
    app_file.write_text(
        "import threading, time\n"
        "from duho import Cli, Cmd\n"
        "\n"
        "def _spam():\n"
        "    for _ in range(200):\n"
        "        print('RACE-PRINT')\n"
        "        time.sleep(0.001)\n"
        "\n"
        "threading.Thread(target=_spam, daemon=True).start()\n"
        "\n"
        "class Hi(Cmd):\n"
        '    """Says hi."""\n'
        "    def __call__(self):\n"
        "        time.sleep(0.5)\n"
        '        print("hi")\n'
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Racy-at-import app."""\n'
        "    _subcommands_ = [Hi]\n",
        encoding="utf-8",
    )
    proc = _run_subprocess_app(
        app_file,
        "racy_import_app:App",
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "app.hi", "arguments": {}},
            },
        ],
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    # Every stdout line must be valid JSON -- the racing thread's output must
    # have landed on stderr, never spliced into the protocol stream.
    responses = [json.loads(ln) for ln in lines]
    assert responses[0]["id"] == 1


def test_subprocess_invalid_utf8_line_gets_parse_error_and_server_keeps_serving(
    tmp_path,
):
    app_file = tmp_path / "echo_utf8_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class Ping(Cmd):\n"
        '    """Replies pong."""\n'
        "    def __call__(self):\n"
        '        print("pong")\n'
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """App."""\n'
        "    _subcommands_ = [Ping]\n",
        encoding="utf-8",
    )
    bad_line = b'{"jsonrpc": "2.0", "id": 50, "method": "ping", "x": "\xff"}\n'
    good_line = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 51,
                "method": "tools/call",
                "params": {"name": "app.ping", "arguments": {}},
            }
        ).encode("utf-8")
        + b"\n"
    )
    proc = _run_subprocess_app(
        app_file, "echo_utf8_app:App", None, input_bytes=bad_line + good_line
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    responses = [json.loads(ln) for ln in lines]
    # The invalid line gets a parse error instead of killing the server --
    # the well-formed request right after it still gets a real reply.
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["id"] == 51
    assert responses[1]["result"]["content"][0]["text"].strip() == "pong"


def test_subprocess_deeply_nested_json_line_never_kills_the_server(tmp_path):
    # A line whose JSON nesting is pathologically deep used to raise an
    # uncaught RecursionError out of json.loads (not a ValueError, so the
    # old `except ValueError` around it never caught it), ending the whole
    # `serve` loop -- every request after it, including this same client's
    # own next ping, went unanswered. Depth chosen well past the rejection
    # threshold but still small/fast to encode and send.
    app_file = tmp_path / "ping_app.py"
    app_file.write_text(
        "from duho import Cli, Cmd\n"
        "\n"
        "class Ping(Cmd):\n"
        '    """Replies with an empty result."""\n'
        "    def __call__(self):\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """App."""\n'
        "    _subcommands_ = [Ping]\n",
        encoding="utf-8",
    )
    depth = 1000
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8"),
        b"[" * depth + b"]" * depth,
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode("utf-8"),
    ]
    proc = _run_subprocess_app(
        app_file, "ping_app:App", None, input_bytes=b"\n".join(lines) + b"\n"
    )
    assert proc.returncode == 0, proc.stderr
    out_lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    responses = [json.loads(ln) for ln in out_lines]
    # Both pings answered -- the nested-array line in between gets its own
    # parse-error response, and the server is still alive right after it.
    assert [r["id"] for r in responses] == [1, None, 2]
    assert responses[1]["error"]["code"] == -32700
    assert responses[2]["result"] == {}


# --------------------------------------------------------------------------
# ANSI escapes from argparse's native color (3.14+) must never reach MCP text
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 14), reason="argparse color is 3.14+")
def test_argument_error_text_has_no_ansi_escapes_under_force_color(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")

    class ConflictingFlags(Cmd):
        """Two flags that cannot both be set."""

        gzip: "Arg[bool, NS(conflicts='compression')]" = False
        "one compression choice"
        ("--gzip",)

        zstd: "Arg[bool, NS(conflicts='compression')]" = False
        "the other compression choice"
        ("--zstd",)

        def __call__(self):  # pragma: no cover
            return 0

    # Both flags are individually schema-valid (each just a boolean), so this
    # is refused by argparse's own mutually-exclusive-group enforcement at
    # PARSE time, not by `_validate_arguments` -- still exercising the
    # ANSI-color-muting path this test is actually about.
    result = call_tool(
        ConflictingFlags, "conflicting-flags", {"gzip": True, "zstd": True}
    )
    assert result["isError"] is True
    assert "\x1b" not in result["content"][0]["text"]


# --------------------------------------------------------------------------
# duho.scaffold's own CLI self-description (it dogfoods duho, same as mcp)
# --------------------------------------------------------------------------


def test_scaffold_cmd_describes_itself_correctly():
    import duho
    from duho.scaffold import ScaffoldCmd

    assert ScaffoldCmd._parsername_ == "duho.scaffold"
    assert ScaffoldCmd._version_ is duho.AUTO
    assert ScaffoldCmd._distribution_ == "duho"


def test_scaffold_reports_a_filesystem_error_cleanly_not_a_traceback(
    tmp_path, capsys, monkeypatch
):
    import duho.scaffold as scaffold_mod

    def _boom(*args, **kwargs):
        raise OSError("disk is full")

    monkeypatch.setattr(scaffold_mod, "generate_launchers", _boom)

    cmd = scaffold_mod.ScaffoldCmd()
    cmd.app = "myapp"
    cmd.root = tmp_path
    cmd.libdir = "lib"
    cmd.python = None
    cmd.force = False

    rc = cmd()
    captured = capsys.readouterr()
    assert rc == 1
    assert "duho.scaffold:" in captured.err
    assert "disk is full" in captured.err
    assert "Traceback" not in captured.err
