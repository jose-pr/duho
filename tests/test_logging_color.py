"""Colored custom log levels via add_logging_level(color=...) (Plan 03 T6).

Covers the named-color resolution path (M9): a single ``"red"`` spec and the
compound ``"red+white"`` fore+back spec both resolve through colorama, and a
missing colorama degrades to plain (empty) output without crashing.
"""

import logging

import pytest

import duho.logging as duho_logging
from duho import add_logging_level
from duho.logging import DefaultFormatter, _getcolor


def _make_record(level, name="COLORLVL"):
    return logging.LogRecord(
        name="t", level=level, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )


def test_getcolor_named_single_resolves_with_colorama():
    colorama = pytest.importorskip("colorama")
    ansi = _getcolor("red")
    assert ansi == colorama.Fore.RED


def test_getcolor_compound_fore_back_resolves():
    colorama = pytest.importorskip("colorama")
    ansi = _getcolor("red+white")
    # Both the fore and back parts resolve (M9: the '+' form used to be returned
    # verbatim because color.isalpha() rejected it).
    assert ansi == colorama.Fore.RED + colorama.Back.WHITE


def test_getcolor_missing_colorama_returns_empty(monkeypatch):
    monkeypatch.setattr(duho_logging, "_resolve_colorama", lambda: None)
    assert _getcolor("red") == ""
    # A compound spec also degrades to empty, never the raw name.
    assert _getcolor("red+white") == ""


def test_add_logging_level_colors_levelname():
    colorama = pytest.importorskip("colorama")
    level = logging.DEBUG - 3
    add_logging_level("T6COLORED", level, force=True, color="red")
    assert DefaultFormatter.COLORS[level] == colorama.Fore.RED
    formatted = DefaultFormatter("%(levelname)s").format(_make_record(level))
    assert colorama.Fore.RED in formatted
    assert DefaultFormatter.RESET_ALL in formatted


def test_add_logging_level_compound_color():
    colorama = pytest.importorskip("colorama")
    level = logging.DEBUG - 4
    add_logging_level("T6COMPOUND", level, force=True, color="red+white")
    assert DefaultFormatter.COLORS[level] == colorama.Fore.RED + colorama.Back.WHITE


def test_add_logging_level_missing_colorama_no_crash(monkeypatch):
    monkeypatch.setattr(duho_logging, "_resolve_colorama", lambda: None)
    level = logging.DEBUG - 6
    # Must not raise even though a named color was requested.
    add_logging_level("T6PLAIN", level, force=True, color="red")
    assert DefaultFormatter.COLORS[level] == ""
    formatted = DefaultFormatter("%(levelname)s").format(_make_record(level))
    # No ANSI wrapping when the color resolved to empty.
    assert "\033[" not in formatted
