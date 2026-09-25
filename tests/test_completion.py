"""Tests for shell completion generation (bash/zsh/fish/powershell)."""

import argparse
import enum
import os
import pathlib
import re
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
    """A documented public function builds the CompletionSpec tree,
    not just the private `_walk`."""
    parser = App._parser_()
    assert completion.spec(parser) == completion._walk(parser)
    assert isinstance(completion.spec(parser), completion.CompletionSpec)


def test_completion_spec_positional_field_order_matches_the_documented_prefix():
    """``CompletionSpec`` is a plain dataclass, so positional construction
    binds by position -- ``path`` (added after the original design) must sit
    LAST, not in 2nd position, so it doesn't shift ``options``/
    ``positionals``/``subcommands``/``help`` for a positional caller."""
    spec = completion.CompletionSpec(
        "myprog",  # prog
        [],  # options
        [],  # positionals
        {},  # subcommands
        "a help string",  # help
        ("Sub",),  # path
    )
    assert spec.prog == "myprog"
    assert spec.options == []
    assert spec.positionals == []
    assert spec.subcommands == {}
    assert spec.help == "a help string"
    assert spec.path == ("Sub",)


def test_walk_skips_suppressed_option_and_subcommand():
    """An option or subcommand hidden via help=SUPPRESS never reaches
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


def test_walk_hides_an_alias_of_a_suppressed_subcommand_too():
    """A `help=argparse.SUPPRESS` pseudo-action exists only for the PRIMARY
    name passed to `add_parser` -- never per-alias -- so keying suppression
    off that dest name alone left an alias of a hidden subcommand fully
    completable. Suppression must follow the underlying parser object,
    since every alias maps to the same one."""
    parser = argparse.ArgumentParser(prog="aliasapp")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("hidden", help=argparse.SUPPRESS, aliases=["hid"])
    sub.add_parser("visible", help="a visible one")

    top = completion.spec(parser)
    assert "hidden" not in top.subcommands
    assert "hid" not in top.subcommands
    assert "visible" in top.subcommands


def test_walk_keeps_a_hidden_positional_slot_but_offers_no_candidates():
    """A positional hidden via `help=argparse.SUPPRESS` must still occupy
    its ordinal slot -- every emitter counts positions sequentially to know
    which one is pending, so dropping the entry entirely shifted every
    later positional's completions one slot early."""
    parser = argparse.ArgumentParser(prog="hiddenpos")
    parser.add_argument("secret", help=argparse.SUPPRESS)
    parser.add_argument("color", choices=["red", "blue"])

    top = completion.spec(parser)
    assert len(top.positionals) == 2
    secret, color = top.positionals
    assert secret.hidden is True
    assert secret.choices is None
    assert color.hidden is False
    assert color.choices == ("red", "blue")


def test_enum_field_gets_completion_choices():
    """An Enum-typed field offers its member names as choices, even
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
    """Two progs that only differ in punctuation get DIFFERENT bash
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


def test_zsh_sibling_function_names_do_not_collide():
    """Two sibling subcommands that only differ in punctuation (`a-b`/`a_b`)
    both sanitise to the same identifier fragment; without a hash suffix per
    path segment, the second definition silently overwrote the first zsh
    function, so completion for the first subcommand dispatched into the
    second's instead."""

    class ADash(Args):
        """a-b"""

        dash: bool = False
        "dash-only flag"
        ("--dash",)

    class AUnder(Args):
        """a_b"""

        under: bool = False
        "under-only flag"
        ("--under",)

    class Root(Args):
        """root"""

        _subcommands_ = [ADash, AUnder]

    ADash._parsername_ = "a-b"
    AUnder._parsername_ = "a_b"
    parser = Root._parser_()
    script = completion.zsh(parser)
    funcids = set(re.findall(r"^(_[A-Za-z0-9_]+) \(\) \{", script, re.MULTILINE))
    # One function per node (root + the two subcommands); no two collapse to
    # the same name despite `a-b`/`a_b` sanitising identically.
    assert len(funcids) == 3


# --- Hostile choice values are escaped, not executed -----------------------


class _Danger(Args):
    """App with a multi-flag value option and hostile choice values."""

    mode: ty.Literal["it's", "safe", "$(touch pwned)"] = "safe"
    "Mode"
    ("--mode",)

    verbose: int = 0
    "Verbosity"
    ("-v", "--verbose")


