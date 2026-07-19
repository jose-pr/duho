"""Tests for JSON config files + the pluggable ``_config_loader_`` hook (F7).

A ``.json`` config path is parsed as JSON (stdlib, lazily imported); a class-level
``_config_loader_`` overrides format dispatch entirely so a user can plug any
format (e.g. YAML) without duho depending on it. JSON and TOML share the same
nested-dict shape, so subcommand tables layer identically.
"""

import json
import sys

import pytest

import duho
from duho import Arg, Cli, Cmd, NS


class JsonArgs(duho.Args):
    """Fields sourced from a JSON config file."""

    host: Arg[str, NS(env="DUHO_TEST_JHOST")] = "localhost"
    "Server host"
    ("--host",)

    port: int = 8000
    "Server port"
    ("--port",)


def test_json_top_level_overrides_class_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DUHO_TEST_JHOST", raising=False)
    cfg = tmp_path / "duho.json"
    cfg.write_text(json.dumps({"host": "from-json", "port": 9000}))
    result = duho.parse(JsonArgs, [], config=cfg)
    assert result.host == "from-json"
    assert result.port == 9000


def test_json_cli_still_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("DUHO_TEST_JHOST", raising=False)
    cfg = tmp_path / "duho.json"
    cfg.write_text(json.dumps({"host": "from-json", "port": 9000}))
    result = duho.parse(JsonArgs, ["--port", "1"], config=cfg)
    assert result.host == "from-json"
    assert result.port == 1


def test_bad_json_error_names_the_file(tmp_path):
    cfg = tmp_path / "broken.json"
    cfg.write_text("{ not valid json ]")
    with pytest.raises(ValueError) as excinfo:
        duho.parse(JsonArgs, [], config=cfg)
    assert "broken.json" in str(excinfo.value)


# -- subcommand nesting: JSON object under a subcommand name --------------------


class Deploy(Cmd):
    """Deploy."""

    region: str = "us-east"
    "target region"
    ("--region",)

    def __call__(self):  # pragma: no cover - not dispatched here
        return 0


class RootApp(Cli):
    """Root with a subcommand tree."""

    _subcommands_ = [Deploy]

    verbose: bool = False
    ("--verbose",)

    def __call__(self):  # pragma: no cover
        return 0


def test_json_subcommand_table_layers(tmp_path):
    cfg = tmp_path / "app.json"
    cfg.write_text(json.dumps({"verbose": True, "Deploy": {"region": "eu-west"}}))
    result = duho.parse(RootApp, ["Deploy"], config=cfg)
    assert result.region == "eu-west"
    assert result.verbose is True


# -- pluggable loader hook -----------------------------------------------------


_LOADER_CALLS = []


def _fake_yaml_loader(path):
    """Stand-in for a user's yaml.safe_load: records the path, returns a dict."""
    _LOADER_CALLS.append(path)
    return {"host": "from-loader", "port": 4242}


class LoaderArgs(duho.Args):
    """Uses a custom _config_loader_ instead of built-in dispatch."""

    _config_loader_ = staticmethod(_fake_yaml_loader)

    host: str = "localhost"
    ("--host",)
    port: int = 8000
    ("--port",)


def test_config_loader_hook_is_called_with_path(tmp_path):
    _LOADER_CALLS.clear()
    # Suffix is irrelevant when a loader is set: it wins over format dispatch.
    cfg = tmp_path / "settings.conf"
    cfg.write_text("ignored by the fake loader")
    result = duho.parse(LoaderArgs, [], config=cfg)
    assert result.host == "from-loader"
    assert result.port == 4242
    assert len(_LOADER_CALLS) == 1
    assert str(_LOADER_CALLS[0]).endswith("settings.conf")


def test_json_import_is_lazy():
    """Importing duho must not eagerly import the json module (F7 acceptance)."""
    import subprocess

    code = "import sys, duho; print('json' in sys.modules)"
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert out.strip() == "False"
