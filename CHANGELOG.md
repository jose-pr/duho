# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`duho.finish_parse(namespace)`** — builds the real command instance from a
  `Namespace` produced by attaching a duho subparser to a plain, hand-built
  argparse root (the documented "manual subparsers" recipe).
- **`duho.parsers`** gains four public functions: `find_subparsers`,
  `strip_subparsers`, `unique_subcommands`, `command_name` — the shared
  subparser-lookup/alias-dedup walk previously duplicated by hand across
  several modules.
- **`duho.discovery`** gains two public functions: `import_from_path(base_name,
  path)` (unique-name-guaranteed, mtime-cached file import) and
  `unregister_command_provider(predicate, builder)` (the counterpart to
  `register_command_provider`, for test isolation / plugin reload).
- **[minor]** `duho.mcp` gains two public exception classes,
  `UnknownToolError` and `InvalidArgumentsError` (both `ValueError`
  subclasses carrying a JSON-RPC `.code` of `-32602`). `call_tool` now
  raises `UnknownToolError` for a tool name that does not resolve, and
  `InvalidArgumentsError` when `arguments` is not a JSON object or fails
  the tool's own schema — previously a malformed request like this was
  returned as an ordinary `isError: true` tool result indistinguishable
  from the dispatched command's own failure. A problem in the dispatched
  command itself (a raised exception, a non-zero exit, `sys.exit`) is
  still reported as an `isError: true` tool result, not an exception.
- **`duho.completion.spec(parser, prog=None) -> CompletionSpec`** — public
  completion-spec builder (bash/zsh/fish/PowerShell already shared its
  private predecessor). An `Enum`-typed field now offers its member names as
  completion candidates. An option or subcommand hidden via
  `help=argparse.SUPPRESS` is no longer offered by any of the four completion
  scripts.
- `duho.print_completion`, `duho.parser`, and `duho.parse` accept an explicit
  `prog=` override (`parser_kwargs={"prog": ...}` for `parser`/`parse`).
- `duho.command()` gained an optional `module=` keyword-only override (the
  caller's module is now inferred correctly by default; only needed to
  override it explicitly).
- `LoggingArgs` gained long flag spellings `--verbose`/`--quiet` alongside
  `-v`/`-q`.
- `Meta` gained `flags=` and `default=` fields — typed equivalents of
  `NS(flags=...)`/`NS(default=...)`.
- `duho.value_sources` reports a new `"instance"` source for a field that came
  from an instance passed to `duho.parse`. `duho.parse_globals` accepts a
  `config=` keyword argument, mirroring `duho.parse`/`duho.main`.
- A module command's own declared fields (a module-level `Args` class) now
  support `NS(conflicts=...)` (mutually-exclusive groups) and
  `NS(group=...)` (titled groups) — previously only a class command's fields
  could use either.
- `Cli.subcommand`/`command()`/`duho.parse`/`duho.parse_globals`/`duho.parser`
  are now generic under a type checker: they return the caller's own
  class/type instead of widening to `Args`/`Cmd`/`Any`. No runtime change.
- CI now runs on Python 3.14 in addition to 3.9–3.13, with a `black --check`
  formatting gate and a job that installs the built wheel and verifies
  `py.typed`/`README.md`/`AGENTS.md` reached it.
- `benchmarks/results/` is no longer gitignored, so saved results can be
  committed; `benchmarks/README.md` documents the result schema and reproduce
  commands. `bench_discovery.py` now also supports `--save`/`--json`.

### Changed

- **`pyproject.toml`** now declares `license = "MIT"` /
  `license-files = ["LICENSE"]` (PEP 639) instead of the legacy
  `license = {file = "LICENSE"}` table plus classifier; the built wheel's
  metadata now carries `License-Expression: MIT`. The wheel's file layout is
  unchanged, but it now relies on hatchling's own `src/`-layout
  auto-detection instead of a pinned package list.
- The release workflow's test gate now reuses the test workflow in full
  instead of a narrower copy, so the coverage/benchmark/shell-completion
  gates run on every release. GitHub Pages deploys are now owned entirely by
  the docs workflow (including on a published release); the release workflow
  only gates on a strict docs build.
- `DUHO_TRACEBACK=n`/`=f` are now read as "off" (they used to
  enable tracebacks) — the traceback-toggle env var now shares its
  truthy/falsy token table with every other boolean in the framework.
- `Env.bool` now strips whitespace before matching and shares its truthy
  table with the rest of duho, instead of a separately hand-kept copy.
- `bool` is no longer accepted as a CLI text factory wherever it
  appears as a `Literal` member, a `Union` member, or a `list`/`set`/`tuple`/
  `dict` element/value type — such a value now parses through the same
  strict token table as a plain `bool` field and the env/config layer.
- A `list[T]`/`set[T]`/`tuple[T, ...]`/`dict[str, V]` element or
  value type now goes through the full type ladder instead of being
  converted raw: an `Enum` element matches by NAME, a `date`/`datetime`/
  `time` element parses ISO text, and a `Literal` element enforces its
  declared choices.
- A `Literal` composed into a multi-member `Union` (e.g.
  `Union[Literal["auto"], int]`) now enforces its declared choices before
  falling through to a later member.
- An env/config value that cannot convert without losing information (a
  fractional `float` into an `int` field, a `list`/`dict` into a scalar
  field, a `bool` into a non-bool field) now raises instead of silently
  truncating, stringifying, or passing through unchanged. A `Union[int, str]`
  (or similar) field's config value that cannot losslessly become its `int`
  member now falls through to its `str` member instead (`1.5` -> `"1.5"`,
  not the previous silently truncated `1`). A config value fed into a
  `pathlib.Path`-typed field is now converted to a `Path` instead of staying
  an unconverted `str`/`int`.
- A conversion error from an enum/dict/`Literal`/`Union` field now shows
  duho's own message on the CLI (e.g.
  `invalid choice: 'PURPLE' (choose from RED, GREEN, BLUE)`) instead of
  argparse's generic error or a raw object repr.
- An annotation the type ladder doesn't recognize now either works correctly
  (`frozenset[str]`) or fails with a clear message naming the field at
  parser-build time (an unsupported generic, a PEP 695 `type X = ...` alias,
  a `typing.NewType`, or `Annotated`/`Arg[...]` nested inside a `Union`),
  instead of silently misconverting values or crashing with an unrelated
  error.
