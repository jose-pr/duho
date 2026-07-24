# `duho` — public API header

Header-file-style reference for the `duho` package: every `__all__` export with its
signature, arguments, contract, and gotchas, so this module can be consumed without
reading its source. Kept current with the public API. For the framework overview and the
class-declaration form, see <https://github.com/jose-pr/duho>.

`import duho` never eagerly imports `json` or `importlib.metadata` (a tested contract);
keep any addition here that would break that lazy.

## Declaring commands

- **`Args`** — base declarative data class. Annotated non-`_` class attrs become CLI
  fields; an adjacent string literal is help text, an adjacent tuple literal is the flag
  set (`("--env","-e")`; omit → positional named after the field). Not runnable on its own.
  Classmethods: `_parser_(subparsers=None, *, parents=None, **kw) -> ArgumentParser`,
  `_getargs_() -> list[ArgumentBuilder]`, `_initparser_(parser)`.
- **`Cmd(Args)`** — executable `Args`. Override **`__call__(self) -> int | None`** (the
  entrypoint; `None` → exit 0). Base `__call__` raises `NotImplementedError` naming the class.
- **`Cli(Cmd)`** — application-root mixin. Declares (as typed sandwich attrs) `_version_`,
  `_distribution_`, `_completion_` (default `False`), `_config_`, `_subcommands_` (default
  `None`). Adds no run behavior. Self-registration: `Cli._register_subcmd_(child)` /
  `@Root.subcommand` attaches a child to the root's `_subcommands_` (copy-on-write per
  class — never mutates a parent's list). Recommended base order `class App(LoggingArgs, Cli)`.
- **`command(args_cls, func, *, name=None) -> type[Cmd]`** — build a `Cmd` subclass from a
  data `Args` + a callable; the built `__call__` calls `func(self)`. `name` sets `_parsername_`.
- **`Argument`** — `runtime_checkable` protocol for custom field types; implement
  `_argbuilder_()` classmethod to customize the `ArgumentBuilder`.
- **`ArgumentBuilder`** — the per-field spec (flags + `add_argument` kwargs). `_effective_default_()`
  gives the argparse-normalized default (e.g. an implicit `False` for a bare `store_true` flag).

### Field metadata helpers (use inside `Arg[T, ...]`)

`Arg` is `typing.Annotated` (`Arg[T, NS(...)]` — needs ≥2 args; a plain-typed field is just
its annotation). Metadata objects: **`NS(...)`** (namespace: `flags`, `help`, `conflicts=`
exclusive group, `metavar`, …), **`Meta`**, **`Choice`**, **`Const`**, **`Count`**,
**`Append`**, **`Extend`**(sep) for collection fields, **`UpdateAction`** (`-O k=v` dict
merge). **`AUTO`** — sentinel for `_version_ = duho.AUTO` (resolve version via
`importlib.metadata`, distribution overridable by `_distribution_`).

## Build / parse / run

- **`parser(cls, ...) -> ArgumentParser`** — delegates to `cls._parser_`.
- **`parse(spec, argv=None, *, parser_kwargs=None)`** — build+parse in one call. `spec` a
  type → new instance; `spec` an instance → its field values become defaults (CLI wins),
  returns a new `type(spec)` instance, `spec` untouched. Precedence CLI > instance > class default.
- **`parse_globals(cls, argv=None, **parser_kwargs)`** — parse only the root globals,
  ignoring subcommands (drops the subparsers action before a help-suppressed parse).
- **`main(cls, argv=None, *, setup_logging=True) -> int`** — build → parse → optional
  logging setup (when the instance has `_set_loglevels_`) → run the selected command.
  Dispatching a bare data `Args` (no `__call__`) raises `NotImplementedError`.
- **`app(root=None, *, commands=None, source=None, argv=None, name=None, description=None,
  env=None, config=None, setup_logging=True, dispatch=None) -> int`** — multi-command
  runner. Command-set precedence: `commands` > `discover_commands(source)` >
  `env.list("CMDS_PATH", ty=Path)` > `root._subcommands_`. Layers env/config defaults onto
  root + each class command (`CLI > env > config > class default`); attaches the resolved
  `Env` as `_env_`. `dispatch(command, instance) -> int` replaces only the final run step.
