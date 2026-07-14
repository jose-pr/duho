# Release Notes

Detailed notes per release: the narrative, the performance story, and the
validation evidence behind each version. `CHANGELOG.md` stays terse and
user-facing; this file is the durable record.

---

## [Unreleased]

Nothing yet.

**Performance target for the next release:** keep parser construction in the
sub-millisecond range as features grow, and no regression on parsing. Compare
against 0.1.0 using CI benchmark runs (see the caveat below on local numbers).

---

## [0.1.0] — 2026-07-14

First public release. See `CHANGELOG.md` for the full feature list.

### The performance story

The original prototype rebuilt every parser from scratch on each call: for each
class in the MRO it re-read the source file with `inspect.getsource()` and re-ran
`ast.parse()`, with no caching anywhere. Parser construction cost tens of
milliseconds, and it grew with the size of the module the class lived in — not
with the size of the class.

0.1.0 replaces that with a cached, module-level AST index keyed by `__qualname__`,
plus per-class caching of the resolved argument declarations. Building a parser is
now a dictionary lookup in the common case.

Order-of-magnitude effect, measured locally on the same machine and interpreter:

| | prototype | 0.1.0 |
| --- | --- | --- |
| Build a simple parser | ~100 ms | ~1 ms or less |
| Build a 7-field parser | ~120 ms | ~1 ms or less |
| Parse arguments | ~0.03 ms | unchanged |

So: **parser construction went from tens of milliseconds to around a millisecond
or below — roughly two orders of magnitude** — while argument *parsing* was never
the bottleneck (argparse does that work) and is unchanged.

The prototype could not even complete this project's own benchmark suite: at
~100 ms per build, the 1000-iteration loop ran past a two-minute timeout.

#### Benchmark caveats — read before quoting these numbers

- These are **local** measurements on a single developer machine (Windows 11,
  Intel i5-1035G1 class), not CI runs. That machine shows run-to-run variance of
  several times on an unchanged commit, so the figures above are stated as
  orders of magnitude rather than precise multipliers on purpose.
- Reproduce with `python benchmarks/run.py`, which reports min/median/max per call
  over repeated samples rather than a single average.
- Treat any future *regression* or *speedup* claim as unproven until it comes from
  a CI run on consistent hardware.

### Validation evidence

Verified locally before tagging:

- **Tests**: 119 passed, 1 skipped on Python 3.12; 118 passed, 2 skipped on
  Python 3.9. All skips are intentional — the PEP 604 union tests can't run on
  3.9, and the "no TOML backend" test is unreachable on 3.11+ where `tomllib` is
  stdlib.
- **Package build**: wheel and sdist build cleanly; `twine check` passes on both.
  The wheel ships `py.typed`; the sdist includes tests and examples.
- **Examples**: both example CLIs run end to end and are covered by smoke tests.

Verified in CI at tag time (the release workflow gates on all of these):

- Test matrix across Linux, Windows, and macOS on Python 3.9 through 3.13.
- `mkdocs build --strict` for the documentation site.

### Publication state

Prepared. The `v0.1.0` tag has **not** been pushed — pushing it triggers the
release workflow, which builds, creates the GitHub release, and publishes to PyPI.
Publishing is irreversible, so the tag is pushed only on explicit go-ahead.

PyPI publishing uses Trusted Publishing (OIDC) rather than a stored token, which
requires a one-time registration on PyPI for this project. Because that
registration can't be created for a project that doesn't exist yet, the very first
release is uploaded manually; subsequent releases go through the workflow.
