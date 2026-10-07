"""A bad env or config value for a field with a custom action is reported
through the parser without echoing the value, like it is for a built-in
action."""

import argparse

import pytest

import duho
from duho import Arg, Args, Meta

SECRET = "S3CRET-VALUE"


class KeepAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)


def to_port(text: str) -> int:
    raise argparse.ArgumentTypeError(f"bad port {text!r}")


class Custom(Args):
    port: Arg[int, Meta(type=to_port, action=KeepAction, env="DUHO_T_CUSTOM_PORT")] = 1


class Plain(Args):
    port: Arg[int, Meta(type=to_port, env="DUHO_T_CUSTOM_PORT")] = 1


@pytest.mark.parametrize("cls", [Custom, Plain])
def test_env_value_error_is_reported_and_redacted(monkeypatch, capsys, cls):
    monkeypatch.setenv("DUHO_T_CUSTOM_PORT", SECRET)
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, [])
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "DUHO_T_CUSTOM_PORT" in err
    assert SECRET not in err


@pytest.mark.parametrize("cls", [Custom, Plain])
def test_config_value_error_is_reported_and_redacted(tmp_path, capsys, cls):
    path = tmp_path / "c.json"
    path.write_text('{"port": "%s"}' % SECRET)
    with pytest.raises(SystemExit) as info:
        duho.parse(cls, [], config=path)
    assert info.value.code == 2
    err = capsys.readouterr().err
    assert "port" in err
    assert SECRET not in err
