"""The ConfigArgs preset: ``--config``/``-c`` names the config file."""

import sys

import pytest

import duho
from duho import Args, Cli, Cmd, ConfigArgs, LoggingArgs

FILES = {
    "json": ("c.json", '{"port": 5, "label": "from-file"}'),
    "toml": ("c.toml", 'port = 5\nlabel = "from-file"\n'),
    "yaml": ("c.yaml", "port: 5\nlabel: from-file\n"),
    "ini": ("c.ini", "[DEFAULT]\nport = 5\nlabel = from-file\n"),
}


def _write(tmp_path, kind):
    if kind == "yaml":
        pytest.importorskip("yaml")
    if kind == "toml":
        pytest.importorskip("tomllib" if sys.version_info >= (3, 11) else "tomli")
    name, text = FILES[kind]
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class Leaf(Cmd):
    quick: bool = False
    ("--quick",)

    def __call__(self):
        print(f"port={self.port} label={self.label} quick={self.quick}")
        return 0


class Tool(ConfigArgs, LoggingArgs, Cli):
    _parsername_ = "config-tool"
    _subcommands_ = [Leaf]
    port: int = 1
    ("--port",)
    label: str = "default"
    ("--label",)
    loud: bool = False
    ("--loud",)


@pytest.mark.parametrize("flag", ["--config", "-c"])
@pytest.mark.parametrize("kind", sorted(FILES))
def test_the_flag_selects_a_file_of_any_format(kind, flag, tmp_path):
    path = _write(tmp_path, kind)
    parsed = duho.parse(Tool, [flag, str(path), "leaf"])
    assert (parsed.port, parsed.label) == (5, "from-file")
    assert parsed.config == path


@pytest.mark.parametrize("kind", sorted(FILES))
def test_main_and_app_read_the_file(kind, tmp_path, capsys):
    path = _write(tmp_path, kind)
    assert duho.main(Tool, ["-c", str(path), "leaf"], setup_logging=False) == 0
    assert "port=5 label=from-file" in capsys.readouterr().out
    assert (
        duho.app(Tool, commands=[], argv=["-c", str(path), "leaf"], setup_logging=False)
        == 0
    )
    assert "port=5 label=from-file" in capsys.readouterr().out


def test_the_command_line_beats_the_file(tmp_path):
    path = _write(tmp_path, "json")
    parsed = duho.parse(Tool, ["-c", str(path), "--port", "9", "leaf"])
    assert (parsed.port, parsed.label) == (9, "from-file")


def test_a_subcommand_table_reaches_the_subcommand(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"leaf": {"quick": true}}')
    parsed = duho.parse(Tool, ["-c", str(path), "leaf"])
    assert parsed.quick is True


def test_no_config_leaves_the_defaults():
    parsed = duho.parse(Tool, ["leaf"])
    assert parsed.config is None and parsed.port == 1


def test_a_missing_path_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as info:
        duho.parse(Tool, ["-c", str(tmp_path / "missing.toml"), "leaf"])
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "no such file" in err and "missing.toml" in err
    assert "Traceback" not in err
    with pytest.raises(SystemExit) as info:
        duho.main(Tool, ["-c", str(tmp_path / "missing.toml"), "leaf"])
    assert info.value.code == 2


def test_a_directory_is_not_a_file(tmp_path, capsys):
    with pytest.raises(SystemExit):
        duho.parse(Tool, ["--config", str(tmp_path), "leaf"])
    assert "no such file" in capsys.readouterr().err


def test_the_home_directory_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "c.json").write_text('{"port": 6}')
    assert duho.parse(Tool, ["-c", "~/c.json", "leaf"]).port == 6


def test_cli_before_config_args_is_refused():
    with pytest.raises(TypeError, match="Late"):

        class Late(Cli, ConfigArgs):
            pass


def test_the_class_body_may_set_the_field_itself():
    class Explicit(Cli, ConfigArgs):
        _config_field_ = "config"

    assert Explicit._config_field_ == "config"


def test_the_preset_alone_names_the_field():
    class Plain(ConfigArgs):
        pass

    assert Plain._config_field_ == "config"


def _help(cls, capsys, *argv):
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, [*argv, "--help"])
    assert info.value.code == 0
    return capsys.readouterr().out


def test_help_lists_the_reversible_bool_form_with_no_config_given(capsys):
    assert "--no-loud" in _help(Tool, capsys)
    assert "--no-quick" in _help(Tool, capsys, "leaf")


def test_each_config_source_attribute_alone_gives_the_reversible_form(capsys):
    class ByField(Cli):
        _parsername_ = "by-field"
        _config_field_ = "where"
        where: str = ""
        ("--where",)
        on: bool = False
        ("--on",)

    class ByEnv(Cli):
        _parsername_ = "by-env"
        _config_env_ = "DUHO_TEST_CONFIG_ARGS_PATH"
        on: bool = False
        ("--on",)

    class Neither(Cli):
        _parsername_ = "neither"
        on: bool = False
        ("--on",)

    assert "--no-on" in _help(ByField, capsys)
    assert "--no-on" in _help(ByEnv, capsys)
    assert "--no-on" not in _help(Neither, capsys)


def test_an_args_root_with_the_preset_parses_a_file(tmp_path):
    class Flat(ConfigArgs):
        port: int = 1
        ("--port",)

    path = tmp_path / "c.json"
    path.write_text('{"port": 4}')
    assert duho.parse(Flat, ["-c", str(path)]).port == 4
    assert issubclass(Flat, Args)
