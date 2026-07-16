"""Tests for config-file + env-var default layers.

Precedence contract (locked): CLI > env > config file > class default.
A value from any layer un-requires the corresponding field for free (via
parser.set_defaults()), which is exercised explicitly below.
"""

import sys

import pytest

import duho
from duho import NS, Arg, Args


class EnvArgs(Args):
    """A field with an env-var default."""

    token: Arg[str, NS(env="DUHO_TEST_TOKEN")] = "class-default"
    "Auth token"
    ("--token",)


def test_env_overrides_class_default(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_TOKEN", "from-env")
    result = duho.parse(EnvArgs, [])
    assert result.token == "from-env"


def test_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_TOKEN", "from-env")
    result = duho.parse(EnvArgs, ["--token", "from-cli"])
    assert result.token == "from-cli"


def test_no_env_falls_back_to_class_default(monkeypatch):
    monkeypatch.delenv("DUHO_TEST_TOKEN", raising=False)
    result = duho.parse(EnvArgs, [])
    assert result.token == "class-default"


class ConfigArgs(Args):
    """Fields sourced from a config file."""

    host: Arg[str, NS(env="DUHO_TEST_HOST")] = "localhost"
    "Server host"
    ("--host",)

    port: int = 8000
    "Server port"
    ("--port",)


def test_config_overrides_class_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DUHO_TEST_HOST", raising=False)
    cfg = tmp_path / "duho.toml"
    cfg.write_text('host = "from-config"\nport = 9000\n')
    result = duho.parse(ConfigArgs, [], config=cfg)
    assert result.host == "from-config"
    assert result.port == 9000


def test_full_four_layer_ladder(tmp_path, monkeypatch):
    """config < env < CLI, all in one test."""
    cfg = tmp_path / "duho.toml"
    cfg.write_text('host = "from-config"\nport = 9000\n')

    # Layer 1: class default only.
    monkeypatch.delenv("DUHO_TEST_HOST", raising=False)
    result = duho.parse(ConfigArgs, [])
    assert result.host == "localhost"
    assert result.port == 8000

    # Layer 2: config overrides class default.
    result = duho.parse(ConfigArgs, [], config=cfg)
    assert result.host == "from-config"
    assert result.port == 9000

    # Layer 3: env overrides config.
    monkeypatch.setenv("DUHO_TEST_HOST", "from-env")
    result = duho.parse(ConfigArgs, [], config=cfg)
    assert result.host == "from-env"
    assert result.port == 9000  # config still wins over class default here

    # Layer 4: CLI overrides env (and config).
    result = duho.parse(ConfigArgs, ["--host", "from-cli", "--port", "1"], config=cfg)
    assert result.host == "from-cli"
    assert result.port == 1

    monkeypatch.delenv("DUHO_TEST_HOST", raising=False)


class RequiredByConfig(Args):
    """A field with NO class default, sourced only from config."""

    name: str
    "Required, but suppliable via config"
    ("--name",)


def test_required_less_by_config_layer(tmp_path):
    """A required field (no class default) supplied only by config must NOT
    raise SystemExit when omitted from the CLI."""
    cfg = tmp_path / "duho.toml"
    cfg.write_text('name = "from-config"\n')
    result = duho.parse(RequiredByConfig, [], config=cfg)
    assert result.name == "from-config"


def test_required_still_enforced_without_any_layer():
    with pytest.raises(SystemExit):
        duho.parse(RequiredByConfig, [])


class Install(Args):
    """Subcommand: install."""

    target: str = "default-target"
    "Install target"
    ("--target",)

    def __call__(self):
        return 0


class App(Args):
    """Root app with an install subcommand."""

    _subcommands_ = [Install]

    verbose: bool = False
    "Verbose output"
    ("--verbose",)

    def __call__(self):
        return 0


def test_subcommand_config_table_scoped_to_subcommand(tmp_path):
    """A `[Install]` table (subcommand name = its _parsername_, which
    defaults to the class name) applies only to the Install subcommand's
    fields, not to the root App's fields."""
    cfg = tmp_path / "duho.toml"
    cfg.write_text(
        "verbose = true\n"
        "\n"
        "[Install]\n"
        'target = "from-config"\n'
    )
    result = duho.parse(App, ["Install"], config=cfg)
    assert result.target == "from-config"
    # Root-level key still applies via the top-level table.
    assert result.verbose is True


def test_subcommand_config_table_does_not_leak_to_root(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text(
        "[Install]\n"
        'target = "from-config"\n'
    )
    result = duho.parse(App, ["Install"], config=cfg)
    assert result.target == "from-config"


def test_value_sources_reports_correct_origin(tmp_path, monkeypatch):
    monkeypatch.delenv("DUHO_TEST_HOST", raising=False)
    cfg = tmp_path / "duho.toml"
    cfg.write_text('host = "from-config"\n')

    result = duho.parse(ConfigArgs, ["--port", "1"], config=cfg)
    sources = duho.value_sources(result)
    assert sources["host"] == "config"
    assert sources["port"] == "cli"


def test_value_sources_env_and_default(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_TOKEN", "from-env")
    result = duho.parse(EnvArgs, [])
    sources = duho.value_sources(result)
    assert sources["token"] == "env"
    monkeypatch.delenv("DUHO_TEST_TOKEN", raising=False)

    result = duho.parse(EnvArgs, [])
    sources = duho.value_sources(result)
    assert sources["token"] == "default"


def test_value_sources_unavailable_returns_empty_dict():
    class Untouched(Args):
        x: str = "y"
        ("--x",)

    instance = Untouched(x="y")
    assert duho.value_sources(instance) == {}


@pytest.mark.skipif(
    sys.version_info >= (3, 11),
    reason="tomllib is always available on 3.11+; the fallback-missing path can't occur",
)
def test_missing_toml_backend_raises_clear_runtimeerror(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("tomllib", "tomli"):
            raise ImportError(f"no module named {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    cfg = tmp_path / "duho.toml"
    cfg.write_text('host = "x"\n')

    with pytest.raises(RuntimeError, match="tomli"):
        duho.parse(ConfigArgs, [], config=cfg)
