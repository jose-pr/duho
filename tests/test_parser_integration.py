"""In-process parser-composition tests for duho.

These exercise ``_parser_`` composition (fields, subcommands, help text, type
conversion) entirely in-process -- they are NOT integration/e2e tests (the real
child-process ``sys.argv`` path lives in ``test_e2e.py``). Renamed from the
misleading ``test_integration.py`` (Plan 03 T2).
"""

import argparse
import io
import sys
from duho import Args, LoggingArgs


class DeployArgs(Args):
    """Deploy the application to a server."""
    environment: str
    "Target environment (prod, staging, dev)"
    ("--env", "-e")

    version: str = "latest"
    "Release version"
    ("--version", "-v")

    dry_run: bool = False
    "Preview without applying"
    ("--dry-run",)


def test_deploy_command_full():
    """Test a realistic deploy command."""
    parser = DeployArgs._parser_()

    # Parse typical deploy command
    args = parser.parse_args(["--env", "prod", "--version", "1.2.3", "--dry-run"])

    assert args.environment == "prod"
    assert args.version == "1.2.3"
    assert args.dry_run is True


def test_deploy_with_defaults():
    """Test deploy with default version."""
    parser = DeployArgs._parser_()
    args = parser.parse_args(["--env", "staging"])

    assert args.environment == "staging"
    assert args.version == "latest"
    assert args.dry_run is False


def test_deploy_short_flags():
    """Test deploy with short flags."""
    parser = DeployArgs._parser_()
    args = parser.parse_args(["-e", "dev", "-v", "2.0.0"])

    assert args.environment == "dev"
    assert args.version == "2.0.0"


class ServeArgs(Args):
    """Start development server."""
    host: str = "localhost"
    ("--host",)

    port: int = 8000
    ("--port",)


class BuildArgs(Args):
    """Build the project."""
    output: str
    "Output directory"
    ("--output",)


def test_subcommands():
    """Test building a CLI with multiple subcommands."""
    parser = argparse.ArgumentParser(prog="myapp")
    subparsers = parser.add_subparsers(dest="command")

    ServeArgs._parser_(subparsers, name="serve")
    BuildArgs._parser_(subparsers, name="build")

    # Parse 'serve' command
    args = parser.parse_args(["serve", "--port", "3000"])
    assert args.command == "serve"
    assert args.port == 3000

    # Parse 'build' command
    args = parser.parse_args(["build", "--output", "dist"])
    assert args.command == "build"
    assert args.output == "dist"


def test_help_output():
    """Test that help text is properly generated."""
    parser = DeployArgs._parser_()

    # Capture help output
    help_text = parser.format_help()
    assert "Deploy the application to a server" in help_text
    assert "--env" in help_text
    assert "Target environment" in help_text
    assert "--version" in help_text
    assert "--dry-run" in help_text


class AppConfig(LoggingArgs):
    """Application configuration."""
    config_file: str
    "Path to config file"
    ("--config",)

    output: str = "output.txt"
    "Output file"
    ("--output", "-o")

    compress: bool = False
    "Compress output"
    ("--compress", "-c")


def test_complex_workflow():
    """Test a more complex real-world scenario."""
    parser = AppConfig._parser_()

    # Simulate real CLI usage
    args = parser.parse_args([
        "--config", "app.yaml",
        "-o", "results.txt",
        "-c",
        "-v", "-v"
    ])

    assert args.config_file == "app.yaml"
    assert args.output == "results.txt"
    assert args.compress is True
    assert args.verbose == 2


class TransformArgs(Args):
    """Transform input data."""
    input_file: str
    "Input file"
    ("input",)

    output_file: str
    "Output file"
    ("output",)

    format: str = "json"
    "Output format"
    ("--format",)


def test_mixed_positional_and_flags():
    """Test mixing positional and flag arguments."""
    parser = TransformArgs._parser_()
    args = parser.parse_args(["data.csv", "result.json", "--format", "xml"])

    # Check positional args (dest may differ from field name)
    assert hasattr(args, "input_file") or hasattr(args, "input")
    assert hasattr(args, "output_file") or hasattr(args, "output")
    assert args.format == "xml"


def test_error_handling_missing_required():
    """Test that missing required arguments raise errors."""
    parser = DeployArgs._parser_()

    try:
        parser.parse_args([])
        assert False, "Should have raised SystemExit"
    except SystemExit:
        # Expected: no --env provided
        pass


class TypedArgs(Args):
    """Arguments with type conversion."""
    count: int
    "Number of items"
    ("--count",)


def test_error_handling_type_conversion():
    """Test that type conversion errors are caught."""
    parser = TypedArgs._parser_()

    try:
        parser.parse_args(["--count", "not_a_number"])
        assert False, "Should have raised SystemExit"
    except SystemExit:
        # Expected: cannot convert "not_a_number" to int
        pass
