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


# -- ASCII-only script body: the console's OEM code page corrupts non-ASCII --
# -- text piped through `| Out-String | Invoke-Expression` even when duho ----
# -- writes correct UTF-8/text; a pure-ASCII script sidesteps it entirely. ---


def test_psq_emits_pure_ascii_for_non_ascii_text():
    assert completion._psq("rápido").isascii()
    assert "[char]0x00E1" in completion._psq("rápido")
    assert completion._psq("ω").isascii()
    assert "[char]0x03C9" in completion._psq("ω")


def test_psq_handles_an_astral_character_as_a_surrogate_pair():
    # An astral character (outside the BMP) needs a UTF-16 surrogate PAIR,
    # not a single `[char]0xNNNN` (that only holds one UTF-16 code unit).
    emoji = "\U0001f600"
    result = completion._psq(emoji)
    assert result.isascii()
    assert result.count("[char]0x") == 2


def test_powershell_script_is_pure_ascii_for_a_non_ascii_choice():
    class Latin(Args):
        """App with a non-ASCII Literal choice."""

        mode: ty.Literal["rápido", "lento"] = "lento"
        "mode"
        ("--mode",)

    script = completion.powershell(Latin._parser_())
    assert script.isascii()
    assert "[char]0x00E1" in script


# -- Inserted completion TEXT is quoted/escaped separately from the script --
# -- body: `_psq` only protects the SCRIPT; the candidate a user accepts on --
# -- Tab must not be able to split into extra args or run on Enter. ---------


def test_powershell_quotes_every_candidate_unconditionally():
    """Every candidate is now always single-quoted (never conditionally, on
    a character-class match): a candidate containing a Unicode
    "smart quote" (never in the old ASCII metacharacter class) used to be
    inserted completely unquoted, letting it close out of the argument."""
    script = completion.powershell(PShellApp._parser_())
    assert "CompletionResult" in script
    # The doubled-character class covers the ASCII quote and PowerShell's
    # Unicode single-quote-equivalent range (U+2018-U+201B).
    assert "\\u2018\\u2019\\u201A\\u201B" in script
    # No more conditional "does this need quoting" branch.
    assert "-cmatch '[\\s`" not in script


def test_powershell_case_sensitive_comparisons():
    """Argparse is case-sensitive; the old case-insensitive `-eq`/`-contains`/
    `switch` let `tool CONVERT -<TAB>` complete a subcommand argparse would
    reject."""
    script = completion.powershell(PShellApp._parser_())
    assert "-ceq" in script
    assert "-ccontains" in script
    assert "-CaseSensitive" in script


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
