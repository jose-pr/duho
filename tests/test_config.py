"""Tests for config-file + env-var default layers.

Precedence contract (locked): CLI > env > config file > class default.
A value from any layer un-requires the corresponding field for free (via
parser.set_defaults()), which is exercised explicitly below.
"""

import enum
import pathlib
import sys
import typing as _ty

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


@pytest.mark.requires_toml
def test_config_overrides_class_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DUHO_TEST_HOST", raising=False)
    cfg = tmp_path / "duho.toml"
    cfg.write_text('host = "from-config"\nport = 9000\n')
    result = duho.parse(ConfigArgs, [], config=cfg)
    assert result.host == "from-config"
    assert result.port == 9000


@pytest.mark.requires_toml
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


@pytest.mark.requires_toml
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


@pytest.mark.requires_toml
def test_subcommand_config_table_scoped_to_subcommand(tmp_path):
    """A `[install]` table (subcommand name = the kebab-case of its class
    name, since it declares no own `_parsername_`) applies only to the
    install subcommand's fields, not to the root App's fields."""
    cfg = tmp_path / "duho.toml"
    cfg.write_text("verbose = true\n" "\n" "[install]\n" 'target = "from-config"\n')
    result = duho.parse(App, ["install"], config=cfg)
    assert result.target == "from-config"
    # Root-level key still applies via the top-level table.
    assert result.verbose is True


@pytest.mark.requires_toml
def test_subcommand_config_table_does_not_leak_to_root(tmp_path):
    cfg = tmp_path / "duho.toml"
    cfg.write_text("[install]\n" 'target = "from-config"\n')
    result = duho.parse(App, ["install"], config=cfg)
    assert result.target == "from-config"


@pytest.mark.requires_toml
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


class _FlagNoDefault(Args):
    flag: bool
    "Flag"
    ("--flag",)


def test_value_sources_store_true_default_not_cli():
    r = duho.parse(_FlagNoDefault, [])
    assert duho.value_sources(r)["flag"] == "default"
    r2 = duho.parse(_FlagNoDefault, ["--flag"])
    assert duho.value_sources(r2)["flag"] == "cli"


class _ValueSourcesSub(duho.Cmd):
    target: str = "dev"
    "Target"
    ("--target",)

    def __call__(self):
        return 0


class _ValueSourcesRoot(duho.Cli):
    _subcommands_ = [_ValueSourcesSub]

    def __call__(self):
        return 0


