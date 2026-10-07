"""``duho.run``: the program entry that maps a command's outcome to stdout and a status."""

import json
import subprocess
import sys
import textwrap

import pytest

import duho
from conftest import subprocess_env
from duho import Cli, Cmd, Result


class Quiet(Cmd):
    _parsername_ = "quiet"

    def __call__(self):
        return None


class Status(Cmd):
    _parsername_ = "status"

    def __call__(self):
        return 3


class Flag(Cmd):
    _parsername_ = "flag"

    def __call__(self):
        return True


class Text(Cmd):
    _parsername_ = "text"

    def __call__(self):
        return "three rows"


class Data(Cmd):
    _parsername_ = "data"

    def __call__(self):
        return {"rows": 3}


class Answer(Cmd):
    _parsername_ = "answer"

    def __call__(self):
        return Result(2, value=["x"], text="not printed")


class WordsOnly(Cmd):
    _parsername_ = "words-only"

    def __call__(self):
        return Result(1, text="mcp only")


class Interrupt(Cmd):
    _parsername_ = "interrupt"

    def __call__(self):
        raise KeyboardInterrupt


class Pipe(Cmd):
    _parsername_ = "pipe"

    def __call__(self):
        raise BrokenPipeError


class Leave(Cmd):
    _parsername_ = "leave"

    def __call__(self):
        raise SystemExit(9)


class Fails(Cmd):
    _parsername_ = "fails"

    def __call__(self):
        raise duho.CommandError("nope", 5)


class Root(Cli):
    _parsername_ = "prog"
    _subcommands_ = [
        Quiet,
        Status,
        Flag,
        Text,
        Data,
        Answer,
        WordsOnly,
        Interrupt,
        Pipe,
        Leave,
        Fails,
    ]


def _run(argv, **kwargs):
    with pytest.raises(SystemExit) as info:
        duho.run(Root, argv, **kwargs)
    return info.value.code


@pytest.mark.parametrize(
    "argv, status, stdout",
    [
        (["quiet"], 0, ""),
        (["status"], 3, ""),
        (["flag"], 1, ""),
        (["text"], 0, "three rows\n"),
        (["data"], 0, json.dumps({"rows": 3}, indent=2) + "\n"),
        (["answer"], 2, json.dumps(["x"], indent=2) + "\n"),
        (["words-only"], 1, ""),
    ],
)
def test_outcome_kinds(capsys, argv, status, stdout):
    assert _run(argv) == status
    captured = capsys.readouterr()
    assert captured.out == stdout
    assert "mcp only" not in captured.err + captured.out


def test_command_error_status_and_message(capsys):
    assert _run(["fails"]) == 5
    assert capsys.readouterr().err == "prog: error: nope\n"


def test_systemexit_from_the_command_passes_through():
    assert _run(["leave"]) == 9


def test_argument_error_passes_through(capsys):
    assert _run(["nosuchcommand"]) == 2
    capsys.readouterr()


def test_keyboard_interrupt_is_130_and_silent(capsys):
    assert _run(["interrupt"]) == 130
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_broken_pipe_is_1_and_silent(capsys, monkeypatch):
    real = sys.stdout
    assert _run(["pipe"]) == 1
    assert sys.stdout is not real
    sys.stdout.close()
    monkeypatch.setattr(sys, "stdout", real)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_traceback_env_reraises_interrupt_and_pipe(monkeypatch):
    monkeypatch.setenv("DUHO_TRACEBACK", "1")
    with pytest.raises(KeyboardInterrupt):
        duho.run(Root, ["interrupt"])
    with pytest.raises(BrokenPipeError):
        duho.run(Root, ["pipe"])


def test_app_keywords_route_through_app(capsys):
    class Extra(Cmd):
        _parsername_ = "extra"

        def __call__(self):
            return "from app"

    assert _run(["extra"], commands=[Extra]) == 0
    assert capsys.readouterr().out == "from app\n"


def test_print_that_hits_a_closed_pipe_is_silent(monkeypatch):
    class Closed:
        def write(self, text):
            raise BrokenPipeError

        def flush(self):
            raise BrokenPipeError

        def fileno(self):
            raise OSError

    monkeypatch.setattr(sys, "stdout", Closed())
    assert _run(["text"]) == 1
    sys.stdout.close()


def test_readme_idiom_in_a_real_process(tmp_path):
    script = tmp_path / "prog.py"
    script.write_text(
        textwrap.dedent('''
            import duho


            class Greet(duho.Cmd):
                """Greets."""

                def __call__(self):
                    return {"hello": "world"}


            if __name__ == "__main__":
                duho.run(Greet)
            '''),
        encoding="utf-8",
    )
    done = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=subprocess_env(),
    )
    assert done.returncode == 0
    assert json.loads(done.stdout) == {"hello": "world"}
    assert done.stderr == ""
