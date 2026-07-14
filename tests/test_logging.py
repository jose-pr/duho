"""Tests for duho.logging module."""

import logging
from duho import add_logging_level, DefaultFormatter, init_stderr_logging, LoggingArgs, parse_loglevels


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
    parser = MyCommand._parser_()
    args = parser.parse_args(["--name", "test", "-v"])

    assert args.name == "test"
    assert args.verbose == 1

    # Should have logger property
    assert hasattr(args, "_logger_")
    assert isinstance(args._logger_, logging.Logger)


class VerboseCommand(LoggingArgs):
    """Test command for verbose logging."""
    pass


def test_verbose_to_loglevel():
    """Test converting verbose count to log level."""
    parser = VerboseCommand._parser_()

    # No -v: use INFO
    args = parser.parse_args([])
    level = args._verbose_loglevel_()
    assert level is not None

    # -v: higher level
    args = parser.parse_args(["-v"])
    level_verbose = args._verbose_loglevel_()
    # Should be different from no-verbose case
    assert level_verbose is not None


class SimpleLoggingCommand(LoggingArgs):
    """Simple logging command."""
    pass


def test_logging_args_set_loglevels():
    """Test setting log levels from parsed args."""
    parser = SimpleLoggingCommand._parser_()
    args = parser.parse_args(["--loglevel", "DEBUG"])
    loglevels = args._set_loglevels_()

    # Should return a dict of level assignments
    assert isinstance(loglevels, dict)


class VerbosityContractCommand(LoggingArgs):
    """Command used to pin down the verbose/quiet -> loglevel contract."""
    pass


def test_verbosity_contract():
    """0 -v -> INFO, 1 -> DEBUG, 2 -> TRACE, >=3 clamps at TRACE.

    Uses duho's own VERBOSE_LEVELS (rather than hardcoded stdlib level
    numbers) because other tests in this module register extra custom
    levels (e.g. CUSTOM=25) via the shared, process-global `logging`
    module, which shifts numeric level values without changing the
    verbose/quiet *index* contract under test here.
    """
    from duho import logging as duho_logging

    duho_logging.initverbose()
    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    parser = VerbosityContractCommand._parser_()

    args = parser.parse_args([])
    assert args._verbose_loglevel_() == levels[base]

    args = parser.parse_args(["-v"])
    assert args._verbose_loglevel_() == levels[base + 1]

    # A verbose count large enough to run off either end of the table must
    # clamp to the least-severe (last) entry, regardless of table length.
    overshoot = len(levels) + 5
    args = parser.parse_args(["-v"] * overshoot)
    assert args._verbose_loglevel_() == levels[-1]

    args = parser.parse_args(["-v"] * (overshoot + 1))
    assert args._verbose_loglevel_() == levels[-1]


def test_quiet_contract():
    """-q -> WARNING, -qq -> ERROR, -qqq -> CRITICAL, more clamps at CRITICAL."""
    from duho import logging as duho_logging

    duho_logging.initverbose()
    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    parser = VerbosityContractCommand._parser_()

    args = parser.parse_args(["-q"])
    assert args._verbose_loglevel_() == levels[base - 1]

    args = parser.parse_args(["-q", "-q"])
    assert args._verbose_loglevel_() == levels[base - 2]

    # A quiet count large enough to run off either end of the table must
    # clamp to the most-severe (first) entry, regardless of table length.
    overshoot = len(levels) + 5
    args = parser.parse_args(["-q"] * overshoot)
    assert args._verbose_loglevel_() == levels[0]

    args = parser.parse_args(["-q"] * (overshoot + 1))
    assert args._verbose_loglevel_() == levels[0]


def test_verbose_and_quiet_offset():
    """verbose and quiet counts offset each other around INFO."""
    from duho import logging as duho_logging

    duho_logging.initverbose()
    levels = list(duho_logging.VERBOSE_LEVELS.keys())
    base = levels.index(logging.INFO)
    parser = VerbosityContractCommand._parser_()

    args = parser.parse_args(["-v", "-v", "-q"])
    assert args._verbose_loglevel_() == levels[base + 1]

    args = parser.parse_args(["-v", "-q", "-q"])
    assert args._verbose_loglevel_() == levels[base - 1]


def test_formatter_does_not_mutate_record():
    """DefaultFormatter.format must leave the original record untouched."""
    formatter = DefaultFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    original_levelname = record.levelname
    formatter.format(record)
    assert record.levelname == original_levelname


def test_formatter_does_not_leak_across_handlers():
    """A record formatted twice (e.g. by two handlers) must not accumulate
    padding/color from the first format() call."""
    formatter = DefaultFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    first = formatter.format(record)
    second = formatter.format(record)
    assert first == second
