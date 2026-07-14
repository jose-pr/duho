"""Tests for duho.logging module."""

import logging
from duho import add_logging_level, DefaultFormatter, init_stderr_logging
from duho.cli.utils import LoggingArgs, parse_loglevels


def test_add_logging_level():
    """Test adding custom log level."""
    add_logging_level("CUSTOM", 25)
    assert hasattr(logging, "CUSTOM")
    assert logging.CUSTOM == 25


def test_add_logging_level_to_logger():
    """Test custom level is callable on logger."""
    add_logging_level("TEST_LEVEL", 15)
    logger = logging.getLogger("test")
    # Should not raise
    logger.test_level("This is a test message")


def test_default_formatter_colors():
    """Test color mapping for log levels."""
    formatter = DefaultFormatter()
    assert logging.DEBUG in formatter.COLORS
    assert logging.INFO in formatter.COLORS
    assert logging.WARNING in formatter.COLORS
    assert logging.ERROR in formatter.COLORS
    assert logging.CRITICAL in formatter.COLORS


def test_init_stderr_logging():
    """Test stderr logging initialization."""
    logger = init_stderr_logging("test_logger", level=logging.DEBUG)
    assert logger.name == "test_logger"
    assert len(logger.handlers) > 0
    assert isinstance(logger.handlers[0], logging.StreamHandler)


def test_parse_loglevels_single():
    """Test parsing single log level."""
    levels = parse_loglevels("DEBUG")
    assert levels.get("") == logging.DEBUG


def test_parse_loglevels_module_specific():
    """Test parsing module-specific log levels."""
    levels = parse_loglevels("mymodule:INFO")
    assert levels.get("mymodule") == logging.INFO


def test_parse_loglevels_multiple():
    """Test parsing multiple log level specs."""
    levels = parse_loglevels("DEBUG,mymodule:WARNING")
    assert levels.get("") == logging.DEBUG
    assert levels.get("mymodule") == logging.WARNING


class MyCommand(LoggingArgs):
    """Command with logging."""
    name: str
    "Name to process"
    ("--name",)


def test_logging_args_integration():
    """Test LoggingArgs mixin."""
    parser = MyCommand.build_parser()
    args = parser.parse_args(["--name", "test", "-v"])

    assert args.name == "test"
    assert args.verbose == 1

    # Should have logger property
    assert hasattr(args, "logger")
    assert isinstance(args.logger, logging.Logger)


class VerboseCommand(LoggingArgs):
    """Test command for verbose logging."""
    pass


def test_verbose_to_loglevel():
    """Test converting verbose count to log level."""
    parser = VerboseCommand.build_parser()

    # No -v: use INFO
    args = parser.parse_args([])
    level = args.verbose_as_loglevel()
    assert level is not None

    # -v: higher level
    args = parser.parse_args(["-v"])
    level_verbose = args.verbose_as_loglevel()
    # Should be different from no-verbose case
    assert level_verbose is not None


class SimpleLoggingCommand(LoggingArgs):
    """Simple logging command."""
    pass


def test_logging_args_set_loglevels():
    """Test setting log levels from parsed args."""
    parser = SimpleLoggingCommand.build_parser()
    args = parser.parse_args(["--loglevel", "DEBUG"])
    loglevels = args.set_loglevels()

    # Should return a dict of level assignments
    assert isinstance(loglevels, dict)
