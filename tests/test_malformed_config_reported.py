"""A malformed TOML or JSON config file is reported through the parser (usage
line, exit 2) with the path and the position, never as a library traceback."""

import importlib.util

import pytest

import duho
from duho import Args
from duho import _layers

pytestmark = pytest.mark.skipif(
    not any(importlib.util.find_spec(m) for m in ("tomllib", "tomli")),
    reason="needs a TOML backend",
)


class _Port(Args):
    port: int = 1


def test_load_config_toml_error_is_a_value_error_naming_the_path(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('port = 1\nname = "x" oops\n')
    with pytest.raises(ValueError) as info:
        _layers._load_config(path)
    assert str(path) in str(info.value)
    assert "line 2" in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


def test_load_config_json_error_hides_the_library_traceback(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"port": ')
    with pytest.raises(ValueError) as info:
        _layers._load_config(path)
    assert str(path) in str(info.value)
    assert info.value.__suppress_context__


@pytest.mark.parametrize("name,text", [("bad.toml", "port = 1 2\n"), ("bad.json", "{")])
def test_parse_reports_a_malformed_config_through_the_parser(
    tmp_path, capsys, name, text
):
    path = tmp_path / name
    path.write_text(text)
    with pytest.raises(SystemExit) as info:
        duho.parse(_Port, [], config=path)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert str(path) in err
    assert "Traceback" not in err


def test_class_config_malformed_is_reported_through_a_bare_parser(tmp_path, capsys):
    path = tmp_path / "bad.toml"
    path.write_text("port = = 1\n")

    class Cfg(Args):
        _config_ = str(path)
        port: int = 1

    with pytest.raises(SystemExit) as info:
        Cfg._parser_().parse_args([])
    assert info.value.code == 2
    assert str(path) in capsys.readouterr().err
