# Running your app

## duho.main

`duho.main(cls, argv=None, *, setup_logging=True, config=None)` is the one-call
entry point. It builds the parser, parses `argv` (defaulting to `sys.argv`), sets
up logging if the class mixes in [`LoggingArgs`](logging.md), and calls the parsed
instance — an `Args` instance is directly callable, so `instance()` runs the
command via its `__call__()`.

```python
import duho
from duho import Args

class Greet(Args):
    """Print a greeting."""

    name: str = "world"
    "Who to greet"
    ("--name",)

    def __call__(self) -> int | None:
        print(f"Hello, {self.name}!")
        # returning None counts as success (exit code 0)

if __name__ == "__main__":
    raise SystemExit(duho.main(Greet))
```

`__call__` returns the process exit code; `None` means 0. `SystemExit` raised by
argparse (bad arguments, `--help`, `--version`) propagates normally. If the
selected class has no `__call__`, `main` raises `NotImplementedError` naming it.

### Exit codes as an `IntEnum`

Because the return value is mapped with `0 if result is None else result`, any
`int` works as an exit code — including an `enum.IntEnum` member, which *is* an
`int`. This is a clean, self-documenting way to name your exit codes without a
dedicated feature:

```python
import enum
import duho

class Exit(enum.IntEnum):
    OK = 0
    CONFIG_ERROR = 78   # name your codes; each member is a real int

class Deploy(duho.Cmd):
    def __call__(self) -> Exit:
        if not self.config_ok():
            return Exit.CONFIG_ERROR   # propagates as process exit code 78
        return Exit.OK

raise SystemExit(duho.main(Deploy))
```

### Async commands

`__call__` may be `async def`. When it returns a coroutine, `duho.main` (and
`duho.run_command`) drive it to completion with `asyncio.run` at the call site,
so the awaited value becomes the exit code:

```python
class Fetch(Cmd):
    """Fetch a URL."""

    async def __call__(self) -> int:
        await do_async_work()
        return 0
```

