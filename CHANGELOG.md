# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **C1** `bool` env/config values now parse correctly: `false`/`0`/`no`/`off`
  map to `False` (previously `bool("false")` was `True`); an unknown string is
  a clear error naming the field and source.
- **C2** An env var / TOML string on a `list`/`set`/`tuple` field now becomes a
  single-element collection (`FILES=a.txt` -> `["a.txt"]`), matching one CLI
  occurrence, instead of running the element factory over the whole string.
- **C3** A subcommand's `set_defaults` no longer clobbers a root option's value
  given before the subcommand: layered defaults skip dests suppressed on the
  child parser.
- **M14** Non-string config (TOML) values are now converted to the field type
  (`timeout = 30` for a `float` field -> `30.0`; a `list[Path]` array ->
  `[Path(...), ...]`) instead of being installed unconverted.
- **C4** `duho.app()` now suppresses the root's inherited option defaults on
  every registered subcommand parser, so a global given before the subcommand
  (`myapp -v deploy`) and root env/config values survive to the dispatched
  command. Required inherited globals are also un-required on the child (the
  root parser still enforces them).
- **C5** `app()` loads config once and applies the root layer before its
  advisory prepass, and degrades to no-prepass on a `SystemExit`, so a required
  global supplied by config no longer hard-exits with a usage error.
- **M6** A command name registered by more than one source (e.g. a module and a
  class command) now logs a warning naming both; the last registration wins and
  dispatch resolves through the same single registry (previously argparse raised
  `conflicting subparser`).
- **C6** Union members now recurse through the full type ladder:
  `Optional[list[int]]` gets element conversion + the extend action (no more
  char-splitting), `Optional[Literal[...]]` gets choices, and a multi-member
  union with a collection member is a clear build-time error.
- **C7** Collection defaults (`list`/`set`/`dict`) are copied per parse/build, so
  mutating one parsed instance's list no longer leaks into the next parse or a
  directly-constructed instance.
- **C8** Foreign `Annotated` metadata (a bare `Annotated[int, "doc"]` string, or
  any non-namespace object) no longer crashes `_getargs_`; a PEP-727-style object
  with a str `.documentation` contributes help text, everything else is ignored.
- **C9** `ClassVar[...]` and `Final[...]` annotations are skipped instead of
  becoming broken CLI flags.
- **C10** `Literal[True, False]` builds and parses (goes through `type=`+`choices=`)
  instead of raising an argparse `TypeError` at build.
- **C15** `datetime.date`/`datetime.datetime`/`datetime.time` fields parse via
  `fromisoformat` (a bad value is a clean argparse error, not a traceback).
- **M15** A `set` used as a flags container is now a clear build-time error
  instead of a crash / nondeterministic flag order.
- **M17** `argparse.SUPPRESS` in `Annotated` metadata hides the field wherever it
  appears, not only as the first metadata item.
- **C11** A missing `<PREFIX>_CMDS_PATH` no longer glob-imports every `.py` in the
  current working directory (`Env.list` returns `[]` and `app()` guards on a
  non-empty value).
- **M3** `Env` companion-module autoload seeds only upper-case, non-underscore
  variables through `str()` coercion, and accepts `Env(prefix, autoload=False)`
  to disable the `sys.path`/CWD import.
- **M5** A fan-out target returning a non-int, non-None value is logged and
  isolated (counts as exit code 1) instead of aborting the whole fan-out.
- **M4** A RunPath step whose import raises `ImportError`/`NotImplementedError`
  is skipped with a warning (resilient) or re-raised (strict); an enabled step
  whose `REQUIRED` names a disabled step warns/raises; a `REQUIRED` cycle raises
  under strict. Non-environmental errors (e.g. `SyntaxError`) still surface.

- **M1** `prerun_parse` no longer patches `argparse._SubParsersAction.__call__`
  / `_HelpAction.__call__` process-globally; it swaps the specific action
  instances' classes (restored in `finally`), so it is thread-safe and reentrant.
