"""Tests for Plan 06: shell completion generation (bash/zsh/fish)."""

import pathlib
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


# --- Phase 1: spec walker --------------------------------------------------


def test_walk_captures_subcommand_choices_and_path():
    parser = App._parser_()
    spec = completion._walk(parser)

    assert "Deploy" in spec.subcommands
    deploy_spec = spec.subcommands["Deploy"]

    mode_opt = next(o for o in deploy_spec.options if "--mode" in o.flags)
    assert mode_opt.choices == ("fast", "slow", "auto")
    assert mode_opt.is_path is False

    target_opt = next(o for o in deploy_spec.options if "--target" in o.flags)
    assert target_opt.is_path is True
    assert target_opt.choices is None


# --- Phase 2/3: emitters ----------------------------------------------------


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
    """Optional smoke check: skipped if bash isn't on PATH."""
    import shutil
    import subprocess

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not available on this machine")

    parser = App._parser_()
    script = completion.bash(parser)
    result = subprocess.run(
        [bash_path, "-n", "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# --- Phase 4: --print-completion wiring ------------------------------------


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
