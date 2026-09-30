"""Tests for the opt-in help formatters (F8).

``DefaultsFormatter`` appends ``(default: X)`` (skipping None/""/False);
``ColorHelpFormatter`` adds ANSI (gated on TTY / NO_COLOR / FORCE_COLOR);
``ColorDefaultsFormatter`` composes both. Assertions are loose substrings, not
golden files, because argparse formatter internals vary across 3.9-3.13.
"""

import argparse
import re
import sys
import typing as ty

import pytest

import duho
from duho import Arg, Args, NS


class DefaultsApp(Args):
    """App using the defaults-in-help formatter."""

    _help_formatter_ = duho.DefaultsFormatter

    region: str = "us-east"
    "target region"
    ("--region",)

    verbose: bool = False
    "chatty output"
    ("--verbose",)

    name: str
    "required, no default"
    ("--name",)


def test_defaults_formatter_appends_nonempty_default():
    help_text = DefaultsApp._parser_().format_help()
    assert "(default: us-east)" in help_text


def test_defaults_formatter_skips_false_and_required():
    help_text = DefaultsApp._parser_().format_help()
    # store_true default False -> no suffix; required field has no default -> none.
    assert "(default: False)" not in help_text
    assert "(default: None)" not in help_text


# --------------------------------------------------------------------------
# Empty sized containers are noise too, same as None/""/False
# --------------------------------------------------------------------------


class ContainerDefaultsApp(duho.LoggingArgs, Args):
    """A list field, plus LoggingArgs' own dict-default --loglevel."""

    _help_formatter_ = duho.DefaultsFormatter

    tags: ty.List[str] = []
    "Tags"
    ("--tags",)


def test_defaults_formatter_skips_empty_containers():
    help_text = ContainerDefaultsApp._parser_().format_help()
    assert "(default: [])" not in help_text
    assert "(default: {})" not in help_text


# --------------------------------------------------------------------------
# DefaultsFormatter shows only the CLASS default, never a live
# env/config value, for human --help too
# --------------------------------------------------------------------------


class SecretHelpApp(Args):
    """App with an env-bound secret shown by DefaultsFormatter."""

    _help_formatter_ = duho.DefaultsFormatter

    token: Arg[str, NS(env="DUHO_TEST_FORMATTERS_SECRET")] = ""
    "Auth token"
    ("--token",)

    def __call__(self):
        return 0


def test_defaults_formatter_redacts_env_secret_on_human_help(monkeypatch, capsys):
    monkeypatch.setenv("DUHO_TEST_FORMATTERS_SECRET", "human-s3cr3t-value")
    with pytest.raises(SystemExit):
        duho.main(SecretHelpApp, ["--help"])
    out = capsys.readouterr().out
    assert "human-s3cr3t-value" not in out
    assert "(from env DUHO_TEST_FORMATTERS_SECRET)" in out


def test_defaults_formatter_shows_class_default_without_secret(monkeypatch, capsys):
    monkeypatch.delenv("DUHO_TEST_FORMATTERS_SECRET", raising=False)
    with pytest.raises(SystemExit):
        duho.main(SecretHelpApp, ["--help"])
    out = capsys.readouterr().out
    assert "(from env" not in out


# --------------------------------------------------------------------------
# A literal `%(default)s` placeholder in help TEXT is a SEPARATE leak from
# the suffix DefaultsFormatter appends itself: argparse's own `%`-expansion
# of help text reads `action.default` directly, bypassing
# `DefaultsFormatter._get_help_string` entirely for text that already
# contains the placeholder.
# --------------------------------------------------------------------------


class SecretPlaceholderApp(Args):
    """App with an env-bound secret spelled directly via %(default)s."""

    _help_formatter_ = duho.DefaultsFormatter

    token: Arg[str, NS(env="DUHO_TEST_FORMATTERS_PLACEHOLDER_SECRET")] = ""
    "Auth token (default: %(default)s)"
    ("--token",)

    def __call__(self):
        return 0


def test_defaults_formatter_placeholder_never_shows_live_env_value(monkeypatch, capsys):
    monkeypatch.setenv(
        "DUHO_TEST_FORMATTERS_PLACEHOLDER_SECRET", "placeholder-formatter-s3cr3t"
    )
    with pytest.raises(SystemExit):
        duho.main(SecretPlaceholderApp, ["--help"])
    out = capsys.readouterr().out
    assert "placeholder-formatter-s3cr3t" not in out


class ColorApp(Args):
    """App using the color formatter."""

    _help_formatter_ = duho.ColorHelpFormatter

    region: str = "us-east"
    "target region"
    ("--region",)


def test_color_formatter_no_ansi_without_tty(monkeypatch):
    # No TTY, no FORCE_COLOR -> plain output (byte-identical to base formatter).
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    help_text = ColorApp._parser_().format_help()
    assert "\033[" not in help_text


