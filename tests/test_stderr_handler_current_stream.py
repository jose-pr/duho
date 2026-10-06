"""duho's stderr log handler writes to whatever ``sys.stderr`` is at emit time."""

import io
import logging
import sys

import pytest

from duho.logging import _STDERR_HANDLER_TAG, init_stderr_logging


@pytest.fixture
def logger():
    name = "duho_test_stderr_handler"
    log = init_stderr_logging(name, logging.INFO)
    log.propagate = False
    try:
        yield log
    finally:
        for handler in list(log.handlers):
            log.removeHandler(handler)


def test_follows_a_stderr_swapped_after_installation(logger, monkeypatch):
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    logger.warning("one")
    monkeypatch.setattr(sys, "stderr", second)
    logger.warning("two")
    assert "one" in first.getvalue() and "two" not in first.getvalue()
    assert "two" in second.getvalue() and "one" not in second.getvalue()


def test_installed_while_stderr_was_another_stream(monkeypatch):
    """The handler is not bound to the stream that was current when it was built."""
    early = io.StringIO()
    monkeypatch.setattr(sys, "stderr", early)
    log = init_stderr_logging("duho_test_stderr_handler_early", logging.INFO)
    log.propagate = False
    try:
        late = io.StringIO()
        monkeypatch.setattr(sys, "stderr", late)
        log.warning("hello")
        assert "hello" in late.getvalue()
        assert early.getvalue() == ""
    finally:
        for handler in list(log.handlers):
            log.removeHandler(handler)


def test_capsys_sees_the_records(logger, capsys):
    logger.warning("seen by capsys")
    assert "seen by capsys" in capsys.readouterr().err


def test_set_stream_still_pins_the_handler(logger, monkeypatch):
    handler = next(h for h in logger.handlers if getattr(h, _STDERR_HANDLER_TAG, False))
    pinned, other = io.StringIO(), io.StringIO()
    handler.setStream(pinned)
    monkeypatch.setattr(sys, "stderr", other)
    logger.warning("pinned")
    assert "pinned" in pinned.getvalue()
    assert other.getvalue() == ""
