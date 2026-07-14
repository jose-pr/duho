# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/jose-pr/duho/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jose-pr/duho/releases/tag/v0.1.0
