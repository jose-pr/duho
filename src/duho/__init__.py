"""Duho: A declarative CLI framework for Python.

Build command-line applications with minimal boilerplate by declaring
your arguments and commands as Python classes.
"""

from .args import (
    Args,
    Arg,
    Argument,
    ArgumentBuilder,
    Extend,
    NS,
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


def build_parser(cls, *args, **kwargs):
    """Build an ArgumentParser for an Args class.

    Public module-level entry point (delegates to cls._build_parser_).
    """
    return cls._build_parser_(*args, **kwargs)


__all__ = [
    "Args",
    "Arg",
    "Argument",
    "ArgumentBuilder",
    "Extend",
    "LoggingArgs",
    "NS",
    "UpdateAction",
    "add_logging_level",
    "build_parser",
    "DefaultFormatter",
    "init_stderr_logging",
    "parse_loglevels",
]
