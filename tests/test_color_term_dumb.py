"""``TERM=dumb`` turns colour off even on a TTY; an explicit FORCE_COLOR still wins."""

import pytest

from duho.formatters import _color_enabled


class _TTY:
    def isatty(self):
        return True


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def test_tty_gets_colour_by_default(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    assert _color_enabled(_TTY()) is True


def test_term_dumb_disables_colour_on_a_tty(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    assert _color_enabled(_TTY()) is False


def test_force_color_still_wins_over_term_dumb(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _color_enabled(_TTY()) is True


def test_empty_no_color_keeps_meaning_set(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "")
    assert _color_enabled(_TTY()) is False
