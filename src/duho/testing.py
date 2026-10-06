"""Test helper: run a duho command line in-process and capture what it did.

Opt-in: core never imports this module (``import duho.testing`` to use it).
"""

from __future__ import annotations

import contextlib as _contextlib
import io as _io
import os as _os
import sys as _sys
import typing as _ty

from .args import main as _main
from .runtime import app as _app

__all__ = ["Result", "invoke"]


class Result(_ty.NamedTuple):
    """What :func:`invoke` observed: exit ``status`` and the captured streams."""

    status: int
    stdout: str
    stderr: str


def _status(code: object, stderr: _io.StringIO) -> int:
    """An exit code as ``sys.exit`` would report it: text goes to stderr, status 1."""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    stderr.write(str(code) + "\n")
    return 1


@_contextlib.contextmanager
def _environ(overrides: _ty.Optional[_ty.Mapping[str, str]]):
    """Apply ``overrides`` to ``os.environ``, restoring every touched key on exit."""
    saved = {key: _os.environ.get(key) for key in (overrides or {})}
    _os.environ.update(overrides or {})
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = old


def invoke(
    root: type,
    argv: _ty.Sequence[str] = (),
    *,
    env: _ty.Optional[_ty.Mapping[str, str]] = None,
    stdin: _ty.Optional[str] = None,
    **app_kwargs: _ty.Any,
) -> Result:
    """Run ``root`` with ``argv`` in-process and return a :class:`Result`.

    With no ``app_kwargs`` this is ``duho.main(root, argv)``; with any, it is
    ``duho.app(root, argv=argv, **app_kwargs)``. Standard output and error are
    captured; ``env`` is applied to ``os.environ`` for the call and restored;
    ``stdin`` is the text the command reads (empty when ``None``).

    A ``SystemExit`` (``--help``, a usage error, ``sys.exit``) becomes the
    ``status``; a returned ``None`` is ``0``. Text passed to ``SystemExit``
    is written to the captured stderr with status ``1``. Any other exception
    propagates, with the environment and streams restored.
    """
    out, err = _io.StringIO(), _io.StringIO()
    old_stdin = _sys.stdin
    _sys.stdin = _io.StringIO(stdin or "")
    try:
        # An ExitStack, not a parenthesized `with`: that form is 3.10 grammar.
        with _contextlib.ExitStack() as stack:
            stack.enter_context(_environ(env))
            stack.enter_context(_contextlib.redirect_stdout(out))
            stack.enter_context(_contextlib.redirect_stderr(err))
            try:
                if app_kwargs:
                    code = _app(root, argv=list(argv), **app_kwargs)
                else:
                    code = _main(root, list(argv))
            except SystemExit as exc:
                code = exc.code
            status = _status(code, err)
    finally:
        _sys.stdin = old_stdin
    return Result(status, out.getvalue(), err.getvalue())