`asyncio` is imported lazily, only when a command actually returns a coroutine —
a synchronous app never pays its import cost. A command dispatched once per
target via [`duho.fanout`](https://github.com/jose-pr/duho/#target-fan-out-duhofanout-opt-in) gets its own `asyncio.run` per call.
**Module-command lifecycle hooks (`init`/`main`/`success`/`finally_`) stay
synchronous** — async support is class-command `__call__` only.

## Building the parser yourself

If you'd rather drive argparse directly:

```python
parser = duho.parser(Greet)      # a real argparse.ArgumentParser
args = parser.parse_args()       # -> a Greet instance
```

`duho.parser(cls)` is the module-level form of `cls._parser_()`. Because it hands
back a genuine `ArgumentParser`, you can add arguments to it, wrap it, or embed it
however you like.

## Parsing in one call

`duho.parse(spec, argv=None)` builds and parses together:

```python
args = duho.parse(Greet)                    # from sys.argv
args = duho.parse(Greet, ["--name", "Bo"])  # from an explicit list
```

### Layering over an existing instance

Pass an **instance** instead of a class and its current field values become the
defaults. CLI arguments still win, the original is never mutated, and you get back
a new instance of the same type:

```python
base = Greet(name="staging")

result = duho.parse(base, [])                  # -> name == "staging"
result = duho.parse(base, ["--name", "prod"])  # -> name == "prod"

assert base.name == "staging"                  # untouched
```

Precedence is **CLI > instance values > class defaults**. A required field with no
class default becomes effectively optional for that call if the instance already
supplies a value. See [Configuration layers](config.md) for env vars and config
files, which slot into the same ladder.

## Subcommands

Set `_subcommands_` to a sequence of `Args` subclasses. duho wires up
`add_subparsers()` for you and dispatches to the selected one's `__call__`:

```python
import duho
from duho import Args

class Serve(Args):
    """Start the development server."""
    port: int = 8000
    ("--port",)

    def __call__(self):
        print(f"serving on {self.port}")

class Build(Args):
    """Build the project."""
    output: str = "dist"
    ("--output",)

    def __call__(self):
        print(f"building to {self.output}")

class App(Args):
    """Example multi-command app."""
    _subcommands_ = [Serve, Build]

if __name__ == "__main__":
    raise SystemExit(duho.main(App))
```

```bash
$ python app.py Serve --port 3000
serving on 3000
```

A subcommand can declare its own `_subcommands_`, composing into multi-level
trees; `main` always dispatches to the **deepest** selected class. Options
declared on a parent (say `-v` from `LoggingArgs`) remain available.

To name a command something other than its class name, pass `name=`:

```python
Serve._parser_(subparsers, name="serve")
```

### Mode flags instead of positional commands

Some established command-line interfaces select a mode with a flag rather than
a positional command, such as tar's `-c` and `-x`. Keep that application-specific
vocabulary at the entry-point boundary by normalizing `argv` before passing it to
`duho.main`; the command classes and generated subcommand help remain unchanged:

```python
import sys

MODES = {"-c": "create", "-x": "extract"}

def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in MODES:
        args[0] = MODES[args[0]]
    return duho.main(App, args)
```

This can coexist with ordinary forms such as `app create`: only an exact leading
mode flag is translated, so flags belonging to the selected command are left
alone. Use `_parseraliases_` instead when the alternate spelling is itself a
positional command (for example, `create` and `c`).

## Discovering commands from files

Instead of declaring `_subcommands_` by hand, point `duho.app` at a directory
(or dotted package) of loose command files with `source=`, or call
`duho.discover_commands(source)` directly to get the resolved list:

```python
import duho

if __name__ == "__main__":
    raise SystemExit(duho.app(source="cmds", name="myapp"))
```

Each `.py` file under `cmds/` contributes a **module command** (a plain
`main`/`register` pair, plus optional `init`/`success`/`finally_` hooks) and/or
any `Cmd` subclass it defines (a **class command**). Discovery is resilient:
a file that fails to import (missing optional
dependency) is logged and skipped, so one broken command never blocks the
rest — see `examples/discovery_app.py` and `examples/discovery_cmds/` for a
complete, runnable version of this pattern (a module command with
`register`/`main`, one with a full `init`/`success`/`finally_` lifecycle, and
a class command, all in one loose directory with no `__init__.py`).

A directory with **no** `__init__.py` whose files are instead named
`NN-name.py` (numbered steps) is a different, opt-in shape entirely — see
[RunPath: ordered step directories](runpath.md).

## Version flag

Set `_version_` and duho adds a `--version` flag that prints
`"<prog> <version>"` and exits:

```python
class App(Args):
    _version_ = "1.2.3"
```

To read the version from installed package metadata instead of hardcoding it, use
the `duho.AUTO` sentinel:

```python
import duho

class App(duho.Args):
    _version_ = duho.AUTO
    _distribution_ = "my-package"   # only if it differs from the import name
```

`AUTO` resolves via `importlib.metadata.version()`, defaulting the distribution
name to the class's top-level import package. If the distribution isn't installed
(a source checkout, say), duho adds **no** `--version` flag at all and logs a
debug message — rather than printing a bogus version or crashing.

## Prettier help: defaults & color

Opt into a richer `--help` by setting a class-level `_help_formatter_`. duho ships
three `argparse.HelpFormatter` subclasses (all **off by default** — plain help is
unchanged unless you set the attribute):

- **`duho.DefaultsFormatter`** — appends `(default: X)` to each option's help,
  skipping `None`/`""`/`False` defaults (an unset optional, a `store_true` flag)
  that argparse's own `ArgumentDefaultsHelpFormatter` would render as noise.
- **`duho.ColorHelpFormatter`** — ANSI-colors section headings and option flags.
  Color is **gated**: it is emitted only to a TTY, `NO_COLOR` disables it, and
  `FORCE_COLOR` forces it on. When color is off the output is byte-identical to
  the plain formatter, so piped/redirected help stays clean and aligned.
- **`duho.ColorDefaultsFormatter`** — both, composed.

```python
import duho

class App(duho.Cli):
    _help_formatter_ = duho.ColorDefaultsFormatter

    region: str = "us-east"
    "Target region"
    ("--region",)
```

A root's `_help_formatter_` propagates to its `_subcommands_` tree, so a single
setting styles the whole app; a subcommand that sets its own `_help_formatter_`
keeps it. You may also point `_help_formatter_` at any custom `HelpFormatter`
subclass of your own.

!!! note
    argparse renders a default only for options that already have help text, so
    give a field a docstring to see its `(default: …)` suffix.
