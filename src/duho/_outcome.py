"""The command result type, its rendering, and the run-time error catch."""

from __future__ import annotations

import sys as _sys
import typing as _ty

from .exceptions import CommandError as _CommandError
from .logging import traceback_enabled as _traceback_enabled


class Result(int):
    """A command's return value that carries more than an exit status.

    ``int(result)`` is the exit status, so ``sys.exit(result)`` and every
    "is it an int" check keep working. ``value`` is the command's answer:
    ``duho.run`` prints it to stdout and an MCP client receives it as the result
    text. ``text`` is words for an MCP client only and is never printed on the
    command line. ``is_error`` is the explicit flag when one was given, else
    ``code != 0``.

    Raises ``TypeError`` when ``code`` is not an ``int`` (a ``bool`` is refused).
    """

    code: int
    value: object
    text: _ty.Optional[str]

    def __new__(
        cls,
        code: int = 0,
        *,
        value: object = None,
        text: _ty.Optional[str] = None,
        is_error: _ty.Optional[bool] = None,
    ) -> Result:
        if isinstance(code, bool) or not isinstance(code, int):
            raise TypeError(
                "Result code must be an int, got %s" % (type(code).__name__,)
            )
        self = super().__new__(cls, code)
        self.code = int(code)
        self.value = value
        self.text = text
        self._is_error = is_error
        return self

    @property
    def is_error(self) -> bool:
        """The explicit flag when one was given, else ``code != 0``."""
        return self.code != 0 if self._is_error is None else self._is_error

    def __repr__(self) -> str:
        parts = [repr(self.code)]
        if self.value is not None:
            parts.append("value=%r" % (self.value,))
        if self.text is not None:
            parts.append("text=%r" % (self.text,))
        if self._is_error is not None:
            parts.append("is_error=%r" % (self._is_error,))
        return "Result(%s)" % ", ".join(parts)


def _render(value: object) -> str:
    """A ``str`` as it is; anything else as indented JSON (``default=str``)."""
    if isinstance(value, str):
        return value
    import json

    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _error_status(
    exc: BaseException, root_cls: _ty.Optional[type]
) -> _ty.Optional[int]:
    """The exit status for ``exc``, or ``None`` when it is not one to catch.

    A ``CommandError`` carries its own; any other exception is looked up, by
    ``isinstance`` in the mapping's order, in the root's ``_errors_``.
    """
    if isinstance(exc, _CommandError):
        return exc.code
    mapping = getattr(root_cls, "_errors_", None)
    if mapping:
        for exc_type, code in mapping.items():
            if isinstance(exc, exc_type):
                return code
    return None


def _guard_dispatch(
    call: _ty.Callable[[], _ty.Any], prog: str, root_cls: _ty.Optional[type]
) -> _ty.Any:
    """Run the dispatch step; turn a caught error into ``prog: error: msg`` and its status.

    Under ``DUHO_TRACEBACK`` the exception is re-raised. Nothing else is caught.
    """
    try:
        return call()
    except Exception as exc:
        code = _error_status(exc, root_cls)
        if code is None or _traceback_enabled():
            raise
        message = str(exc)
        if message:
            print("%s: error: %s" % (prog, message), file=_sys.stderr)
        return code
