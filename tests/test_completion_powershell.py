"""Tests for PowerShell completion generation (F9).

Assertions carry the weight (pwsh is absent on CI images); a syntax smoke-check
runs only when ``pwsh`` is on PATH. Also covers ``--print-completion powershell``
wiring and the ``_psq`` single-quote-doubling escaping discipline.
"""

import pathlib
import typing as ty

import pytest

import duho
import duho.completion as completion
from duho import Args


class Deploy(Args):
    """Deploy the application."""

    mode: ty.Literal["fast", "slow", "auto"] = "fast"
    "Deployment mode"
    ("--mode",)

    target: pathlib.Path = pathlib.Path(".")
    "Target directory"
    ("--target",)


class PShellApp(Args):
    """Example app with a subcommand tree."""

    _subcommands_ = [Deploy]


class PShellCompletionApp(Args):
    """Same tree, opted into --print-completion."""

    _completion_ = True
    _subcommands_ = [Deploy]


def test_powershell_script_content():
    script = completion.powershell(PShellApp._parser_())

    assert isinstance(script, str)
    assert script.strip()
    assert "Register-ArgumentCompleter" in script
    assert "-Native" in script
    assert "CompletionResult" in script
    assert "PShellApp" in script
    assert "Deploy" in script
    assert "fast" in script  # a Literal choice value
    # No unrendered Python placeholders leaked into the output.
    assert "{prog}" not in script
    assert "{choices}" not in script
    assert "{flags}" not in script


def test_powershell_is_registered_emitter():
    assert "powershell" in completion.__all__
    assert hasattr(completion, "powershell")


def test_powershell_psq_escapes_single_quotes():
    # A hostile choice with a single quote must be doubled, never left able to
    # break out of the surrounding single-quoted literal.
    assert completion._psq("it's") == "'it''s'"
    assert completion._psq("plain") == "'plain'"


def test_powershell_escapes_hostile_choice():
    class Hostile(Args):
        """App with a nasty choice value."""

        mode: ty.Literal["a'b", "$(rm)"] = "a'b"
        "mode"
        ("--mode",)

    script = completion.powershell(Hostile._parser_())
    # The single quote is doubled; the raw unescaped form never appears.
    assert "'a''b'" in script
    # $(rm) is inside a single-quoted (non-interpolating) literal.
    assert "'$(rm)'" in script


def test_print_completion_flag_accepts_powershell(capsys):
    with pytest.raises(SystemExit) as excinfo:
        duho.main(PShellCompletionApp, ["--print-completion", "powershell"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Register-ArgumentCompleter" in out
    assert "PShellCompletionApp" in out


def test_print_completion_lists_powershell_choice():
    parser = PShellCompletionApp._parser_()
    help_text = parser.format_help()
    assert "powershell" in help_text


def test_print_completion_standalone_powershell():
    import io

    buf = io.StringIO()
    duho.print_completion(PShellCompletionApp, "powershell", file=buf)
    out = buf.getvalue()
    assert "Register-ArgumentCompleter" in out
    assert "PShellCompletionApp" in out


def test_powershell_script_syntax_if_pwsh_available():
    """Optional smoke check: skipped unless pwsh is on PATH."""
    import shutil
    import subprocess

    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh not available on this machine")

    script = completion.powershell(PShellApp._parser_())
    # Parse-only: succeeds (True) if the script block is syntactically valid.
    check = (
        "$ErrorActionPreference='Stop'; "
        "$null=[System.Management.Automation.Language.Parser]::ParseInput("
        "$args[0], [ref]$null, [ref]$null); 'ok'"
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", check, script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
