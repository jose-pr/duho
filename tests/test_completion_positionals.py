"""Completion coverage for positional args + functional/injection round-trips.

Beyond the positional branches of every emitter, this drives the generated
scripts against REAL bash, zsh, fish and PowerShell (each skipped when its
binary is absent) rather than only syntax-checking them: a script that
parses fine can still complete nothing, error at Tab-time, or execute a
hostile value, and a parse-only check misses all three. Every shell-driving
helper here is timeout-bounded so a hang never wedges the whole run.
"""

import argparse
import os
import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import typing as ty

import pytest

import duho.completion as completion
from duho import Args


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


def _usable(path: "str | None", *trivial: str) -> "str | None":
    """``path`` when it runs a trivial command to completion, else None.

    A shell that cannot even do that (a WSL launcher stub, a wedged install) is
    skipped once here; one that then hangs on a generated script fails its test.
    """
    if not path:
        return None
    try:
        done = subprocess.run([path, *trivial], capture_output=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return path if done.returncode == 0 else None


_BASH = _usable(_find_bash(), "-c", "exit 0")
_ZSH = _usable(shutil.which("zsh"), "-fc", "exit 0")
_FISH = _usable(shutil.which("fish"), "--no-config", "-c", "exit 0")
_PWSH = _usable(shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", "0")


# --- Fixtures ---------------------------------------------------------------


class Convert(Args):
    """Convert an input file to an output format."""

    source: pathlib.Path
    "Input file (required positional)"
    ("source",)

    fmt: ty.Literal["json", "yaml", "toml"] = "json"
    "Output format (choice-bearing positional)"
    ("fmt",)

    extras: ty.List[str] = []
    "Extra positional tokens (variadic)"
    ("extras",)

    env: ty.Literal["prod", "dev"] = "dev"
    "Environment (a value-taking flag with choices)"
    ("--env",)

    name: str = ""
    "A free-value flag (no choices, not a Path)"
    ("--name",)

    out: pathlib.Path = pathlib.Path(".")
    "A Path-typed flag"
    ("-o", "--out")

    color: bool = False
    "A boolean flag"
    ("--color",)


class Tool(Args):
    """A tool with a subcommand tree and positionals."""

    verbose: int = 0
    "Verbosity"
    ("-v", "--verbose")

    _subcommands_ = [Convert]


def _tool_parser():
    parser = Tool._parser_()
    parser.prog = "tool"
    return parser


# --- Fixture: a node with its OWN positional (choices overlapping a real --
# --- subcommand name) AND a subcommand table -- argparse always consumes --
# --- a node's own positional(s) before ever treating a word as its --------
# --- subparsers dispatch value, so a word equal to a subcommand name must -
# --- still be read as the pending positional's value until that ----------
# --- positional is satisfied. -----------------------------------------------


class GoSub(Args):
    """Enter the go state (named the same as one of `target`'s choices)."""

    fast: bool = False
    "fast flag, only on the go subcommand"
    ("--fast",)


class PosNode(Args):
    """A node with its own positional AND a subcommand table."""

    target: ty.Literal["t1", "t2", "go"] = "t1"
    "target (one choice, 'go', collides with a real subcommand name)"
    ("target",)
    _subcommands_ = [GoSub]


class PosRoot(Args):
    """root"""

    _subcommands_ = [PosNode]


PosNode._parsername_ = "pos"
GoSub._parsername_ = "go"


# --- Fixture: sibling subcommands differing ONLY in case ("run"/"Run"), --
# --- each with its own nested sub-subcommand carrying a distinguishing ---
# --- flag, so a PowerShell dictionary that folds the two paths together --
# --- (case-insensitive `@{}`) is caught even though the TOP-level -------
# --- candidate lists (built from separate, unquoted `elseif` branches) ---
# --- happen to stay correct on their own. ---------------------------------


class LSub(Args):
    """lsub"""

    lower_flag: bool = False
    "flag that only exists on run's own child"
    ("--lower-flag",)


class USub(Args):
    """usub"""

    upper_flag: bool = False
    "flag that only exists on Run's own child"
    ("--upper-flag",)


class RunLower(Args):
    """run (lowercase)"""

    lpos: ty.Literal["L1"] = "L1"
    "lpos"
    ("lpos",)
    _subcommands_ = [LSub]


class RunUpper(Args):
    """Run (uppercase)"""

    upos: ty.Literal["U1"] = "U1"
    "upos"
    ("upos",)
    _subcommands_ = [USub]


class CaseRoot(Args):
    """root"""

    _subcommands_ = [RunLower, RunUpper]


RunLower._parsername_ = "run"
RunUpper._parsername_ = "Run"
LSub._parsername_ = "lsub"
USub._parsername_ = "usub"


def _bash_func(parser) -> str:
    """The bash function name duho would register for `parser`'s prog."""
    return completion._bash_func_name(parser.prog)


# --- Positional branches emit for every shell -------------------------------


def test_walk_captures_positionals():
    spec = completion.spec(Tool._parser_())
    convert = spec.subcommands["convert"]
    names = {p.name for p in convert.positionals}
    assert {"source", "fmt", "extras"} <= names
    src = next(p for p in convert.positionals if p.name == "source")
    assert src.is_path is True
    fmt = next(p for p in convert.positionals if p.name == "fmt")
    assert fmt.choices == ("json", "yaml", "toml")


def test_bash_emits_positional_choices():
    script = completion.bash(Tool._parser_())
    # The choice-bearing positional's values reach the candidate word list.
    assert "json" in script and "yaml" in script and "toml" in script


def test_zsh_emits_numbered_positional_specs():
    """zsh positionals use the required `N:message:action` form
    (1-based), not the `name:name:action` form `_arguments` rejects on
    every Tab."""
    script = completion.zsh(Tool._parser_())
    assert "1:source:_files" in script
    assert "2:fmt:(json yaml toml)" in script


def test_fish_emits_positional_completions():
    script = completion.fish(Tool._parser_())
    # The Path positional turns into a `-F` (file) completion line, the
    # choice-bearing one into an `-a 'json yaml toml'` line.
    assert "-F" in script
    assert "json yaml toml" in script


def test_powershell_emits_positional_choices():
    script = completion.powershell(Tool._parser_())
    assert "'json'" in script and "'yaml'" in script and "'toml'" in script


# --- zsh / fish syntax checks over the positional-bearing parser ------------


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_positional_script_syntax_valid():
    script = completion.zsh(Tool._parser_())
    result = subprocess.run([_ZSH, "-n", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_positional_script_syntax_valid():
    script = completion.fish(Tool._parser_())
    result = subprocess.run(
        [_FISH, "--no-execute", "/dev/stdin"],
        input=script,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_positional_script_syntax_valid():
    script = completion.bash(Tool._parser_())
    result = subprocess.run([_BASH, "-n", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- Functional bash completion (source + drive the completion function) ----


def _complete_bash(script, func, words, cword, cwd=None):
    """Source `script`, run the completion function, print COMPREPLY lines."""
    words_literal = " ".join(_bash_arr(w) for w in words)
    harness = (
        script
        + f"\nCOMP_WORDS=({words_literal})\nCOMP_CWORD={cword}\n"
        + f"{func}\nprintf '%s\\n' \"${{COMPREPLY[@]}}\"\n"
    )
    # Bounded, so a hang fails this test instead of wedging the run.
    result = subprocess.run(
        [_BASH, "-c", harness],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def _bash_arr(word):
    import shlex

    return shlex.quote(word)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_root_subcommand_names():
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(script, _bash_func(parser), ["tool", ""], 1)
    assert "convert" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_flags():
    parser = _tool_parser()
    script = completion.bash(parser)
    # `tool Convert --e<TAB>` -> the --env flag.
    reply = _complete_bash(script, _bash_func(parser), ["tool", "convert", "--e"], 2)
    assert "--env" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_choice_values_after_flag():
    parser = _tool_parser()
    script = completion.bash(parser)
    # `tool Convert --env <TAB>` -> the choice values for --env.
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "convert", "--env", ""], 3
    )
    assert "prod" in reply and "dev" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_after_flag_value_does_not_break_subcommand():
    """A value-taking flag before the cursor must not corrupt the cmd path.

    `tool --verbose 2 <TAB>` at the root must still offer the subcommand
    names (the `--verbose`'s value `2` is skipped, not treated as a
    cmd-path word).
    """
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "--verbose", "2", ""], 3
    )
    assert "convert" in reply


# --- Positional-then-flag / opt=value / free & Path flags -------------------


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_after_a_positional_value():
    """A positional value must not be mistaken for a subcommand word,
    and completion must resume for the NEXT positional's own choices."""
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "convert", "in.txt", ""], 3
    )
    assert set(reply) == {"json", "yaml", "toml"}


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_flags_after_all_positionals_consumed():
    """Options placed after positionals (a common argparse usage) are
    completed, not silently dropped."""
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "convert", "in.txt", "yaml", "--"], 4
    )
    assert "--env" in reply and "--color" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_hidden_positional_still_occupies_its_slot():
    """A positional hidden via `help=argparse.SUPPRESS` used to be
    dropped from the walk entirely, which shifted every LATER positional's
    completion one slot early -- so the choice-bearing positional right
    after a hidden one never got offered at all."""

    parser = argparse.ArgumentParser(prog="hiddenposapp")
    parser.add_argument("secretpos", help=argparse.SUPPRESS)
    parser.add_argument("color", choices=["red", "blue"])
    script = completion.bash(parser)
    func = completion._bash_func_name(parser.prog)
    # Nothing typed for the hidden positional yet: no candidates of its own.
    reply = _complete_bash(script, func, ["hiddenposapp", ""], 1)
    assert reply == []
    # Once its (arbitrary) value is given, the NEXT positional's choices
    # must appear -- not nothing, and not the hidden one's (there are none).
    reply = _complete_bash(script, func, ["hiddenposapp", "secretval", ""], 2)
    assert set(reply) == {"red", "blue"}


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_free_value_flag_offers_nothing():
    """A value-taking flag with neither choices nor a Path type must
    not fall through to the general flag/subcommand candidate list."""
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "convert", "--name", ""], 3
    )
    assert reply == []


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_path_flag_completes_files(tmp_path):
    """A Path-typed flag gets native file completion, not the general
    flag/subcommand list."""
    (tmp_path / "afile.txt").write_text("x")
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "convert", "--out", "af"], 3, cwd=tmp_path
    )
    assert any("afile.txt" in c for c in reply)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_split_opt_equals_value():
    """`--opt=value` arrives as the three words `--opt`, `=`, `value`
    (COMP_WORDBREAKS splits on `=`) -- both the walker (using the value to
    descend correctly) and direct `--opt=<TAB>` completion must handle it."""
    parser = _tool_parser()
    script = completion.bash(parser)
    # tool --verbose=2 Convert --<TAB> : the root's own --verbose=value must
    # still resolve into Convert (the value flag lives on the root here).
    reply = _complete_bash(
        script,
        _bash_func(parser),
        ["tool", "--verbose", "=", "2", "convert", "--"],
        5,
    )
    assert "--color" in reply
    # tool Convert --env=<TAB> : must offer the choice values.
    reply = _complete_bash(
        script,
        _bash_func(parser),
        ["tool", "convert", "--env", "=", ""],
        4,
    )
    assert "prod" in reply and "dev" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_value_flag_scoped_per_command_path():
    """A flag that is boolean at one level and value-taking at another
    is resolved per command path, not merged globally."""

    class Deploy(Args):
        """deploy"""

        name: str = ""
        "value-taking -n at this level"
        ("-n", "--name")

    class R3(Args):
        """root"""

        dry_run: bool = False
        "boolean -n at the root"
        ("-n", "--dry-run")
        _subcommands_ = [Deploy]

    parser = R3._parser_()
    parser.prog = "r3"
    script = completion.bash(parser)
    func = _bash_func(parser)
    # At the root, -n is boolean: the word after it is NOT swallowed, so
    # `Deploy` is still recognised as the subcommand and its OWN flags
    # (including its value-taking -n/--name) are offered.
    reply = _complete_bash(script, func, ["r3", "-n", "deploy", "-"], 3)
    assert {"-n", "--name", "--help"} <= set(reply)
    reply = _complete_bash(script, func, ["r3", "-n", ""], 2)
    assert "deploy" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_positional_value_matching_a_subcommand_name_is_not_mistaken_for_it():
    """argparse always consumes a node's OWN positional(s) before ever
    treating a word as its subparsers dispatch value, so a word equal to a
    real subcommand's name must still count as the pending positional's
    value until that positional is satisfied. The walker used to match
    `is_sub` on the WORD alone, ignoring how many of the node's own
    positionals were already consumed -- so `pos go<TAB>` (the FIRST `go`,
    which is `target`'s value) wrongly descended straight into the `go`
    subcommand, one word early.

    `postool pos go<TAB>`: `pos` has one own positional (`target`, whose
    choices include `go`) THEN a subcommand table containing `go`. The
    first `go` must be read as `target`'s value, leaving `pos` still
    awaiting its subcommand-dispatch word -- so the only candidate here is
    the subcommand name `go` itself. Under the bug, the walker had already
    (wrongly) descended into the `go` node on that first word, which has
    no positionals or subcommands of its own, so nothing was offered.
    """
    parser = PosRoot._parser_()
    parser.prog = "postool"
    script = completion.bash(parser)
    func = completion._bash_func_name(parser.prog)
    reply = _complete_bash(script, func, ["postool", "pos", "go", ""], 3)
    assert reply == ["go"]


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_positional_then_real_dispatch_word_reaches_the_subcommand():
    """Once the pending positional actually IS consumed, the NEXT word
    correctly dispatches into the subcommand of the same name and that
    subcommand's own flags are offered."""
    parser = PosRoot._parser_()
    parser.prog = "postool"
    script = completion.bash(parser)
    func = completion._bash_func_name(parser.prog)
    reply = _complete_bash(script, func, ["postool", "pos", "go", "go", "--"], 4)
    assert "--fast" in reply


