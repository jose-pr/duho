"""Tests for agent-oriented help (``duho.agenthelp``).

Two triggers, one emitter:

* the always-on ``AGENT_HELP`` env var flips ``-h``/``--help`` into a detailed,
  machine-readable JSON description (human help is byte-identical when unset);
* the opt-in ``--help-agents`` flag emits the same document unconditionally.

The document is built by walking the *built* parser tree and enriching each
command with duho's field metadata (types, defaults, required/repeatable, env
bindings, conflict groups), plus version, exit codes, and examples.

Fixtures are declared at module level (a real source file) because duho's
flags-tuple / docstring introspection is AST-based and needs one -- the same
reason ``test_completion.py`` does it this way.
"""

import argparse
import enum
import json
import pathlib
import typing as ty

import pytest

import duho
from duho import Arg, Cli, Cmd, LoggingArgs, NS
from duho.agenthelp import SCHEMA, agent_help_requested, describe, describe_parser, render


# --------------------------------------------------------------------------
# Fixtures: a multi-command app exercising the full field surface
# --------------------------------------------------------------------------


class Color(enum.Enum):
    RED = 1
    GREEN = 2


class Deploy(Cmd):
    """Deploy the app to a target."""

    _parseraliases_ = ["d", "dep"]

    environment: str
    "Target environment"
    ("--env",)

    replicas: int = 1
    "Replica count"
    ("--replicas",)

    mode: ty.Literal["fast", "slow"] = "fast"
    "Deployment mode"
    ("--mode",)

    color: Color = Color.RED
    "A color"
    ("--color",)

    tags: ty.List[str]
    "Repeatable tag (accumulates)"
    ("--tag",)

    token: Arg[str, NS(env="DEPLOY_TOKEN")] = ""
    "Auth token"
    ("--token",)

    gzip: Arg[bool, NS(conflicts="compression")] = False
    "Compress with gzip"
    ("--gzip",)

    zstd: Arg[bool, NS(conflicts="compression")] = False
    "Compress with zstd"
    ("--zstd",)

    source: pathlib.Path
    "Required positional source"
    ("source",)

    dest: str = "."
    "Optional positional destination"
    ("dest",)

    def __call__(self):
        return 0


class App(LoggingArgs, Cli):
    """My multi-command app."""

    _version_ = "9.9.9"
    _agent_help_ = True
    _subcommands_ = [Deploy]
    _examples_ = [("myapp Deploy --env prod ./src", "Deploy to prod")]
    _exit_codes_ = {3: "Custom failure."}


class PlainApp(Cli):
    """An app that did NOT opt into --help-agents."""

    _subcommands_ = [Deploy]


def _deploy_spec(doc):
    return next(s for s in doc["subcommands"] if s["name"] == "Deploy")


def _opt(spec, dest):
    return next(o for o in spec["options"] if o["dest"] == dest)


# --------------------------------------------------------------------------
# Document shape
# --------------------------------------------------------------------------


def test_root_document_has_schema_version_and_tree():
    doc = describe(App)
    assert doc["schema"] == SCHEMA
    assert doc["prog"] == "App"
    assert doc["version"] == "9.9.9"
    assert doc["description"] == "My multi-command app."
    assert [s["name"] for s in doc["subcommands"]] == ["Deploy"]


def test_document_is_valid_json_roundtrip():
    doc = describe(App)
    text = render(doc)
    assert json.loads(text) == doc
    assert text.endswith("\n")


def test_option_metadata_type_default_required_repeatable():
    dep = _deploy_spec(describe(App))

    env = _opt(dep, "environment")
    assert env["names"] == ["--env"]
    assert env["type"] == "str"
    assert env["required"] is True
    assert env["takes_value"] is True
    assert env["help"] == "Target environment"

    replicas = _opt(dep, "replicas")
    assert replicas["type"] == "int"
    assert replicas["required"] is False
    assert replicas["default"] == 1

    tags = _opt(dep, "tags")
    assert tags["repeatable"] is True
    assert tags["default"] == []


def test_option_choices_from_literal_and_enum():
    dep = _deploy_spec(describe(App))
    assert _opt(dep, "mode")["choices"] == ["fast", "slow"]
    # An Enum field offers its member NAMES as choices.
    assert _opt(dep, "color")["choices"] == ["RED", "GREEN"]


def test_env_and_conflicts_metadata():
    dep = _deploy_spec(describe(App))
    assert _opt(dep, "token")["env"] == "DEPLOY_TOKEN"
    assert _opt(dep, "gzip")["conflicts"] == "compression"

    groups = {g["group"]: g for g in dep["conflicts"]}
    assert set(groups["compression"]["members"]) == {"gzip", "zstd"}
    assert groups["compression"]["required"] is False


