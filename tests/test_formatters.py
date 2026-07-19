"""Tests for the opt-in help formatters (F8).

``DefaultsFormatter`` appends ``(default: X)`` (skipping None/""/False);
``ColorHelpFormatter`` adds ANSI (gated on TTY / NO_COLOR / FORCE_COLOR);
``ColorDefaultsFormatter`` composes both. Assertions are loose substrings, not
golden files, because argparse formatter internals vary across 3.9-3.13.
"""

import argparse

import pytest

import duho
from duho import Args


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
    assert "(default: us-east)" in help_text
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
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    sub_parser = subparsers_action.choices["Sub"]
    assert "(default: x)" in sub_parser.format_help()


def test_formatters_are_helpformatter_subclasses():
    for f in (duho.DefaultsFormatter, duho.ColorHelpFormatter, duho.ColorDefaultsFormatter):
        assert issubclass(f, argparse.HelpFormatter)


def test_default_help_unchanged_without_opt_in():
    """A class that does not set _help_formatter_ gets plain argparse help."""

    class Plain(Args):
        region: str = "us-east"
        ("--region",)

    assert "(default:" not in Plain._parser_().format_help()
