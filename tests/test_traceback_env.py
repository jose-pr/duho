"""Tests for the DUHO_TRACEBACK switch (duho.logging.traceback_enabled/log_exception).

The framework's resilient paths deliberately swallow exceptions (discovery skips a
bad command, a non-strict runpath step failure is logged and the run continues), so
the log line is the ONLY record of the failure. Without a traceback it says *that*
something broke but never *where*. ``DUHO_TRACEBACK=1`` turns every such site into a
full ``exc_info`` log.

Tests use real files under ``tmp_path`` (never ``-c``) and a ``caplog``-captured
record's ``exc_info`` to assert the traceback is actually attached, since a formatted
string alone would not distinguish "logged the message" from "logged the stack".
"""

import logging

import pytest

from duho.logging import TRACEBACK_ENV, log_exception, traceback_enabled


# --------------------------------------------------------------------------
# traceback_enabled: the env-var contract
# --------------------------------------------------------------------------


def test_disabled_when_unset(monkeypatch):
    monkeypatch.delenv(TRACEBACK_ENV, raising=False)
    assert traceback_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "anything"])
def test_enabled_for_truthy_values(monkeypatch, value):
    monkeypatch.setenv(TRACEBACK_ENV, value)
    assert traceback_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "FALSE", "no", "off", "  off  "])
def test_disabled_for_falsey_values(monkeypatch, value):
    """An explicitly-off value must not enable tracebacks (incl. an EMPTY value)."""
    monkeypatch.setenv(TRACEBACK_ENV, value)
    assert traceback_enabled() is False


def test_read_live_not_cached(monkeypatch):
    """The env is re-read per call, so flipping it mid-process takes effect."""
    monkeypatch.delenv(TRACEBACK_ENV, raising=False)
    assert traceback_enabled() is False
    monkeypatch.setenv(TRACEBACK_ENV, "1")
    assert traceback_enabled() is True
    monkeypatch.setenv(TRACEBACK_ENV, "0")
    assert traceback_enabled() is False


# --------------------------------------------------------------------------
# log_exception: attaches exc_info iff enabled
# --------------------------------------------------------------------------


def _emit(logger):
    try:
        raise ValueError("boom")
    except ValueError as exc:
        log_exception(logger, "it failed: %s", exc)


def test_log_exception_without_traceback(monkeypatch, caplog):
    monkeypatch.delenv(TRACEBACK_ENV, raising=False)
    logger = logging.getLogger("duho.test.notb")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        _emit(logger)
    (record,) = caplog.records
    assert record.getMessage() == "it failed: boom"
    assert record.exc_info is None
    assert "Traceback" not in caplog.text


def test_log_exception_with_traceback(monkeypatch, caplog):
    monkeypatch.setenv(TRACEBACK_ENV, "1")
    logger = logging.getLogger("duho.test.tb")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        _emit(logger)
    (record,) = caplog.records
    assert record.getMessage() == "it failed: boom"
    assert record.exc_info is not None
    assert record.exc_info[0] is ValueError
    # The formatted output carries the real stack, which is the whole point.
    assert "Traceback" in caplog.text
    assert "ValueError: boom" in caplog.text


def test_log_exception_honors_level(monkeypatch, caplog):
    """`level=` routes the record (discovery's skip sites log at WARNING)."""
    monkeypatch.setenv(TRACEBACK_ENV, "1")
    logger = logging.getLogger("duho.test.level")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        try:
            raise RuntimeError("nope")
        except RuntimeError as exc:
            log_exception(logger, "skipped: %s", exc, level=logging.WARNING)
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.exc_info is not None


# --------------------------------------------------------------------------
# End-to-end: a swallowed runpath step failure
# --------------------------------------------------------------------------


_FAILING_STEP = """\
def main(cmd):
    raise RuntimeError("step exploded")
"""


@pytest.fixture(autouse=True)
def _restore_providers():
    """Snapshot/restore the global provider registry (see test_runpath.py).

    ``import duho.runpath`` auto-registers its provider as an import side-effect,
    so without this the registration leaks into every later test in the session.
    """
    import duho.discovery as _discovery
    import duho.runpath as runpath

    saved = list(_discovery._PROVIDERS)
    saved_registered = runpath._REGISTERED
    # Register explicitly rather than relying on the import side-effect: by the
    # time this file runs, an earlier test module may already have imported
    # duho.runpath (consuming the one-shot side-effect) and then restored a
    # snapshot taken before it, leaving no provider registered.
    runpath.register()
    try:
        yield
    finally:
        _discovery._PROVIDERS[:] = saved
        runpath._REGISTERED = saved_registered


def _runpath_dir(tmp_path):
    directory = tmp_path / "steps"
    directory.mkdir()
    # `!strict` keeps the failure RESILIENT -- the run continues and the
    # exception is swallowed, which is exactly the case the traceback rescues.
    (directory / "01-boom;!strict.py").write_text(_FAILING_STEP, encoding="utf-8")
    return directory


def _run_steps(directory, caplog, monkeypatch, tb):
    from duho.discovery import CmdBuilder

    if tb:
        monkeypatch.setenv(TRACEBACK_ENV, "1")
    else:
        monkeypatch.delenv(TRACEBACK_ENV, raising=False)

    cmd_cls = CmdBuilder(directory.name, directory).command
    instance = cmd_cls()
    instance.rcopts = []
    with caplog.at_level(logging.ERROR, logger="duho.runpath"):
        assert instance() == 0  # resilient: the failing step does not abort
    return [r for r in caplog.records if "step exploded" in r.getMessage() or r.exc_info]


def test_runpath_step_failure_gains_traceback(tmp_path, caplog, monkeypatch):
    """The swallowed step failure carries a real stack under DUHO_TRACEBACK."""
    records = _run_steps(_runpath_dir(tmp_path), caplog, monkeypatch, tb=True)
    assert records, "expected the step failure to be logged"
    assert any(
        r.exc_info is not None and r.exc_info[0] is RuntimeError for r in records
    )


def test_runpath_step_failure_quiet_by_default(tmp_path, caplog, monkeypatch):
    """Default stays a one-line message -- no stack buried in ordinary CLI output."""
    records = _run_steps(_runpath_dir(tmp_path), caplog, monkeypatch, tb=False)
    assert records, "expected the step failure to be logged"
    assert all(r.exc_info is None for r in records)