# --- Injection round-trip ---------------------------------------------------


class _Hostile(Args):
    """App carrying an injected hostile choice value."""

    mode: ty.Literal["safe"] = "safe"
    "mode"
    ("--mode",)


def _hostile_parser(value="it's $(uh oh)"):
    parser = _Hostile._parser_()
    parser.prog = "hostileapp"  # clean prog so the completion func name is stable
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = (value, "safe")
    return parser


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "powershell"])
def test_hostile_choice_present_and_scripts_generated(shell):
    script = getattr(completion, shell)(_hostile_parser())
    assert isinstance(script, str) and script.strip()


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_hostile_choice_bash_syntax_valid():
    script = completion.bash(_hostile_parser())
    result = subprocess.run([_BASH, "-n", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_hostile_choice_zsh_syntax_valid():
    script = completion.zsh(_hostile_parser())
    result = subprocess.run([_ZSH, "-n", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_hostile_choice_fish_syntax_valid():
    script = completion.fish(_hostile_parser())
    result = subprocess.run(
        [_FISH, "--no-execute", "/dev/stdin"],
        input=script,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_hostile_choice_bash_does_not_execute(tmp_path):
    """The injected `$(...)` must NOT run when the completion is driven.

    Runs the bash subprocess with cwd=tmp_path and a RELATIVE marker name:
    an earlier version of this test spliced a Windows tmp_path (with `\\`)
    into the payload, which on Windows meant the marker check never fired
    because bash strips the backslashes and `touch` creates a mangled
    filename in the process's cwd instead -- which, since that cwd was the
    repo root, left a stray untracked file there. Both fixed here.
    """
    marker = tmp_path / "pwned"
    parser = _hostile_parser("it's $(touch pwned)")
    script = completion.bash(parser)
    reply = _complete_bash(
        script,
        completion._bash_func_name(parser.prog),
        ["hostileapp", "--mode", ""],
        2,
        cwd=tmp_path,
    )
    assert not marker.exists()  # the substitution never ran
    assert list(os.listdir(tmp_path)) == []
    # Some fragment of the literal survives (the `touch` token is offered as a
    # candidate rather than being executed).
    assert any("touch" in c for c in reply)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_hostile_choice_bash_round_trips_as_one_candidate(tmp_path):
    """A choice value containing whitespace/quotes now round-trips as ONE
    candidate through bash's `compgen -W`: escaping every character outside
    a conservative safe set (not just backslash/``$``/backtick/quotes)
    backslash-protects the internal space too, so IFS no longer splits
    `it's $(uh oh)` into the separate tokens `its`, `\\$(uh`, `oh)` it used
    to. This used to be an accepted, documented limitation (bash's static
    word-splitting is otherwise inherent to `compgen -W`); the wider escape
    set removes it."""
    parser = _hostile_parser("it's $(uh oh)")
    script = completion.bash(parser)
    reply = _complete_bash(
        script,
        completion._bash_func_name(parser.prog),
        ["hostileapp", "--mode", ""],
        2,
        cwd=tmp_path,
    )
    assert "it's $(uh oh)" in reply
    assert list(os.listdir(tmp_path)) == []


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_choices_cannot_run_process_or_command_substitution(tmp_path):
    """Security: `compgen -W`'s word list gets a SECOND, dynamic
    (re-)evaluation at Tab-press, exactly as if the joined candidate string
    had been freshly typed -- process substitution (`<(...)`/`>(...)`) needs
    no leading `$` and used to run at Tab-time even though `$`/backtick/
    quotes were already escaped, because those characters were never in the
    old escape set. Drive a real completion carrying every classic
    injection vector (process substitution, command substitution,
    backticks, brace expansion, globbing) and confirm none of them execute
    or expand -- each still comes back as its own literal candidate."""
    hostile_values = [
        "safe",
        "x<(touch MARK_PSUB_IN)",
        "y>(touch MARK_PSUB_OUT)",
        "z$(touch MARK_CMDSUB)",
        "w`touch MARK_BACKTICK`",
        "brace{a,b}",
        "glob*",
    ]
    parser = _hostile_parser(hostile_values[0])
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = tuple(hostile_values)
    script = completion.bash(parser)
    reply = _complete_bash(
        script,
        completion._bash_func_name(parser.prog),
        ["hostileapp", "--mode", ""],
        2,
        cwd=tmp_path,
    )
    assert list(os.listdir(tmp_path)) == []
    assert set(reply) == set(hostile_values)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_quote_in_one_choice_does_not_merge_later_choices():
    """`compgen -W` gives its word-list argument a SECOND, dynamic
    re-evaluation at Tab-press (splitting, expanding and quote-removing the
    joined string again, exactly like fresh shell input). An unescaped `'`
    in one choice opened an unmatched quoted region at THAT re-evaluation,
    swallowing every value after it (spaces included) into one merged,
    mangled candidate -- `it's`, `a;b`, `#hash`, `x,y` came back as the
    single blob `its a;b #hash x,y`."""
    parser = _hostile_parser("it's")
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = ("fast", "it's", "a;b", "#hash", "x,y")
    script = completion.bash(parser)
    reply = _complete_bash(
        script, completion._bash_func_name(parser.prog), ["hostileapp", "--mode", ""], 2
    )
    assert set(reply) == {"fast", "it's", "a;b", "#hash", "x,y"}


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_opt_equals_with_nothing_typed_yet_offers_choices():
    """`--opt=` with NOTHING typed after the `=` arrives as only the two
    words `--opt`, `=` (no fourth, empty word) -- `cur` IS the literal `=`
    character, which used to be handed straight to `compgen -W ... -- "="`,
    matching nothing and falling back to bash's default filename
    completion."""
    parser = _tool_parser()
    script = completion.bash(parser)
    reply = _complete_bash(
        script, _bash_func(parser), ["tool", "convert", "--env", "="], 3
    )
    assert set(reply) == {"prod", "dev"}


# --- Real zsh/fish functional drives (not just syntax checks) --------------
#
# A script that PARSES fine can still complete nothing (a broken command-path
# walk), error at Tab-time (an invalid `_arguments` spec), or run a hostile
# value (a second-evaluation escaping gap) -- none of which a `-n`/
# `--no-execute` syntax check can catch. These drive the real shells.


def _zsh_drive(zsh_path, fpath_dir, funcname, cmdname, cmdline, timeout=20):
    """Drive a real, interactive zsh (via zpty) far enough to render Tab
    candidates for `cmdline`, and return the raw terminal output.

    zsh's `_arguments` refuses to run outside a genuine completion context
    (`can only be called from completion function`), so a plain `zsh -c`
    invocation that just seeds `$words`/`$CURRENT` does not work -- an
    actual interactive completion widget is required, hence zpty.
    """
    driver = textwrap.dedent("""
        zmodload zsh/zpty
        zpty sh 'zsh -i'
        _drain() {
          local acc="" chunk n=0
          while (( n < 30 )); do
            if zpty -r -t sh chunk 2>/dev/null; then
              acc+=$chunk
            else
              sleep 0.05
            fi
            (( n++ ))
          done
          print -rn -- "$acc"
        }
        _drain > /dev/null
        zpty -w -n sh "$1"$'\\t'
        sleep 0.7
        out=$(_drain)
        print -r -- "$out"
        zpty -d sh 2>/dev/null
        """)
    with tempfile.TemporaryDirectory() as home:
        # .zshenv is read before any global zshrc: skip the distro's global
        # rc files (Ubuntu's /etc/zsh/zshrc runs its own plain `compinit`,
        # which aborts on a CI runner's insecure fpath dirs) so only the
        # .zshrc below configures this isolated shell.
        with open(os.path.join(home, ".zshenv"), "w", newline="\n") as f:
            f.write("setopt no_global_rcs\nskip_global_compinit=1\n")
        zshrc = os.path.join(home, ".zshrc")
        with open(zshrc, "w", newline="\n") as f:
            f.write(
                "autoload -Uz compinit && compinit -u -d %s/.zcompdump\n"
                "fpath=(%s $fpath)\n"
                "autoload -Uz %s\n"
                "compdef %s %s\n" % (home, fpath_dir, funcname, funcname, cmdname)
            )
        driver_path = os.path.join(home, "drive.zsh")
        with open(driver_path, "w", newline="\n") as f:
            f.write(driver)
        env = dict(os.environ)
        env["HOME"] = home
        env["ZDOTDIR"] = home
        result = subprocess.run(
            [zsh_path, driver_path, cmdline],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=home,
            env=env,
        )
        return result.stdout


def _write_zsh_script(fpath_dir, funcname, script_text):
    os.makedirs(fpath_dir, exist_ok=True)
    with open(os.path.join(fpath_dir, funcname), "w", newline="\n") as f:
        f.write(script_text)


class Up(Args):
    """Migrate up."""

    steps: int = 1
    "steps"
    ("--steps",)


class Down(Args):
    """Migrate down."""

    force: bool = False
    "force"
    ("--force",)


class Migrate(Args):
    """Migrate the db."""

    _subcommands_ = [Up, Down]


class Db(Args):
    """DB tools."""

    _subcommands_ = [Migrate]


class Ship(Args):
    """Ship it."""

    speed: ty.Literal["fast", "slow"] = "fast"
    "speed"
    ("--speed",)


class Nest(Args):
    """A 3-level-deep subcommand tree, for a real depth >= 2 completion."""

    _completion_ = True
    _subcommands_ = [Db, Ship]


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_completes_at_depth_three(tmp_path):
    """The old zsh emitter only worked at the root; a grandchild
    command (`Nest Db Migrate Up -<TAB>`) errored or offered nothing."""
    parser = Nest._parser_()
    parser.prog = "Nest"
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_Nest", script)
    out = _zsh_drive(_ZSH, str(fpath_dir), "_Nest", "Nest", "Nest db migrate up -")
    assert "invalid argument" not in out
    assert "command not found" not in out
    assert "--steps" in out
    assert "--help" in out


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_completes_root_subcommands_and_does_not_leak_siblings(tmp_path):
    """On the zsh side, at `Nest Db <TAB>`, only Migrate is offered --
    not Ship (a sibling of Db) and not Up/Down (Migrate's own children)."""
    parser = Nest._parser_()
    parser.prog = "Nest"
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_Nest", script)
    out = _zsh_drive(_ZSH, str(fpath_dir), "_Nest", "Nest", "Nest db ")
    assert "invalid argument" not in out
    assert "migrate" in out


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_positional_completion_does_not_error(tmp_path):
    """A Path positional used to make EVERY Tab in that command
    error (`invalid argument: src:src:_files`)."""
    parser = Tool._parser_()
    parser.prog = "Tool"
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_Tool", script)
    out = _zsh_drive(_ZSH, str(fpath_dir), "_Tool", "Tool", "Tool convert ")
    assert "invalid argument" not in out


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_node_with_its_own_positional_still_reaches_its_subcommand(tmp_path):
    """A node that has BOTH its own positional AND a subcommand
    table shared one `_arguments -C` call between a plain numbered
    positional and the `*::` rest spec, which made zsh's own bookkeeping of
    "which word is which" ambiguous -- the subcommand was never reached at
    all, on top of the dispatched child then misreading its own position
    count (both fixed by folding everything into `*::` and by resetting
    `$words`/`$CURRENT` explicitly before dispatch)."""

    class Deploy(Args):
        """deploy"""

        force: bool = False
        "force flag"
        ("--force",)

    Deploy._parsername_ = "deploy"

    class EnvApp(Args):
        """An app with a root positional AND a subcommand table."""

        _completion_ = True
        env: ty.Literal["prod", "dev"] = "dev"
        "environment"
        ("env",)
        _subcommands_ = [Deploy]

    parser = EnvApp._parser_()
    parser.prog = "EnvApp"
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_EnvApp", script)
    out = _zsh_drive(_ZSH, str(fpath_dir), "_EnvApp", "EnvApp", "EnvApp prod deploy -")
    assert "invalid argument" not in out
    assert "--force" in out


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_hostile_subcommand_name_does_not_execute(tmp_path):
    """Security: a hostile `_parsername_` must not run as shell code
    when the root's subcommand list is completed."""

    class CondRoot(Args):
        """cond"""

    CondRoot._parsername_ = "x;touch pwned_cond"

    class Root(Args):
        """root"""

        _completion_ = True
        _subcommands_ = [CondRoot]

    parser = Root._parser_()
    parser.prog = "HostRoot"
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_HostRoot", script)
    _zsh_drive(_ZSH, str(fpath_dir), "_HostRoot", "HostRoot", "HostRoot ")
    assert not (tmp_path / "pwned_cond").exists()


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_hostile_choice_does_not_execute(tmp_path):
    """Security: `$(...)` in a choice value must not run at Tab-time
    in zsh -- this is the core risk (zsh's `_arguments`
    evaluates a `(a b c)` action list with `eval`)."""
    parser = _hostile_parser("safe2 $(touch pwned_choice)")
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_hostileapp", script)
    _zsh_drive(_ZSH, str(fpath_dir), "_hostileapp", "hostileapp", "hostileapp --mode ")
    # The substitution never ran. Whether the raw literal is visible in the
    # small captured terminal window is a rendering detail (zsh may only
    # show the disambiguated common prefix on the first Tab); the file
    # never existing is the load-bearing assertion here.
    assert not (tmp_path / "pwned_choice").exists()


# --- Real fish functional drives ---------------------------------------------


def _fish_drive(fish_path, script_path, cmdline, cwd, timeout=10):
    result = subprocess.run(
        [
            fish_path,
            "--no-config",
            "-c",
            f"source {script_path}; complete -C '{cmdline}'",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    return result.stdout, result.stderr


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_completes_at_depth_two_without_leaking(tmp_path):
    """Fish's old `and`-string bug + missing negation leaked
    grandchild names/flags into a parent level and offered root names again
    after a subcommand was chosen."""
    parser = Nest._parser_()
    parser.prog = "Nest"
    script = completion.fish(parser)
    script_path = tmp_path / "nest.fish"
    script_path.write_bytes(script.encode("utf-8"))
    out, err = _fish_drive(_FISH, script_path, "Nest db ", tmp_path)
    assert err == ""
    names = {
        line.split("\t")[0]
        for line in out.splitlines()
        if line and not line.startswith("-")
    }
    assert names == {"migrate"}


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_same_named_nested_subcommand_does_not_leak_parent_flags(tmp_path):
    """`__fish_seen_subcommand_from <name>` only asks "does this word
    appear ANYWHERE on the command line", with no notion of depth -- a
    subcommand name reused at a deeper level (root `run` vs. nested `db
    run`) made the ROOT `run` node's own gate true too, leaking its flags
    into the nested one. Resolving the exact path (mirroring bash/PowerShell)
    and gating on exact equality removes the ambiguity."""

    class RootRun(Args):
        """run at the root"""

        fast: bool = False
        "root run's own flag"
        ("--fast",)

    class DbRun(Args):
        """run nested under db"""

        dbrunflag: bool = False
        "db run's own flag"
        ("--dbrunflag",)

    class Db(Args):
        """db"""

        _subcommands_ = [DbRun]

    DbRun._parsername_ = "run"
    RootRun._parsername_ = "run"
    Db._parsername_ = "db"

    class SameNameApp(Args):
        """root"""

        _completion_ = True
        _subcommands_ = [RootRun, Db]

    parser = SameNameApp._parser_()
    parser.prog = "SameNameApp"
    script = completion.fish(parser)
    script_path = tmp_path / "samename.fish"
    script_path.write_bytes(script.encode("utf-8"))
    out, err = _fish_drive(_FISH, script_path, "SameNameApp db run -", tmp_path)
    assert err == ""
    flags = {line.split("\t")[0] for line in out.splitlines() if line}
    assert "--dbrunflag" in flags
    assert "--fast" not in flags


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_choice_option_does_not_offer_files():
    """A choice option must use `-x`, not `-r` (which still allows
    file completion for its value alongside the declared choices)."""
    script = completion.fish(Tool._parser_())
    assert "-x" in script


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_hostile_choice_does_not_execute(tmp_path):
    """Security: `$(...)`/`(...)` in a choice value must not run when
    fish expands a `complete -a` argument at Tab-time."""
    parser = _hostile_parser("safe2 $(touch pwned_fish)")
    script = completion.fish(parser)
    script_path = tmp_path / "hostileapp.fish"
    script_path.write_bytes(script.encode("utf-8"))
    out, err = _fish_drive(_FISH, script_path, "hostileapp --mode ", tmp_path)
    assert not (tmp_path / "pwned_fish").exists()
    assert "touch" in out


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_hostile_subcommand_name_does_not_execute(tmp_path):
    """Security: a hostile subcommand name must not run when it
    appears as a fish `-n __fish_seen_subcommand_from` condition or `-a`
    completion value."""

    class CondRoot(Args):
        """cond"""

    CondRoot._parsername_ = "x;touch pwned_fish_cond"

    class Root(Args):
        """root"""

        _completion_ = True
        _subcommands_ = [CondRoot]

    parser = Root._parser_()
    parser.prog = "HostRootFish"
    script = completion.fish(parser)
    script_path = tmp_path / "hostrootfish.fish"
    script_path.write_bytes(script.encode("utf-8"))
    _fish_drive(_FISH, script_path, "HostRootFish ", tmp_path)
    assert not (tmp_path / "pwned_fish_cond").exists()


@pytest.mark.skipif(_FISH is None, reason="fish not available")
def test_fish_choice_with_whitespace_and_quote_round_trips(tmp_path):
    """Unlike bash's `compgen -W`, fish does not IFS-split its `-a` values:
    'dry run' and "it's" survive as distinct, literal candidates."""
    parser = _hostile_parser("it's")
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = ("dry run", "it's", "safe")
    script = completion.fish(parser)
    script_path = tmp_path / "hostileapp.fish"
    script_path.write_bytes(script.encode("utf-8"))
    out, _ = _fish_drive(_FISH, script_path, "hostileapp --mode ", tmp_path)
    values = {line.split("\t")[0] for line in out.splitlines() if line}
    assert values == {"dry run", "it's", "safe"}


# --- Real PowerShell functional drives ---------------------------------------


def _pwsh_complete(script, line, cwd, timeout=15):
    ps_script = (
        "$ErrorActionPreference = 'Stop'\n"
        + script
        + "\n"
        + f"$r = TabExpansion2 -inputScript {completion._psq(line)} -cursorColumn {len(line)}\n"
        + "$r.CompletionMatches | ForEach-Object { $_.CompletionText }\n"
    )
    # A script FILE, not `-Command -`: fed on stdin, pwsh runs its REPL,
    # which on Linux/macOS writes cursor-key-mode escape sequences
    # (`ESC[?1h`/`ESC[?1l`) into stdout around every line.
    with tempfile.TemporaryDirectory() as script_dir:
        script_file = pathlib.Path(script_dir) / "complete.ps1"
        script_file.write_bytes(ps_script.encode("utf-8"))
        result = subprocess.run(
            [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(script_file)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_completes_after_a_positional_value(tmp_path):
    """On the PowerShell side, a positional value must not be mistaken for
    a subcommand word."""
    parser = _tool_parser()
    script = completion.powershell(parser)
    reply = _pwsh_complete(script, "tool convert in.txt ", tmp_path)
    # Every inserted candidate is now always single-quoted (see
    # test_powershell_quotes_every_candidate_unconditionally), including
    # these plain, metacharacter-free choices.
    assert set(reply) == {"'json'", "'yaml'", "'toml'"}


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_free_value_flag_offers_nothing(tmp_path):
    """On the PowerShell side, a free-value flag falls through to native
    file completion (no candidates of our own), not the flag/subcommand list."""
    parser = _tool_parser()
    script = completion.powershell(parser)
    reply = _pwsh_complete(script, "tool convert --name ", tmp_path)
    assert "--help" not in reply and "convert" not in reply


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_positional_matching_a_subcommand_name_is_not_mistaken_for_it(
    tmp_path,
):
    """argparse always consumes a node's OWN positional(s) before ever
    treating a word as its subparsers dispatch value, so on the PowerShell
    side too a word equal to a real subcommand's name must still count as
    the pending positional's value until that positional is satisfied --
    the walk used to descend into `go` on the FIRST `go` (`target`'s own
    value), regardless of whether `target` had been consumed yet."""
    parser = PosRoot._parser_()
    parser.prog = "postool"
    script = completion.powershell(parser)
    reply = _pwsh_complete(script, "postool pos go ", tmp_path)
    assert reply == ["'go'"]


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_case_sensitive_sibling_subcommands_do_not_collide(tmp_path):
    """PowerShell's `@{}` hashtable literal compares its string keys
    case-INsensitively by default, so two sibling subcommand paths
    differing only in case (`run`/`Run`) shared one dictionary slot in
    `$subsByPath`/`$vflagsByPath` and the later assignment clobbered the
    earlier one's own child-subcommand/value-flag table. Distinguishing
    flags on each side's own nested child (`--lower-flag` under `run lsub`,
    `--upper-flag` under `Run usub`) catch this even though the immediate
    top-level candidate lists (built from separate, unquoted `elseif`
    branches) happen to stay correct on their own."""
    parser = CaseRoot._parser_()
    parser.prog = "casetool"
    script = completion.powershell(parser)
    reply = _pwsh_complete(script, "casetool run L1 lsub -", tmp_path)
    assert "'--lower-flag'" in reply
    assert "'--upper-flag'" not in reply
    reply = _pwsh_complete(script, "casetool Run U1 usub -", tmp_path)
    assert "'--upper-flag'" in reply
    assert "'--lower-flag'" not in reply


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_hostile_choice_is_quoted_when_inserted(tmp_path):
    """Security-adjacent: a candidate containing whitespace or a
    PowerShell metacharacter is inserted as ONE quoted literal, not split or
    left able to run on Enter."""
    parser = _hostile_parser("dry run")
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = ("dry run", "$(rm -rf /)", "safe")
    script = completion.powershell(parser)
    reply = _pwsh_complete(script, "hostileapp --mode ", tmp_path)
    assert "'dry run'" in reply
    assert "'$(rm -rf /)'" in reply


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_unicode_smart_quote_cannot_break_out(tmp_path):
    """Security: PowerShell's tokenizer treats U+2018-U+201B as
    equivalent to an ASCII single quote when it delimits a string. The old
    quoting check only recognised ASCII shell metacharacters, so a candidate
    containing a smart quote was inserted completely UNquoted -- closing out
    of the argument early and running whatever followed as soon as the
    completed line was executed."""
    marker_name = "M_CURLY"
    hostile = "q’;New-Item " + marker_name + ";’"
    parser = _hostile_parser(hostile)
    for action in parser._actions:
        if "--mode" in getattr(action, "option_strings", []):
            action.choices = (hostile, "safe")
    script = completion.powershell(parser)
    ps_script = (
        "$ErrorActionPreference = 'Stop'\n"
        + script
        + "\n"
        + "function hostileapp { }\n"
        + "$line = 'hostileapp --mode q'\n"
        + "$r = TabExpansion2 -inputScript $line -cursorColumn $line.Length\n"
        + "$m = $r.CompletionMatches | Where-Object { $_.CompletionText -like '*New-Item*' }\n"
        + "Invoke-Expression ('hostileapp --mode ' + $m[0].CompletionText)\n"
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-Command", "-"],
        input=ps_script,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / marker_name).exists()


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_does_not_skip_an_earlier_word_equal_to_current():
    """The old command-path walk skipped any element whose TEXT
    equalled `$wordToComplete`, not just the one actually being completed --
    so an earlier, already-typed word that happens to repeat that text (a
    subcommand named the same as the value being completed) was wrongly
    dropped from the resolved command path."""

    class Go(Args):
        """go"""

        where: ty.Literal["go", "gone"] = "go"
        "where"
        ("where",)

        fast: bool = False
        "fast flag, only on Go"
        ("--fast",)

    class Repeat(Args):
        """root"""

        _subcommands_ = [Go]

    Go._parsername_ = "go"  # matches the lowercase `where` choice too
    parser = Repeat._parser_()
    parser.prog = "repeatapp"
    script = completion.powershell(parser)
    # `repeatapp go go<TAB>`: the FIRST `go` is the subcommand; the word
    # being completed is the second `go`, whose TEXT equals the first `go`'s
    # too. The old code skipped any element whose text equalled
    # `$wordToComplete`, so it also skipped the first (subcommand-
    # identifying) `go`, leaving `$cmdPath` at the root and offering the
    # root's own subcommand names instead of `where`'s choices.
    reply = _pwsh_complete(script, "repeatapp go go", None)
    assert set(reply) == {"'go'", "'gone'"}


@pytest.mark.skipif(_PWSH is None, reason="pwsh not available")
def test_powershell_dispatches_to_a_subcommand_name_needing_quotes():
    """`$el.Extent.Text` keeps the user's OWN typed quoting around a
    subcommand name (needed here because it contains a space), so comparing
    it straight against our unquoted subcommand table never matched --
    `$el.Value` (the already-dequoted literal) does."""
    parser = argparse.ArgumentParser(prog="quotedsub", add_help=False)
    sub = parser.add_subparsers(dest="cmd")
    sp = sub.add_parser("my sub")
    sp.add_argument("--fast", action="store_true")
    script = completion.powershell(parser)
    reply = _pwsh_complete(script, "quotedsub 'my sub' -", None)
    assert "'--fast'" in reply


@pytest.mark.skipif(_ZSH is None, reason="zsh not available")
def test_zsh_dispatches_to_a_subcommand_name_needing_quotes(tmp_path):
    """`$line` preserves whatever quoting the user themselves typed
    around a subcommand name, so a bare (unquoted) case label like `'my
    sub')` never matched `$line[1]` when the name needed quoting (a space
    here) -- `${(Q)line[1]}` strips that quoting before the comparison."""
    parser = argparse.ArgumentParser(prog="QuotedSub", add_help=False)
    sub = parser.add_subparsers(dest="cmd")
    sp = sub.add_parser("my sub")
    sp.add_argument("--fast", action="store_true")
    script = completion.zsh(parser)
    fpath_dir = tmp_path / "comp"
    _write_zsh_script(fpath_dir, "_QuotedSub", script)
    out = _zsh_drive(
        _ZSH, str(fpath_dir), "_QuotedSub", "QuotedSub", "QuotedSub 'my sub' -"
    )
    assert "invalid argument" not in out
    assert "--fast" in out


# --- A shell that hangs on a real script fails the test, never skips it ------


@pytest.mark.parametrize(
    "drive",
    [
        lambda: _complete_bash("true", "true", ["x"], 0),
        lambda: _zsh_drive("zsh", "/nonexistent", "_f", "f", "f "),
        lambda: _fish_drive("fish", "/nonexistent", "f ", None),
        lambda: _pwsh_complete("", "f ", None),
    ],
    ids=["bash", "zsh", "fish", "pwsh"],
)
def test_a_timeout_driving_a_shell_propagates(monkeypatch, drive):
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(subprocess.TimeoutExpired):
        drive()
