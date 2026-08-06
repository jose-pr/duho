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

    def __call__(self):
        self._logger_.info("working on %s", self.target)
        self._logger_.debug("some detail")

if __name__ == "__main__":
    raise SystemExit(duho.main(App))
```

`duho.main` calls `self._set_loglevels_()` before dispatching, so by the time
`__call__` runs the levels are applied. (Pass `setup_logging=False` to opt out; if
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

## Debugging framework failures: `DUHO_TRACEBACK`

Several duho paths are deliberately **resilient**: they log a failure and keep
going rather than aborting the whole run. A command file that fails to import is
skipped so one bad command never hides the rest; a non-strict RunPath step that
raises is logged and the run continues; a fan-out target that raises fails only
that target.

That resilience is the right default, but it means the exception is swallowed —
the log line is the only record, and a one-line message tells you *that*
something broke, never *where*:

```
ERROR steps: step boom failed: kaboom
```

Set `DUHO_TRACEBACK` to get the full stack at every one of those sites:

```bash
DUHO_TRACEBACK=1 myapp run-steps
```

```
ERROR steps: step boom failed: kaboom
Traceback (most recent call last):
  File ".../duho/runpath.py", line 999, in __call__
    entrypoint(self)
  File ".../steps/01-boom.py", line 2, in main
    raise RuntimeError("kaboom")
RuntimeError: kaboom
```

The variable is read fresh on every log call, so you can export it for one run
without reinstalling or re-importing anything. `0`, `false`, `no`, `off`, and the
empty value all count as off; any other value enables it.

Behavior is unchanged either way — this only controls how much detail is
*logged*. A skipped command is still skipped, and a resilient step failure still
doesn't abort the run. Use `--rcopts strict` if you want a RunPath step failure
to actually stop the run.

## Logger names

Framework modules log under their own dotted name (`duho.runpath`,
`duho.discovery`, `duho.runtime`, `duho.fanout`, `duho.mcp`), so a handler or
`--loglevel` filter can target one subsystem:

```bash
myapp --loglevel duho.discovery:DEBUG run
```

All of them sit under the `duho` parent, so `--loglevel duho:DEBUG` still turns
on everything at once.

**Your commands' own records are named after the command, not the module.** A
command's `self._logger_` is scoped to its parser name, and that is what the
framework logs *through* wherever a run is associated with one. RunPath is the
clearest case: a `steps/` directory logs its per-step messages under `steps`,
not `duho.runpath`, so several RunPaths in one app stay distinguishable:

```
INFO steps: running step boom
ERROR steps: step boom failed: kaboom
```

Target those by the command's own name:

```bash
myapp --loglevel steps:DEBUG steps
```

The `duho.runpath` logger is only the fallback for a bare `RunPathCmd` with no
`LoggingArgs` mixin (plus a couple of module-level messages emitted while
scanning for step files, before any run is under way). Module commands work the
same way — their hooks get the args instance's `_logger_`, falling back to the
plain `duho` logger when the args class has none.
