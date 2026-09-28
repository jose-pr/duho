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
automatically, so old and new steps coexist in the same directory. The `ctx`
argument is only ever supplied when a `__main__.py` actually exists in the
directory; a 2-parameter step in a directory with no `__main__.py` simply
keeps calling with just `cmd`, so its own second parameter keeps whatever
default it declared. `init` raising is **always fatal**, regardless of
`--rcopts strict` — every step depends on `ctx`.

`success` runs *inside* the run, right after the last step and before
`finally_` — not after it — and only when nothing failed (see "Step outcomes"
below); a raising `finally_` is logged and swallowed rather than replacing
whatever error is already in flight.

## Giving steps your app's own signature

Steps are called `main(cmd)` or `main(cmd, ctx)`. If your app's *module*
commands take a different shape — say `run(client, args, logger)` — steps and
commands disagree, and the same body cannot move between them.

`register(step_adapter=...)` takes a callable applied to each step's entrypoint
just before it runs; it receives the entrypoint and returns the callable to call
instead. That makes the step signature an app-wide convention rather than
something every step file opts into with a decorator:

```python
import duho.runpath

def adapter(entrypoint):
    def call(cmd, ctx=None):
        return entrypoint(ctx, cmd, cmd._logger_)   # (client, args, logger)
    return call

duho.runpath.register(step_adapter=adapter)
```

The *adapted* callable is what arity detection inspects, so a wrapper is free to
change the signature. An adapter that returns its argument unchanged leaves
duho-native steps alone — which is how an app supports both shapes at once.

Pass `None` to clear it; omit the argument to leave it unchanged. Unlike `base`,
it applies per step run, so it also affects already-built commands. The default
is `None`: steps are called exactly as written.

## Ordering and dependencies

- `PRIORITY: int` — overrides the `NN` prefix for ordering.
- `REQUIRED: list[str]` — a **hard** dependency: the named step must run and
  succeed **before** this one. A name that is missing or disabled is a
  warning (or an error under a bare `--rcopts strict`); a name that IS
  present, enabled, and actually **fails** (raises, or a non-zero step
  return) skips this step too, following THIS step's own strict setting —
  not just an ordering hint.
- `BEFORE: list[str]` / `AFTER: list[str]` — **soft** ordering only (no
  existence/success requirement), styled after systemd's `Before=`/`After=`.
  A missing or disabled name here is silently a no-op — never a warning. Only
  enabled, selected steps ever enter the ordering graph, so a disabled step's
  `BEFORE`/`AFTER`/`REQUIRED` can never reorder or gate an enabled one.

## Step outcomes

A step returns `None` (success) or a non-zero `int` (failure) — handled
through the exact same strict-vs-resilient path as a raised exception. The
command's own exit code is the MAXIMUM of every step's code (a resiliently
swallowed failure contributes `1`, since it has no numeric code of its own),
so a resilient run that swallowed a failure still reports it via a non-zero
exit code instead of always returning `0`. `__main__.py`'s `success` hook
only fires when that aggregate stays clean.

A step (or a `__main__.py` hook) must be synchronous — an `async def` one has
its coroutine closed immediately and raises `TypeError`, so a mistaken
`async def` is a loud failure instead of a silently-never-awaited coroutine.

## Filename-encoded per-step options

A step's filename can carry a leading `!` (disabled by default) plus
`:`/`;`-separated tokens (`key`, `!key`, `key=value`) — the same grammar
`--rcopts` uses per entry:

```
01-step1.py                       # enabled, strict (defaults)
!02-step2.py                      # disabled by default
02-step2;!enable.py               # same, explicit-token spelling
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
`examples/rc/__main__.py` is self-contained (it reads the shared root's
`label` field directly, with a fallback, rather than importing anything from
`runpath_app.py`), so the directory resolves the same way from ANY entry
point, including the snippet below saved as its own script at the repo root:

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

To see the shared-root-method form (`register(base=...)`, giving steps a real
`cmd._tag_line_(...)` method instead of just data fields), run the example
directly instead: `python examples/runpath_app.py rc`.