def _danger_script(shell):
    return getattr(completion, shell)(_Danger._parser_())


def test_zsh_multiflag_optspec_form():
    script = _danger_script("zsh")
    # Correct exclusion-list + brace-expansion form -- both the exclusion
    # list AND the individual flags in the brace are quoted (an unquoted
    # flag interpolated raw into the script body could inject shell code the
    # moment the script is sourced, not just at Tab-time).
    assert "'(-v --verbose)'{'-v','--verbose'}" in script
    # The old invalid quoted-pipe brace must be gone.
    assert "'{-v|--verbose}'" not in script
    # The old fully-unquoted brace form must be gone too.
    assert "{-v,--verbose}" not in script


def test_bash_choices_neutralize_command_substitution():
    script = _danger_script("bash")
    # The '$' in a hostile choice is backslash-escaped so compgen -W (which
    # expands its word list) cannot run the substitution.
    assert "\\$(touch pwned)" in script
    # And the raw, unescaped command substitution must NOT appear in a word list.
    assert '-W "$(touch pwned)' not in script


def test_zsh_choice_escaped_for_the_dynamic_eval_too():
    """zsh's `_arguments` evaluates a choice list a SECOND time; a value must
    survive that pass literally, not just the static script parse."""
    script = _danger_script("zsh")
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
    script = _danger_script("fish")
    assert "$(touch pwned)" not in script
    assert "\\\\$\\\\(touch\\\\ pwned\\\\)" in script
    assert "it\\\\\\'s" in script


def test_fish_word_always_escapes_percent():
    """Unlike zsh, fish expands a bare `%self`/`%<job>` job-id token
    even inside an already-`_fish_word`-escaped value, so `%` cannot share
    zsh's safe set -- it must always be backslash-escaped for fish's
    dynamic (second) evaluation."""
    assert completion._fish_word("%self") == "\\%self"
    # zsh's escaper is unaffected -- `%` stays in ITS safe set.
    assert completion._zsh_word("%self") == "%self"


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


class _OldFlag(Args):
    """App with an old-style single-dash multi-char flag and a documented sub."""

    rc: str = ""
    "Old-style flag"
    ("-rc", "--runconfig")


def test_fish_oldstyle_flag_uses_o():
    """A single-dash multi-char flag (``-rc``) uses fish's ``-o``, never ``-s``
    (which is reserved for single-char short flags)."""
    script = completion.fish(_OldFlag._parser_())
    assert "-o 'rc'" in script
    assert "-s 'rc'" not in script


def test_bash_completion_does_not_execute_hostile_choice(tmp_path):
    """Driving the bash completion with a hostile choice must NOT run it.

    Calls the REAL registered function name (`completion._bash_func_name`),
    not a guessed one -- an earlier version of this test called a name the
    emitter never defines, so it always exited 127 ("command not found")
    and the assertion passed vacuously no matter what the emitter did. Also
    runs with cwd=tmp_path and a relative marker name so a regression can't
    write a stray file into the repo root.
    """
    if not _BASH:
        pytest.skip("bash not available on this machine")

    marker = tmp_path / "pwned"

    class _Attack(Args):
        """attack"""

        mode: ty.Literal["safe"] = "safe"  # placeholder; real value injected below
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
        [_BASH, "-c", harness],
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
    if not _BASH:
        pytest.skip("bash not available on this machine")
    script = _danger_script("bash")
    result = subprocess.run([_BASH, "-n", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_zsh_script_valid_if_available():
    zsh_path = shutil.which("zsh")
    if not zsh_path:
        pytest.skip("zsh not available")
    script = _danger_script("zsh")
    result = subprocess.run(
        [zsh_path, "-n", "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_fish_script_valid_if_available():
    fish_path = shutil.which("fish")
    if not fish_path:
        pytest.skip("fish not available")
    script = _danger_script("fish")
    result = subprocess.run(
        [fish_path, "--no-execute", "/dev/stdin"],
        input=script,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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


# --- stale-docs regression --------------------------------------------------


def test_walk_docstring_mentions_all_four_emitters():
    """The internal walk's docstring used to say 'three emitters',
    stale since the PowerShell emitter was added."""
    assert "three emitters" not in (completion._walk.__doc__ or "")