- `datetime`/`time` fields now accept a trailing `Z` (RFC 3339's
  UTC marker) on Python 3.9/3.10 too, matching `duho.mcp`'s `format:
  date-time` hint, which already advertised it on every version. A `date`
  field never accepts a trailing `Z` on any version (a date has no time/UTC
  component). A basic, no-dash format (`20240101`) works for `date`/`datetime`
  fields on Python 3.11+ (native `fromisoformat` accepts it there); it
  remains unsupported on 3.9/3.10.
- An `Enum` field's CLI value now matches against
  `enum_cls.__members__`, so a declared alias name is accepted, and a `Flag`
  composite member's name is accepted on Python 3.11+ too (previously
  rejected there).
- A `Literal` of specific `Enum` members (e.g. `Literal[Color.RED,
  Color.BLUE]`) now resolves an input by member NAME, matching the
  `{RED,BLUE}` metavar it already advertised (a name previously raised).
- A `list`/`dict[str, V]` option's first CLI occurrence now REPLACES a
  class/env/config/instance default instead of appending/merging onto it,
  matching how `set`/`tuple` fields already behaved and restoring the
  documented CLI > instance > env > config > class-default ladder. Repeated
  flags still accumulate as before. A `duho.Append()`-marked field is
  unaffected — it always accumulates onto its default, by design.
- `duho.Extend()` no longer discards a field's declared default when the
  flag is absent, and now composes with the field's own declared element
  type and collection kind instead of forcing a list-only action:
  `Arg[list[int], Extend(",")]` now yields ints, and `Extend()` on a
  `set`/`tuple` field now produces that collection.
- The documented `NS(nargs="*")` escape hatch on a `list[T]`
  option now actually restores space-separated multi-value input
  (`--x a b` -> `['a', 'b']`). A `list[T]` field made positional via
  `NS(flags=(...))` now correctly accepts multiple values too.
- A `bool` field that can receive `True` from an env var or a
  config file now also gets a `--no-*` flag, so it can be turned back off
  from the command line — previously only a `True` class-level default got
  one.
- A `bool` field defaulting to `True` whose flag already starts
  with `--no-` (or carries an explicit `metavar=`) no longer crashes parser
  construction on Python 3.14+; on every version such a field is now a plain
  `--no-<name>` flag instead of a confusing `--no-<name>`/`--no-no-<name>`
  pair.
- A variadic (`nargs="*"`) `list[T]` positional restricted via `Choice(...)`/
  `NS(choices=...)` can be omitted again on every supported Python version;
  an invalid value on such a field now shows duho's own message rather than
  argparse's.
- `--print-completion`/`duho.print_completion` now register the completion
  script under the invoked command name (`sys.argv[0]`'s stem) instead of the
  root class's name, when no `_parsername_`/`duho.app(name=...)` was
  declared.
- A field named `help`, `version` (when `_version_` is set), or
  `print_completion` (when `_completion_` is set) now raises a build-time
  `ValueError` naming the collision instead of silently being dropped from
  the CLI.
- **[minor]** A class's derived subcommand name is no longer written back
  onto the class; a subclass that does not declare its own `_parsername_`
  always gets its own class name now, even after a sibling/base's parser has
  already been built.
- **[minor]** `app()`'s internal subcommand-dispatch dest is renamed from
  `command` to a private `_duho_command_`; `instance.command` no longer
  exists as a side effect of dispatch. A root field literally named
  `command` is no longer silently overwritten.
- `--loglevel`'s metavar/help now show the real `[NAME:]LEVEL[,...]`
  grammar; `-v`'s help text is now "Increase verbosity (repeatable)".
  `parse_loglevels` is now case-insensitive, accepts a plain integer, strips
  whitespace, and raises a clear error naming the bad token instead of
  silently dropping it. **[minor]** code that fed it deliberately malformed
  input now gets a parser error (exit 2) instead of a silent no-op.
  `--loglevel app:LEVEL` now applies to the whole `app.*` logger subtree,
  including a descendant logger (e.g. `app.cli`) that already had its own
  explicit level set.
- Log output (`init_stderr_logging`, and the default handler `duho.main`/
  `duho.app` install) is no longer unconditionally ANSI: color now follows
  the same `NO_COLOR`/`FORCE_COLOR`/TTY rule the `--help` formatters already
  used, and `colorama.just_fix_windows_console()` is called when colorama is
  installed and color is enabled.
- `add_logging_level` now immediately refreshes the `-v`/`-q` verbosity
  table (previously only `init_stderr_logging` did); the table's displayed
  alias per level now prefers the canonical stdlib name (`WARNING`) over a
  deprecated one (`WARN`). It also refuses (`ValueError`) to silently replace
  an unrelated existing `logging`/`Logger` attribute (`force=True` still
  allows a deliberate override).
- `init_stderr_logging` is now idempotent (a repeat call skips adding a
  second handler); `duho.main`/`duho.app` now call it unconditionally rather
  than only when the root logger has no handlers yet — a caller managing its
  own logging entirely should pass `setup_logging=False`.
- **[minor]** `duho.logging`'s attribute-forwarding `__getattr__` no longer
  answers for dunder/private names (e.g. `__path__`, `__version__`); public
  stdlib `logging` names still forward normally.
- `LoggingArgs` now seeds `_duho_constants_` and declares each field's flags
  directly as metadata instead of an AST scan of its own source — duho's own
  `-v`/`-q`/`--loglevel`/`--verbose`/`--quiet` keep their documented shape
  even when duho ships without `.py` source (a PyInstaller build, a
  `.pyc`-only install, Nuitka).
- **(security)** `Env.paths` (and `duho.app`'s `CMDS_PATH` resolution) no
  longer treats an empty/whitespace-only path-list segment as the current
  directory; it is dropped instead. An explicit `"."` segment is still
  honored.
- A stale/missing/non-directory `CMDS_PATH` entry is now logged at WARNING
  and skipped instead of crashing every invocation; the other entries are
  still used.
- **(security)** **[minor]** The `CMDS_PATH` separator can no longer be
  overridden by an unprefixed, process-wide `PATHSEP` env var; use the
  app-prefixed `<PREFIX>PATHSEP` instead. The override must be a single
  character other than `/`, `\` or `.`, and while it is in effect every
  segment must be absolute or explicitly relative (`.`, `./dir`); a segment
  such as the `C` left by splitting `C:\tools` on `:` is rejected instead of
  being resolved against the current directory.
- `Env.__init__` no longer autoloads a companion module for an empty prefix,
  or for a prefix whose normalized form has a character outside
  `[A-Za-z0-9_]`. Its companion-module autoload now only swallows the
  companion module's own absence — an error raised by code inside an
  existing companion module now propagates instead of silently dropping
  every shipped default.
- `del env[key]`/`env.pop(key)`/`env.clear()` on an `os.environ`- or
  companion-default-backed key now behaves consistently (an in-object
  tombstone; `os.environ` itself is never mutated).
- A module command's entrypoint (`main`/`run`/`call`) and lifecycle hooks
  (`register`/`init`/`success`/`finally_`) are no longer satisfied by an
  imported callable of the same name; only a callable defined in the module
  (or listed in its `__all__`) counts.
- **(security)** `CmdBuilder` on a package directory now verifies the
  qualname actually resolves (via `sys.path`) to that directory before
  importing it, instead of silently wrapping a different same-named package
  found elsewhere on `sys.path`. `CmdBuilder` on a loose file always uses a
  private namespace now, so it can never register under a real module's bare
  name.
- **(security)** `discover_commands`/`duho.app(source=...)` on a bare
  package name now prefers an importable package over a same-named directory
  relative to the current working directory.
- **(security)** `discover_commands("")` and `duho.app(source="")` now raise
  `ValueError` instead of silently scanning and importing every file in the
  current working directory. `"."` still means the current directory
  explicitly; so does `Path("")`, because Python normalises it to `Path(".")`.
- A class whose name starts with `_` is no longer discovered/listed as a
  runnable subcommand. A package's own module command is now named after the
  package, not `--init--`. An entry point's advertised name is now used only
  when the loaded module declares no `_parsername_` of its own.
- Overriding a subcommand (via `CMDS_PATH`, an explicit `commands=`/
  `source=`/`entry_points=` override, or two independently-resolved commands
  sharing a name) now also deregisters every alias of the command it
  replaces.
- The "CMDS_PATH overrides a built-in" notice is now logged once, at INFO,
  after `app()` has actually configured logging; a genuine collision between
  two independently-resolved (non-`CMDS_PATH`) commands still warns.
- `commands=`/`source=`/`entry_points=` passed to `app()` have always ADDED
  to a root's own declared `_subcommands_` rather than replacing them; this
  is now documented and tested (only the documentation was previously
  misleading).
- RunPath step ordering is now a proper stable topological sort; a step
  reordered by `REQUIRED`/`BEFORE`/`AFTER` can no longer jump past unrelated
  later steps.
- `RunPathCmd.__call__` used to always return `0` regardless of step outcomes
  (0.5.4 ignores every step's return value entirely); a step returning a
  non-zero `int` is now a failure, and the command's own exit code ranks
  every step's code by magnitude (the same aggregator `duho.fanout` uses), so
  a negative step code (e.g. a signal-killed subprocess) is no longer
  silently hidden behind an earlier or later `0`. A resilient run whose steps
  all failed non-fatally now returns non-zero instead of always `0`. A step
  returning a non-zero code under the default strict mode logs the failure,
  still runs `__main__.py`'s `finally_` hook, and returns that step's own
  code. Breaking
  an unresolved dependency cycle now forces through the lowest-ranked step
  that is actually part of the cycle, not merely the lowest-ranked stuck step
  overall. A disabled duplicate step file no longer claims a step name ahead
  of an enabled file of the same name, regardless of file-listing order; two
  enabled files sharing a name still raise the existing duplicate error.
- **[minor]** The `__main__.py` lifecycle now runs `success` before
  `finally_` (previously the reverse), gated on no failure; `finally_`
  errors are logged and swallowed rather than replacing the real step error.
- **[minor]** Disabled/deselected RunPath step files are no longer imported
  (their module body no longer executes); only enabled, selected steps are
  imported.
- **[minor]** `REQUIRED` is now a real hard dependency: a dependency that
  ran and failed (or failed to import) now also skips the dependent step,
  following the dependent's own strict setting — previously it was
  ordering-only.
- **[minor]** `register(base=<a Cli app root>)` no longer lets a RunPath
  command inherit the root's unrelated `_subcommands_`/`_version_`/etc., or
  replace the step runner's own `__call__`.
- `duho.mcp`'s `initialize` now negotiates the client's `protocolVersion`
  against a small supported set instead of echoing back any value verbatim.
  **[minor]** a "namespace" tool (a node whose own subcommand is mandatory)
  is no longer listed by `describe_tools`/`tools/list`; its own fields are
  instead merged into every one of its descendants' `inputSchema`.
- `duho.mcp` command dispatch now goes through one shared, cached, layered
  root parser per root class, so env/config values, root globals,
  passthrough args, and `LoggingArgs` verbosity setup all reach an
  MCP-dispatched command exactly as they would under `duho.main`/
  `duho.parse`. `serverInfo.version` now reports duho's own version.
  Arguments are validated before anything runs: a value that would make the
  parse select a different subcommand than the tool names, a counting flag
  above 10, an array or object over 1000 items, a missing required property
  or a value outside the schema's `enum` is rejected with a JSON-RPC `-32602`
  error (`InvalidArgumentsError` from `call_tool`), and a request whose parse
  still resolves to another command is refused as an `isError` result. A field
  name declared at several levels is set only at the deepest one.
- **(security)** An app's own import-time output (e.g. a stray top-level
  `print`) can no longer corrupt the MCP stdio protocol stream; stdio is
  isolated before the target app is even resolved.
- **(security)** An invalid-UTF-8 request line no longer kills the MCP
  stdio server process on a UTF-8 stdio (the default on Linux and macOS); it
  is now answered with a JSON-RPC parse error (`-32700`) and the server keeps
  running.
- `store_false` and `argparse.BooleanOptionalAction` bool fields (including
  one whose `True` comes from an env/config layer) now round-trip correctly
  over MCP — previously such a field could be inverted (a client asking for
  `false` could set it `true`, or vice versa).
- A dict field with a custom `type=` override (e.g. `LoggingArgs.loglevels`,
  `--loglevel app=DEBUG` style) now works when called over MCP instead of
  always failing with an invalid-value error.
- Logging emitted by a command dispatched over MCP is now captured and
  reported; each call's own log output is isolated to that call.
- A `nargs="+"` positional with a declared default is now correctly
  published as a required property in the tool's schema (it was previously
  advertised as optional).
- A dispatched command's non-zero `int` return now includes any captured
  stderr text in the `isError` result, not just an "exit code: N" line.
- A JSON-RPC *batch* request (a JSON array of request objects) is now
  dispatched element by element instead of crashing the server; an
  empty batch array gets its own `-32600` error.
- Fan-out's default `aggregate` now ranks a negative exit code
  (e.g. a signal-killed subprocess) as a failure instead of a plain `max`
  hiding it behind a succeeding target. On Python 3.12+, `duho.fanout`'s
  per-target `[<target>]` log prefix no longer mutates the shared
  `LogRecord` in place, so a handler on the same logger that carries no
  filter no longer sees the prefix too; the record attribute the prefix
  filter sets is also renamed to `_duho_target_tagged_` (private; no known
  external reader). On Python 3.9–3.11 the stdlib gives a `Filter` no way to
  substitute a per-handler record, so an unfiltered sibling handler still
  sees the prefix there, same as before.
- `pysafe` now always produces a valid identifier. Output is unchanged for
  every input that already produced one; inputs that produced an invalid
  identifier now differ — e.g. a leading digit is prefixed (`"1abc"` →
  `"_1abc"`) and a result that is a keyword gets a trailing underscore
  (`"!"` → `"not_"`).
- `snakecase` no longer emits a doubled underscore after a separator
  (`snakecase("My-App")` was `"my__app"`, is now `"my_app"`).
- `expand`/range validation now raises `ValueError` for a reversed,
  mismatched-kind, multi-character, or mixed-case range instead of silently
  yielding nothing or an ASCII-punctuation walk; the range expansion is
  computed iteratively, removing a recursion-depth limit on large ranges.
- **[minor]** `text.range`/`text.unicode_range` are no longer on
  `duho.text.__all__` (still reachable as `duho.text.range`/
  `duho.text.unicode_range`), so `from duho.text import *` can no longer
  shadow the builtin `range`.
- `QualName.relative_to`'s `ValueError` message is now a readable sentence
  instead of a raw tuple/list repr. `PythonName.new()` called on a subclass
  now returns an instance of that subclass.
- `duho.scaffold.generate_launchers` now validates `app`, `libdir`, and
  `python` (ASCII, no shell/batch metacharacters, no path traversal),
  raising `ValueError` instead of silently producing a broken or
  command-injectable launcher. The generated POSIX launcher now clears
  `CDPATH` around its `cd` calls.
- Shell completion: zsh completion now works below the root command at any
  depth (previously root-only) and no longer errors on a command with a
  positional argument. bash, PowerShell, and zsh no longer mistake a
  positional value for a subcommand name. `--opt=value` now completes
  correctly in bash. **(security)** a choice/subcommand-name value
  containing shell metacharacters (`$(...)`, `;`, a backslash, a single
  quote) can no longer execute or corrupt the script when zsh or fish
  actually complete it. fish's per-command gating no longer leaks a nested
  subcommand's flags/names into its parent, or root names/flags after a
  subcommand is chosen. A free-value option no longer offers the
  surrounding flags/subcommand names as its value in bash and PowerShell.
  `Path`-typed options/positionals get real native file completion in bash
  and PowerShell. PowerShell candidates containing whitespace or a
  metacharacter are now inserted as one quoted literal, and matching is now
  case-sensitive. A non-ASCII completion choice no longer gets mangled by
  the console's code page under the documented PowerShell install one-liner.
  The generated bash function name is now namespaced/hashed so two programs
  with similar names no longer redefine each other's completion function.
  bash no longer merges every completion choice after one containing a `'`
  or `"` into a single mangled candidate; `--opt=` with nothing typed yet no
  longer falls back to filename completion. **(security)** PowerShell now
  always single-quotes an inserted completion candidate, closing a
  break-out via Unicode "smart quotes" (U+2018-U+201B) that the previous
  ASCII-only quoting check missed; a completion choice containing `#`/`@` is
  also now quoted instead of inserted bare. PowerShell no longer skips an
  earlier, already-typed word that happens to repeat the text of the word
  currently being completed, and can now dispatch to a subcommand name that
  itself needs quoting. A positional hidden via `help=argparse.SUPPRESS` no
  longer shifts every later positional's completions one slot early; a
  hidden subcommand's alias is now hidden too (previously only its primary
  name was). zsh now completes correctly for a node that has both its own
  positional and a subcommand table (previously the subcommand table was
  never reached); zsh sibling subcommands whose names sanitize identically
  (e.g. `a-b`/`a_b`) no longer collide on the same generated shell function;
  zsh can also now dispatch to a subcommand name that needs quoting. fish no
  longer leaks a shallower, same-named subcommand's flags into a deeper one
  (e.g. root `run` vs. nested `db run`).
