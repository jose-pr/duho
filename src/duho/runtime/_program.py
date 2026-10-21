from __future__ import annotations

import sys as _sys
import typing as _ty

from .._outcome import Result as _Result
from .._outcome import (
    _as_broken_pipe,
    _flush_stdout,
    _render,
    _silence_stdout,
    _stdout_closed,
)
from .. import logging as _duho_logging
from ..logging import traceback_enabled as _traceback_enabled


def _status_of(outcome: object) -> int:
    """Print what ``outcome`` says to print and return the exit status."""
    if outcome is None:
        return 0
    if isinstance(outcome, _Result):
        if outcome.value is not None:
            print(_render(outcome.value))
            _sys.stdout.flush()
        return int(outcome)
    if isinstance(outcome, int):
        return int(outcome)
    print(_render(outcome))
    _sys.stdout.flush()
    return 0


def _exit_status(code: object) -> int:
    """The status ``sys.exit(code)`` would end the process with.

    ``None`` is 0 and an ``int`` is itself; anything else is printed to standard
    error, as the interpreter does, and is 1.
    """
    if code is None:
        return 0
    if isinstance(code, int):
        return int(code)
    print(code, file=_sys.stderr)
    return 1


_ExitPolicy = _ty.Union[
    bool,
    _ty.Literal["parse"],
    _ty.Callable[
        [int, _ty.Optional[BaseException]], _ty.Union[bool, _ty.Literal["parse"]]
    ],
]


@_ty.overload
def run(
    root: type,
    argv: _ty.Optional[_ty.Sequence[str]] = None,
    *,
    exit: _ty.Literal[True] = True,
    **app_kwargs: _ty.Any,
) -> _ty.NoReturn: ...


@_ty.overload
def run(
    root: type,
    argv: _ty.Optional[_ty.Sequence[str]] = None,
    *,
    exit: _ExitPolicy,
    **app_kwargs: _ty.Any,
) -> int: ...


def run(
    root: type,
    argv: _ty.Optional[_ty.Sequence[str]] = None,
    *,
    exit: _ExitPolicy = True,
    **app_kwargs: _ty.Any,
) -> int:
    """Run ``root`` as the program: dispatch, print the answer, and exit.

    Calls ``duho.main(root, argv)``, or ``duho.app(root, argv=argv, **app_kwargs)``
    when keywords are given, then ``sys.exit`` with the status. ``None`` is 0; a
    ``Result`` prints its rendered ``value`` (if not ``None``) and exits with its
    code; any other ``int`` is the status; any other value is printed (a ``str``
    as it is, else JSON) with status 0. ``KeyboardInterrupt`` exits 130, silently
    unless ``DUHO_TRACEBACK`` is set, which re-raises it; a closed stdout (a
    ``BrokenPipeError``, or on Windows ``OSError(EINVAL)``), from the command, from
    printing or from the final flush, exits 1 silently either way. A
    ``SystemExit`` passes through untouched.

    ``exit`` says when to end the process instead of returning the status:
    ``True`` always; ``False`` never, so a ``SystemExit`` becomes its status
    (``None`` is 0, an ``int`` itself, anything else is printed to standard error
    and is 1), ``--help`` and ``--version`` included; ``"parse"`` only for a
    ``SystemExit`` raised while the command line is parsed (``--help``,
    ``--version``, a usage error). A callable is called as ``f(status, error)``,
    ``error`` being the ``SystemExit``, ``KeyboardInterrupt`` or
    ``BrokenPipeError`` caught (else ``None``), and returns one of those.
    """
    _token = _duho_logging._enter_traceback_env(root)
    try:
        error: _ty.Optional[BaseException] = None
        try:
            if app_kwargs:
                from ._app import app as _app

                outcome = _app(root, argv=argv, **app_kwargs)
            else:
                from ..args._entry import main as _main

                outcome = _main(root, argv)
            status = _status_of(outcome)
        except SystemExit as exc:
            if exit is True:
                raise
            error, status = exc, _exit_status(exc.code)
        except KeyboardInterrupt as exc:
            if _traceback_enabled():
                raise
            error, status = exc, 130
        except OSError as exc:
            if not _stdout_closed(exc):
                raise
            _silence_stdout()
            error, status = _as_broken_pipe(exc), 1
        else:
            closed = _flush_stdout()
            if closed is not None:
                error, status = closed, 1
        policy = exit(status, error) if callable(exit) else exit
        if policy == "parse":
            policy = getattr(error, "_duho_parser_exit_", False)
        if policy is True:
            _sys.exit(status)
        return status
    finally:
        _duho_logging._exit_traceback_env(_token)
