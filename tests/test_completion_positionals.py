"""Completion coverage for positional args + functional/injection round-trips.

Plan 03 T3. The positional branches of every emitter (bash/zsh/fish/powershell)
were at 0% coverage -- no fixture declared positionals. This adds a parser with
required/optional/variadic positionals (one choice-bearing, one Path) and asserts
each shell emits its positional branch. It also drives the generated bash script
functionally (subcommand names, `--fl<TAB>` flags, choice values, and the
after-a-flag-value case that M8 fixed) and checks an injected hostile choice
value round-trips as one literal candidate.

All shell-execution tests skipif the shell binary is absent.
"""

import pathlib
import shutil
import subprocess
import typing as ty

import pytest

import duho.completion as completion
from duho import Args

_BASH = shutil.which("bash")
_ZSH = shutil.which("zsh")
_FISH = shutil.which("fish")


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


class Tool(Args):
    """A tool with a subcommand tree and positionals."""

    verbose: int = 0
    "Verbosity"
    ("-v", "--verbose")

    _subcommands_ = [Convert]


# --- Positional branches emit for every shell -------------------------------


def test_walk_captures_positionals():
    spec = completion._walk(Tool._parser_())
    convert = spec.subcommands["Convert"]
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


def test_zsh_emits_positional_specs():
    script = completion.zsh(Tool._parser_())
    # zsh renders positionals as `name:name:(...)` / `name:name:_files` specs.
    assert "source:source:_files" in script
    assert "fmt:fmt:(json yaml toml)" in script


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


def _complete_bash(script, func, words, cword):
    """Source `script`, run the completion function, print COMPREPLY lines."""
    words_literal = " ".join(_bash_arr(w) for w in words)
    harness = (
        script
        + f"\nCOMP_WORDS=({words_literal})\nCOMP_CWORD={cword}\n"
        + f"{func}\nprintf '%s\\n' \"${{COMPREPLY[@]}}\"\n"
    )
    result = subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def _bash_arr(word):
    import shlex

    return shlex.quote(word)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_root_subcommand_names():
    script = completion.bash(Tool._parser_())
    reply = _complete_bash(script, "_Tool", ["Tool", ""], 1)
    assert "Convert" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_flags():
    script = completion.bash(Tool._parser_())
    # `Tool Convert --e<TAB>` -> the --env flag.
    reply = _complete_bash(script, "_Tool", ["Tool", "Convert", "--e"], 2)
    assert "--env" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_completes_choice_values_after_flag():
    script = completion.bash(Tool._parser_())
    # `Tool Convert --env <TAB>` -> the choice values for --env.
    reply = _complete_bash(script, "_Tool", ["Tool", "Convert", "--env", ""], 3)
    assert "prod" in reply and "dev" in reply


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_bash_after_flag_value_does_not_break_subcommand(tmp_path):
    """M8: a value-taking flag before the cursor must not corrupt the cmd path.

    `Tool --verbose 2 <TAB>` at the root must still offer the subcommand names
    (the `--verbose`'s value `2` is skipped, not treated as a cmd-path word).
    `--verbose` takes a value here (it's an int flag), so this exercises the
    skip-the-value-after-a-value-flag branch.
    """
    script = completion.bash(Tool._parser_())
    reply = _complete_bash(script, "_Tool", ["Tool", "--verbose", "2", ""], 3)
    assert "Convert" in reply


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
    """The injected `$(...)` must NOT run when the completion is driven (01-D3)."""
    marker = tmp_path / "pwned"
    parser = _hostile_parser(f"it's $(touch {marker})")
    script = completion.bash(parser)
    reply = _complete_bash(script, "_hostileapp", ["hostileapp", "--mode", ""], 2)
    assert not marker.exists()  # the substitution never ran
    # Some fragment of the literal survives (the `touch` token is offered as a
    # candidate rather than being executed).
    assert any("touch" in c for c in reply)


@pytest.mark.skipif(_BASH is None, reason="bash not available")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "A choice value containing whitespace/quotes cannot round-trip as ONE "
        "candidate through bash's `compgen -W`: the word list is IFS-split, so "
        "`it's $(uh oh)` comes back as the separate tokens `its`, `\\$(uh`, `oh)`. "
        "01-D3/M2 hardened the emitter against EXECUTION (verified separately), "
        "but static `compgen -W` word-splitting is inherent -- the intact "
        "single-candidate round-trip is NOT delivered for metacharacter values."
    ),
)
def test_hostile_choice_bash_round_trips_as_one_candidate(tmp_path):
    parser = _hostile_parser("it's $(uh oh)")
    script = completion.bash(parser)
    reply = _complete_bash(script, "_hostileapp", ["hostileapp", "--mode", ""], 2)
    assert "it's $(uh oh)" in reply
