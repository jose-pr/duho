"""Duho: A declarative CLI framework for Python.

Build command-line applications with minimal boilerplate by declaring
your arguments and commands as Python classes.
"""

from __future__ import annotations

import typing as _ty

from ._compat import utf8_stdio
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
    finish_parse,
    main,
    InitParserKwargs,
    Meta,
    parse,
    parse_globals,
    print_agent_help,
    print_completion,
    subcommand,
    UpdateAction,
    value_sources,
)
from .args._meta import _A as _A
from ._outcome import Result
from .exceptions import CommandError, UsageError
from .discovery import (
    CmdBuilder,
    Command,
    ModuleCommand,
    discover_commands,
    discover_entry_points,
    import_from_path,
    is_class_command,
    is_module_command,
    register_command_provider,
    unregister_command_provider,
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
from .parsers import command_name
from .presets import LoggingArgs
from .qualname import PythonName, QualName
from .runtime import app, run, run_command
from .text import (
    camelcase,
    expand,
    gettext,
    kebabcase,
    parse_bool,
    pysafe,
    snakecase,
)

from .args._meta import _Parser as _Parser

__version__ = "0.7.1"


def parser(cls: type[_A], *args: object, **kwargs: object) -> _Parser[_A]:
    """Build an ArgumentParser for an Args class.

    Public module-level entry point (delegates to ``cls._parser_``, matching
    its own ``_Parser[Self]`` return typing so ``duho.parser(MyApp)`` keeps
    ``MyApp``'s type instead of widening to ``Any``). With subcommands, the
    instance ``parse_args`` returns is the SELECTED LEAF command's, not a
    ``MyApp``: it is typed as ``MyApp``, but a chosen subcommand's class need
    not derive from it.
    """
    return cls._parser_(*args, **kwargs)


def __getattr__(name):
    """Lazily import a feature submodule on first attribute access (PEP 562).

    ``duho.agenthelp`` and ``duho.completion`` are feature modules only touched
    when their feature actually fires (agent help's ``AGENT_HELP`` trigger /
    ``--help-agents`` flag / ``print_agent_help``; completion's
    ``--print-completion`` action / ``print_completion()``) -- both already
    import lazily at call time internally. Keeping them OUT of ``import duho``
    means a plain import resolves no extra submodule (and, for ``completion``,
    no extra ``shlex`` import) and pays no extra import cost, while
    ``duho.agenthelp``/``duho.completion`` (and ``import duho.agenthelp``/
    ``import duho.completion``) still work on demand.
    """
    if name in ("agenthelp", "completion"):
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
    "command_name",
    "CommandError",
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
    "finish_parse",
    "gettext",
    "import_from_path",
    "is_class_command",
    "is_module_command",
    "kebabcase",
    "LoggingArgs",
    "main",
    "InitParserKwargs",
    "Meta",
    "ModuleCommand",
    "parse",
    "parse_bool",
    "parse_globals",
    "parser",
    "print_agent_help",
    "print_completion",
    "pysafe",
    "PythonName",
    "QualName",
    "Result",
    "register_command_provider",
    "run",
    "run_command",
    "snakecase",
    "subcommand",
    "unregister_command_provider",
    "UsageError",
    "utf8_stdio",
    "UpdateAction",
    "value_sources",
    "add_logging_level",
    "DefaultFormatter",
    "init_stderr_logging",
    "parse_loglevels",
]
