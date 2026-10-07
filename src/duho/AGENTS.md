# `duho` — public API header

Header-file-style reference for the `duho` package: every public export with
its signature, arguments, contract and gotchas, so the package can be used
without reading its source. It ships inside the package and is
self-contained. Development documentation lives with the source at
<https://github.com/jose-pr/duho>.

Public names are the `__all__` exports of `duho` and of its public modules, each named
in a section heading below; import them from there (`from duho import Cmd, main`,
`from duho.fanout import run_targets`). A module or file whose name starts with `_`, and
the private `_*.py` submodules inside the `args`, `completion`, `discovery`, `mcp`,
`runpath` and `runtime` packages, are not API.

Install with `pip install duho`; it has no required dependencies. Two extras add optional
integrations: `pip install duho[colorama]` (`colorama>=0.4.6,<0.5`) resolves named log colors and
patches a legacy Windows console for ANSI output (log color itself needs no extra), and
`pip install duho[config]` (`tomli>=2.0,<3`, only below Python 3.11) reads TOML config
files on Python 3.9 and 3.10 (3.11+ has `tomllib`; JSON config files need nothing).

`duho.__version__` is the package's own version as a string.

The package is typed (it ships `py.typed`), and every annotation on a public callable
resolves with `typing.get_type_hints` on every supported Python, 3.9 included.

`import duho` never eagerly imports `json`, `importlib.metadata`, `duho.completion`,
`shlex`, `duho.agenthelp`, `importlib.util`, or `pkgutil` (a tested contract) — each
loads lazily on first actual use. Keep any addition here that would break that lazy.

## Module layout

Purely informational (no API surface of its own) for someone reading the installed
source tree; every name below is still reachable as `duho.*`/`duho.args.*` etc.
regardless of which internal module implements it:

- `args/` — `Args`/`Cmd`/`Cli`/`ArgumentBuilder` and most module-level public
  functions (`parse`, `main`, `command`, `print_completion`, ...). `completion/`,
  `discovery/`, `mcp/`, `runpath/` and `runtime/` are likewise packages of private
  `_*.py` submodules whose `__init__` re-exports every name.
- `_fieldspec.py` — the type-to-`ArgumentBuilder` ladder (`int`/`bool`/collections/
  `Enum`/`date`-like/`Literal`/...); exposes `Factory` (a `Callable[[str], T]` type
  alias for a text-to-value converter).
- `_layers.py` — the env/config/instance/CLI value-layering pipeline; implements
  `value_sources`.
- `_introspect.py` — AST-based class-body introspection (docstrings, flag literals);
  exposes `NOT_DEFINED`, a sentinel meaning "no declared default" (distinct from `None`).
- `_compat.py` — cross-version shims (the truthy/falsy token tables are
  `duho.text.BOOL_TRUE`/`BOOL_FALSE`).

## Declaring commands (`duho`, `duho.args`)

