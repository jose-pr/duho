"""A module whose own ``main`` is a wrapper that fails ownership is reported, not silently dropped."""

import logging

from duho.discovery import discover_commands

_NO_WRAPS_HELPER = """\
def timed(fn):
    def inner(*args, **kwargs):
        return fn(*args, **kwargs)
    return inner
"""

_WRAPS_HELPER = """\
import functools

def timed(fn):
    @functools.wraps(fn)
    def inner(*args, **kwargs):
        return fn(*args, **kwargs)
    return inner
"""

_WRAPPED_MAIN = "from _helpers import timed\n\n@timed\ndef main(args):\n    return 0\n"


def _write(tmp_path, helper, body):
    (tmp_path / "_helpers.py").write_text(helper)
    (tmp_path / "wrapped.py").write_text(body)


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_wrapper_without_wraps_logs_a_warning_naming_all(tmp_path, caplog):
    _write(tmp_path, _NO_WRAPS_HELPER, _WRAPPED_MAIN)
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        commands = discover_commands(tmp_path)
    assert commands == []
    messages = _warnings(caplog)
    assert any("'main'" in m and "__all__" in m for m in messages), messages


def test_wrapper_with_wrapped_attribute_from_elsewhere_warns(tmp_path, caplog):
    helper = (
        "def timed(fn):\n"
        "    def inner(*args, **kwargs):\n"
        "        return fn(*args, **kwargs)\n"
        "    inner.__wrapped__ = fn\n"
        "    inner.__qualname__ = 'inner'\n"
        "    inner.__module__ = 'elsewhere'\n"
        "    return inner\n"
    )
    _write(tmp_path, helper, _WRAPPED_MAIN)
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        assert discover_commands(tmp_path) == []
    assert any("__all__" in m for m in _warnings(caplog))


def test_functools_wraps_main_is_a_command_without_a_warning(tmp_path, caplog):
    # wraps() copies __module__ from the wrapped function, so it passes the
    # ownership test.
    _write(tmp_path, _WRAPS_HELPER, _WRAPPED_MAIN)
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        commands = discover_commands(tmp_path)
    assert len(commands) == 1
    assert not _warnings(caplog)


def test_all_listing_accepts_the_wrapped_main_without_a_warning(tmp_path, caplog):
    body = (
        "from _helpers import timed\n__all__ = ['main']\n\n"
        "@timed\ndef main(args):\n    return 0\n"
    )
    _write(tmp_path, _NO_WRAPS_HELPER, body)
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        commands = discover_commands(tmp_path)
    assert len(commands) == 1
    assert not _warnings(caplog)


def test_imported_run_logs_nothing(tmp_path, caplog):
    (tmp_path / "helper.py").write_text("from subprocess import run\n")
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        assert discover_commands(tmp_path) == []
    assert not _warnings(caplog)


def test_helpers_only_module_stays_silent(tmp_path, caplog):
    (tmp_path / "plain.py").write_text("X = 1\n")
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        assert discover_commands(tmp_path) == []
    assert not _warnings(caplog)
