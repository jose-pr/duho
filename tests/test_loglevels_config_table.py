"""`LoggingArgs.loglevels` read from a config table converts each value the
way `--loglevel NAME:LEVEL` does."""

import json
import logging

import pytest

import duho
from duho import Cmd, LoggingArgs


class _Levels(LoggingArgs, Cmd):
    def __call__(self):
        return 0


def _cfg(tmp_path, obj):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(obj))
    return str(path)


@pytest.mark.parametrize("value", [10, "debug", "DEBUG", "10"])
def test_table_value_converts_to_the_numeric_level(tmp_path, value):
    cfg = _cfg(tmp_path, {"loglevels": {"a.b": value}})
    assert duho.parse(_Levels, [], config=cfg).loglevels == {"a.b": 10}


def test_main_applies_a_numeric_table_level(tmp_path):
    cfg = _cfg(tmp_path, {"loglevels": {"duho_t_levels": 10}})
    try:
        assert duho.main(_Levels, [], config=cfg) == 0
        assert logging.getLogger("duho_t_levels").level == 10
    finally:
        logging.getLogger("duho_t_levels").setLevel(0)


def test_bad_table_level_is_reported_not_raised(tmp_path, capsys):
    cfg = _cfg(tmp_path, {"loglevels": {"a.b": "loud"}})
    with pytest.raises(SystemExit) as info:
        duho.parse(_Levels, [], config=cfg)
    assert info.value.code == 2
    assert "loglevels" in capsys.readouterr().err