- **[minor]** Agent-help JSON (`--help-agents`, `AGENT_HELP=1 --help`) and
  the human `--help` output no longer show a live environment- or
  config-file value as a field's default. They show the field's declared
  (class) default, plus — only when the shown value actually came from env
  or config — a value-free provenance note instead of any value (JSON: a new
  `"default_source"` key, e.g. `"env DEPLOY_TOKEN"`/`"config"`; human help:
  `"(from env DEPLOY_TOKEN)"`/`"(from config)"` in place of
  `"(default: X)"`). This also now covers a module command's own
  env/config-bound fields.
- `DefaultsFormatter` now also skips the `(default: X)` suffix for an empty
  `list`/`tuple`/`set`/`frozenset`/`dict` default.
- `ColorHelpFormatter`'s colored help no longer misaligns columns on Python
  3.9–3.13 (ANSI escape bytes were being counted toward column width) and no
  longer nests its own ANSI codes around argparse's native color on 3.14+.
- **[minor]** An agent-help document's `"type"` string is now identical on every
  supported interpreter: a union renders as `"int | None"`, and a generic renders
  with its arguments whether it is spelled `list[str]` or `typing.List[str]`
  (`"list[str]"`, `"dict[str, int]"`, `"tuple[int, ...]"`; Python 3.9/3.10
  previously dropped the arguments of a bare `list[str]`). A `Literal` argument
  value is shown with Python repr quoting (e.g. `Literal['x, y', 'z']`) instead of
  losing its quotes, which previously made a comma-containing value
  indistinguishable from multiple separate literal members.
