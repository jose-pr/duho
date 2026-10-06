"""Every entry point layers the class's own `_config_` and `NS(env=...)`: a
bare parser from `duho.parser`/`cls._parser_()` agrees with `duho.parse`."""

import json

import pytest

import duho
from duho import Arg, Args, NS


@pytest.fixture
def app_cls(tmp_path, monkeypatch):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"port": 9000, "debug": True}))
    monkeypatch.setenv("DUHO_T_ENTRY_HOST", "from-env")

    class EntryLayerApp(Args):
        _config_ = str(cfg)
        port: int = 1
        host: Arg[str, NS(env="DUHO_T_ENTRY_HOST")] = "localhost"
        debug: bool = False

    return EntryLayerApp


def _triple(result):
    return (result.port, result.host, result.debug)


def test_parse_applies_config_and_env(app_cls):
    assert _triple(duho.parse(app_cls, [])) == (9000, "from-env", True)


def test_duho_parser_applies_config_and_env(app_cls):
    assert _triple(duho.parser(app_cls).parse_args([])) == (9000, "from-env", True)


def test_class_parser_applies_config_and_env(app_cls):
    assert _triple(app_cls._parser_().parse_args([])) == (9000, "from-env", True)


def test_parse_globals_applies_config_and_env(app_cls):
    assert _triple(duho.parse_globals(app_cls, [])) == (9000, "from-env", True)


def test_bare_parser_cli_still_wins(app_cls):
    parsed = app_cls._parser_().parse_args(["--port", "5", "--no-debug"])
    assert (parsed.port, parsed.debug) == (5, False)


def test_bare_parser_value_sources(app_cls):
    parsed = app_cls._parser_().parse_args([])
    assert duho.value_sources(parsed)["port"] == "config"


def test_bare_parser_without_config_file_is_unaffected(tmp_path):
    class NoFile(Args):
        _config_ = str(tmp_path / "missing.toml")
        port: int = 1

    assert NoFile._parser_().parse_args([]).port == 1
