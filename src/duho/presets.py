"""Pre-configured argument classes for common patterns."""

import argparse as _argparse
import typing as _ty

from . import logging as _logging
from .args import Args, NS, UpdateAction
from .logging import parse_loglevels


class LoggingArgs(Args):
    """Args subclass with built-in --verbose and --loglevel support."""

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

    def _verbose_loglevel_(self):
        """Convert verbose count to a log level name."""
        loglevel = (
            list(_logging.VERBOSE_LEVELS.keys()).index(_logging.INFO)
            if self.verbose == _logging.NOTSET
            else self.verbose
        )
        loglevel = min(
            loglevel,
            len(_logging.VERBOSE_LEVELS) - 1,
        )
        return list(_logging.VERBOSE_LEVELS.keys())[loglevel]

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
