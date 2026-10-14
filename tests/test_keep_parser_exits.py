"""``run(..., exit=False, keep_parser_exits=True)`` lets the parser's own exits propagate."""

import sys

import pytest

import duho
from duho import Cli, Cmd


class Boom(Cmd):
    _parsername_ = "boom"

    def __call__(self):
        raise SystemExit(3)

    n: int = 0


class Quit(Cmd):
    _parsername_ = "quit"

    def __call__(self):
        sys.exit(3)


class Interrupt(Cmd):
    _parsername_ = "interrupt"

    def __call__(self):
        raise KeyboardInterrupt


class Pipe(Cmd):
    _parsername_ = "pipe"

    def __call__(self):
        raise BrokenPipeError


class Fine(Cmd):
    _parsername_ = "fine"

    def __call__(self):
        return 5


ROOT = type(
    "Keeper",
    (Cli,),
    {
        "_parsername_": "keeper",
        "_version_": "1.2",
        "_subcommands_": [Boom, Quit, Interrupt, Pipe, Fine],
    },
)

# main() takes no keywords beyond its own; app() is reached by passing one.
MODES = [{"setup_logging": False}, {"setup_logging": False, "name": "keeper"}]
IDS = ["main", "app"]


def _run(mode, argv, **kw):
    return duho.run(ROOT, argv, exit=False, **dict(mode, **kw))


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("DUHO_TRACEBACK", raising=False)


@pytest.mark.parametrize("mode", MODES, ids=IDS)
@pytest.mark.parametrize(
    "argv, status",
    [(["--help"], 0), (["--version"], 0), (["--nonsense"], 2), (["boom", "-x"], 2)],
)
def test_parser_exits_return_by_default(mode, argv, status, capsys):
    assert _run(mode, argv) == status


@pytest.mark.parametrize("mode", MODES, ids=IDS)
@pytest.mark.parametrize(
    "argv, status",
    [(["--help"], 0), (["--version"], 0), (["--nonsense"], 2), (["boom", "-x"], 2)],
)
def test_parser_exits_propagate_when_kept(mode, argv, status, capsys):
    with pytest.raises(SystemExit) as info:
        _run(mode, argv, keep_parser_exits=True)
    assert info.value.code == status


@pytest.mark.parametrize("mode", MODES, ids=IDS)
@pytest.mark.parametrize("keep", [False, True])
@pytest.mark.parametrize("cmd", ["boom", "quit"])
def test_a_commands_own_exit_is_returned_either_way(mode, keep, cmd):
    assert _run(mode, [cmd], keep_parser_exits=keep) == 3


@pytest.mark.parametrize("mode", MODES, ids=IDS)
def test_other_outcomes_are_unchanged_when_kept(mode):
    assert _run(mode, ["fine"], keep_parser_exits=True) == 5
    assert _run(mode, ["interrupt"], keep_parser_exits=True) == 130


@pytest.mark.parametrize("mode", MODES, ids=IDS)
def test_a_broken_pipe_is_still_1_when_kept(mode, capsys, monkeypatch):
    real = sys.stdout
    assert _run(mode, ["pipe"], keep_parser_exits=True) == 1
    sys.stdout.close()
    monkeypatch.setattr(sys, "stdout", real)
    assert capsys.readouterr().err == ""


def test_exit_true_ignores_the_keyword(capsys):
    for keep in (False, True):
        with pytest.raises(SystemExit) as info:
            duho.run(ROOT, ["fine"], keep_parser_exits=keep, setup_logging=False)
        assert info.value.code == 5
        with pytest.raises(SystemExit) as info:
            duho.run(ROOT, ["--help"], keep_parser_exits=keep, setup_logging=False)
        assert info.value.code == 0


def test_a_nested_programs_parser_exit_is_a_commands_exit():
    class Nested(Cmd):
        _parsername_ = "nested"

        def __call__(self):
            duho.main(ROOT, ["--nonsense"], setup_logging=False)

    root = type(
        "Outer",
        (Cli,),
        {"_parsername_": "outer", "_subcommands_": [Nested]},
    )
    assert duho.run(root, ["nested"], exit=False, keep_parser_exits=True) == 2
