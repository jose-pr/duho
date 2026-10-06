# Plugins via entry points

Beyond discovering commands from a local package or directory
(`duho.app(root, source=...)`), duho can load commands advertised by
**separately-installed distributions** through their
[entry points](https://packaging.python.org/en/latest/specifications/entry-points/).
This lets a third-party package extend your app with new subcommands **without your
app importing it directly** — the classic plugin pattern.

## Using an entry-point group

Point `duho.app` at an entry-point **group** name:

```python
import duho

class CLI(duho.LoggingArgs, duho.Cli):
    "myapp"

raise SystemExit(duho.app(CLI, entry_points="myapp.commands"))
```

Every entry point advertised in the `myapp.commands` group by any installed
distribution is loaded and registered as a subcommand.

## Advertising commands from a plugin package

A plugin package declares its commands in its packaging metadata. With
`pyproject.toml`:

```toml
[project.entry-points."myapp.commands"]
hello = "myapp_hello.plugin:HelloCmd"   # a Cmd subclass -> class command
bye   = "myapp_hello.bye"               # a module with main() -> module command
```

An entry point may resolve to either command shape, coerced through the same path
as every other source:

- a **`Cmd` subclass** → a class command (its `_parsername_`, or the
  kebab-case of its class name when it declares none, is the subcommand
  name);
- a **command module** (a module whose top-level `main`/`run`/`call` is the
  entrypoint) → a module command (the entry-point name is used as the subcommand
  name when the module declares no `_parsername_`).

## Resilience and cost

Loading is **resilient**, in the same spirit as `discover_commands`: an entry
point that fails to import (a broken or renamed target, a missing optional
dependency) or that does not resolve to a command is logged at `WARNING` and
skipped, so one bad plugin never takes the whole app down — the rest still load.

`importlib.metadata` is imported **lazily**, only when entry-point discovery
actually runs, so an app that does not use `entry_points=` never pays its import
cost.

## Precedence

`duho.app` picks its **base** command set from the first of these that's given:

```
commands=  >  source=  >  entry_points=  >  root._subcommands_
```

A `CMDS_PATH` entry from `env=` is not a lower rung on that ladder — it **always**
layers on top of whichever base was chosen (even when `commands=`/`source=`/
`entry_points=` was also given), and a discovered command whose name collides
with a base command **wins** the collision (logged, never silent). `CMDS_PATH`
is additive: it never removes a base command, it only adds to — and can
override — it.

## Several sources and your own error policy

`source=` (and `duho.discover_commands`) also takes a list or tuple. Each source is
discovered on its own, a command of the same name in a later source replaces the
earlier one, and the merged result is sorted by name. An empty sequence gives `[]`,
and each member keeps the empty-source and bare-drive-letter checks (`ValueError`).

By default discovery skips a command that raises `ImportError` or
`NotImplementedError` (with a warning) and lets anything else, such as a
`SyntaxError`, propagate. `on_error(source, exc)` replaces that rule. It is called for
any exception, `SystemExit` included, raised importing one file (`source` is the file
`Path`), importing one package module (the dotted name), building a command, or
building a provider command (the directory). Returning skips that one; raising aborts
discovery:

<!-- runnable -->
```python
import tempfile
from pathlib import Path

import duho

root = Path(tempfile.mkdtemp())
for name, body in {
    "base/hello.py": "def main(args):\n    return 1\n",
    "base/broken.py": "def main(:\n",
    "site/hello.py": "def main(args):\n    return 2\n",
}.items():
    (root / name).parent.mkdir(exist_ok=True)
    (root / name).write_text(body)

skipped = []
commands = duho.discover_commands(
    [root / "base", root / "site"],
    on_error=lambda source, exc: skipped.append((source.name, type(exc).__name__)),
)
assert skipped == [("broken.py", "SyntaxError")]
assert [duho.command_name(c) for c in commands] == ["hello"]
assert commands[0].entrypoint(None) == 2      # site/hello.py replaced base/hello.py
```

`duho.app` passes the same `on_error` to discovery for `source=` and `CMDS_PATH`, and
also calls it, with `(command, exc)`, when building one command's parser or running its
`register` hook raises; that command is dropped.

With `providers=True` (filesystem sources only, off by default) the source directory
itself, then each child directory whose name does not start with `_` or `.`, is
offered to the providers registered with `duho.register_command_provider`. A match is
built with `builder(path, name_with_dashes)`, and a source directory a provider claims
yields that one command instead of being scanned for files.

## Adapting a module command's entrypoint

A module command's resolved entrypoint is `ModuleCommand.entrypoint` (read-only).
`duho.app(adapter=...)` and `duho.run_command(command, instance, adapter=...)` call
`adapter(entrypoint)` and run what it returns, with the parsed instance, in its place;
a falsy return keeps the entrypoint. That lets an app accept entrypoint signatures of
its own without a decorator in every file. The adapter is never applied to `init`,
`success` or `finally_`, nor to a class command.

<!-- runnable -->
```python
import tempfile
from pathlib import Path

import duho
from duho.runtime import accepts_positional

root = Path(tempfile.mkdtemp())
(root / "hello.py").write_text("def main(args):\n    return 2\n")

code = duho.app(
    source=root,
    argv=["hello"],
    adapter=lambda entrypoint: lambda args: entrypoint(args) + 40,
)
assert code == 42

assert accepts_positional(lambda a, b: 0, 2)
assert not accepts_positional(lambda a: 0, 2)
```

`accepts_positional(func, count)` is true when `func` declares at least `count`
positional parameters (defaults allowed) or `*args`; keyword-only parameters do not
count, and an unreadable signature gives `False`. Combining `adapter=` with
`dispatch=` raises `ValueError`: a custom `dispatch` passes `adapter` to
`run_command` itself.

## The `register` hook's `args`

A module command's `register(parser, args)` hook receives the root instance parsed
with its required options treated as optional, so a missing one is unset or its
default. When a global fails to convert it receives the root's defaults instance, and
`args` is `None` only if the root cannot be built without arguments. The real parse
still enforces the required options.

## Getting the list directly

Call `duho.discover_entry_points(group)` to get the resolved `list[Command]`
without building an app (read a command's name with `duho.parsers.command_name`; a
class command has `_parsername_` only if it declares one):

```python
commands = duho.discover_entry_points("myapp.commands")
```