- The synthesized "minimal invocation" example in agent-help now always
  includes `<command>` when the app has subcommands, and prefers a long
  (`--flag`) spelling over a short one when both are declared. A
  subcommand-scoped agent-help document now reports the app's own
  `version`/`exit_codes`, not the subcommand's, and scopes its examples to
  the current subcommand.
- Agent-help JSON and human `--help` output no longer raise a
  `UnicodeEncodeError` (empty stdout, exit 1) when piped/redirected on
  Windows with a non-ASCII character in a docstring or field help. Agent-help
  JSON is now ASCII-escaped and written as raw UTF-8 bytes; human help is
  written using the stream's own encoding with a lossy fallback so an
  unencodable character is escaped rather than crashing the whole document.
  `duho.scaffold`'s CLI reports each written launcher path the same
  crash-proof way.
- An agent-help document's `"usage"` field is now always plain text, even
  when argparse's own native color (3.14+) is active.
- **[minor]** `Meta` no longer accepts `dest=` (raises `TypeError`; it was
  silently ignored before — a field's dest is always its declared name).
- `duho.Arg` is now `typing.Annotated` via a real
  `from typing import Annotated as Arg` (the same object at runtime; fixes
  `Arg[...]` failing under mypy). No runtime behavior change.
- `duho.main`/`duho.app`'s documented return type is now `Any`, not `int`
  (both always returned whatever the selected command returned; the
  annotation was simply wrong before). Not `object` either: an intermediate
  `object` annotation broke `sys.exit(duho.main(App))` under a strict-mypy
  consumer, since `sys.exit` does not accept `object` — `Any` is honest
  about the pass-through while keeping `sys.exit(...)` clean.
- `import duho` no longer imports `duho.completion`/`shlex` or
  (transitively, via `duho.discovery`) `importlib.util`/`pkgutil` eagerly;
  all three now load on first actual use, same as `json`/
  `importlib.metadata` already did. No API change.
- Internal (no public API change): `src/duho/args.py` is split into
  `src/duho/args.py` (`Args`/`Cmd`/`Cli`/`ArgumentBuilder` and public
  functions), `src/duho/_fieldspec.py` (the type-to-argparse-spec ladder),
  and `src/duho/_layers.py` (the env/config/instance layering pipeline).
  Every existing PUBLIC import path is unchanged; the names the two new
  modules need from each other are re-exported from `args.py` (a private
  helper that lived on `args.py` before the split is not guaranteed to still
  be reachable there — it may have moved to `_fieldspec.py`/`_layers.py`).
  `duho.mcp`'s JSON-Schema ISO-format type map is now derived from a single
  shared source instead of a second, independently hand-kept list.
- `bench_startup.py`'s gated end-to-end timing metrics now measure from a
  real temporary `.py` file instead of `python -c`, so they actually
  exercise duho's source-reading path; the old `-c`-based number survives as
  a separate, informational metric and is not comparable to the new one.
  Cold-build benchmarks no longer delete a seed that duho's own base classes
  pre-populate, which previously inflated every "cold" sample by re-parsing
  duho's own source; any previously quoted cold/warm ratio predates this
  fix. `compare_cache.py`'s cold-vs-warm summary line direction is corrected
  (it previously read backwards).

### Fixed

- `duho.value_sources` no longer merges sibling subcommands' provenance
  together, no longer reports a stale parser inherited via the MRO for a
  class that was never itself parsed, and a subcommand instance's report now
  also includes its inherited root/global fields (previously omitted).
- An env/config value for a `Literal`/`Choice(...)` field is now validated
  the same way a CLI value is. A class-level `_config_` file that doesn't
  exist yet is now skipped (with a debug log) instead of crashing every
  invocation; an explicit `config=` kwarg stays strict. An env/config/
  instance-supplied value is no longer run through a field's type factory a
  second time. `duho.parse(instance)` no longer treats a field
  `Args.__init__` seeded as a placeholder as an explicit override that would
  outrank env/config.
- An env/config value now correctly satisfies a required mutually-exclusive
  group, and is dropped in favor of a sibling given on the CLI instead of
  coexisting with it. An invalid env/config value belonging to an unselected
  subcommand no longer crashes the whole invocation.
- A bad env or config value is now reported the same way a bad
  CLI value is — usage text and exit code 2 — instead of a raised
  `ValueError`; catch `SystemExit` instead. An env var set to the empty
  string is now treated as unset, except for a field whose values are plain
  strings (`str`, `Optional[str]`, or a `Literal`/`Choice` of strings), which
  keeps `""`.
- **(security)** An env/config value that fails conversion is never echoed:
  the error names the variable (or config key), the field and the expected
  type only. This also holds when the field's own `type=` raises
  `argparse.ArgumentTypeError`, `KeyError` or any other exception; 0.5.4
  printed such a value in a traceback.
- A config file whose top level is not a table/object now raises a clear
  `ValueError` naming the file (a custom `_config_loader_` returning `None`
  means "no config"). `duho.app()` now threads env/config layering into a class
  command's own nested `_subcommands_` tree, and into a module command's
  declared `Args` class fields. `duho.app()` now honors a subcommand's
  deliberately redeclared default for a root field, and — for a command
  reached through `commands=`/`source=`/`entry_points=`/`CMDS_PATH`
  discovery — now accepts a required global option given after the
  subcommand. A statically declared `_subcommands_` tree is unaffected: a
  required root option there must still come before the subcommand name.
  Under `duho.app()`, a required root global that this relaxation makes
  internally optional now still displays as required (no `[...]` brackets)
  in `--help`/usage text, instead of the enforcement and the displayed usage
  disagreeing with each other.
- A set[T]/list[T] positional given zero tokens no longer crashes or
  duplicates its declared default. `duho.Append()` on a `set`/`tuple` field
  now raises a clear error at parser-build time instead of crashing at parse
  time. A mutually-exclusive member with no explicit `required=`/default no
  longer fails the parser build. Declaring the same conflicts= key under two
  different group titles now raises a clear build-time error.
- A parser built once and reused across multiple `parse_args()` calls no
  longer shares (and, on mutation, leaks through) the same mutable
  list/set/dict default object between those calls; a directly constructed
  instance with a mutable class-level default now gets its own copy.
- `Args.__init__` now seeds EVERY declared field's effective default onto a
  directly constructed instance when the caller didn't pass it (not only
  fields with an explicit class-level default) — a `store_true`/`store_false`
  bool or any other field whose default only used to materialize via
  argparse now gets the same attribute surface as a parsed instance. This
  changes `repr()`/`==` for a directly constructed instance built with fewer
  keyword arguments than the class declares fields.
