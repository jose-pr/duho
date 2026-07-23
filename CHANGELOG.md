# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-07-23

### Added
- **RunPath `__main__.py` lifecycle, filename-encoded per-step options, and
  `BEFORE`/`AFTER` soft ordering** (`duho.runpath`, opt-in). A RunPath
  directory may now define an optional `__main__.py` with up to three callables --
  `init(cmd, logger) -> ctx` (once, before any step; raising is always fatal,
  regardless of `--rcopts strict`, since every step depends on `ctx`),
  `success(ctx, cmd, logger)` (once, after a clean run), `finally_(ctx, cmd,
  logger)` (once, unconditionally) -- and a step entrypoint written `(cmd,
  ctx)` (arity-detected) receives that `ctx`; a `(cmd)` step is unaffected, so
  every existing step file keeps working unchanged. Step filenames now also
  accept a leading `!` (disables the step by default, stripped before the
  `NN-name` split) plus `:`/`;`-separated option tokens (`key`/`!key`/
  `key=value`; `:` and `;` are fully interchangeable everywhere -- NOT an
  OS-conditional split -- so a Windows-authored filename can use `;` instead,
  since `:` is an invalid Windows filename character). Two tokens are
  special: `strict`/`!strict` (a step's own default, absent the token, is
  strict-on-failure; `!strict` opts that ONE step out) and `enable`/
  `!enable` (an explicit, more-specific alternative to the leading `!`; wins
  if both are somehow present). This is the SAME token grammar `--rcopts` now
  uses per comma-entry -- one shared parser, including a new per-pattern
  `--rcopts` strict override (e.g. `build:!strict`) scoped to matching steps
  only, distinct from the pre-existing bare `strict`/`!strict` run-wide
  toggle. Precedence for a step's strict setting: filename default -> a
  matching per-pattern `--rcopts` token -> an explicit bare `--rcopts strict`/
  `!strict` (run-wide, wins last). Step modules may set `BEFORE: list[str]` /
  `AFTER: list[str]` (soft ordering only -- a missing or disabled name is
  silently a no-op, unlike the existing hard `REQUIRED`, whose
  missing/disabled-dep warning is unchanged) alongside the existing
  `REQUIRED: list[str]`, resolved together in one merged predecessor graph
  before `_order_steps`'s existing topological pass.
- **MCP tool surface** (`duho.mcp`, opt-in) A zero-dependency stdio JSON-RPC 2.0
  server that exposes a duho CLI's `Cmd`/`Cli` classes as MCP (Model Context
  Protocol) tools, with zero redeclaration: `input_schema_for_command(cls)` /
  `json_schema_for_field(decl, builder)` map each field's declared type
  (`str`/`int`/`float`/`bool`, `Literal`/`Enum`, `list`/`set`/`tuple`/`dict`,
  `Optional`/`Union`, `pathlib.Path`) to a JSON Schema fragment, reusing
  `duho._introspect.get_clsargs` + `cls._getargs_()` (the same per-field data
  `duho.agenthelp` collects, per Decision 2 -- `agenthelp` itself is
  untouched). `describe_tools(root_cls)` walks the built parser tree (reusing
  `duho.agenthelp.describe_parser`'s alias-dedup-by-identity) so every `Cmd` in
  a `_subcommands_` tree -- root included -- becomes one tool, namespaced
  `parent.child` when nested. `call_tool(root_cls, name, arguments)`
  synthesizes an argv from the JSON `arguments` (a repeatable field becomes a
  repeated flag; a `dict` field becomes repeated `KEY=VALUE` tokens; a
  positional a bare token, in declared order) and reuses the target class's own
  `_parser_()` + `duho.run_command` to dispatch, capturing stdout. Return
  convention: `None`/`0` -> success (captured stdout as one `text` block); a
  non-zero int -> `isError: true` (stdout + a trailing `exit code: N` line); a
  JSON-serialisable object/list return -> passed through as one `text` block
  holding its JSON dump. `python -m duho.mcp <app>` (`<app>` a dotted
  `module:ClassName` or `module.ClassName` qualname, resolved via the stdlib
  `pkgutil.resolve_name`) runs the stdio server against real stdin/stdout.
  Like `duho.runpath`/`duho.fanout`/`duho.scaffold`, this is a **standalone
  opt-in submodule** -- core `duho` never imports it, and it is not on the
  top-level `duho.*` surface; `json`/`importlib.metadata` stay lazily imported
  so `import duho.mcp` alone never pays their cost. v1 limitations (documented,
  not silently wrong): a custom `action=`/`type=` field with no registered
  override is passed through as a plain string; `NS(conflicts=...)` exclusive
  groups are noted in the tool description text only (no `oneOf`/`not`
  encoding yet); a module command (no duho class behind it) can be listed but
  not called; one request maps to exactly one result (no streaming/long-running
  commands).
- **Agent help** A detailed, machine-readable (JSON) description of a CLI, built
  for AI agents, on top of duho's existing introspection (`get_clsargs` /
  `ClsArgDeclaration` + per-field `ArgumentBuilder`). Two triggers: the always-on
  `AGENT_HELP` environment variable flips `-h`/`--help` into agent mode (human
  help is byte-identical when it is unset; the var name is overridable via
  `_agent_help_env_`), and the opt-in `_agent_help_ = True` adds a discoverable
  `--help-agents` flag. The document (schema `duho/agent-help@1`) covers every
  subcommand (with aliases), each option's type/default/required/repeatable/
  choices, positionals, per-field env-var bindings, mutually-exclusive conflict
  groups, examples (author-declared `_examples_` or a synthesized minimal
  invocation), and exit codes (`_exit_codes_` overrides). New module
  `duho.agenthelp` (parser-tree walk, mirrors `duho.completion`), plus
  `duho.print_agent_help(cls)`. `json` stays lazily imported.