- **`run_command(command, instance, *, context=None) -> int`** — dispatch one resolved
  command. Class command → `instance()`. Module command → `init → main → success /
  finally_` lifecycle (`finally_` always runs; `main` exception propagates after it).
  `None` → 0, int propagated, any other return value passes through unchanged.

`_passthrough_`: on a parsed instance, argv after the first literal `--` (a `list[str]`,
empty when absent).

## Discovery

- **`Command`** — `runtime_checkable` protocol: `_parsername_` + a runnable body. Two
  kinds: a class command (strict `Cmd` subclass) and a `ModuleCommand`.
- **`ModuleCommand`** — adapts a command `.py` module (plain wrapper, not a `ModuleType`
  subclass). `_parsername_` = module `_parsername_`/`_cli_name` override, else file stem with
  `_`→`-`. Entrypoint `main` (fallback `run`/`call`); optional hooks `register`/`init`/
  `success`/`finally_`. A module with no entrypoint raises `NotImplementedError` (→ skipped).
- **`CmdBuilder(qualname, source=None)`** — resolve one source (Path / dotted import path /
  module / Command) to a command. Filesystem imports use synthesized-unique `sys.modules`
  keys (a loose `json.py` never clobbers stdlib `json`).
- **`discover_commands(source) -> list[Command]`** — walk a package or directory; collects
  both class commands (module-boundary deduped) and one `ModuleCommand` per entrypoint
  module. Result sorted by subcommand name. **Resilience**: catches only `(ImportError,
  NotImplementedError)` per command (logs + skips); everything else (e.g. `SyntaxError`)
  propagates.
- **`register_command_provider(predicate, builder)`** — injection seam for directory-shaped
  runtimes; providers (a module-global, newest-first) are consulted before importing a
  filesystem/namespace source. Tests must snapshot/restore.
- **`discover_entry_points(...)`** — enumerate installed entry points (imports
  `importlib.metadata` lazily).

## Env / config

- **`Env(prefix, ...)`** — prefixed, typed `os.environ` accessor. Normalizes `prefix`
  (upper, `-`→`_`, trailing `_`), autoloads an optional `<prefix.lower()>env` companion
  module of defaults (missing → silently ignored). Precedence, highest first: `**env`
  kwargs / a runtime `env[k] = v` write, then the real `os.environ`, then the companion
  module's shipped defaults (fixed 2026-07-24 — a companion-module value used to
  shadow a real exported variable). Methods incl. `.list(name, ty=)`, `.paths(name,
  ty=)` (splits on `os.pathsep`, not `.list`'s `":"` default — use this for a path-list
  var so a Windows drive letter is never mis-split). Mapping-like (`__iter__`/`__len__`/
  `**env`).
- **`value_sources(...)`** — introspect where a parsed value came from (CLI/env/config/default).

## Logging

- **`LoggingArgs`** — mixin adding `-v/--verbose`, `-q/--quiet`, `--loglevel`. `_logger_`
  (scoped to parser name), `_set_loglevels_()`, `_verbose_loglevel_()`. Verbosity table
  `VERBOSE_LEVELS` is most-severe-first; `-v`→DEBUG, `-vv`→TRACE, `-q`→WARNING; verbose and
  quiet offset each other.
- **`init_stderr_logging(...)`** — one-liner console logging setup.
- **`add_logging_level(name, level)`** — register a custom level (e.g. TRACE); shifts the
  verbosity table indices.
- **`parse_loglevels(...)`** — parse a level spec string.
- **`DefaultFormatter` / `DefaultsFormatter` / `ColorDefaultsFormatter` /
  `ColorHelpFormatter`** — formatters (ANSI color via optional `colorama`).

## Agent help / completion

- **`print_agent_help(cls, file=None)`** — write `cls`'s machine-readable JSON help
  document (also triggered by the `AGENT_HELP` env var / flag). `agenthelp` submodule:
  `describe(cls) -> dict`, `render(spec) -> str`.
