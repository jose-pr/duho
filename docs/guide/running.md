# Running your app

## duho.main

`duho.main(cls, argv=None, *, setup_logging=True, config=None)` is the one-call
entry point. It builds the parser, parses `argv` (defaulting to `sys.argv`), sets
up logging if the class mixes in [`LoggingArgs`](logging.md), and calls the parsed
instance's `__run__()`.

```python
import duho
from duho import Args

class Greet(Args):
    """Print a greeting."""

    name: str = "world"
    "Who to greet"
    ("--name",)

    def __run__(self) -> int | None:
        print(f"Hello, {self.name}!")
        # returning None counts as success (exit code 0)

if __name__ == "__main__":
    raise SystemExit(duho.main(Greet))
```

`__run__` returns the process exit code; `None` means 0. `SystemExit` raised by
argparse (bad arguments, `--help`, `--version`) propagates normally. If the
selected class has no `__run__`, `main` raises `NotImplementedError` naming it.

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
`add_subparsers()` for you and dispatches to the selected one's `__run__`:

```python
import duho
from duho import Args

class Serve(Args):
    """Start the development server."""
    port: int = 8000
    ("--port",)

    def __run__(self):
        print(f"serving on {self.port}")

class Build(Args):
    """Build the project."""
    output: str = "dist"
    ("--output",)

    def __run__(self):
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
