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

__version__ = "0.5.1"


def parser(cls, *args, **kwargs):
    """Build an ArgumentParser for an Args class.

    Public module-level entry point (delegates to cls._parser_).
    """
    return cls._parser_(*args, **kwargs)


def __getattr__(name):
    """Lazily import the ``agenthelp`` submodule on first attribute access (PEP 562).

    ``duho.agenthelp`` is a feature module only touched when agent help actually
    fires (the ``AGENT_HELP`` trigger / ``--help-agents`` flag / ``print_agent_help``
    -- all of which import it lazily at call time). Keeping it OUT of ``import
    duho`` means a plain import resolves no extra submodule and pays no extra
    import cost, while ``duho.agenthelp`` (and ``import duho.agenthelp``) still
    work on demand.
    """
    if name == "agenthelp":
        import importlib

        module = importlib.import_module("." + name, __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
