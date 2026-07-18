# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/jose-pr/duho/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jose-pr/duho/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jose-pr/duho/releases/tag/v0.1.1
[0.1.0]: https://github.com/jose-pr/duho/releases/tag/v0.1.0