- **F1** First-class `dict[str, V]` fields. A `dict`-annotated field collects
  `KEY=VALUE` tokens; repeated flags merge into one dict via `UpdateAction`, and
  the value half is converted with `V` (bare `dict` == `dict[str, str]`). Only
  the first `=` splits; a token with no `=` is a clear argparse error; a non-`str`
  key type is a build-time error. Default `{}`. Env (`k=v` → one-pair dict) and
  TOML-table config layers are supported. `UpdateAction` now makes a shallow
  per-occurrence copy instead of a `deepcopy`. `duho.Count()` counted flags
  (`-vvv` → `3`) are documented in the README type table.
- **F2** Required mutually-exclusive groups: `NS(conflicts="grp",
  conflicts_required=True)` on any member makes the whole group required
  (argparse requires exactly one). Omitting all members errors; the group's
  `required` flag is set at build time.
- **F3** Titled argument groups: `NS(group="Section title")` buckets a field
  under a named `--help` section (lazily created per title). A field combining
  `group=` and `conflicts=` nests the mutually-exclusive group inside the titled
  section.
- **F4** Async `__call__` support: a `Cmd` whose `__call__` is `async def` is
  driven to completion via `asyncio.run` at the call site (`duho.main` and
  `duho.run_command`), so the awaited value is the exit code. `asyncio` is
  imported lazily. Module-command lifecycle hooks stay synchronous.
- **F5** `duho.Meta`: a typed, typo-safe dataclass alternative to `NS(...)` for
  field metadata. An unknown keyword is a `TypeError` at class-definition time
  (an `NS(...)` typo silently vanishes); only the fields you set are merged.
  `NS` keeps working. PEP-727 `Doc` duck-typing (a metadata object with a str
  `.documentation` attr contributes help) is documented.
- **F6** Entry-points plugin discovery: `duho.app(root, entry_points="group")`
  loads commands advertised by installed distributions' entry points in `group`,
  coercing each to a command (a `Cmd` subclass → class command; a module →
  module command) through the same path as every other source. Loading is
  resilient — a plugin that fails to import or does not resolve to a command
  warns and is skipped. New public `duho.discover_entry_points(group)`.
  `importlib.metadata` stays lazily imported (only entry-point discovery loads
  it). Sits in `app`'s source precedence after `source=` and before the
  `CMDS_PATH` env layer.