- **M20** `pop_action` also removes the action from its argument group's
  `_group_actions`, so a popped flag no longer lingers in `format_help()`.

### Changed
- **C11 (breaking-ish)** `duho.Env.list` returns `[]` for a missing or empty
  value instead of the previous `[ty("")]` single-empty-element contract.

### Changed
- Internal tidy-up (no behavior change): removed unused imports (`typing` in
  `completion`, `argparse` in `presets`, `stat` in `scaffold`) and an orphaned
  dead helper (`_zsh_value_spec`) plus its unused-result callers in `completion`.
  The `from logging import *` under `TYPE_CHECKING` in `logging` is intentional
  (re-exports stdlib logging names for type checkers) and is retained.

## [0.3.2] - 2026-07-18

### Fixed

- **A literal `%` in a `Cmd` docstring no longer crashes parser build.** Docstring-derived
  `description`/`help` are escaped (`%` → `%%`) before argparse, which `%`-expands help
  strings; previously a docstring mentioning e.g. an RPM `%files` list raised
  `ValueError: badly formed help string` at parser-build time.
- **A global option given before a subcommand is no longer shadowed.** When a subcommand
  inherits an option the root also declares, the child's inherited default was clobbering
  the root's parsed value (so `app --db X sub` lost `--db`). The child's inherited optional
  defaults are now suppressed for root-declared dests, so the pre-subcommand value survives;
  passing the flag after the subcommand still overrides, and absent it uses the root default.
- **Constructing a `Cmd` directly now seeds declared field defaults.** A directly-built or
  self-cloned instance (`type(self)(**self._get_kwargs())`) previously lacked any field not
  passed — notably `store_true` bools, whose default only materialized via argparse. `Args`
  now fills those gaps with each field's effective default; passed/parsed values always win.

### Added

- **`parser.exclusive_groups`** is exposed on a built parser, so a `_parser_` override can
  add extra options into a `conflicts=`-built mutually-exclusive group.

## [0.3.1] - 2026-07-18

### Changed