def test_positionals_required_and_optional():
    dep = _deploy_spec(describe(App))
    positionals = {p["name"]: p for p in dep["positionals"]}
    assert positionals["source"]["required"] is True
    assert positionals["source"]["type"] == "Path"
    # An optional positional (has a default) is nargs="?" -> not required.
    assert positionals["dest"]["required"] is False


def test_subcommand_aliases_described_once():
    doc = describe(App)
    deploys = [s for s in doc["subcommands"] if s["name"] == "Deploy"]
    assert len(deploys) == 1
    assert set(deploys[0]["aliases"]) == {"d", "dep"}


def test_exit_codes_merge_defaults_with_overrides():
    doc = describe(App)
    assert doc["exit_codes"]["0"].startswith("Success")
    assert doc["exit_codes"]["2"].startswith("Usage error")
    assert doc["exit_codes"]["3"] == "Custom failure."


def test_examples_prefers_declared():
    doc = describe(App)
    assert doc["examples"] == [
        {"command": "myapp Deploy --env prod ./src", "description": "Deploy to prod"}
    ]


def test_examples_synthesized_when_undeclared():
    # PlainApp declares no _examples_; a minimal line is synthesized.
    doc = describe(PlainApp)
    assert len(doc["examples"]) == 1
    assert doc["examples"][0]["command"].startswith("PlainApp")


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------


def test_help_agents_flag_emits_json_and_exits(capsys):
    parser = App._parser_()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help-agents"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["schema"] == SCHEMA
    assert doc["prog"] == "App"


def test_help_agents_flag_absent_without_optin():
    # PlainApp did not set _agent_help_, so no --help-agents flag exists.
    parser = PlainApp._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help-agents"])  # argparse: unrecognized argument


def test_env_trigger_flips_help_to_json(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HELP", "1")
    parser = PlainApp._parser_()  # works even without the opt-in flag
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == SCHEMA


def test_help_is_human_without_env(capsys):
    # No AGENT_HELP in the environment -> the ordinary human help renders.
    parser = App._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    out = capsys.readouterr().out
    assert out.lstrip()[:1] != "{"
    assert "usage:" in out


def test_env_trigger_scopes_to_subcommand(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_HELP", "1")
    parser = App._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["Deploy", "--help"])
    doc = json.loads(capsys.readouterr().out)
    # Subcommand help under the env trigger describes that subcommand as root.
    assert doc["prog"] == "App Deploy"
    assert any(o["dest"] == "environment" for o in doc["options"])


# --------------------------------------------------------------------------
# agent_help_requested
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "y", "t", "anything"])
def test_agent_help_requested_truthy(value):
    assert agent_help_requested(environ={"AGENT_HELP": value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "n", "f"])
def test_agent_help_requested_falsey(value):
    assert agent_help_requested(environ={"AGENT_HELP": value}) is False


def test_agent_help_requested_unset():
    assert agent_help_requested(environ={}) is False


def test_custom_env_var_name(monkeypatch, capsys):
    class CustomApp(Cli):
        """Custom env app."""

        _agent_help_env_ = "MY_AGENT_HELP"
        _subcommands_ = [Deploy]

    monkeypatch.delenv("AGENT_HELP", raising=False)
    monkeypatch.setenv("MY_AGENT_HELP", "1")
    parser = CustomApp._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    assert json.loads(capsys.readouterr().out)["prog"] == "CustomApp"


# --------------------------------------------------------------------------
# Robustness: a parser with no duho class behind it
# --------------------------------------------------------------------------


def test_describe_parser_on_plain_argparse():
    # A raw argparse parser has no _duho_cls_; it must still describe cleanly,
    # just without duho-only metadata (env/conflicts).
    parser = argparse.ArgumentParser(prog="raw", description="Raw parser.")
    parser.add_argument("--name")
    parser.add_argument("count", type=int)
    doc = describe_parser(parser, root=True)
    assert doc["prog"] == "raw"
    name = next(o for o in doc["options"] if o["dest"] == "name")
    assert "env" not in name and "conflicts" not in name
    count = next(p for p in doc["positionals"] if p["name"] == "count")
    assert count["required"] is True


def test_print_agent_help_writes_json(tmp_path):
    out = tmp_path / "help.json"
    with out.open("w", encoding="utf-8") as fh:
        duho.print_agent_help(App, file=fh)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA
