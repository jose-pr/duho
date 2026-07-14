# Logging

Mix in `LoggingArgs` to get verbosity flags and a configured logger for free.

```python
import duho
from duho import LoggingArgs

class App(LoggingArgs):
    """Do the thing."""

    target: str
    "What to act on"
    ("--target",)

    def __run__(self):
        self._logger_.info("working on %s", self.target)
        self._logger_.debug("some detail")

if __name__ == "__main__":
    raise SystemExit(duho.main(App))
```

`duho.main` calls `self._set_loglevels_()` before dispatching, so by the time
`__run__` runs the levels are applied. (Pass `setup_logging=False` to opt out; if
you drive the parser yourself, call `_set_loglevels_()` before you start logging.)

## The flags

`LoggingArgs` adds:

| Flag | Effect |
| --- | --- |
| `-v`, `-vv`, … | More verbose: `INFO` → `DEBUG` → `TRACE` |
| `-q`, `-qq`, … | Quieter: `INFO` → `WARNING` → `ERROR` → `CRITICAL` |
| `--loglevel LEVEL` | Set a level explicitly |
| `--loglevel mod:LEVEL` | Set a level for one module |

```bash
$ app --target x -v                     # DEBUG
$ app --target x -vv                    # TRACE
$ app --target x -q                     # WARNING
$ app --target x --loglevel DEBUG
$ app --target x --loglevel urllib3:WARNING,myapp:TRACE
```

`-v` and `-q` are counted flags that move in opposite directions from the default
`INFO`, and they offset each other (`-vv -q` nets one step more verbose). Both
ends of the scale clamp rather than wrapping or erroring.

## Colored output

`duho.init_stderr_logging()` installs a handler with `DefaultFormatter`, which
colors the level name. If [colorama](https://pypi.org/project/colorama/) is
installed it's used for Windows compatibility; otherwise duho emits raw ANSI
codes.

```bash
pip install duho[colorama]
```

## The TRACE level

duho registers a `TRACE` level below `DEBUG`:

```python
logger.trace("very fine detail")
```

Add your own levels with `duho.add_logging_level`:

```python
import duho

duho.add_logging_level("NOTICE", 25, color="cyan")
```

The new level is usable as `logger.notice(...)`, participates in the `-v`/`-q`
scale, and is accepted by `--loglevel`.

## Naming the logger

`self._logger_` is scoped to the parser's name. Override it with `_logger_name_`:

```python
class App(LoggingArgs):
    _logger_name_ = "myapp.cli"
```
