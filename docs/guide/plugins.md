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

- a **`Cmd` subclass** → a class command (its `_parsername_`/class name is the
  subcommand name);
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

`entry_points=` sits in `duho.app`'s command-source precedence:

```
commands=  >  source=  >  entry_points=  >  env (CMDS_PATH)  >  root._subcommands_
```

## Getting the list directly

Call `duho.discover_entry_points(group)` to get the resolved `list[Command]`
without building an app:

```python
commands = duho.discover_entry_points("myapp.commands")
```
