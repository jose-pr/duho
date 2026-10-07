# Release Notes

Detailed notes per release: the narrative, the performance story, and the
validation evidence behind each version. `CHANGELOG.md` stays terse and
user-facing; this file is the durable record.

---

## [Unreleased]

Prepared as 0.7.0. It is a minor release because it breaks the documented API,
in two ways and no others.

- **`NS` is removed.** Field metadata is written as `Meta(...)`, or as a plain
  `dict` for a key `Meta` does not have. `NS` was an alias of
  `argparse.Namespace`: untyped, and a misspelled key was dropped. There were
  three ways to say the same thing; there are now two, one strict and one
  permissive.
- **`Meta(...)` takes keyword arguments only.** `Meta("-n", "--name")` used to
  set `help` and `env` without a word; it is now a `TypeError` that says where
  flags go.

`Choice`, `Const`, `Count`, `Append` and `Extend` return a `Meta`.

### Upgrade notes

The edit is mechanical, and a project that imports `NS` fails at import, not
quietly:

- `from duho import NS` raises `ImportError`. Replace `NS(` with `Meta(` and
  import `Meta`.
- An `argparse.Namespace` given directly as a field's metadata keeps working:
  it is read like a `dict`, the permissive form.
- A `TypeError` from `Meta` naming an unknown field means that key was being
  ignored before: delete it, or write that field's metadata as a `dict` if the
  key belongs to a custom argument.
- A positional `Meta(...)` call names its arguments; a flag goes in
  `flags=("-n", "--name")`.
- Only a `Meta`, or a subclass of it, is strict. A `dict` or any other object
  with attributes is permissive: the keys duho knows are used, and a key
  nothing claims is ignored with a warning.

### Performance

No performance claim is made. The CI regression gate passed on Python 3.9,
3.13 and 3.14 at the tree below against the same baseline as 0.6.5.

### Validation

- Test suite, no failures: Windows Python 3.9 (2786 passed, 44 skipped) and
  3.14 (2800 passed, 30 skipped); Linux (WSL) Python 3.14 (2804 passed, 26
  skipped).
