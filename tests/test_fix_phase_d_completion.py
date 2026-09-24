"""Phase D regression tests: completion emitter escaping + form (M8, fish)."""

import os
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


# -- zsh multi-flag optspec form: quoted exclusion-list + brace-expansion ----


def test_zsh_multiflag_optspec_form():
    script = _script("zsh")
    # Correct exclusion-list + brace-expansion form -- both the exclusion
    # list AND the individual flags in the brace are quoted (an unquoted
    # flag interpolated raw into the script body could inject shell code the
    # moment the script is sourced, not just at Tab-time).
    assert "'(-v --verbose)'{'-v','--verbose'}" in script
    # The old invalid quoted-pipe brace must be gone.
    assert "'{-v|--verbose}'" not in script
    # The old fully-unquoted brace form must be gone too.
    assert "{-v,--verbose}" not in script


# -- Hostile choice values are escaped for BOTH the static parse and any --
# -- second (dynamic) evaluation zsh/fish perform at Tab-time. -------------


def test_bash_choices_neutralize_command_substitution():
    script = _script("bash")
    # The '$' in a hostile choice is backslash-escaped so compgen -W (which
    # expands its word list) cannot run the substitution.
    assert "\\$(touch pwned)" in script
    # And the raw, unescaped command substitution must NOT appear in a word list.
    assert '-W "$(touch pwned)' not in script


def test_zsh_choice_escaped_for_the_dynamic_eval_too():
    """zsh's `_arguments` evaluates a choice list a SECOND time; a value must
    survive that pass literally, not just the static script parse."""
    script = _script("zsh")
    # `$` and `(`/`)` from the hostile choice must not appear un-escaped.
    assert "$(touch pwned)" not in script
    assert "\\$\\(touch\\ pwned\\)" in script
    # The apostrophe choice is escaped for the dynamic eval (backslash) before
    # being wrapped for the static parse (the doubled '\'' quote dance).
    assert "it\\'\\''s" in script


def test_fish_choice_escaped_for_the_dynamic_eval_too():
    """fish expands a `complete -a` argument a SECOND time at Tab-time; a
    value must survive that pass literally too. The backslashes from that
    first (dynamic-eval) escaping are themselves doubled by `_fsq`'s
    static-parse quoting (it escapes `\\` before `'`), so the hostile
    choice's `\\$`/`\\(`/`\\)` each end up as TWO backslashes here."""
    script = _script("fish")
    assert "$(touch pwned)" not in script
    assert "\\\\$\\\\(touch\\\\ pwned\\\\)" in script
    assert "it\\\\\\'s" in script


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
    assert "-o 'rc'" in script
    # It must NOT be emitted as a single-char short flag.
    assert "-s 'rc'" not in script


# -- Shell syntax + execution smoke checks (skip if shell absent) ------------


def _is_real_bash(path: str) -> bool:
    """A real bash binary is several MB; the WSL launcher shims Windows
    installs both under System32 AND at the WindowsApps app-execution-alias
    path (which `shutil.which` can return FIRST under PowerShell) are
    near-zero-byte stub/reparse files that hang or misbehave when driven
    non-interactively via subprocess."""
    try:
        return os.path.getsize(path) > 4096
    except OSError:
        return False


def _find_bash() -> "str | None":
    """Resolve a real bash deterministically (see `_is_real_bash`)."""
    found = shutil.which("bash")
    if found and _is_real_bash(found):
        return found
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = shutil.which("bash", path=directory)
        if candidate and _is_real_bash(candidate):
            return candidate
    return None


def test_bash_completion_does_not_execute_hostile_choice(tmp_path):
    """Driving the bash completion with a hostile choice must NOT run it.

    Calls the REAL registered function name (`completion._bash_func_name`),
    not a guessed one -- an earlier version of this test called a name the
    emitter never defines, so it always exited 127 ("command not found")
    and the assertion passed vacuously no matter what the emitter did. Also
    runs with cwd=tmp_path and a relative marker name so a regression can't
    write a stray file into the repo root.
    """
    bash_path = _find_bash()
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
    parser.prog = "_Attack"
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = ("$(touch pwned)", "safe")
    script = completion.bash(parser)
    func = completion._bash_func_name(parser.prog)

    harness = script + (
        f'\nCOMP_WORDS=({parser.prog} --mode "")\nCOMP_CWORD=2\n{func}\n'
        "printf '%s\\n' \"${COMPREPLY[@]}\"\n"
    )
    result = subprocess.run(
        [bash_path, "-c", harness],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert list(os.listdir(tmp_path)) == []
    assert any("touch" in c for c in result.stdout.splitlines())


def test_bash_script_valid_with_hostile_choices():
    bash_path = _find_bash()
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
