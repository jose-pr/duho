"""A settings FILE error is one line, ``PROG: error: MESSAGE``, status 2, with no
usage lines; a value that fails to convert keeps argparse's usage form. The
strict line is bounded, and the field ``_config_field_`` names is never a key."""

import json
import logging
import sys

import pytest

import duho
from duho import Args, Cli, Cmd, ConfigArgs, LoggingArgs
from duho.parsers import prerun_parse


class Leaf(Cmd):
    _parsername_ = "leaf"

    def __call__(self):
        return 0


class Tool(ConfigArgs, LoggingArgs, Cli):
    _parsername_ = "file-error-tool"
    _subcommands_ = [Leaf]
    port: int = 1
    ("--port",)


def _variant(name, **attrs):
    return type(name, (Tool,), {"_parsername_": "file-error-tool", **attrs})


def _strict(**extra):
    return _variant("Strict", **{"_config_validate_": "strict", **extra})


def _write(tmp_path, text, name="s.json"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _one_line(capsys, needle):
    err = capsys.readouterr().err
    assert "usage" not in err.lower()
    assert "Traceback" not in err
    lines = err.splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("file-error-tool: error: ")
    assert needle in lines[0]
    return lines[0]


def _runs(kind, root=Tool):
    """One way a program reaches the file, as a callable taking the path."""
    if kind == "parse":
        return lambda path: duho.parse(root, ["leaf"], config=path)
    if kind == "main":
        return lambda path: duho.main(root, ["leaf"], config=path, setup_logging=False)
    return lambda path: duho.app(
        root, commands=[Leaf], argv=["leaf"], config=path, setup_logging=False
    )


KINDS = ["parse", "main", "app"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_file_that_is_not_its_format_is_one_line(tmp_path, capsys, kind):
    path = _write(tmp_path, "{")
    with pytest.raises(SystemExit) as info:
        _runs(kind)(path)
    assert info.value.code == 2
    _one_line(capsys, str(path))


@pytest.mark.parametrize("kind", KINDS)
def test_a_top_level_that_is_not_a_table_is_one_line(tmp_path, capsys, kind):
    path = _write(tmp_path, "[1, 2]")
    with pytest.raises(SystemExit) as info:
        _runs(kind)(path)
    assert info.value.code == 2
    _one_line(capsys, "table")


@pytest.mark.parametrize("kind", KINDS)
def test_a_format_no_backend_reads_is_one_line(tmp_path, capsys, kind):
    root = _variant("Narrow", _config_backends_=("json",))
    path = _write(tmp_path, "port = 2", name="s.toml")
    with pytest.raises(SystemExit) as info:
        _runs(kind, root)(path)
    assert info.value.code == 2
    _one_line(capsys, "toml")


@pytest.mark.parametrize("kind", KINDS)
def test_a_missing_parser_library_is_one_line(tmp_path, capsys, monkeypatch, kind):
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)
    path = _write(tmp_path, "port = 2", name="s.toml")
    with pytest.raises(SystemExit) as info:
        _runs(kind)(path)
    assert info.value.code == 2
    _one_line(capsys, "duho[config]")


@pytest.mark.parametrize("kind", KINDS)
def test_the_strict_unknown_key_error_is_one_line(tmp_path, capsys, kind):
    path = _write(tmp_path, json.dumps({"bogus": 1}))
    with pytest.raises(SystemExit) as info:
        _runs(kind, _strict())(path)
    assert info.value.code == 2
    _one_line(capsys, "unknown key 'bogus'")


@pytest.mark.parametrize("kind", KINDS)
def test_a_validate_function_value_error_is_one_line(tmp_path, capsys, kind):
    def check(args, config):
        raise ValueError("port must be above 1024")

    path = _write(tmp_path, json.dumps({"port": 2}))
    with pytest.raises(SystemExit) as info:
        _runs(kind, _strict(_config_validate_=check))(path)
    assert info.value.code == 2
    line = _one_line(capsys, "port must be above 1024")
    assert str(path) in line


def test_a_bad_field_value_keeps_the_usage_form(tmp_path, capsys):
    path = _write(tmp_path, json.dumps({"port": "many"}))
    with pytest.raises(SystemExit) as info:
        duho.parse(Tool, ["leaf"], config=path)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err and "port" in err


def test_the_advisory_prepass_prints_nothing(tmp_path, capsys):
    path = _write(tmp_path, "{")

    class Cfg(Args):
        _config_ = str(path)
        port: int = 1

    parser = Cfg._parser_()
    with pytest.raises(SystemExit) as info:
        prerun_parse(parser, [], quiet=True)
    assert info.value.code == 2
    assert capsys.readouterr().err == ""
    assert "error" not in vars(parser)


def test_app_with_a_module_command_reports_the_error_once(tmp_path, capsys):
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "plain.py").write_text("def main(args):\n    return None\n")
    path = _write(tmp_path, "{")
    with pytest.raises(SystemExit) as info:
        duho.app(
            Tool,
            commands=duho.discover_commands(cmds),
            argv=["plain"],
            config=path,
            setup_logging=False,
        )
    assert info.value.code == 2
    _one_line(capsys, str(path))


# -- the strict line is bounded ---------------------------------------------


def _strict_line(tmp_path, capsys, keys):
    path = _write(tmp_path, json.dumps({k: 1 for k in keys}))
    with pytest.raises(SystemExit):
        duho.parse(_strict(), ["leaf"], config=path)
    return _one_line(capsys, "unknown key")


def test_a_long_key_is_cut_to_120_characters(tmp_path, capsys):
    line = _strict_line(tmp_path, capsys, ["k" * 500])
    shown = line.split("unknown key ", 1)[1]
    assert shown == repr("k" * 117 + "...")
    assert len(shown) == 120 + 2


def test_a_key_with_a_newline_is_escaped(tmp_path, capsys):
    line = _strict_line(tmp_path, capsys, ["a\nb\x1b[0m"])
    assert line.endswith("unknown key 'a\\nb\\x1b[0m'")


def test_more_than_twenty_keys_are_counted(tmp_path, capsys):
    keys = [f"key{i:02d}" for i in range(25)]
    line = _strict_line(tmp_path, capsys, keys)
    shown = line.split("unknown keys: ", 1)[1]
    assert shown == ", ".join(repr(k) for k in keys[:20]) + " and 5 more"


def test_exactly_twenty_keys_are_all_shown(tmp_path, capsys):
    keys = [f"key{i:02d}" for i in range(20)]
    line = _strict_line(tmp_path, capsys, keys)
    assert line.endswith(repr(keys[-1])) and "more" not in line


def test_a_function_gets_every_path_whole(tmp_path):
    seen = []
    keys = ["k" * 500] + [f"key{i:02d}" for i in range(24)]
    root = _strict(_config_validate_=lambda a, c, unknown: seen.append(unknown))
    path = _write(tmp_path, json.dumps({k: 1 for k in keys}))
    duho.parse(root, ["leaf"], config=path)
    assert seen == [tuple(keys)]


def test_no_value_reaches_the_bounded_line(tmp_path, capsys):
    path = _write(tmp_path, json.dumps({"bogus": "s3cr3t-value-7731"}))
    with pytest.raises(SystemExit):
        duho.parse(_strict(), ["leaf"], config=path)
    assert "s3cr3t-value-7731" not in capsys.readouterr().err


# -- the --config field is never a settings key ------------------------------


def test_the_config_field_in_a_file_is_not_applied(tmp_path, caplog):
    other = _write(tmp_path, json.dumps({"port": 9}), name="other.json")
    path = _write(tmp_path, json.dumps({"port": 3, "config": str(other)}))
    with caplog.at_level(logging.DEBUG, logger="duho._layers"):
        parsed = duho.parse(Tool, ["leaf"], config=path)
    assert parsed.port == 3
    assert parsed.config != other
    assert any("unknown config key 'config'" in r.getMessage() for r in caplog.records)


def test_the_config_field_in_a_file_is_refused_under_strict(tmp_path, capsys):
    path = _write(tmp_path, json.dumps({"port": 3, "config": "other.json"}))
    with pytest.raises(SystemExit) as info:
        duho.parse(_strict(), ["-c", str(path), "leaf"])
    assert info.value.code == 2
    _one_line(capsys, "unknown key 'config'")


def test_the_config_field_is_unknown_to_a_validate_function(tmp_path):
    seen = []
    root = _strict(_config_validate_=lambda a, c, unknown: seen.append(unknown))
    path = _write(tmp_path, json.dumps({"config": "other.json"}))
    duho.parse(root, ["-c", str(path), "leaf"])
    assert seen == [("config",)]


def test_another_field_is_still_a_key(tmp_path):
    path = _write(tmp_path, json.dumps({"port": 3}))
    assert duho.parse(_strict(), ["-c", str(path), "leaf"]).port == 3


def test_app_refuses_the_config_field_under_strict(tmp_path, capsys):
    path = _write(tmp_path, json.dumps({"port": 3, "config": "other.json"}))
    with pytest.raises(SystemExit) as info:
        duho.app(
            _strict(),
            commands=[Leaf],
            argv=["-c", str(path), "leaf"],
            setup_logging=False,
        )
    assert info.value.code == 2
    _one_line(capsys, "unknown key 'config'")
