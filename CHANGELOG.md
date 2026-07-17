# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`Cmd` command type**: a new `duho.Cmd(Args)` base carries the executable
  contract. Define `main(self)` on a `Cmd` subclass; `__call__` delegates to it, so
  a `Cmd` instance stays directly callable. `Cmd.main`'s base raises
  `NotImplementedError` naming the class when a subclass implements neither.
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
  resolved command: a class command via `instance.main()`, a module command through
  the `init -> main -> success / finally_` lifecycle with a shared context (hooks read
  the args instance's `_logger_`; no separate `logger` argument). `None` maps to exit
  code `0`; a returned int is propagated.

### Changed

- **BREAKING**: `Args` is now pure **data** and no longer runnable on its own —
  "every `Args` is callable" (from 0.2.0) is reversed. To run a command, subclass
  `duho.Cmd` and implement `main(self)` (or build one with `duho.command(...)`).
  Dispatching a bare data `Args` via `duho.main` now raises a clear
  `NotImplementedError` instead of silently doing nothing. The `LoggingArgs` preset
  stays a data mixin; combine it as `class App(LoggingArgs, Cmd)` (recommended base
  order) to get logging + a runnable command. Migrate `def __call__(self)` command
  bodies to `def main(self)` on a `Cmd` subclass.

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
