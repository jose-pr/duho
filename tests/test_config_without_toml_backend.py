"""With no TOML reader installed, an existing `.toml` config is reported
through the parser naming the `config` extra, while `--help` and `--version`
keep working."""

import sys

import pytest

import duho
from duho import Args, Cli


@pytest.fixture
def no_toml(monkeypatch):
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "app.toml"
    path.write_text("port = 9\n")
    return path


class _App(Args):
    _version_ = "1.2.3"
    port: int = 1


def test_parse_reports_the_missing_backend_through_the_parser(no_toml, cfg, capsys):
    with pytest.raises(SystemExit) as info:
        duho.parse(_App, [], config=cfg)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "duho[config]" in err
    assert "Traceback" not in err


def test_help_still_works(no_toml, cfg, capsys):
    with pytest.raises(SystemExit) as info:
        duho.parse(_App, ["--help"], config=cfg)
    assert info.value.code == 0
    assert "--port" in capsys.readouterr().out


def test_version_still_works(no_toml, cfg, capsys):
    with pytest.raises(SystemExit) as info:
        duho.parse(_App, ["--version"], config=cfg)
    assert info.value.code == 0
    assert "1.2.3" in capsys.readouterr().out


def test_class_config_through_a_bare_parser(no_toml, cfg, capsys):
    class WithClassConfig(Args):
        _config_ = str(cfg)
        _version_ = "1.2.3"
        port: int = 1

    parser = WithClassConfig._parser_()
    with pytest.raises(SystemExit) as info:
        parser.parse_args(["--version"])
    assert info.value.code == 0
    with pytest.raises(SystemExit) as info:
        parser.parse_args([])
    assert info.value.code == 2
    assert "duho[config]" in capsys.readouterr().err


def test_json_config_is_unaffected(no_toml, tmp_path):
    path = tmp_path / "app.json"
    path.write_text('{"port": 9}')
    assert duho.parse(_App, [], config=path).port == 9


def test_subcommand_help_still_works(no_toml, cfg, capsys):
    class Sub(duho.Cmd):
        n: int = 0

        def __call__(self):
            return 0

    class Root(Cli):
        _subcommands_ = [Sub]

    with pytest.raises(SystemExit) as info:
        duho.parse(Root, ["sub", "--help"], config=cfg)
    assert info.value.code == 0
