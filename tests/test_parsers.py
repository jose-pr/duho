"""Tests for duho.parse_globals: parse only a root's globals, ignore subcommands.

``parse_globals`` is the public form of the internal help-suppressed,
subcommand-relaxed prepass (``duho.parsers.prerun_parse``): it lets a consumer
resolve config-driven command search paths (or any global) BEFORE committing to
the full subcommand parser. These tests pin the two guarantees that make it
useful: a missing subcommand does not error, and an unknown trailing token does
not crash the globals parse.

All command classes are defined in this real ``.py`` file so their AST-derived
flags/docstrings resolve normally (never via ``-c``).
"""

import pytest

import duho
from duho import Cli, Cmd, NS, Arg


class _Child(Cmd):
    """A leaf subcommand."""

    target: str = "here"
    "Where to act"
    ("--target",)

    def __call__(self):  # pragma: no cover - not dispatched in these tests
        return 0


class _Root(Cli):
    """A root command with a global flag and a subcommand tree."""

    flag: str = "default"
    "A global option resolved before subcommands"
    ("--flag",)

    _subcommands_ = [_Child]


def test_parse_globals_returns_root_instance_with_globals_set():
    """parse_globals(Root, ['--flag', 'x']) returns the root with the global set."""
    parsed = duho.parse_globals(_Root, ["--flag", "x"])
    assert isinstance(parsed, _Root)
    assert parsed.flag == "x"


def test_parse_globals_no_error_when_subcommand_omitted():
    """A missing subcommand does NOT raise, even though the tree is required."""
    parsed = duho.parse_globals(_Root, ["--flag", "y"])
    assert parsed.flag == "y"


def test_parse_globals_ignores_unknown_trailing_token():
    """An unknown trailing token (would-be subcommand + its args) is ignored."""
    parsed = duho.parse_globals(_Root, ["--flag", "z", "somecmd", "--unknown", "v"])
    assert parsed.flag == "z"


def test_parse_globals_default_when_flag_absent():
    """With no argv the global keeps its class default (globals-only parse)."""
    parsed = duho.parse_globals(_Root, [])
    assert parsed.flag == "default"


def test_parse_globals_forwards_parser_kwargs():
    """**parser_kwargs reach cls._parser_ (e.g. add_help=False)."""
    # add_help=False must not raise; --help is simply not injected. The parse
    # still succeeds and returns the root instance with globals set.
    parsed = duho.parse_globals(_Root, ["--flag", "kw"], add_help=False)
    assert parsed.flag == "kw"


# --------------------------------------------------------------------------
# parse_globals must apply the same env/config layers duho.main/parse do
# --------------------------------------------------------------------------


class _EnvRoot(Cli):
    """A root whose global is backed by an env var."""

    cmds_path: "Arg[str, NS(env='DUHO_TEST_GLOBALS_ENV')]" = "builtin"
    ("--cmds-path",)

    _subcommands_ = [_Child]


def test_parse_globals_applies_env_layer(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_GLOBALS_ENV", "/from/env")
    parsed = duho.parse_globals(_EnvRoot, [])
    monkeypatch.delenv("DUHO_TEST_GLOBALS_ENV", raising=False)
    assert parsed.cmds_path == "/from/env"


@pytest.mark.requires_toml
def test_parse_globals_applies_config_kwarg(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text('cmds_path = "/from/config"\n')
    parsed = duho.parse_globals(_EnvRoot, [], config=cfg)
    assert parsed.cmds_path == "/from/config"


class _RequiredEnvRoot(Cli):
    """A REQUIRED global (no class default) suppliable only via env."""

    token: "Arg[str, NS(env='DUHO_TEST_GLOBALS_TOKEN')]"
    ("--token",)

    _subcommands_ = [_Child]


def test_parse_globals_env_layer_satisfies_a_required_global(monkeypatch):
    # parse_globals must apply env/config layers: a required global
    # suppliable only by env must not raise SystemExit(2) here when the full
    # duho.parse of the same class succeeds.
    monkeypatch.setenv("DUHO_TEST_GLOBALS_TOKEN", "tok")
    parsed = duho.parse_globals(_RequiredEnvRoot, ["_Child"])
    monkeypatch.delenv("DUHO_TEST_GLOBALS_TOKEN", raising=False)
    assert parsed.token == "tok"


# --------------------------------------------------------------------------
# the EXPORTED duho.parsers.prerun_parse must be safe to call directly
# on a duho root that still has its own subparsers action -- not just
# through parse_globals.
# --------------------------------------------------------------------------

from duho.parsers import prerun_parse  # noqa: E402


def test_exported_prerun_parse_on_a_duho_root_with_subcommands():
    parser = _Root._parser_()
    # A trailing subcommand name (with the child's own flag after it) must not
    # raise KeyError('#cls') (the relaxed subparsers action re-entering this
    # SAME parser's own patched parse_known_args would double-pop the
    # selection marker). It must just parse the globals and ignore the
    # rest, exactly like duho.parse_globals does.
    parsed = prerun_parse(parser, ["--flag", "x", "_Child", "--target", "here"])
    assert parsed.flag == "x"


def test_exported_prerun_parse_on_a_duho_root_with_subcommands_no_subcommand():
    parser = _Root._parser_()
    parsed = prerun_parse(parser, ["--flag", "y"])
    assert parsed.flag == "y"
