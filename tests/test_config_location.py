"""``_config_env_`` and ``_config_field_``: where the config file path comes from."""

import json
from pathlib import Path
from typing import Optional

import pytest

import duho
from duho import Arg, Cli, Cmd, NS
from duho.testing import invoke


class _EnvOnly(Cmd):
    _config_env_ = "CFGLOC_FILE"

    level: str = "default"
    ("--level",)

    def __call__(self):
        print(self.level)
        return 0


class _FieldOnly(Cmd):
    _config_field_ = "config"

    config: Optional[str] = None
    ("--config",)

    level: str = "default"
    ("--level",)

    def __call__(self):
        print(self.level)
        return 0


class _FieldWithEnv(Cmd):
    _config_field_ = "config"

    config: "Arg[Optional[str], NS(env='CFGLOC_FIELD_ENV')]" = None
    ("--config",)

    level: str = "default"
    ("--level",)

    def __call__(self):
        print(self.level)
        return 0


class _Both(Cmd):
    _config_ = "unset"
    _config_env_ = "CFGLOC_FILE"
    _config_field_ = "config"

    config: Optional[str] = None
    ("--config",)

    level: str = "default"
    ("--level",)

    def __call__(self):
        print(self.level)
        return 0


class _BadField(Cmd):
    _config_field_ = "nope"

    level: str = "default"
    ("--level",)

    def __call__(self):
        return 0


class _Leaf(Cmd):
    def __call__(self):
        return 0


class _CliField(Cli):
    _parsername_ = "cfgloc-app"
    _config_field_ = "config"
    _subcommands_ = [_Leaf]

    config: Optional[str] = None
    ("--config",)

    level: str = "default"
    ("--level",)


def _cfg(tmp_path, name, level):
    path = tmp_path / name
    path.write_text(json.dumps({"level": level}))
    return str(path)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("CFGLOC_FILE", "CFGLOC_FIELD_ENV"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_off():
    assert duho.Cli._config_env_ is None
    assert duho.Cli._config_field_ is None


def test_env_variable_names_the_file(tmp_path):
    path = _cfg(tmp_path, "a.json", "from-env-file")
    result = invoke(_EnvOnly, env={"CFGLOC_FILE": path})
    assert result.stdout == "from-env-file\n"


def test_empty_env_variable_is_ignored(tmp_path):
    result = invoke(_EnvOnly, env={"CFGLOC_FILE": ""})
    assert result.status == 0 and result.stdout == "default\n"


def test_unset_env_variable_is_ignored():
    assert invoke(_EnvOnly).stdout == "default\n"


def test_env_file_outranks_class_config(tmp_path, monkeypatch):
    monkeypatch.setattr(_Both, "_config_", _cfg(tmp_path, "cls.json", "from-class"))
    env = {"CFGLOC_FILE": _cfg(tmp_path, "env.json", "from-env-file")}
    assert invoke(_Both, env=env).stdout == "from-env-file\n"
    assert invoke(_Both).stdout == "from-class\n"


def test_cli_field_names_the_file(tmp_path):
    path = _cfg(tmp_path, "f.json", "from-field")
    assert invoke(_FieldOnly, ["--config", path]).stdout == "from-field\n"


def test_field_given_by_its_own_env_var(tmp_path):
    path = _cfg(tmp_path, "f.json", "from-field-env")
    result = invoke(_FieldWithEnv, env={"CFGLOC_FIELD_ENV": path})
    assert result.stdout == "from-field-env\n"


def test_field_outranks_env_variable_and_class_config(tmp_path, monkeypatch):
    monkeypatch.setattr(_Both, "_config_", _cfg(tmp_path, "cls.json", "from-class"))
    env = {"CFGLOC_FILE": _cfg(tmp_path, "env.json", "from-env-file")}
    field = _cfg(tmp_path, "field.json", "from-field")
    assert invoke(_Both, ["--config", field], env=env).stdout == "from-field\n"


def test_explicit_config_argument_outranks_the_field(tmp_path):
    field = _cfg(tmp_path, "field.json", "from-field")
    explicit = _cfg(tmp_path, "explicit.json", "from-explicit")
    result = invoke(_FieldOnly, ["--config", field], config=explicit)
    assert result.stdout == "from-explicit\n"
    assert duho.parse(_FieldOnly, ["--config", field], config=explicit).level == (
        "from-explicit"
    )


def test_cli_value_still_outranks_the_file(tmp_path):
    path = _cfg(tmp_path, "f.json", "from-field")
    result = invoke(_FieldOnly, ["--config", path, "--level", "cli"])
    assert result.stdout == "cli\n"


def test_field_not_given_falls_back_to_env_then_class(tmp_path, monkeypatch):
    monkeypatch.setattr(_Both, "_config_", _cfg(tmp_path, "cls.json", "from-class"))
    assert invoke(_Both).stdout == "from-class\n"
    env = {"CFGLOC_FILE": _cfg(tmp_path, "env.json", "from-env-file")}
    assert invoke(_Both, env=env).stdout == "from-env-file\n"


def test_a_field_default_is_not_a_given_value(tmp_path, monkeypatch):
    class Defaulted(_FieldOnly):
        config: Optional[str] = _cfg(tmp_path, "d.json", "from-default")

    result = invoke(Defaulted)
    assert result.stdout == "default\n"


def test_parse_and_parse_globals_honour_the_field(tmp_path):
    path = _cfg(tmp_path, "f.json", "from-field")
    assert duho.parse(_FieldOnly, ["--config", path]).level == "from-field"
    assert duho.parse_globals(_FieldOnly, ["--config", path]).level == "from-field"


def test_root_with_subcommands_via_main(tmp_path):
    path = _cfg(tmp_path, "f.json", "from-field")
    result = invoke(_CliField, ["--config", path, "leaf"])
    assert result.status == 0, result.stderr


def test_app_applies_the_field(tmp_path):
    path = _cfg(tmp_path, "f.json", "from-field")

    class Show(Cmd):
        def __call__(self):
            print(duho.value_sources(self).get("level"))
            return 0

    result = invoke(_CliField, ["--config", path, "show"], commands=[Show])
    assert result.status == 0, result.stderr
    assert result.stdout == "config\n"


def test_unknown_field_is_a_value_error_naming_the_class():
    with pytest.raises(ValueError, match="_BadField") as excinfo:
        invoke(_BadField)
    assert "nope" in str(excinfo.value)
    with pytest.raises(ValueError, match="_BadField"):
        duho.parse(_BadField, [])


def test_missing_file_named_by_the_user_raises_like_an_explicit_config(tmp_path):
    absent = str(tmp_path / "absent.json")
    with pytest.raises(FileNotFoundError):
        duho.parse(_FieldOnly, [], config=absent)
    with pytest.raises(FileNotFoundError):
        invoke(_FieldOnly, ["--config", absent])
    with pytest.raises(FileNotFoundError):
        invoke(_EnvOnly, env={"CFGLOC_FILE": absent})
