"""Phase D regression tests: completion emitter escaping + form (C12, M2, M8, fish)."""

import shutil
import subprocess
import typing as ty

import pytest

import duho.completion as completion
from duho import Args


class _Danger(Args):
    """App with a multi-flag value option and hostile choice values."""

    mode: ty.Literal["it's", "safe", "$(touch pwned)"] = "safe"
    "Mode"
    ("--mode",)

    verbose: int = 0
    "Verbosity"
    ("-v", "--verbose")


def _script(shell):
    return getattr(completion, shell)(_Danger._parser_())


# -- C12: zsh multi-flag optspec form ----------------------------------------


def test_zsh_multiflag_optspec_form():
    script = _script("zsh")
    # Correct exclusion-list + brace-expansion form.
    assert "'(-v --verbose)'{-v,--verbose}" in script
    # The old invalid quoted-pipe brace must be gone.
    assert "'{-v|--verbose}'" not in script


# -- M2: hostile choice values are escaped -----------------------------------


def test_bash_choices_neutralize_command_substitution():
    script = _script("bash")
    # The '$' in a hostile choice is backslash-escaped so compgen -W (which
    # expands its word list) cannot run the substitution.
    assert "\\$(touch pwned)" in script
    # And the raw, unescaped command substitution must NOT appear in a word list.
    assert "-W \"$(touch pwned)" not in script


def test_zsh_choice_single_quote_escaped():
    script = _script("zsh")
    # A single quote in a choice is escaped '\'' so it cannot break the script.
    assert "it'\\''s" in script


def test_fish_choice_single_quote_escaped():
    script = _script("fish")
    assert "it'\\''s" in script


def test_prog_with_whitespace_rejected():
    parser = _Danger._parser_()
    parser.prog = "evil prog"
    with pytest.raises(ValueError):
        completion.bash(parser)


def test_prog_with_metachar_rejected():
    parser = _Danger._parser_()
    parser.prog = "evil$(x)"
    with pytest.raises(ValueError):
        completion.zsh(parser)


# -- fish: single-dash multi-char flag uses -o, description is the help -------


class _OldFlag(Args):
    """App with an old-style single-dash multi-char flag and a documented sub."""

    rc: str = ""
    "Old-style flag"
    ("-rc", "--runconfig")


def test_fish_oldstyle_flag_uses_o():
    script = completion.fish(_OldFlag._parser_())
    assert "-o 'rc'" in script or "-o rc" in script
    # It must NOT be emitted as a single-char short flag.
    assert "-s 'rc'" not in script


# -- Shell syntax smoke checks (skip if shell absent) ------------------------


def test_bash_completion_does_not_execute_hostile_choice(tmp_path):
    """Driving the bash completion with a hostile choice must NOT run it (M2)."""
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not available")

    import typing as _ty

    from duho import Args as _Args

    marker = tmp_path / "pwned"

    class _Attack(_Args):
        """attack"""

        mode: _ty.Literal["safe"] = "safe"  # placeholder; real value injected below
        "m"
        ("--mode",)

    # Inject a hostile choice directly on the built parser's action.
    parser = _Attack._parser_()
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = (f"$(touch {marker})", "safe")
    script = completion.bash(parser)

    harness = script + (
        "\nCOMP_WORDS=(_Attack --mode \"\")\nCOMP_CWORD=2\n_Attack\n"
    )
    subprocess.run([bash_path, "-c", harness], capture_output=True, text=True)
    assert not marker.exists()


def test_bash_script_valid_with_hostile_choices():
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not available")
    script = _script("bash")
    result = subprocess.run(
        [bash_path, "-n", "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_zsh_script_valid_if_available():
    zsh_path = shutil.which("zsh")
    if not zsh_path:
        pytest.skip("zsh not available")
    script = _script("zsh")
    result = subprocess.run(
        [zsh_path, "-n", "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_fish_script_valid_if_available():
    fish_path = shutil.which("fish")
    if not fish_path:
        pytest.skip("fish not available")
    script = _script("fish")
    result = subprocess.run(
        [fish_path, "--no-execute", "/dev/stdin"],
        input=script,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
