"""A module command's ``register`` hook gets parsed root args even when a
required root option has not been given."""

import pytest

import duho
from duho.runtime import app

_SYNC = '''\
"""Sync things."""


def register(parser, args):
    parser.add_argument("--chunk", type=int, default=64 if args.fast else 8)


def main(args):
    print("sync chunk", args.chunk)
    return 0
'''


class Root(duho.Cli):
    """root"""

    token: str
    "API token"
    ("--token",)
    fast: bool = False
    "Fast mode"


@pytest.fixture
def mods(tmp_path):
    (tmp_path / "sync.py").write_bytes(_SYNC.encode())
    return tmp_path


def test_hook_gets_args_with_all_globals_given(mods, capsys):
    argv = ["--token", "t", "--fast", "sync"]
    assert app(Root, source=mods, argv=argv, setup_logging=False) == 0
    assert "sync chunk 64" in capsys.readouterr().out


def test_missing_required_root_option_is_a_usage_error(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        app(Root, source=mods, argv=["sync"], setup_logging=False)
    assert exc.value.code == 2
    assert "--token" in capsys.readouterr().err


def test_help_works_with_a_required_root_option(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        app(Root, source=mods, argv=["--help"], setup_logging=False)
    assert exc.value.code == 0
    assert "sync" in capsys.readouterr().out


def test_unknown_option_is_a_usage_error(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        app(Root, source=mods, argv=["--bogus", "sync"], setup_logging=False)
    assert exc.value.code == 2
    assert "AttributeError" not in capsys.readouterr().err


class _CountRoot(duho.Cli):
    """root"""

    count: int = 1
    "How many"
    ("--count",)
    fast: bool = False
    "Fast mode"


def test_hook_gets_root_defaults_when_a_global_fails_to_convert(mods, capsys):
    with pytest.raises(SystemExit) as exc:
        app(_CountRoot, source=mods, argv=["--count", "x", "sync"], setup_logging=False)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "AttributeError" not in err
    assert "--count" in err or "count" in err
