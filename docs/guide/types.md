# Types and conversion

The annotation decides how duho converts the string argparse hands it.

| Annotation | Behavior |
| --- | --- |
| `str`, `int`, `float` | Direct conversion. A conversion failure is a normal argparse error. |
| `bool` | Default `False` (or no default) → a simple `--flag` switch. Default `True` → `--flag` / `--no-flag`, so the default can be turned back off. |
| `typing.Literal["a", "b"]` | Becomes `choices`. Mixed-type literals (`Literal["auto", 1]`) try each declared value's own type and keep whichever round-trips. |
| `enum.Enum` subclass | `choices` are the member **names**; the parsed value is the member itself. |
| `list` / `list[T]` | Accepts repeated (`--x a --x b`) and space-separated (`--x a b`) forms. Bare `list` elements are `str`. Defaults to `[]`. |
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
`NS(kwargs=...)` escape hatch to pass raw `add_argument` keywords.

## Booleans

```python
class App(Args):
    verbose: bool = False     # --verbose
    ("--verbose",)

    color: bool = True        # --color / --no-color
    ("--color",)
```

A `True` default uses `argparse.BooleanOptionalAction` — without it, a
`store_true` flag could never express "actually, false".

## Enums

Enum members are matched by **name**, not value:

```python
import enum
from duho import Args

class Color(enum.Enum):
    RED = 1
    GREEN = 2

class App(Args):
    color: Color = Color.RED
    "Pick a color"
    ("--color",)
```

```bash
$ app --color GREEN     # -> Color.GREEN
$ app --color 2         # error: invalid choice
```

`--help` shows the member names, and unknown names are rejected.

### Enums inside a Union

The same name-matching applies when an enum sits inside a `Union` or `Optional` —
and a name match wins **before** falling through to a later member, so
declaration order matters:

```python
class App(Args):
    kind: ty.Union[Color, str] = "auto"
    ("--kind",)
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

```python
class App(Args):
    tags: list[str] = []
    ("--tag",)
```

```bash
$ app --tag a --tag b     # -> ["a", "b"]
$ app --tag a b           # -> ["a", "b"]
```

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
    ("--value",)
```

Only `TypeError`/`ValueError` count as "try the next member" — an unexpected
exception from a custom type propagates rather than being silently swallowed.

## Custom types

Any callable taking a single string works as a type via `NS(type=...)`:

```python
from duho import Args, Arg, NS

def kv(text: str) -> tuple[str, str]:
    key, _, value = text.partition("=")
    return key, value

class App(Args):
    setting: Arg[tuple, NS(type=kv)] = ("", "")
    ("--set",)
```

For richer control, implement the `Argument` protocol and provide an
`_argbuilder_` classmethod — see the [API reference](../api/args.md).
