# duho benchmarks

Stdlib + duho only (no third-party deps), excluded from the sdist
(`pyproject.toml`'s `[tool.hatch.build.targets.sdist] exclude`), so this
directory never ships to users. Requires duho importable: run from a checkout
with `PYTHONPATH=src` (or an editable install).

## Scripts

- `run.py` -- the main structured runner. In-process **warm** metrics (parser
  build + parse, tree scaling, the field-type matrix) sampled min/median/max
  over repeated calls, plus `first_build.complex` (a parser build with every
  cache dropped, the cost a one-shot CLI pays) -- gated with the warm group.
  `--cold` also runs the **cold** (per-invocation, caches dropped) set,
  reported for insight but not gated.
  ```
  python benchmarks/run.py
  python benchmarks/run.py --cold
  python benchmarks/run.py --save              # write benchmarks/results/<name>.json
  python benchmarks/run.py --json PATH
  python benchmarks/run.py --name foo
  ```
- `bench_startup.py` -- fresh-process startup cost (interpreter spawn + `import
  duho` + one end-to-end build/parse), as a min-of-N over real subprocesses.
  The gated numbers are duho's added cost over bare `python -c pass`
  (`import_duho_delta`, `e2e_delta`). `e2e_build_parse` runs its snippet from a
  real temporary `.py` file (not `python -c`), because `-c` source has no
  `__file__` and duho's class-body introspection silently skips the AST scan
  for it -- `e2e_no_source` keeps that `-c`-based number too, for comparison,
  but it is informational only (not gated) since it never touches the
  AST/getsource path a real invocation always pays for.
  ```
  python benchmarks/bench_startup.py
  python benchmarks/bench_startup.py -n 20
  python benchmarks/bench_startup.py --save
  python benchmarks/bench_startup.py --json PATH
  ```
- `bench_discovery.py` -- cost of `discover_commands()` scanning a directory of
  N command modules, and of `app()` discovering + building + parsing +
  dispatching one command end-to-end. Informational (import cost is
  inherently one-shot / cache-sensitive), not gated.
  ```
  python benchmarks/bench_discovery.py
  python benchmarks/bench_discovery.py -n 5 --files 25
  python benchmarks/bench_discovery.py --save
  ```
- `compare_cache.py` -- an A/B, same-process comparison of COLD (per-invocation,
  caches dropped) vs WARM (cached) parser construction, to show how much the
  AST/declaration caches actually save. Shares its workloads and cache-dropping
  with `_bench.py` (see below), rather than keeping its own copy.
  ```
  python benchmarks/compare_cache.py
  ```
- `check_baseline.py` -- the CI regression gate: compares a fresh run's warm
  medians and startup deltas against `baseline.json` for the running Python's
  `major.minor`. Exits 1 on a regression beyond threshold, 0 otherwise
  (including when there is no baseline entry for this Python version, which is
  a SKIP, not a pass/fail). Normalises for runner speed before comparing (see
  "Calibration" below), so a uniformly slower/faster shared CI runner no
  longer trips it on its own.
  ```
  python benchmarks/check_baseline.py
  python benchmarks/check_baseline.py -n 15
  ```
- `update_baseline.py` -- regenerates `baseline.json`'s entry for the CURRENT
  interpreter only (other versions' entries are left untouched). Run ONLY
  after an intentional, understood performance change, on a machine of the
  same kind that will later be compared against it -- see "Baseline
  provenance" below.
  ```
  python benchmarks/update_baseline.py
  python benchmarks/update_baseline.py -n 15
  ```
- `_bench.py` -- not a script: the shared core (sample workloads, cache
  dropping, the sampler, the calibration workload, and the result-JSON
  envelope writer) that every script above imports from, so they all measure
  and report the same things the same way.

## Calibration (runner-speed normalisation)

`check_baseline.py` gates on a *ratio to threshold*, not a raw time -- but a
shared CI runner's raw speed varies run to run (same unchanged code measured
0.8x-1.6x of its own baseline across different runs; see "Baseline
provenance" below), which made the 1.5x/1.3x thresholds trip on ordinary
noise, not a real regression.

Two calibration references are used, one per measurement domain -- NOT one
shared ratio across both:

- **`_bench.calibration_metric()`** measures a fixed, duho-independent
  workload -- building and parsing a plain `argparse` parser of known size --
  in-process, the exact same way a warm metric is measured. It normalises the
  **warm** group.
- **`python -c pass`'s own median** -- `bench_startup.py` already measures
  this as `abs.python_pass` on every run -- is a bare subprocess spawn with
  no duho involved. It normalises the **startup** group.

A single shared ratio was tried first and rejected: measured directly on a
real confirming CI run, the in-process workload's ratio and the subprocess
spawn's ratio moved by *different* amounts from the SAME runner-speed swing
(python_pass 13-17% faster vs. the in-process workload 39% faster). Dividing
the startup delta -- itself a harmless 0.87x raw ratio -- by the unrelated
in-process ratio produced a false "1.39x REGRESSION". An in-process CPU-bound
loop and a fresh-process spawn (dominated by exec/loader/syscall overhead,
not raw clock speed) just don't scale together, so each group needs a
reference from its own domain for the cancellation to hold.

Each group's calibration ratio is `current/baseline` for its own reference;
a gated metric's own ratio is divided by its group's calibration ratio before
comparing to the threshold. The medians are stored as `calibration_ms`
(warm) and `calibration_subprocess_ms` (startup) in each `baseline.json`
version entry, alongside `warm`/`startup`. An entry from before these existed
has neither key -- `check_baseline.py` then falls back to an unnormalised 1.0
ratio for the affected group (the old, pre-calibration behavior) rather than
crashing.

## Result JSON schema

`run.py`, `bench_startup.py` and `bench_discovery.py` (the scripts that take
`--save`/`--json`) write the same envelope
(`_bench.result_envelope`/`_bench.save_result`):

```json
{
  "name": "duho-0.5.4-py314",
  "python": "3.14.0",
  "python_minor": "3.14",
  "platform": "...",
  "processor": "...",
  "timestamp": "2026-09-24T12:00:00+00:00",
  "metrics": {
    "build.simple": {"min_ms": 0.09, "median_ms": 0.10, "max_ms": 0.12},
    "...": {"...": "..."}
  }
}
```

`metrics` maps a metric name to `{min_ms, median_ms, max_ms}` (or, for a
handful of derived scalars such as the startup deltas, a bare float). **Compare
on median** -- a single sample hides real run-to-run noise, which is exactly
why every metric here is min/median/max over repeated samples rather than one
`timeit` average.

## Results directory

`--save` writes to `benchmarks/results/<name>.json`. Nothing in that directory is
committed: `baseline.json` is the only benchmark record in the repository, and a
saved file stays local unless you commit it. The `benchmark` CI job uploads its
JSON as a workflow artifact, which expires with the run.

To compare two saved runs, read `metrics[<name>]["median_ms"]` from each file
(a derived scalar such as a startup delta is a bare number).

## Reproduce a full pass

```
python benchmarks/run.py --cold --save
python benchmarks/bench_startup.py -n 20 --save
python benchmarks/bench_discovery.py --save
python benchmarks/compare_cache.py
python benchmarks/check_baseline.py -n 20
```

## Baseline provenance

`baseline.json` is what `check_baseline.py` gates CI on. A local run is a
sanity check, not baseline-grade evidence: machine speed differs enough that
the same delta measured on this box varied several-fold between a native
Windows run and a WSL run on identical hardware. The gate only makes sense
comparing runs produced the same way -- ideally the CI benchmark job's own
runner, for exactly the Python versions in its matrix
(`.github/workflows/test.yml`'s `benchmark` job).

`baseline.json` carries CI-matrix `3.9`/`3.13`/`3.14` entries. The gate
compares only the keys an entry holds, so a metric the harness starts to
measure is gated from the run that regenerates the baseline, and fails nothing
before it. Each entry comes from one benchmark job of one CI run: the
Regression gate step's log gives `calibration_ms` and the warm medians, and
that job's uploaded artifact gives `first_build.complex`, `python -c pass`
(`calibration_subprocess_ms`) and the startup deltas. Taking a group's
reference and its metrics from the same job is what makes the
ratio-normalisation meaningful. Of the startup deltas the harness reports,
the baseline records the ones named in `bench_startup.GATED_DELTAS`
(`import_duho_delta`, `e2e_delta`); `e2e_large_delta` is reported only, since
most of it is the interpreter compiling the padded probe file.
