"""Pre-configured argument classes for common patterns."""

import argparse as _argparse
import typing as _ty

from . import logging as _logging
from .args import Args, NS, UpdateAction
from .logging import parse_loglevels


class LoggingArgs(Args):
    """Args subclass with built-in --verbose and --loglevel support.

    ``LoggingArgs`` is a **data mixin** (verbosity fields + ``_set_loglevels_``
    + the ``_logger_`` property); it defines no ``__call__`` and is
    NOT itself runnable. Since Plan 13's ``Args``/``Cmd`` split, combine it
    with ``Cmd`` to get a runnable command with logging::

        class MyApp(LoggingArgs, Cmd):
            _version_ = "1.2.3"

            def __call__(self):
                self._logger_.info("running")
                return 0

    **Recommended base order: ``(LoggingArgs, Cmd)``** -- data mixin first,
    executable base last (reads "add logging to a command"). Both orders
    resolve correctly because ``LoggingArgs`` overrides no ``Cmd`` member;
    ``_logger_``/``_set_loglevels_`` come from ``LoggingArgs`` and
    ``__call__`` from ``Cmd`` regardless of order.

    Set a class attr ``_version_`` to opt into a ``--version`` flag; no
    separate preset class is needed. ``--version`` prints
    ``"%(prog)s 1.2.3"`` and exits 0. It is skipped if a ``version``-dest
    action already exists (e.g. supplied by a parent parser).
    """

    loglevels: _ty.Annotated[
        dict[str, int], NS(type=parse_loglevels, action=UpdateAction)
    ] = {}
    "Log Levels"
    ("--loglevel",)  # type:ignore

    verbose: _ty.Annotated[
        int, NS(action="count", help=lambda: _logging.VERBOSE_HELP)
    ] = 0
    "Verbose level"
    ("-v",)  # type:ignore

    quiet: _ty.Annotated[
        int, NS(action="count", help="Decrease verbosity (repeatable)")
    ] = 0
    "Quiet level"
    ("-q",)  # type:ignore

    def _verbose_loglevel_(self):
        """Convert verbose/quiet count to a log level name."""
        levels = list(_logging.VERBOSE_LEVELS.keys())
        base = levels.index(_logging.INFO)
        index = base + self.verbose - self.quiet
        index = max(0, min(index, len(levels) - 1))
        return levels[index]

    def _set_loglevels_(self):
        """Apply parsed log levels to loggers."""
        loglevels = self.loglevels.copy()
        loglevels.setdefault(self._logger_.name, LoggingArgs._verbose_loglevel_(self))
        for name, level in loglevels.items():
            _logging.getLogger(name).setLevel(level)

        return loglevels

    @property
    def _logger_(self):
        """Get logger scoped to this parser's name."""
        return _logging.getLogger(getattr(self, "_logger_name_", self._parsername_))


__all__ = ["LoggingArgs"]
