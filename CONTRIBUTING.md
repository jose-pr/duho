# Contributing to Duho

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/jose-pr/duho.git
cd duho

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode with test dependencies
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=src/duho tests/
```

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

# The single-file parse/build micro-bench
python -m benchmarks.bench_parsing
```

### Benchmark regression gate

CI (the `benchmark` job in `.github/workflows/test.yml`) runs
`benchmarks/check_baseline.py`, which compares a fresh run against
`benchmarks/baseline.json` and **fails the build** on a structural regression:

- **warm metrics** (build/parse/tree/field-matrix medians): more than **1.5x**
  the baseline median;
- **startup deltas** (duho's added cost over bare python): more than **1.3x**
  the baseline. The delta normalizes out runner speed, so the bound is tighter.

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

- Follow PEP 8
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

## Reporting Issues

When reporting bugs, please include:
- Python version
- Duho version
- Minimal code example that reproduces the issue
- Expected vs. actual behavior

## Questions?

Open a discussion or issue on GitHub!
