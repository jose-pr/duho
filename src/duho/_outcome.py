"""The command result type, its rendering, and the run-time error catch."""

from __future__ import annotations

import errno as _errno
import os as _os
import sys as _sys
import typing as _ty

from . import _compat as _compat
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


_DEVNULL: _ty.Optional[_ty.TextIO] = None


def _silence_stdout() -> None:
    """Point stdout at the null device so a closed pipe prints nothing at shutdown."""
    global _DEVNULL
    try:
        if _sys.stdout.fileno() == 1:
            fd = _os.open(_os.devnull, _os.O_WRONLY)
            try:
                _os.dup2(fd, 1)
            finally:
                _os.close(fd)
    except (OSError, ValueError, AttributeError):
        pass
    # One stream for the process: a caller that puts its own stdout back (a test)
    # must not leave an unclosed file behind.
    if _DEVNULL is None:
        _DEVNULL = open(_os.devnull, "w")
    _sys.stdout = _DEVNULL


def _stdout_closed(exc: BaseException) -> bool:
    """Whether ``exc`` is a write to a stdout nobody reads any more.

    Windows reports that as ``OSError(EINVAL)``, POSIX as ``EPIPE``. An error that
    names a file is not one, and an errno alone is not enough: stdout must also
    refuse a flush, so an unrelated ``EINVAL`` is left to ``_errors_``.
    """
    if isinstance(exc, BrokenPipeError):
        return True
    if not isinstance(exc, OSError) or exc.filename is not None:
        return False
    if exc.errno not in (_errno.EPIPE, _errno.EINVAL):
        return False
    try:
        _sys.stdout.flush()
    except (OSError, ValueError):
        return True
    except AttributeError:
        return False
    return False


def _as_broken_pipe(exc: OSError) -> BrokenPipeError:
    """``exc`` itself when it is a ``BrokenPipeError``, else one standing for it."""
    if isinstance(exc, BrokenPipeError):
        return exc
    return BrokenPipeError(_errno.EPIPE, "stdout is closed")


def _flush_stdout() -> _ty.Optional[BrokenPipeError]:
    """Flush stdout; a closed one is silenced and returned as a ``BrokenPipeError``."""
    try:
        _sys.stdout.flush()
    except OSError as exc:
        if exc.errno not in (_errno.EPIPE, _errno.EINVAL):
            raise
        _silence_stdout()
        return _as_broken_pipe(exc)
    except (AttributeError, ValueError):
        pass
    return None


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


def _mark_parser_exit(exc: SystemExit) -> None:
    """Tag a ``SystemExit`` raised while parsing, but not a reported command error."""
    if not getattr(exc, "_duho_reported_", False):
        exc._duho_parser_exit_ = True  # type: ignore[attr-defined]


def _call_report(
    report: _ty.Callable[..., object],
    message: str,
    code: int,
    exc: Exception,
    command: object,
) -> None:
    """Call a ``_report_error_`` hook with as many arguments as it can take (2 to 4)."""
    from .runtime._arity import accepts_positional  # lazy: runtime imports this

    if accepts_positional(report, 4):
        report(message, code, exc, command)
    elif accepts_positional(report, 3):
        report(message, code, exc)
    else:
        report(message, code)


def _report_caught(
    exc: Exception,
    prog: str,
    root_cls: _ty.Optional[type],
    command: object = None,
) -> _ty.Optional[int]:
    """Report ``exc`` and return its exit status, or ``None`` when it is to propagate.

    ``None`` means ``exc`` is not a ``CommandError`` or an ``_errors_`` match, or
    ``DUHO_TRACEBACK`` is set and the root has no ``_report_error_``. A hook is
    called the same with or without the variable and prints any traceback itself.
    The hook takes ``(message, code, error, command)``, or the first three, or the
    first two, by how many positional arguments it can take.
    """
    code = _error_status(exc, root_cls)
    if code is None:
        return None
    report = getattr(root_cls, "_report_error_", None)
    if report is None and _traceback_enabled():
        return None
    message = str(exc)
    if report is not None:
        _call_report(report, message, code, exc, command)
    elif message:
        print("%s: error: %s" % (prog, message), file=_sys.stderr)
    return code


def _reported_exit(status: int) -> SystemExit:
    """The ``SystemExit`` that ends a parse with an error already reported."""
    reported = SystemExit(status)
    reported._duho_reported_ = True  # type: ignore[attr-defined]
    return reported


def _end_if_reported(exc: Exception, prog: str, root_cls: _ty.Optional[type]) -> None:
    """Called from an ``except``: report ``exc`` and end with its status, or return.

    Returns (so the caller re-raises) when ``exc`` is not one to report, and always
    inside a tool call, which reports it itself.
    """
    if _compat._RUNNING_MODE.get() == "mcp":
        return
    status = _report_caught(exc, prog, root_cls)
    if status is not None:
        raise _reported_exit(status) from None


def _guard_dispatch(
    call: _ty.Callable[[], _ty.Any],
    prog: str,
    root_cls: _ty.Optional[type],
    command: object = None,
) -> _ty.Any:
    """Run the dispatch step; turn a caught error into ``prog: error: msg`` and its status.

    The root's ``_report_error_`` writes the report instead when it has one.
    Under ``DUHO_TRACEBACK`` the exception is re-raised unless the root has a
    ``_report_error_``. A closed stdout (``BrokenPipeError``, or an
    ``OSError`` with ``EPIPE``/``EINVAL`` that stdout cannot flush past) is never
    reported: stdout is silenced and the status is 1. Nothing else is caught.
    """
    try:
        return call()
    except SystemExit as exc:
        # An exit from a nested program's parse is not this program's parse.
        vars(exc).pop("_duho_parser_exit_", None)
        raise
    except Exception as exc:
        if _stdout_closed(exc):
            _silence_stdout()
            return 1
        code = _report_caught(exc, prog, root_cls, command)
        if code is None:
            raise
        return code
