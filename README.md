# duho

[![Version](https://img.shields.io/pypi/v/duho.svg)](https://pypi.org/project/duho/)
[![Python versions](https://img.shields.io/pypi/pyversions/duho.svg)](https://pypi.org/project/duho/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/jose-pr/duho/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-latest-blue.svg)](https://jose-pr.github.io/duho/)
[![CI](https://img.shields.io/github/actions/workflow/status/jose-pr/duho/test.yml)](https://github.com/jose-pr/duho/actions/workflows/test.yml)

**Duho** is a declarative CLI framework for Python that turns the complexity of building command-line applications into simple, type-safe class definitions.

Named after the sacred Taíno ceremonial stool—a symbol of power and authority—duho provides the **foundation** from which you command your application.

[Full documentation](https://jose-pr.github.io/duho/)

## Features

- **Declarative**: Define CLI arguments as class annotations—no boilerplate argparse setup
- **Type-safe**: Built-in type conversion and validation from Python type hints
- **Logging**: Integrated colored logging with configurable verbosity levels
- **Subcommands**: Easily compose multi-command CLI applications
- **Extensible**: Customize argument behavior with protocols and builders
- **Layered defaults**: Environment variables and TOML or JSON config files sit under the command line, and `duho.value_sources` reports which layer supplied each value
- **Shell completion**: Static bash, zsh, fish and PowerShell completion scripts, generated from the same declarations
- **Agent help**: A machine-readable `--help` document, and the same classes served as MCP tools
- **Discovery**: Commands from loose files, packages or installed entry points, plus ordered step directories (RunPath)
- **Opt-in modules**: Target fan-out, launcher scaffolding and the MCP server, none imported unless you ask

## Installation

```bash
pip install duho
```

duho has no required runtime dependencies. Each extra adds one optional integration:

| Extra | Install | Adds |
| --- | --- | --- |
| `colorama` | `pip install duho[colorama]` | `colorama>=0.4.6,<0.5`: named log colors (`color="red"`) and ANSI rendering on a legacy Windows console; log color itself needs no extra |
| `config` | `pip install duho[config]` | `tomli>=2.0,<3` on Python below 3.11, to read TOML config files on 3.9 and 3.10 (3.11+ has `tomllib`); JSON config needs nothing |

### Optional Dependencies

Colored logging works out of the box (gated on a TTY / `NO_COLOR` / `FORCE_COLOR` /
`TERM=dumb`, raw ANSI codes, no dependency required). `colorama` is only needed to resolve a
*named* color (e.g. `color="red"` on `duho.add_logging_level`) and to patch a
legacy Windows console so it renders ANSI codes instead of showing them literally:

```bash
pip install duho[colorama]
```

## Quick start

<!-- runnable: commands -->
```python
import duho
from duho import Args

class MyApp(Args):
    name: str
    "The name to greet"

    count: int = 1
    "How many times to greet"

if __name__ == "__main__":
    args = duho.parse(MyApp)
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

**Every annotated field gets a default long flag for free** —
`"--" + kebabcase(field_name)` (underscores become dashes; a camelCase or
ACRONYM name is kebab-cased too: `dry_run` → `--dry-run`, `testMe` →
`--test-me`). `name` above needs no tuple to become `--name`. Reach for an
explicit flags tuple only for:

- a **positional** — `("source",)`
- a **short flag or extra aliases** — `("-n", "--name")`
- a **different spelling** — `("--env",)` for a field named `environment`

A bare `"--"` entry inside a tuple expands to that same default long flag, so
you can pair a short flag with it without spelling it out:
`("-n", "--")` → `("-n", "--name")`.

### A runnable command

`Args` is data; `Cmd` adds `__call__`, and `duho.main` builds the parser, parses
`argv`, runs the command and returns its exit code:

<!-- runnable -->
```python
import duho
from duho import Cmd

class Greet(Cmd):
    """Print a greeting."""

    name: str = "world"
    "Who to greet"

    def __call__(self):
        print(f"Hello, {self.name}!")

if __name__ == "__main__":
    raise SystemExit(duho.main(Greet))
```

```bash
python greet.py --name Alice
```

### Subcommands

A root lists its subcommands in `_subcommands_`; `duho.main` dispatches to the
selected one:

<!-- runnable: commands -->
```python
import duho
from duho import Cli, Cmd

class Serve(Cmd):
    """Start the development server."""

    port: int = 8000
    "Port to listen on"

    def __call__(self):
        print(f"serving on {self.port}")

class Build(Cmd):
    """Build the project."""

    output: str = "dist"
    "Output directory"

    def __call__(self):
        print(f"building to {self.output}")

class App(Cli):
    _subcommands_ = [Serve, Build]

if __name__ == "__main__":
    raise SystemExit(duho.main(App))
```

```bash
python app.py serve --port 3000
python app.py build
python app.py --help
```

### Layered defaults

A field can take its default from an environment variable (`NS(env=...)`) or a
config file; the command line wins over both. `duho.value_sources` tells you
which layer supplied each value:

<!-- runnable -->
```python
import duho
from duho import Arg, Cmd, NS

class Deploy(Cmd):
    """Deploy the application."""

    token: Arg[str, NS(env="DEPLOY_TOKEN")] = "none"
    "Auth token"

    def __call__(self):
        source = duho.value_sources(self)["token"]
        print(f"token {self.token!r} came from {source}")

if __name__ == "__main__":
    raise SystemExit(duho.main(Deploy))
```

```bash
python deploy.py --token abc
```

With `DEPLOY_TOKEN=abc` set and no `--token`, the same program reports `env`.

## Command line

Two opt-in modules run as `python -m`:

| Command | What it does |
| --- | --- |
| `python -m duho.scaffold <app>` | Writes `bin/<app>` and `bin/<app>.cmd` launchers that run the app from a checkout (`--root`, `--libdir`, `--python`, `--force`) |
| `python -m duho.mcp <app>` | Serves a CLI's commands as MCP tools over stdio; `<app>` is `module:ClassName` |

```bash
python -m duho.scaffold --help
python -m duho.mcp --help
```

## API overview

Everything in the first row is importable from the top-level `duho`; the other
modules are imported by name. The full reference is on the
[documentation site](https://jose-pr.github.io/duho/api/reference/).

| Module | Purpose |
| --- | --- |
| `duho` | `Args`, `Cmd`, `Cli`, the `Arg`/`NS`/`Meta` field helpers, `parser`, `parse`, `main`, `app`, `run_command`, `command`, `subcommand`, `value_sources`, `parse_bool`, `utf8_stdio`, `AUTO` |
| `duho.discovery` | `discover_commands`, `discover_entry_points`, `CmdBuilder`, `ModuleCommand`, `register_command_provider` |
| `duho.env` | `Env`, the prefixed, typed environment accessor |
| `duho.logging` | Colored log formatting, custom levels, `init_stderr_logging`; a superset of stdlib `logging` |
| `duho.formatters` | `DefaultsFormatter`, `ColorHelpFormatter`, `ColorDefaultsFormatter` for `_help_formatter_` |
| `duho.presets` | `LoggingArgs`, the `-v`/`-q`/`--loglevel` mixin |
| `duho.agenthelp` | The machine-readable `--help` document (`describe`, `print_agent_help`) |
| `duho.completion` | bash, zsh, fish and PowerShell completion-script generation |
| `duho.text` | `expand`, `pysafe`, `camelcase`, `snakecase`, `kebabcase` |
| `duho.qualname` | Dotted-name algebra for command qualified names |
| `duho.parsers` | Subparser helpers (`pop_action`, `find_subparsers`, `command_name`, ...) |
| `duho.fanout` | Opt-in: run one command against many targets and roll up the exit codes |
| `duho.testing` | Opt-in: `invoke`, run a command line in-process and get its status and output |
| `duho.runpath` | Opt-in: ordered `NN-name.py` step directories as one command |
| `duho.scaffold` | Opt-in: generate run-from-checkout launchers |
| `duho.mcp` | Opt-in: expose a CLI as MCP tools |

## Guide

### Core Concepts

#### Args: Declare Your CLI

Define arguments using class annotations. The docstring becomes the help text, and expressions after the annotation become argument flags:

<!-- runnable -->
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
    
    dry_run: bool = False
    "Preview changes without applying them"
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

**The flags-tuple/docstring idiom needs readable source.** Duho locates a
class's own body by reading and parsing its source file at parser-build time,
so it can tell a docstring/flags-tuple statement apart from an ordinary class
attribute. A class whose source isn't available this way — defined in a REPL
or via `exec`, or shipped as a frozen executable (PyInstaller, Nuitka), a
`.pyc`-only install, or a zipapp — logs a one-time warning and falls back to a
derived `--field-name` flag with no help text for every field, silently
dropping any class-body flags/docstrings/`NS(env=...)`. Use `Meta`/`NS`
metadata instead in that case — it needs no source lookup:

```python
class Copy(Args):
    source: Arg[str, Meta(flags=("-s", "--source"), help="Source path")]
```

#### Supported Field Types

| Annotation | Behavior |
| --- | --- |
| `str`, `int`, `float`, `bool` | Direct conversion; `bool` gets `store_true` or `--flag`/`--no-flag` (see above) |
| `typing.Literal["a", "b"]` | `choices=("a", "b")`; mixed-type literals (`Literal["auto", 1]`) try each declared value's own type and keep whichever round-trips |
| `enum.Enum` subclass | `choices` are the member **names**; the parsed value is the Enum member (`Color["RED"] -> Color.RED`). `Meta(enum_by="value")` matches the members' value text instead |
| `list` / `list[T]` | As an OPTION: one value per flag occurrence, repeated (`--x a --x b`) to accumulate — via `action="extend", nargs=None`. As a POSITIONAL: variadic (`nargs="*"`, space-separated: `a b c`). Bare `list` elements are `str`; default is `[]` when no explicit default is given. Pass an explicit `NS(nargs="*")` to opt an OPTION back into space-separated multi-value |
| `set` / `set[T]` | Same option-vs-positional split as `list`, but the final value is a `set` (dedups; **iteration order is not guaranteed**); bare `set` elements are `str`; default is `set()` when no explicit default is given |
| `tuple[T, ...]` / `tuple` | Variadic **homogeneous** tuple, same option-vs-positional split as `list`, final value a `tuple` (order preserved, no dedup); bare `tuple` elements are `str`; default is `()` when no explicit default is given. A fixed-length heterogeneous `tuple[A, B]` is **not** supported and raises a clear error at parser build — use `tuple[T, ...]` |
| `dict` / `dict[str, V]` | Each occurrence is one `KEY=VALUE` token; repeated flags merge into one dict (`--opt k=1 --opt j=2` → `{"k": ..., "j": ...}`) via `UpdateAction`; the value half is converted with `V` (bare `dict` == `dict[str, str]`); only **`str` keys** are supported (a non-`str` key type is a clear build-time error); default is `{}` when no explicit default is given |
| `datetime.date` / `datetime.datetime` / `datetime.time` | Parsed via the type's own `fromisoformat` (e.g. `2026-01-01`, `2026-01-01T12:00:00`). A trailing `Z` UTC marker (RFC 3339) is accepted for `datetime`/`time` on **every** supported Python version, including 3.9/3.10 (rewritten to `+00:00` before delegating); a `date` value never accepts a trailing `Z` on any version (a date has no time/UTC component). A basic, no-dash format like `20260101` works for `date`/`datetime` on 3.11+ (native `fromisoformat` accepts it) but is **not** supported on 3.9/3.10 |
| `typing.Optional[T]` / `T \| None` (3.10+) | Not required; tries `T` |
| `typing.Union[A, B]` / `A \| B` (3.10+) | Tries each type in declaration order |
| `Union`/`Optional` containing an `Enum` | The Enum member is matched by **name**, same as a bare `enum.Enum` field — a name match wins before falling through to a later `str` member, so declaration order matters (`Union[Color, str]` with `--c RED` yields `Color.RED`, while `--c other` yields the string `"other"`) |
| `Arg[int, duho.Count()]` | A repeatable counted flag (`-vvv` → `3`), via argparse `action="count"`. The value is the number of occurrences; pair a short flag like `("-v",)` with it. `LoggingArgs` uses this for `-v`/`-q` |

**Negative numbers** work as values out of the box — `--temp -5` and a positional
`int` accepting `-3` both parse correctly (argparse's `_negative_number_matcher`
handles them as long as no option is itself declared to look like `-1`). If you
truly need a `-1`-style flag, use the `NS(kwargs=...)` escape hatch.

#### Positional arguments

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

#### An option between two positionals just works

argparse's own greedy positional matching normally breaks when an option is
placed BETWEEN a fixed positional and a variadic one after it (a well-known
argparse limitation, [bpo-15112](https://bugs.python.org/issue15112)) — the
classic `<command> <name> [TARGET ...]` shape a per-target/fan-out app wants:

```python
class Query(Args):
    namespace: str
    ("namespace",)

    targets: list[str] = []
    ("targets",)

    filters: list[str] = []
    ("-f",)
```

```bash
python query.py user -f username=root nas1   # used to fail: "unrecognized arguments: nas1"
```

Duho detects this shape (a variable-arity positional alongside another
positional in the same parser) and transparently reorders recognized flags
ahead of the positional run before the real parse — all four orderings
(flag before/after/between the positionals, or no flag at all) now parse
identically. A genuinely unrecognized/misspelled flag still raises argparse's
own honest "unrecognized arguments" error; the fix never silently absorbs a
typo as a phantom positional value.

#### Field metadata: `NS` or `Meta`

Extra per-field configuration goes in the `Arg[T, ...]` metadata slot. `NS(...)`
(an `argparse.Namespace`) is the untyped form; `duho.Meta` is the typed,
typo-safe form — a dataclass whose unknown keyword is a `TypeError`
(`NS(hlep=...)` is ignored, with a logged warning) raised as soon as the annotation is
evaluated: at class-definition time on Python 3.9-3.13 with eager
annotations, or at first parser build on 3.14+ (PEP 649), under string
annotations, or with `from __future__ import annotations`:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

class App(Args):
    level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
```

`Meta` accepts every field `NS` does EXCEPT `dest` (`help`, `env`, `conflicts`,
`conflicts_required`, `group`, `action`, `nargs`, `const`, `default`, `choices`,
`metavar`, `required`, `type`, `version`, `flags`, `kwargs`, `enum_by`,
`literal_value`) and only merges the fields you set. A field's `dest` is always its declared name — there is no
`dest=` override on `Meta`, so `Meta(dest=...)` is a `TypeError` at
class-definition time instead of `NS(dest=...)`'s silently-ignored value.
`NS` keeps working forever. Any metadata object exposing a str
`.documentation` attribute (a PEP-727-style `Doc`) contributes help text.

**Misdeclaration warnings.** Two mistakes that would otherwise do nothing are logged
once as a WARNING on the `duho.args` logger when the parser is built; neither is an
error. An `NS(...)` key that is not a `Meta` field is ignored with
`App.port: NS(hlep=...) is not a Meta field and is ignored; closest Meta field:
'help'`; `dest` is such a key, since a field's `dest` is always its own name. And a
sandwich attribute on a command class that duho does not read but whose spelling is
a near-miss of one it does (`_verison_` for `_version_`) logs `App declares
'_verison_', which duho does not read; did you mean '_version_'?`. An attribute of
your own that only extends a known name (`_config_dir_`) is not reported.

**A converter's own message.** With `NS(type=parse_port)` (or `Meta(type=...)`), a
`ValueError`/`TypeError` your converter raises with a non-empty message becomes the
usage error: `app: error: argument --port: port must be 1..65535`. A builtin,
duho's own factories, an empty message and `argparse.ArgumentTypeError` keep
argparse's text, and a bad env or config value never echoes the value or the
message. This covers an explicit `type=`, not an annotation such as `port: SomeClass`.

**Matching an Enum by value.** `Meta(enum_by="value")` matches the text against
`str(member.value)` on the command line, in env and in config, and `--help`, error
text and completion list the values; the parsed field is still the member. It
applies to `Enum`, `Optional[Enum]` and collection elements, not to a `Literal` of
members. Members whose value text collides raise `ValueError` at build time.

```python
class Mode(enum.Enum):
    FAST = "f"
    SLOW = "s"

class App(duho.Cmd):
    mode: Arg[Mode, Meta(enum_by="value")] = Mode.FAST   # --mode {f,s}
```

**An option value that looks like `--`.** `Meta(literal_value=True)` on an option
that takes exactly one value makes the token after the flag always its value, so
`--k --` gives `k == "--"` and `--k -x -- tail` gives `k == "-x"` with `tail` still
the passthrough. A flag, a variable-arity option or a positional with it raises
`ValueError` at build time. Not joined: an abbreviated long flag (`--ka` for
`--k...`) and a short flag inside a cluster (`-vk -x`).

#### Mutually exclusive options

Set `NS(conflicts="group-name")` on the fields that must not be used together.
Duho builds one `argparse` mutually-exclusive group per distinct `conflicts`
value, so only one option from the group may appear on the command line:

<!-- runnable -->
```python
import duho
from duho import Args, Arg, NS

class Archive(Args):
    """Create an archive."""

    gzip: Arg[bool, NS(conflicts="compression")] = False
    "Compress with gzip."

    zstd: Arg[bool, NS(conflicts="compression")] = False
    "Compress with zstd."

    none: Arg[bool, NS(conflicts="compression")] = False
    "Store uncompressed."

if __name__ == "__main__":
    print(duho.parse(Archive))
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

    pull: Arg[bool, NS(conflicts="mode")] = False
```

```bash
python app.py            # error: one of the arguments --push --pull is required
python app.py --push     # ok
```

#### Titled argument groups

Set `NS(group="Section title")` to bucket fields under a named section in
`--help`. Fields sharing a title join the same section; the rest stay under the
default `options:`:

```python
class App(Args):
    outfile: Arg[str, NS(group="Output options")] = "-"
    "Where to write."

    verbose: Arg[bool, NS(group="Output options")] = False
    "Verbose output."
```

A field may combine `group=` and `conflicts=`: the mutually-exclusive group is
nested inside the titled section (still exclusive, and shown under the title).

#### Prettier help: defaults & color

Set a class-level `_help_formatter_` to opt into a richer `--help`. duho ships
three `argparse.HelpFormatter` subclasses (all off by default, so plain help is
unchanged unless you ask):

| Formatter | Effect |
| --- | --- |
| `duho.DefaultsFormatter` | Appends `(default: X)` to each option's help — but skips the noise of `None`/`""`/`False` defaults (unlike argparse's own `ArgumentDefaultsHelpFormatter`) |
| `duho.ColorHelpFormatter` | ANSI-colors section headings and option flags, **gated** on a TTY (honors `NO_COLOR` and `TERM=dumb`; `FORCE_COLOR` forces it on). When color is off the output is byte-identical to the default, so piping stays clean |
| `duho.ColorDefaultsFormatter` | Both composed |

```python
class App(duho.Cli):
    _help_formatter_ = duho.ColorDefaultsFormatter
    region: str = "us-east"
    "Target region"
```

A root's `_help_formatter_` propagates to its `_subcommands_` tree, so one setting
styles the whole app. You can also point it at any custom `HelpFormatter`
subclass. (Note: argparse only renders defaults for options that *have* help text,
so give a field a docstring to see its `(default: …)`.)

#### Run your app

`duho.main(cls, argv=None, *, setup_logging=True)` builds the parser, parses
`argv` (or `sys.argv` when omitted), optionally wires up stderr logging and
verbosity (for classes mixing in `LoggingArgs`), and runs the command. The class
must be a `duho.Cmd` (see [Commands: Args vs Cmd](https://github.com/jose-pr/duho/#commands-args-vs-cmd) below) —
`main` dispatches the parsed instance via `__call__`:

> **`main` vs `app` — which entry point?** Use **`duho.main(cls)`** for a command
> (or a tree declared statically with `_subcommands_`) — it's the simple, direct
> runner. Reach for **`duho.app(root, ...)`** when you need what `main` doesn't do:
> **discovering** commands from a package/directory (`source=`), threading app-wide
> **config/env** down to subcommands, or **overriding dispatch** (`dispatch=`, e.g. to
> fan a command out over targets). `app` is the multi-command driver; `main` is the
> one-shot runner. Both dispatch a `Cmd` root via `__call__`.

<!-- runnable -->
```python
import duho
from duho import Cmd

class Greet(Cmd):
    """Print a greeting."""
    name: str = "world"
    "Who to greet"

    def __call__(self):
        print(f"Hello, {self.name}!")
        # returning None counts as a successful exit (code 0)

if __name__ == "__main__":
    duho.run(Greet)
```

`duho.run(root, argv=None)` is the program entry: it calls `main`, prints the
command's answer and exits with the status. `duho.main` returns that status
instead, for a caller that wants it (`raise SystemExit(main(Greet))` is the same
program minus the printing). The return contract, once: `None` or an `int` is the
exit status; anything else is an answer, which `run` prints to stdout (a `str` as
it is, other values as JSON) with status 0 and an MCP client receives as the tool
result. A command that wants both a status and an answer returns `duho.Result`;
one that fails with a message raises `duho.CommandError` (the
running guide covers both).

`SystemExit` raised by argparse (bad args, `--help`, `--version`) propagates
normally. Dispatching a bare data `Args` (not a `Cmd`) raises a clear
`NotImplementedError` naming the class.

`__call__` may be `async def` — when it returns a coroutine, `main` (and
`run_command`) run it to completion via `asyncio.run` at the call site (imported
lazily, so a synchronous app never pays for it), and the awaited value becomes
the exit code. Module-command lifecycle hooks stay synchronous.

**Subcommands**: set `_subcommands_` to a sequence of `Cmd` subclasses and
`main`/`_parser_` wires up `add_subparsers(dest="_duho_command_", required=True)`
automatically — no manual subparser plumbing needed. The dest is private; a
parsed instance has no `.command` attribute from this. Nested `_subcommands_`
(a subcommand that itself declares `_subcommands_`) compose naturally into
multi-level command trees, and `main` always dispatches to the deepest
selected command via `__call__`.

```python
class Serve(Cmd):
    """Start the development server."""
    port: int = 8000
    def __call__(self):
        print(f"serving on {self.port}")

class Build(Cmd):
    """Build the project."""
    output: str = "dist"
    def __call__(self):
        print(f"building to {self.output}")

class App(Args):
    """Example multi-command app."""
    _subcommands_ = [Serve, Build]

if __name__ == "__main__":
    raise SystemExit(main(App))
```

```bash
python app.py serve --port 3000
python app.py build --output dist
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
python app.py create web   # full name (the class name, kebab-case)
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

With no `_version_`, a class-level `__version__` string is used the same way
(`class MyApp(Cmd): __version__ = "1.2.3"` also gets `--version`). `_version_`
wins when both are set, and a `__version__` that is not a string is ignored.

**Autodetected version**: set `_version_ = duho.AUTO` to resolve the version
from installed package metadata via `importlib.metadata.version(...)` instead
of hardcoding a string. By default the distribution name is the class's
top-level import package (`cls.__module__.split(".")[0]`); set `_distribution_`
to override it when the import name differs from the distribution name on
PyPI:

<!-- runnable -->
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

#### The application's name

An application has one name, and every surface uses it: the usage line, the
name a `--print-completion` script binds, the `<NAME>_MCP` launch variable, the
root of every MCP tool name (`NAME.command`) and the default logger that
`-v`/`-q` raise. It is, in order: `duho.app(name=...)`, the root class's own
`_parsername_`, the top-level package the root class is defined in (the `pkg`
of `pkg.cli.Root`, also under `python -m pkg`), then the kebab-case of the class
name. A script run directly has no package, so it is named after its class. The
name never comes from the script's file name, so `python app.py`, `python tools/run.py`,
`python -m pkg` and a console script all agree. Declare `_parsername_` to fix
the name wherever the class lives. A subcommand's name is its own
`_parsername_`, else the kebab-case of its class name (`Create` → `create`).

#### Output encoding

`duho.main`/`duho.app` call `duho.utf8_stdio()` first thing, before anything
else runs. It reconfigures `sys.stdout`/`sys.stderr` to UTF-8 whenever nothing
already guarantees correct encoding — skipping a real terminal (`isatty()`),
an already-UTF-8 stream, a stream with no `.reconfigure()` (pytest's capture,
`io.StringIO`), Python's own UTF-8 mode, and an explicit `PYTHONIOENCODING`.
This matters most on Windows: piped or redirected output there defaults to
the console's ANSI code page (`cp1252`) with strict error handling, so
`print()`-ing (or `--version`-ing) a non-ASCII character used to raise
`UnicodeEncodeError` — empty output, exit 1. UTF-8 is the only encoding that
can't raise, so the default makes that whole class of crash impossible.

**Opt-out**, for an app that wants to manage its own stdio: set
`_utf8_stdio_ = False` on the root class, or pass `utf8_stdio=False` to
`main`/`app` (an explicit kwarg always wins over the class attribute):

```python
class MyApp(Cli):
    _utf8_stdio_ = False  # duho leaves stdio completely alone
```

```python
main(MyApp, utf8_stdio=False)      # same, for one call
app(MyApp, utf8_stdio=False)
```

Opted out (or when a parser built by duho is used outside `main`/`app`
entirely), duho's own writes still can't crash: `--version` and duho's
internal error messages go through a write helper that falls back to
`errors="backslashreplace"` instead of raising when the stream's own
encoding can't represent a character. The app may call `duho.utf8_stdio()`
itself — it accepts an optional `streams=` mapping for reconfiguring
something other than the real `sys.stdout`/`sys.stderr` — or do anything else.

**PowerShell tip**: capturing/piping a Python process's UTF-8 output through
PowerShell still needs the *console itself* set to UTF-8 for the text to
*display* correctly (`Select-String`, `> file`, etc. all decode using
`[Console]::OutputEncoding`):

```powershell
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
```

#### Build and Parse

```python
parser = Deploy._parser_()
args = parser.parse_args()

print(f"Deploying to {args.environment} (dry-run: {args.dry_run})")
```

#### Quick parse

`duho.parser(cls, ...)` is the module-level entry point for building a parser
(delegates to `cls._parser_(...)`). `duho.parse(spec, argv=None, *,
parser_kwargs=None, config=None)` goes one step further and parses in a single call:

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
for that call if the instance already supplies a value. A field counts as set when
it was passed to the constructor or changed since, whether by assignment or by
mutating its value in place (`base.tags.append("x")`). A directly built instance
carries every field that has a default; a field with no default stays unset on it
until you assign one.

#### Parsing only the globals (config before commands)

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
only); it is the public form of the prepass `duho.app` runs internally. On a root's
static `_subcommands_` tree only options written before the subcommand name count as
globals: an option after the name (even one spelled like a prefix of a root option)
belongs to the subcommand. Pass any
`cls._parser_` keyword through it (e.g. `add_help=False`). If you also want the
leftover argv, call `parser.parse_known_args` directly instead.

#### Configuration layers

Beyond instance overrides, two more default layers apply at every entry point
(`duho.parse`, `duho.main`, `duho.parse_globals`, `duho.app` and a parser from
`duho.parser(cls)`): per-field environment variables and a config file. Combined
precedence ladder, highest wins:

```
CLI args > env var > config file > class default
```

A value supplied by *any* layer also un-requires that field — a field with
no class default that's set in the config file (say) no longer needs to be
passed on the CLI.

**Environment variables**: annotate a field with `NS(env="VAR_NAME")`:

<!-- runnable -->
```python
from duho import Args, Arg, NS

class Deploy(Args):
    token: Arg[str, NS(env="DEPLOY_TOKEN")] = ""
    "Auth token"
```

**Config file**: set `_config_` on the class, or pass `config=` to
`duho.parse`/`duho.main` (the kwarg overrides the class attr):

```python
class Deploy(Args):
    _config_ = "~/.config/myapp/config.toml"
    ...

result = duho.parse(Deploy, config="./deploy.toml")
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

**A malformed config file** (invalid JSON or TOML, a TOML file with no TOML reader
installed, or a file whose top level is not a table/object) is a usage error naming
the file, exit status 2, under `main`, `parse` and `app` alike; `--help` and
`--version` still work.

**JSON config**: a config path ending in `.json` is parsed as JSON (stdlib, no
extra dependency), producing the same nested-dict shape as TOML — top-level keys
map to the root, a nested object named for a subcommand maps to that subcommand:

```json
{ "verbose": true, "install": { "target": "prod" } }
```

**Any other format via `_config_loader_`**: set a class-level
`_config_loader_ = Callable[[Path], dict]` and duho calls it *instead of* the
built-in JSON/TOML dispatch. This is the zero-dependency escape hatch for a
format duho does not ship — e.g. YAML, plugged with your own `yaml.safe_load`,
without duho ever importing or depending on it:

```python
import yaml

class Deploy(duho.Cli):
    _config_ = "./deploy.yaml"
    _config_loader_ = staticmethod(lambda path: yaml.safe_load(path.read_text()) or {})
```

An exception your loader raises propagates to the caller unchanged, so the
application reports it its own way; a loader that returns something that is not a
mapping is a usage error naming the file.

**Choosing the config file at run time.** Beyond `_config_` and `config=`, the root
can name where the path comes from: `_config_env_ = "MYAPP_CONFIG"` reads it from
that environment variable, and `_config_field_ = "config"` reads it from the field of
that name when the user gave it on the command line or through the field's own env
var. Highest first: an explicit `config=` argument, the `_config_field_` field, the
`_config_env_` variable, then `_config_`. A path chosen by the field or the variable
must exist (a missing file raises `FileNotFoundError`), unlike a `_config_` that does
not exist yet. A `_config_field_` naming no declared field is a `ValueError` naming
the class. Neither is applied to a tree served over MCP.

```python
class Tool(duho.Cmd):
    _config_env_ = "MYAPP_CONFIG"
    _config_field_ = "config"

    config: Optional[str] = None
    "Config file (else $MYAPP_CONFIG)"
```

**Env/config value conversion.** Layered values are converted to match what CLI
parsing of the same field yields. A `bool` field reads `1/true/yes/on/y/t` as
`True` and `0/false/no/off/n/f`/empty as `False` (an unknown string is an error);
`duho.parse_bool(text)` applies the same rule to any string, and `duho.text.BOOL_TRUE` and
`BOOL_FALSE` are the token tables.
A **collection** field (`list`/`set`/`tuple`) treats an env var or a TOML
*string* as a **single element** (`FILES=a.txt` → `["a.txt"]`, matching one CLI
occurrence), while a TOML **array** converts element-wise. Non-string TOML scalars
are coerced to the field type (`timeout = 30` for a `float` field → `30.0`).

**Debugging where a value came from**: `duho.value_sources(parsed)` returns
`{field_name: "cli" | "env" | "config" | "default"}` for a parsed
instance — the one `duho.parse` returns, or `self` inside a command's `__call__`.
(`duho.main` returns the command's exit code, not an instance.)

```python
result = duho.parse(Deploy, [], config="./deploy.toml")
duho.value_sources(result)  # {"token": "env", "verbose": "config", ...}
```

#### Logging Integration

Combine with `LoggingArgs` for structured logging:

<!-- runnable -->
```python
from duho import LoggingArgs, Cmd

class MyApp(LoggingArgs, Cmd):
    command: str
    "The command to run"
    ("command",)

    def __call__(self):
        logger = self._logger_
        logger.info(f"Running: {self.command}")
```

`duho.main()` calls `self._set_loglevels_()` for you before dispatching the
command (pass `setup_logging=False` to opt out). `-v`/`-q` raise the
application's logger (see [the application's name](https://github.com/jose-pr/duho/#the-applications-name))
for every command, whether or not the command is itself a `LoggingArgs`;
`_logger_name_` on a command, or on the root, names a different one. If you drive the parser
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

`-v`/`-q` step through the registered levels from `_base_loglevel_` on the
`LoggingArgs` root (default `logging.INFO`): a level number or the name of a
registered level, anything else a `ValueError` at dispatch. With
`_base_loglevel_ = logging.WARNING`, `-v` gives `INFO` and `-vv` gives `DEBUG`. A plain
`Cmd` leaf under that root uses the root's value; a command that overrides
`_verbose_loglevel_` still wins.

#### Shell completion

Generate a self-contained bash/zsh/fish/PowerShell completion script from your
parser — **static** generation (no runtime dependency, no per-keystroke
re-invocation of your program, unlike argcomplete):

<!-- runnable -->
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

```powershell
# PowerShell: emit and dot-source (add to your $PROFILE to persist)
python app.py --print-completion powershell | Out-String | Invoke-Expression
```

`_completion_` is off by default (matches the `_version_` opt-in precedent) —
set it to add the `--print-completion {bash,zsh,fish,powershell}` flag, which
registers the emitted script under the [application's
name](https://github.com/jose-pr/duho/#the-applications-name). You can also generate a script without adding
the flag at all, via the standalone function:

```python
import sys
import duho

duho.print_completion(MyApp, "bash", file=sys.stdout, prog="myapp")
```

`print_completion`'s keyword-only `prog=` overrides the command name the script
binds to; by default it is the application's name, so the script is the same
whether it is generated from the app itself or from a separate build script.
Pass `prog=` when the command users type differs from that name.

A third way is a subcommand: `_completion_command_ = True` on a `Cli` root
registers `completion` (a string names it instead), which takes the shell as its one
argument and prints that shell's script to stdout (`myapp completion fish`). It
needs another subcommand and a free name (else `ValueError`), is never an MCP tool,
and works under both `duho.main` and `duho.app`; `--print-completion` is unchanged.

Both paths walk the built parser tree, including nested `_subcommands_`:
`Literal`/`Enum` fields offer their choices as completion candidates, and
`pathlib.Path`-typed fields get the shell's native file/directory
completion. The PowerShell emitter registers a `Register-ArgumentCompleter
-Native` script block resolving the subcommand path to its flags/choices, with
file completion falling through to PowerShell's defaults.

#### Agent help (machine-readable `--help`)

Alongside the compact, human-facing `--help`, duho can emit a **complete,
machine-readable JSON description** of your CLI — enough for an AI agent (or any
tool) to understand the whole command surface in one shot: every subcommand,
each option's type/default/required/repeatable/choices, positionals, per-field
env-var bindings, mutually-exclusive conflict groups, examples, and exit codes.
It is built entirely on duho's existing introspection — no second declaration.

Two triggers:

```bash
# 1) Always on, zero-config: set AGENT_HELP and --help emits the agent document.
#    Human --help is byte-identical when AGENT_HELP is unset.
AGENT_HELP=1 python app.py --help
AGENT_HELP=1 python app.py deploy --help    # scoped to the subcommand

# 2) Opt-in discoverable flag (set _agent_help_ = True on the root):
python app.py --help-agents
```

<!-- runnable -->
```python
import duho

class MyApp(duho.Cli):
    """My app."""
    _version_ = "1.2.3"
    _agent_help_ = True                       # adds --help-agents (opt-in)
    _agent_help_env_ = "AGENT_HELP"           # env trigger name (default shown)
    _examples_ = [("myapp deploy --env prod", "Deploy to prod")]
    _exit_codes_ = {3: "Partial failure."}    # merged over duho's 0/1/2 defaults
```

The document is tagged with a `schema` (`duho/agent-help@1`) so consumers can
detect and pin the shape. Each option carries `type`, `default`, `required`,
`repeatable`, `choices`, `metavar`, and — when declared — its `env` binding and
`conflicts` group; subcommands nest recursively with their `aliases`. You can
also produce it directly, independent of either trigger:

```python
import duho

duho.print_agent_help(MyApp)                  # JSON to stdout
spec = duho.agenthelp.describe(MyApp)         # the document as a dict
```

The `AGENT_HELP` env trigger is safe to leave always-on: it changes `--help`
behavior only when the variable is deliberately set, so nothing changes for
ordinary human use. Set `_agent_help_env_` to rename the trigger per-app.

**No secrets in agent help.** Neither the agent-help JSON nor human `--help`
ever renders a field's *live* env/config-sourced value as its default — that
would print a secret straight from the flagship `NS(env="DEPLOY_TOKEN")`
example. Both instead show the field's **declared class default**, plus, only
when the value actually came from an env var or a config file, a value-free
provenance note in its place: `"default_source": "env DEPLOY_TOKEN"` in the
JSON document, `(from env DEPLOY_TOKEN)` appended to the option's help in
human `--help`. This covers a module command's own env/config-bound fields
too, not just declarative ones. A default is published (agent help,
MCP tool list) only for fields declared with duho; an option a module command's
`register` hook adds to the parser directly carries none, so a value such a hook reads
from the environment is never exposed.

#### Manual subparsers

`_subcommands_` (above) is the recommended way to build command trees. If you
need to attach duho commands to a parser you build yourself, pass the
subparsers action to `_parser_`:

<!-- runnable -->
```python
import argparse
import duho
from duho import Cmd

class Serve(Cmd):
    """Start the development server."""
    port: int = 8000

    def __call__(self):
        print(f"serving on {self.port}")

root = argparse.ArgumentParser()
subparsers = root.add_subparsers()
Serve._parser_(subparsers, name="serve")

args = root.parse_args()
```

A plain `root.parse_args()` returns a raw `Namespace` still carrying duho's
internal subcommand-selection marker, not a real `Serve` instance — call
`duho.finish_parse(args)` to get the genuine instance the recipe was always
meant to produce (methods, `_passthrough_`, and all):

```python
cmd = duho.finish_parse(args)   # -> a real Serve instance
raise SystemExit(cmd())
```

### Commands: Args vs Cmd

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

def run(args):
    print(f"Hello, {args.name}!")

GreetCmd = duho.command(Greet, run, name="greet")
raise SystemExit(duho.main(GreetCmd))
```

`LoggingArgs` stays a data mixin; combine it as `class App(LoggingArgs, Cmd)`
(recommended base order — data mixin first, executable base last) to get logging
plus a runnable command.

### Cli: the application root

A leaf `Cmd` is lean — it declares its own flags and a `__call__`. The **root** of
a multi-command app usually wants more: a `--version` flag, shell completion, a
config file, a subcommand tree. `duho.Cli` is an **opt-in** mixin over `Cmd` that
gives those a typed home. Subclass `Cli` for your app root; keep leaf commands as
plain `Cmd`:

<!-- runnable -->
```python
import duho
from duho import Cli, LoggingArgs

class MyApp(LoggingArgs, Cli):     # data mixin first, root base last
    """My multi-command app."""
    _version_ = "1.2.3"            # adds --version
    _completion_ = True            # adds --print-completion {bash,zsh,fish,powershell}
    _config_ = "myapp.toml"        # layered config-file defaults
```

`Cli` is purely additive: it adds **no** new runtime behavior for *running* (it
inherits `Cmd.__call__` unchanged), and a plain `Cmd` root still works everywhere
`Cli` does — `Cli` just types and documents the app-root attributes (`_version_`,
`_distribution_`, `_completion_`, `_config_`, `_subcommands_`), all sandwich-named
so your CLI-field namespace stays 100% yours. `LoggingArgs` stays orthogonal — mix
it in when you want `-v`/`-q` verbosity, leave it out when you don't.

#### Self-registration: `@MyApp.subcommand`

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
appears once). `MyApp._register_subcmd_(Deploy)` is the non-decorator form, and
`@duho.subcommand(parent)` is the decorator for any `Cmd` group, not only a `Cli`
(`@duho.subcommand(Tools)` on `class Build(duho.Cmd)`). Once the
command files are imported, `duho.main(MyApp)` sees the full tree (use `duho.app` if
you also want discovery/config/env — see [main vs app](https://github.com/jose-pr/duho/#run-your-app)).

#### A default subcommand: `_default_subcommand_`

A group can name the subcommand to use when the user leaves it out. With
`_default_subcommand_ = "resolve"`, `tool -v bob` runs `tool -v resolve bob`: duho
skips the group's own options (and one value for an option that takes one) and, if
the first other token is not a subcommand name or alias, inserts the default name
before it. The subcommand stays required when there is no such token, and when a
`--`, an unregistered option, a variable-arity option or a missing value comes first,
so `tool --unknown bob` is still an error. An unknown name, or the attribute on a
class with no subcommands, raises `ValueError` naming the class when the parser is
built. It works on a nested group too.

```python
class Tool(duho.Cli):
    _default_subcommand_ = "resolve"
    _subcommands_ = [Resolve, Fetch]
```

#### App-wide config & env with `duho.app`

`duho.app(root, ...)` threads a `Cli` root's `_config_` and any `env` down to the
dispatched subcommand. TOML top-level keys apply to the root's fields; a
`[<subcommand>]` table (its kebab-case name, e.g. `[deploy]`) applies to that
subcommand; and the resolved `duho.Env` (if
passed) is reachable from the command as `self._env_`:

```python
# app.toml
# [deploy]
# region = "eu-west"

raise SystemExit(duho.app(MyApp, source="myapp.commands",
                          env=duho.Env("myapp")))
# `myapp deploy` now defaults region to "eu-west" (CLI still overrides),
# and Deploy.__call__ can read self._env_ for app-wide settings.
```

Pass `config="other.toml"` to `duho.app` to override the root's `_config_` for one
run. Precedence is unchanged: CLI > env > config > class default.

### Environment access

`duho.Env(prefix)` is an app-wide, typed view over the environment variables
sharing a common prefix. The prefix is uppercased with `-`→`_` and a trailing `_`
ensured, so `Env("my-app")` reads `MY_APP_*` keys:

<!-- runnable -->
```python
from pathlib import Path
import duho

env = duho.Env("my-app")           # reads MY_APP_* from os.environ
debug = env.bool("DEBUG")          # MY_APP_DEBUG -> True for 1/true/yes/y/t
paths = env.paths("CMDS_PATH", ty=Path)  # MY_APP_CMDS_PATH split on os.pathsep into Paths
```

`Env` is a `MutableMapping`, so `env["KEY"]`, `env.get(...)`, `in`, and iteration
all work. `.bool(key)` treats a missing key as `False`. `.list(key, sep=":",
ty=str)` splits on a caller-supplied separator (default `:`) and applies `ty` to
each part — the right choice for a generic delimited value. `.paths(key, ty=str)`
is specifically for an OS path list (`CMDS_PATH` and friends): it splits on
`os.pathsep` (`;` on Windows, `:` on POSIX, so a Windows drive letter is never
mis-split) and drops empty segments before `ty` ever sees them, rather than
treating one as the current directory. Both return `[]` — never `[ty("")]` — for
a missing or empty value. On construction `Env` also autoloads an optional
companion `<prefix>env` module of defaults an app may ship (e.g. `my_app_env`),
seeding its **upper-case, non-underscore** variables (all `str()`-coerced); a
missing one is ignored. Pass `Env(prefix, autoload=False)` to disable the import
— autoload imports `<prefix>env` from anywhere on `sys.path` (including the CWD),
so disable it if the prefix is not fully under your control. This is distinct from
the per-field `NS(env="VAR")` default layer above — that resolves one argparse
field; `Env` is the app-level accessor a driver reads settings through.

### String/target expansion

`duho.expand` expands `[a-b]` brace ranges into concrete strings — handy for
turning a host pattern into a target list. Output is **not** zero-padded:

<!-- runnable -->
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

A `:spec` suffix inside the brackets is a `str.format` spec applied to each member,
which is how to pad:

<!-- runnable -->
```python
import duho

list(duho.expand("host[1-3:02d]"))
# ['host01', 'host02', 'host03']
```

A spec containing braces, or one a member rejects (`rack[A-C:02d]`), raises
`ValueError`.

Companion helpers `duho.pysafe` (coerce text to a Python-safe dotted identifier),
`duho.snakecase`/`duho.camelcase`/`duho.kebabcase` (case conversion — `kebabcase`
is acronym-aware and is the rule behind a class-derived command name and a
field's default flag, both above), and `duho.gettext` (a `gettext` shim) round
out the text utilities.

### Dynamic command discovery

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

<!-- runnable -->
```python
# myapp/cmds/deploy.py
import duho

class Deploy(duho.Cmd):
    """Deploy the application."""
    name: str
    def __call__(self):
        print("deployed", self.name)
```

<!-- runnable -->
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

The subcommand name is the class's `_parsername_`, or the **kebab-case of its
class name** when it declares none (`BuildPyz` → `build-pyz`), for class
commands; and the file **stem with `_`→`-`** for module commands
(`deploy_all.py` → `deploy-all`; override with a module-level `_parsername_`).
You can
also call `duho.discover_commands(source)` directly to get the `list[Command]`;
read each one's name with `duho.parsers.command_name(command)` (a discovered class
command has `_parsername_` only if it declares one).

Discovery is **resilient**: a command that can't be imported (a missing optional
dependency → `ImportError`) or isn't actually a command (`NotImplementedError`) is
logged with a warning and skipped, so one broken command never takes down the rest.
A genuine bug in a command file (e.g. a `SyntaxError`) is *not* swallowed — it
surfaces so you can fix it.

**Several sources, your own error policy.** `source=` (and `discover_commands`)
takes a list or tuple: each source is discovered on its own, a command of the same
name in a later source replaces the earlier, and the result is sorted by name.
`on_error(source, exc)` replaces the rule above: it is called for any exception
(`SystemExit` too) raised importing one file (`source` is its `Path`), importing a
package module (the dotted name), or building a command; returning skips it,
raising aborts. `duho.app` hands the same `on_error` to discovery and also calls it,
with `(command, exc)`, when building one command's parser or running its `register`
hook fails; that command is dropped. `providers=True` also offers the source
directory and each of its child directories (not starting `_` or `.`) to the
registered command providers, off by default.

```python
raise SystemExit(duho.app(CLI, source=[BASE, SITE],
                          on_error=lambda source, exc: log.warning("skipped %s", source)))
```

**Module-command entrypoints.** `ModuleCommand.entrypoint` is the resolved
callable. `duho.app(adapter=...)` (and `duho.run_command(adapter=...)`) calls
`adapter(entrypoint)` and runs what it returns, with the parsed instance, in place of
the entrypoint, so an app can accept entrypoint signatures of its own; a falsy
return keeps the entrypoint, and it is never applied to `init`/`success`/`finally_`
or to a class command. Combining `adapter=` with `dispatch=` raises `ValueError`;
a custom `dispatch` passes `adapter` to `run_command` itself.
`duho.runtime.accepts_positional(func, count)` reports whether `func` takes at least
`count` positional parameters, for adapting by arity.

**What gets scanned.** A bare string source that names a directory without
`__init__.py` is scanned like a path, as loose files that may import each other. A
command file imports a sibling helper (`import _helpers`, also inside a function);
the helper is one module per directory, shared by its command files, and is not put
on `sys.path`. The standard library and installed packages win over a same-named
file. A launcher script that lives in the scanned directory is not registered as a
command, and a file with an upper-case `.PY` suffix is ignored. A module whose `main`
is a decorator-wrapped function defined elsewhere is not a command; discovery logs a
warning on `duho.discovery` naming it, and listing the name in the module's
`__all__` accepts it.

**No commands.** When nothing resolves (no `commands=`, `source=`, `entry_points=`,
`CMDS_PATH` or root `_subcommands_`), `duho.app` runs the root if it is a `Cmd`, and
otherwise exits 2 with a "no commands are available" message.

#### Plugins via entry points

For commands that ship in **separately-installed packages**, point `duho.app` at
an entry-point **group** instead of a local package. Every entry point advertised
in that group by any installed distribution becomes a subcommand — so a third-party
plugin can extend your app without your app importing it directly:

```python
# your app
raise SystemExit(duho.app(CLI, entry_points="myapp.commands"))
```

```toml
# a plugin package's pyproject.toml
[project.entry-points."myapp.commands"]
hello = "myapp_hello.plugin:HelloCmd"   # a Cmd subclass -> class command
bye   = "myapp_hello.bye"               # a module with main() -> module command
```

An entry point may resolve to a `Cmd` subclass (class command) or a command module
(module command); it is coerced through the same path as every other source. Loading
is **resilient** in the same spirit — a plugin that fails to import or does not resolve
to a command is logged and skipped, so one bad plugin never takes the app down.
`importlib.metadata` is imported **lazily**, so an app that does not use
`entry_points=` never pays its import cost. Call `duho.discover_entry_points(group)`
directly to get the `list[Command]`.

### RunPath: ordered step commands (opt-in)

`duho.runpath` is an **opt-in** module that turns a directory of numbered `.py`
files into a single command that runs them **in order**. It plugs into the
discovery provider hook above and needs no core changes — core `duho` never
imports it; you activate it explicitly:

<!-- runnable -->
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

<!-- runnable -->
```python
# 10-build.py — a step's body is its top-level main/run/call (same precedence
# as module commands). It receives the parsed command instance.
def main(args):
    args._logger_.info("building")
```

#### Inheriting your app's shared root (`register(base=...)`)

`args._logger_` above works out of the box: by default every RunPath command
this module builds ALSO inherits `duho.LoggingArgs` (alongside `RunPathCmd`
itself), so `-v`/`_logger_`/`_set_loglevels_` are real inherited methods —
not just data fields copied onto the parsed instance by `app()`'s `parents=`
mechanism (which only ever copies *data*, never *methods*). If your app's
shared root is a custom `LoggingArgs` subclass carrying its own methods, call
`register(base=MyAppRoot)` once, early, so every RunPath command your app
builds inherits those methods too:

<!-- runnable -->
```python
import duho.runpath

class MyAppRoot(duho.LoggingArgs):
    def greet(self):
        return f"hi, {self.label}"

duho.runpath.register(base=MyAppRoot)   # before building/running any RunPath command
```

#### Giving steps your app's own signature (`register(step_adapter=...)`)

Steps are called `main(cmd)` or `main(cmd, ctx)`. If your app's *module*
commands take a different shape — say `run(client, args, logger)` — steps and
commands disagree, and the same body cannot move between them.

`step_adapter` is a callable applied to each step's entrypoint just before it
runs; it receives the entrypoint and returns the callable to call instead. That
makes the step signature an app-wide convention rather than something every
step file has to opt into with a decorator:

```python
def adapter(entrypoint):
    def call(cmd, ctx=None):
        return entrypoint(ctx, cmd, cmd._logger_)   # (client, args, logger)
    return call

duho.runpath.register(base=MyAppRoot, step_adapter=adapter)
```

The *adapted* callable is what arity detection inspects, so a wrapper is free
to change the signature. An adapter that returns its argument unchanged leaves
duho-native steps alone, which is how an app supports both shapes at once.

Pass `None` to clear it; omit the argument to leave it unchanged. Unlike
`base`, it takes effect immediately for every RunPath command in the process —
it is consulted per step run, not at class-build time. The default is `None`:
steps are called exactly as written.

Each step is named after the part of the filename after `NN-`; the numeric prefix
is its ordering key. A step module may override ordering and declare dependencies:

- `PRIORITY: int` — overrides the `NN` prefix for ordering.
- `REQUIRED: list[str]` — a **hard** dependency: names of steps that must run and
  succeed **before** this one. A missing or disabled `REQUIRED` name is a warning
  (or, under a run-wide `strict`, an error) — see "Strict vs. resilient" below.
- `BEFORE: list[str]` / `AFTER: list[str]` — **soft** ordering only (no
  existence/success requirement), styled after systemd's `Before=`/`After=`:
  `BEFORE = ["x"]` on a step means "I run before `x`, if `x` is present and
  enabled"; `AFTER = ["x"]` means "I run after `x`, if `x` runs" — the mirror
  direction. A `BEFORE`/`AFTER` name that's missing, or present but disabled, is
  silently a no-op for ordering (never a warning — contrast with `REQUIRED`
  above). `REQUIRED`'s hardness is independent of `BEFORE`/`AFTER` and of a
  step's own filename modifiers (below): all three are separate axes.

#### The optional `__main__.py` lifecycle

A RunPath directory may define a `__main__.py` file — the same dunder Python
already uses for "this directory's entrypoint" (as in `python -m package`), no
new naming convention invented. Its own leading `_` already excludes it from
step discovery. It defines up to three optional callables:

<!-- runnable -->
```python
# __main__.py — runs once per invocation, before any step
def init(cmd, logger):
    return connect_once()          # ctx handed to every 2-arg step

def success(ctx, cmd, logger):
    logger.info("all steps completed cleanly")

def finally_(ctx, cmd, logger):
    ctx.close()                    # always runs, success or failure
```

<!-- runnable -->
```python
# 20-provision.py — a step opting into ctx just adds a 2nd parameter
def main(cmd, ctx):
    ctx.provision()
```

A step written `(cmd)` (the original shape) is unaffected — arity is detected
automatically, so old and new steps coexist in the same directory. `init`
raising is **always fatal**, regardless of `--rcopts strict` — every step
depends on `ctx`, so there is no meaningful partial/resilient init. A directory
with no `__main__.py` behaves exactly as before this lifecycle existed.

#### Filename-encoded per-step options

Before a step file's `NN-name` prefix is parsed, its stem is checked for a
leading `!` and `:`/`;`-separated option tokens (both stripped first, so
`!02-provision:key.py` still yields prefix `02`, name `provision`):

- a leading `!` **disables** the step by default — `!02-provision.py`;
- everything after that is a list of `key` / `!key` / `key=value` tokens,
  separated by `:` **or** `;` interchangeably — both work identically
  everywhere (not an OS-conditional split), so a Windows-authored filename can
  use `;` (`:` is invalid in a Windows filename; `;` is valid on both Windows
  and POSIX). Two tokens are recognized specially:
  - `strict` / `!strict` — a step's own default (no token) is **strict**;
    `!strict` opts that ONE step OUT of strict — `03-cleanup;!strict.py` logs
    and continues past a failure in `cleanup` even while every other
    plain-named step is fatal;
  - `enable` / `!enable` — an explicit alternative to the leading `!`:
    `!step1.py` and `step1;!enable.py` disable the same step. If BOTH the
    leading `!` and an explicit token are present, the **token wins** (more
    specific than the whole-name shorthand).
  - any other token is collected for forward compatibility (not yet consumed
    by anything).

This is the exact same token grammar `--rcopts` uses per comma-entry (below) —
one parser, not two.

Precedence for a step's effective strict setting: the step's own filename
default (strict, absent `!strict`), then a per-pattern `--rcopts` `!strict`
token matching that step, then an EXPLICIT bare `--rcopts strict`/`!strict`
(no pattern attached — the run-wide toggle), which wins last of all. This is
symlink-transparent by construction: two symlinks (or copies) pointing at the
same physical step file, named differently in two RunPath directories, get
different effective enabled/strict defaults, because the parse reads the
directory entry's own name, never the target file's content.

```
01-step1.py                       # enabled, strict (defaults)
!02-step2.py                      # disabled by default
02-step2;!enable.py              # same as above, explicit-token spelling
03-cleanup;!strict.py             # enabled, non-strict for this ONE step
04-report;key=daily;!strict.py    # extra key=value token, also non-strict
```

Once `duho.runpath` is imported, pointing at the directory yields a run-path
command:

```python
import duho, duho.runpath
from pathlib import Path

cmd = duho.CmdBuilder("release", Path("release")).command
raise SystemExit(cmd()())   # build → test → publish, in order
```

#### Selecting steps with `--rcopts` (`-O`)

`--rcopts` takes a comma-separated list of entries, each an [fnmatch] pattern
matched against step names, optionally followed by `:`/`;`-separated option
tokens — the exact same grammar (and `strict`/`enable` special tokens) a
step's own filename uses, above:

- a leading `!` **disables** matching steps — `!*` disables everything, so
  `--rcopts '!*,test'` means "run only `test`"; `test:!enable` is an
  equivalent, more explicit spelling of `!test`;
- a bare entry that is exactly `strict`/`!strict` (no pattern) opts into
  **run-wide strict mode** (see below);
- an entry with a pattern AND a `strict`/`!strict` token (e.g.
  `build:!strict`) scopes that override to steps matching `build` only,
  without touching the run-wide flag or any other step.

Later entries win when several match the same step, so `!*,build-*` disables
all then re-enables everything matching `build-*`.

```
--rcopts '!*,test'                 # run only test
--rcopts 'test:!enable'           # same effect as --rcopts '!test'
--rcopts 'build:!strict'           # build's own failure is resilient;
                                    #   every other step's strict handling
                                    #   is untouched
--rcopts 'strict'                  # run-wide: overrides every step's own
                                    #   filename-derived strict setting
```

#### Strict vs. resilient

A step is **strict** by default: a step whose body raises (or returns a non-zero
code) stops the run at that step with its traceback, and later steps never run.
What stays a **warning** by default is selection, not execution:

- an `--rcopts` pattern that matches no step is logged as a warning;
- a `REQUIRED` name that is missing or disabled is logged as a warning.

`!strict` makes a step **resilient**: its failure is logged (`step test failed:
...`) and the run continues with the next step. Spell it in the step's filename
(`03-cleanup;!strict.py`) or for matching steps with `--rcopts 'build:!strict'`;
it applies to that one step even in an otherwise-strict run (see
"Filename-encoded per-step options" above).

Passing a bare `strict`/`!strict` in `--rcopts` (e.g. `--rcopts 'strict'`) sets
the policy **run-wide**: with `strict`, an unmatched pattern raises, a `REQUIRED`
dependency naming a missing step raises, and — because it's the EXPLICIT run-wide
token — it overrides every step's own filename-derived (or per-pattern `--rcopts`)
strict setting, uniformly. With `!strict`, every step is resilient. The bare
run-wide token wins last of all.
The module's public API is `duho.runpath.RunPathCmd`, `register()`, and
`unregister()` (`register`/`unregister` give explicit control over the provider —
`unregister()` is what tests use to keep provider state from leaking). These are
deliberately **not** on the top-level `duho.*` surface — RunPath is opt-in.

[fnmatch]: https://docs.python.org/3/library/fnmatch.html

### Module commands & lifecycle

A **module command** is a plain `.py` file. Its entrypoint is `main` (preferred),
falling back to `run` or `call`, and receives the parsed args instance:

<!-- runnable -->
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

### Customizing a subcommand parser

A module command's optional `register` hook hands you the raw argparse subparser
so you can add arguments the declarative layer doesn't cover. It may be written
either **2-arg** `register(parser, args)` or **3-arg**
`register(parser, args, logger)` — duho inspects your hook's signature and calls
the form you declared:

<!-- runnable -->
```python
def register(parser, args):                 # 2-arg form
    parser.add_argument("--force", action="store_true")

def main(args):
    if args.force:
        ...
```

<!-- runnable -->
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

The `args` a `register` hook receives is the root instance parsed with its
required options treated as optional (a missing one is unset or its default), or the
root's defaults instance when a global fails to convert; the real parse still
enforces the required options.

Every subcommand parser is built with **parent-arg inheritance** — the root
command's global options (verbosity, etc.) appear on each subcommand automatically
via argparse `parents=`. For a command reached through `commands=`/`source=`/`entry_points=`,
`myapp -v deploy` and `myapp deploy -v` both work; a root's own static
`_subcommands_` tree accepts a global only before the subcommand name
(`myapp -v deploy`).
A required root option on a static tree takes its value before the subcommand
name: after the name the root still reports it missing, and a value given before the
name satisfies a subcommand that inherits the option. A global given on both sides of
the subcommand name does not merge: the later
one wins. For a counting option that means `myapp -v deploy -v` is verbosity 1,
not 2; write `-vv` on one side to count both.

> **Avoid the root's reserved flags in `register`.** Because the subparser already
> carries every root/global option, a `register` hook that adds one of them
> collides. Steer clear of the globals the root contributes — `-h`/`--help`,
> `--version` (if `_version_` is set), and, with a `LoggingArgs` root, `-v`
> (verbose), `-q` (quiet), and `--loglevel`. If you do collide, `duho.app` raises a
> clear error naming your command and the offending flag (rather than argparse's
> bare "conflicting option string"), so pick a different flag.

### Passthrough args

Argv after the first literal `--` separator is captured at parse time and exposed
on the parsed instance as `_passthrough_: list[str]` — useful for forwarding
trailing args to a wrapped tool. Only the first `--` splits; a second `--` is part
of the payload:

```python
class Run(duho.Cmd):
    def __call__(self):
        subprocess.run(["pytest", *self._passthrough_])

# myapp run -- -k test_foo -x   ->   self._passthrough_ == ["-k", "test_foo", "-x"]
```

`--` never acts as argparse's own end-of-options marker in duho — it always
starts the passthrough capture, even for a command that never reads
`_passthrough_` and even when a variadic positional would otherwise happily
accept a value starting with `-`. So a value that genuinely needs to start
with `-` (the POSIX `rm -- -oddfile.txt` idiom) cannot be given after a `--`
to a duho command; it is captured into `_passthrough_` instead, read or not.

A command that has no use for a tail can refuse one: `_allow_passthrough_ = False`
makes a non-empty tail after `--` a usage error naming the command (exit 2), while no
tail, or a bare `--`, still parses. It is read from the command the parse selects, so
a subcommand sets its own.

### Target fan-out (`duho.fanout`, opt-in)

duho dispatches **one** command per run by design. When you need to run that one
command against a list of targets (hosts, environments, datasets) and roll their
exit codes into one, `import duho.fanout` — an opt-in, stdlib-only helper (core
never imports it, and it stays off the top-level `duho.*` surface).

`run_targets(func, targets, *, max_workers=None, aggregate=<worst-by-magnitude>, logger=None, label=None)` runs `func(target)`
for each target concurrently on a thread pool and returns an aggregated exit code
(`None` → `0`, an int as-is, an unhandled exception → logged and treated as `1` so
one failing target never aborts the rest; codes reduced to the worst by magnitude —
`0` only if all succeed, and a negative code such as `-9` (a process killed by a
signal) is not hidden by a succeeding target as `max` would hide it). Log lines a target emits while it runs are tagged with a `[<target>]`
prefix so interleaved concurrent output stays attributable; the prefixing filter is
installed on your existing stderr handler for the duration and removed afterwards.
`label=` is a callable giving the prefix text for a target in place of `str(target)`
(`label=lambda host: host.name`); `func` still receives the target itself, and a
`label` that raises fails only that target (code 1).

<!-- runnable -->
```python
import logging
import duho, duho.fanout

duho.init_stderr_logging(level=logging.INFO)   # so INFO-level log lines are visible

targets = list(duho.expand("web[01-03].example.com"))

def deploy_to(host):
    log = duho.logging.getLogger("duho.deploy")
    log.info("deploying")          # tagged "[web1.example.com] deploying" (see below)
    return 0                       # your per-target work; int/None exit code

raise SystemExit(duho.fanout.run_targets(deploy_to, targets, max_workers=4))
```

Each line above lands on stderr prefixed with its target (the timestamp/level
columns are `duho.init_stderr_logging`'s own formatting, elided here):

```text
[web1.example.com] deploying
[web2.example.com] deploying
[web3.example.com] deploying
```

`fan_out_command(command, make_instance, targets, ...)` is thin sugar for "run one
resolved duho command once per target": you supply `make_instance(target)` (a parsed
instance is app-specific) and each is dispatched via `duho.run_command`. Pass
`aggregate=any` or a custom reducer to change the exit-code policy. You can still
hand-roll a `ThreadPoolExecutor` wrapper if you prefer.

#### Composing `app()`: the `dispatch=` seam

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

### Generating launchers (`duho.scaffold`, opt-in)

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

<!-- runnable -->
```python
from duho.scaffold import generate_launchers

paths = generate_launchers("myapp", ".", libdir="src")   # -> [Path("bin/myapp"), Path("bin/myapp.cmd")]
```

The CLI dogfoods duho itself — `duho.scaffold.ScaffoldCmd` is an ordinary `duho.Cli`
command.

### MCP tool surface (`duho.mcp`, opt-in)

Expose the *same* `Cmd`/`Cli` classes that back a duho CLI as **MCP tools** (the
[Model Context Protocol](https://modelcontextprotocol.io)) — zero redeclaration.
Like `duho.runpath`/`duho.fanout`/`duho.scaffold`, this is an **opt-in, standalone**
module: core `duho` never imports it, it's not on the top-level `duho.*` surface, and
it stays **zero-dependency** — a stdlib JSON-RPC-over-stdio server, no MCP SDK.

```console
$ python -m duho.mcp mypackage.cli:MyApp
```

`<app>` is a dotted qualname to your `Cmd`/`Cli` root class — either
`module.sub:ClassName` (colon syntax, the same convention this project's own
entry-point plugins use) or the legacy dotted `module.sub.ClassName` form; both are
resolved via the stdlib `pkgutil.resolve_name`. The process then speaks
newline-delimited JSON-RPC 2.0 over stdin/stdout — wire it into any MCP client as a
stdio server.

Every `Cmd` reachable through your root's `_subcommands_` tree, recursively,
becomes one tool, named by its command path under the
[application's name](https://github.com/jose-pr/duho/#the-applications-name) (`app.parent.child`; e.g. a root
named `my-app` with a `Deploy` child → `my-app.deploy`). A node whose own
subcommand is mandatory — a root or group that only holds subcommands — is never
listed as a tool: its fields are merged into the input schema of each descendant
instead, and `call_tool` on its own name raises `UnknownToolError` (`serve`
answers JSON-RPC error `-32602`). `serve` supports protocol revisions `2025-11-25`,
`2025-06-18`, `2025-03-26` and `2024-11-05`. Arguments that fail a tool's schema
are a JSON-RPC `-32602` error up to `2025-06-18` and, in a session that negotiated
`2025-11-25`, a tool result with `isError: true` and the message as its text, so the
model can correct them; an unknown tool is always the JSON-RPC error. A tool's
`inputSchema` is a real JSON Schema built from the same field declarations that
already drive your `--help`:

```python
from duho.mcp import describe_tools, call_tool

tools = describe_tools(MyApp)   # -> [{"name", "description", "inputSchema"}, ...]
result = call_tool(MyApp, "my-app.deploy", {"environment": "prod", "replicas": 3})
```

`str`/`int`/`float`/`bool` map to `string`/`integer`/`number`/`boolean`;
`Literal[...]`/`Enum` become a schema `enum` (an `Enum` by member **name**, duho's
standing convention); `list[T]`/`set[T]`/`tuple[T, ...]` become an `array` (a `set`
additionally gets `uniqueItems: true`); `dict[str, V]` becomes an `object` with
`additionalProperties`; `Optional[T]`/`Union[...]` drop out of `required` (a single
non-`None` member unwraps directly, several become `anyOf`); `pathlib.Path` is a
plain `string`.

**Calling a tool** synthesizes an argv from the JSON `arguments` — a repeatable field
becomes a repeated flag, a `dict` field becomes repeated `KEY=VALUE` tokens, a
positional a bare token — and reuses your class's own parser + `duho.run_command` to
dispatch, so every bit of argparse coercion/validation you already rely on runs
unchanged. **Return convention** (the one new contract this module adds): a command
returning `None`/`0` is a success result with your captured stdout as one `text`
content block; a non-zero return is `isError: true` (stdout + a trailing
`exit code: N` line); a JSON-serialisable object/list return is passed through as one
`text` block holding its JSON dump — this is additive, existing int/`None` commands
keep working exactly as before. A command that wants to say more returns a
`duho.Result` (`text`/`value` for the client, `is_error` to choose), or raises a
`duho.CommandError` (an `isError` result carrying its message, with no `Type:`
prefix); a root's `_exit_codes_` adds ` (meaning)` to the `exit code: N` line.

**v1 limitations** (documented, not silently wrong): a custom `action=`/`type=`
field with no registered override is passed through as a plain string; an
`NS(conflicts=...)` exclusive group is noted in the tool's description text only (no
`oneOf`/`not` JSON Schema encoding yet); it's strictly one request → one result, no
streaming/long-running commands. See [`examples/mcp_app.py`](https://github.com/jose-pr/duho/blob/main/examples/mcp_app.py) for
a runnable app plus a note on wiring it into an MCP client.

#### Serving a full `duho.app()` tree

A `duho.app()`-built tree — class AND module commands, from discovered files,
`CMDS_PATH`, entry points, or an explicit `commands=` list — is served too, not just a
class's static `_subcommands_`. That happens through the launch variable or the
`_mcp_command_` subcommand below, which hand the server the tree `app()` built; the
server core they pass is internal and is not something to construct yourself (pass
`describe_tools`/`call_tool`/`serve` a `Cmd`/`Cli` class). A module command with its
own declared `Args` (or none at all) is listed **and callable**, exactly like a class
command, through the same one dispatch path and the same security checks.

A module command whose `register` hook adds its own subparsers is listed as one tool
and can be called: choose the subparser through a property named after the subparsers'
`dest`. The hand-made subparsers are not tools of their own, and their own options are
not served.

#### Launching a server from the CLI itself

No MCP-specific code is required to make an existing CLI servable: every
`duho.main(cls)`/`duho.app(...)` call checks a launch-trigger environment variable
*first*, before parsing `argv`:

```console
$ MYAPP_MCP=stdio myapp
```

The variable name is `<PREFIX>MCP` when the app supplies an `Env` (`app(env=Env
("myapp"))` → `MYAPP_MCP`), else `<NAME>_MCP` derived from the [application's
name](https://github.com/jose-pr/duho/#the-applications-name) (upper-cased, every character outside `[A-Z0-9]`
replaced by `_`) — e.g. an app named `some-app` → `SOME_APP_MCP`. Set to `stdio`, it serves the
app's full tool tree over stdio instead of running any command; set to anything
else, the process exits `2` naming the unsupported transport. The variable is
always removed from `os.environ` the moment it's seen — present or not — so a
served command's own child processes never inherit it. This is **on by default**;
disable it with a root class attribute `_mcp_ = False` (on a `Cli`) or
`app(..., mcp=False)`. The variable takes over stdio before the app is built, so anything printed while
commands are discovered or registered (module imports, `register` hooks) goes to
stderr, not the protocol stream.

Prefer a real subcommand instead of an env var? `duho.mcp.McpCmd` is a ready `Cmd`
(`--transport {stdio}`) that serves the CLI it was dispatched from — register it
under any name, or let the class attribute do it for you under **either**
`duho.main` or `duho.app`:

```python
class MyApp(Cli):
    _mcp_command_ = True  # adds "myapp mcp" (a str instead names it explicitly)
    _subcommands_ = [Deploy, Rollback]
```

```console
$ myapp mcp
```

`app(..., mcp_command=...)` overrides the class attribute per call (`duho.main` has
no such kwarg — it always reads `_mcp_command_` directly). Either entry point
requires the app to already have at least one OTHER subcommand, and a chosen name
that collides with an existing command/alias is a build-time error — both checked
before anything is registered, never silently swallowed. With the subcommand the app is already
built when the server starts, so output printed during that build reaches stdout ahead
of the first reply.

#### Naming and identity

The dotted tool-name namespace, and the `serverInfo` an MCP client sees in its
`initialize` response, both follow the [application's
name](https://github.com/jose-pr/duho/#the-applications-name):

```python
app(Dotagents, name="dotagents")   # tools come out "dotagents.*"
app(Dotagents)                     # the root's `_parsername_`, else its package
```

`serverInfo.version` reports the served app's own `_version_` when it resolves to a
string (a literal, `duho.AUTO`, or a class-level `__version__` fallback — see
"Version flag" above); otherwise it reports the empty string. duho's own version is
never reported as the served app's.

#### Excluding a command from the tool surface

A command that should never be reachable over MCP — one that prints secrets, or
returns something a client has no business asking for — opts out with
`_mcp_ = False` on that command's own class:

```python
class Env(Cmd):
    """Print resolved environment values (may include secrets)."""

    _mcp_ = False

    def __call__(self):
        ...
```

The excluded command, and its whole subtree if it has one (a `Cli` with its own
`_subcommands_`), is left out of `tools/list` entirely; calling it by name (or
anything nested under it) fails exactly like an unknown tool name — no existence is
disclosed either way. A subclass of an excluded command inherits the exclusion
without redeclaring it (plain attribute lookup). A **module command** gets the same
opt-out via a module-level `_mcp_ = False`:

<!-- runnable -->
```python
# secrets.py
"""Print resolved secrets."""

_mcp_ = False


def main(args=None):
    ...
```

`_mcp_` on the ROOT of the tree being served keeps its separate, pre-existing
meaning (the launch-trigger opt-out above) — it is never treated as this
per-command exclusion, and `duho.mcp.McpCmd`'s own registered subcommand is always
excluded regardless of this attribute.

### Testing a command line (`duho.testing`, opt-in)

`duho.testing.invoke(root, argv=(), *, env=None, stdin=None, **app_kwargs)` runs a
command line in-process and returns `Result(status, stdout, stderr)`. With no
`app_kwargs` it is `duho.main(root, argv)`; with any (`source=`, `name=`, ...) it is
`duho.app(root, argv=argv, **app_kwargs)`. `SystemExit` becomes `status` (`None` is
0; text passed to it goes to `stderr` and the status is 1), a returned `None` is 0,
`env` is applied to `os.environ` for the call and restored, `stdin` is the text the
command reads, and any other exception propagates.

<!-- runnable -->
```python
import duho
from duho.testing import invoke

class Greet(duho.Cmd):
    name: str = "world"
    "Who to greet"

    def __call__(self):
        print(f"hello {self.name}")
        return 3

result = invoke(Greet, ["--name", "x"])
assert (result.status, result.stdout) == (3, "hello x\n")
assert invoke(Greet, ["--nope"]).status == 2
```

### Examples

[`examples/`](https://github.com/jose-pr/duho/tree/main/examples) holds five runnable apps. Two are
self-contained CLIs that each build a small umbrella app with an `install`
subcommand, ported from real-world scripts to show duho's full surface (they stub
the actual filesystem work — the point is the CLI); the other three show
`duho.app(source=...)` discovery (`discovery_app.py`), RunPath step directories
(`runpath_app.py`) and the MCP tool surface (`mcp_app.py`):

- [`examples/dotagents.py`](https://github.com/jose-pr/duho/blob/main/examples/dotagents.py) — an agent-config installer
  (`LoggingArgs`, `_subcommands_`, `--dest`/`--dry-run`/`--with-examples`):

  ```python
  class Install(LoggingArgs, Cmd):
      """Copy the agent-config payload into the destination directory."""

      _parsername_ = "install"

      dest: Path = Path.home() / ".agents"
      dry_run: bool = False

  # ...
  if __name__ == "__main__":
      sys.exit(duho.main(Dotagents))
  ```

  ```bash
  python examples/dotagents.py install --dry-run
  ```

- [`examples/fileinstall.py`](https://github.com/jose-pr/duho/blob/main/examples/fileinstall.py) — an `install(1)`-like file
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

## Development

Contributions welcome! See [CONTRIBUTING.md](https://github.com/jose-pr/duho/blob/main/CONTRIBUTING.md) for guidelines.

```bash
git clone https://github.com/jose-pr/duho.git
cd duho
pip install -e ".[dev,colorama,config]"
pytest
python -m black --check src tests examples benchmarks
```

`pip install -e ".[dev,docs,colorama,config]"` adds the documentation tooling;
`mkdocs build --strict` builds the site as CI does. Run the suite on the oldest
supported Python (3.9) as well as the newest.

### Releasing

A release is a `v*` tag, pushed by a maintainer. The version is written in
`pyproject.toml` and in `src/duho/__init__.py` (a test fails if they differ), the
change is described in `CHANGELOG.md`, and the Release workflow tests, builds,
verifies the wheel and publishes to PyPI. See
[CONTRIBUTING.md](https://github.com/jose-pr/duho/blob/main/CONTRIBUTING.md#releasing).

## License

MIT License. See [LICENSE](https://github.com/jose-pr/duho/blob/main/LICENSE) for details.
