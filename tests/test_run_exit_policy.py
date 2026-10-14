"""``run(..., exit=...)``: ``False``, ``"parse"`` and a callable that decides."""

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


def _run(mode, argv, exit=False):
    return duho.run(ROOT, argv, exit=exit, **mode)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.delenv("DUHO_TRACEBACK", raising=False)


PARSER_EXITS = pytest.mark.parametrize(
    "argv, status",
    [(["--help"], 0), (["--version"], 0), (["--nonsense"], 2), (["boom", "-x"], 2)],
)


@pytest.mark.parametrize("mode", MODES, ids=IDS)
@PARSER_EXITS
def test_parser_exits_return_when_false(mode, argv, status, capsys):
    assert _run(mode, argv) == status


@pytest.mark.parametrize("mode", MODES, ids=IDS)
@PARSER_EXITS
def test_parser_exits_propagate_under_parse(mode, argv, status, capsys):
    with pytest.raises(SystemExit) as info:
        _run(mode, argv, "parse")
    assert info.value.code == status


@pytest.mark.parametrize("mode", MODES, ids=IDS)
@pytest.mark.parametrize("policy", [False, "parse"])
@pytest.mark.parametrize("cmd", ["boom", "quit"])
def test_a_commands_own_exit_is_returned(mode, policy, cmd):
    assert _run(mode, [cmd], policy) == 3


@pytest.mark.parametrize("mode", MODES, ids=IDS)
def test_other_outcomes_are_returned_under_parse(mode):
    assert _run(mode, ["fine"], "parse") == 5
    assert _run(mode, ["interrupt"], "parse") == 130


@pytest.mark.parametrize("mode", MODES, ids=IDS)
def test_a_broken_pipe_is_1_under_parse(mode, capsys, monkeypatch):
    real = sys.stdout
    assert _run(mode, ["pipe"], "parse") == 1
    sys.stdout.close()
    monkeypatch.setattr(sys, "stdout", real)
    assert capsys.readouterr().err == ""


def test_true_always_exits(capsys):
    for argv, status in ((["fine"], 5), (["--help"], 0), (["interrupt"], 130)):
        with pytest.raises(SystemExit) as info:
            duho.run(ROOT, argv, exit=True, setup_logging=False)
        assert info.value.code == status


def test_a_callable_gets_the_status_and_the_error_and_decides(capsys):
    seen = []

    def policy(status, error):
        seen.append((status, type(error).__name__ if error else None))
        return status != 5

    assert duho.run(ROOT, ["fine"], exit=policy, setup_logging=False) == 5
    with pytest.raises(SystemExit) as info:
        duho.run(ROOT, ["interrupt"], exit=policy, setup_logging=False)
    assert info.value.code == 130
    with pytest.raises(SystemExit) as info:
        duho.run(ROOT, ["quit"], exit=policy, setup_logging=False)
    assert info.value.code == 3
    assert seen == [(5, None), (130, "KeyboardInterrupt"), (3, "SystemExit")]


@pytest.mark.parametrize("argv, raises", [(["--help"], True), (["quit"], False)])
def test_a_callable_may_answer_parse(argv, raises, capsys):
    def policy(status, error):
        return "parse"

    if raises:
        with pytest.raises(SystemExit):
            duho.run(ROOT, argv, exit=policy, setup_logging=False)
    else:
        assert duho.run(ROOT, argv, exit=policy, setup_logging=False) == 3


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
    assert duho.run(root, ["nested"], exit="parse") == 2
