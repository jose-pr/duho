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


class _Run(duho.Cmd):
    """Run."""

    _parsername_ = "run"

    def __call__(self):
        return 0


@pytest.mark.parametrize("name,text", [("bad.toml", "port = 1 2\n"), ("bad.json", "{")])
def test_app_reports_a_malformed_config_through_the_parser(
    tmp_path, capsys, name, text
):
    path = tmp_path / name
    path.write_text(text)
    with pytest.raises(SystemExit) as info:
        duho.app(
            duho.LoggingArgs,
            commands=[_Run],
            argv=["run"],
            config=path,
            setup_logging=False,
        )
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert str(path) in err
    assert "Traceback" not in err


def _refusing_loader(path):
    raise ValueError(f"{path.name}: not a format this application reads")


class _OwnLoader(Args):
    port: int = 1
    _config_loader_ = staticmethod(_refusing_loader)


class _OwnLoaderRoot(duho.LoggingArgs):
    _config_loader_ = staticmethod(_refusing_loader)


def test_a_value_error_from_the_applications_own_loader_propagates(tmp_path, capsys):
    path = tmp_path / "settings.conf"
    path.write_text("anything")
    with pytest.raises(ValueError, match="not a format this application reads"):
        duho.parse(_OwnLoader, [], config=path)
    with pytest.raises(ValueError, match="not a format this application reads"):
        duho.app(
            _OwnLoaderRoot,
            commands=[_Run],
            argv=["run"],
            config=path,
            setup_logging=False,
        )
    assert capsys.readouterr().err == ""


def test_a_loader_that_returns_no_table_is_reported_through_the_parser(
    tmp_path, capsys
):
    class Listy(Args):
        port: int = 1
        _config_loader_ = staticmethod(lambda path: [1, 2])

    path = tmp_path / "settings.conf"
    path.write_text("anything")
    with pytest.raises(SystemExit) as info:
        duho.parse(Listy, [], config=path)
    assert info.value.code == 2
    assert str(path) in capsys.readouterr().err
