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
