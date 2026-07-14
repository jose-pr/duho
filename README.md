# Duho

**Duho** is a declarative CLI framework for Python that turns the complexity of building command-line applications into simple, type-safe class definitions.

Named after the sacred Taíno ceremonial stool—a symbol of power and authority—duho provides the **foundation** from which you command your application.

## Features

- **Declarative**: Define CLI arguments as class annotations—no boilerplate argparse setup
- **Type-safe**: Built-in type conversion and validation from Python type hints
- **Logging**: Integrated colored logging with configurable verbosity levels
- **Subcommands**: Easily compose multi-command CLI applications
- **Extensible**: Customize argument behavior with protocols and builders

## Quick Start

```python
from duho import Args

class MyApp(Args):
    name: str
    "The name to greet"
    ("--name",)
    
    count: int = 1
    "How many times to greet"
    ("--count",)

if __name__ == "__main__":
    parser = MyApp._parser_()
    args = parser.parse_args()
    for _ in range(args.count):
        print(f"Hello, {args.name}!")
```

Run it:

```bash
python app.py --name Alice --count 3
# Output:
# Hello, Alice!
# Hello, Alice!
# Hello, Alice!
```

## Installation

```bash
pip install duho
```

### Optional Dependencies

For colored output in logging:

```bash
pip install duho[colorama]
```

## Core Concepts

### Args: Declare Your CLI

Define arguments using class annotations. The docstring becomes the help text, and expressions after the annotation become argument flags:

```python
from duho import Args
import typing as ty

class Deploy(Args):
    """Deploy the application to production."""
    
    environment: str
    "Target environment (prod, staging, dev)"
    ("--env",)
    
    version: ty.Optional[str] = None
    "Release version (defaults to latest)"
    ("--version",)
    
    dry_run: bool = False
    "Preview changes without applying them"
    ("--dry-run",)
```

Bool fields defaulting to `False` (or with no default) get a simple `--flag`
switch. Bool fields defaulting to `True` get `--flag`/`--no-flag` (via
`argparse.BooleanOptionalAction`) so the default can be explicitly turned back off.

### Supported Field Types

| Annotation | Behavior |
| --- | --- |
| `str`, `int`, `float`, `bool` | Direct conversion; `bool` gets `store_true` or `--flag`/`--no-flag` (see above) |
| `typing.Literal["a", "b"]` | `choices=("a", "b")`; mixed-type literals (`Literal["auto", 1]`) try each declared value's own type and keep whichever round-trips |
| `enum.Enum` subclass | `choices` are the member **names**; the parsed value is the Enum member (`Color["RED"] -> Color.RED`) |
| `list` / `list[T]` | Accepts both repeated (`--x a --x b`) and space-separated (`--x a b`) forms via `action="extend", nargs="*"`; bare `list` elements are `str`; default is `[]` when no explicit default is given |
| `typing.Optional[T]` / `T \| None` (3.10+) | Not required; tries `T` |
| `typing.Union[A, B]` / `A \| B` (3.10+) | Tries each type in declaration order |

### Run your app

`duho.main(cls, argv=None, *, setup_logging=True)` builds the parser, parses
`argv` (or `sys.argv` when omitted), optionally wires up stderr logging and
verbosity (for classes mixing in `LoggingArgs`), and calls `instance.__run__()`:

```python
from duho import Args, main

class Greet(Args):
    """Print a greeting."""
    name: str = "world"
    "Who to greet"
    ("--name",)

    def __run__(self) -> int | None:
        print(f"Hello, {self.name}!")
        # returning None counts as a successful exit (code 0)

if __name__ == "__main__":
    raise SystemExit(main(Greet))
```

`SystemExit` raised by argparse (bad args, `--help`, `--version`) propagates
normally. If the selected class has no `__run__`, `main` raises
`NotImplementedError` naming the class.

**Subcommands**: set `_subcommands_` to a sequence of `Args` subclasses and
`main`/`_parser_` wires up `add_subparsers(dest="command", required=True)`
automatically — no manual subparser plumbing needed. Nested `_subcommands_`
(a subcommand that itself declares `_subcommands_`) compose naturally into
multi-level command trees, and `main` always dispatches to the deepest
selected class's `__run__`.

```python
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
    raise SystemExit(main(App))
```

```bash
python app.py Serve --port 3000
python app.py Build --output dist
```

**Version flag**: set `_version_` on any `Args` subclass to add a `--version`
flag that prints `"%(prog)s <version>"` and exits 0 (skipped if a `version`-dest
action already exists, e.g. from a parent parser):

```python
class MyApp(Args):
    _version_ = "1.2.3"
```

### Build and Parse

```python
parser = Deploy._parser_()
args = parser.parse_args()

print(f"Deploying to {args.environment} (dry-run: {args.dry_run})")
```

### Quick parse

`duho.parser(cls, ...)` is the module-level entry point for building a parser
(delegates to `cls._parser_(...)`). `duho.parse(spec, argv=None, *,
parser_kwargs=None)` goes one step further and parses in a single call:

```python
import duho

# spec is a type: build + parse in one call
args = duho.parse(Deploy)
```

`spec` can also be an **instance**, letting you layer CLI overrides on top of
config-file/programmatic defaults. The instance's current field values become
the argparse defaults; CLI args still win; the original instance is left
unmutated and a new instance of the same type is returned:

```python
base = Deploy(environment="staging", dry_run=False)

# No --env on the CLI -> falls back to base.environment ("staging")
result = duho.parse(base, ["--dry-run"])

assert result.environment == "staging"   # from base
assert result.dry_run is True            # from CLI
assert base.dry_run is False             # base is untouched
```

Precedence: **CLI args > instance field values > class defaults**. This also
means a required field with no class default becomes effectively optional
for that call if the instance already supplies a value.

### Logging Integration

Combine with `LoggingArgs` for structured logging:

```python
from duho import LoggingArgs

class MyApp(LoggingArgs):
    command: str
    "The command to run"
    
    def run(self):
        self._set_loglevels_()
        logger = self._logger_
        logger.info(f"Running: {self.command}")
```

Control logging from the CLI:

```bash
python app.py mycommand -v                    # Verbose: INFO -> DEBUG
python app.py mycommand -vv                   # More verbose: -> TRACE (max)
python app.py mycommand -q                    # Quiet: INFO -> WARNING
python app.py mycommand -qq                   # Quieter: -> ERROR
python app.py mycommand --loglevel DEBUG      # Debug level
python app.py mycommand --loglevel foo:TRACE  # Module-specific level
```

`-v`/`-q` are counted flags that move away from/toward the default `INFO` level in
opposite directions and can be combined (e.g. `-vv -q` nets one step more verbose
than the default); each end of the scale (`CRITICAL`/`TRACE`) clamps rather than
wrapping or erroring.

### Subcommands

Build hierarchical CLIs with subparsers:

```python
from duho import Args

class Serve(Args):
    """Start the development server."""
    host: str = "localhost"
    ("--host",)
    port: int = 8000
    ("--port",)

class Build(Args):
    """Build the project."""
    output: str
    ("--output",)

if __name__ == "__main__":
    main_parser = argparse.ArgumentParser()
    subparsers = main_parser.add_subparsers()
    
    Serve._parser_(subparsers, name="serve")
    Build._parser_(subparsers, name="build")
    
    args = main_parser.parse_args()
```

## Documentation

Full documentation: https://jose-pr.github.io/duho/

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License. See [LICENSE](LICENSE) for details.