@pytest.mark.requires_toml
def test_value_sources_subcommand_config(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[value-sources-sub]\ntarget = "prod"\n')
    result = duho.parse(_ValueSourcesRoot, ["value-sources-sub"], config=cfg)
    assert result.target == "prod"
    assert duho.value_sources(result)["target"] == "config"


class _ChildOverrideSub(duho.Cmd):
    verbose: int = 3
    "Verbosity"
    ("--verbose",)

    def __call__(self):
        return 0


class _ChildOverrideRoot(duho.Cli):
    verbose: int = 0
    "Verbosity"
    ("--verbose",)

    _subcommands_ = [_ChildOverrideSub]

    def __call__(self):
        return 0


def test_child_override_default_wins():
    """A subcommand's own default for a field it re-declares wins over the
    root's default for that same field name."""
    r = duho.parse(_ChildOverrideRoot, ["child-override-sub"])
    assert r.verbose == 3


def test_value_sources_unavailable_returns_empty_dict():
    class Untouched(Args):
        x: str = "y"
        ("--x",)

    instance = Untouched(x="y")
    assert duho.value_sources(instance) == {}


@pytest.mark.requires_toml
def test_parse_config_kwarg_overrides_class_config_attr(tmp_path):
    """An explicit ``config=`` to ``duho.parse`` beats a class-level
    ``_config_`` -- both point at REAL files with DIFFERENT values here, so
    the precedence documented in docs/guide/config.md is actually exercised."""

    class BothConfigured(Args):
        _config_ = None
        host: str = "localhost"
        ("--host",)

    BothConfigured._config_ = str(tmp_path / "class-attr.toml")
    (tmp_path / "class-attr.toml").write_text('host = "from-class-attr"\n')
    (tmp_path / "kwarg.toml").write_text('host = "from-kwarg"\n')

    result = duho.parse(BothConfigured, [], config=str(tmp_path / "kwarg.toml"))
    assert result.host == "from-kwarg"


@pytest.mark.requires_toml
def test_main_config_kwarg_overrides_class_config_attr(tmp_path):
    """Same precedence, through ``duho.main``."""

    class BothConfiguredCmd(Args):
        _config_ = None
        host: str = "localhost"
        ("--host",)

        def __call__(self):
            return self.host

    BothConfiguredCmd._config_ = str(tmp_path / "class-attr.toml")
    (tmp_path / "class-attr.toml").write_text('host = "from-class-attr"\n')
    (tmp_path / "kwarg.toml").write_text('host = "from-kwarg"\n')

    result = duho.main(
        BothConfiguredCmd, [], config=str(tmp_path / "kwarg.toml"), setup_logging=False
    )
    assert result == "from-kwarg"


class _ChoiceLayered(Args):
    """Literal/Choice fields backed by env and config."""

    mode: "Arg[str, NS(choices=('fast', 'slow'), env='DUHO_TEST_MODE')]" = "fast"
    ("--mode",)


def test_env_value_rejects_invalid_choice(monkeypatch, capsys):
    # A bad layered value is rejected the same as a bad CLI one -- usage text
    # + exit 2, with the SAME "invalid choice" wording the CLI itself gives
    # -- but the raw value is never echoed back (it could be a secret): the
    # message names the field/variable and the valid choices only.
    monkeypatch.setenv("DUHO_TEST_MODE", "banana")
    with pytest.raises(SystemExit) as exc:
        duho.parse(_ChoiceLayered, [])
    monkeypatch.delenv("DUHO_TEST_MODE", raising=False)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "environment variable 'DUHO_TEST_MODE' for field 'mode'" in err
    assert "invalid choice (choose from 'fast', 'slow')" in err
    assert "banana" not in err
    assert "usage:" in err


@pytest.mark.requires_toml
def test_config_value_rejects_invalid_choice(tmp_path, capsys):
    cfg = tmp_path / "duho.toml"
    cfg.write_text('mode = "banana"\n')
    with pytest.raises(SystemExit) as exc:
        duho.parse(_ChoiceLayered, [], config=cfg)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "config value for field 'mode' on _ChoiceLayered" in err
    assert "invalid choice (choose from 'fast', 'slow')" in err
    assert "banana" not in err
    assert "usage:" in err


def test_valid_env_choice_still_works(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_MODE", "slow")
    result = duho.parse(_ChoiceLayered, [])
    monkeypatch.delenv("DUHO_TEST_MODE", raising=False)
    assert result.mode == "slow"


class _MsgLevel(enum.Enum):
    LOW = 1
    HIGH = 2


class _EnumLayered(Args):
    """A bare Enum field backed by config."""

    level: "_MsgLevel" = _MsgLevel.LOW
    ("--level",)


@pytest.mark.requires_toml
def test_config_value_rejects_bad_enum_names_the_enum_not_a_bare_factory(
    tmp_path, capsys
):
    # Previously this showed the internal factory function's own generic
    # name ("expected _factory") instead of the enum's.
    cfg = tmp_path / "duho.toml"
    cfg.write_text("level = true\n")
    with pytest.raises(SystemExit) as exc:
        duho.parse(_EnumLayered, [], config=cfg)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "expected _MsgLevel" in err
    assert "_factory" not in err


class _UnionLayered(Args):
    """A multi-member Union field backed by config."""

    amount: "_ty.Union[int, float]" = 0
    ("--amount",)


@pytest.mark.requires_toml
def test_config_value_rejects_bad_union_names_its_members_not_a_bare_factory(
    tmp_path, capsys
):
    # Previously this showed the internal composed-factory function's own
    # generic name ("expected factory") instead of naming its members.
    cfg = tmp_path / "duho.toml"
    cfg.write_text("amount = true\n")
    with pytest.raises(SystemExit) as exc:
        duho.parse(_UnionLayered, [], config=cfg)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "expected int or float" in err
    assert "expected factory" not in err


class _ConfigFileMayBeMissing(Args):
    """A class-level ``_config_`` pointing at a not-yet-created file."""

    target: str = "dev"
    ("--target",)


def test_missing_class_level_config_is_skipped_not_a_crash(tmp_path):
    missing = tmp_path / "does" / "not" / "exist.toml"
    _ConfigFileMayBeMissing._config_ = str(missing)
    try:
        assert not missing.exists()
        result = duho.parse(_ConfigFileMayBeMissing, [])
        assert result.target == "dev"
        # --help must not crash either.
        with pytest.raises(SystemExit) as exc:
            duho.parse(_ConfigFileMayBeMissing, ["--help"])
        assert exc.value.code == 0
    finally:
        _ConfigFileMayBeMissing._config_ = None


@pytest.mark.requires_toml
def test_explicit_missing_config_kwarg_still_raises(tmp_path):
    # An explicit `config=` is a deliberate request -- unlike a class-level
    # `_config_`, a missing file there stays a clear, surfaced error.
    missing = tmp_path / "nope.toml"
    with pytest.raises(FileNotFoundError):
        duho.parse(_ConfigFileMayBeMissing, [], config=missing)


def test_non_mapping_config_top_level_is_reported_with_a_clear_error(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text("[1, 2]")
    with pytest.raises(SystemExit) as excinfo:
        duho.parse(_ConfigFileMayBeMissing, [], config=cfg)
    assert excinfo.value.code == 2
    assert "must contain a table/object" in capsys.readouterr().err


def test_config_loader_returning_none_is_treated_as_empty(tmp_path):
    class WithLoader(Args):
        _config_ = "unused.yaml"
        _config_loader_ = staticmethod(lambda p: None)
        target: str = "dev"
        ("--target",)

    result = duho.parse(WithLoader, [])
    assert result.target == "dev"


class _EmptyEnvArgs(Args):
    """An env var set to the empty string."""

    paths: "Arg[list[str], NS(env='DUHO_TEST_PATHS')]" = []
    ("--paths",)

    port: "Arg[_ty.Optional[int], NS(env='DUHO_TEST_PORT')]" = None
    ("--port",)

    name: "Arg[str, NS(env='DUHO_TEST_NAME')]" = "default-name"
    ("--name",)


def test_empty_env_collection_treated_as_unset(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_PATHS", "")
    result = duho.parse(_EmptyEnvArgs, [])
    monkeypatch.delenv("DUHO_TEST_PATHS", raising=False)
    assert result.paths == []


def test_empty_env_optional_int_treated_as_unset(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_PORT", "")
    result = duho.parse(_EmptyEnvArgs, [])
    monkeypatch.delenv("DUHO_TEST_PORT", raising=False)
    assert result.port is None


def test_empty_env_str_field_keeps_empty_string(monkeypatch):
    monkeypatch.setenv("DUHO_TEST_NAME", "")
    result = duho.parse(_EmptyEnvArgs, [])
    monkeypatch.delenv("DUHO_TEST_NAME", raising=False)
    assert result.name == ""


@pytest.mark.skipif(
    sys.version_info >= (3, 11),
    reason="tomllib is always available on 3.11+; the fallback-missing path can't occur",
)
def test_missing_toml_backend_is_reported_with_a_clear_error(
    tmp_path, monkeypatch, capsys
):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("tomllib", "tomli"):
            raise ImportError(f"no module named {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    cfg = tmp_path / "duho.toml"
    cfg.write_text('host = "x"\n')

    with pytest.raises(SystemExit) as excinfo:
        duho.parse(ConfigArgs, [], config=cfg)
    assert excinfo.value.code == 2
    assert "tomli" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Layered bool env conversion
# --------------------------------------------------------------------------


class _BoolEnv(Args):
    dry: Arg[bool, NS(env="DUHO_A1_DRY")] = False
    "Dry run"
    ("--dry",)


def test_bool_env_false_string_is_false(monkeypatch):
    # A naive bool("false") is True; the layered converter must not do that.
    monkeypatch.setenv("DUHO_A1_DRY", "false")
    result = duho.parse(_BoolEnv, [])
    assert result.dry is False


def test_bool_env_zero_is_false(monkeypatch):
    monkeypatch.setenv("DUHO_A1_DRY", "0")
    result = duho.parse(_BoolEnv, [])
    assert result.dry is False


def test_bool_env_one_is_true(monkeypatch):
    monkeypatch.setenv("DUHO_A1_DRY", "1")
    result = duho.parse(_BoolEnv, [])
    assert result.dry is True


def test_bool_env_garbage_reports_usage_error(monkeypatch, capsys):
    # A bad env value is reported the same way a bad CLI value would be --
    # usage text + exit 2, never a raw traceback.
    monkeypatch.setenv("DUHO_A1_DRY", "banana")
    with pytest.raises(SystemExit) as exc:
        duho.parse(_BoolEnv, [])
    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "DUHO_A1_DRY" in stderr
    assert "dry" in stderr
    assert "usage:" in stderr


# --------------------------------------------------------------------------
# Layered collection env conversion
# --------------------------------------------------------------------------


class _ListEnv(Args):
    files: Arg[list[str], NS(env="DUHO_A1_FILES")]
    "Files"
    ("--files",)


class _SetEnv(Args):
    tags: Arg[set[str], NS(env="DUHO_A1_TAGS")]
    "Tags"
    ("--tags",)


def test_list_env_single_element_wrapped(monkeypatch):
    # A naive element factory (str) run on the whole string would give the
    # scalar "a.txt" instead of a one-element list.
    monkeypatch.setenv("DUHO_A1_FILES", "a.txt")
    result = duho.parse(_ListEnv, [])
    assert result.files == ["a.txt"]


def test_set_env_single_element_wrapped(monkeypatch):
    monkeypatch.setenv("DUHO_A1_TAGS", "x")
    result = duho.parse(_SetEnv, [])
    assert result.tags == {"x"}


# --------------------------------------------------------------------------
# Non-string config-layer conversion
# --------------------------------------------------------------------------


class _TimeoutArgs(Args):
    timeout: float = 10.0
    "Timeout"
    ("--timeout",)

    paths: list[pathlib.Path]
    "Paths"
    ("--paths",)


@pytest.mark.requires_toml
def test_config_int_becomes_float(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("timeout = 30\n")
    result = duho.parse(_TimeoutArgs, [], config=cfg)
    assert result.timeout == 30.0
    assert isinstance(result.timeout, float)


@pytest.mark.requires_toml
def test_config_list_of_paths(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('paths = ["a", "b"]\n')
    result = duho.parse(_TimeoutArgs, [], config=cfg)
    assert result.paths == [pathlib.Path("a"), pathlib.Path("b")]


# --------------------------------------------------------------------------
# A layered CLI flag declared on the root survives past subcommand dispatch
# --------------------------------------------------------------------------


class _SubLayeredVerbose(Args):
    verbose: Arg[int, NS(env="DUHO_A2_VERBOSE")] = 0
    "Verbosity"
    ("--verbose",)

    def __call__(self):
        return 0


class _RootLayeredVerbose(duho.Cli):
    verbose: Arg[int, NS(env="DUHO_A2_VERBOSE")] = 0
    "Verbosity"
    ("--verbose",)

    _subcommands_ = [_SubLayeredVerbose]

    def __call__(self):
        return 0


def test_cli_flag_before_subcommand_survives_env(monkeypatch):
    monkeypatch.setenv("DUHO_A2_VERBOSE", "5")
    result = duho.parse(_RootLayeredVerbose, ["--verbose", "3", "sub-layered-verbose"])
    assert result.verbose == 3


def test_env_applies_when_no_cli_flag(monkeypatch):
    monkeypatch.setenv("DUHO_A2_VERBOSE", "5")
    result = duho.parse(_RootLayeredVerbose, ["sub-layered-verbose"])
    assert result.verbose == 5


# --------------------------------------------------------------------------
# A layered value un-requires a positional the same way it un-requires a flag
# --------------------------------------------------------------------------


class _PositionalEnv(Args):
    name: Arg[str, NS(env="DUHO_A3_NAME")]
    "Name"
    ("name",)


def test_positional_env_makes_optional(monkeypatch):
    monkeypatch.setenv("DUHO_A3_NAME", "from-env")
    result = duho.parse(_PositionalEnv, [])
    assert result.name == "from-env"


def test_positional_no_env_still_required(monkeypatch):
    monkeypatch.delenv("DUHO_A3_NAME", raising=False)
    with pytest.raises(SystemExit):
        duho.parse(_PositionalEnv, [])
