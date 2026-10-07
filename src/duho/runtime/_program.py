from __future__ import annotations

import os as _os
import sys as _sys
import typing as _ty

from .._outcome import Result as _Result
from .._outcome import _render
from ..logging import traceback_enabled as _traceback_enabled


def _silence_stdout() -> None:
    """Point stdout at the null device so a closed pipe prints nothing at shutdown."""
    try:
        if _sys.stdout.fileno() == 1:
            _os.dup2(_os.open(_os.devnull, _os.O_WRONLY), 1)
    except (OSError, ValueError, AttributeError):
        pass
    _sys.stdout = open(_os.devnull, "w")


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


def run(
    root: type,
    argv: _ty.Optional[_ty.Sequence[str]] = None,
    **app_kwargs: _ty.Any,
) -> _ty.NoReturn:
    """Run ``root`` as the program: dispatch, print the answer, and exit.

    Calls ``duho.main(root, argv)``, or ``duho.app(root, argv=argv, **app_kwargs)``
    when keywords are given, then ``sys.exit`` with the status. ``None`` is 0; a
    ``Result`` prints its rendered ``value`` (if not ``None``) and exits with its
    code; any other ``int`` is the status; any other value is printed (a ``str``
    as it is, else JSON) with status 0. ``KeyboardInterrupt`` exits 130 and a
    ``BrokenPipeError`` exits 1, both silently, unless ``DUHO_TRACEBACK`` is set,
    which re-raises them. A ``SystemExit`` passes through untouched.
    """
    try:
        if app_kwargs:
            from ._app import app as _app

            outcome = _app(root, argv=argv, **app_kwargs)
        else:
            from ..args._entry import main as _main

            outcome = _main(root, argv)
        status = _status_of(outcome)
    except KeyboardInterrupt:
        if _traceback_enabled():
            raise
        status = 130
    except BrokenPipeError:
        if _traceback_enabled():
            raise
        _silence_stdout()
        status = 1
    _sys.exit(status)
