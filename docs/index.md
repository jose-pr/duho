# duho

**duho** is a declarative CLI framework for Python. You define a class, annotate
its fields, and duho builds the `argparse` parser — type conversion, help text,
subcommands, config layering, and shell completion included.

Named after the sacred Taíno ceremonial stool — a symbol of power and authority —
duho is the **foundation** from which you command your application.

It has zero required runtime dependencies. Colored logging (`colorama`) and TOML
config on Python 3.9/3.10 (`tomli`) are optional extras, imported only when used.

## Why duho

- **Declarative.** The class *is* the CLI. A field's annotation gives its type,
  its docstring gives the help text, and a tuple literal gives its flags.
- **Type-driven.** `int`, `Literal`, `Enum`, `list[T]`, `Optional`, `Union` — the
  annotation decides how the CLI text is parsed and validated.
- **Not a walled garden.** duho builds a real `argparse.ArgumentParser`. Any
  `add_argument` keyword is reachable via `Arg[T, NS(...)]`, and you can take the
  parser and do whatever you want with it.
- **Layered defaults.** CLI args override environment variables, which override a
  TOML config file, which overrides class defaults — with a helper that tells you
  which layer won.
- **Batteries included.** Verbosity flags, `--version`, and static shell
  completion are one attribute each.

## Install

```bash
pip install duho
```

Optional extras:

```bash
pip install duho[colorama]   # colored log output
pip install duho[config]     # TOML config files on Python 3.9/3.10
```

## A first CLI

```python
import duho
from duho import Args

class Greet(Args):
    """Print a greeting."""

    name: str = "world"
    "Who to greet"
    ("--name", "-n")

    count: int = 1
    "How many times"
    ("--count", "-c")

    def __run__(self):
        for _ in range(self.count):
            print(f"Hello, {self.name}!")

if __name__ == "__main__":
    raise SystemExit(duho.main(Greet))
```

```bash
$ python greet.py --name Alice -c 2
Hello, Alice!
Hello, Alice!

$ python greet.py --help
usage: Greet [-h] [--name NAME] [--count COUNT]

Print a greeting.

options:
  -h, --help            show this help message and exit
  --name NAME, -n NAME  Who to greet
  --count COUNT, -c COUNT
                        How many times
```

## Where to next

- **[Declaring arguments](guide/arguments.md)** — fields, flags, help text,
  positionals, and the full `argparse` surface.
- **[Types and conversion](guide/types.md)** — what each annotation does.
- **[Running your app](guide/running.md)** — `duho.main`, `__run__`, subcommands,
  `--version`.
- **[Configuration layers](guide/config.md)** — env vars and TOML config files.
- **[Logging](guide/logging.md)** — `LoggingArgs`, `-v`/`-q`, colored output.
- **[Shell completion](guide/completion.md)** — bash/zsh/fish script generation.