- An `Optional[T]` positional with no explicit default is no longer
  required. A non-required option with no declared default now reports
  `None` as its effective default on a directly constructed instance. On
  Python 3.9/3.10, a `bool` field defaulting to `True` no longer has a
  spurious `(default: %(default)s)` appended to its help text (this
  previously also defeated `NS(help=argparse.SUPPRESS)`).
- The flag/positional reorder pass now also recognizes an attached
  short-option value (`-fVALUE`) and an unambiguous long-option prefix, and
  now also covers a single variable-arity positional with no sibling
  positional. `Count()`/`store_const`/`append_const`/`store_false` fields
  with no declared default are no longer mandatory options. `const=`/
  `version=` supplied through `NS(kwargs={...})` is no longer ignored by
  build-time checks. `NS(nargs="*")` on a `dict[str, V]` field no longer
  crashes.
- A class source file saved as UTF-8 with a byte-order mark, or one using a
  non-UTF-8 coding cookie, no longer silently loses every field's flags/
  aliases/docstrings. The same class name declared twice in one file (an
  `if`/`else` version guard, a `try`/`except ImportError` fallback) now uses
  the branch Python actually ran. A private field's unresolvable type
  annotation no longer crashes parser build for the whole class. A subclass
  that redeclares only an inherited field's flags (or only its help text)
  now correctly keeps the base's help text (or flags) instead of losing it.
