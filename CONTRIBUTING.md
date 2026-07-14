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

```bash
python -m benchmarks.bench_parsing
```

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
