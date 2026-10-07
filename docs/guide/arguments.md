# Declaring arguments

A duho CLI is a class. Each annotated field becomes an argument.

<!-- runnable -->
```python
from duho import Args

class Deploy(Args):
    """Deploy the application."""

    environment: str
    "Target environment (prod, staging, dev)"
    ("--env", "-e")

    dry_run: bool = False
    "Preview changes without applying them"
```

Three things make up a field:

| Part | Purpose |
| --- | --- |
| The **annotation** (`environment: str`) | The type. Drives conversion and validation — see [Types](types.md). |
| The **docstring** below it | The argument's `help` text. Optional. |
| The **tuple literal** below that | The flags. Optional. |

The class docstring becomes the parser's description.

The help is the first run of consecutive string literals after the field, joined with
one space, and it may stand before or after the flag tuple:

<!-- runnable -->
```python
from duho import Args

class Serve(Args):
    port: int = 8000
    "Port to listen on,"
    "default 8000"          # -> help "Port to listen on, default 8000"
    ("--port", "-p")
```

A one-element flag tuple written without its comma (`("--port")` is just the string
`"--port"`) would become help text, so a lone flag-shaped string raises a
`ValueError` at build time that shows the fix: `("--port",)`.

## Flags are optional

With no tuple literal, the flag defaults to one long flag,
`"--" + kebabcase(field_name)` — underscores become dashes, and a camelCase or
ACRONYM name is kebab-cased too (`dry_run` → `--dry-run`, `testMe` →
`--test-me`, `HTTPPort` → `--http-port`). Reach for an explicit tuple only for:

- a **positional** — `("source",)`
- a **short flag or extra aliases** — `("-n", "--name")`
- a **different spelling** — `("--env",)` for a field named `environment`

A bare `"--"` entry inside a tuple expands to that same default long flag, so
you can pair a short flag with it without spelling it out:
`("-n", "--")` → `("-n", "--name")`.

```python
class Build(Args):
    """Build the project."""

    workers: int = 4          # -> --workers
    "How many parallel workers"

    dry_run: bool = False     # -> --dry-run
```

This also works when a field has a docstring but no tuple — the docstring is
recognized as help text, not as flags.

## Required vs optional

A field **without** a default is required; a field **with** one is not.

```python
class Deploy(Args):
    environment: str          # required
    ("--env",)

    version: str = "latest"   # optional
```

`Optional[T]` fields are never required (they default to `None` if you don't give
them a default).

Any value supplied by a [configuration layer](config.md) — an environment variable
or config file — also un-requires the field.

## Positional arguments

A tuple whose single entry has **no leading dash** declares a positional:

```python
class Copy(Args):
    """Copy SOURCE to DESTINATION."""

    source: Path
    "File to copy"
    ("source",)

    destination: Path
    "Where to put it"
    ("destination",)
```

Positionals are matched in declaration order. A positional **with a default**
becomes optional (duho gives it `nargs="?"`):

```python
    output: str = "-"
    "Where to write (default: stdout)"
    ("output",)          # optional positional
```

## The full argparse surface

Anything `parser.add_argument()` accepts is reachable through `Arg[T, Meta(...)]`,
where `Arg` is `typing.Annotated`:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

class Run(Args):
    tags: Arg[list, Meta(action="append", metavar="TAG")] = []
    "Repeatable tag"
    ("--tag",)

    level: Arg[int, Meta(choices=(1, 2, 3))] = 1
```

`action`, `nargs`, `const`, `metavar`, `choices`, `required` — they all
pass straight through. Anything duho doesn't model explicitly can go through
`Meta(kwargs={...})`, which is merged last. `dest` is not among them: a field's `dest`
is always its declared name, and `Meta(dest=...)` is a `TypeError`.

### Typed metadata with `Meta`

`duho.Meta` is a dataclass of the known metadata fields. It takes keyword
arguments only: a positional argument and an unknown keyword (`Meta(hlep="oops")`)
are both a `TypeError`, and only the fields you set are merged. The `TypeError` is raised as soon as the
annotation is evaluated: at class-definition time on Python 3.9-3.13 with
eager annotations, or at first parser build on 3.14+ (PEP 649), under string
annotations, or with `from __future__ import annotations`:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

class Run(Args):
    level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
```

A field's `dest` is always its declared name, so `Meta` has no `dest` field at
all and `Meta(dest=...)` is a `TypeError`. `Meta(kwargs=...)` is the raw
`add_argument` escape hatch.

### Untyped metadata with a `dict`