- A PEP 695 type alias wrapping `Annotated`, and `Optional[Arg[T, NS(...)]]`
  or any `Union` with exactly one `Annotated` member, now resolve and apply
  their metadata instead of crashing; a `Union` with more than one
  `Annotated` member now raises a clear error naming the field.
- `duho.app`'s advisory `register` prepass no longer fails silently when the
  app root already declares built-in `_subcommands_`; a module command's
  `register(parser, args)` hook now receives the real parsed globals instead
  of `None` in that shape. `duho.app` no longer prints `--version` twice,
  concatenates two `--print-completion` scripts, or shows a spurious
  "arguments are required" error before a module subcommand's own `--help`.
- `duho.parsers.disable_subparser_check`/`enable_subparser_check` are now
  safe to call in a nested pair on the same action. `insert_action` now
  defaults to appending (previously inserted before the last action) and
  re-adds the action to its help group; `pop_action` now also removes an
  action from any mutually-exclusive groups it belonged to, and no longer
  leaves a stray `SUPPRESS` key on the parsed namespace when the subparsers
  action had no explicit `dest=`.
- On Python 3.14, `Env.bool`'s own parameter/return annotations no longer
  resolve to the `Env.bool` method itself. Directory-based discovery now
  supports a `_helpers.py` sibling-import convention. Repeated
  discovery of an unchanged command file reuses the already-imported module
  instead of re-executing it. A module command's hooks now get the `"duho"`
  fallback logger even when the root has no `LoggingArgs`-based logger.
- A literal `%` in a field docstring, a module command's docstring, or a
  resolved `--version` string no longer crashes parser build or `--help`/
  `--version`. `_help_formatter_` now styles the whole subcommand tree
  (previously only direct children). A subcommand that subclasses its own
  root no longer recurses forever. `_version_ = duho.AUTO` now resolves
  correctly under `python -m pkg`, is cached per distribution name, and
  retries a normalized distribution name on Python 3.9.
- A custom `Argument` type's own parsing logic is no longer bypassed when
  the field also carries `NS(...)`/`Meta(...)` metadata. `parse_intermixed_args`/
  `parse_known_intermixed_args` now raise a clear error instead of crashing
  or silently returning an unusable result. An async command's result is
  now driven to completion whether or not it is a native coroutine object;
  an async-generator result now raises a clear error; calling from inside an
  already-running event loop now raises a clear error. The agent-help JSON
  document now expands `%(default)s`-style placeholders in help text instead
  of copying the raw template, and no longer corrupts a description
  containing a literal `%%`. `duho.command()`-built classes now carry the
  caller's own module.
- A register hook's `ArgumentError` is now only reworded as "collides with a
  global flag" when the option string actually belongs to the root's global
  options. A register hook with a keyword-only `logger` parameter is now
  called correctly; hook arity detection now inspects a
  `functools.wraps`-decorated hook's own signature. A module command's
  hook returning a coroutine is now closed immediately and raises `TypeError`
  naming the hook. Passing a non-command object in `app(commands=[...])` now
  raises `TypeError` naming the bad object.
- A `--rcopts` entry carrying an unrecognized token no longer silently
  forces that step strict. Duplicate RunPath step names are now detected
  (warn/error, naming both files). `REQUIRED`/`BEFORE`/`AFTER` given as a
  bare string is now normalized to a single-element list with a warning; a
  non-integer `PRIORITY` now names the offending step/file. An `async def`
  RunPath step or `__main__.py` hook now raises `TypeError` immediately
  instead of silently never running. A `step_adapter` shim wrapped with
  `functools.wraps` is now inspected on its own signature. Assorted
  filename/pattern-parsing edge cases (a non-decimal Unicode digit prefix,
  an uppercase `.PY` extension, unstripped whitespace in a `--rcopts`
  pattern) are fixed.
- `duho.mcp` tool arguments are now synthesized into argv so a value can
  never be reinterpreted as a flag or the `--` passthrough separator. A
  `bool` field's actual argparse action now drives argv synthesis instead of
  a re-derived guess. `call_tool` now validates `arguments` against the
  tool's own schema before dispatch, and treats a JSON `null` as "field
  omitted". A command's own `sys.exit()`/`SystemExit` no longer kills the
  MCP server process. The stdio JSON-RPC server no longer crashes on a
  malformed-but-valid JSON request. The real stdio transport now isolates
  file descriptors 0/1 from anything a dispatched command does, and forces
  UTF-8/LF on the protocol channel. `ping` is now answered with an empty
  result instead of "method not found". `input_schema_for_command`'s
  `required`/`description` now match what argparse itself enforces, and
  never leak an internal sentinel. `duho.scaffold`'s CLI now reports any
  filesystem error as a one-line message instead of a traceback, and
  self-describes with its own name/version. `python -m duho.mcp -h`/
  `--help` now prints usage instead of failing.
- Fan-out: Ctrl-C now cancels queued (not-yet-started) targets instead of
  running the whole list to completion first. A target's `SystemExit` is
  normalized into an exit code instead of discarding every other target's
  result. A raising target's failure log now honors `DUHO_TRACEBACK` like
  every other resilient code path. `run_targets`/`fan_out_command` now
  reject a bare `str`/`bytes` target and a non-positive `max_workers` up
  front; an `async def` target callable is now awaited.
- `duho.scaffold`'s generated CLI now prints a one-line error (naming
  `--force`) and exits 1 on a re-run without `--force`, instead of an
  unhandled traceback.

### Removed

- **[minor]** The undocumented `_cli_name` alias for `_parsername_` (module-
  level override on a command module). Only `_parsername_` is honored now.
- `benchmarks/bench_parsing.py` (superseded by `run.py`, which already
  reports the same metrics as min/median/max instead of a single average).

## [0.5.4] - 2026-08-16

### Fixed
- **`duho.__version__` now matches the released version.** It had been left at
  `0.5.1` while `pyproject.toml` went to `0.5.2` and then `0.5.3`, so an
  installed `duho==0.5.3` reported `0.5.1` from a public, documented attribute.
  `tests/test_version_sync.py` now fails whenever the two disagree.
- **`Env.bool` accepts `on`.** The layered env/config bool converter has always
  taken `on`, so a variable spelled `ON` read as `True` through a declared
  field and silently as `False` through `Env.bool`. The truthy set is now
  `1`/`true`/`yes`/`y`/`t`/`on` in both places; they still differ in strictness
  only (`Env.bool` treats an unrecognized value as `False`, the layered
  converter raises).

