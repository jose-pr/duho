"""Duho: A declarative CLI framework for Python.

Build command-line applications with minimal boilerplate by declaring
your arguments and commands as Python classes.
"""

from .args import (
    Append,
    Args,
    Arg,
    Argument,
    ArgumentBuilder,
    Choice,
    Const,
    Count,
    Extend,
    main,
    NS,
    parse,
    UpdateAction,
)
from .logging import (
    DefaultFormatter,
    add_logging_level,
    init_stderr_logging,
    parse_loglevels,
)
from .presets import LoggingArgs

__version__ = "0.1.0"


def parser(cls, *args, **kwargs):
    """Build an ArgumentParser for an Args class.

    Public module-level entry point (delegates to cls._parser_).
    """
    return cls._parser_(*args, **kwargs)


__all__ = [
    "Append",
    "Args",
    "Arg",
    "Argument",
    "ArgumentBuilder",
    "Choice",
    "Const",
    "Count",
    "Extend",
    "LoggingArgs",
    "main",
    "NS",
    "parse",
    "parser",
    "UpdateAction",
    "add_logging_level",
    "DefaultFormatter",
    "init_stderr_logging",
    "parse_loglevels",
]
