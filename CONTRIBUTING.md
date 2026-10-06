# Contributing to Duho

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/jose-pr/duho.git
cd duho

# Create a virtual environment (name it by interpreter/OS/arch so more than
# one Python can coexist under .venv/, e.g. a 3.9 floor and 3.14 side by side)
python -m venv .venv/3.14-posix-x86_64
source .venv/3.14-posix-x86_64/bin/activate  # Windows: .venv\3.14-nt-amd64\Scripts\activate

# Install in development mode with test dependencies and the extras the
# suite exercises -- ".[dev]" alone silently skips the config-file and
# colorama tests instead of running them
pip install -e ".[dev,colorama,config]"
```

duho targets the latest stable Python as well as the floor declared in
`pyproject.toml`'s `requires-python` (currently 3.9). Create a second venv for
the floor version (`.venv/3.9-<os>-<arch>`) and run the suite there too before
sending a change that touches parsing/typing behavior — some bugs only exist
on the older end of the supported range.

## Running Tests

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=src/duho tests/
```

Missing the `config`/`colorama` extras produces clean, reasoned skips (not
failures) for the tests that need them.

CI also fails a pull request on two checks that `pytest` does not run:

```bash
python -m black --check src tests examples benchmarks

pip install -e ".[dev,docs,colorama,config]"   # the `docs` extra holds mkdocs
mkdocs build --strict
```

`python -m black src tests examples benchmarks` applies the formatting.

## Running Benchmarks

The `benchmarks/` directory (excluded from the sdist; stdlib + duho only, no
extra deps) measures the costs that matter for a CLI: interpreter startup +
`import duho` + one cold build/parse, plus warm build/parse, subcommand-tree
scaling, the field-type matrix, and command discovery.

```bash
# Warm build/parse/tree/field metrics (add --cold for the per-invocation set)
python benchmarks/run.py --cold

# Fresh-process startup deltas (duho's cost over bare python)
python benchmarks/bench_startup.py

# Cold vs warm parser construction (shows what the caches save)
python benchmarks/compare_cache.py

# Discovery + dispatch of a generated 25-command directory
python benchmarks/bench_discovery.py
```

`run.py`, `bench_startup.py` and `bench_discovery.py` take `--save` (write
`benchmarks/results/<name>.json`) and `--json PATH`; both write the same JSON
envelope. Saved results are not committed: `benchmarks/baseline.json` is the
only benchmark record in the repository.

### Benchmark regression gate

CI (the `benchmark` job in `.github/workflows/test.yml`) runs
`benchmarks/check_baseline.py`, which compares a fresh run against
`benchmarks/baseline.json` and **fails the build** on a structural regression:

- **warm metrics** (build/parse/tree/field-matrix medians): more than **1.5x**
  the baseline median;
- **startup deltas** (duho's added cost over bare python): more than **1.3x**
  the baseline. The delta cancels the fixed per-process overhead shared by
  both sides of the subtraction (spawn + interpreter bootstrap), not the
  machine's clock speed — it is meaningful only because a baseline and its
  comparison run are produced on the same CI runner image.

Thresholds are intentionally generous because CI runner timing is noisy; a trip
means a real regression, not jitter. The baseline is keyed by Python
`major.minor`; a version with no committed entry is skipped (not failed).

**Updating the baseline (only after an intentional, understood perf change).**
Run on a quiet machine, once per Python version you can run locally, then commit
`benchmarks/baseline.json`:

```bash
python benchmarks/update_baseline.py        # merges the current interpreter's entry
```

Each invocation updates only the entry for the interpreter that runs it and
leaves the other versions untouched, so regenerate under each version you have.
Never regenerate the baseline just to make a red gate pass -- investigate the
regression first.

## Code Style

- Format with `black` (`python -m black src tests examples benchmarks`); the
  target version is Python 3.9
- Use type hints
- Keep functions focused and well-named

## Commit Guidelines

Follow the format: `type: description`

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `test:` Test additions/improvements
- `chore:` Build, CI, or tooling changes

Examples:
- `feat: add shell completion support`
- `fix: handle union types with None correctly`
- `docs: add subcommand examples`

## Pull Request Process

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Make your changes and add tests
3. Run `pytest` to ensure all tests pass
4. Commit with a clear message (see guidelines above)
5. Push to your fork and open a pull request

## Releasing

A release is a version tag; the maintainers cut it. A change that is meant to
ship needs:

1. the version bumped in both places it is written, `version` in
   `pyproject.toml` and `__version__` in `src/duho/__init__.py`
   (`tests/test_version_sync.py` fails when they differ);
2. a `CHANGELOG.md` entry, terse and user-facing, moved from `[Unreleased]` to
   the new version's heading;
3. a `RELEASENOTES.md` section with the narrative and the validation evidence.

Pushing a `v*` tag runs the Release workflow: the whole `test.yml` matrix
(including the benchmark gate and the strict docs build), a build of the sdist
and wheel, an install check of the built wheel on the floor and the latest
Python, a GitHub Release whose body is that version's `CHANGELOG.md` section,
and the PyPI upload.

## Reporting Issues

When reporting bugs, please include:
- Python version
- Duho version
- Minimal code example that reproduces the issue
- Expected vs. actual behavior

## Questions?

Open a discussion or issue on GitHub!
