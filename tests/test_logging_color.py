"""Colored custom log levels via add_logging_level(color=...).

Covers the named-color resolution path: a single ``"red"`` spec and the
compound ``"red+white"`` fore+back spec both resolve through colorama, and a
missing colorama degrades to plain (empty) output without crashing.

Also covers colorama's ``"..._EX"`` bright color names, and
``init_stderr_logging`` gating ANSI on NO_COLOR/FORCE_COLOR/TTY the same way
the ``--help`` formatters do, instead of always emitting escape codes.
"""

import io
import logging

import pytest

import duho.logging as duho_logging
from duho import add_logging_level, init_stderr_logging
from duho.logging import DefaultFormatter, _getcolor


class _FakeStream(io.StringIO):
    """A stream whose ``isatty()`` is controllable, unlike a plain StringIO."""

    def __init__(self, isatty=True):
        super().__init__()
        self._isatty = isatty

    def isatty(self):
        return self._isatty


def _make_record(level, name="COLORLVL"):
    return logging.LogRecord(
        name="t",
        level=level,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )


def test_getcolor_named_single_resolves_with_colorama():
    colorama = pytest.importorskip("colorama")
    ansi = _getcolor("red")
    assert ansi == colorama.Fore.RED


def test_getcolor_compound_fore_back_resolves():
    colorama = pytest.importorskip("colorama")
    ansi = _getcolor("red+white")
    # Both the fore and back parts resolve (the '+' form must not be returned
    # verbatim just because color.isalpha() rejects it).
    assert ansi == colorama.Fore.RED + colorama.Back.WHITE


def test_getcolor_missing_colorama_returns_empty(monkeypatch):
    monkeypatch.setattr(duho_logging, "_resolve_colorama", lambda: None)
    assert _getcolor("red") == ""
    # A compound spec also degrades to empty, never the raw name.
    assert _getcolor("red+white") == ""


def test_getcolor_ansi_escape_passes_through_without_colorama(monkeypatch):
    """An already-ANSI-formatted color spec (not a named color) passes
    through unchanged, even when colorama is unavailable -- it needs no
    name lookup at all."""
    monkeypatch.setattr(duho_logging, "_resolve_colorama", lambda: None)
    assert _getcolor("\033[31m") == "\033[31m"


def test_resolve_colorama_handles_a_genuinely_missing_module(monkeypatch):
    """Exercise the real ``except ImportError`` branch in ``_resolve_colorama``.

    The test above bypasses it entirely by monkeypatching the whole function;
    here the actual ``import colorama`` statement is made to fail (a ``None``
    entry in ``sys.modules`` forces ``ImportError``), so the fallback that
    caches ``None`` -- instead of crashing or leaving the not-yet-probed
    sentinel behind -- is what's actually running.
    """
    import sys

    monkeypatch.setitem(sys.modules, "colorama", None)
    monkeypatch.setattr(duho_logging, "_color", False)  # not-yet-probed sentinel

    resolved = duho_logging._resolve_colorama()

    assert resolved is None
    assert duho_logging._color is None  # probed once and cached, not re-probed
    assert _getcolor("red") == ""


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


def test_getcolor_bright_ex_name_resolves_with_colorama():
    """colorama's "_EX" bright variants must resolve as NAMES, not be
    passed through verbatim as literal text (they contain "_", which the old
    `color.isalpha()` check rejected)."""
    colorama = pytest.importorskip("colorama")
    assert _getcolor("lightred_ex") == colorama.Fore.LIGHTRED_EX


def test_getcolor_unresolvable_name_is_empty_not_verbatim():
    pytest.importorskip("colorama")
    assert _getcolor("not_a_real_color_ex") == ""


def _cleanup_logger(name):
    logger = logging.getLogger(name)
    logger.handlers[:] = []
    logger.setLevel(logging.NOTSET)


def test_init_stderr_logging_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("sys.stderr", _FakeStream(isatty=True))
    try:
        logger = init_stderr_logging("t_c010_no_color")
        rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "hi", (), None)
        formatted = logger.handlers[-1].format(rec)
        assert "\033[" not in formatted
    finally:
        _cleanup_logger("t_c010_no_color")


def test_init_stderr_logging_respects_force_color_on_a_non_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr("sys.stderr", _FakeStream(isatty=False))
    try:
        logger = init_stderr_logging("t_c010_force_color")
        rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "hi", (), None)
        formatted = logger.handlers[-1].format(rec)
        assert "\033[" in formatted
    finally:
        _cleanup_logger("t_c010_force_color")


def test_init_stderr_logging_no_color_on_a_plain_non_tty_stream(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr("sys.stderr", _FakeStream(isatty=False))
    try:
        logger = init_stderr_logging("t_c010_plain")
        rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "hi", (), None)
        formatted = logger.handlers[-1].format(rec)
        assert "\033[" not in formatted
    finally:
        _cleanup_logger("t_c010_plain")


def test_init_stderr_logging_fixes_the_windows_console_when_color_is_on(monkeypatch):
    colorama = pytest.importorskip("colorama")
    calls = []
    monkeypatch.setattr(colorama, "just_fix_windows_console", lambda: calls.append(1))
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setattr("sys.stderr", _FakeStream(isatty=False))
    try:
        init_stderr_logging("t_c010_win_console")
        assert calls == [1]
    finally:
        _cleanup_logger("t_c010_win_console")
