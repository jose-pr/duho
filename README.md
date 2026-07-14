# Duho

**Duho** is a declarative CLI framework for Python that turns the complexity of building command-line applications into simple, type-safe class definitions.

Named after the sacred Taíno ceremonial stool—a symbol of power and authority—duho provides the **foundation** from which you command your application.

## Features

- **Declarative**: Define CLI arguments as class annotations—no boilerplate argparse setup
- **Type-safe**: Built-in type conversion and validation from Python type hints
- **Logging**: Integrated colored logging with configurable verbosity levels
- **Subcommands**: Easily compose multi-command CLI applications
- **Extensible**: Customize argument behavior with protocols and builders

## Quick Start

```python
from duho import Args

class MyApp(Args):
    name: str
    "The name to greet"
    ("--name",)
    
    count: int = 1
    "How many times to greet"
    ("--count",)

if __name__ == "__main__":
    parser = MyApp._build_parser_()
    args = parser.parse_args()
    for _ in range(args.count):
        print(f"Hello, {args.name}!")
```

Run it:

```bash
python app.py --name Alice --count 3
# Output:
# Hello, Alice!
# Hello, Alice!
# Hello, Alice!
```

## Installation

```bash
pip install duho
```

### Optional Dependencies

For colored output in logging:

```bash
pip install duho[colorama]
```

## Core Concepts

### Args: Declare Your CLI

Define arguments using class annotations. The docstring becomes the help text, and expressions after the annotation become argument flags:

```python
from duho import Args
import typing as ty

class Deploy(Args):
    """Deploy the application to production."""
    
    environment: str
    "Target environment (prod, staging, dev)"
    ("--env",)
    
    version: ty.Optional[str] = None
    "Release version (defaults to latest)"
    ("--version",)
    
    dry_run: bool = False
    "Preview changes without applying them"
    ("--dry-run",)
```

### Build and Parse

```python
parser = Deploy._build_parser_()
args = parser.parse_args()

print(f"Deploying to {args.environment} (dry-run: {args.dry_run})")
```

### Logging Integration

Combine with `LoggingArgs` for structured logging:

```python
from duho import LoggingArgs

class MyApp(LoggingArgs):
    command: str
    "The command to run"
    
    def run(self):
        self._set_loglevels_()
        logger = self._logger_
        logger.info(f"Running: {self.command}")
```

Control logging from the CLI:

```bash
python app.py mycommand -v                    # Verbose
python app.py mycommand --loglevel DEBUG      # Debug level
python app.py mycommand --loglevel foo:TRACE  # Module-specific level
```

### Subcommands

Build hierarchical CLIs with subparsers:

```python
from duho import Args

class Serve(Args):
    """Start the development server."""
    host: str = "localhost"
    ("--host",)
    port: int = 8000
    ("--port",)

class Build(Args):
    """Build the project."""
    output: str
    ("--output",)

if __name__ == "__main__":
    main_parser = argparse.ArgumentParser()
    subparsers = main_parser.add_subparsers()
    
    Serve._build_parser_(subparsers, name="serve")
    Build._build_parser_(subparsers, name="build")
    
    args = main_parser.parse_args()
```

## Documentation

Full documentation: https://jose-pr.github.io/duho/

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License. See [LICENSE](LICENSE) for details.