- **`Args`** — base declarative data class. Annotated non-`_` class attrs become CLI
  fields; an adjacent string literal is help text, an adjacent tuple literal is the flag
  set. The help is the first run of consecutive string literals after the field, joined
  with one space, and may stand before or after the flag tuple; a run that is a single
  flag-shaped string (`"--env"`, a one-element tuple written without its comma) is a
  build-time `ValueError`. A flag set is written `("--env","-e")`; a single dash-less
  entry such as `("src",)` makes a positional named after the field. A field with no declared flag tuple at all is an option with
  one long flag, `"--" + kebabcase(field_name)` (`duho.text.kebabcase` — see "Text /
  names" below), required when it has no default: `dry_run` → `--dry-run`,
  `testMe` → `--test-me`, `HTTPPort` → `--http-port`. This is the DEFAULT
  flag only — an explicitly spelled flag, the field's own attribute/dest name, and any
  config/env key are never touched. Inside a DECLARED flag tuple, an entry that is
  exactly `"--"` expands to that same default long flag: `("--",)` → `("--dry-run",)`,
  `("-n", "--")` → `("-n", "--name")`; any other entry passes through unchanged. A tuple
  containing `"--"` more than once is a build-time `ValueError` naming the field. Not
  runnable on its own. Classmethods:
  - `_parser_(subparser=None, name=None, parents=(), **kw) -> ArgumentParser` — build
    this class's (sub)parser.
  - `_initparser_(parser, is_subcommand=False, parent_dests=None, explicit_prog=False, agent_root_cls=None, external_config=False)` —
    populate an already-created parser with this class's fields. `explicit_prog` is
    accepted and unused. An override must accept `**kwargs` and forward them to
    `super()._initparser_(...)`: `_parser_` passes build context by keyword
    (`external_config` today) and may pass more than the signature lists.
  - `_getargs_() -> list[ArgumentBuilder]` — this class's resolved field specs (cached).
  A `list[T]`/`set[T]`/`tuple[T, ...]` field used as a POSITIONAL keeps `nargs="*"`
  (space-separated: `prog a b`); used as an OPTION it defaults to `nargs=None` — ONE
  value per flag occurrence, repeat the flag for more (`-f a -f b`), not
  space-separated in one occurrence (`-f a b`) — pass an explicit `NS(nargs="*")` to
  restore space-separated collection on a specific OPTION field. An option placed BETWEEN
  two positionals (one variable-arity) parses correctly, where argparse's own greedy
  positional-run matching would trip on it; a genuinely unrecognized flag still raises
  argparse's own error.
- **`Cmd(Args)`** — executable `Args`. Override **`__call__(self) -> int | None`** (the
  entrypoint; `None` → exit 0; `__call__` may be `async def`, driven to completion with
  `asyncio.run` at the dispatch site). Base `__call__` raises `NotImplementedError`
  naming the class.
- **`Cli(Cmd)`** — application-root mixin. Declares (as typed sandwich attrs) `_version_`,
  `_distribution_`, `_completion_` (default `False`), `_config_`, `_subcommands_` (default
  `None`), `_utf8_stdio_` (default `True` — see "Output encoding" below), `_mcp_`
  (default `True`), `_mcp_command_` (default `False`). Adds no run behavior of its own.
  Self-registration: `@Root.subcommand` (a `Cli` classmethod) attaches a child to the
  root's `_subcommands_`; it calls `Cmd._register_subcmd_(child)`, which every `Cmd` has.
  Copy-on-write per class — never mutates a parent's list; registering a class twice is
  a no-op. Recommended base order `class App(LoggingArgs, Cli)`.
  **Gotcha**: `subcommand` is `Cli`'s one reserved plain attribute name — a CLI field
  also named `subcommand` silently replaces the `@Root.subcommand` decorator instead of
  raising; avoid that field name on a `Cli` subclass.
  A subclass's derived `_parsername_` is never written back onto the class: each
  subclass gets its own name from its own class name unless it declares `_parsername_`
  itself (a base class's build never leaks its derived name to a subclass). A class with
  no OWN `_parsername_` is named with the KEBAB-CASE (`duho.text.kebabcase`) of its class
  name, not the exact class name: `BuildPyz` → `build-pyz`, `ShowHTTPStatus` →
  `show-http-status`. An explicit `_parsername_`, a `_parseraliases_` alias, and a
  discovered MODULE command's own file-stem name (`_` → `-`, unrelated to a class) are
  never kebab-cased — only a bare class-name fallback is.
  **The application's name** — one name, used for the root parser's `prog` (the usage
  line), the `--print-completion` script, the `<NAME>_MCP` launch variable, the root
  segment of every MCP tool name and `serverInfo.name`, and the default logger `-v`/`-q`
  raise. In order: `app(name=...)`, the root class's own `_parsername_`, the top-level
  package the root class is defined in (also under `python -m pkg`; `__main__` and
  `duho` itself never count), then the kebab-case of the class name — so a single-file
  script run directly is named after its class. It never comes from the script's file name:
  `python app.py`, `python tools/run.py`, `python -m pkg` and a console script all agree.
  A root that must keep a fixed name wherever it lives declares `_parsername_`.
  `duho.app(root=None)` builds on duho's own `Args` and so is named `args`; pass `name=`.
- **`subcommand(parent)`** (`duho`, `duho.args`) — decorator factory: `@duho.subcommand(Tools)
  class Build(duho.Cmd)` registers `Build` on any `Cmd` group `parent` (it need not be a
  `Cli`) and returns the class unchanged; idempotent. `Cmd` itself has no `subcommand`
  attribute, so a field of that name stays legal on a plain `Cmd`.
- **Default subcommand** — `_default_subcommand_ = "name"` (a name or alias; declared on
  `Cmd`, default `None`) on a group, nested ones included. After the `--` tail is split
  off, parse scans the group's own argv slice, skipping its registered options, their
  attached values and exactly one separate value for a one-value option; if the first
  other token is not a registered subcommand name or alias, the default name is
  inserted before it (`tool -v bob` → `tool -v resolve bob`). It does nothing, so the
  subcommand stays required, when no such token exists or when a `--`, an unregistered
  option, a variable-arity option or a missing value comes first. Name an unknown
  subcommand, or set it on a class with no subcommands, and the parser build raises
  `ValueError` naming the class.
- **`command(args_cls, func, *, name=None, module=None) -> type[Cmd]`** — build a `Cmd`
  subclass from a data `Args` + a callable; the built `__call__` calls `func(self)`.
  `name` sets `_parsername_`. `module=` overrides the built class's `__module__`
  (default: the caller's own module) — needed so discovery's module-boundary filter
  finds the class and `_version_ = duho.AUTO` resolves the caller's distribution, not duho's.
- **`Argument`** — `runtime_checkable` protocol for custom field types; implement
  classmethod `_argbuilder_(name, decl, factory=None) -> ArgumentBuilder` to customize
  how a field's `ArgumentBuilder` is built. **`ArgumentMeta`** is the metaclass backing
  `Argument`'s duck-typed `isinstance` check (any object with a callable
  `_argbuilder_` satisfies the protocol — no explicit subclassing needed). The hook is
  also used for a type that is a member of an `Optional`/`Union` or the element of a
  `list`/`set`/`tuple`/`dict` field; there only the builder's `type` (the factory),
  `choices` and `metavar` are taken, and `decl` has no default, annotations or docstring.
- **`ArgumentBuilder`** — the per-field spec (flags + `add_argument` kwargs). `_effective_default_()`
  gives the argparse-normalized default (e.g. an implicit `False` for a bare `store_true` flag).
- **`ClsArgDeclaration`** — what the class-body introspection records for one field and
  passes to `_argbuilder_` as `decl`: a dataclass with `default` (`NOT_DEFINED` when
  undeclared), `type`, `annotations` (the `Arg[...]` metadata), `docstring` and `exprs`
  (the literal statements that follow the field).
- **`Factory`** — type alias `Callable[[str], T]` for a field's text-to-value converter.
- **`NOT_DEFINED`** — sentinel meaning "this field declares no default" (distinguishes
  "no default" from an explicit `None` default).

**Reserved field names** — a field named `help`, `version` (when a `--version` flag
resolves), or `print_completion` (when `_completion_ = True`) raises a build-time
`ValueError` naming the collision (a field's `dest` is always its declared name — there
is no `dest=` override; `NS(kwargs={"dest": ...})` is the raw `add_argument` escape
hatch). `subcommand` on a `Cli` subclass is a *different*, non-raising gotcha — see
above.

### Class attributes

Every sandwich-named attribute duho reads. `Cli` declares the root ones as typed
attributes; each is also honoured through `getattr`, so a plain `Cmd` (or any class) may
set it. A "root" attribute is read on the class `main`/`app` was called with; a
"command" attribute on each command class.

| Attribute | Type | Default | Applies to | Effect |
| --- | --- | --- | --- | --- |
| `_version_` | `str \| duho.AUTO \| None` | `None` | root | adds `--version`; `AUTO` reads installed package metadata; with `_version_` unset, a class-level `__version__` string is used instead |
| `_distribution_` | `str \| None` | `None` | root | distribution name for `AUTO` when it differs from the import package |
| `_completion_` | `bool` | `False` | root | adds `--print-completion {bash,zsh,fish,powershell}` |
| `_config_` | `str \| Path \| None` | `None` | root, command | config file layered under env and CLI; a path that does not exist yet is skipped |
| `_config_loader_` | `Callable[[Path], dict] \| None` | `None` | root, command | reads the config file instead of the built-in JSON/TOML dispatch; its own exceptions propagate unchanged, a non-mapping result is a usage error naming the file |
| `_config_env_` | `str \| None` | `None` | root | name of an environment variable holding the config file's path; outranks `_config_`, and the file must exist; not applied to a tree served over MCP |
| `_config_field_` | `str \| None` | `None` | root | name of a declared field that holds the config file's path when given on the command line or through its own env var; outranks `_config_env_` and `_config_`, and the file must exist; not applied to a tree served over MCP |
| `_help_formatter_` | `type \| None` | `None` | root, command | argparse `formatter_class`; a root's value propagates to its subcommands |
| `_subcommands_` | `Sequence[type[Cmd]] \| None` | `None` | root, command | the static subcommand tree; nests |
| `_default_subcommand_` | `str \| None` | `None` | root, command | the subcommand inserted when the first token after the group's own options names none of its subcommands; a `ValueError` naming the class at build for an unknown name or a class with no subcommands (see "Declaring commands") |
| `_allow_passthrough_` | `bool` | `True` | root, command | `False` makes a non-empty `--` tail a usage error naming the command (exit 2); no tail, or a bare `--`, still parses |
| `_agent_help_` | `bool` | `False` | root | adds the `--help-agents` flag |
| `_agent_help_env_` | `str \| None` | `None` | root | the one env var that switches `--help` to agent mode, replacing `AGENT_HELP`/`AGENTS_HELP` |
| `_examples_` | `Sequence[str \| tuple[str, str]] \| None` | `None` | root, command | examples in the agent-help document (a command's own, else a synthesized line) |
| `_exit_codes_` | `Mapping[int, str] \| None` | `None` | root | exit-code table merged over the default 0/1/2 |
| `_utf8_stdio_` | `bool` | `True` | root | `main`/`app` call `utf8_stdio()` first |
| `_mcp_` | `bool` | `True` | root; command | on the root, `False` disables the `<NAME>_MCP` launch variable; on any other command, `False` leaves it and its subtree out of the MCP tools (a module command sets it at module level) |
| `_mcp_command_` | `str \| bool` | `False` | root | registers a built-in MCP-serving subcommand (`True` → `mcp`, a string → that name) |
| `_completion_command_` | `str \| bool` | `False` | root | registers a subcommand that prints a shell completion script (`True` → `completion`, a string → that name; no whitespace, not starting with `-`); its one required positional is the shell (`bash`, `zsh`, `fish`, `powershell`) and the script goes to stdout; needs another subcommand and a free name, else `ValueError` naming the attribute; never an MCP tool; read by `main` and `app` (no kwarg) |
| `_parsername_` | `str` | kebab-case class name | command | the subcommand name (and the application's name on a root) |
| `_parseraliases_` | `Sequence[str]` | none | command | extra subcommand names |
| `_runpath_dir_` | `Path \| None` | `None` | `duho.runpath.RunPathCmd` subclass | the directory of `NN-name.py` steps; the provider sets it |
| `_logger_name_` | `str \| None` | the application's name | root, command | the logger `-v`/`-q`/`--loglevel` raise and `self._logger_` returns; a command's own wins over the root's |
| `_base_loglevel_` | `int \| str` | `logging.INFO` | `LoggingArgs` root | the level `-v`/`-q` step from: a number or the name of a registered level (any other value is a `ValueError` at dispatch); a plain `Cmd` leaf under a `LoggingArgs` root uses the root's value; an overridden `_verbose_loglevel_` wins |

**Misspelled attribute warning.** The first time a class's parser is built, each
attribute named with a leading and a trailing underscore in that class's own body that
duho does not read, and whose spelling is very close to exactly one name it does read
(difflib similarity of 0.85 or more), logs one WARNING on logger `duho.args`:
`App declares '_verison_', which duho does not read; did you mean '_version_'?`. It
never raises and has no switch. Only a near-miss is reported: an application's own
attribute that merely extends a known name (a word added to it), a dunder and a name
starting `_duho_` are not.

### Field metadata helpers (use inside `Arg[T, ...]`)

`Arg` is `typing.Annotated` (`from typing import Annotated as Arg`; same runtime object,
so it type-checks correctly) — `Arg[T, NS(...)]` needs ≥2 args; a plain-typed field is
just its annotation.

- **`NS(**kwargs)`** — a bare `argparse.Namespace` alias: an untyped metadata bag. Accepts
  any keyword (`flags`, `help`, `env`, `conflicts=` exclusive-group key,
  `conflicts_required=`, `group=` titled-group name, `metavar`, `nargs`, `action`,
  `const`, `default`, `choices`, `required`, `type`, `version`, `kwargs=` raw
  `add_argument` passthrough, `enum_by`, `literal_value`, …). A key that is not a `Meta`
  field (a misspelling, or `dest`, which is always the field's own name) is ignored, and
  the parser build logs one WARNING per field on logger `duho.args`:
  `App.port: NS(hlep=...) is not a Meta field and is ignored; closest Meta field:
  'help'`. Never an error. Keys set by `Extend`/`Count`/`Append`/`Const`/`Choice` and the
  attributes a custom `ArgumentBuilder` subclass declares are accepted; only
  `argparse.Namespace` metadata is checked, not a plain `dict`.
- **`Meta(help, env, conflicts, conflicts_required, group, action, nargs, const, choices, metavar, required, type, version, flags, kwargs, default, enum_by, literal_value, *, dest)`** — typed, typo-safe alternative to `NS`: a dataclass with exactly the
  same fields as `NS` (`help`, `env`, `conflicts`, `conflicts_required`, `group`,
  `action`, `nargs`, `const`, `default`, `choices`, `metavar`, `required`, `type`,
  `version`, `flags`, `kwargs`, `enum_by`, `literal_value`) EXCEPT `dest` — `Meta` has no `dest` field at all (a
  field's `dest` is always its declared name), so `Meta(dest=...)` is a `TypeError` at
  class-definition time instead of `NS(dest=...)`'s silently-ignored value. `flags=`
  and `default=` are the typed equivalents of `NS(flags=...)`/`NS(default=...)`. Only
  explicitly-set fields are merged; an unknown keyword to `Meta(...)` is a `TypeError`.
  - `type=f` with a user converter `f` (neither a builtin nor from `duho`): a
    `ValueError`/`TypeError` from `f` with a non-empty message is shown as the usage-error
    text (`app: error: argument --port: port must be 1..65535`, exit 2). An empty message,
    a builtin, a `duho` factory and an `argparse.ArgumentTypeError` keep argparse's own
    text. A bad env or config value never echoes the value or the message. Only an
    explicit `type=` is covered, not the annotation itself (`port: SomeClass`).
  - `enum_by` (`"name"` default, or `"value"`) — how an `Enum` field's text is matched.
    `"value"` matches `str(member.value)` on the command line, in env and in config (a
    TOML number `2` matches value `2`); help metavar, error text, completion candidates,
    agent help `choices`/`default` and the MCP schema `enum`/`default` list the value
    text; the parsed field is still the member. Applies to `Enum`, `Optional[Enum]` and
    `list`/`set`/`tuple`/`dict`-value elements, not to a `Literal` of members. Two members
    whose value text is equal (aliases excepted) raise `ValueError` naming the field at
    build time, as does any other `enum_by`.
  - `literal_value=True` (default `False`) — on an option taking exactly one value, the
    token after the flag is always its value, even `--` or `-x`: `--k --` gives `k == "--"`
    and `--k -x -- tail` gives `k == "-x"`, `_passthrough_ == ["tail"]`; `-k -x` works for
    a short flag. A flag, a variable-arity option or a positional with it raises
    `ValueError` naming the field at build time. The option strings of the whole
    subcommand tree are collected once per parser. Not joined: an abbreviated long flag
    (`--ka` for `--k...`), a short flag inside a cluster (`-vk -x`), and an empty value
    after a short flag. `--k=--` works without it; a repeated flag joins once per
    occurrence.
- **`argparse.SUPPRESS`** — placed anywhere in a field's metadata (`Arg[str, argparse.SUPPRESS]`)
  it hides the field from the command line entirely: no flag, no parsed value.
- **`Choice(*choices, **kw)`** — restrict accepted values to `choices`.
- **`Const(value, **kw)`** — `store_const`-action: stores `value` when the flag is present.
- **`Count(**kw)`** — count-action (`-vvv` → `3`).
- **`Append(type=str, **kw)`** — append-action, accumulating one scalar per repeated
  flag occurrence. Raises a build-time `ValueError` on a `set`/`tuple`-typed field
  (argparse's stdlib append action always produces a `list`; a repeatable `set`/`tuple`
  field already accumulates one value per occurrence without it).
- **`Extend(split, **kwargs)`** — split one flag occurrence's text into several
  values (`split` a separator string or a `str -> Iterable` callable). Composes
  with the field's own declared element type AND collection kind — `Arg[list[int],
  Extend(",")]` yields ints, not strings, and `set`/`tuple`-typed fields work too, not
  just `list`. The field's own declared default is kept when the flag is absent and
  replaced — like any other collection option — on the first CLI occurrence.
- **`UpdateAction`** — `-O k=v` style action that merges into a `dict[str, V]` field
  (see the collections gotcha below for its replace-on-first-occurrence rule).
- **`AUTO`** — sentinel for `_version_ = duho.AUTO`: resolves via `importlib.metadata`,
  distribution name overridable by `_distribution_` (else the class's top-level import
  package — resolved correctly even under `python -m pkg`, where `__module__` alone
  would read the useless literal `"__main__"`). Cached process-lifetime per distribution
  name (including a "not found" result). Retries once with `-`/`_`/`.` runs collapsed to
  a single `_` if the verbatim distribution name doesn't resolve, since Python 3.9's
  `importlib.metadata` (unlike 3.10+) does not treat `.` as a name separator.

## Type conversion & collections (`duho.args`)

- `list[T]`/`set[T]`/`tuple[T, ...]`/`dict[str, V]` element/value types go through the
  SAME full type ladder as a scalar field — an `Enum` element matches by member NAME
  (or by value text under `Meta(enum_by="value")`),
  a `date`/`datetime`/`time` element parses ISO text, and a `Literal` element enforces
  its choice set — not just a raw string.
- `date`, `datetime`, `time` fields (scalar or as a collection element) parse ISO
  format text. A trailing `Z` UTC marker (`"2024-01-01T00:00:00Z"`) is accepted for
  `datetime`/`time` on every supported Python version (3.9/3.10 included, via an
  explicit shim; 3.11+ accepts it natively); a `date` value never accepts a trailing
  `Z` on any version (a date has no time/UTC component). A basic, no-dash date
  (`"20240101"`) is accepted for `date`/`datetime` on 3.11+ (native `fromisoformat`)
  but NOT on 3.9/3.10.
- A `list`/`set`/`tuple`/`dict[str, V]` OPTION's first CLI occurrence REPLACES a
  class/env/config/instance default rather than appending to it; a second and later
  occurrence of the same flag then accumulates normally. This applies uniformly across
  all four collection kinds.
- A `bool` field gets a `--no-*` flag (`argparse.BooleanOptionalAction`) whenever it
  could receive `True` from something other than the CLI: a `True` class-level default,
  a declared `env=`, OR the owning class having any `_config_` set at all (not
  necessarily that specific field appearing in the config file).

## Output encoding (`duho.utf8_stdio`)

- **`utf8_stdio(streams=None) -> list[str]`** (`duho._compat`, re-exported at
  the top level) — reconfigure text streams to UTF-8 in place. `streams`
  defaults to `{"stdout": sys.stdout, "stderr": sys.stderr}`; pass a mapping
  of fake streams to target something else (this is how tests exercise it
  without touching the real ones). A stream is left alone when ANY of: (1)
  `PYTHONIOENCODING` is set (non-empty) in the environment; (2) Python's own
  UTF-8 mode is active (`sys.flags.utf8_mode`); (3) it has no
  `.reconfigure()` (pytest's capture, `io.StringIO`); (4) `stream.isatty()`
  is true; (5) its encoding, normalized via `codecs.lookup(...).name`, is
  already `"utf-8"`. Otherwise it's switched via
  `stream.reconfigure(encoding="utf-8", errors=...)` — `"surrogateescape"`
  for the stream named `"stdout"`, `"backslashreplace"` for every other
  name. Never raises (`OSError`/`ValueError` from `reconfigure()` itself is
  swallowed, that one stream left as-is); idempotent (a stream already
  switched matches rule 5 on a later call). Returns the names actually
  switched.
- **`duho.main`/`duho.app` call `utf8_stdio()` FIRST**, before the MCP launch
  trigger and before `argv` is parsed, unless opted out: a root class
  attribute **`_utf8_stdio_ = False`** (declared on `Cli`, default `True`;
  read via `getattr` so any class works), or the `main(..., utf8_stdio=...)`/
  `app(..., utf8_stdio=...)` kwarg (`None` — the default — defers to the
  class attribute; an explicit `True`/`False` wins). Opted out, duho does
  not touch stdio at all; the app may call `utf8_stdio()` itself or do
  nothing. This targets Windows specifically: piped/redirected stdio there
  defaults to the console's ANSI code page (`cp1252`) with strict errors, so
  a non-ASCII character raises `UnicodeEncodeError` (empty output,
  exit 1) from `print()` or `--version`. UTF-8 is the one encoding that
  can't raise, so the default removes that failure mode entirely.
- **Crash-proofing when NOT UTF-8** (opted out, or a duho parser used outside
  `main`/`app` entirely): `--version` is implemented by
  `_Utf8SafeVersionAction` (a subclass of stdlib `argparse._VersionAction` —
  same text/exit code, same `isinstance` recognition anywhere the stdlib
  class is checked for, e.g. `parsers._is_terminal_action`) which writes via
  `write_human` instead of `parser._print_message`, so a non-ASCII
  version/prog string falls back to `errors="backslashreplace"` instead of
  raising. duho's own stderr messages (an unsupported MCP transport, a
  `duho.mcp`/`duho.scaffold` CLI error) are written the same way, not via a
  raw `print(..., file=sys.stderr)`.

## Build / parse / run (`duho`)

- **`parser(cls, *args, **kwargs) -> ArgumentParser`** — delegates to `cls._parser_`. Generic: under
  a type checker, `duho.parser(MyApp)`/`duho.parse(MyApp)`/`duho.parse_globals(MyApp)`/
  `Cli.subcommand`/`command()`'s decorated class all keep the caller's own class/type
  rather than widening to a base type (no runtime behavior change). With subcommands,
  the instance `parse_args`/`parse` returns is the SELECTED LEAF command's: it is typed
  as `cls`, but the chosen subcommand's class need not derive from it.
- **`parse(spec, argv=None, *, parser_kwargs=None, config=None)`** — build+parse in one call. `spec` a
  type → new instance; `spec` an instance → its explicitly-set field values become
  defaults (CLI wins), returns a new `type(spec)` instance, `spec` itself is untouched.
  A field counts as set when it was passed to the constructor or changed since, by
  assignment or by mutating its value in place (`inst.tags.append("x")`).
  Full precedence: **CLI > instance > env > config > class default**. `parser_kwargs`
  forwards to `cls._parser_(...)` (e.g. `parser_kwargs={"prog": "myapp"}`).
- **Every field with a default is materialized.** Whether built by `parse`/`main`/`app`
  or constructed directly (`MyArgs(...)`), an instance carries every field that has a
  default — including one with no explicit `default=` (its effective default, e.g. a
  `store_true`/`store_false` bool's `False`) and one omitted from a direct constructor
  call. A field with no default at all is set by `parse`/`main`/`app` (they require it)
  but stays unset on a directly constructed instance that omits it. `_passthrough_` is
  `[]` on every instance (not shared between them), a parsed instance carries no
  private subcommand-selector attribute, and an optional positional has the same default
  whether the instance was parsed or built directly.
- **`parse_globals(cls, argv=None, *, config=None, **parser_kwargs)`** — parse only the
  root globals, ignoring subcommands (drops the subparsers action before a
  help-suppressed parse). Accepts `config=` mirroring `parse`/`main`. On a root's static
  `_subcommands_` tree only options written before the subcommand name count as globals:
  `--out x` after the name is not a prefix of the root's `--output`, and a same-named
  option after the name belongs to the subcommand.
- **`main(cls, argv=None, *, setup_logging=True, config=None, utf8_stdio=None) -> Any`** —
  build → parse → optional logging setup → run the selected command. Return type is `Any`,
  not `int`: a `None` command result maps to exit code `0`, but any other value the
  command returns passes straight through unchanged (an `IntEnum` member works
  directly as a distinct exit code). `Any` (not `object`) keeps `sys.exit(duho.main(...))`
  type-clean under a strict-mypy consumer. Dispatching a bare data `Args` (no `__call__`)
  raises `NotImplementedError`. When the dispatched leaf is a plain `Cmd` with no
  `_set_loglevels_` of its own but `cls` (the root `main`/`app` was called with) mixes
  in `LoggingArgs`, logging is still set up under the root's own command name — a
  `-v`/`-q`/`--loglevel` on a `LoggingArgs`+`Cli` root is never a silent no-op on a
  plain-`Cmd` leaf. The stderr handler itself is only added when the root logger
  has no handler other than duho's own previously-installed one — an app/harness
  that already owns logging (`basicConfig`, pytest's capture handler) never gets
  a second, unrequested handler; `setter()` (verbosity) still always runs.
  **`utf8_stdio`** (`bool | None`, default `None`): FIRST thing this function
  does, before the MCP launch trigger and before `argv` is parsed, is call
  `duho.utf8_stdio()` unless opted out — `utf8_stdio=False` here, or `cls`'s
  own `_utf8_stdio_ = False` when this kwarg is left `None`. See "Output
  encoding" above.
- **`app(root=None, *, commands=None, source=None, entry_points=None, argv=None,
  name=None, description=None, env=None, config=None, setup_logging=True,
  dispatch=None, mcp=None, mcp_command=None, utf8_stdio=None, on_error=None,
  adapter=None) -> Any`** —
  multi-command runner (return type is `Any`, not `int`,
  for the same reason as `main`: a command's non-`None`, non-`int` return value
  passes straight through). Base command-set precedence:
  `commands` > `discover_commands(source)` > `discover_entry_points(entry_points)` >
  `root._subcommands_` (only when NONE of `commands`/`source`/`entry_points` is given).
  **Additive, not exclusive**: `root`'s own declared `_subcommands_` (and any
  `@Root.subcommand`-registered children) are ALWAYS registered too, regardless of
  what `commands=`/`source=`/`entry_points=` was passed — passing one of those never
  removes a root's own subcommands. `env`'s `env.paths("CMDS_PATH", ty=Path)` (splits on
  `os.pathsep`, overridable only by this app's OWN prefixed `<PREFIX>PATHSEP` — e.g.
  `MYAPP_PATHSEP`, never a bare/global `PATHSEP` — NOT `.list`'s `":"` default — a
  Windows drive letter would otherwise mis-split) is a LAYER that ALWAYS merges on top
  of whichever base command
  set was used; a `CMDS_PATH` command wins a name clash with the base (logged once
  logging is set up, never silent). Overriding a subcommand this way (or any other
  name-clash override) deregisters every alias (`_parseraliases_`) of the command it
  replaces too, not just its primary name. For a command reached through
  `commands=`/`source=`/`entry_points=`/`CMDS_PATH` discovery, a required global
  option is honored whether it appears before OR after the subcommand on the
  command line; a root's own statically declared `_subcommands_` tree (the
  fallback used when none of those is given) is unaffected — a required root
  option there must still come before the subcommand name (a subcommand that inherits
  the root's required option is satisfied by the value given before the name; after the
  name the root still reports it missing). Layers
  env/config defaults onto root + each class command: **CLI > env > config > class
  default** (no "instance" layer here — that only exists for `parse()`). Attaches the
  resolved `Env` as `_env_`. A module command's own declared `Args` fields support
  `NS(conflicts=...)`/`NS(group=...)` the same as a class command's. `dispatch(command,
  instance)` replaces only the final run step; what it returns is what `app` returns
  (typed `Any`, like a command's own return value).
  `source` may be one source or a list/tuple of them (see `discover_commands`).
  `on_error(source, exc)` is passed to discovery (`source=`, `CMDS_PATH`) and is also
  called, with `(command, exc)`, when building one command's parser or running its
  `register` hook raises; that command is dropped. `adapter(entrypoint)` is called
  with each module command's entrypoint in the default run step and returns the callable
  run with the parsed instance in its place (a falsy return keeps the entrypoint); it is
  not applied to `init`/`success`/`finally_` or to a class command, and combining
  `adapter=` with `dispatch=` raises `ValueError` (a custom `dispatch` passes `adapter` to
  `run_command` itself). **No commands**: when none resolves (from `commands=`,
  `source=`, `entry_points=`, `CMDS_PATH` or the root's `_subcommands_`), `app` runs the
  root if it is a `Cmd` (one that never overrides `__call__` raises
  `NotImplementedError`), and otherwise exits `2` with a "no commands are available"
  message. The `register(parser, args)` hook of a module command receives `args`, the
  root instance parsed with its required options treated as optional (a missing one is
  unset or its default), or the root's defaults instance when a global fails to convert;
  `args` is `None` only if the root cannot be built without arguments. The real parse
  still enforces the required options. A malformed or unreadable built-in config file is a
  usage error here as under `main` (see "Env / config").
- **`run_command(command, instance, *, context=None, adapter=None) -> Any`** —
  dispatch one resolved
  command. `adapter(entrypoint)` is the same hook as `app`'s: it applies to a module
  command's entrypoint only. Class command → `instance()` (awaited via `asyncio.run` if it returns a
  coroutine). Module command → `init → main → success` (only on a `None`/`0` result) `→
  finally_` lifecycle — `finally_` always runs, and if it raises, that exception is
  logged and swallowed so it can never mask `main`'s own result or a propagating
  exception. `None` → `0`, an `int` propagates, any other return value passes through
  unchanged. A module hook returning a coroutine is rejected loudly (module lifecycle
  hooks are synchronous-only, unlike a class command's `__call__`).
- `duho.runtime.accepts_positional(func, count) -> bool` (not exported from the root) —
  `True` when `func` declares at least `count` positional parameters (defaults allowed) or
  `*args`; keyword-only parameters do not count. Read from the callable's own signature
  (a `functools.wraps` wrapper is not unwrapped); an unreadable signature gives `False`.
  For writing an `adapter` that adapts by arity.
- **`finish_parse(namespace) -> Args`** — for the manual-subparsers recipe (a duho
  `_parser_(subparsers, name=...)` attached to a hand-built, plain
  `argparse.ArgumentParser()` root): that plain root's `parse_args()` returns a raw
  `Namespace` still carrying an internal `"#cls"` marker instead of a constructed
  instance. Call `finish_parse` once, right after `parse_args`, to build and return the
  real selected `Args`/`Cmd` instance. Raises `ValueError` if `namespace` carries no
  `"#cls"` marker (no duho subparser was ever reached for this invocation).
- **`print_completion(cls, shell, file=None, *, prog=None) -> None`** — print a shell
  completion script for `cls`. `shell` is one of `"bash"`, `"zsh"`, `"fish"`,
  `"powershell"` (an unrecognized name raises `ValueError` listing the valid ones).
  Standalone counterpart to the `--print-completion` flag injected when `_completion_ =
  True` — builds `cls`'s parser tree fresh, independent of whether `_completion_` is
  set. `prog` overrides the command name the emitted script binds to; by default it
  is the application's name (see "The application's name" above), the same one the
  `--print-completion` flag binds, however the program was launched.

`_passthrough_`: on a parsed instance, argv after the first literal `--` (a `list[str]`,
empty when absent).

## Discovery (`duho.discovery`)

- **`Command`** — `runtime_checkable` protocol: `_parsername_` + a runnable body. Two
  kinds: a class command (strict `Cmd` subclass) and a `ModuleCommand`. A discovered
  class command is the class itself and carries `_parsername_` only when it declares
  one, so `isinstance(cls, Command)` is `False` for one that does not and reading
  `cmd._parsername_` on it raises `AttributeError`. Read a resolved command's name with
  `duho.parsers.command_name(command)` (`-> str`), which works for both kinds.
  **`is_class_command(obj) -> bool`** / **`is_module_command(obj) -> bool`** — the
  corresponding type checks (a strict `Cmd` subclass; a `ModuleCommand` instance).
- **`ModuleCommand`** — adapts a command `.py` module (plain wrapper, not a `ModuleType`
  subclass). `_parsername_` = module `_parsername_` override, else file stem with
  `_`→`-`. Entrypoint `main` (fallback `run`/`call`); optional hooks `register`/`init`/
  `success`/`finally_`, held as plain attributes (`cmd.register`, ...) that may be
  reassigned. `cmd.module` is the wrapped module; `cmd.entrypoint` is a read-only property returning
  the resolved entrypoint callable. A module-level `_mcp_ = False` leaves
  the command out of the MCP tools. A module with no entrypoint raises
  `NotImplementedError` (→ skipped).
  `args_cls` — an optional module-level `Args` declaring the module's own CLI fields
  DECLARATIVELY, an alternative to adding everything imperatively in `register`. Either
  a real `Args` subclass (strict, not `Args`/`Cmd` themselves — a bare `from duho import
  Args` with no subclassing is ignored) or a plain class with annotated fields. The
  effective class is resolved as: already a subclass of the app's root class → used
  directly; otherwise synthesized as `type("_Args", (args_cls, root_cls), {})`, so the
  module's fields work AND the parsed instance still carries the root's own
  fields/methods. Its fields are added to the subparser BEFORE `register` runs
  (declared positionals first, register-added ones last), through the SAME field-adding
  routine a class command uses — so a declarative `args_cls` field's `NS(conflicts=...)`/
  `NS(group=...)` metadata is honored too, the same as on a class command. The parsed
  instance stays the ROOT instance either way (declarative fields never install the
  class-command-only `"#cls"` dispatch-patching machinery).
- **`CmdBuilder(qualname, source=None)`** — resolve one source (Path / dotted import path /
  module / Command) to a command. Filesystem imports use synthesized-unique `sys.modules`
  keys (a loose `json.py` never clobbers stdlib `json`).
- **`import_from_path(base_name, path) -> ModuleType`** — import a `.py` file under a
  `sys.modules` key derived from `base_name` (uniquified), reusing a cached module on a
  repeat import of the same file (matched by resolved path + mtime).
- **`discover_commands(source, *, on_error=None, providers=False) -> list[Command]`** —
  walk a package or directory; collects
  both class commands (module-boundary deduped) and one `ModuleCommand` per entrypoint
  module. Result sorted by subcommand name. `source` is a package name, a directory, or
  a list/tuple of them: each is discovered on its own, a same-named command in a later
  source replaces the earlier, `[]` for an empty sequence, and each member keeps the
  empty-source and bare-drive-letter rejections (`ValueError`). A bare string that
  resolves to a directory with no `__init__.py` is scanned like a path (loose files that
  may import one another); one that names a package is imported as one. A command
  file imports a sibling helper by its bare name (`import _helpers`, `from _helpers
  import x`), at module level or inside a function: each directory is one private
  package, so the helper is one module object shared by every command file of that
  directory for the life of the process (its `__name__` is under
  `duho._discovered._dir_<tag>`), is never in `sys.modules` under its bare name, and
  two directories' same-named helpers stay separate. The directory is not put on
  `sys.path`. The standard library, installed packages and any module already
  imported win over a same-named file. `importlib.import_module` does not see
  siblings, and a helper is not reloaded when its file changes. A
  file with an upper-case `.PY` suffix is ignored on every platform. When the scanned
  directory holds the running script (`__main__`), that file is not a command.
  **Resilience**: with `on_error=None`, catches only `(ImportError, NotImplementedError)`
  per command (logs a warning + skips); everything else (e.g. `SyntaxError`) propagates.
  `on_error(source, exc)` replaces that rule: it is called for any exception
  (`SystemExit` too) raised importing one file (`source` is the file `Path`), importing
  one package module (the dotted name), building its commands, or building a provider
  command (the directory); returning skips it, raising aborts discovery. With
  `providers=True` (filesystem sources only, default off) the source directory itself,
  then each child directory not starting `_` or `.`, is offered to the registered
  providers; a match is built with `builder(path, name_with_dashes)`, and a source
  directory a provider claims yields that one command. A module whose `main`/`run`/`call`
  is a decorator-wrapped function defined elsewhere is not a command: discovery logs a
  WARNING on `duho.discovery` naming it; list the name in the module's `__all__` to
  accept it. A plain imported callable is skipped silently.
- **`register_command_provider(predicate, builder)`** — injection seam for directory-shaped
  runtimes; providers (a module-global, newest-first) are consulted before importing a
  filesystem/namespace source. Tests must snapshot/restore.
- **`unregister_command_provider(predicate, builder)`** — counterpart to
  `register_command_provider`: removes the exact `(predicate, builder)` pair
  (matched by equality); a no-op if that pair is not currently registered.
- **`discover_entry_points(group) -> list[Command]`** — enumerate installed entry points (imports
  `importlib.metadata` lazily).

## Env / config (`duho.env`)

- **`Env(prefix, autoload=True, **env)`** — prefixed, typed `os.environ` accessor. Normalizes `prefix`
  (upper, `-`→`_`, trailing `_`), autoloads an optional `<prefix.lower()>env` companion
  module of defaults (missing → silently ignored). Precedence, highest first: `**env`
  kwargs / a runtime `env[k] = v` write, then the real `os.environ`, then the companion
  module's shipped defaults. Methods incl. `.list(name, sep=":", ty=str)`, `.paths(name,
  ty=str, strict=True, on_reject=None)` (splits on `os.pathsep`, overridable only by
  this app's own prefixed `<PREFIX>PATHSEP` env var — never a bare/global `PATHSEP` —
  NOT `.list`'s `":"` default — so a path-list var never mis-splits a Windows drive
  letter; also rejects a bare drive-letter segment and any segment resolving to the
  CWD unless spelled `.`. `strict=False` skips a rejected segment instead of raising
  for the whole call, keeping every other entry; `on_reject(segment, reason)`, when
  given, is called once per skipped segment),
  `.bool(name)` —
  truthy (case-insensitive, stripped) matches the shared `BOOL_TRUE` token set (see
  "Environment variables" below); anything else (incl. a missing key) is `False`. That
  set matches the layered env/config bool converter; the difference is strictness —
  `.bool` returns `False` for an unrecognized value, the layered converter raises.
  Mapping-like (`__iter__`/`__len__`/`**env`).
- **Field env binding** — `NS(env="VAR")` (or `Meta(env=...)`) on a field makes the
  environment variable `VAR` supply its default; the text is converted with the field's
  own type. Layering, highest first: **CLI > instance (`parse` only) > env > config >
  class default**; a value from any layer also un-requires a field with no class default.
- **Config files** — `_config_` on a class (a path; `~` is expanded) or `config=` on
  `parse`/`parse_globals`/`main`/`app` (wins over the attribute) names a file whose values
  become defaults. A path ending `.json` is read as JSON; any other path is read as TOML
  (stdlib `tomllib` on Python 3.11+; on 3.9/3.10 it needs `pip install duho[config]`,
  i.e. `tomli`). Top-level keys map to the root command's fields, and a table (TOML) or
  object (JSON) named after a subcommand's `_parsername_` maps to that subcommand's
  fields (`verbose = true` plus `[install]` with `target = "prod"`). Unknown keys are
  ignored. Every entry point layers env and the class's `_config_` the same way:
  `parse`, `main`, `parse_globals`, `app` and a parser from `duho.parser(cls)`.
  A `_config_` path that does not exist yet is skipped; an explicit `config=` path that is
  missing raises. A malformed JSON or TOML file, a TOML file with no TOML reader
  installed (3.9/3.10 without `tomli`), and a file whose top level is not a table/object
  are usage errors (message naming the file, exit `2`) under `main`, `parse` and `app`
  alike; `--help` and `--version` still work. `_config_loader_` (`Callable[[Path], dict]`)
  replaces the built-in reader, for any other format; it is not called for a skipped file.
  An exception the loader itself raises propagates to the caller unchanged (the
  application reports it); a loader that returns something that is not a mapping is a
  usage error naming the file. A `loglevels` table in a config file takes level names or
  numbers and is converted to numeric levels. A collection field treats an
  env or TOML *string* as one element and a TOML array element-wise; non-string TOML
  scalars are coerced to the field type.
- **Choosing the config file.** Highest first: an explicit `config=` argument to
  `main`/`parse`/`parse_globals`/`app`; the field named by the root's `_config_field_`,
  when the user gave it on the command line or through its own env var (a class default
  does not count; it is read by the same prepass `parse_globals` runs, without a config
  layer); the variable named by the root's `_config_env_`, when set and non-empty; then
  `_config_`. A path chosen by the field or the variable is strict like `config=`: a
  missing file raises `FileNotFoundError`, unlike a missing `_config_`. A
  `_config_field_` naming no declared field is a `ValueError` naming the class, raised by
  `main`/`parse`/`parse_globals`/`app` before parsing. Neither attribute is applied to a
  tree served over MCP.
- **`value_sources(parsed) -> dict[str, str]`** — introspect where each field's value
  came from: `"cli"`, `"env"`, `"config"`, `"instance"` (a field that came from an
  instance passed to `duho.parse`), or `"default"`.
- A bad env or config value is reported the SAME way a bad CLI value is — argparse
  usage text on stderr plus `SystemExit(2)` — for a normal `main`/`parse`/`parse_globals`
  class-command build. A module command's own declaratively-bound fields under `app()`
  are reported the same way (usage plus exit 2, no traceback), but only when that
  command's own subparser parses: the conversion is deferred to then, so one bad value
  fails that command alone (including its own `--help` and a run that passes the field on
  the command line) while the root `--help` and every sibling command still work.

## Logging (`duho.logging`)

`duho.logging` is a superset of stdlib `logging`: every public (non-underscore) stdlib
name resolves on it, outside `__all__` — `duho.logging.getLogger`, `.Logger`,
`.LoggerAdapter`, `.WARNING`, `.DEBUG`, ... — and it adds `TRACE` (level 5). So
`from duho import logging` can stand in for `import logging`. A name beginning with
`_` is not forwarded (`AttributeError`). Only the names listed below are duho's own.

- **`LoggingArgs`** — mixin adding `-v/--verbose`, `-q/--quiet` (both repeatable count
  flags — long spellings work alongside the short ones), and `--loglevel
  [NAME:]LEVEL[,...]` (a per-logger level spec, NOT a generic `KEY=VALUE` dict grammar —
  `LEVEL` matches a registered level name case-insensitively or a plain integer, e.g.
  `--loglevel mypkg.sub:DEBUG,other:20`). Its fields are `verbose: int`, `quiet: int`
  (the two counts, readable as `self.verbose`/`self.quiet`) and `loglevels: dict[str, int]`.
  `_logger_` (the logger `-v`/`-q` apply to: a
  `_logger_name_` on the command's class, else one on the root that dispatched it, else
  the application's name), `_set_loglevels_()`, `_verbose_loglevel_()` (returns the NUMERIC level,
  e.g. `logging.DEBUG` — not a level name). `VERBOSE_LEVELS` is most-severe-first;
  `-v`→DEBUG, `-vv`→TRACE, `-q`→WARNING; verbose and quiet offset each other in one
  combined count. `-v`/`-q` step through the registered levels from `_base_loglevel_`
  (default `logging.INFO`; a level number or the name of a registered level, else
  `ValueError` at dispatch). On a `LoggingArgs` root, a plain `Cmd` leaf uses the root's
  value, and an overridden `_verbose_loglevel_` still wins.
- **`init_stderr_logging(name=None, level=None) -> Logger`** — one-liner console
  logging setup; idempotent (a repeat call on the same logger does not add a duplicate
  handler), honors `NO_COLOR`/`FORCE_COLOR`/`TERM=dumb`/TTY detection. The handler
  writes to the `sys.stderr` current at each emit, so a later reassignment or
  redirection of `sys.stderr` is followed; `handler.setStream(stream)` pins an explicit
  stream.
- **`add_logging_level(name, level, force=False, color=None)`** — register a custom
  level (e.g. TRACE); refreshes the `-v`/`-q` verbosity table so the new level
  immediately participates in it. Registering a name again at the same number is a
  no-op; at another number it raises `ValueError` unless `force=True`. `import duho`
  registers `TRACE` itself and tolerates one the process already defines (an integer
  `TRACE` level is reused, a foreign `trace` method is kept); only an explicit
  `add_logging_level` call raises on such a collision.
- **`initverbose()`** — rebuild the `VERBOSE_LEVELS`/`VERBOSE_HELP` tables from the
  currently registered logging levels (called automatically at import and whenever
  `add_logging_level`/`init_stderr_logging` runs).
- **`VERBOSE_LEVELS`** — most-severe-first `{level_number: (names...)}` table backing
  `-v`/`-q`. **`VERBOSE_HELP`** — a display string joining each level's canonical name,
  used in `--loglevel`'s own help text.
- **`parse_loglevels(text, itemdivider=",", valkey_separator=":") -> dict[str, int]`** —
  parse a `[NAME:]LEVEL[,...]` spec string; raises `argparse.ArgumentTypeError` on a
  bad token (reported as a normal argparse usage error, not silently dropped).
- **`TRACEBACK_ENV`** — the string `"DUHO_TRACEBACK"`. **`traceback_enabled() -> bool`** —
  current truthiness of that env var (re-read every call). **`log_exception(logger, msg,
  *args, level=logging.ERROR)`** — log `msg` from inside an `except:` block, attaching
  `exc_info` iff `traceback_enabled()` (omits the kwarg entirely rather than passing
  `False` when disabled, so `LogRecord.exc_info` stays `None` like an undecorated record).
- **`DefaultFormatter`** (log-record formatter, this module) / **`DefaultsFormatter` /
  `ColorDefaultsFormatter` / `ColorHelpFormatter`** (argparse help formatters,
  `duho.formatters`) — colored output in raw ANSI codes, no dependency needed (the optional
  `colorama` only resolves a named color for `add_logging_level` and patches a legacy
  Windows console), gated by `NO_COLOR`/`FORCE_COLOR`/TTY detection (see "Environment
  variables" below);
  `ColorHelpFormatter` is a no-op on Python 3.14+, which has its own native argparse
  color support.

## Agent help (`duho.agenthelp`)

- **`print_agent_help(cls, file=None)`** — write `cls`'s machine-readable JSON help
  document to `file` (default stdout); also triggered automatically by the
  `AGENT_HELP` env var or a `--help-agents` flag in place of human `--help`. The flag is
  opt-in: it exists only on a root that sets `_agent_help_ = True` (`bool`, default
  `False`); `_completion_` and `_version_` do not add it, and without the attribute
  `--help-agents` is an unrecognized argument (exit 2). The env-var trigger needs no
  opt-in. Two more root attributes shape the document: `_examples_` (`Sequence` of
  command strings or `(command, description)` pairs; default `None` → one synthesized
  invocation line) and `_exit_codes_` (`Mapping[code, meaning]` merged over the default
  0/1/2 table; default `None`).
- **`describe(cls, argv=None) -> dict`** — build the agent-help document for `cls`
  (the dict `render()`/`print_agent_help` serialize).
- **`describe_parser(parser, *, root=False, root_cls=None, name=None, aliases=None) -> dict`** —
  lower-level: describe one already-built `ArgumentParser` node (recurses into
  subcommands).
- **`render(spec) -> str`** — serialize a `describe()` dict to its final JSON text
  (`ensure_ascii=True`, indented).
- **`agent_help_requested(env_name=None, environ=None) -> bool`** — whether the trigger
  env var currently requests agent-help mode. `env_name=None` checks every name in
  `DEFAULT_ENVS` (either truthy triggers); an explicit `env_name` checks only that one
  variable.
- **`SCHEMA`** — the document format tag stamped into every agent-help JSON document
  (currently `"duho/agent-help@1"`), so a consumer can detect the shape and pin to it.
- **`DEFAULT_ENV`** — the string `"AGENT_HELP"` (the primary default trigger env var name).
- **`DEFAULT_ENVS`** — `("AGENT_HELP", "AGENTS_HELP")`, the full set of default trigger
  env var names checked when no `_agent_help_env_` override is set.
- **No-secrets contract**: neither the agent-help JSON nor the human `--help` output
  (via `DefaultsFormatter`) ever shows a LIVE env/config value as a field's default —
  both show the field's DECLARED class default plus, only when the effective value
  actually came from env or config, a value-free provenance note instead of the real
  value. JSON: a `"default_source"` key (e.g. `"env DEPLOY_TOKEN"` or `"config"`) next
  to a `"default"` that stays the declared default (or omitted/`None`). Human help:
  `"... (from env DEPLOY_TOKEN)"` / `"... (from config)"` appended to the help text in
  place of `"(default: X)"`. This also covers a module command's own env/config-bound
  declared fields under `app()`, not just class-command fields. Agent help and the MCP tool list
  publish a default only for fields declared with duho; an option a module command's
  `register` hook adds to the parser directly publishes none, so a value such a hook reads
  from the environment is never exposed.
- **Encoding-safe output**: agent-help JSON is ASCII-escaped at the JSON level and then
  written as raw UTF-8 bytes (bypassing the console's text-mode encoding/newline
  translation entirely); human `--help`/`--print-completion` output is written using the
  destination stream's own encoding with `errors="backslashreplace"` — neither crashes
  on a non-ASCII string under Windows console redirection.
- **Stable schema across interpreters**: a field's `"type"` string in the agent-help
  document is identical on every supported Python version (e.g. always `"int | None"`),
  not subject to each interpreter's own `str(type)` quirks (3.9/3.10 rendering
  `list[str]` as bare `list`, spelling unions as `Optional[int]`/`Union[int, str]`
  instead of `int | None`/`int | str`).

## Completion (`duho.completion`)

Static completion-SCRIPT generation (bash, zsh, fish, PowerShell — four shells) — the
user installs a self-contained generated script once; this is not a dynamic
argcomplete-style hook that re-invokes the program on every Tab press, so it costs zero
runtime dependency and zero per-invocation overhead.

- **`spec(parser, prog=None) -> CompletionSpec`** — walk a built `argparse.ArgumentParser`
  into a plain, shell-agnostic completion tree; all four shell emitters below are built
  on top of this one walk.
- **`CompletionSpec`** — one parser node: `prog`, `path` (tuple of ancestor subcommand
  names), `options: list[CompletionOption]`, `positionals: list[CompletionPositional]`,
  `subcommands: dict[str, CompletionSpec]`, `help`.
- **`CompletionOption`** — one flag: `flags: tuple[str, ...]`, `takes_value: bool`,
  `choices: tuple[str, ...] | None`, `is_path: bool`.
- **`CompletionPositional`** — one positional: `name`, `choices`, `is_path`.
- **`bash(parser, prog=None) -> str`** / **`zsh(parser, prog=None) -> str`** /
  **`fish(parser, prog=None) -> str`** / **`powershell(parser, prog=None) -> str`** —
  emit that shell's completion script text.
- An `Enum`-typed field offers its member NAMES as completion candidates (same
  by-name matching the parser itself uses). A field or subcommand declared with
  `help=argparse.SUPPRESS` is hidden from completion too, not just from `--help`.
  zsh completion works correctly below the root at any depth (one generated shell
  function per subcommand node dispatching into its children), not root-commands-only.
- `duho.print_completion(cls, shell, file=None, *, prog=None)` is the standalone,
  callable counterpart to the `--print-completion` flag injected when `_completion_ =
  True` — see "Build / parse / run" above for its `prog=` behavior and the flag's
  invoked-name default.

## Text / names (`duho.text`)

- **`expand(text)`** — brace-range expansion (non-zero-padded, e.g. `"a[1-3]"` → `"a1"`,
  `"a2"`, `"a3"`). A `:spec` suffix inside the brackets is a `str.format` spec applied to
  each member: `"host[1-3:02d]"` → `host01`, `host02`, `host03`; a spec containing braces,
  or one the member rejects (`"[a-c:02d]"`), raises `ValueError`. **`pysafe(text, separator=".")`** — coerce each `separator`-delimited
  part into a valid Python identifier (symbol substitution via `PYREPLACE`, a leading
  digit or bare keyword gets an underscore, never produces an empty part). **`PYREPLACE`** —
  the symbol→word substitution table `pysafe` consults (e.g. `+`→`plus`). **`camelcase(text,
  separators=None)`** — case conversion to CamelCase. **`snakecase(name)`** — case
  conversion to snake_case; an upper-case letter that immediately follows a separator is
  lower-cased WITHOUT an extra inserted underscore, so `snakecase("My-App")` correctly
  gives `"my_app"` (not `"my__app"`), and `snakecase("CamelCaseName")` gives
  `"camel_case_name"`; an acronym run lowers letter-by-letter (`"HTTPServer"` →
  `"h_t_t_p_server"`). **`kebabcase(name)`** — acronym-aware kebab-case: splits on a
  lower/digit→Upper boundary, on an Upper letter followed by Upper+lower (an acronym run
  stays together up to its last letter: `"ShowHTTPStatus"` → `"show-http-status"`, unlike
  `snakecase`'s letter-by-letter acronym handling), and on one or more `_` (never yields a
  leading/trailing/doubled `-`: `"_Private"` → `"private"`). Text with no such boundary,
  including an already-hyphenated name, passes through unchanged (lower-cased). This is
  the rule behind a class-derived command name (below) and a field's default long flag
  (see "Declaring commands" → field defaults). **`gettext`** / **`ngettext`** —
  translation shims (stdlib `gettext` re-exports, with a no-op fallback if unavailable).
  `duho.text.range`/`duho.text.unicode_range` are real module functions but are
  deliberately excluded from `duho.text.__all__`, so `from duho.text import *` cannot
  shadow a caller's own `range` builtin — access them as `duho.text.range(...)`.
- **`parse_bool(text) -> bool`** (also `duho.parse_bool`) — a strict boolean from
  text: a member of **`BOOL_TRUE`** is `True`, a member of **`BOOL_FALSE`** (the
  empty string included) is `False`, case-insensitively after `.strip()`. Anything
  else, and any non-string, raises `ValueError` naming the accepted tokens. The
  tables are listed under "Environment variables".

## Qualified names (`duho.qualname`)

- **`QualName`** — abstract dotted-name algebra: `parts`, `name`, `parent`,
  join/split, path mapping.
- **`DotQualNamed`** — mixes `QualName` into `str`, splitting/joining on a configurable
  separator (default `"."`), dropping empty segments.
- **`PythonName`** — a `DotQualNamed` whose `.new()` classmethod runs each dotted part
  through `pysafe` (unless `sanitize=False`) so every part is a valid Python identifier.

## Subparser helpers (`duho.parsers`)

Lower-level `argparse` plumbing `duho` itself is built on, exposed for a consumer
manipulating a parser tree directly:

- **`pop_action(parser, name)`** / **`insert_action(parser, action, index=None)`** —
  remove an action by its `dest` name (returns it) / insert an action object, while correctly maintaining its owning argument GROUP's own
  action list too (not just the parser's flat list), since `format_help` renders from
  the group lists; `insert_action`'s default (`index=None`) truly appends at the end.
- **`add_help_argument(parser)`** — add a standard `-h`/`--help` action
  (`default=argparse.SUPPRESS`).
- **`disable_subparser_check(action)`** / **`enable_subparser_check(action)`** — pair
  that temporarily relaxes a subparsers action's own value validation; safely
  reentrant via a depth counter, so a nested `disable`/`enable` pair only the OUTERMOST
  pair actually saves/restores the original state.
- **`prerun_parse(parser, argv=None, *, quiet=False)`** — an advisory pre-parse of root-level
  options only: detaches any subparsers action for the call (restored after), turns
  every terminal action (`-h`/`--help`, `--version`, `--print-completion`,
  `--help-agents`) into a no-op for the call, and optionally silences `parser.error()`
  to a bare `SystemExit(2)`.
- **`find_subparsers(parser)`** — return `parser`'s subparsers action, or `None`.
- **`strip_subparsers(parser)`** — detach the subparsers action from the parser,
  returning a handle to restore it later (or `None` if there is none).
- **`unique_subcommands(parser, seen=None)`** — yield `(canonical_name, aliases,
  subparser)` once per distinct subcommand, deduping argparse's alias-as-extra-choice
  representation.
- **`command_name(command) -> str`** — the effective subcommand name for a resolved
  `Command`; also exported as `duho.command_name` (the same function).

## Opt-in submodules (`duho.fanout`, `duho.runpath`, `duho.scaffold`, `duho.mcp`, `duho.testing`)

- **`duho.fanout`** — **`run_targets(func, targets, *, max_workers=None,
  aggregate=<worst-by-magnitude>, logger=None, label=None) -> int`** (ThreadPool
  per-target, exit-code reduced by `aggregate`; default logger is this module's own,
  `"duho.fanout"`; `label(target)` gives the text of that target's log prefix in
  place of `str(target)`, and `func` still receives the target), **`fan_out_command(command, make_instance, targets, *, context=None, max_workers=None, aggregate=<worst-by-magnitude>, logger=None, label=None) -> int`** (sugar over `run_targets`: `make_instance(target)` builds
  the parsed instance for each target and the resolved duho `Command` is dispatched via
  `run_command`), **`target_logging(logger=None)`** (a
  context manager installing/removing a per-target log prefix for the duration of a
  fan-out), **`TargetPrefixFilter`** (the `logging.Filter` that prefixes active-target
  records with `[<target>] ` without mutating the record's own message/args),
  **`current_target`** (the `contextvars.ContextVar` naming the target currently
  running, read by `TargetPrefixFilter`).
- **`duho.testing`** — **`invoke(root, argv=(), *, env=None, stdin=None,
  **app_kwargs) -> Result`** runs a command line in-process: `duho.main(root, argv)`,
  or `duho.app(root, argv=argv, **app_kwargs)` when any `app_kwargs` is given.
  **`Result`** is a named tuple `(status, stdout, stderr)`. `env` is applied to
  `os.environ` for the call and restored; `stdin` is the text the command reads (empty when `None`). A
  `SystemExit` becomes `status` (text passed to it goes to `stderr`, status 1); any
  other exception propagates.
- **`duho.runpath`** — ordered `NN-name.py` step-runner over a dir with no `__init__.py`.
  **`is_runpath_dir(path)`** — whether a directory looks like a RunPath step directory.
  `import duho.runpath` auto-registers its discovery provider; **`register(base=None,
  step_adapter=<keep current>)`** / **`unregister()`** for explicit control. `register`'s `base`
  (default: keeps the current base, initially `LoggingArgs`) is the class every
  provider-built **`RunPathCmd`** subclass also inherits from — `app()`'s `parents=`
  only copies a root's DATA fields onto a class command's parsed instance, never its
  METHODS, so `_logger_`/`_set_loglevels_` need real class inheritance to work;
  defaulting to `LoggingArgs` makes `-v`/logging work with zero config, and
  `register(base=MyAppRoot)` lets a custom root's own methods reach every RunPath
  command too. `register`'s `step_adapter` (default: unchanged) is a callable applied to
  each step's entrypoint just before it runs (`adapter(entrypoint) -> callable`), so an
  app can accept step signatures of its own without a decorator in every step file; the
  ADAPTED callable is what arity detection inspects. `None` clears it; unlike `base` it
  applies per step run, so it affects already-built commands too.
  `RunPathCmd`'s `--rcopts/-O` selects/tunes which steps run. Optional per-directory
  `__main__.py` lifecycle: `init(cmd, logger) -> ctx` (once, before any step; raising is
  always fatal), `success(ctx, cmd, logger)` (once, on a clean run), `finally_(ctx,
  cmd, logger)` (once, unconditionally) — a step entrypoint written `(cmd, ctx)`
  (arity-detected) receives `ctx`; `(cmd)` steps are unaffected. Step filenames accept
  a leading `!` (disable, stripped before the `NN-name` split) plus `:`/`;`-separated
  option tokens (`key`/`!key`/`key=value`; `:` and `;` both work everywhere — `;` is the
  Windows-authorable spelling since `:` is an invalid Windows filename character). Two
  tokens are special: `strict`/`!strict` (default strict, absent the token; `!strict`
  opts that ONE step out) and `enable`/`!enable` (explicit alternative to the leading
  `!`; wins if both are present — more specific). The same grammar is reused verbatim by
  `--rcopts` per comma-entry. Precedence for a step's strict setting: filename default
  -> a per-pattern `--rcopts` `!strict` token matching it -> an EXPLICIT bare `--rcopts
  strict`/`!strict` (run-wide, wins last). Step modules may also set `BEFORE:
  list[str]` / `AFTER: list[str]` (soft ordering, no existence/success requirement —
  silently a no-op if the named step is missing or disabled) alongside the hard
  `REQUIRED: list[str]` (a missing/disabled dep still warns, or errors under strict).
- **`duho.scaffold`** — **`generate_launchers(app, root, *, libdir="lib", python=None,
  overwrite=False) -> list[Path]`** writes a `bin/<app>` + `bin/<app>.cmd` launcher pair.
  Validates `app` (a safe, ASCII, dotted-identifier module name) and `libdir`/`python`
  (ASCII, free of shell/batch metacharacters), raising `ValueError` on a bad value
  instead of silently producing a broken or command-injectable launcher. **`ScaffoldCmd`** —
  the `duho.Cmd` implementing the CLI: `python -m duho.scaffold <app>`.
- **`duho.mcp`** — expose a duho CLI's `Cmd`/`Cli` classes (a static
  `_subcommands_` tree) OR a full `duho.app()`-built tree (class AND module
  commands, from discovered files/`CMDS_PATH`/entry points/`commands=`) as MCP
  tools (stdlib JSON-RPC over stdio, zero-dep). **`describe_tools(root_cls) ->
  list[dict]`** — one tool per leaf/runnable command; a "namespace" node (a
  mandatory-subcommand parent with no runnable body of its own) is never
  listed as its own tool — its fields merge into each descendant's own input
  schema instead. **`call_tool(root_cls, name, arguments) -> dict`**,
  **`serve(root_cls, *, stdin=None, stdout=None)`** (a `ping` request is
  answered directly), **`input_schema_for_command(cls) -> dict`**,
  **`json_schema_for_field(decl, builder) -> tuple[dict, bool]`** (per-field JSON Schema fragment), **`main(argv=None)
  -> int`** — CLI entry point: `python -m duho.mcp <app>` (`<app>` a `module:ClassName`
  or dotted `module.ClassName` path to a root `Cmd`/`Cli`). Pass a `Cmd`/`Cli` class as
  `root_cls`. The other kind of root these accept is the opaque server core duho itself
  builds from a full `app()` tree (class and module commands) for the `<NAME>_MCP`
  trigger, `_mcp_command_` and `serve_running_app`; it is not something to construct.
  A module command whose `register` hook adds its own subparsers is listed as one tool:
  the subparser is chosen through a property named after the subparsers' `dest`, and the
  hand-made subparsers' own options are not served. **`UnknownToolError`** /
  **`InvalidArgumentsError`** — `ValueError` subclasses (JSON-RPC code `-32602`) for an
  unresolvable tool name and for arguments that are not a JSON object or fail the
  tool's schema, respectively. `initialize`'s `serverInfo` reports the served APP's own
  identity, not a fixed placeholder: `name` is the application's name, the same root
  tool-name segment every tool name uses, and `version` is the app's own `_version_` when it resolves to a
  string, else the empty string -- duho's own version is NEVER reported as the served
  app's. `initialize` negotiates
  `protocolVersion` against the supported set (`2025-11-25`, `2025-06-18`,
  `2025-03-26`, `2024-11-05`; an unsupported or absent request gets the newest)
  rather than echoing the client's request unconditionally. In a session that
  negotiated `2025-11-25` or later, `tools/call` with a JSON-object `arguments` that
  raises `InvalidArgumentsError` is answered with a successful result
  `{"content": [{"type": "text", "text": <message>}], "isError": true}`; an unknown
  tool, non-object `arguments` and any request before `initialize` stay JSON-RPC
  `-32602`, as do all bad arguments up to `2025-06-18`. `call_tool` raises either way.
  `json`/`importlib.metadata` stay function-local.
  - **Launching a server from the CLI itself** (no MCP-specific code required):
    every `duho.main(cls)`/`duho.app(...)` call checks a `<PREFIX>MCP` (an
    `Env(prefix)` app's own prefix) or `<NAME>_MCP` (derived from the application's name,
    upper-cased with non-`[A-Z0-9]` characters replaced by `_`) environment
    variable FIRST, before parsing `argv`. Set to `stdio`, it serves that
    app's full tool tree over stdio instead of running any command; set to
    anything else, it exits `2` with a message naming the unsupported
    transport; the variable is always removed from `os.environ` the moment
    it is seen (present or not), so a served command's own child processes
    never inherit it. The trigger takes over stdio before the app is built, so output
    printed while commands are discovered or registered (module imports, `register`
    hooks) goes to stderr, not the protocol stream. Default on; a root class attribute **`_mcp_ = False`**
    (declared on `Cli`) or **`app(..., mcp=False)`** disables it entirely
    (the variable, if set, is then left untouched).
  - **`McpCmd`** — a ready `Cmd` (`--transport {stdio}`) whose `__call__`
    calls **`serve_running_app(transport="stdio") -> int`**, which serves
    the CLI currently being dispatched (read from a `ContextVar` `duho.main`/
    `duho.app` set around their own dispatch — `RuntimeError` outside such a
    dispatch; `ValueError` for an unsupported transport). Register a
    (dynamically-named) `McpCmd` subclass under any name to add a
    self-serving MCP subcommand by hand; the **`_mcp_command_`** class
    attribute (declared on `Cli`, `Union[str, bool]`, default `False`) does
    exactly this for you under both `duho.main(cls)` AND `duho.app(...)` --
    the latter also accepts an **`app(..., mcp_command=...)`** kwarg, which
    wins over the class attribute when given (`duho.main` has no such kwarg;
    it always reads the class attribute directly). Either way: `True` →
    registers it as `"mcp"`; a non-empty `str` → that exact name (validated
    at build time: non-empty, no whitespace, not starting with `-`; a name
    collision with an existing command/alias, or an app/class with no OTHER
    subcommand at all, is a build-time `ValueError` -- the same validation
    and message text either entry point goes through). A node whose class is
    (or subclasses) `McpCmd` is never itself listed as (or callable as) an
    MCP tool. With the `mcp` subcommand the app is already built when the server
    starts, so output printed during that build (module imports, `register` hooks)
    reaches stdout ahead of the first reply.

## Exceptions

- **`duho.mcp.UnknownToolError`** — `ValueError` subclass; the tool name does not resolve
  (a namespace node that is never listed raises it too). JSON-RPC code `-32602` through
  `serve`.
- **`duho.mcp.InvalidArgumentsError`** — `ValueError` subclass; the tool arguments are not
  a JSON object or fail the tool's schema. JSON-RPC code `-32602`; over `serve` a schema
  failure is an `isError` tool result instead in a `2025-11-25`+ session.
- **`NotImplementedError`** — dispatching an `Args` that is not a `Cmd`, or a `Cmd` that
  never overrode `__call__`, names the class. A module with no entrypoint raises it too,
  which discovery treats as "not a command" and skips.
- **`ValueError`** — a build-time error naming the cause: a reserved field name, a flag
  tuple with `"--"` more than once, `Append` on a `set`/`tuple` field, an unknown shell for
  `print_completion`, a `finish_parse` namespace with no duho subparser, an invalid
  `_mcp_command_` or `_completion_command_` name or collision, an unknown or misplaced
  `_default_subcommand_`, a `_config_field_` naming no field, an invalid `_base_loglevel_`,
  an invalid `enum_by` or `literal_value` use, an `adapter=` combined with `dispatch=`, an
  unsupported MCP transport, a `duho.expand` range format with braces or one a member
  rejects, a different `add_logging_level` number for a registered name, and a
  `generate_launchers` argument with unsafe characters.
- **`FileNotFoundError`** — a config path chosen by `config=`, `_config_env_` or
  `_config_field_` that does not exist.
- **`RuntimeError`** — `duho.mcp.serve_running_app` called outside a `main`/`app` dispatch.
- **`argparse.ArgumentTypeError`** — `parse_loglevels` on a bad token (reported as a usage
  error).
- **Exit status `2`** (raised as `SystemExit`) — a bad CLI, env or config value, a malformed or
  non-table config file, a `--` tail given to a command with `_allow_passthrough_ = False`,
  an `app` with no commands and a non-runnable root, and an unsupported `<NAME>_MCP`
  transport. Other exit codes are the command's own return value.

## Command line

- **`python -m duho.mcp <app>`** — serve a duho CLI as MCP tools over stdio (JSON-RPC 2.0,
  newline-delimited). `<app>` is `module:ClassName` or dotted `module.ClassName`, naming a
  root `Cmd`/`Cli`.
- **`python -m duho.scaffold <app> [--root DIR] [--libdir lib] [--python PY] [--force]`** —
  write the `bin/<app>` and `bin/<app>.cmd` launcher pair under `--root` (default: the
  current directory) and print each path written; `--force` overwrites existing launchers.
  `--version` is also accepted.
- **Flags duho adds to an app** — `--version` (when `_version_` or a class `__version__`
  resolves), `--print-completion {bash,zsh,fish,powershell}` (`_completion_ = True`),
  `--help-agents` (`_agent_help_ = True`), `-v/--verbose`, `-q/--quiet` and `--loglevel`
  (`LoggingArgs`), `--rcopts/-O` (`duho.runpath.RunPathCmd`) and `--transport {stdio}`
  (`duho.mcp.McpCmd`).

## Environment variables

- **`AGENT_HELP`** / **`AGENTS_HELP`** — either truthy switches `--help`/agent-help
  output into machine-readable JSON (see "Agent help" above). Falsy tokens: `""`, `0`,
  `false`, `no`, `off`, `n`, `f` (case-insensitive); anything else counts as on. An
  explicit `_agent_help_env_` on the CLI root replaces both defaults with exactly one
  variable name (no aliasing).
- **`<PREFIX>MCP`** (an `Env(prefix)` app's own prefix) / **`<NAME>_MCP`** (derived from
  the application's name, upper-cased, non-`[A-Z0-9]` characters replaced by `_`) — read
  by every `duho.main`/`duho.app` call before `argv` is parsed. `stdio` serves the app's
  tools over stdio instead of running a command; any other value exits `2` naming the
  unsupported transport. The variable is removed from `os.environ` as soon as it is
  seen, so a served command's child processes never inherit it. A root `_mcp_ = False`
  or `app(..., mcp=False)` disables the check and leaves the variable alone.
- **`DUHO_TRACEBACK`** — truthy enables a full traceback (`exc_info`) on an exception
  duho itself logs-and-swallows at a resilient boundary (discovery skipping a bad
  command source, a non-strict RunPath step failure, `app`'s advisory `register`
  prepass, `mcp.call_tool` converting an exception to a client-facing string); off by
  default and re-read on every call (never cached). Falsy/truthy tokens match `BOOL_FALSE`/
  `BOOL_TRUE` below.
- **`NO_COLOR`** / **`FORCE_COLOR`** / **`TERM`** — the standard convention: `NO_COLOR`
  set to anything, including empty, forces color off; `TERM=dumb` turns color off;
  `FORCE_COLOR` set to a truthy value forces color on, even under `TERM=dumb`;
  otherwise color follows whether the output stream is a TTY. Gates ANSI in both the
  argparse help formatters (`ColorHelpFormatter`/`ColorDefaultsFormatter`) and
  `init_stderr_logging`'s default log formatter.
- **`<PREFIX>PATHSEP`** (e.g. `MYAPP_PATHSEP`; scoped to this app's own prefix, never
  a bare/global `PATHSEP`) — overrides `os.pathsep` as the separator `Env.paths(...)`
  (and the `CMDS_PATH` convention below) splits on.
- **`CMDS_PATH` convention** — not a literal single env var: a per-prefix
  `Env(prefix).paths("CMDS_PATH", ty=Path)` lookup (e.g. `MYAPP_CMDS_PATH` for
  `duho.app(env=Env("myapp"))`). See `app()` above for precedence — it always merges on
  top of the base command source, and a `CMDS_PATH` command wins a name clash (logged,
  never silent).
- **`BOOL_TRUE` / `BOOL_FALSE`** (`duho.text`; `parse_bool` reads them) — the canonical
  truthy/falsy text tokens: truthy = `1`, `true`, `yes`, `on`, `y`, `t`; falsy = `0`,
  `false`, `no`, `off`, `n`, `f`, `""` — matched case-insensitively after stripping
  whitespace. `Env.bool`, the layered env/config bool converter, and the strict CLI
  bool-field text factory all match against this same table.

## Gotchas

- **`Args` is data, not a command.** `main`/`app`/`run_command` raise `NotImplementedError`
  for an `Args` that is not a `Cmd`; a subcommand tree must be `Cmd` subclasses.
- **Reserved and replaced names.** A field named `help`, `version` (with a `--version`
  flag) or `print_completion` (with `_completion_`) raises `ValueError`. A field named
  `subcommand` on a `Cli` subclass silently replaces the `@Root.subcommand` decorator.
- **A field with no flag tuple is a required `--name` option**, never a positional; a
  positional needs a dash-less entry such as `("src",)`.
- **Global options and subcommands.** Under a root's static `_subcommands_` tree a global
  option must come before the subcommand name. A command reached through
  `commands=`/`source=`/`entry_points=` accepts it on either side, but a value given on
  both sides does not merge: the later one wins, so `-v sub -v` is verbosity 1.
- **`--` always starts the passthrough capture** (`_passthrough_`); a value that must start
  with `-` cannot be given after it.
- **Collection options take one value per flag occurrence**, and the first occurrence
  replaces any class, env, config or instance default.
- **A bad env or config value of a module command's declared field** exits `2` for that
  command only; the root `--help` and its sibling commands still work.
- **`--help-agents` needs `_agent_help_ = True`**; the `AGENT_HELP` variable does not.
- **A discovered class command carries `_parsername_` only when it declares one**; read a
  resolved command's name with `duho.parsers.command_name`.
- **A RunPath step is strict by default**: a failing step stops the run unless it is marked
  `!strict`. Unmatched `--rcopts` patterns and missing `REQUIRED` names are warnings
  unless a bare `strict` is passed.
- **`ColorHelpFormatter` does nothing on Python 3.14+**, which colors argparse help itself.
- **Opt-in modules** (`duho.fanout`, `duho.runpath`, `duho.scaffold`, `duho.mcp`) are not
  imported by `import duho` and are not on the top-level namespace.