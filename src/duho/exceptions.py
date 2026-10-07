"""Errors a command raises to end with a message and an exit status."""

from __future__ import annotations

__all__ = ["CommandError", "UsageError"]


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
