"""A module whose own ``main`` fails the ownership test is reported, not silently dropped."""

import logging

from duho.discovery import discover_commands

_HELPER = """\
def timed(fn):
    def inner(*args, **kwargs):
        return fn(*args, **kwargs)
    return inner
"""


def _write_pair(tmp_path, body):
    (tmp_path / "_helpers.py").write_text(_HELPER)
    (tmp_path / "wrapped.py").write_text(body)


def test_decorator_wrapped_main_logs_a_warning_naming_all(tmp_path, caplog):
    _write_pair(
        tmp_path,
        "from _helpers import timed\n\n@timed\ndef main(args):\n    return 0\n",
    )
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        commands = discover_commands(tmp_path)
    assert commands == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("'main'" in m and "__all__" in m for m in messages), messages


def test_all_listing_accepts_the_wrapped_main_without_a_warning(tmp_path, caplog):
    _write_pair(
        tmp_path,
        "from _helpers import timed\n__all__ = ['main']\n\n"
        "@timed\ndef main(args):\n    return 0\n",
    )
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        commands = discover_commands(tmp_path)
    assert len(commands) == 1
    assert not caplog.records


def test_helpers_only_module_stays_silent(tmp_path, caplog):
    (tmp_path / "plain.py").write_text("X = 1\n")
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        assert discover_commands(tmp_path) == []
    assert not caplog.records
