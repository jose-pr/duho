"""``_completion_command_``: an opt-in subcommand that prints a shell completion script."""

import pytest

import duho
from duho import Cli, Cmd
from duho.testing import invoke

SHELLS = ("bash", "zsh", "fish", "powershell")


class _Leaf(Cmd):
    """A leaf."""

    def __call__(self):
        return 0


class _Plain(Cli):
    _parsername_ = "compl-app"
    _subcommands_ = [_Leaf]


class _On(Cli):
    _parsername_ = "compl-app"
    _completion_command_ = True
    _subcommands_ = [_Leaf]


class _Named(Cli):
    _parsername_ = "compl-app"
    _completion_command_ = "comp"
    _subcommands_ = [_Leaf]


def test_default_registers_nothing():
    assert duho.Cli._completion_command_ is False
    result = invoke(_Plain, ["completion", "bash"])
    assert result.status == 2


@pytest.mark.parametrize("shell", SHELLS)
def test_main_prints_the_script_for_each_shell(shell):
    result = invoke(_On, ["completion", shell])
    assert result.status == 0
    assert "compl-app" in result.stdout
    assert "leaf" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("shell", SHELLS)
def test_app_prints_the_script_for_each_shell(shell):
    result = invoke(_On, ["completion", shell], commands=[_Leaf])
    assert result.status == 0
    assert "compl-app" in result.stdout
    assert "leaf" in result.stdout


def test_script_offers_the_completion_subcommand_itself():
    assert "completion" in invoke(_On, ["completion", "bash"]).stdout
    assert "completion" in invoke(_On, ["completion", "bash"], commands=[_Leaf]).stdout


def test_a_string_names_the_subcommand():
    assert invoke(_Named, ["comp", "fish"]).status == 0
    assert invoke(_Named, ["completion", "fish"]).status == 2


def test_the_shell_is_required_and_checked():
    assert invoke(_On, ["completion"]).status == 2
    result = invoke(_On, ["completion", "tcsh"])
    assert result.status == 2
    assert "bash" in result.stderr


def test_print_completion_flag_is_unchanged():
    class Both(Cli):
        _parsername_ = "compl-app"
        _completion_ = True
        _completion_command_ = True
        _subcommands_ = [_Leaf]

    result = invoke(Both, ["--print-completion", "bash"])
    assert result.status == 0
    assert "compl-app" in result.stdout


def test_other_commands_still_run():
    assert invoke(_On, ["leaf"]).status == 0
    assert invoke(_On, ["leaf"], commands=[_Leaf]).status == 0


@pytest.mark.parametrize("kwargs", [{}, {"commands": [_Leaf]}])
def test_collision_with_a_command_is_a_value_error(kwargs):
    class Taken(Cmd):
        _parsername_ = "completion"

        def __call__(self):
            return 0

    class Root(Cli):
        _parsername_ = "compl-app"
        _completion_command_ = True
        _subcommands_ = [_Leaf, Taken]

    with pytest.raises(ValueError, match="completion"):
        invoke(Root, ["leaf"], **kwargs)


def test_collision_with_the_mcp_command_is_a_value_error():
    class Root(Cli):
        _parsername_ = "compl-app"
        _completion_command_ = "mcp"
        _mcp_command_ = True
        _subcommands_ = [_Leaf]

    with pytest.raises(ValueError, match="mcp"):
        invoke(Root, ["leaf"])
    with pytest.raises(ValueError, match="mcp"):
        invoke(Root, ["leaf"], commands=[_Leaf])


def test_it_coexists_with_the_mcp_command():
    class Root(Cli):
        _parsername_ = "compl-app"
        _completion_command_ = True
        _mcp_command_ = True
        _subcommands_ = [_Leaf]

    for kwargs in ({}, {"commands": [_Leaf]}):
        result = invoke(Root, ["completion", "bash"], **kwargs)
        assert result.status == 0 and "mcp" in result.stdout


def test_needs_another_subcommand():
    class Alone(Cli):
        _parsername_ = "compl-app"
        _completion_command_ = True

    with pytest.raises(ValueError, match="completion"):
        invoke(Alone, ["completion", "bash"])
    with pytest.raises(ValueError, match="completion"):
        invoke(Alone, ["completion", "bash"], commands=[])


@pytest.mark.parametrize("bad", ["", "a b", "-x"])
def test_invalid_names_are_value_errors(bad):
    class Root(Cli):
        _parsername_ = "compl-app"
        _completion_command_ = bad
        _subcommands_ = [_Leaf]

    with pytest.raises(ValueError, match="completion"):
        invoke(Root, ["leaf"])


def test_it_is_not_an_mcp_tool():
    from duho import mcp

    names = [tool["name"] for tool in mcp.describe_tools(_On)]
    assert not [n for n in names if "completion" in n]
    from duho.completion._cmd import CompletionCmd

    assert CompletionCmd._mcp_ is False


def test_a_default_app_does_not_load_completion(tmp_path):
    import subprocess
    import sys

    from conftest import subprocess_env

    script = tmp_path / "plain_app.py"
    script.write_text(
        "import sys, duho\n"
        "class L(duho.Cmd):\n"
        "    def __call__(self): return 0\n"
        "class R(duho.Cli):\n"
        "    _subcommands_ = [L]\n"
        "duho.main(R, ['l'])\n"
        "duho.app(R, argv=['l'])\n"
        "print('duho.completion' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, str(script)], text=True, env=subprocess_env()
    )
    assert out.strip() == "False"