- **F7** JSON config files + a pluggable loader. A `_config_`/`config=` path
  ending in `.json` is parsed as JSON (stdlib `json`, imported lazily; a malformed
  file raises a clear error naming it); any other suffix stays TOML. JSON yields
  the same nested-dict shape as TOML, so subcommand tables layer identically. A
  new class-level `_config_loader_` (`Callable[[Path], dict]`, declared on `Cli`)
  is used *instead of* the built-in dispatch when set, letting users plug any
  format (e.g. YAML) without duho depending on it — the zero-runtime-deps
  contract holds.
- **F8** Opt-in help formatters via a class-level `_help_formatter_`
  (plumbed into argparse's `formatter_class`, and propagated across a
  `_subcommands_` tree). New public `duho.DefaultsFormatter` (append
  `(default: X)`, skipping `None`/`""`/`False`), `duho.ColorHelpFormatter` (ANSI
  section headings + flags, gated on TTY/`NO_COLOR`/`FORCE_COLOR` — byte-identical
  to plain when off), and `duho.ColorDefaultsFormatter` (both composed). All ANSI
  reuses the logging color codes (no `colorama` import). Off by default; plain
  help is unchanged.
- **F9** PowerShell completion: a new `duho.completion.powershell(parser)` emitter
  walks the same `CompletionSpec` tree and emits a `Register-ArgumentCompleter
  -Native` script block resolving the subcommand path to flags/subcommands/choices
  (file completion falls through to PowerShell's defaults). `"powershell"` is
  added to the `--print-completion` choices and to `duho.print_completion`. A new
  `_psq` helper applies PowerShell single-quote doubling so a hostile choice can
  neither break out of the script nor be expanded.

### Documentation
- Documented that **negative numbers** work as values out of the box (option
  values and positionals) via argparse's `_negative_number_matcher`, with the
  `NS(kwargs=...)` escape hatch for the rare `-1`-style flag; added a regression
  test. (Plan 04 rejected "negative-number handling" as a feature.)
- Documented using an **`enum.IntEnum` as exit codes** — an `IntEnum` return from
  `__call__` propagates as the process exit code unchanged (it is an `int`); added
  a test. (Plan 04 rejected "exit-code enum" as a feature.)

### Performance
- **P1** `importlib.metadata` is now imported lazily, inside `_resolve_version`'s
  `_version_ = duho.AUTO` branch, instead of at module top. A plain
  `import duho` no longer pays its ~20-30 ms cost; only a class that opts into
  `AUTO` triggers the load, at parser-build time.
- **P4** `colorama` is now imported lazily on first use (a named color spec such
  as `"red"`/`"red+white"`), not at `duho.logging` import. `import duho` no
  longer pays colorama's ~3-5 ms when it is installed; built-in level colors are
  hard-coded ANSI and never need it.
- **P2** duho no longer AST-parses its own `args.py` on every parser build:
  `Args`/`Cmd`/`Cli` seed an empty `_duho_constants_` class attribute so the
  class-body scan short-circuits for framework base classes.
- **P3** The qualname walk in `_introspect._module_index` now recurses only into
  statement containers (class/function bodies, `if`/`for`/`while`/`with`/`try`
  clauses) instead of every AST node, cutting the per-file walk time ~30x.
- **P5** `getclsdef` returns `None` immediately when a file's module index was
  built successfully but the class qualname is absent (a dynamically-created
  class), skipping a redundant `inspect.getsource` re-parse that would fail
  anyway. The REPL/`exec` no-module-file case still uses the fallback.

Combined, P1-P5 cut fresh-process `import duho` from ~75 ms to ~51 ms and
end-to-end import+build+parse from ~90 ms to ~55 ms, and the cold 10-subcommand
tree build from ~41 ms to ~10 ms (min, reference machine).

### Changed
- **P6** Benchmark harness upgraded so the wins stay visible: fresh-process
  startup deltas (`benchmarks/bench_startup.py`), subcommand-tree scaling and a
  field-type matrix (`benchmarks/run.py`), a command-discovery benchmark
  (`benchmarks/bench_discovery.py`), a committed `benchmarks/baseline.json` with
  `update_baseline.py`/`check_baseline.py`, and a CI regression gate (fails at
  >1.5x on warm-metric medians / >1.3x on startup deltas). `compare_cache.py`
  output is re-labelled cold-vs-warm (the cold path is what real invocations
  pay). All benchmark tooling is stdlib-only and stays excluded from the sdist.

### Fixed
- `CMDS_PATH` command-search-path resolution now splits on the platform path
  separator (`os.pathsep` — `;` on Windows, `:` on POSIX; overridable via a
  `PATHSEP` env var) via the new `Env.paths()`. Previously it split on a
  hard-coded `:`, so on Windows an absolute path's drive-letter colon (`C:\…`)
  was mis-split into a bogus `C` entry (`ImportError: not a directory: C`).
  `Env.list()`'s generic `:` default is unchanged.
- Building a parser for a bare framework base class used directly as a root
  (`duho.app(root=None)` builds `Args._parser_()`) no longer persists
  `_parsername_` onto the shared `Args`/`Cmd`/`Cli` base. Previously that name
  leaked via inheritance to every subclass, so a later `app(root=None, ...)`
  mis-derived subcommand names (`invalid choice: 'Deploy' (choose from 'Args')`).
  Surfaced by F6's plugin-only apps, which commonly run with no explicit root.
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
- **C13** `duho.snakecase` lower-cases interior upper-case letters with an
  underscore (`CamelCaseName` -> `camel_case_name`) instead of dropping them, and
  returns `""` for empty input.
- **C14** `duho.value_sources` compares against each field's *effective* default
  (so an undeclared-default `store_true` left off the CLI is `"default"`, not
  `"cli"`) and merges subcommand parsers' provenance up to the root, so a
  config-supplied subcommand field is labeled `"config"`.
- **M9** `logging._getcolor` resolves the documented `"fore+back"` syntax and
  returns `""` (never the raw compound string) when colorama is absent or a name
  does not resolve.
- **M10** `_parser_(name="alias")` no longer permanently writes `_parsername_`
  onto the class; the alias is a one-off.
- **M11** Source is read as UTF-8, and `UnicodeDecodeError`/`ValueError` are
  caught so non-ASCII source under a non-UTF-8 locale no longer crashes.
- **M12** The `_CollectionAction` sidecar (`_duho_items_<dest>`) is dropped before
  instance construction, so it no longer leaks into `vars(instance)`.
- **M16** `_suppress_inherited_defaults` keeps a child's deliberately overridden
  default (a re-declared field with a different default) instead of discarding it.
- **M18** A non-literal class-body expression resets docstring attribution (no
  misattribution to the previous field), and a class whose source can't be located
  emits a one-time debug diagnostic.
- **M19** `QualName.relative_to` with an empty base returns the name unchanged
  instead of dropping the first part.
- **M22** A module command's `success` hook runs only on a successful exit (not
  for a non-zero exit code), and a raising `finally_` no longer masks the original
  exception.
- **C12** The zsh emitter emits valid multi-flag optspecs
  (`'(-v --verbose)'{-v,--verbose}'[option]'`), rebuilds the command path from
  non-option words, and drops the dead `_describe` call.
- **M2** Completion scripts escape every interpolated value: bash word lists
  neutralise command substitution (a hostile choice like `$(...)` no longer runs
  at Tab-press), zsh/fish single-quoted contexts escape embedded quotes, and a
  program name with whitespace/metacharacters is rejected.
- **M8** The bash emitter skips the value following a value-taking flag when
  reconstructing the command path (`myapp --env prod deploy <TAB>` now completes).
- **fish** Single-dash multi-char flags are emitted with `-o` (old-style) rather
  than `-s`, and a subcommand's `-d` description is its one-line help.

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

[Unreleased]: https://github.com/jose-pr/duho/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/jose-pr/duho/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/jose-pr/duho/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/jose-pr/duho/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/jose-pr/duho/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/jose-pr/duho/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jose-pr/duho/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jose-pr/duho/releases/tag/v0.1.1
[0.1.0]: https://github.com/jose-pr/duho/releases/tag/v0.1.0