- **Clearer error when a module `register` hook collides with a global flag.** Because
  every subcommand parser inherits the root's global options (parent-arg inheritance),
  a `register(parser, args)` hook that adds an inherited flag (e.g. `-q` from a
  `LoggingArgs` root) previously crashed with argparse's bare `conflicting option
  string: -q`. `duho.app` now catches that and re-raises naming the command and pointing
  at the global-flag cause. The README also documents the root's reserved flags to avoid
  in `register`.

## [0.3.0] - 2026-07-17

### Added

- **`duho.scaffold` opt-in launcher generator**: an opt-in, stdlib-only module (not on
  the core `duho.*` surface — core never imports it) that generates a cross-platform
  launcher pair so an app laid out as `bin/` + a `lib/`/`src/` package can run from a
  checkout without an install. `generate_launchers(app, root, *, libdir="lib",
  python=None, overwrite=False)` writes `bin/<app>` (POSIX `sh`) + `bin/<app>.cmd`
  (Windows), each of which prepends `<root>/<libdir>` to `PYTHONPATH` and runs
  `python -m <app>`, honoring a `PYTHON` environment override. The generator writes
  plain files (never symlinks), sets the POSIX launcher executable best-effort, and
  refuses to overwrite an existing launcher unless `overwrite=True`. A thin CLI
  (`python -m duho.scaffold <app> [--root DIR] [--libdir lib] [--python PY] [--force]`)
  dogfoods duho — it is itself a `duho.Cli` command.
- **`set`/`set[T]` and `tuple[T, ...]`/`tuple` collection fields**: annotate a field
  with `set`, `set[T]`, bare `tuple`, or a variadic homogeneous `tuple[T, ...]` and it
  parses like a `list` field — both `--x a --x b` (repeated) and `--x a b`
  (space-separated) forms, per-element type conversion, bare forms use `str` elements —
  but the final value is a `set` (dedups; iteration order not guaranteed) or `tuple`
  (order preserved). Defaults are `set()` / `()` when the field has no explicit default.
  A fixed-length heterogeneous `tuple[A, B]` is not supported and raises a clear error
  at parser build, naming the field and pointing to `tuple[T, ...]`.
- **Module `register` hook now accepts a 3-arg `(parser, args, logger)` form** in
  addition to the existing 2-arg `(parser, args)`. `duho.app` inspects the hook's
  signature and, for a 3-arg (or `*args`) hook, passes
  `logger = getattr(args, "_logger_", logging.getLogger("duho"))`; a 2-arg or
  non-introspectable hook is called unchanged. Fully backward-compatible — existing
  2-arg hooks are unaffected.
- **`duho.parse_globals(cls, argv=None, **parser_kwargs)`**: parse only a root
  command's global args, ignoring/relaxing the subcommand tree, so a consumer can
  resolve config-file-driven command search paths (or any other global) *before*
  building the full subcommand parser. A missing subcommand does not error and an
  unknown trailing token does not crash the parse; it returns the parsed root
  instance (globals only). This is the public form of the help-suppressed,
  subcommand-relaxed prepass `duho.app` already runs internally. Additive.
- **`duho.fanout` opt-in target fan-out**: an opt-in, stdlib-only module (not on the
  core `duho.*` surface — core never imports it) for running one command against many
  targets concurrently and rolling their exit codes into one.
  `run_targets(func, targets, *, max_workers=None, aggregate=max)` runs `func(target)`
  for each target on a `ThreadPoolExecutor` and returns an aggregated exit code
  (`None` → `0`, an int as-is, an unhandled exception → logged and treated as `1` so
  one failing target never aborts the rest; default `max` policy — `0` only if all
  succeed; empty targets → `0`; pass `aggregate=any` or a custom reducer to change it).
  Log records a target emits while it runs are tagged with a `[<target>]` prefix via a
  filter installed on the app's existing stderr handler for the duration and removed
  afterwards (no leaked filter, no per-target handler churn). `fan_out_command(command,
  make_instance, targets, ...)` is thin sugar dispatching one resolved command once per
  target via `duho.run_command`. Public API: `run_targets`, `fan_out_command`,
  `target_logging`, `TargetPrefixFilter`, `current_target`. Additive.
- **`duho.app(dispatch=...)` seam**: `app()` now accepts an optional
  `dispatch(command, instance) -> int` callback that replaces only the final
  "run the one selected command" step, while `app()` keeps owning discovery, parser
  build, registration, config/env thread-down, parsing, and logging setup. A consumer
  that needs a custom run contract (build a per-invocation context, fan the command out
  over targets via `duho.fanout`) reuses everything `app()` resolved instead of
  re-deriving it. `dispatch` receives the resolved `Command` (a `Cmd` subclass, or the
  `ModuleCommand`) and the parsed instance and returns the exit code. With
  `dispatch=None` (the default) behavior is unchanged — `app()` calls `run_command` as
  before.
- **`duho.runpath` opt-in RunPath step-runner**: an opt-in module (not on the core
  `duho.*` surface) that turns a directory of numbered `NN-name.py` files into one
  command running them in order. `import duho.runpath` registers a command provider
  on the Plan-13 `register_command_provider` hook (its first consumer) — core `duho`
  never imports it. Steps declare ordering via the `NN` prefix or a module-level
  `PRIORITY`, and dependencies via `REQUIRED`; a `--rcopts`/`-O` flag selects steps
  with comma-separated fnmatch patterns (`!` disables, `!*,x` = "only x") and a
  `strict` marker that turns unmatched-pattern / failed-step warnings into errors
  (resilient by default, matching discovery). Public API: `RunPathCmd`, `register`,
  `unregister`. Additive; nothing on the existing surface changes.
- **`duho.Cli` application root**: an opt-in mixin over `Cmd` for the *root* of a
  multi-command app. It types and documents the app-wide, sandwich-named config
  attributes a leaf `Cmd` doesn't declare — `_version_`, `_distribution_`,
  `_completion_`, `_config_`, `_subcommands_` — without changing how any of them is
  read (purely additive; a plain `Cmd` root still works). Recommended batteries-
  included recipe: `class MyApp(LoggingArgs, Cli)`. `LoggingArgs` stays orthogonal.
- **`@MyApp.subcommand` self-registration**: a leaf command file can attach itself to
  a `Cli` root's subcommand tree with the `@Root.subcommand` decorator (or
  `Root._register_subcmd_(child)`), instead of the root centrally listing every child
  in `_subcommands_`. Registration is per-class (copy-on-write — two `Cli` subclasses
  never cross-contaminate, a parent's list is never mutated) and composes with a
  statically-declared `_subcommands_` (union + dedup — a child listed both ways
  appears once).
- **`duho.app` config/env thread-down**: `app(root, ..., env=, config=)` now layers a
  `Cli` root's `_config_` (or an explicit `config=`) TOML defaults onto the root and
  each class command's fields (top-level keys → root, `[<Subcommand>]` table →
  subcommand), and attaches the resolved `Env` to the dispatched instance as the
  sandwich-named `_env_` handle so a command can read app-wide settings via
  `self._env_`. Precedence is unchanged: CLI > env > config > class default.
- **`duho.Env(prefix)`**: a prefixed, typed, app-wide view over `os.environ`. Reads
  keys sharing a normalized `<PREFIX>_` prefix (`Env("my-app")` → `MY_APP_*`), with
  `.bool(key)` and `.list(key, sep=":", ty=str)` accessors and an optional autoloaded
  `<prefix>env` defaults module. It is a `MutableMapping`. Distinct from the per-field
  `NS(env="VAR")` default layer — this is the app-level settings accessor.
- **Text/name utilities** (`duho.expand`, `pysafe`, `camelcase`, `snakecase`,
  `gettext`): `expand("web[01-03]")` expands `[a-b]` brace ranges into concrete
  strings (cartesian product for multiple ranges; **not** zero-padded); `pysafe`
  coerces text to a Python-safe dotted identifier; `camelcase`/`snakecase` convert
  case; `gettext` is a `gettext` shim.
- **`duho.PythonName` / `duho.QualName`**: dotted-name algebra (parts, parent,
  join/split, `/` composition, path mapping) for building command qualnames;
  `PythonName` runs each part through `pysafe`.
- **Command discovery** (`discovery.py`): `duho.discover_commands(source)` walks a
  dotted package name or a directory and returns a `list[Command]`, collecting BOTH
  class commands (`Cmd` subclasses) and module commands (`ModuleCommand`). It is
  **resilient** — a command that fails with `ImportError` (missing optional dep) or
  `NotImplementedError` (not a command) is logged and skipped so the rest still load,
  while a real bug (e.g. `SyntaxError`) still propagates. `duho.CmdBuilder(qualname,
  source=None)` resolves a single import path / filesystem path / module to a
  `Command`; `duho.ModuleCommand` adapts a `.py` module (entrypoint `main`/`run`/
  `call`, docstring help, optional `register`/`init`/`success`/`finally_` lifecycle
  hooks) to the `Command` protocol without subclassing `ModuleType`. The
  `duho.Command` protocol is the shape dispatch needs.
- **`duho.register_command_provider(predicate, builder)`**: an injection seam letting
  an external package teach `CmdBuilder` how to build a command from a directory shape
  core duho doesn't understand (e.g. an ordered run-path of numbered step files),
  without core importing that package. Consulted newest-first before a normal import.
- **`Cmd` command type**: a new `duho.Cmd(Args)` base carries the executable
  contract. Define `__call__(self)` on a `Cmd` subclass — a dunder, so it never
  collides with a CLI field (a plain `main` method would clash with a `--main` flag).
  A `Cmd` instance stays directly callable. `Cmd.__call__`'s base raises
  `NotImplementedError` naming the class when a subclass doesn't override it.
- **`duho.command(args_cls, func, *, name=None)`**: build a `Cmd` subclass from an
  existing data `Args` class and a callable — `func(self)` receives the parsed
  instance and its return value is the command result. `name` sets the subcommand
  name (`_parsername_`).
- **`_passthrough_`**: argv after a literal `--` separator is captured at parse time
  and exposed on the parsed instance as `_passthrough_: list[str]` (empty when no
  `--`; only the first `--` splits). Useful for forwarding trailing args to a wrapped
  command.
- **`duho.app()` / `duho.run_command()`** (`runtime.py`): a multi-command app runner.
  `app(root=None, *, commands=None, source=None, argv=None, name=None,
  description=None, env=None, setup_logging=True) -> int` builds a top-level parser
  for a `root` command, resolves a command set (explicit `commands` >
  `discover_commands(source)` > `env.list("CMDS_PATH", ty=Path)` >
  `root._subcommands_`), registers each under a subparsers tree, parses `argv`, and
  dispatches one command. Class commands and module commands (`ModuleCommand`) are
  both supported; global options are inherited by every subcommand, a module
  `register(parser, args)` hook can add arguments directly, `_passthrough_` reaches
  the dispatched command, and discovery is resilient (one bad command is skipped).
  `run_command(command, instance, *, context=None) -> int` dispatches a single
  resolved command: a class command via `instance()`, a module command through
  the `init -> main -> success / finally_` lifecycle with a shared context (hooks read
  the args instance's `_logger_`; no separate `logger` argument). `None` maps to exit
  code `0`; a returned int is propagated.

### Fixed

- **`duho.camelcase` crashed on a trailing, doubled, or leading separator**
  (`camelcase("global_")` raised `IndexError`). Empty segments from the split are now
  skipped. This surfaced constantly in code generation, where `pysafe` turns a Python
  keyword (e.g. a namespace named `global`) into `global_` and camelcasing that name
  hit the trailing underscore.

### Changed

- **BREAKING**: `Args` is now pure **data** and no longer runnable on its own —
  "every `Args` is callable" (from 0.2.0) is reversed. To run a command, subclass
  `duho.Cmd` and implement `__call__(self)` (or build one with `duho.command(...)`).
  Dispatching a bare data `Args` via `duho.main` now raises a clear
  `NotImplementedError` instead of silently doing nothing. The `LoggingArgs` preset
  stays a data mixin; combine it as `class App(LoggingArgs, Cmd)` (recommended base
  order) to get logging + a runnable command. A `Cmd`'s command body is `__call__`
  (dunder, collision-free); if you used a `main`-method draft during pre-release, rename
  it to `__call__`.

## [0.2.0] - 2026-07-16

### Added

- **Subcommand aliases**: set `_parseraliases_` on an `Args` subclass to register
  short/alternate names for it in a `_subcommands_` tree (e.g. `_parseraliases_ =
  ["c"]` so `app c` runs the same command as `app create`). Aliases dispatch to the
  same `__call__`. Absence of the attr is the unchanged default (no aliases).
- **`__version__` fallback for `--version`**: when `_version_` is unset, a
  class-level `__version__` string is now used to populate the `--version` flag, so
  an app already carrying the conventional `__version__` gets `--version` for free.
  `_version_` still wins when both are set (and remains the only form that accepts
  the `duho.AUTO` sentinel).

### Changed

- **BREAKING**: the command-dispatch hook is renamed from `__run__` to `__call__`.
  An `Args` instance is now directly callable — `instance()` runs the command —
  and `duho.main()` dispatches to `instance.__call__()`. Rename `def __run__(self)`
  to `def __call__(self)` on your command classes.

## [0.1.1] - 2026-07-14

### Added

- Documentation site at <https://jose-pr.github.io/duho/> — guides for declaring
  arguments, types and conversion, running your app, configuration layers,
  logging, and shell completion, plus a generated API reference.

### Changed

- Corrected the performance figures in the release notes to numbers measured on
  a fixed CI runner. Parser construction is **40–70× faster** than the uncached
  path (10.5–11.0 ms → 0.15–0.27 ms, median, on Python 3.9 and 3.13); the
  previously published multiplier came from a noisy development machine.

### Fixed

- README links to `LICENSE` are absolute, so they resolve on the PyPI project
  page rather than 404ing.

## [0.1.0] - 2026-07-14

Initial release.

### Added

- **Declarative `Args` classes** — define a CLI by annotating class fields. The
  field's docstring becomes its help text and a following tuple literal declares
  its flags (`("--name", "-n")`); with no tuple, the flag is derived from the
  field name (`dry_run` → `--dry-run`).
- **Type-driven conversion** from annotations: `str`/`int`/`float`/`bool`,
  `typing.Literal` (→ `choices`), `enum.Enum` (members matched by name),
  `list[T]` (repeated or space-separated), `Optional[T]`, and `Union[A, B]`
  (including PEP 604 `A | B` on 3.10+). Enums inside a `Union`/`Optional` are
  matched by member name, consistently with bare enum fields.
- **Positional arguments** — a flag tuple with no leading dash (`("source",)`);
  a positional with a default becomes optional (`nargs="?"`).
- **Full argparse passthrough** via `Arg[T, NS(...)]` — `action`, `nargs`,
  `const`, `metavar`, `dest`, `choices`, and any other `add_argument` keyword,
  plus `NS(conflicts="group")` for mutually exclusive groups.
- **Argument helpers**: `Count()`, `Append()`, `Const()`, `Choice()`, `Extend()`,
  and the `UpdateAction` action.
- **Entry points**: `duho.parser(cls)` builds a parser; `duho.parse(spec, argv)`
  builds and parses in one call — passing an *instance* layers CLI overrides on
  top of its field values (CLI > instance > class default) and returns a new
  instance without mutating the original.
- **Command dispatch**: `duho.main(cls, argv=None)` builds, parses, sets up
  logging, and calls the selected instance's `__run__()`. `_subcommands_` builds
  nested subparser trees automatically and dispatches to the deepest selected
  class.
- **Layered defaults**: per-field environment variables via `NS(env="VAR")` and
  TOML config files via `_config_` / `config=`, with the precedence ladder
  CLI > env > config > class default. Any layer supplying a value also
  un-requires that field. `duho.value_sources(parsed)` reports which layer won
  for each field.
- **`--version`**: set `_version_` to a string, or to `duho.AUTO` to resolve it
  from installed package metadata (`_distribution_` overrides the distribution
  name). When the distribution isn't installed, no `--version` flag is added
  rather than printing a bogus version.
- **Shell completion**: opt in with `_completion_ = True` to add
  `--print-completion {bash,zsh,fish}`, or call `duho.print_completion()`.
  Scripts are generated statically — no runtime dependency and no re-invoking
  your program on every keypress.
- **`LoggingArgs`** preset — `-v`/`-q` counted verbosity (offsetting, clamped at
  each end of the scale), `--loglevel` for global or per-module levels, colored
  stderr output (optional `colorama`), and a `TRACE` level.
- Type hints ship with the package (`py.typed`).

### Notes

- Zero required runtime dependencies. Optional extras: `colorama` (colored
  logging) and `config` (TOML on Python 3.9/3.10, where `tomllib` isn't stdlib).
- Supports Python 3.9 through 3.13.

[Unreleased]: https://github.com/jose-pr/duho/compare/v0.3.3...HEAD
[0.3.3]: https://github.com/jose-pr/duho/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/jose-pr/duho/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/jose-pr/duho/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/jose-pr/duho/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jose-pr/duho/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jose-pr/duho/releases/tag/v0.1.1
[0.1.0]: https://github.com/jose-pr/duho/releases/tag/v0.1.0