def test_color_formatter_emits_ansi_when_forced(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    help_text = ColorApp._parser_().format_help()
    assert "\033[" in help_text
    assert "--region" in help_text  # flag text still present (inside color codes)


def test_no_color_beats_force_color(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    help_text = ColorApp._parser_().format_help()
    assert "\033[" not in help_text


class _NonTTYStream:
    def isatty(self):
        return False


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "anything"])
def test_force_color_unrecognized_value_does_not_force_color(monkeypatch, value):
    """FORCE_COLOR=0/false/no/garbage must not force color ON: an
    unrecognized-as-truthy value is treated as UNSET, falling through to the
    normal TTY check, never as an explicit "off" (which would be
    indistinguishable from NO_COLOR)."""
    from duho.formatters import _color_enabled

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", value)
    assert _color_enabled(_NonTTYStream()) is False


def test_force_color_truthy_value_forces_color_on_a_non_tty(monkeypatch):
    from duho.formatters import _color_enabled

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "yes")
    assert _color_enabled(_NonTTYStream()) is True


# --------------------------------------------------------------------------
# Colored help never misaligns (pre-3.14) or double-colors (3.14+)
# --------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class ColorAlignApp(duho.LoggingArgs, Args):
    """A short+long flag alongside LoggingArgs' own --loglevel -- the shape
    that showed ragged columns pre-fix (a short invocation on one line, a
    longer one wrapping and shifting the help column)."""

    _help_formatter_ = duho.ColorHelpFormatter

    name: str = "x"
    "The name"
    ("-n", "--name")

    tags: ty.List[str] = []
    "Tags"
    ("--tags",)


def test_color_help_alignment_matches_plain_help_when_forced(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PYTHON_COLORS", raising=False)
    colored = ColorAlignApp._parser_().format_help()
    # `formatter_class=argparse.HelpFormatter` opts OUT of duho's own
    # coloring, but not necessarily argparse's native one (a 3.14+
    # `ArgumentParser`'s own `color` default is independent of which
    # formatter class is used) -- strip both sides so the comparison is
    # about LAYOUT, the thing this test is actually about.
    plain = ColorAlignApp._parser_(formatter_class=argparse.HelpFormatter).format_help()
    assert _ANSI.sub("", colored) == _ANSI.sub("", plain)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="argparse native color is 3.14+")
def test_color_help_alignment_matches_plain_with_native_color_disabled(monkeypatch):
    # duho defers entirely to argparse's own color on 3.14+ (never nests its
    # own codes around argparse's native theme); with that native color also
    # switched off (`PYTHON_COLORS=0`), the whole line is plain either way.
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    monkeypatch.delenv("NO_COLOR", raising=False)
    colored = ColorAlignApp._parser_().format_help()
    plain = ColorAlignApp._parser_(formatter_class=argparse.HelpFormatter).format_help()
    assert _ANSI.sub("", colored) == _ANSI.sub("", plain)


class ComposedApp(Args):
    """App composing color + defaults."""

    _help_formatter_ = duho.ColorDefaultsFormatter

    region: str = "us-east"
    "target region"
    ("--region",)


def test_composed_formatter_has_defaults_and_color(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    help_text = ComposedApp._parser_().format_help()
    # 3.15+ colors the expanded %(default)s value itself, so the plain text
    # only appears once ANSI codes are stripped (duho's own coloring is
    # unaffected either way; this just stops the pin depending on which
    # argparse version formats defaults).
    assert "(default: us-east)" in _ANSI.sub("", help_text)
    assert "\033[" in help_text


def test_formatter_inherited_by_subcommands():
    """A root's _help_formatter_ reaches its subcommand parsers too."""

    class Sub(duho.Cmd):
        """A subcommand."""

        flag: str = "x"
        "a flag"
        ("--flag",)

        def __call__(self):  # pragma: no cover
            return 0

    class Root(duho.Cli):
        """Root."""

        _help_formatter_ = duho.DefaultsFormatter
        _subcommands_ = [Sub]

        def __call__(self):  # pragma: no cover
            return 0

    parser = Root._parser_()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    sub_parser = subparsers_action.choices["sub"]
    assert "(default: x)" in sub_parser.format_help()


def test_formatters_are_helpformatter_subclasses():
    for f in (
        duho.DefaultsFormatter,
        duho.ColorHelpFormatter,
        duho.ColorDefaultsFormatter,
    ):
        assert issubclass(f, argparse.HelpFormatter)


def test_default_help_unchanged_without_opt_in():
    """A class that does not set _help_formatter_ gets plain argparse help."""

    class Plain(Args):
        region: str = "us-east"
        ("--region",)

    assert "(default:" not in Plain._parser_().format_help()
