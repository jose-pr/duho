"""Tests for agent-oriented help (``duho.agenthelp``).

Two triggers, one emitter:

* the always-on ``AGENT_HELP``/``AGENTS_HELP`` env vars (either truthy) flip
  ``-h``/``--help`` into a detailed, machine-readable JSON description (human
  help is byte-identical when both are unset);
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
from duho.agenthelp import (
    DEFAULT_ENVS,
    SCHEMA,
    agent_help_requested,
    describe,
    describe_parser,
    render,
)

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
    return next(s for s in doc["subcommands"] if s["name"] == "deploy")


def _opt(spec, dest):
    return next(o for o in spec["options"] if o["dest"] == dest)


# --------------------------------------------------------------------------
# Env/config secret values are never leaked as agent-help defaults
# --------------------------------------------------------------------------


class SecretDeploy(Cmd):
    """Deploy something using a secret."""

    token: Arg[str, NS(env="DUHO_TEST_AGENTHELP_SECRET")] = ""
    "Auth token"
    ("--token",)

    password: str = ""
    "DB password"
    ("--password",)

    def __call__(self):
        return 0


class SecretApp(Cli):
    """App with an env-bound secret field of its own, plus a subcommand with
    an env- and config-bound secret of ITS own."""

    _version_ = "1.0.0"
    _agent_help_ = True
    _subcommands_ = [SecretDeploy]

    root_token: Arg[str, NS(env="DUHO_TEST_AGENTHELP_ROOT_SECRET")] = ""
    "Root-level auth token"
    ("--root-token",)


def test_agent_help_flag_redacts_root_env_secret(monkeypatch, capsys):
    # `--help-agents` alone never selects (or parses into) a subcommand, so
    # only the ROOT's own env/config-bound fields are ever actually layered
    # for this invocation -- exercised here on `root_token`.
    #
    # The LIVE env value must never appear; the CLASS-declared default
    # (never a secret -- a fixed, code-level constant) always does, so a
    # consumer still learns the field's declared shape/default. Previously
    # this over-redacted to a bare `null`, losing that safe information too.
    monkeypatch.setenv("DUHO_TEST_AGENTHELP_ROOT_SECRET", "root-s3cr3t-api-key")
    with pytest.raises(SystemExit):
        duho.main(SecretApp, ["--help-agents"])
    out = capsys.readouterr().out

    assert "root-s3cr3t-api-key" not in out
    doc = json.loads(out)
    root_token = next(o for o in doc["options"] if o["dest"] == "root_token")
    assert root_token["default"] == ""  # the class default, not the live env value
    assert root_token["default_source"] == "env DUHO_TEST_AGENTHELP_ROOT_SECRET"


def test_agent_help_no_secret_leaves_default_untouched(monkeypatch, capsys):
    # No env/config value in play: the class default still shows normally,
    # and no `default_source` key is added.
    monkeypatch.delenv("DUHO_TEST_AGENTHELP_ROOT_SECRET", raising=False)
    with pytest.raises(SystemExit):
        duho.main(SecretApp, ["--help-agents"])
    doc = json.loads(capsys.readouterr().out)
    root_token = next(o for o in doc["options"] if o["dest"] == "root_token")
    assert root_token["default"] == ""
    assert "default_source" not in root_token


@pytest.mark.requires_toml
def test_agent_help_env_trigger_scoped_to_subcommand_redacts_env_and_config_secret(
    tmp_path, monkeypatch, capsys
):
    # `AGENT_HELP=1 app SecretDeploy --help` DOES parse into the subcommand,
    # so both its env- and config-bound fields get layered for real before
    # this fires -- the flagship secret-leak scenario this test guards against.
    monkeypatch.setenv("AGENT_HELP", "1")
    monkeypatch.setenv("DUHO_TEST_AGENTHELP_SECRET", "s3cr3t-api-key")
    cfg = tmp_path / "cfg.toml"
    cfg.write_text('[secret-deploy]\npassword = "cfg-db-password"\n')

    with pytest.raises(SystemExit):
        duho.main(SecretApp, ["secret-deploy", "--help"], config=cfg)
    out = capsys.readouterr().out

    assert "s3cr3t-api-key" not in out
    assert "cfg-db-password" not in out
    doc = json.loads(out)
    token = next(o for o in doc["options"] if o["dest"] == "token")
    password = next(o for o in doc["options"] if o["dest"] == "password")
    # The class default (never itself a secret) is still shown -- only the
    # LIVE env/config value is redacted; `default_source` is what tells a
    # reader the value in effect right now may differ from it.
    assert token["default"] == ""
    assert token["default_source"] == "env DUHO_TEST_AGENTHELP_SECRET"
    assert password["default"] == ""
    assert password["default_source"] == "config"


# --------------------------------------------------------------------------
# A literal `%(default)s` placeholder in help TEXT must never expand to a
# live env/config value either -- a separate leak from the `default`/
# `default_source` JSON fields above, since argparse's own `%`-expansion of
# help text reads `action.default` directly.
# --------------------------------------------------------------------------


class PlaceholderDeploy(Cmd):
    """Deploy something using a secret, with the default spelled in help."""

    token: Arg[str, NS(env="DUHO_TEST_AGENTHELP_PLACEHOLDER_SECRET")] = ""
    "API token (default: %(default)s)"
    ("--token",)

    plain: str = "unremarkable"
    "A plain field whose default is not secret (default: %(default)s)"
    ("--plain",)

    def __call__(self):
        return 0


class PlaceholderApp(Cli):
    """App whose subcommand spells `%(default)s` directly in help text."""

    _agent_help_ = True
    _subcommands_ = [PlaceholderDeploy]


def test_agent_help_placeholder_in_help_text_never_shows_live_env_value(
    monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_HELP", "1")
    monkeypatch.setenv("DUHO_TEST_AGENTHELP_PLACEHOLDER_SECRET", "placeholder-s3cr3t")
    with pytest.raises(SystemExit):
        duho.main(PlaceholderApp, ["placeholder-deploy", "--help"])
    out = capsys.readouterr().out
    assert "placeholder-s3cr3t" not in out
    doc = json.loads(out)
    token = next(o for o in doc["options"] if o["dest"] == "token")
    assert "placeholder-s3cr3t" not in token["help"]
    # A plain (non-env) field's declared default keeps rendering normally.
    plain = next(o for o in doc["options"] if o["dest"] == "plain")
    assert "(default: unremarkable)" in plain["help"]


def test_human_help_placeholder_in_help_text_never_shows_live_env_value(
    monkeypatch, capsys
):
    monkeypatch.delenv("AGENT_HELP", raising=False)
    monkeypatch.setenv("DUHO_TEST_AGENTHELP_PLACEHOLDER_SECRET", "placeholder-s3cr3t")
    with pytest.raises(SystemExit):
        duho.main(PlaceholderApp, ["placeholder-deploy", "--help"])
    out = capsys.readouterr().out
    assert "placeholder-s3cr3t" not in out
    # The plain field's declared default keeps rendering normally in human
    # help too (argparse may wrap the line, so match loosely).
    assert "unremarkable" in out


# --------------------------------------------------------------------------
# Document shape
# --------------------------------------------------------------------------


def test_root_document_has_schema_version_and_tree():
    doc = describe(App)
    assert doc["schema"] == SCHEMA
    assert doc["prog"] == "app"
    assert doc["version"] == "9.9.9"
    assert doc["description"] == "My multi-command app."
    assert [s["name"] for s in doc["subcommands"]] == ["deploy"]


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
    deploys = [s for s in doc["subcommands"] if s["name"] == "deploy"]
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
    assert doc["examples"][0]["command"].startswith("plain-app")


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
    assert doc["prog"] == "app"


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
        parser.parse_args(["deploy", "--help"])
    doc = json.loads(capsys.readouterr().out)
    # Subcommand help under the env trigger describes that subcommand as root.
    assert doc["prog"] == "app deploy"
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


def test_default_envs_tuple():
    assert DEFAULT_ENVS == ("AGENT_HELP", "AGENTS_HELP")


def test_agent_help_requested_agents_help_alias_truthy():
    assert agent_help_requested(environ={"AGENTS_HELP": "1"}) is True


def test_agent_help_requested_agents_help_alias_falsey():
    assert agent_help_requested(environ={"AGENTS_HELP": "0"}) is False


def test_agent_help_requested_both_set_truthy():
    assert agent_help_requested(environ={"AGENT_HELP": "1", "AGENTS_HELP": "1"}) is True


def test_agent_help_requested_either_truthy_triggers():
    # AGENT_HELP off, AGENTS_HELP on -> still triggers (either truthy).
    assert agent_help_requested(environ={"AGENT_HELP": "0", "AGENTS_HELP": "1"}) is True
    # AGENT_HELP on, AGENTS_HELP off -> still triggers.
    assert agent_help_requested(environ={"AGENT_HELP": "1", "AGENTS_HELP": "0"}) is True


def test_agent_help_requested_neither_set():
    assert (
        agent_help_requested(environ={"AGENT_HELP": "0", "AGENTS_HELP": "0"}) is False
    )


def test_agent_help_requested_explicit_env_name_ignores_defaults():
    # An explicit env_name checks ONLY that variable -- no aliasing to either
    # default, even when one of them is set truthy in the same environ.
    assert (
        agent_help_requested(
            "MY_AGENT_HELP", environ={"AGENT_HELP": "1", "AGENTS_HELP": "1"}
        )
        is False
    )
    assert (
        agent_help_requested(
            "MY_AGENT_HELP", environ={"MY_AGENT_HELP": "1", "AGENT_HELP": "0"}
        )
        is True
    )


def test_custom_env_var_agents_help_alias_does_not_trigger(monkeypatch, capsys):
    """An explicit ``_agent_help_env_`` replaces BOTH defaults -- ``AGENTS_HELP``
    set truthy must not trigger agent help for an app with its own override."""

    class CustomAliasApp(Cli):
        """Custom env app, alias check."""

        _agent_help_env_ = "MY_AGENT_HELP"
        _subcommands_ = [Deploy]

    monkeypatch.delenv("AGENT_HELP", raising=False)
    monkeypatch.delenv("MY_AGENT_HELP", raising=False)
    monkeypatch.setenv("AGENTS_HELP", "1")
    parser = CustomAliasApp._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    # Human help rendered (not the JSON agent document) -- AGENTS_HELP alone
    # does not trigger an app that declared its own _agent_help_env_.
    out = capsys.readouterr().out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


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
    assert json.loads(capsys.readouterr().out)["prog"] == "custom-app"


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


# --------------------------------------------------------------------------
# Agent-help type strings are version-independent
# --------------------------------------------------------------------------


class TypeStrings(Cmd):
    """Fields exercising every type-string rendering path."""

    port: ty.Optional[int] = None
    "Port"
    ("--port",)

    tags: ty.List[str]
    "Tags"
    ("--tags",)

    colors: ty.List[Color]
    "Colors"
    ("--colors",)

    maybe: ty.Optional[Color] = None
    "Maybe a color"
    ("--maybe",)

    color: Color = Color.RED
    "A color"
    ("--color",)

    labels: ty.Literal["x, y", "z"] = "z"
    "A literal with a comma inside one member"
    ("--labels",)

    mixed: ty.Literal["1", 1] = 1
    "A literal mixing a string and an int of the same spelling"
    ("--mixed",)

    def __call__(self):
        return 0


def test_type_strings_are_version_independent():
    # Pinned to one canonical spelling regardless of interpreter: 3.9/3.10
    # used to render a bare `list[str]` as `list` (losing the element type)
    # and `Optional[int]`; 3.14 renders unions as `int | None`. A qualified
    # Enum used to appear only inside a generic (`list[__main__.Color]`).
    # `labels`/`mixed` pin a SEPARATE regression: rendering a `Literal`'s own
    # ARGS (values, not types) by recursing into the same type-string logic
    # dropped their quoting entirely (`Literal["x, y", "z"]` -> `Literal[x, y,
    # z]`, indistinguishable from a 3-member literal) and collapsed a string
    # member onto an int of the same spelling (`Literal["1", 1]` ->
    # `Literal[1, 1]`) -- `repr()` on each member fixes both, identically on
    # every interpreter.
    doc = describe(TypeStrings)
    opts = {o["dest"]: o for o in doc["options"]}
    assert opts["port"]["type"] == "int | None"
    assert opts["tags"]["type"] == "list[str]"
    assert opts["colors"]["type"] == "list[Color]"
    assert opts["maybe"]["type"] == "Color | None"
    assert opts["color"]["type"] == "Color"
    assert opts["labels"]["type"] == "Literal['x, y', 'z']"
    assert opts["mixed"]["type"] == "Literal['1', 1]"


class TupleType(Cmd):
    """A variadic-tuple field."""

    counts: "ty.Tuple[int, ...]" = ()
    "Repeated counts"
    ("--counts",)

    def __call__(self):
        return 0


def test_variadic_tuple_type_renders_ellipsis_literally():
    # `_ty.get_args(tuple[int, ...])` hands back the literal `Ellipsis`
    # object for the `...` -- recursing into the same type-string logic
    # stringified it as the word `Ellipsis` (`tuple[int, Ellipsis]`) instead
    # of the `...` Python itself would show.
    doc = describe(TupleType)
    opt = next(o for o in doc["options"] if o["dest"] == "counts")
    assert opt["type"] == "tuple[int, ...]"


# --------------------------------------------------------------------------
# The synthesized minimal invocation always includes <command>
# --------------------------------------------------------------------------


class ReqRootSub(Cmd):
    """A trivial subcommand."""

    def __call__(self):
        return 0


class ReqRootApp(Cli):
    """Root requiring a global option, plus a subcommand."""

    _subcommands_ = [ReqRootSub]

    region: str
    "Region"
    ("--region", "-r")


def test_synthesized_example_keeps_command_with_required_root_option():
    doc = describe(ReqRootApp)
    example = doc["examples"][0]["command"]
    # `<command>` survives even though a root option is also required
    # (duho's subparsers are always `required=True`, independent of that);
    # the long flag is preferred over the short alias.
    assert example == "req-root-app --region REGION <command>"
    parser = ReqRootApp._parser_()
    parsed = parser.parse_args(["--region", "x", "req-root-sub"])
    assert parsed is not None


# --------------------------------------------------------------------------
# Subcommand-scoped agent help still reports the APP's version/exit
# codes, while examples stay scoped to the current command
# --------------------------------------------------------------------------


def test_env_trigger_scoped_help_reports_root_version_and_exit_codes(
    monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_HELP", "1")
    parser = App._parser_()
    with pytest.raises(SystemExit):
        parser.parse_args(["deploy", "--help"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["prog"] == "app deploy"
    assert doc["version"] == "9.9.9"
    assert doc["exit_codes"]["3"] == "Custom failure."
    # Deploy declares no `_examples_` of its own: a Deploy-scoped example is
    # synthesized, not the app's unrelated root-level declared example.
    assert doc["examples"][0]["command"].startswith("app deploy")
    assert doc["examples"][0]["command"] != "myapp Deploy --env prod ./src"


# --------------------------------------------------------------------------
# (agenthelp half): the usage text in an agent-help document is never
# colored, even when argparse's native 3.14+ color is forced on
# --------------------------------------------------------------------------


def test_agent_help_usage_has_no_ansi_when_color_forced(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    doc = describe(App)
    assert "\x1b" not in doc["usage"]
    dep = _deploy_spec(doc)
    assert "\x1b" not in dep["usage"]
    # A control byte JSON must escape (`\u001b`) survives a decode round-trip
    # back into a real ESC character -- check the DECODED value too, not just
    # the serialized text (which is always control-byte-escaped regardless).
    decoded = json.loads(render(doc))
    assert "\x1b" not in decoded["usage"]
    assert "\x1b" not in decoded["subcommands"][0]["usage"]


def test_print_agent_help_has_no_ansi_when_color_forced(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    out = tmp_path / "help.json"
    with out.open("w", encoding="utf-8") as fh:
        duho.print_agent_help(App, file=fh)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "\x1b" not in doc["usage"]


@pytest.mark.parametrize(
    "annotation, expected",
    [
        (list[str], "list[str]"),
        (dict[str, int], "dict[str, int]"),
        (tuple[int, ...], "tuple[int, ...]"),
        (set[str], "set[str]"),
        (ty.List[int], "list[int]"),
        (ty.Optional[list[str]], "list[str] | None"),
        (int, "int"),
    ],
)
def test_builtin_generic_type_strings_keep_their_arguments(annotation, expected):
    """A bare ``list[str]``-style generic renders with its arguments on every
    interpreter (3.9/3.10 treat such an alias as a ``type`` instance)."""
    from duho.agenthelp import _render_type

    assert _render_type(annotation) == expected
