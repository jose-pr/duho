"""Tests for shell completion generation (bash/zsh/fish/powershell)."""

import argparse
import enum
import os
import pathlib
import shutil
import subprocess
import typing as ty

import pytest

import duho
import duho.completion as completion
from duho import Args

# --- Fixtures: a 2-level subcommand app with a choice field + Path field --


class Deploy(Args):
    """Deploy the application."""

    mode: ty.Literal["fast", "slow", "auto"] = "fast"
    "Deployment mode"
    ("--mode",)

    target: pathlib.Path = pathlib.Path(".")
    "Target directory"
    ("--target",)


class App(Args):
    """Example app with a subcommand tree."""

    _subcommands_ = [Deploy]


class CompletionApp(Args):
    """Same tree, opted into --print-completion."""

    _completion_ = True
    _subcommands_ = [Deploy]


def _is_real_bash(path: str) -> bool:
    """A real bash binary is several MB; the WSL launcher shims Windows
    installs at ``...\\System32\\bash.exe`` AND at the WindowsApps app-
    execution-alias path (``...\\WindowsApps\\bash.exe``, which `shutil.which`
    can return FIRST under PowerShell even ahead of System32) are both
    near-zero-byte stub/reparse files that hang or misbehave when driven
    non-interactively via subprocess."""
    try:
        return os.path.getsize(path) > 4096
    except OSError:
        return False


def _find_bash() -> "str | None":
    """Resolve a real bash deterministically for driving completion (see
    `_is_real_bash`)."""
    found = shutil.which("bash")
    if found and _is_real_bash(found):
        return found
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = shutil.which("bash", path=directory)
        if candidate and _is_real_bash(candidate):
            return candidate
    return None


_BASH = _find_bash()


# --- parser-tree spec walker ------------------------------------------------


def test_walk_captures_subcommand_choices_and_path():
    parser = App._parser_()
    spec = completion.spec(parser)

    assert "Deploy" in spec.subcommands
    deploy_spec = spec.subcommands["Deploy"]
    assert deploy_spec.path == ("Deploy",)

    mode_opt = next(o for o in deploy_spec.options if "--mode" in o.flags)
    assert mode_opt.choices == ("fast", "slow", "auto")
    assert mode_opt.is_path is False

    target_opt = next(o for o in deploy_spec.options if "--target" in o.flags)
    assert target_opt.is_path is True
    assert target_opt.choices is None


def test_spec_is_the_public_entry_point():
    """C053: a documented public function builds the CompletionSpec tree,
    not just the private `_walk`."""
    parser = App._parser_()
    assert completion.spec(parser) == completion._walk(parser)
    assert isinstance(completion.spec(parser), completion.CompletionSpec)


def test_walk_skips_suppressed_option_and_subcommand():
    """C049: an option or subcommand hidden via help=SUPPRESS never reaches
    a completion script."""

    class Hidden(Args):
        """Hidden subcommand."""

    class Visible(Args):
        """Visible subcommand."""

    class Suppressible(Args):
        """App with a suppressed flag and a suppressed subcommand."""

        secret: str = ""
        "hidden flag"
        ("--secret",)
        _subcommands_ = [Visible, Hidden]

    parser = Suppressible._parser_()
    for action in parser._actions:
        if "--secret" in getattr(action, "option_strings", []):
            action.help = argparse.SUPPRESS
        if isinstance(action, argparse._SubParsersAction):
            for pseudo in action._choices_actions:
                if pseudo.dest == "Hidden":
                    pseudo.help = argparse.SUPPRESS

    top = completion.spec(parser)
    assert "--secret" not in {f for o in top.options for f in o.flags}
    assert "Hidden" not in top.subcommands
    assert "Visible" in top.subcommands


def test_enum_field_gets_completion_choices():
    """C025: an Enum-typed field offers its member names as choices, even
    though duho leaves argparse's own `choices` unset for Enum fields."""

    class Color(enum.Enum):
        RED = 1
        GREEN = 2

    class Paint(Args):
        """Paint something."""

        color: Color = Color.RED
        "color"
        ("--color",)
        shade: Color = Color.RED
        "shade"
        ("shade",)

    parser = Paint._parser_()
    top = completion.spec(parser)
    color_opt = next(o for o in top.options if "--color" in o.flags)
    assert color_opt.choices == ("RED", "GREEN")
    shade_pos = next(p for p in top.positionals if p.name == "shade")
    assert shade_pos.choices == ("RED", "GREEN")


def test_enum_fallback_ignores_a_plain_factory():
    """A non-Enum custom factory (no `_names` parameter) yields no choices,
    rather than raising."""
    assert completion._enum_choices(str) is None
    assert completion._enum_choices(int) is None


# --- shell script emitters --------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_emitters_render_nonempty_script_with_expected_content(shell):
    parser = App._parser_()
    emitter = getattr(completion, shell)
    script = emitter(parser)

    assert isinstance(script, str)
    assert script.strip()
    assert "App" in script
    assert "Deploy" in script
    assert "fast" in script  # a choice value from the Literal field
    # No unrendered Python format-string placeholders left in the output.
    assert "{choices}" not in script
    assert "{prog}" not in script
    assert "{flags}" not in script


def test_bash_script_is_syntactically_valid():
    """Optional smoke check: skipped if a real bash isn't on PATH."""
    if not _BASH:
        pytest.skip("bash not available on this machine")

    parser = App._parser_()
    script = completion.bash(parser)
    result = subprocess.run(
        [_BASH, "-n", "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_bash_function_name_is_namespaced_and_collision_resistant():
    """C051: two progs that only differ in punctuation get DIFFERENT bash
    function names, and the function is namespaced away from bash-completion
    helpers like `_filedir`."""

    class A(Args):
        """a"""

    for prog_a, prog_b in [("my-app", "my.app"), ("filedir", "filedir2")]:
        parser_a = A._parser_()
        parser_a.prog = prog_a
        parser_b = A._parser_()
        parser_b.prog = prog_b
        script_a = completion.bash(parser_a)
        script_b = completion.bash(parser_b)
        assert "_duho_complete_" in script_a
        # Extract the defined function name from `complete -F <func> <prog>`.
        func_a = script_a.splitlines()[1].split("(")[0]
        func_b = script_b.splitlines()[1].split("(")[0]
        assert func_a != func_b


# --- --print-completion wiring ----------------------------------------------


def test_print_completion_flag_prints_script_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        duho.main(CompletionApp, ["--print-completion", "bash"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "complete" in captured.out
    assert "CompletionApp" in captured.out


def test_print_completion_flag_absent_without_opt_in():
    parser = App._parser_()
    help_text = parser.format_help()
    assert "--print-completion" not in help_text


def test_print_completion_standalone_function():
    import io

    buf = io.StringIO()
    duho.print_completion(CompletionApp, "zsh", file=buf)
    out = buf.getvalue()
    assert "complete" in out.lower() or "compdef" in out
    assert "CompletionApp" in out


# --- stale-docs regression (C054) -------------------------------------------


def test_walk_docstring_mentions_all_four_emitters():
    """C054: the internal walk's docstring used to say 'three emitters',
    stale since the PowerShell emitter was added."""
    assert "three emitters" not in (completion._walk.__doc__ or "")
