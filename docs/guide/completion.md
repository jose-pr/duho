# Shell completion

duho generates **static** completion scripts for bash, zsh, fish, and PowerShell.
Static means the script is a plain shell function you install once — there's no
runtime dependency and, unlike `argcomplete`, your program is not re-invoked on
every keypress.

## Adding the flag

Set `_completion_ = True` to add a `--print-completion {bash,zsh,fish,powershell}`
option:

```python
import duho

class App(duho.Args):
    """My tool."""

    _completion_ = True
    _subcommands_ = [Serve, Build]
```

```bash
# bash
app --print-completion bash > ~/.local/share/bash-completion/completions/app

# zsh (somewhere on your $fpath)
app --print-completion zsh > ~/.zfunc/_app

# fish
app --print-completion fish > ~/.config/fish/completions/app.fish
```

```powershell
# PowerShell — emit and evaluate (add to your $PROFILE to persist)
app --print-completion powershell | Out-String | Invoke-Expression
```

The PowerShell emitter registers a `Register-ArgumentCompleter -Native` script
block that resolves the current subcommand path to its flags, subcommand names,
and choice values; a command taking a free/path value offers nothing, so
PowerShell's own file completion takes over.

`_completion_` is off by default — the same opt-in precedent as `_version_` —
so the flag doesn't clutter `--help` for tools that don't want it.

## Without the flag

Generate a script without exposing the option at all:

```python
import sys
import duho

duho.print_completion(App, "bash", file=sys.stdout)
```

## What gets completed

The generator walks the built parser tree, so it knows everything duho knows:

- **Subcommands**, including nested `_subcommands_` trees.
- **Option flags** for the command *and* for whichever subcommand is being typed.
- **Choices** — a `Literal` or `enum.Enum` field offers its values as completion
  candidates.
- **Paths** — a `pathlib.Path`-typed field gets the shell's native file and
  directory completion.

## Regenerating

The script is a snapshot of your CLI's shape. Regenerate it when you add or
rename commands or options — typically as a release step, or from a `make`
target, so the shipped completions never drift from the actual interface.