- CI: all 17 jobs green (run
  [37616464145](https://github.com/jose-pr/duho/actions/runs/37616464145)).
- `black --check` and `mkdocs build --strict` pass. The leak check exits 1 with
  exactly its three known hits (one README line and two lines of
  `examples/dotagents.py`, whose subject is that directory).
- Nine projects that use duho had their own suites run against the tree one
  commit earlier (before the permissive form was widened to any object with
  attributes). Six are unchanged. Three import `NS` and fail at import, as
  intended, until they make the edit above.

### Publication state

Prepared and pushed to `main`. The version number is not bumped and no tag
exists: tagging `v0.7.0` awaits the owner's consent for this release.

---

## [0.6.5] — 2026-10-07

A large patch: the fixes and additions that came out of a whole-repository
review, with nothing removed from the documented API. `CHANGELOG.md` has the
full list; this is what is worth knowing before upgrading.

**Fixed.** Options written after a subcommand now always bind to the
subcommand; a root option's default redeclared by one command no longer leaks
to its siblings under `duho.app`; a malformed config file is a usage error and
not a traceback; the MCP server no longer reads a tool value as an argument
file, runs a tool below an optional positional, and rejects non-finite numbers
and malformed requests with the right error; helper modules
of a command directory are shared, not copied per command file; zsh and fish
completion scripts survive unusual flag names and help text.

**Added, all opt-in.** A command can raise `duho.CommandError` or return a
`duho.Result`, and `duho.run(App)` is a program entry that prints a command's
answer and exits with its status. Discovery takes several sources, an error
policy and providers. New field options (`enum_by`, `literal_value`), class
attributes (`_default_subcommand_`, `_allow_passthrough_`, `_config_env_`,
`_config_field_`, `_completion_command_`, `_base_loglevel_`, `_errors_`),
`duho.testing.invoke`, and MCP protocol revision 2025-11-25.

**Also.** Every public callable is fully annotated and its annotations resolve
at run time on Python 3.9; the six largest modules are packages of private
submodules with every import path kept; comments and private docstrings were
cut to what the code requires.

### Upgrade notes

- **One application name.** The usage line, the completion script, the
  `<NAME>_MCP` variable, MCP tool names and the default logger all use one
  name: `app(name=)`, else the root's own `_parsername_`, else its top-level
  package, else its kebab-case class name. A root in package `pkg` with no
  `_parsername_` is now `pkg`, and a command's `-v` raises the application's
  logger, not one named after the command. A test that asserts on a logger
  named after a command needs the application's name instead.
- **Config errors.** A malformed JSON or TOML config is a usage error (exit 2)
  under `main`, `parse` and `app`. An exception raised by an application's own
  `_config_loader_` still reaches the caller unchanged.
- **Helper modules of a command directory** are loaded once under a private
  name and are not in `sys.modules` under their bare name.
- **MCP.** A client that names no protocol revision, or an unknown one, is
  answered `2025-11-25`. In a connection on that revision a bad argument is a
  tool result with `isError: true`, not a `-32602` error.
- **Extras.** `duho[colorama]` requires `colorama>=0.4.6,<0.5`; `duho[config]`
  requires `tomli>=2.0,<3` below Python 3.11.
- The other deliberate behaviour changes are under "Changed" in
  `CHANGELOG.md`, each with what you will see.

### Performance

No performance claim is made for this release beyond the gate result below.

- The CI regression gate compares against `benchmarks/baseline.json`, which
  was regenerated during this cycle from one CI run
  ([37529677398](https://github.com/jose-pr/duho/actions/runs/37529677398)):
  the 3.9 entry had been off against its own calibration (an unchanged tree
  read 1.1x to 1.5x). The baseline now also holds `first_build.complex` and
  `e2e_delta`, so a first parser build and an end-to-end start are gated, not
  only `import duho`.
- The gate at the tree this release was cut from (run
  [37563938647](https://github.com/jose-pr/duho/actions/runs/37563938647),
  `ubuntu-latest`), every metric within its threshold (1.5x warm, 1.3x
  startup). Ratios are normalised by the calibration row of their group, so a
  faster or slower runner cancels out:

| Metric (ms) | 3.9: baseline, this run, ratio | 3.13: baseline, this run, ratio | 3.14: baseline, this run, ratio |
| --- | --- | --- | --- |
| calibration, in-process | 0.2373, 0.1657, 0.70x | 0.2333, 0.1856, 0.80x | 0.2115, 0.2083, 0.98x |
| calibration, subprocess | 10.6500, 8.8900, 0.83x | 13.2600, 12.3100, 0.93x | 13.5500, 13.4800, 0.99x |
| `build.complex` | 0.2709, 0.1836, **0.97x** | 0.2864, 0.2139, **0.94x** | 0.2427, 0.2391, **1.00x** |
| `build.enum` | 0.1555, 0.1087, **1.00x** | 0.1541, 0.1147, **0.94x** | 0.1636, 0.1629, **1.01x** |
| `build.list` | 0.1557, 0.1056, **0.97x** | 0.1534, 0.1156, **0.95x** | 0.1643, 0.1656, **1.02x** |
| `build.literal` | 0.1584, 0.1137, **1.03x** | 0.1548, 0.1154, **0.94x** | 0.1656, 0.1647, **1.01x** |
| `build.set` | 0.1561, 0.1085, **1.00x** | 0.1547, 0.1158, **0.94x** | 0.1663, 0.1647, **1.01x** |
| `build.simple` | 0.1767, 0.1195, **0.97x** | 0.1782, 0.1331, **0.94x** | 0.1785, 0.1802, **1.03x** |
| `build.tuple` | 0.1563, 0.1095, **1.00x** | 0.1537, 0.1147, **0.94x** | 0.1636, 0.1638, **1.02x** |
| `build.union` | 0.1555, 0.1110, **1.02x** | 0.1528, 0.1144, **0.94x** | 0.1639, 0.1629, **1.01x** |
| `first_build.complex` | 2.2184, 1.4481, **0.93x** | 1.2700, 1.0258, **1.02x** | 1.2275, 1.1713, **0.97x** |
| `parse.complex` | 0.0687, 0.0394, **0.82x** | 0.0634, 0.0540, **1.07x** | 0.0646, 0.0640, **1.01x** |
| `parse.simple` | 0.0389, 0.0193, **0.71x** | 0.0376, 0.0302, **1.01x** | 0.0384, 0.0384, **1.02x** |
| `tree.build.1` | 0.3682, 0.2487, **0.97x** | 0.3830, 0.2887, **0.95x** | 0.4066, 0.4013, **1.00x** |
| `tree.build.10` | 2.2547, 1.5519, **0.99x** | 2.3426, 1.7750, **0.95x** | 2.2851, 2.2765, **1.01x** |
| `tree.build.50` | 10.7761, 7.6372, **1.01x** | 11.0966, 8.4100, **0.95x** | 10.6552, 10.6471, **1.01x** |
| `tree.parse.1` | 0.0756, 0.0425, **0.81x** | 0.0747, 0.0646, **1.09x** | 0.0753, 0.0753, **1.02x** |
| `tree.parse.10` | 0.0757, 0.0434, **0.82x** | 0.0741, 0.0646, **1.10x** | 0.0753, 0.0747, **1.01x** |
| `tree.parse.50` | 0.0765, 0.0435, **0.81x** | 0.0751, 0.0654, **1.09x** | 0.0756, 0.0750, **1.01x** |
| `e2e_delta` | 41.69, 28.61, **0.82x** | 51.88, 48.72, **1.01x** | 63.11, 62.63, **1.00x** |
| `import_duho_delta` | 36.48, 27.41, **0.90x** | 46.87, 43.30, **1.00x** | 53.89, 54.02, **1.01x** |

- Caveats: one run per Python version on shared runners; the baseline and
  this run are different machines, which is what the calibration rows correct
  for; medians of the benchmark's own repeats. Local numbers on the
  development machine vary several-fold and are not quoted.
- Two changes touch measured paths. On Python 3.13 and later a class in a
  large file is read on its own instead of parsing the whole file, for the
  first few classes of a file only. A class's type hints are resolved from its
  public annotations only. Neither is claimed as a speed-up here: the gate
  rows above are the evidence that neither is a regression.

### Validation

- Test suite, no failures: Windows Python 3.9 (2784 passed, 44 skipped) and
  3.14 (2798 passed, 30 skipped); Linux (WSL) Python 3.14 (2802 passed, 26
  skipped). Warnings are errors in the test run.
- CI: all 17 jobs green at the tree this release was cut from (run
  37563938647 above): the test matrix on Python 3.9 to 3.14 across Linux,
  Windows and macOS, the dependency-floor job, coverage, formatting, the docs
  build and the three benchmark gates. The same workflow ran at the release
  commit before tagging.
- `black --check` and `mkdocs build --strict` pass.
- The leak check exits 1 with exactly its three known hits and no other: one
  README line that describes the `examples/dotagents.py` example, and two
  lines of that example, whose subject is that directory.
- Nine projects that use duho had their own suites run against this tree:
  eight are unchanged. One has a test that asserts a logger named after a
  command (see the first upgrade note).
- The release workflow itself changed in this cycle (it checks the tag against
  the built version, runs `twine check`, and dispatches the docs build) and
  runs for the first time with this tag.

### Publication state

Prepared and pushed to `main`; tagged `v0.6.5` with the owner's consent for
this release (2026-10-07).

---

## [0.6.4] — 2026-10-06

One bug, reported by a downstream project, fixed in both places it showed.

- An option value that is exactly `--` (`--flag=--`, `-f--`) now reaches the
  field on every supported Python. argparse dropped it on Python 3.9 through
  3.12.6 and on 3.13.0, so the field silently received an empty list. duho
  now keeps the value itself on every parser it builds, so a field's
  `type=` converter and `choices` see the real string.
- An MCP tool call may pass `"--"` as an option value. The server used to
  refuse it outright as a guard against the loss above. It is still refused
  as a positional value and for a field with only a short flag, where `--`
  really is the end-of-options marker.

The affected Python versions were read from CPython's own `Lib/argparse.py`
at each release tag, not inferred from the two versions tested locally.

### Validation

- Test suite green on Windows Python 3.9 and 3.14 and Linux (WSL) Python
  3.14. `black`, `mkdocs build --strict` and the leak check are clean. The CI
  test workflow, which covers Python 3.9 to 3.14, ran at the release commit
  before tagging.
- The new tests fail on Python 3.9 with the fix removed.
- Correction (2026-10-07): the leak check was not clean at this release. It
  reported the same four known hits (the docs badge label in `README.md`, one
  README line, and two lines of `examples/dotagents.py`) and exited 1.

No performance claim is made.

---

## [0.6.3] — 2026-10-03

Three small fixes, plus CI benchmark baselines and an MCP conformance test.

- A NUL in a `choices` value no longer breaks fish or PowerShell
  completion for the whole command; that candidate is dropped.
- A bad env/config value for a lookup-typed field (`type=MAP.__getitem__`)
  now says what is accepted (`one of: green, red`) instead of
  `expected __getitem__`.
- An MCP server no longer reports duho's own version as the app's.
- A command class built at runtime with `type(...)` no longer logs a
  warning per class; the warning stays for apps whose source is really missing.
- A dev-only test checks duho's MCP stdio server against the official
  `mcp` SDK client on Python 3.10+. Runtime dependencies are unchanged.

### Performance

**Target met.** `benchmarks/baseline.json` now carries CI-sourced
`3.9`, `3.13`, and `3.14` entries, all measured on the same `ubuntu-latest`
Benchmark job the regression gate itself runs on (GitHub Actions run
[37057601055](https://github.com/jose-pr/duho/actions/runs/37057601055), tag
`ci-bench-20261003025756`, 2026-10-02). The `3.13` benchmark job itself was
added to the matrix in the same run (previously only `3.9`/`3.14` were
benchmarked). A follow-up CI run
([37060019624](https://github.com/jose-pr/duho/actions/runs/37060019624),
2026-10-02) confirmed `check_baseline.py` passes against the new baseline on
all three versions — after several earlier follow-up runs tripped the 1.5x
warm-metric threshold on one version or another from ordinary `ubuntu-latest`
shared-runner timing variance (same code, no regression; a run's warm medians
were seen ranging roughly 0.8x-1.6x of another run's, both against the
unchanged baseline).

**The gate noise above is now fixed** (2026-10-03): `check_baseline.py` normalises each gated group against a
calibration reference from its own measurement domain before applying the
1.5x/1.3x thresholds — warm metrics (in-process) against a fixed,
duho-independent `argparse` build+parse; startup deltas (subprocess spawns)
against `python -c pass`'s own median. A uniformly slower or faster shared
runner moves a group's calibration ratio by the same factor it moves every
metric in that group, so the division cancels it; a regression confined to
duho's own code still trips the gate.

A single shared calibration ratio was tried first and rejected: it is NOT
the design above, and the difference is why there are two references, not
one. Measured directly on a real CI run
([37098728406](https://github.com/jose-pr/duho/actions/runs/37098728406)),
the in-process workload sped up 39% while the subprocess spawn only sped up
~13-17% on the SAME run — dividing the startup delta's own harmless 0.87x
raw ratio by the unrelated in-process ratio produced a false "1.39x
REGRESSION" on the `3.9` job. Domain-matching each group to its own
reference fixed it.

`baseline.json`'s `3.9`/`3.13`/`3.14` entries were regenerated with both
calibration references from one CI run
([37098319162](https://github.com/jose-pr/duho/actions/runs/37098319162),
tag `ci-bench40-20261003115759`) — `calibration_ms` and warm/startup medians
from that run's benchmark artifacts, `calibration_subprocess_ms` from the
same run's Regression gate step log. Three follow-up confirming runs of
unchanged code against the fixed, domain-matched gate all passed on every
benchmark job (`3.9`/`3.13`/`3.14`):
[37099383438](https://github.com/jose-pr/duho/actions/runs/37099383438),
[37099521291](https://github.com/jose-pr/duho/actions/runs/37099521291), and
[37099645764](https://github.com/jose-pr/duho/actions/runs/37099645764). All
throwaway `ci-*` tags used along the way were deleted after confirming.

---

## [0.6.2] — 2026-10-01

Three fixes and two small additions, all found while using 0.6 on Windows and
in downstream projects.

- **UTF-8 output by default.** On Windows, piped or captured output used the
  ANSI code page (`cp1252`). `--version` and an app's own `print()` crashed
  on non-ASCII text, and help and logs reached PowerShell garbled. `duho.main`
  and `duho.app` now switch non-UTF-8, non-terminal stdout/stderr to UTF-8
  first. They respect `PYTHONIOENCODING` and Python's UTF-8 mode, and an app
  can opt out with `_utf8_stdio_ = False` or `utf8_stdio=False`. duho's own
  writes (`--version`, its stderr messages) no longer crash even when an app
  opts out. For correct display of captured non-ASCII text in PowerShell, set
  `[Console]::OutputEncoding = [Text.UTF8Encoding]::new()`.
- **Kebab-case default names, as intended.** A command class with no
  `_parsername_` is now named `build-pyz` rather than `BuildPyz`, and a
  field's default flag is `--test-me` rather than `--testMe`. This is visible
  in usage, subcommand names and default logger names. Before release it was
  checked against twelve downstream projects: none of their own commands or
  flags changed, and one project's command override was restored by it.
- **`("--",)` flag shorthand** for a field's default long flag, and the
  public `duho.kebabcase` helper.

### Validation

- Test suite green on Windows Python 3.9 and 3.14 and Linux (WSL) Python
  3.14. `black`, `mkdocs build --strict` and the leak check are clean. The CI
  test workflow ran at the release commit before tagging.
- Encoding probes: piped output on Windows, and captured output in Windows
  PowerShell 5.1 and pwsh 7, no longer crash.
- Correction (2026-10-07): the leak check was not clean at this release. It
  reported the same four known hits (the docs badge label in `README.md`, one
  README line, and two lines of `examples/dotagents.py`) and exited 1.

---

## [0.6.1] — 2026-09-29

A single bug fix, reported by a downstream project the day after 0.6.0:
`--loglevel app:LEVEL` now reaches the dispatched command's own logger
(`app.<command>`), as 0.6.0 documented. It adds regression tests. The CI
test workflow ran at the release commit before tagging. No performance
claim is made.

---

## [0.6.0] — 2026-09-28

A large review pass across the whole library, plus launching an MCP server
from the CLI itself (a `<NAME>_MCP=stdio` environment variable, or an opt-in
subcommand). This is a minor release because the documented API broke; the
**[minor]** bullets in `CHANGELOG.md` list each break. Before release, twelve
downstream projects were run against it: none needed a code change beyond
their version pin, and the three regressions that run found were fixed.

### Validation evidence

- Test suite: Windows Python 3.9 and 3.14, and Linux (WSL) Python 3.14, all
  green; the CI test workflow is run at the release commit before tagging.
- `black --check`, `mkdocs build --strict` and the leak check are clean.
- Correction (2026-10-07): the leak check was not clean at this release. It
  reported the same four known hits (the docs badge label in `README.md`, one
  README line, and two lines of `examples/dotagents.py`) and exited 1.
- Downstream: twelve consumer projects' own test suites passed against the
  release tree, with results identical to 0.5.4 for every project except
  the regressions above, which are fixed.

### Performance

No new benchmark evidence for this release. The CI baseline regeneration
targeted below was **not** done for 0.6.0, so no performance claim is made.

### Performance evidence gap (0.3.0 – 0.5.4)

The releases between 0.3.0 and 0.5.4 (RunPath, MCP, agent help, formatters,
the lazy-import and AST-walk performance work, PowerShell completion, and
the current benchmark-harness rewrite) shipped without a
`RELEASENOTES.md` entry recording their perf evidence. Their `CHANGELOG.md`
entries quote local/development-machine numbers (e.g. the 0.4.0 lazy-import
and AST-walk "~75 ms to ~51 ms" import figures) that were never captured as a committed
CI benchmark run for that release. Those numbers are not reproducible from
`benchmarks/baseline.json` today and should not be cited as CI-verified —
treat them as informal, at-the-time observations only.

The benchmark harness itself changed materially in this pass (see
`CHANGELOG.md`): `bench_startup.py`'s end-to-end metrics now measure from a
real temporary file, the cold-build path no longer double-counts duho's own
source parse, and `benchmarks/baseline.json` was regenerated to match the
current CI matrix (`3.9`, `3.13`). None of the pre-existing 0.3.x–0.5.x
numbers are comparable to a run against the current harness.

**Performance target for the next release:** regenerate
`benchmarks/baseline.json`'s `3.9` and `3.13` entries from an actual CI
benchmark-job run (not a local machine) before the next release, so
`check_baseline.py` gates on evidence instead of a stale local number for
`3.13` and skips `3.9` entirely, as it does today. Keep parser construction
in the sub-millisecond range as features grow, and no regression on parsing.
Compare against the 0.1.1 CI baseline below only informally — a like-for-like
CI comparison requires the regenerated baseline above.

---

## [0.2.0] — 2026-07-16

A small API release adding subcommand aliases and a cleaner dispatch idiom.

### What changed

- **Dispatch hook renamed `__run__` → `__call__` (breaking).** An `Args`
  instance is now directly callable — `instance()` runs the command — and
  `duho.main()` dispatches to `instance.__call__()`. The migration is a
  one-line rename per command class. This aligns the run hook with a real
  Python protocol rather than a bespoke dunder; the `getattr(instance,
  "__call__", None)` check still makes a subcommands-only class with no
  `__call__` raise `NotImplementedError`, so the "did you implement it?"
  guard is unchanged.
- **Subcommand aliases via `_parseraliases_`.** A list of alternate names on a
  subcommand class registers argparse aliases (e.g. `create`/`c`), all
  dispatching to the same `__call__`. Absence of the attr is the prior
  behavior (no aliases). Applied only when the class is registered as a
  subparser (the top-level `ArgumentParser` has no `aliases`).
- **`__version__` fallback for `--version`.** When `_version_` is unset, a
  class-level `__version__` string now populates `--version`, so an app already
  carrying the conventional dunder gets the flag for free. `_version_` still
  wins when both are set and remains the only form accepting `duho.AUTO`.

### Migration

Rename `def __run__(self)` to `def __call__(self)` on every command class. No
other change is required; aliases and the `__version__` fallback are additive.

### Performance

No perf-relevant changes — dispatch and version resolution are one-time,
non-hot-path operations. The 0.1.1 CI baseline for parser construction stands.

### Validation

- Full suite green on Python 3.9 (123 passed, 2 skipped — the PEP-604 cases)
  and 3.14 (124 passed, 1 skipped), including new tests for alias dispatch, the
  canonical-name path, the no-alias default, the `__version__` fallback, and
  `_version_`-wins-over-`__version__`.
- Local `python -m build` is isolated and hangs on the dev machine; the no-
  isolation `hatchling.build` path plus `twine check` was used for the local
  sanity build, and CI's release workflow performs the authoritative isolated
  build before publish.
- **Publication state:** prepared and committed on `master`; the `v0.2.0` tag
  is pushed only with per-release user consent (which triggers the PyPI
  publish).

---

## [0.1.1] — 2026-07-14

A documentation and accuracy release. No functional changes to the library —
the API and behavior are identical to 0.1.0.

### What changed

- **Documentation site** published at <https://jose-pr.github.io/duho/>, built
  from `docs/` with MkDocs Material and a generated API reference. Six guides
  cover declaring arguments, types and conversion, running your app,
  configuration layers, logging, and shell completion.
- **Corrected performance figures.** 0.1.0's notes quoted a speedup measured on
  a development laptop. Re-measured on a fixed CI runner, the honest number for
  parser construction is **40–70× faster**, not the ~350× a noisy machine
  suggested. The methodology is now an A/B in one process on one runner
  (`benchmarks/compare_cache.py`) rather than a comparison against a historical
  local run.
- **README fixes** — the `LICENSE` link is absolute so it resolves on PyPI, and
  a documentation badge points at the new site.

### Performance (CI baseline)

Median ms per `duho.parser()` call, ubuntu-latest, uncached path vs 0.1.x:

| | uncached | 0.1.x | |
| --- | --- | --- | --- |
| 2-field parser (3.13) | 10.51 ms | **0.154 ms** | 68× |
| 7-field parser (3.13) | 10.98 ms | **0.252 ms** | 44× |
| 2-field parser (3.9) | 10.64 ms | **0.178 ms** | 60× |
| 7-field parser (3.9) | 10.90 ms | **0.265 ms** | 41× |

Argument parsing is unchanged: 0.013 ms (3.13) / 0.018 ms (3.9) for a simple
parser.

### Validation evidence

- CI matrix green: Python 3.9–3.13 on Linux, plus 3.9 and 3.13 on Windows and
  macOS.
- `mkdocs build --strict` passes (now checked on every CI run, not only at
  release time — a docs break should never surface midway through an
  irreversible release).
- Benchmarks recorded on ubuntu-latest for 3.9 and 3.13.

### Publication state

Published to PyPI via the release workflow's Trusted Publishing (OIDC) — the
first release to exercise the automated path end to end. 0.1.0 was uploaded
manually, since Trusted Publishing cannot be registered for a project that does
not yet exist on the index.

---

## [0.1.0] — 2026-07-14

First public release. See `CHANGELOG.md` for the full feature list.

### The performance story

The original prototype rebuilt every parser from scratch on each call: for each
class in the MRO it re-read the source file with `inspect.getsource()` and re-ran
`ast.parse()`, with no caching anywhere. Parser construction cost tens of
milliseconds and scaled with the size of the *module* the class lived in — not
with the size of the class.

0.1.0 replaces that with a cached, module-level AST index keyed by `__qualname__`,
plus per-class caching of the resolved argument declarations. Building a parser is
a dictionary lookup in the common case.

Measured in CI (ubuntu-latest), median ms per `duho.parser()` call, comparing the
uncached and cached paths **in the same process on the same runner**
(`benchmarks/compare_cache.py`):

| | uncached (prototype path) | 0.1.0 | |
| --- | --- | --- | --- |
| Build a 2-field parser (3.13) | 10.51 ms | **0.154 ms** | 68× |
| Build a 7-field parser (3.13) | 10.98 ms | **0.252 ms** | 44× |
| Build a 2-field parser (3.9) | 10.64 ms | **0.178 ms** | 60× |
| Build a 7-field parser (3.9) | 10.90 ms | **0.265 ms** | 41× |

**Parser construction is roughly 40–70× faster**, and now takes a fraction of a
millisecond. Argument *parsing* was never the bottleneck — argparse does that work
— and is unchanged, at 0.013 ms (3.13) / 0.018 ms (3.9) for a simple parser.

#### About these numbers

- They come from **CI on a fixed runner**, not a developer machine. Reproduce with
  `python benchmarks/run.py` (steady-state) or `python benchmarks/compare_cache.py`
  (the A/B above); both report min/median/max per call across repeated samples.
- The uncached cost is dominated by filesystem reads and `ast.parse()`, so it
  varies a lot with the host. On a loaded Windows laptop the same uncached path
  measures ~90 ms rather than ~11 ms — which is precisely why the figures quoted
  here are the CI ones, and why local timings shouldn't be used to claim a
  regression or a speedup.

### Validation evidence

Verified in CI (run on the release commit's tree, all green):

- **Tests**: the full matrix passes — Python 3.9, 3.10, 3.11, 3.12, and 3.13 on
  Linux, plus 3.9 and 3.13 on Windows and macOS.
- **Benchmarks**: recorded on ubuntu-latest for 3.9 and 3.13 (numbers above).

Verified locally:

- **Tests**: 119 passed, 1 skipped on Python 3.12; 118 passed, 2 skipped on
  Python 3.9. All skips are intentional — the PEP 604 union tests can't run on
  3.9, and the "no TOML backend" test is unreachable on 3.11+ where `tomllib` is
  stdlib.
- **Package build**: wheel and sdist build cleanly; `twine check` passes on both.
  The wheel ships `py.typed`; the sdist includes tests and examples and excludes
  development scratch.

Gated by the release workflow at tag time:

- `mkdocs build --strict` for the documentation site.

### Publication state

Prepared. The `v0.1.0` tag has **not** been pushed — pushing it triggers the
release workflow, which builds, creates the GitHub release, and publishes to PyPI.
Publishing is irreversible, so the tag is pushed only on explicit go-ahead.

PyPI publishing uses Trusted Publishing (OIDC) rather than a stored token, which
requires a one-time registration on PyPI for this project. Because that
registration can't be created for a project that doesn't exist yet, the very first
release is uploaded manually; subsequent releases go through the workflow.
