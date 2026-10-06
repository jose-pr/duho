"""``app()`` with no resolved command: run a runnable root, else say so."""

import pytest

import duho
from duho.runtime import app


class Runnable(duho.Cmd):
    """A runnable root"""

    def __call__(self):
        print("root ran")
        return 0


class DataRoot(duho.Args):
    """A root that holds data and cannot run"""


def test_runnable_root_runs_with_no_commands(capsys):
    assert app(Runnable, argv=[], setup_logging=False) == 0
    assert "root ran" in capsys.readouterr().out


def test_runnable_root_runs_with_an_empty_command_list(capsys):
    assert app(Runnable, commands=[], argv=[], setup_logging=False) == 0
    assert "root ran" in capsys.readouterr().out


def test_data_root_with_no_commands_reports_that_none_are_available(capsys):
    with pytest.raises(SystemExit) as exc:
        app(DataRoot, commands=[], argv=[], setup_logging=False)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "no commands" in err
    assert "required: {}" not in err
