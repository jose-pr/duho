"""Tests for the module-level duho.parser() / duho.parse() entry points (Plan 08)."""

import argparse
import typing as ty

import duho
from duho import Args


class Deploy(Args):
    """Deploy the application."""

    env: str
    "Target environment"
    ("--env", "-e")

    version: str = "1"
    "Release version"
    ("--version",)


def test_duho_parser_returns_parser():
    """duho.parser(Cls) is the renamed module-level entry point."""
    parser = duho.parser(Deploy)
    assert isinstance(parser, argparse.ArgumentParser)
    args = parser.parse_args(["--env", "prod"])
    assert args.env == "prod"
    assert isinstance(args, Deploy)


def test_parse_type_branch():
    """duho.parse(Cls, argv) builds + parses in one call."""
    result = duho.parse(Deploy, ["--env", "prod", "--version", "3"])
    assert isinstance(result, Deploy)
    assert result.env == "prod"
    assert result.version == "3"


def test_parse_instance_branch_overrides_and_precedence():
    """duho.parse(instance, argv): instance values are defaults, CLI overrides win,
    the original instance is left unmutated, and a new instance is returned."""
    base = Deploy(env="staging", version="1")
    result = duho.parse(base, ["--version", "2"])

    assert result.env == "staging"  # carried over from the instance
    assert result.version == "2"  # CLI override wins
    assert base.env == "staging"
    assert base.version == "1"  # base is untouched
    assert result is not base
    assert type(result) is Deploy


class RequiredArgs(Args):
    """A required field with no class default."""

    name: str
    "Required name, no default"
    ("--name",)


def test_parse_instance_branch_unrequires_required_field():
    """A required field (no class default) that the instance already carries a
    value for becomes effectively optional for that parse() call."""
    base = RequiredArgs(name="from-instance")
    # No SystemExit even though --name is required and not supplied on argv.
    result = duho.parse(base, [])
    assert result.name == "from-instance"
    assert result is not base
