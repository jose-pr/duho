# Release Notes

Detailed notes per release: the narrative, the performance story, and the
validation evidence behind each version. `CHANGELOG.md` stays terse and
user-facing; this file is the durable record.

---

## [Unreleased]

Nothing yet.

**Performance target for the next release:** keep parser construction in the
sub-millisecond range as features grow, and no regression on parsing. Compare
against the 0.1.1 CI baseline below (never against local numbers).

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
