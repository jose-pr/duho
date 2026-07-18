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

import duho
from duho import Cli, Cmd


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
