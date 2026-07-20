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
    AUTO,
    Choice,
    Cli,
    Cmd,
    command,
    Const,
    Count,
    Extend,
    main,
    Meta,
    NS,
    parse,
    parse_globals,
    print_agent_help,
    print_completion,
    UpdateAction,
    value_sources,
)
from . import agenthelp
from . import completion
from .discovery import (
    CmdBuilder,
    Command,
    ModuleCommand,
    discover_commands,
    discover_entry_points,
    register_command_provider,
)
from .env import Env
from .formatters import (
    ColorDefaultsFormatter,
    ColorHelpFormatter,
    DefaultsFormatter,
)
from .logging import (
    DefaultFormatter,
    add_logging_level,
    init_stderr_logging,
    parse_loglevels,
)
from .presets import LoggingArgs
from .qualname import PythonName, QualName
from .runtime import app, run_command
from .text import camelcase, expand, gettext, pysafe, snakecase

__version__ = "0.3.3"


def parser(cls, *args, **kwargs):
    """Build an ArgumentParser for an Args class.

    Public module-level entry point (delegates to cls._parser_).
    """
    return cls._parser_(*args, **kwargs)


__all__ = [
    "agenthelp",
    "Append",
    "app",
    "Args",
    "Arg",
    "Argument",
    "ArgumentBuilder",
    "AUTO",
    "camelcase",
    "Choice",
    "Cli",
    "Cmd",
    "CmdBuilder",
    "ColorDefaultsFormatter",
    "ColorHelpFormatter",
    "command",
    "Command",
    "completion",
    "Const",
    "Count",
    "DefaultsFormatter",
    "discover_commands",
    "discover_entry_points",
    "Env",
    "expand",
    "Extend",
    "gettext",
    "LoggingArgs",
    "main",
    "Meta",
    "ModuleCommand",
    "NS",
    "parse",
    "parse_globals",
    "parser",
    "print_agent_help",
    "print_completion",
    "pysafe",
    "PythonName",
    "QualName",
    "register_command_provider",
    "run_command",
    "snakecase",
    "UpdateAction",
    "value_sources",
    "add_logging_level",
    "DefaultFormatter",
    "init_stderr_logging",
    "parse_loglevels",
]
