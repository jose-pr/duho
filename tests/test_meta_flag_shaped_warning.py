"""A flag given to ``Meta`` by position is reported, never rejected or moved."""

import logging

import pytest

from duho import Meta
from duho.args._meta import _META_UNSET


def _records(caplog):
    return [r for r in caplog.records if r.name == "duho.args"]


def test_two_flags_by_position_warn_once_and_are_still_used(caplog):
    with caplog.at_level(logging.WARNING, logger="duho.args"):
        meta = Meta("-n", "--name")
    (record,) = _records(caplog)
    message = record.getMessage()
    assert "help='-n'" in message and "env='--name'" in message
    assert "flags=(...)" in message and "help, env" in message
    assert meta.help == "-n"
    assert meta.env == "--name"
    assert meta.flags is _META_UNSET


@pytest.mark.parametrize("help_text", ["--dry-run", "-v", "-x_y"])
def test_flag_shaped_help_warns(caplog, help_text):
    with caplog.at_level(logging.WARNING, logger="duho.args"):
        meta = Meta(help=help_text)
    (record,) = _records(caplog)
    assert repr(help_text) in record.getMessage()
    assert meta.help == help_text


def test_dash_led_env_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="duho.args"):
        Meta(env="-X")
    (record,) = _records(caplog)
    assert "env='-X'" in record.getMessage()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"help": "- optional"},
        {"help": "--  note"},
        {"help": "the port to listen on"},
        {"help": "-"},
        {"help": ""},
        {"help": 3},
        {"env": "APP_PORT"},
        {"env": 3},
        {"flags": ("-n", "--name")},
        {},
    ],
)
def test_ordinary_values_do_not_warn(caplog, kwargs):
    with caplog.at_level(logging.WARNING, logger="duho.args"):
        Meta(**kwargs)
    assert _records(caplog) == []
