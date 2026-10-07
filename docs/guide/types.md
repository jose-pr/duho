# Types and conversion

The annotation decides how duho converts the string argparse hands it.

| Annotation | Behavior |
| --- | --- |
| `str`, `int`, `float` | Direct conversion. A conversion failure is a normal argparse error. |
| `bool` | Default `False` (or no default) → a simple `--flag` switch. Default `True` → `--flag` / `--no-flag`, so the default can be turned back off. |
| `typing.Literal["a", "b"]` | Becomes `choices`. Mixed-type literals (`Literal["auto", 1]`) try each declared value's own type and keep whichever round-trips. |
| `enum.Enum` subclass | `choices` are the member **names**; the parsed value is the member itself. `Meta(enum_by="value")` matches the value text instead. |
| `list` / `list[T]` | As an OPTION: one value per flag occurrence, repeated (`--x a --x b`) to accumulate — space-separated (`--x a b`) is **not** the default; pass `Meta(nargs="*")` to opt back into it. As a POSITIONAL: variadic and space-separated (`nargs="*"`) unconditionally. Bare `list` elements are `str`. Defaults to `[]`. |
| `datetime.date` / `datetime.datetime` / `datetime.time` | Parsed via `fromisoformat`. See [Dates and times](#dates-and-times) below. |
| `typing.Optional[T]` / `T \| None` | Not required; converts with `T`. |
| `typing.Union[A, B]` / `A \| B` | Tries each member in declaration order; the first that accepts the text wins. |
| `pathlib.Path` | Converted to a `Path`. Also gets file completion in generated [completion scripts](completion.md). |

PEP 604 unions (`int | str`) require Python 3.10+. On 3.9, use `typing.Union`.

## Negative numbers

Negative numeric values work out of the box — both as option values
(`--temp -5`) and as positionals (a positional `int` accepts `-3`). This is
argparse's own `_negative_number_matcher` at work: when your parser declares no
option that itself *looks* like a negative number (e.g. a literal `-1` flag),
argparse treats `-5`/`-2.5` as values, not unknown options. No special support is
needed.

If you genuinely need a `-1`-style *flag* (rare and ambiguous), reach for the
`Meta(kwargs=...)` escape hatch to pass raw `add_argument` keywords.

## Booleans

```python
class App(Args):
    verbose: bool = False     # --verbose

    color: bool = True        # --color / --no-color
```

A `True` default uses `argparse.BooleanOptionalAction` — without it, a
`store_true` flag could never express "actually, false".

## Enums

Enum members are matched by **name**, not value:

<!-- runnable -->
```python
import enum
from duho import Args

class Color(enum.Enum):
    RED = 1
    GREEN = 2

class App(Args):
    color: Color = Color.RED
    "Pick a color"
```

```bash
$ app --color GREEN     # -> Color.GREEN
$ app --color 2         # error: invalid choice
```

`--help` shows the member names, and unknown names are rejected.

### Matching by value

`Meta(enum_by="value")` (or `Meta(enum_by="value")`) matches the text against
`str(member.value)` instead of the member name. It applies on the command line, in
env and in config (a TOML number `2` matches the value `2`); `--help`, the error
text, shell completion, agent help and the MCP schema all list the value text. The
parsed field is still the member:

<!-- runnable -->
```python
import enum
from duho import Args, Arg, Meta

class Mode(enum.Enum):
    FAST = "f"
    SLOW = "s"

class App(Args):
    mode: Arg[Mode, Meta(enum_by="value")] = Mode.FAST
    "Speed"
```

```bash
$ app --mode s       # -> Mode.SLOW
$ app --mode SLOW    # error: invalid choice: 'SLOW' (choose from f, s)
```

It applies to a plain `Enum`, an `Optional[Enum]` and the elements of a
`list`/`set`/`tuple` or the values of a `dict`; a `Literal` of `Enum` members is
unchanged. Two members whose value text is the same (aliases excepted) raise
`ValueError` naming the field when the parser is built, and so does any `enum_by`
other than `"name"` or `"value"`.

### Enums inside a Union

The same name-matching applies when an enum sits inside a `Union` or `Optional` —
and a name match wins **before** falling through to a later member, so
declaration order matters:

```python
class App(Args):
    kind: ty.Union[Color, str] = "auto"
```

```bash
$ app --kind RED        # -> Color.RED   (matched the enum by name)
$ app --kind whatever   # -> "whatever"  (fell through to str)
```

Without the name-first rule a total type like `str` would swallow every value and
the enum would never match.

!!! note
    A `Union` containing an enum does **not** set `choices` — argparse can't
    express "an enum name *or* any string". The field stays free-form, with enum
    names preferred. A bare enum field does set `choices`.

## Lists

An OPTION collects a `list[T]` field with one value per flag occurrence —
repeat the flag to accumulate:

```python
class App(Args):
    tags: list[str] = []
    ("--tag",)
```

```bash
$ app --tag a --tag b     # -> ["a", "b"]
$ app --tag a b           # error: unrecognized arguments: b
```

Space-separated multi-value (`--tag a b`) is not the default for an option —
pass an explicit `Meta(nargs="*")` to opt back into it:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

class App(Args):
    tags: Arg[list[str], Meta(nargs="*")] = []
    ("--tag",)
```

```bash
$ app --tag a b           # -> ["a", "b"]
```

A `list[T]` **positional**, by contrast, is always variadic and
space-separated (`nargs="*"`) — no override needed:

```python
class App(Args):
    tags: list[str] = []
    ("tags",)
```

```bash
$ app a b     # -> ["a", "b"]
```

## Dates and times

`datetime.date`, `datetime.datetime`, and `datetime.time` fields are parsed
with the type's own `fromisoformat`:

<!-- runnable -->
```python
import datetime
from duho import Args

class App(Args):
    day: datetime.date = None
```

```bash
$ app --day 2026-01-01     # -> datetime.date(2026, 1, 1)
```

A trailing `Z` (RFC 3339's UTC marker) is accepted for `datetime`/`time`
values on **every** supported Python version — including 3.9/3.10, where
it's rewritten to `+00:00` before delegating to `fromisoformat` (which only
started accepting `Z` natively on 3.11). A `date` value never accepts a
trailing `Z` on any version — a date has no time-of-day/UTC component, so
`fromisoformat` rejects it the same way on every version. A basic, no-dash
format like `20260101` works for `date`/`datetime` on 3.11+ (native
`fromisoformat` accepts it there) but raises on 3.9/3.10 — stick to the
dashed/colon-separated ISO forms if you need to support the older versions.

## Dicts

A `dict[str, V]` field collects `KEY=VALUE` tokens; repeated flags merge into
one dict, and the value half is converted with `V`:

```python
class App(Args):
    define: dict[str, int] = {}
    ("--define", "-D")
```

```bash
$ app -D width=80 -D height=24     # -> {"width": 80, "height": 24}
```

Only the first `=` splits, so a value may itself contain `=`
(`-D url=a=b` → `{"url": "a=b"}`). A token with no `=` is a clear argparse
error. Keys are always strings — a non-`str` key type (`dict[int, str]`) is a
build-time error. A bare `dict` means `dict[str, str]`. The default is `{}`
when none is declared. Under the env/config layers, an env string `k=v` becomes
a one-pair dict and a TOML table converts each value through `V`.

## Unions

Members are tried in order, so put the most specific type first:

```python
    value: ty.Union[int, str]     # "5" -> 5,  "x" -> "x"
```

Only `TypeError`/`ValueError` count as "try the next member" — an unexpected
exception from a custom type propagates rather than being silently swallowed.

## Custom types

Any callable taking a single string works as a type via `Meta(type=...)`:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

def kv(text: str) -> tuple[str, str]:
    key, _, value = text.partition("=")
    return key, value

class App(Args):
    setting: Arg[tuple, Meta(type=kv)] = ("", "")
    ("--set",)
```

When your converter raises `ValueError` or `TypeError` with a message, that message
is the usage error:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

def port(text: str) -> int:
    value = int(text)
    if not 1 <= value <= 65535:
        raise ValueError("port must be 1..65535")
    return value

class App(Args):
    port: Arg[int, Meta(type=port)] = 8080
```

```bash
$ app --port 99999
app: error: argument --port: port must be 1..65535
```

An empty message, a builtin type, duho's own factories and a converter that raises
`argparse.ArgumentTypeError` keep argparse's own text. This covers an explicit
`type=`, not a class used as the annotation (`port: SomeClass`), and a bad value from
the env or config layers never echoes the value or the message.

For richer control, implement the `Argument` protocol and provide an
`_argbuilder_` classmethod — see the [API reference](../api/args.md). The hook also
applies to such a type as a member of an `Optional`/`Union` or the element of a
`list`, `set`, `tuple` or `dict`; there only the builder's factory (`type`),
`choices` and `metavar` are used.
