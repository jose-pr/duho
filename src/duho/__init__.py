"""Duho: A declarative CLI framework for Python.

Build command-line applications with minimal boilerplate by declaring
your arguments and commands as Python classes.
"""

from .cli.args import Args, Argument, ArgumentBuilder
from .cli.utils import LoggingArgs, Extend, parse_loglevels
from .logging import add_logging_level, DefaultFormatter, init_stderr_logging

__version__ = "0.1.0"

__all__ = [
    "Args",
    "Argument",
    "ArgumentBuilder",
    "LoggingArgs",
    "Extend",
    "parse_loglevels",
    "add_logging_level",
    "DefaultFormatter",
    "init_stderr_logging",
]
