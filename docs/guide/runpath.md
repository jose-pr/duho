# RunPath: ordered step directories (opt-in)

`duho.runpath` turns a directory of numbered `.py` files into a single command
that runs them **in order**. It is opt-in — core `duho` never imports it:

```python
import duho.runpath   # importing it registers the RunPath provider
```

A **RunPath directory** is a directory with *no* `__init__.py` whose files are
`NN-name.py` *steps*:

```
release/
├── 10-build.py
├── 20-test.py
└── 30-publish.py
```

```python
# 10-build.py — a step's body is its top-level main/run/call, same
# precedence as a module command. It receives the parsed command instance.
def main(cmd):
    cmd._logger_.info("building")
```

This is the same "bare directory of loose `.py` files, no `__init__.py`"
shape [discovering commands from files](running.md#discovering-commands-from-files)
uses — what routes a RunPath directory to this runner instead of normal
per-file discovery is entirely its `NN-name.py` filenames.

## A shared, per-run context: `__main__.py`

A RunPath directory may define a `__main__.py` — the same dunder Python
already uses for "this directory's entrypoint" — with up to three optional
callables:

```python
# __main__.py — runs once per invocation, before any step
def init(cmd, logger):
    return connect_once()          # ctx handed to every 2-arg step

def success(ctx, cmd, logger):
    logger.info("all steps completed cleanly")

def finally_(ctx, cmd, logger):
    ctx.close()                    # always runs, success or failure
```

```python
# 20-provision.py — a step opting into ctx just adds a 2nd parameter
def main(cmd, ctx):
    ctx.provision()
```

A step written `(cmd)` (no `ctx`) is unaffected — arity is detected
automatically, so old and new steps coexist in the same directory. `init`
raising is **always fatal**, regardless of `--rcopts strict` — every step
depends on `ctx`.

## Ordering and dependencies

- `PRIORITY: int` — overrides the `NN` prefix for ordering.
- `REQUIRED: list[str]` — a **hard** dependency: the named step must run and
  succeed **before** this one. Missing/disabled is a warning, or an error
  under strict.
- `BEFORE: list[str]` / `AFTER: list[str]` — **soft** ordering only (no
  existence/success requirement), styled after systemd's `Before=`/`After=`.
  A missing or disabled name here is silently a no-op — never a warning.

## Filename-encoded per-step options

A step's filename can carry a leading `!` (disabled by default) plus
`:`/`;`-separated tokens (`key`, `!key`, `key=value`) — the same grammar
`--rcopts` uses per entry:

```
01-step1.py                       # enabled, strict (defaults)
!02-step2.py                      # disabled by default
02-step2;!enabled.py              # same, explicit-token spelling
03-cleanup;!strict.py             # enabled, non-strict for this ONE step
```

## Selecting steps with `--rcopts` (`-O`)

```
--rcopts '!*,test'                 # run only test
--rcopts 'build:!strict'           # build's failure is resilient; every
                                    #   other step is untouched
--rcopts 'strict'                  # run-wide: overrides every step's own
                                    #   setting
```

See the [README](https://github.com/jose-pr/duho/#runpath-ordered-step-commands-opt-in)
for the full precedence rules and strict-vs-resilient semantics.

## A complete example

`examples/rc/` and `examples/runpath_app.py` in the repository demonstrate
every feature above in one runnable directory: a `__main__.py` lifecycle, a
`BEFORE`/`REQUIRED`/`AFTER` mix, and a filename-encoded `!strict` step.
Resolving a RunPath directory into an app uses `duho.discovery.CmdBuilder`
(not `discover_commands`, which only walks a directory's top-level files —
see [Discovering commands from files](running.md#discovering-commands-from-files)):

```python
import duho
import duho.runpath
from duho.discovery import CmdBuilder
from pathlib import Path

rc_command = CmdBuilder("rc", Path("examples/rc")).command
raise SystemExit(duho.app(commands=[rc_command], name="myapp"))
```
