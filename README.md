# Duho

[![PyPI version](https://img.shields.io/pypi/v/duho.svg)](https://pypi.org/project/duho/)
[![Python versions](https://img.shields.io/pypi/pyversions/duho.svg)](https://pypi.org/project/duho/)
[![Documentation](https://img.shields.io/badge/docs-jose--pr.github.io%2Fduho-blue.svg)](https://jose-pr.github.io/duho/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/jose-pr/duho/blob/master/LICENSE)

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

**The docstring is optional.** The flags-tuple alone declares an argument — a
field needs no docstring. When present, the docstring only sets the argument's
`help=` text; when absent, `help` defaults to `""`. Add a docstring where the help
text earns its keep, and skip it where the flag speaks for itself:

```python
class Copy(Args):
    # No docstring needed -- the flags-tuple alone declares the argument.
    source: str
    ("-s", "--source")

    force: bool = False
    "Overwrite the destination if it exists."  # help text where it's useful
    ("-f", "--force")
```

### Supported Field Types

| Annotation | Behavior |
| --- | --- |
| `str`, `int`, `float`, `bool` | Direct conversion; `bool` gets `store_true` or `--flag`/`--no-flag` (see above) |
| `typing.Literal["a", "b"]` | `choices=("a", "b")`; mixed-type literals (`Literal["auto", 1]`) try each declared value's own type and keep whichever round-trips |
| `enum.Enum` subclass | `choices` are the member **names**; the parsed value is the Enum member (`Color["RED"] -> Color.RED`) |
| `list` / `list[T]` | Accepts both repeated (`--x a --x b`) and space-separated (`--x a b`) forms via `action="extend", nargs="*"`; bare `list` elements are `str`; default is `[]` when no explicit default is given |
| `set` / `set[T]` | Same repeated + space-separated forms as `list`, but the final value is a `set` (dedups; **iteration order is not guaranteed**); bare `set` elements are `str`; default is `set()` when no explicit default is given |
| `tuple[T, ...]` / `tuple` | Variadic **homogeneous** tuple, same forms as `list`, final value a `tuple` (order preserved, no dedup); bare `tuple` elements are `str`; default is `()` when no explicit default is given. A fixed-length heterogeneous `tuple[A, B]` is **not** supported and raises a clear error at parser build — use `tuple[T, ...]` |
| `dict` / `dict[str, V]` | Each occurrence is one `KEY=VALUE` token; repeated flags merge into one dict (`--opt k=1 --opt j=2` → `{"k": ..., "j": ...}`) via `UpdateAction`; the value half is converted with `V` (bare `dict` == `dict[str, str]`); only **`str` keys** are supported (a non-`str` key type is a clear build-time error); default is `{}` when no explicit default is given |
| `typing.Optional[T]` / `T \| None` (3.10+) | Not required; tries `T` |
| `typing.Union[A, B]` / `A \| B` (3.10+) | Tries each type in declaration order |
| `Union`/`Optional` containing an `Enum` | The Enum member is matched by **name**, same as a bare `enum.Enum` field — a name match wins before falling through to a later `str` member, so declaration order matters (`Union[Color, str]` with `--c RED` yields `Color.RED`, while `--c other` yields the string `"other"`) |
| `Arg[int, duho.Count()]` | A repeatable counted flag (`-vvv` → `3`), via argparse `action="count"`. The value is the number of occurrences; pair a short flag like `("-v",)` with it. `LoggingArgs` uses this for `-v`/`-q` |

### Positional arguments

A flags-tuple whose single entry does **not** start with `-` declares a
positional instead of an option. Duho picks the `nargs` for you from the type
and default:

```python
class Move(Args):
    source: str
    ("source",)                 # required positional

    dest: str = "."
    ("dest",)                   # optional positional -> nargs="?" (uses the default when omitted)

    extra: list[str]
    ("extra",)                  # variadic positional -> nargs="*" (a list[str] positional)
```

```bash
python move.py a.txt              # source="a.txt", dest=".",   extra=[]
python move.py a.txt out/         # source="a.txt", dest="out/", extra=[]
python move.py a.txt out/ x y z   # source="a.txt", dest="out/", extra=["x", "y", "z"]
```

An optional positional (a real default present, `nargs` unset) automatically
gets `nargs="?"` — without it argparse would make the positional required and
ignore the default. A `list`/`list[T]` positional becomes variadic
(`nargs="*"`), defaulting to `[]`. `required=` is never emitted for positionals.

### Mutually exclusive options

Set `NS(conflicts="group-name")` on the fields that must not be used together.
Duho builds one `argparse` mutually-exclusive group per distinct `conflicts`
value, so only one option from the group may appear on the command line:

```python
from duho import Args, Arg, NS

class Archive(Args):
    """Create an archive."""

    gzip: Arg[bool, NS(conflicts="compression")] = False
    "Compress with gzip."
    ("--gzip",)

    zstd: Arg[bool, NS(conflicts="compression")] = False
    "Compress with zstd."
    ("--zstd",)

    none: Arg[bool, NS(conflicts="compression")] = False
    "Store uncompressed."
    ("--none",)
```

```bash
python archive.py --gzip            # ok
python archive.py --gzip --zstd     # error: not allowed with argument --gzip
```

Fields sharing the same `conflicts` string join the same group; use different
strings for independent exclusive sets. (The `examples/fileinstall.py` `--type`
field uses `NS(conflicts="type")` this way.)

Add `conflicts_required=True` on **any** member to make the whole group
required — the user must supply exactly one of its options:

```python
    push: Arg[bool, NS(conflicts="mode", conflicts_required=True)] = False
    ("--push",)

    pull: Arg[bool, NS(conflicts="mode")] = False
    ("--pull",)
```

```bash
python app.py            # error: one of --push, --pull is required
python app.py --push     # ok
```

### Titled argument groups

Set `NS(group="Section title")` to bucket fields under a named section in
`--help`. Fields sharing a title join the same section; the rest stay under the
default `options:`:

```python
class App(Args):
    outfile: Arg[str, NS(group="Output options")] = "-"
    "Where to write."
    ("--outfile",)

    verbose: Arg[bool, NS(group="Output options")] = False
    "Verbose output."
    ("--verbose",)
```

A field may combine `group=` and `conflicts=`: the mutually-exclusive group is
nested inside the titled section (still exclusive, and shown under the title).

### Run your app

`duho.main(cls, argv=None, *, setup_logging=True)` builds the parser, parses
`argv` (or `sys.argv` when omitted), optionally wires up stderr logging and
verbosity (for classes mixing in `LoggingArgs`), and runs the command. The class
must be a `duho.Cmd` (see [Commands: Args vs Cmd](#commands-args-vs-cmd) below) —
`main` dispatches the parsed instance via `__call__`:

> **`main` vs `app` — which entry point?** Use **`duho.main(cls)`** for a command
> (or a tree declared statically with `_subcommands_`) — it's the simple, direct
> runner. Reach for **`duho.app(root, ...)`** when you need what `main` doesn't do:
> **discovering** commands from a package/directory (`source=`), threading app-wide
> **config/env** down to subcommands, or **overriding dispatch** (`dispatch=`, e.g. to
> fan a command out over targets). `app` is the multi-command driver; `main` is the
> one-shot runner. Both dispatch a `Cmd` root via `__call__`.

```python
from duho import Cmd, main

class Greet(Cmd):
    """Print a greeting."""
    name: str = "world"
    "Who to greet"
    ("--name",)

    def __call__(self) -> int | None:
        print(f"Hello, {self.name}!")
        # returning None counts as a successful exit (code 0)

if __name__ == "__main__":
    raise SystemExit(main(Greet))
```

`SystemExit` raised by argparse (bad args, `--help`, `--version`) propagates
normally. Dispatching a bare data `Args` (not a `Cmd`) raises a clear
`NotImplementedError` naming the class.

`__call__` may be `async def` — when it returns a coroutine, `main` (and
`run_command`) run it to completion via `asyncio.run` at the call site (imported
lazily, so a synchronous app never pays for it), and the awaited value becomes
the exit code. Module-command lifecycle hooks stay synchronous.

**Subcommands**: set `_subcommands_` to a sequence of `Cmd` subclasses and
`main`/`_parser_` wires up `add_subparsers(dest="command", required=True)`
automatically — no manual subparser plumbing needed. Nested `_subcommands_`
(a subcommand that itself declares `_subcommands_`) compose naturally into
multi-level command trees, and `main` always dispatches to the deepest
selected command via `__call__`.

```python
class Serve(Cmd):
    """Start the development server."""
    port: int = 8000
    ("--port",)
    def __call__(self):
        print(f"serving on {self.port}")

class Build(Cmd):
    """Build the project."""
    output: str = "dist"
    ("--output",)
    def __call__(self):
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

**Subcommand aliases**: set `_parseraliases_` on a `Cmd` subclass to register
short or alternate names for it within a `_subcommands_` tree. An alias dispatches
to the same `__call__` as the full name:

```python
class Create(Cmd):
    """Create a new resource."""
    _parseraliases_ = ["c", "new"]
    name: str
    ("name",)
    def __call__(self):
        print(f"creating {self.name}")

class App(Args):
    _subcommands_ = [Create]
```

```bash
python app.py create web   # full name
python app.py c web        # alias -> same command
python app.py new web      # alias -> same command
```

Absence of `_parseraliases_` is the default (no aliases). Aliases apply only to
nested subcommands (argparse's `add_parser` accepts `aliases`; a top-level parser
does not).

**Version flag**: set `_version_` on any `Args` subclass to add a `--version`
flag that prints `"%(prog)s <version>"` and exits 0 (skipped if a `version`-dest
action already exists, e.g. from a parent parser):

```python
class MyApp(Args):
    _version_ = "1.2.3"
```

**Autodetected version**: set `_version_ = duho.AUTO` to resolve the version
from installed package metadata via `importlib.metadata.version(...)` instead
of hardcoding a string. By default the distribution name is the class's
top-level import package (`cls.__module__.split(".")[0]`); set `_distribution_`
to override it when the import name differs from the distribution name on
PyPI:

```python
import duho

class MyApp(duho.Args):
    _version_ = duho.AUTO
    _distribution_ = "my-package"  # only needed if it differs from the import name
```

If the distribution can't be found (e.g. running from a source checkout that
isn't installed), duho does **not** add a `--version` flag at all — it logs a
debug message via `logging.getLogger("duho")` instead of printing a bogus
`0.0.0+unknown`-style version or raising.

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

### Parsing only the globals (config before commands)

Sometimes you need to read a root/global option *before* you can build the full
subcommand parser — for example, a `--config` path (or an env-derived setting)
that decides which command modules to discover and load. `duho.parse_globals`
parses only the root command's global args and ignores the subcommand tree:

```python
import duho

# Root is a Cli/Cmd with global flags and a subcommand tree.
globals_only = duho.parse_globals(Root, ["--config", "prod.toml", "deploy", "..."])
assert globals_only.config == "prod.toml"   # resolved without validating "deploy"
```

A missing subcommand does not error, and an unknown trailing token (a not-yet-
loaded subcommand name and its args) does not crash the parse — it is simply
ignored in this pass. `parse_globals` returns the parsed root instance (globals
only); it is the public form of the prepass `duho.app` runs internally. Pass any
`cls._parser_` keyword through it (e.g. `add_help=False`). If you also want the
leftover argv, call `parser.parse_known_args` directly instead.

### Configuration layers

Beyond instance overrides, `duho.parse`/`duho.main` support two more default
layers: per-field environment variables and a TOML config file. Combined
precedence ladder, highest wins:

```
CLI args > env var > config file > class default
```

A value supplied by *any* layer also un-requires that field — a field with
no class default that's set in the config file (say) no longer needs to be
passed on the CLI.

**Environment variables**: annotate a field with `NS(env="VAR_NAME")`:

```python
from duho import Args, Arg, NS

class Deploy(Args):
    token: Arg[str, NS(env="DEPLOY_TOKEN")] = ""
    "Auth token"
    ("--token",)
```

**Config file**: set `_config_` on the class, or pass `config=` to
`duho.parse`/`duho.main` (the kwarg overrides the class attr):

```python
class Deploy(Args):
    _config_ = "~/.config/myapp/config.toml"
    ...

result = duho.parse(Deploy, config="./deploy.toml")
result = duho.main(Deploy, config="./deploy.toml")
```

Top-level TOML keys map to the root command's fields; a table named after a
subcommand's `_parsername_` maps to that subcommand's fields:

```toml
# deploy.toml
verbose = true

[install]
target = "prod"
```

Reading TOML uses the stdlib `tomllib` on Python 3.11+; on 3.9/3.10 it falls
back to the third-party `tomli` package if installed (`pip install
duho[config]`) — duho stays zero-runtime-dependency by default, so this
extra is only needed if you actually use `_config_`/`config=` on an older
interpreter.

**Env/config value conversion.** Layered values are converted to match what CLI
parsing of the same field yields. A `bool` field reads `1/true/yes/on/y/t` as
`True` and `0/false/no/off/n/f`/empty as `False` (an unknown string is an error).
A **collection** field (`list`/`set`/`tuple`) treats an env var or a TOML
*string* as a **single element** (`FILES=a.txt` → `["a.txt"]`, matching one CLI
occurrence), while a TOML **array** converts element-wise. Non-string TOML scalars
are coerced to the field type (`timeout = 30` for a `float` field → `30.0`).

**Debugging where a value came from**: `duho.value_sources(parsed)` returns
`{field_name: "cli" | "env" | "config" | "default"}` for the instance
returned by `duho.parse`/`duho.main`.

```python
result = duho.parse(Deploy, [], config="./deploy.toml")
duho.value_sources(result)  # {"token": "env", "verbose": "config", ...}
```

### Logging Integration

Combine with `LoggingArgs` for structured logging:

```python
from duho import LoggingArgs, Cmd

class MyApp(LoggingArgs, Cmd):
    command: str
    "The command to run"
    ("--command",)

    def __call__(self):
        logger = self._logger_
        logger.info(f"Running: {self.command}")
```

`duho.main()` calls `self._set_loglevels_()` for you before dispatching the
command (pass `setup_logging=False` to opt out). If you drive the parser
yourself instead of using `duho.main()`, call `self._set_loglevels_()` before
you start logging.

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

### Shell completion

Generate a self-contained bash/zsh/fish completion script from your parser —
**static** generation (no runtime dependency, no per-keystroke re-invocation
of your program, unlike argcomplete):

```python
import duho

class MyApp(duho.Args):
    _completion_ = True  # opt-in: adds --print-completion to --help
    ...
```

```bash
python app.py --print-completion bash > _myapp.bash && source _myapp.bash
python app.py --print-completion zsh  > _myapp   # place on your $fpath
python app.py --print-completion fish > myapp.fish && source myapp.fish
```

`_completion_` is off by default (matches the `_version_` opt-in precedent) —
set it to add the `--print-completion {bash,zsh,fish}` flag. You can also
generate a script without adding the flag at all, via the standalone
function:

```python
import sys
import duho

duho.print_completion(MyApp, "bash", file=sys.stdout)
```

Both paths walk the built parser tree, including nested `_subcommands_`:
`Literal`/`Enum` fields offer their choices as completion candidates, and
`pathlib.Path`-typed fields get the shell's native file/directory
completion.

### Manual subparsers

`_subcommands_` (above) is the recommended way to build command trees. If you
need to attach duho commands to a parser you build yourself, pass the
subparsers action to `_parser_`:

```python
import argparse
from duho import Args

class Serve(Args):
    """Start the development server."""
    port: int = 8000
    ("--port",)

root = argparse.ArgumentParser()
subparsers = root.add_subparsers()
Serve._parser_(subparsers, name="serve")

args = root.parse_args()
```

## Commands: Args vs Cmd

`Args` classes are **pure data** — a typed namespace of parsed values. To make one
*runnable*, subclass `duho.Cmd` and implement `__call__(self)`. A `Cmd` instance is
directly callable (`__call__` runs the command), and `duho.main`/`duho.app`
dispatch a `Cmd`:

```python
import duho

class Deploy(duho.Cmd):
    """Deploy the application."""
    environment: str
    ("--env",)

    def __call__(self):
        print(f"deploying to {self.environment}")
        # returning None counts as a successful exit (code 0)

if __name__ == "__main__":
    raise SystemExit(duho.main(Deploy))
```

> **Upgrade note (breaking):** earlier releases made *every* `Args` instance
> callable. `Args` is now data-only; make a command a `Cmd` (or build one with
> `duho.command(...)`) and implement `__call__(self)`. Dispatching a bare data
> `Args` raises a clear `NotImplementedError` instead of silently doing nothing.
> The command entrypoint is `__call__` (not a plain `main` method): a `Cmd`
> subclass's namespace is user-owned — annotated fields become CLI flags — so a
> `main` method would collide with a declared `main` field (`--main`), whereas
> the `__call__` dunder never can.

To attach behavior to an **existing** data `Args` class without rewriting it,
use `duho.command(args_cls, func, *, name=None)` — it returns a `Cmd` subclass
whose `__call__` calls `func(self)` (the parsed instance):

```python
class Greet(duho.Args):
    name: str = "world"
    ("--name",)

def run(args):
    print(f"Hello, {args.name}!")

GreetCmd = duho.command(Greet, run, name="greet")
raise SystemExit(duho.main(GreetCmd))
```

`LoggingArgs` stays a data mixin; combine it as `class App(LoggingArgs, Cmd)`
(recommended base order — data mixin first, executable base last) to get logging
plus a runnable command.

## Cli: the application root

A leaf `Cmd` is lean — it declares its own flags and a `__call__`. The **root** of
a multi-command app usually wants more: a `--version` flag, shell completion, a
config file, a subcommand tree. `duho.Cli` is an **opt-in** mixin over `Cmd` that
gives those a typed home. Subclass `Cli` for your app root; keep leaf commands as
plain `Cmd`:

```python
import duho
from duho import Cli, LoggingArgs

class MyApp(LoggingArgs, Cli):     # data mixin first, root base last
    """My multi-command app."""
    _version_ = "1.2.3"            # adds --version
    _completion_ = True            # adds --print-completion {bash,zsh,fish}
    _config_ = "myapp.toml"        # layered config-file defaults
```

`Cli` is purely additive: it adds **no** new runtime behavior for *running* (it
inherits `Cmd.__call__` unchanged), and a plain `Cmd` root still works everywhere
`Cli` does — `Cli` just types and documents the app-root attributes (`_version_`,
`_distribution_`, `_completion_`, `_config_`, `_subcommands_`), all sandwich-named
so your CLI-field namespace stays 100% yours. `LoggingArgs` stays orthogonal — mix
it in when you want `-v`/`-q` verbosity, leave it out when you don't.

### Self-registration: `@MyApp.subcommand`

Instead of the root listing every child in `_subcommands_`, a leaf command file can
**attach itself** to the root with the `@MyApp.subcommand` decorator. This keeps
command definitions decentralized — each command lives in its own file and opts into
the app:

```python
# myapp/app.py
from duho import Cli

class MyApp(Cli):
    """My app."""
    _version_ = "1.0.0"

# myapp/commands/deploy.py
import duho
from myapp.app import MyApp

@MyApp.subcommand
class Deploy(duho.Cmd):
    """Deploy to a region."""
    region: str = "local"
    ("--region",)

    def __call__(self):
        print(f"deploying to {self.region}")

# myapp/commands/build.py
from myapp.app import MyApp

@MyApp.subcommand
class Build(duho.Cmd):
    """Build the project."""
    def __call__(self):
        print("building")
```

Each `@MyApp.subcommand` appends the class to `MyApp`'s **own** subcommand list
(materialized copy-on-write, so two `Cli` subclasses never cross-contaminate and a
parent's list is never mutated by a subclass). It composes with a
statically-declared `_subcommands_` (union + dedup — a child listed both ways
appears once). `MyApp._register_subcmd_(Deploy)` is the non-decorator form. Once the
command files are imported, `duho.main(MyApp)` sees the full tree (use `duho.app` if
you also want discovery/config/env — see [main vs app](#run-your-app)).

### App-wide config & env with `duho.app`

`duho.app(root, ...)` threads a `Cli` root's `_config_` and any `env` down to the
dispatched subcommand. TOML top-level keys apply to the root's fields; a
`[<Subcommand>]` table applies to that subcommand; and the resolved `duho.Env` (if
passed) is reachable from the command as `self._env_`:

```python
# app.toml
# [Deploy]
# region = "eu-west"

raise SystemExit(duho.app(MyApp, source="myapp.commands",
                          env=duho.Env("myapp")))
# `myapp deploy` now defaults region to "eu-west" (CLI still overrides),
# and Deploy.__call__ can read self._env_ for app-wide settings.
```

Pass `config="other.toml"` to `duho.app` to override the root's `_config_` for one
run. Precedence is unchanged: CLI > env > config > class default.

## Environment access

`duho.Env(prefix)` is an app-wide, typed view over the environment variables
sharing a common prefix. The prefix is uppercased with `-`→`_` and a trailing `_`
ensured, so `Env("my-app")` reads `MY_APP_*` keys:

```python
import duho

env = duho.Env("my-app")           # reads MY_APP_* from os.environ
debug = env.bool("DEBUG")          # MY_APP_DEBUG -> True for 1/true/yes/y/t
paths = env.list("CMDS_PATH", ty=Path)   # MY_APP_CMDS_PATH split on ":" into Paths
```

`Env` is a `MutableMapping`, so `env["KEY"]`, `env.get(...)`, `in`, and iteration
all work. `.bool(key)` treats a missing key as `False`; `.list(key, sep=":",
ty=str)` splits on `sep` and applies `ty` to each part (a missing or empty value
yields `[]` — an empty list). On construction `Env` also autoloads an optional
companion `<prefix>env` module of defaults an app may ship (e.g. `my_app_env`),
seeding its **upper-case, non-underscore** variables (all `str()`-coerced); a
missing one is ignored. Pass `Env(prefix, autoload=False)` to disable the import
— autoload imports `<prefix>env` from anywhere on `sys.path` (including the CWD),
so disable it if the prefix is not fully under your control. This is distinct from
the per-field `NS(env="VAR")` default layer above — that resolves one argparse
field; `Env` is the app-level accessor a driver reads settings through.

## String/target expansion

`duho.expand` expands `[a-b]` brace ranges into concrete strings — handy for
turning a host pattern into a target list. Output is **not** zero-padded:

```python
import duho

list(duho.expand("web[01-03].example.com"))
# ['web1.example.com', 'web2.example.com', 'web3.example.com']

list(duho.expand("rack[A-C]"))
# ['rackA', 'rackB', 'rackC']

list(duho.expand("plain"))          # no range -> unchanged
# ['plain']

list(duho.expand("x[1-2]y[1-2]"))   # multiple ranges -> cartesian product
# ['x1y1', 'x2y1', 'x1y2', 'x2y2']
```

Companion helpers `duho.pysafe` (coerce text to a Python-safe dotted identifier),
`duho.snakecase`/`duho.camelcase` (case conversion), and `duho.gettext` (a
`gettext` shim) round out the text utilities.

## Dynamic command discovery

Instead of listing subcommands by hand, point `duho.app` at a package or directory
and it discovers every command living there. Commands come in two shapes — a
**class command** (a `Cmd` subclass) and a **module command** (a `.py` file whose
top-level `main` is the entrypoint):

```
myapp/
├── cli.py            # defines the CLI root (global options)
└── cmds/
    ├── deploy.py     # a class command
    └── backup.py     # a module command
```

```python
# myapp/cmds/deploy.py
import duho

class Deploy(duho.Cmd):
    """Deploy the application."""
    name: str
    ("--name",)
    def __call__(self):
        print("deployed", self.name)
```

```python
# myapp/cmds/backup.py
"""Back things up."""

def main(args):
    print("backing up")
```

```python
import duho

class CLI(duho.LoggingArgs, duho.Cmd):
    "myapp"

# discover by dotted package name...
raise SystemExit(duho.app(CLI, source="myapp.cmds"))
# ...or by directory path:
raise SystemExit(duho.app(CLI, source=Path("myapp/cmds")))
```

The subcommand name is the class's `_parsername_`/class name for class commands,
and the file **stem with `_`→`-`** for module commands (`deploy_all.py` →
`deploy-all`; override with a module-level `_parsername_`/`_cli_name`). You can
also call `duho.discover_commands(source)` directly to get the `list[Command]`.

Discovery is **resilient**: a command that can't be imported (a missing optional
dependency → `ImportError`) or isn't actually a command (`NotImplementedError`) is
logged with a warning and skipped, so one broken command never takes down the rest.
A genuine bug in a command file (e.g. a `SyntaxError`) is *not* swallowed — it
surfaces so you can fix it.

## RunPath: ordered step commands (opt-in)

`duho.runpath` is an **opt-in** module that turns a directory of numbered `.py`
files into a single command that runs them **in order**. It plugs into the
discovery provider hook above and needs no core changes — core `duho` never
imports it; you activate it explicitly:

```python
import duho.runpath   # importing it registers the RunPath provider
```

A **RunPath directory** is a directory (with *no* `__init__.py`) of `NN-name.py`
*step* files:

```
release/
├── 10-build.py
├── 20-test.py
└── 30-publish.py
```

```python
# 10-build.py — a step's body is its top-level main/run/call (same precedence
# as module commands). It receives the parsed command instance.
def main(args):
    args._logger_.info("building")
```

Each step is named after the part of the filename after `NN-`; the numeric prefix
is its ordering key. A step module may override ordering and declare dependencies:

- `PRIORITY: int` — overrides the `NN` prefix for ordering.
- `REQUIRED: list[str]` — names of steps that must run **before** this one; the
  runner reorders so present dependencies run first.

Once `duho.runpath` is imported, pointing at the directory yields a run-path
command:

```python
import duho, duho.runpath
from pathlib import Path

cmd = duho.CmdBuilder("release", Path("release")).command
raise SystemExit(cmd()())   # build → test → publish, in order
```

### Selecting steps with `--rcopts` (`-O`)

`--rcopts` takes a comma-separated list of [fnmatch] patterns matched against step
names, with two markers:

- a leading `!` **disables** matching steps — `!*` disables everything, so
  `--rcopts '!*,test'` means "run only `test`";
- the token `strict` opts into **strict mode** (see below).

Later patterns win, so `!*,build-*` disables all then re-enables everything
matching `build-*`.

### Strict vs. resilient

The default is **resilient**, matching duho's discovery philosophy:

- an `--rcopts` pattern that matches no step is a **warning**, not an error;
- a step whose body raises is **logged and skipped** — the run continues.

Passing `strict` in `--rcopts` (e.g. `--rcopts 'strict'`) flips this: an unmatched
pattern raises, a `REQUIRED` dependency naming a missing step raises, and the first
step to fail re-raises and stops the run. So you run resilient by default and ask
for strict when you want a hard failure.

The module's public API is `duho.runpath.RunPathCmd`, `register()`, and
`unregister()` (`register`/`unregister` give explicit control over the provider —
`unregister()` is what tests use to keep provider state from leaking). These are
deliberately **not** on the top-level `duho.*` surface — RunPath is opt-in.

[fnmatch]: https://docs.python.org/3/library/fnmatch.html

## Module commands & lifecycle

A **module command** is a plain `.py` file. Its entrypoint is `main` (preferred),
falling back to `run` or `call`, and receives the parsed args instance:

```python
"""Restore from a backup."""   # docstring -> subcommand help

def init(args):                # optional: build a shared context
    return {"db": connect()}

def main(args):                # required entrypoint (or run/call)
    print("restoring", args)

def success(ctx, args):        # optional: runs only on a successful exit
    ctx["db"].commit()

def finally_(ctx, args):       # optional: always runs (cleanup)
    ctx["db"].close()

def register(parser, args):    # optional: add args directly on argparse
    parser.add_argument("--force", action="store_true")
```

The driver runs the lifecycle `init → main → success / finally_`: `ctx =
init(args)` builds a shared context (default: `None`), `main(args)` runs the
command, `success(ctx, args)` runs only on a **successful** exit (`main` returned
`None` or `0`, and did not raise — a non-zero exit code skips `success`), and
`finally_(ctx, args)` always runs. A `finally_` that itself raises is logged and
swallowed so it never masks `main`'s original exception or exit code. Note the
entrypoint receives **only** the args instance
(`main(args)`) — the context is threaded to `success`/`finally_`, not to `main`.
There is **no separate `logger` parameter**: hooks read the logger from the args
instance's `_logger_` (present on `LoggingArgs`-based commands), falling back to
`logging.getLogger("duho")`.

## Customizing a subcommand parser

A module command's optional `register` hook hands you the raw argparse subparser
so you can add arguments the declarative layer doesn't cover. It may be written
either **2-arg** `register(parser, args)` or **3-arg**
`register(parser, args, logger)` — duho inspects your hook's signature and calls
the form you declared:

```python
def register(parser, args):                 # 2-arg form
    parser.add_argument("--force", action="store_true")

def main(args):
    if args.force:
        ...
```

```python
def register(parser, args, logger):         # 3-arg form: logger is supplied
    logger.debug("registering deploy flags")
    parser.add_argument("--force", action="store_true")
```

For the 3-arg form the `logger` passed is the parsed args' own `_logger_` (on a
`LoggingArgs`-based root) or `logging.getLogger("duho")` — the same logger the
lifecycle hooks read off `args._logger_`. A `*args` hook is treated as
3-arg-capable; anything whose signature can't be introspected falls back to the
2-arg call.

Every subcommand parser is built with **parent-arg inheritance** — the root
command's global options (verbosity, etc.) appear on each subcommand automatically
via argparse `parents=`, so `myapp -v deploy` and `myapp deploy -v` both work.

> **Avoid the root's reserved flags in `register`.** Because the subparser already
> carries every root/global option, a `register` hook that adds one of them
> collides. Steer clear of the globals the root contributes — `-h`/`--help`,
> `--version` (if `_version_` is set), and, with a `LoggingArgs` root, `-v`
> (verbose), `-q` (quiet), and `--loglevel`. If you do collide, `duho.app` raises a
> clear error naming your command and the offending flag (rather than argparse's
> bare "conflicting option string"), so pick a different flag.

## Passthrough args

Argv after the first literal `--` separator is captured at parse time and exposed
on the parsed instance as `_passthrough_: list[str]` — useful for forwarding
trailing args to a wrapped tool. Only the first `--` splits; a second `--` is part
of the payload:

```python
class Run(duho.Cmd):
    def __call__(self):
        subprocess.run(["pytest", *self._passthrough_])

# myapp Run -- -k test_foo -x   ->   self._passthrough_ == ["-k", "test_foo", "-x"]
```

## Target fan-out (`duho.fanout`, opt-in)

duho dispatches **one** command per run by design. When you need to run that one
command against a list of targets (hosts, environments, datasets) and roll their
exit codes into one, `import duho.fanout` — an opt-in, stdlib-only helper (core
never imports it, and it stays off the top-level `duho.*` surface).

`run_targets(func, targets, *, max_workers=None, aggregate=max)` runs `func(target)`
for each target concurrently on a thread pool and returns an aggregated exit code
(`None` → `0`, an int as-is, an unhandled exception → logged and treated as `1` so
one failing target never aborts the rest; codes reduced by `max` — `0` only if all
succeed). Log lines a target emits while it runs are tagged with a `[<target>]`
prefix so interleaved concurrent output stays attributable; the prefixing filter is
installed on your existing stderr handler for the duration and removed afterwards.

```python
import duho, duho.fanout

targets = list(duho.expand("web[01-03].example.com"))

def deploy_to(host):
    log = duho.logging.getLogger("duho.deploy")
    log.info("deploying")          # emitted as "[web01.example.com] deploying"
    return 0                       # your per-target work; int/None exit code

raise SystemExit(duho.fanout.run_targets(deploy_to, targets, max_workers=4))
```

```text
[web01.example.com] deploying
[web02.example.com] deploying
[web03.example.com] deploying
```

`fan_out_command(command, make_instance, targets, ...)` is thin sugar for "run one
resolved duho command once per target": you supply `make_instance(target)` (a parsed
instance is app-specific) and each is dispatched via `duho.run_command`. Pass
`aggregate=any` or a custom reducer to change the exit-code policy. You can still
hand-roll a `ThreadPoolExecutor` wrapper if you prefer.

### Composing `app()`: the `dispatch=` seam

`duho.app(...)` owns discovery, parser build, registration, config/env thread-down,
parsing, and logging setup, then runs **one** selected command. To keep all of that
but override only the final run step — e.g. build a per-invocation context or fan the
command out over targets — pass `dispatch`:

```python
import duho, duho.fanout

def dispatch(command, instance):           # (resolved Command, parsed instance)
    targets = list(duho.expand(instance.targets))
    return duho.fanout.fan_out_command(
        command, lambda t: instance_for(t), targets
    )

raise SystemExit(duho.app(Root, source="commands/", dispatch=dispatch))
```

The callable receives the resolved command and the parsed instance and returns an
`int` exit code (which becomes `app()`'s return). With `dispatch=None` (the default)
`app()` behaves exactly as before, calling `duho.run_command` — existing callers are
unaffected.

## Generating launchers (`duho.scaffold`, opt-in)

An app laid out as `bin/` + a `lib/` (or `src/`) package can be run straight from a
checkout — no install — with a tiny launcher that puts the package on `PYTHONPATH`
and runs `python -m <app>`. `duho.scaffold` generates that launcher for you, as a
cross-platform pair. It's an **opt-in** dev tool: core `duho` never imports it, and it
is deliberately **not** on the top-level `duho.*` surface — you `import duho.scaffold`
or run `python -m duho.scaffold`.

```console
$ python -m duho.scaffold myapp --root . --libdir lib
bin/myapp
bin/myapp.cmd
```

This writes a matched pair into `<root>/bin/`:

- `bin/myapp` — a POSIX `sh` launcher that resolves its own directory, derives the app
  root (the parent of `bin/`), prepends `<root>/<libdir>` to `PYTHONPATH`, and execs
  `python -m myapp "$@"`;
- `bin/myapp.cmd` — the Windows cousin doing the same via `%~dp0` and `%PYTHON%`.

Both launchers are dependency-free and honor a **`PYTHON` environment override** so you
can pin the interpreter (e.g. `PYTHON=python3.11 bin/myapp`). The generator writes
**plain files — never symlinks** (symlinks need privilege on Windows and add failure
modes), and sets the POSIX launcher's executable bit best-effort. An existing launcher
is **not** overwritten unless you pass `--force` (`overwrite=True`), so a customized
launcher is never silently clobbered.

The same thing from Python:

```python
from duho.scaffold import generate_launchers

paths = generate_launchers("myapp", ".", libdir="src")   # -> [Path("bin/myapp"), Path("bin/myapp.cmd")]
```

The CLI dogfoods duho itself — `duho.scaffold.ScaffoldCmd` is an ordinary `duho.Cli`
command.

## Examples

Two self-contained example CLIs under [`examples/`](examples/) each build a small
umbrella app with an `install` subcommand, ported from real-world scripts to show
duho's full surface (they stub the actual filesystem work — the point is the CLI):

- [`examples/dotagents.py`](examples/dotagents.py) — an agent-config installer
  (`LoggingArgs`, `_subcommands_`, `--dest`/`--dry-run`/`--with-examples`):

  ```python
  class Install(LoggingArgs):
      """Copy the agent-config payload into the destination directory."""

      dest: Path = Path.home() / ".agents"
      ("--dest",)
      dry_run: bool = False
      ("--dry-run",)

  # ...
  if __name__ == "__main__":
      sys.exit(duho.main(Dotagents))
  ```

  ```bash
  python examples/dotagents.py install --dry-run
  ```

- [`examples/fileinstall.py`](examples/fileinstall.py) — an `install(1)`-like file
  installer; exercises positionals, `Union` types, `NS(nargs="?")`, a custom
  `action=UpdateAction`, and `NS(conflicts=...)` mutually-exclusive grouping:

  ```python
  class Install(LoggingArgs, Cmd):
      """Install SOURCE at DESTINATION."""

      options: Arg[
          dict,
          NS(action=UpdateAction, type=lambda x: [x.split("=", maxsplit=1)]),
      ] = {}
      ("-O",)
      source: Path
      ("source",)
      destination: Path
      ("destination",)

  # ...
  if __name__ == "__main__":
      sys.exit(duho.main(FileInstall))
  ```

  ```bash
  python examples/fileinstall.py install --type dir -O k=v src dst
  ```

## Documentation

Full documentation: https://jose-pr.github.io/duho/

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License. See [LICENSE](LICENSE) for details.
