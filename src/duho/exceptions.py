"""Errors a command raises to end with a message and an exit status."""

from __future__ import annotations

import typing as _ty

__all__ = [
    "CommandError",
    "ConfigDependencyError",
    "ConfigError",
    "UnsupportedFormatError",
    "UsageError",
]


class CommandError(Exception):
    """A command failed; ``duho.main``/``duho.app`` print ``message`` and return ``code``.

    ``str(exc)`` is the message. Under ``DUHO_TRACEBACK`` the exception propagates.
    Over MCP it becomes an error result whose text is the message.
    """

    def __init__(self, message: str = "", code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return self.message


class UsageError(CommandError):
    """The invocation was wrong, found out while running (status 2 by default)."""

    def __init__(self, message: str = "", code: int = 2) -> None:
        super().__init__(message, code)


class ConfigError(ValueError):
    """A configuration document could not be read or written.

    ``str(exc)`` is the message and never holds text of the document.
    ``path``, ``lineno`` and ``colno`` locate the problem when known.
    """

    def __init__(
        self,
        message: str = "",
        path: _ty.Optional[str] = None,
        lineno: _ty.Optional[int] = None,
        colno: _ty.Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.lineno = lineno
        self.colno = colno

    def __str__(self) -> str:
        return self.message


class UnsupportedFormatError(ConfigError):
    """No backend matches the format name or the file name."""


class ConfigDependencyError(ConfigError):
    """A backend needs a library that is not installed; ``extra`` names the pip extra."""

    def __init__(
        self,
        message: str = "",
        extra: _ty.Optional[str] = None,
        path: _ty.Optional[str] = None,
    ) -> None:
        super().__init__(message, path)
        self.extra = extra