### Changed
- Corrected two stale docstrings: `RunPathCmd._runpath_logger_` documented a
  pre-0.5.3 `"duho"` fallback (it returns the `duho.runpath` module logger;
  `ModuleCommand._logger_for`'s own `"duho"` fallback, handed to user hook
  code, is unaffected), and `LoggingArgs._verbose_loglevel_` documented
  returning a log level name when it returns the numeric level. No behavior
  change.
- Removed a dead `aborted` flag from `RunPathCmd.__call__`; a strict step
  failure already raised before the later `not aborted` guard could run it.
  No behavior change.

## [0.5.3] - 2026-08-05

### Added
- **`DUHO_TRACEBACK` — full tracebacks for swallowed framework failures.** duho's
  resilient paths deliberately log-and-continue (a command file that fails to
  import is skipped, a non-strict RunPath step that raises is logged and the run
  goes on, a fan-out target's exception fails only that target), which means the
  log line is the only record of the failure — and a one-line message says *that*
  something broke, never *where*. Set `DUHO_TRACEBACK` to a truthy value and every
  such site logs full `exc_info` instead. Off by default (`0`/`false`/`no`/`off`/
  empty also count as off), re-read per call so it can be exported for a single
  run. Behavior is unchanged either way — this only controls logged detail.

  New public helpers in `duho.logging`: `traceback_enabled()` and
  `log_exception(logger, msg, *args, level=ERROR)`, plus the `TRACEBACK_ENV`
  constant. Wired into `runpath` (step import + step run + `init` failure),
  `discovery` (skipped command module/file, failed entry point), `runtime`
  (the advisory `register` prepass, previously swallowed with no log at all), and
  `mcp` (a tool exception previously reached the client as a bare `Type: message`
  string with no server-side stack).

### Changed
- **Framework log records now carry their own module's logger name** —
  `duho.runpath`, `duho.discovery`, `duho.runtime`, `duho.fanout`, `duho.mcp`
  instead of a flat `duho` — so a handler or `--loglevel duho.discovery:DEBUG`
  can target one subsystem. All remain children of `duho`, so
  `--loglevel duho:DEBUG` is unchanged.

  This affects records the framework emits *as itself*. Records tied to a
  running command are unchanged: they still go through the command's own
  `_logger_`, named after its parser. A RunPath's per-step messages, for
  instance, keep logging under the directory name (`steps`), not
  `duho.runpath` — which is the fallback only for a bare `RunPathCmd` with no
  `LoggingArgs` mixin. Likewise a module command's hooks and a 3-arg
  `register(parser, args, logger)` still fall back to the plain `duho` logger,
  never to a framework-internal module logger.
- **Redundant source prefixes removed from log messages** — a record logged
  under `duho.runpath` no longer also begins with `"duho.runpath: "`, and the
  per-step case no longer reads `steps: duho.runpath: running step boom`, where
  the prefix actively contradicted the logger name. The `ValueError` messages
  that share their text with a log line keep their prefix (an exception carries
  no logger name to identify it).

## [0.5.2] - 2026-08-03

### Added
- **`duho.runpath.register(step_adapter=...)`** — an app-supplied callable
  applied to each step's entrypoint just before it runs
  (`adapter(entrypoint) -> callable`). It lets an app accept step signatures of
  its own rather than duho's `(cmd)`/`(cmd, ctx)` — an app whose module
  commands take `run(client, args, logger)` can let steps be written that way
  too, without a decorator in every step file. The *adapted* callable is what
  arity detection inspects, so a wrapper may change the signature; returning
  the entrypoint unchanged leaves duho-native steps alone. Pass `None` to clear
  it, omit the argument to leave it unchanged. The default is `None`, which
  calls steps exactly as before. Unlike `base`, it is consulted per step run,
  so it also affects already-built commands.

## [0.5.1] - 2026-07-24

### Fixed
- **A module whose `__file__` doesn't exist on disk (e.g. a zipapp, where
  `__file__` is a zip-internal path) now still recovers its declared
  flags/positionals and docstrings** instead of silently losing them and
  falling back to name-derived option flags. `getclsdef` tried its
  file-index lookup and the `inspect.getsource` fallback inside a single
  `try`, so an `OSError` from the first (a nonexistent path) skipped the
  second, which would have worked fine (`inspect.getsource` reads zipimport
  modules via the loader's `get_source`). The file-index `OSError` is now
  caught narrowly so the fallback still runs. (GH #1)

## [0.5.0] - 2026-07-24

### Changed
- **BREAKING: `list`/`set`/`tuple` fields used as an OPTION no longer accept
  space-separated multi-value in one occurrence.** `--x a b` used to
  accumulate `["a", "b"]` in one flag occurrence (`nargs="*"`); an option
  field now defaults to ONE value per occurrence (`nargs=None`) — repeat the
  flag for more (`--x a --x b`), matching how `dict` fields already work.
  The POSITIONAL case is unaffected (still `nargs="*"`, space-separated —
  that's the whole point of a trailing variadic positional). Pass an
  explicit `NS(nargs="*")` on a specific option field to restore the old
  space-separated behavior.

### Fixed
- **A flag placed between two positionals no longer breaks a trailing
  variadic positional.** argparse's own greedy positional-run matching
  (bpo-15112) settles a run containing a fixed positional followed by a
  variadic one against the argv slice before the NEXT optional token — so
  `<command> <name> -f value <targets...>` used to fail with "unrecognized
  arguments" pointing at the targets, even though `<command> <name>
  <targets...> -f value` and `<command> -f value <name> <targets...>` both
  worked. Duho now detects this shape at parser-build time and transparently
  reorders recognized flags ahead of the positional run before the real
  parse; a genuinely unrecognized/misspelled flag still raises argparse's
  own honest error, never silently absorbed as a phantom positional value.
  (This fix is what makes the `list`/`set`/`tuple`-as-option default change
  above safe and necessary: `nargs="*"` on an interspersed option is itself
  ambiguous even in ALREADY-correctly-ordered argv — confirmed against bare
  stdlib argparse — so the reorder fix could not have covered that case
  regardless; downgrading the option default to `nargs=None` removes the
  ambiguity at its source instead.)
- **The reorder fix above now also applies to module command subparsers.**
  A module command's subparser (`discover_commands`/`ModuleCommand`, built
  via a plain `subparsers.add_parser(...)`) was never patched with the fix —
  only duho's own declarative `Cmd`/`Args` subcommand tree was. This meant
  `<module-command> <positional> -f value <targets...>` still raised
  "unrecognized arguments" on a module command even though the identical
  shape already worked on a declarative subcommand. Module command
  subparsers now get the same detect-and-reorder treatment once all of
  their fields (declared `Args` + any `register()`-added arguments) are in
  place.
- **A field whose name is identical to its own type annotation (e.g.
  `bool: bool = False`) now raises a clear `TypeError` at class-definition
  time** instead of silently corrupting later argparse behavior. Python's
  own class-body execution order stores the field's value BEFORE the
  annotation expression is evaluated, so the name shadows itself within the
  same statement — this is unfixable at the annotation-reading level; duho
  now detects the symptom and raises with an actionable message naming the
  field, rather than the confusing crash it produced before.

## [0.4.1] - 2026-07-24

### Added
- **Module commands can declare their own `Args` class.** Previously a
  module command's fields could only be added imperatively via `register()`
  — a module-level `Args` class was silently ignored. A module may now
  define `class Args: ...` (or `class Args(SomeSharedRoot): ...`, the
  common convention) with annotated fields; they're added to the
  subparser declaratively, BEFORE `register()` runs (so a `register`-added
  positional still lands last, e.g. a shared trailing-positional helper).
  If the module's `Args` doesn't already subclass the app's root class, it's
  mixed with it on the fly so the module's own fields work AND the parsed
  instance still carries the root's shared fields/methods. Does not (yet)
  support `NS(conflicts=...)`/`NS(group=...)` for these declared fields —
  use `register()` for those.

### Fixed
- **`CMDS_PATH` (`env=`) is now a layer, not a mutually-exclusive branch.**
  `app`'s command-set resolution used to return early for an explicit
  `commands=`/`source=`/`entry_points=`, so `CMDS_PATH` was only ever
  consulted in the fallback `root._subcommands_` branch — passing any other
  source silently disabled `CMDS_PATH` entirely, even with `env=` also
  given, with no warning. `env`-discovered commands now always merge on top
  of whichever base source produced the list (a discovered command still
  wins on a name clash, still logged, never silent).
- **A wrapped `command.register` on a module command is no longer silently
  skipped.** `app()`'s module-command registration gated and introspected
  arity on a fresh `getattr(module, "register", ...)` re-fetch instead of
  `command.register` (the object actually called) — so a caller that wraps
  or reassigns `command.register` directly (a documented-looking seam) saw
  its wrapper silently skipped for any module defining no `register` hook
  of its own (`module.register` is `None` there → not callable → gate never
  passed). Now gated/introspected on `command.register` itself.
- **`Env` companion-module defaults no longer override the real environment.**
  `Env`'s autoloaded `<prefix>env` companion module seeds genuine defaults
  (lowest precedence) instead of shadowing a real exported
  `<PREFIX>_<KEY>` variable — the class docstring already called these
  "defaults"; the implementation now matches. Precedence: `**env` kwargs /
  a runtime `env[k] = v` write, then `os.environ`, then the companion
  module.

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
  `duho.agenthelp` collects; `agenthelp` itself is untouched).
  `describe_tools(root_cls)` walks the built parser tree (reusing
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

### Changed
- Documented that **negative numbers** work as values out of the box (option
  values and positionals) via argparse's `_negative_number_matcher`, with the
  `NS(kwargs=...)` escape hatch for the rare `-1`-style flag; added a regression
  test.
- Documented using an **`enum.IntEnum` as exit codes** — an `IntEnum` return from
  `__call__` propagates as the process exit code unchanged (it is an `int`); added
  a test.
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
- **P6** Benchmark harness upgraded so the wins stay visible: fresh-process
  startup deltas (`benchmarks/bench_startup.py`), subcommand-tree scaling and a
  field-type matrix (`benchmarks/run.py`), a command-discovery benchmark
  (`benchmarks/bench_discovery.py`), a committed `benchmarks/baseline.json` with
  `update_baseline.py`/`check_baseline.py`, and a CI regression gate (fails at
  >1.5x on warm-metric medians / >1.3x on startup deltas). `compare_cache.py`
  output is re-labelled cold-vs-warm (the cold path is what real invocations
  pay). All benchmark tooling is stdlib-only and stays excluded from the sdist.
- **BREAKING** `duho.Env.list` returns `[]` for a missing or empty
  value instead of the previous `[ty("")]` single-empty-element contract.

### Fixed
- `CMDS_PATH` command-search-path resolution now splits on the platform path
  separator (`os.pathsep` — `;` on Windows, `:` on POSIX; overridable via an
  app-prefixed `<PREFIX>PATHSEP` env var, e.g. `MYAPP_PATHSEP` — never a bare/
  global `PATHSEP`, which used to let any unrelated process's `PATHSEP`
  bypass this for every duho app on the system) via the new `Env.paths()`.
  Previously it split on a hard-coded `:`, so on Windows an absolute path's
  drive-letter colon (`C:\…`) was mis-split into a bogus `C` entry
  (`ImportError: not a directory: C`). `Env.paths()` also rejects a bare
  drive-letter segment (`C:`) and any other segment that resolves to the
  current working directory unless it is spelled exactly `.`, and drops an
  empty/whitespace-only segment instead of treating it as the current
  directory. `Env.list()`'s generic `:` default is unchanged.
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

## [0.3.3] - 2026-07-18

### Changed
- Internal tidy-up (no behavior change): removed unused imports (`typing` in
  `completion`, `argparse` in `presets`, `stat` in `scaffold`) and an orphaned
  dead helper (`_zsh_value_spec`) plus its unused-result callers in `completion`.
  The `from logging import *` under `TYPE_CHECKING` in `logging` re-exports
  stdlib logging names for type checkers and is retained.

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
- Zero required runtime dependencies. Optional extras: `colorama` (colored
  logging) and `config` (TOML on Python 3.9/3.10, where `tomllib` isn't stdlib).
- Supports Python 3.9 through 3.13.

[Unreleased]: https://github.com/jose-pr/duho/compare/v0.5.4...HEAD
[0.5.4]: https://github.com/jose-pr/duho/compare/v0.5.3...v0.5.4
[0.5.3]: https://github.com/jose-pr/duho/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/jose-pr/duho/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/jose-pr/duho/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/jose-pr/duho/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/jose-pr/duho/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/jose-pr/duho/compare/v0.3.3...v0.4.0
[0.3.3]: https://github.com/jose-pr/duho/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/jose-pr/duho/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/jose-pr/duho/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/jose-pr/duho/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jose-pr/duho/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/jose-pr/duho/releases/tag/v0.1.1
[0.1.0]: https://pypi.org/project/duho/0.1.0/
