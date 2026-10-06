from __future__ import annotations

import inspect as _inspect
import logging as _logging
import typing as _ty

from ..args._entry import _maybe_await as _maybe_await
from ..discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    is_module_command as _is_module_command,
)
from ..args._naming import _command_name as _command_name

_LOGGER = _logging.getLogger(__package__)


# `_command_name` (the "`_parsername_` if set, else the class name" rule) is
# imported from `duho.args`, the one definition `duho.discovery` shares too.


def _reject_coroutine(result: object, where: str) -> None:
    """Refuse a coroutine ``result`` from a module command's entrypoint/hooks.

    duho only ever awaits ``Cmd.__call__`` (via ``duho.args._maybe_await`` --
    a class command may declare ``async def __call__``, driven to completion
    with ``asyncio.run`` at the call site). A module command's ``main``/
    ``init``/``success``/``finally_`` are NOT awaited: before this, an
    ``async def`` hook silently produced a coroutine nothing ever ran, whose
    only symptom was an easy-to-miss "coroutine was never awaited"
    ``RuntimeWarning`` raised later from the coroutine's own ``__del__``. This
    turns that into an immediate, loud failure instead. Mirrors
    :func:`duho.runpath._reject_coroutine` -- a separate copy, since
    ``duho.runtime`` and ``duho.runpath`` intentionally don't import each other.
    """
    if _inspect.iscoroutine(result):
        result.close()
        raise TypeError(
            "duho.runtime: %s returned a coroutine; duho awaits only "
            "Cmd.__call__ -- module command hooks must be synchronous" % where
        )


def run_command(
    command: _Command,
    instance: object,
    *,
    context: object = None,
    adapter: _ty.Optional[
        _ty.Callable[
            [_ty.Callable[..., object]], _ty.Optional[_ty.Callable[..., object]]
        ]
    ] = None,
) -> _ty.Any:
    """Dispatch one already-resolved command against a parsed ``instance``.

    ``instance`` is the parsed args/command instance produced by parsing (for a
    class command it IS the command; for a module command it is the root/parent
    instance carrying the parsed globals). Returns what the command returns:
    ``None`` becomes ``0``, an ``int`` propagates, and anything else (e.g. a
    JSON-serialisable object) passes through unchanged.

    * **Class command** (a ``Cmd``): the parsed ``instance`` is itself the
      command, so this calls ``instance()`` (``Cmd.__call__`` is the entrypoint).
      Parsing already owns building the instance; there is no separate parse here.
    * **Module command** (:class:`ModuleCommand`): runs the lifecycle --
      ``ctx = command.init(instance)`` (identity/no-op default returning
      ``None``), then ``command.main(instance)`` and ``command.success(ctx,
      instance)`` inside a ``try`` whose ``finally`` always runs
      ``command.finally_(ctx, instance)``. If ``context`` is passed it overrides
      the ``init`` result (the driver builds the context once and threads it in).
      ``main``'s return value (``None`` -> ``0``) is the result; an
      exception from ``main`` propagates after ``finally_`` runs.

    ``adapter(entrypoint)``, when given, is called with a module command's
    entrypoint (:attr:`ModuleCommand.entrypoint`) and returns the callable to
    call with ``instance`` in its place; a falsy return keeps the entrypoint.
    It never applies to the lifecycle hooks, nor to a class command.

    No separate ``logger`` argument is threaded: hooks read ``instance._logger_``.
    For a module command, THIS driver ensures it is present before any hook
    runs: when ``instance`` has no ``_logger_`` of its own (a plain root, not
    ``LoggingArgs``-based), it is set to ``module_command._logger_for(instance)``
    (the ``"duho"`` fallback) so a hook written against the documented
    ``args._logger_`` convention never hits ``AttributeError``. Setting
    the attribute is best-effort: a root whose ``_logger_`` is a read-only
    property simply keeps using its own resolution.

    A module command's ``init``/``main``/``success``/``finally_`` are never
    awaited (unlike a class command's ``__call__``, see :func:`_maybe_await`):
    a coroutine returned by any of them is closed immediately and raises
    ``TypeError`` (see :func:`_reject_coroutine`), rather than silently never
    running.
    """
    if _is_module_command(command):
        module_command = _ty.cast(_ModuleCommand, command)
        if not isinstance(getattr(instance, "_logger_", None), _logging.Logger):
            try:
                instance._logger_ = module_command._logger_for(instance)  # type: ignore[attr-defined]
            except (
                Exception
            ):  # pragma: no cover - a property-bearing root may refuse the write
                pass
        ctx = context if context is not None else module_command.init(instance)
        _reject_coroutine(ctx, "%s init()" % _command_name(command))
        try:
            adapted = adapter(module_command.entrypoint) if adapter else None
            result = adapted(instance) if adapted else module_command.main(instance)
            _reject_coroutine(result, "%s main()" % _command_name(command))
            # `success` is the SUCCESS hook: run it only when main reported
            # success (None or exit code 0), not for a non-zero exit code.
            if result is None or result == 0:
                success_result = module_command.success(ctx, instance)
                _reject_coroutine(
                    success_result, "%s success()" % _command_name(command)
                )
        finally:
            # A raising `finally_` must not mask the original exception (if main
            # raised) nor the real exit code: log and swallow its error.
            try:
                fin_result = module_command.finally_(ctx, instance)
                _reject_coroutine(fin_result, "%s finally_()" % _command_name(command))
            except Exception:
                _LOGGER.exception(
                    "finally_ hook for command %r raised; ignoring",
                    _command_name(command),
                )
        return 0 if result is None else result

    # Class command: the parsed instance is the command; run it via __call__.
    # An ``async def __call__`` returns a coroutine; drive it to completion with
    # its own ``asyncio.run`` per call -- so a fan-out worker dispatching
    # the command per target gets an independent loop each time.
    result = _maybe_await(instance())  # type: ignore[operator]
    return 0 if result is None else result