- **`print_completion(...)` / `completion`** — bash/zsh/fish completion script generation.

## Text / names

- **`expand(s)`** — brace-range expansion (non-zero-padded). **`pysafe(s)`** — make a valid
  Python identifier. **`camelcase(s)` / `snakecase(s)`** — case conversion (note: `snakecase`
  drops interior uppercase — does not round-trip; see the private notes). **`gettext`** —
  translation shim.
- **`QualName` / `PythonName`** — dotted-name algebra (build/split/join qualnames).

## Opt-in submodules (import explicitly; off `duho.*`)

- **`duho.fanout`** — `run_targets(func, targets, *, max_workers=None, aggregate=max,
  logger=None) -> int` (ThreadPool per-target, exit-code reduced by `aggregate`),
  `fan_out_command`, `target_logging`, `TargetPrefixFilter`, `current_target`.
- **`duho.runpath`** — ordered `NN-name.py` step-runner over a dir with no `__init__.py`.
  `import duho.runpath` auto-registers its provider; `register(base=None)`/`unregister()`
  for explicit control. `register`'s `base` (default keeps the current `_BASE`, initially
  `LoggingArgs`) is the class every provider-built `RunPathCmd` subclass ALSO inherits
  from — `app()`'s `parents=` only copies a root's DATA fields onto a class command's
  parsed instance, never its METHODS, so `_logger_`/`_set_loglevels_` need real class
  inheritance to work; defaulting to `LoggingArgs` makes `-v`/logging work with zero
  config, and `register(base=MyAppRoot)` lets a custom root's own methods reach every
  RunPath command too. `RunPathCmd`, `--rcopts/-O` selection. Optional per-directory
  `__main__.py` lifecycle: `init(cmd, logger) -> ctx` (once, before any step; raising is
  always fatal), `success(ctx, cmd, logger)` (once, on a clean run), `finally_(ctx,
  cmd, logger)` (once, unconditionally) — a step entrypoint written `(cmd, ctx)`
  (arity-detected) receives `ctx`; `(cmd)` steps are unaffected. Step filenames accept
  a leading `!` (disable, stripped before the `NN-name` split) plus `:`/`;`-separated
  option tokens (`key`/`!key`/`key=value`; `:` and `;` both work everywhere, NOT an
  OS-conditional split — `;` is the Windows-authorable spelling since `:` is an
  invalid Windows filename character). Two tokens are special: `strict`/`!strict`
  (default strict, absent the token; `!strict` opts that ONE step out) and
  `enable`/`!enable` (explicit alternative to the leading `!`; wins if both are
  present — more specific). Same grammar reused verbatim by `--rcopts` per
  comma-entry (`_Opts.parse`/`_split_tokens`, shared, not duplicated). Precedence for
  a step's strict setting: filename default -> a per-pattern `--rcopts` `!strict`
  token matching it -> an EXPLICIT bare `--rcopts strict`/`!strict` (run-wide, wins
  last). Step modules may also set `BEFORE: list[str]` / `AFTER:
  list[str]` (soft ordering, no existence/success requirement — silently a no-op if
  the named step is missing or disabled) alongside the existing hard `REQUIRED:
  list[str]` (missing/disabled dep still warns, or errors under strict).
- **`duho.scaffold`** — `generate_launchers(app, root, *, libdir="lib", python=None,
  overwrite=False) -> list[Path]` writes a `bin/<app>` + `bin/<app>.cmd` launcher pair.
  CLI: `python -m duho.scaffold <app>`.
- **`duho.mcp`** — expose a duho CLI's `Cmd`/`Cli` classes as MCP tools (stdlib JSON-RPC
  over stdio, zero-dep). `describe_tools(root_cls) -> list[dict]`,
  `call_tool(root_cls, name, arguments) -> dict`, `serve(root_cls, *, stdin=None,
  stdout=None)`, `input_schema_for_command(cls) -> dict`. CLI: `python -m duho.mcp <app>`
  (`<app>` is a `module:ClassName` or dotted path to a root `Cmd`). `json`/`importlib.metadata`
  stay function-local.
