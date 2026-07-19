# Declaring arguments

A duho CLI is a class. Each annotated field becomes an argument.

```python
from duho import Args

class Deploy(Args):
    """Deploy the application."""

    environment: str
    "Target environment (prod, staging, dev)"
    ("--env", "-e")

    dry_run: bool = False
    "Preview changes without applying them"
    ("--dry-run",)
```

Three things make up a field:

| Part | Purpose |
| --- | --- |
| The **annotation** (`environment: str`) | The type. Drives conversion and validation — see [Types](types.md). |
| The **docstring** below it | The argument's `help` text. Optional. |
| The **tuple literal** below that | The flags. Optional. |

The class docstring becomes the parser's description.

## Flags are optional

With no tuple literal, the flag is derived from the field name, with underscores
becoming dashes:

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
    ("--version",)
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

Anything `parser.add_argument()` accepts is reachable through `Arg[T, NS(...)]`,
where `Arg` is `typing.Annotated` and `NS` is `argparse.Namespace`:

```python
from duho import Args, Arg, NS

class Run(Args):
    tags: Arg[list, NS(action="append", metavar="TAG")] = []
    "Repeatable tag"
    ("--tag",)

    level: Arg[int, NS(choices=(1, 2, 3))] = 1
    ("--level",)
```

`action`, `nargs`, `const`, `metavar`, `dest`, `choices`, `required` — they all
pass straight through. Anything duho doesn't model explicitly can go through
`NS(kwargs={...})`, which is merged last.

### Typed metadata with `Meta`

`NS(...)` is an untyped `argparse.Namespace`, so a misspelled key
(`NS(hlep="oops")`) is silently dropped. `duho.Meta` is a dataclass with the
same known fields — an unknown keyword is a `TypeError` at class-definition
time, and only the fields you set are merged:

```python
from duho import Args, Arg, Meta

class Run(Args):
    level: Arg[int, Meta(help="verbosity", env="LEVEL")] = 0
    ("--level",)
```

`Meta` is the recommended, typo-safe form; `NS` keeps working. `Meta.kwargs` is
the same raw `add_argument` escape hatch as `NS(kwargs=...)`.

Any metadata object exposing a str `.documentation` attribute (a PEP-727-style
`Doc`) contributes help text, so `Arg[int, Doc("how many")]` works too.

### Mutually exclusive groups

`NS(conflicts="<group-name>")` puts fields into the same mutually exclusive group:

```python
class Output(Args):
    json: Arg[bool, NS(conflicts="format")] = False
    ("--json",)

    yaml: Arg[bool, NS(conflicts="format")] = False
    ("--yaml",)
```

Passing both `--json` and `--yaml` is now an error.

Add `conflicts_required=True` on any member to require exactly one:

```python
    push: Arg[bool, NS(conflicts="mode", conflicts_required=True)] = False
    ("--push",)

    pull: Arg[bool, NS(conflicts="mode")] = False
    ("--pull",)
```

Omitting both `--push` and `--pull` is now an error.

### Titled argument groups

`NS(group="<title>")` buckets fields under a named `--help` section:

```python
class App(Args):
    outfile: Arg[str, NS(group="Output options")] = "-"
    ("--outfile",)
```

A field combining `group=` and `conflicts=` nests the mutually-exclusive group
inside the titled section.

### Helpers

Common `NS(...)` combinations have shorthands:

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
    ("--color",)

    paths: Arg[list, Extend(":")] = []        # --path a:b -> ["a", "b"]
    ("--path",)
```

## Private fields

A field whose name starts with `_` is **not** a CLI argument — duho skips it.
Use this for internal state you want on the instance but not on the command line.

Framework members are sandwich-named (`_parser_`, `_version_`, `_subcommands_`,
`_config_`…) and the dispatch hook is `__call__` (an `Args` instance is directly
callable — `instance()` runs the command), so the ordinary name space is
entirely yours: a field called `main`, `parse`, or `help` will not collide with
anything.