A plain `dict` (`dict(help="...", env="...")` or a literal) is the permissive form:
use it for a key `Meta` does not have, or for the keys a custom argument's own
builder declares. A key that no `Meta` field and no such builder claims is ignored
and logged (see [Misdeclaration warnings](#misdeclaration-warnings)):

```python
from duho import Arg, Args

class Run(Args):
    level: Arg[int, dict(help="verbosity", env="LEVEL")] = 0
```

Any metadata object exposing a str `.documentation` attribute (a PEP-727-style
`Doc`) contributes help text, so `Arg[int, Doc("how many")]` works too.

### An option value that looks like `--`

By default argparse treats a token after an option that starts with `-` as another
option, and duho treats a bare `--` as the start of the passthrough tail. For an
option whose value may legitimately be one of those, `Meta(literal_value=True)` makes
the token after the flag always its value:

<!-- runnable -->
```python
import duho
from duho import Cmd, Arg, Meta

class Grep(Cmd):
    pattern: Arg[str, Meta(literal_value=True)] = ""
    ("-e", "--pattern")

    def __call__(self):
        print(self.pattern, self._passthrough_)

if __name__ == "__main__":
    raise SystemExit(duho.main(Grep))
```

`grep.py --pattern -x -- tail` prints `-x ['tail']`, and `--pattern --` makes the
pattern `--`. It applies only to an option that takes exactly one value: on a bare
flag, a variable-arity option or a positional it raises `ValueError` naming the field
when the parser is built. A repeated option joins once per occurrence, and
`--pattern=--` works with or without it. The option strings of the whole subcommand
tree are collected once per parser. Two forms are not joined: an abbreviated long flag
(`--pat` for `--pattern`) and a short flag inside a cluster (`-vp -x`); an empty value
after a short flag is left alone as well.

### Mutually exclusive groups

`Meta(conflicts="<group-name>")` puts fields into the same mutually exclusive group:

```python
class Output(Args):
    json: Arg[bool, Meta(conflicts="format")] = False

    yaml: Arg[bool, Meta(conflicts="format")] = False
```

Passing both `--json` and `--yaml` is now an error.

Add `conflicts_required=True` on any member to require exactly one:

```python
    push: Arg[bool, Meta(conflicts="mode", conflicts_required=True)] = False

    pull: Arg[bool, Meta(conflicts="mode")] = False
```

Omitting both `--push` and `--pull` is now an error.

### Titled argument groups

`Meta(group="<title>")` buckets fields under a named `--help` section:

```python
class App(Args):
    outfile: Arg[str, Meta(group="Output options")] = "-"
```

A field combining `group=` and `conflicts=` nests the mutually-exclusive group
inside the titled section.

### Helpers

Common `Meta(...)` combinations have shorthands:

<!-- runnable -->
```python
from duho import Args, Arg, Count, Append, Const, Choice, Extend

class App(Args):
    verbose: Arg[int, Count()] = 0            # -vvv -> 3
    ("-v",)

    tags: Arg[list, Append()] = []            # --tag a --tag b -> ["a", "b"]
    ("--tag",)

    mode: Arg[str, Const("fast")] = "slow"    # --fast -> "fast"
    ("--fast",)

    color: Arg[str, Choice("auto", "always", "never")] = "auto"

    paths: Arg[list, Extend(":")] = []        # --path a:b -> ["a", "b"]
    ("--path",)
```

## Misdeclaration warnings

Two mistakes that would otherwise silently do nothing are logged as a WARNING on the
`duho.args` logger when the parser is built, once each. Neither is an error and neither
has a switch.

A `dict` key that is not a `Meta` field is reported once per field, naming the
nearest `Meta` field when there is one:

```text
App.port: dict(hlep=...) is not a Meta field and is ignored; closest Meta field: 'help'
```

`dest` is such a key. Keys that `Extend`, `Count`, `Append`, `Const` and `Choice` set,
and attributes a custom `ArgumentBuilder` subclass declares, are accepted. `Meta`
itself needs no such check: an unknown keyword is a `TypeError`.

A sandwich attribute (leading and trailing underscore) in a class body that duho does
not read, but whose spelling is a near-miss of exactly one it does, is reported once
per class:

```text
App declares '_verison_', which duho does not read; did you mean '_version_'?
```

Only a near-miss is reported. An attribute of your own that merely extends a name duho
reads (`_config_dir_`), a dunder, and a name starting `_duho_` are left alone.

## Private fields

A field whose name starts with `_` is **not** a CLI argument — duho skips it.
Use this for internal state you want on the instance but not on the command line.

Framework members are sandwich-named (`_parser_`, `_version_`, `_subcommands_`,
`_config_`…) and the dispatch hook is the `__call__` dunder — implement it (on a
`duho.Cmd` subclass) to make a class runnable, and
`duho.main`/`duho.run_command` call `instance()` to run it — so a field called
`main` or `parse` will not collide with anything.

Three plain (non-sandwich) names ARE reserved, though: a field named `help`,
`version` (when a `--version` flag resolves), or `print_completion` (when
`_completion_ = True`) raises a build-time `ValueError` naming the collision,
since each would otherwise clash with an argparse-added flag of the same
name. On a `duho.Cli` subclass, `subcommand` is a fourth, differently-behaved
gotcha: it is the name `Cli` itself uses for the `@Root.subcommand` decorator,
so a CLI field also named `subcommand` silently replaces the decorator
instead of raising — avoid that field name on a `Cli` subclass.
