"""Opt-in target fan-out: run one callable over many targets, concurrently.

A CLI frequently needs to run the *same* command against a list of targets
(hosts, environments, datasets) and roll their exit codes into one. Core duho
dispatches exactly ONE command per run by design (:func:`duho.app` /
:func:`duho.run_command`); this module is the **opt-in** helper that fans that
one command out over :func:`duho.expand`-produced targets. Core never imports it
-- you ``import duho.fanout`` and call its functions.

**Threads-first, stdlib only.** CLI fan-out is typically I/O-bound (a subprocess
or a network round-trip per target), so a :class:`concurrent.futures.ThreadPoolExecutor`
suffices and keeps duho zero-dependency. An asyncio variant is deliberately out of
scope (a future add-on if a real need appears); an ``async def`` target callable
is still supported -- its coroutine is driven to completion the same way
:func:`duho.run_command` does (driving any returned coroutine to completion).

**Per-target logging.** While a target's work runs, log records it emits are
tagged with a ``[<target>]`` prefix so interleaved concurrent output stays
attributable. This is done with a single prefixing :class:`logging.Filter`
installed on the app's existing stderr handler(s) for the duration of the
fan-out and removed afterwards (no per-target handler churn, no leaked filter,
no permanent mutation of global logging config). The current target is carried in
a :class:`contextvars.ContextVar` set at the top of each worker call, so
concurrent worker threads tag their own records without cross-talk. The filter
never rewrites the record's own ``msg``/``args``; it defers rendering the
prefixed text until the record is actually formatted, by retagging the record
as a picklable :class:`_TargetRecord` whose ``getMessage`` adds the prefix, so a mismatched-args log call still fails the
same "--- Logging error ---" way it would outside a fan-out instead of raising
into the target, and a tagged record still survives ``pickle.dumps``
(:class:`logging.handlers.SocketHandler`/``QueueHandler``).

On Python 3.12+ the filter returns a tagged COPY of the record rather than
mutating the shared one in place (the stdlib's "a filter attached to a handler
may return a replacement ``LogRecord``" support -- see
:class:`TargetPrefixFilter`), so a handler on the same logger that never had
the filter installed sees the record exactly as emitted, never the
``[target]`` prefix. Before 3.12 that isolation does not exist in the stdlib:
the tag is set on the shared record in place, so an unfiltered sibling handler
also sees the prefix -- a documented limitation on 3.9-3.11, not something
this module can work around.

**Exit-code aggregation.** Each call's result is normalised to an exit code
(``None`` -> ``0``; an ``int`` as-is (including negative -- see below); a
``SystemExit`` -> its ``.code`` normalised the same way; an unhandled exception
-> logged and treated as ``1`` -- one target failing never aborts the others)
and the per-target codes are reduced with ``aggregate`` (default: a worst-by-
magnitude reducer, so ``0`` only if every target is ``0``; empty target list ->
``0``). A negative code (a POSIX subprocess killed by a signal reports
``-signum``) ranks as a failure under the default reducer instead of being
hidden by any succeeding (``0``) target the way ``max`` would hide it. Pass
``aggregate=any`` or a custom reducer to change the policy.

**Ctrl-C.** An interrupt while targets are still queued cancels the queued
ones (already-running targets are allowed to finish) and re-raises
``KeyboardInterrupt`` -- it does not silently drain the rest of the queue.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import concurrent.futures as _futures
import contextlib as _contextlib
import contextvars as _contextvars
import copy as _copy
import logging as _logging
import sys as _sys
import typing as _ty

from .args._entry import _maybe_await as _maybe_await
from .discovery import Command as _Command
from .logging import log_exception as _log_exception
from .runtime import run_command as _run_command

__all__ = [
    "run_targets",
    "fan_out_command",
    "current_target",
    "TargetPrefixFilter",
    "target_logging",
]

_LOGGER = _logging.getLogger(__name__)

#: The target whose work is currently running in this context. Set at the top of
#: each worker call (so it is thread-local by virtue of each worker thread having
#: its own context) and read by :class:`TargetPrefixFilter` to tag records. The
#: default ``None`` means "no active target" -- records are then left unprefixed.
current_target: "_contextvars.ContextVar[object]" = _contextvars.ContextVar(
    "duho_fanout_current_target", default=None
)


class _TargetRecord(_logging.LogRecord):
    """A :class:`logging.LogRecord` whose message renders as ``[<target>] <message>``.

    A module-level class (not a closure), so a tagged record survives
    ``pickle.dumps`` (``SocketHandler``, ``QueueHandler`` over a
    ``multiprocessing.Queue``). A handler such as ``QueueHandler`` folds the
    already-prefixed text, traceback included, into ``msg`` and clears ``args``
    to ``None``; ``getMessage`` then returns that ``msg`` unchanged instead of
    prefixing it again. Otherwise ``%``-expansion happens at format time, so a
    mismatched-args call fails in the handler's own error protection.
    """

    _duho_target_: object = None
    _duho_tagged_args_: object = None

    def getMessage(self) -> str:
        if self.args is None and self._duho_tagged_args_ is not None:
            return str(self.msg)
        return "[%s] %s" % (self._duho_target_, super().getMessage())


class TargetPrefixFilter(_logging.Filter):
    """A :class:`logging.Filter` that prefixes records with the current target.

    While a target's work runs, :data:`current_target` names it; this filter
    reads that context var and, when set, arranges for the record to render as
    ``[<target>] <original>``. It never drops a record (``filter`` always
    returns a true value) -- it only annotates. When no target is active it is
    a no-op, so it is safe to leave installed across code that is not fanning
    out (though :func:`target_logging` removes it promptly regardless).

    The prefix is applied by retagging the record as a :class:`_TargetRecord`,
    whose ``getMessage`` renders ``[<target>] ...`` -- deferred until whatever
    formats the record (a :class:`logging.Formatter`, or a handler that calls
    ``record.getMessage()`` directly) actually calls it. Rendering eagerly
    here, outside the ``emit``/``handleError`` protection every handler gives its
    own formatting, would let a mismatched-``%``-args log call raise a
    ``TypeError`` straight into the target's code; deferring it means that call
    fails exactly the way it would outside a fan-out (a "--- Logging error ---"
    notice, or nothing under ``logging.raiseExceptions = False``), not by
    marking the target as failed.

    A module-level class rather than a closure, so a tagged record survives
    ``pickle.dumps`` (:class:`logging.handlers.SocketHandler` and
    ``QueueHandler``), and a record a handler has already rewritten (prefix and
    traceback folded into ``msg``, ``args`` cleared) renders as it was left.

    **Isolation across handlers.** A single filter instance is installed on
    every effective handler of the target logger (:func:`target_logging`), and
    ``logging`` shares ONE record object across all of them, so tagging it in
    place would leak the prefix into a handler that never had this filter
    added. On Python 3.12+, ``filter`` returns a shallow COPY of the record
    (a :class:`logging.LogRecord` return from a handler's filter replaces the
    record for THAT handler only -- new in 3.12) with the tag applied to the
    copy, leaving the original untouched for any sibling handler. Before 3.12
    the stdlib only ever treats a filter's return value as true/false, so
    there is no way to hand different handlers different records: the tag is
    set on the shared record in place, and an unfiltered sibling handler on
    the same logger sees the prefix too -- a documented limitation on
    3.9-3.11.
    """

    def filter(
        self, record: "_logging.LogRecord"
    ) -> "_ty.Union[bool, _logging.LogRecord]":
        target = current_target.get()
        if target is None or getattr(record, "_duho_target_tagged_", False):
            return True
        if _sys.version_info >= (3, 12):
            record = _copy.copy(record)
        record._duho_target_tagged_ = True  # type: ignore[attr-defined]
        record._duho_target_ = target  # type: ignore[attr-defined]
        record._duho_tagged_args_ = record.args  # type: ignore[attr-defined]
        record.__class__ = _TargetRecord
        return record if _sys.version_info >= (3, 12) else True


def _handlers_for(logger: "_logging.Logger") -> "list[_logging.Handler]":
    """Collect the effective handlers for ``logger`` (walking up to the root).

    Mirrors ``logging``'s own propagation: a logger with no handlers of its own
    still emits through an ancestor's handlers, so the prefixing filter must be
    attached to whichever handlers will actually format the records. Stops at the
    first ancestor whose ``propagate`` is false (same rule as ``Logger.callHandlers``).
    """
    handlers: "list[_logging.Handler]" = []
    current: "_logging.Logger | None" = logger
    while current is not None:
        handlers.extend(current.handlers)
        if not current.propagate:
            break
        current = current.parent
    return handlers


@_contextlib.contextmanager
def target_logging(
    logger: "_logging.Logger | None" = None,
) -> "_ty.Iterator[TargetPrefixFilter]":
    """Install a :class:`TargetPrefixFilter` on ``logger``'s handlers, then remove it.

    ``logger`` defaults to the root logger (the app's stderr handler set up by
    :func:`duho.init_stderr_logging` lives there, or on the ``"duho"`` logger
    which propagates to root). The filter is added to every effective handler
    (:func:`_handlers_for`) on entry and removed from exactly those handlers on
    exit -- even if the body raises -- so no filter is ever leaked and global
    logging config is left exactly as it was found. Yields the filter instance.

    Only handlers present at install time are tracked; a handler added *during*
    the fan-out is not touched (and so is cleaned up trivially -- there is nothing
    of ours on it).
    """
    target_logger = logger if logger is not None else _logging.getLogger()
    prefix_filter = TargetPrefixFilter()
    handlers = _handlers_for(target_logger)
    for handler in handlers:
        handler.addFilter(prefix_filter)
    try:
        yield prefix_filter
    finally:
        for handler in handlers:
            handler.removeFilter(prefix_filter)


def _run_one(
    func: "_ty.Callable[[object], object]",
    target: object,
    logger: "_logging.Logger",
) -> int:
    """Run ``func(target)`` in a target-tagged context and normalise to an exit code.

    Sets :data:`current_target` for the duration of the call (so records emitted
    by ``func`` -- or anything it calls -- are prefixed by an installed
    :class:`TargetPrefixFilter`), drives an ``async def`` result to completion
    (the same coroutine-driving helper :func:`duho.run_command` uses), and
    normalises the outcome: ``None`` -> ``0``,
    an ``int`` as-is (including negative -- see the module docstring's
    "Exit-code aggregation"), a :class:`SystemExit` -> its ``.code`` normalised
    the same way (``None`` -> ``0``, an ``int`` as-is, anything else -> logged and
    ``1``), and an unhandled ``Exception`` -> logged (honoring ``DUHO_TRACEBACK``
    via :func:`duho.logging.log_exception`) and treated as ``1``. A target
    raising or exiting never aborts the whole fan-out -- only that target's code
    is affected. ``KeyboardInterrupt`` (and any other non-``SystemExit``
    ``BaseException``) is deliberately let through uncaught.

    Runs in the worker thread, so the context var it sets is isolated to that
    thread.
    """
    token = current_target.set(target)
    try:
        try:
            result = _maybe_await(func(target))
        except SystemExit as exc:
            code = exc.code
            if code is None:
                return 0
            if isinstance(code, int):
                return code
            logger.error("target %r exited with non-int code %r", target, code)
            return 1
        except Exception as exc:
            _log_exception(logger, "target %r failed: %s", target, exc)
            return 1
        # Normalise inside the isolation boundary: a target returning a non-int,
        # non-None value must not abort the whole fan-out via an escaping
        # ValueError/TypeError from int() -- it is that one target's failure.
        try:
            return 0 if result is None else int(result)
        except (TypeError, ValueError):
            logger.error("target %r returned non-int %r", target, result)
            return 1
    finally:
        current_target.reset(token)


def _worst(codes: "_ty.Sequence[int]") -> int:
    """Reduce per-target exit codes to the worst one, ranking by magnitude.

    The default ``aggregate`` for :func:`run_targets`/:func:`fan_out_command`.
    Unlike :func:`max`, a negative code -- a POSIX subprocess killed by a signal
    reports ``-signum`` -- ranks as a failure rather than being hidden by any
    succeeding (``0``) target: codes are compared by absolute value, so
    ``[0, -9, 0]`` returns ``-9``. For all-non-negative codes this returns
    exactly what :func:`max` would, so ordinary (non-negative) exit codes are
    unaffected.
    """
    return max(codes, key=abs)


def run_targets(
    func: "_ty.Callable[[object], object]",
    targets: "_ty.Iterable[object]",
    *,
    max_workers: "int | None" = None,
    aggregate: "_ty.Callable[[_ty.Sequence[int]], int]" = _worst,
    logger: "_logging.Logger | None" = None,
) -> int:
    """Run ``func(target)`` for each target concurrently; return an aggregate code.

    The primary, general fan-out primitive. Each ``func(target)`` call runs on a
    :class:`~concurrent.futures.ThreadPoolExecutor` worker; its result is
    normalised to an exit code by :func:`_run_one` -- one target failing never
    aborts the others. The per-target codes are reduced by ``aggregate``
    (default: :func:`_worst`, a worst-by-magnitude reducer -- ``0`` only if every
    target succeeds, and a negative code is never hidden by a succeeding one).
    An empty ``targets`` returns ``0`` (``aggregate`` is not called).

    ``targets`` must be an iterable of individually-meaningful targets, not a
    bare ``str``/``bytes`` -- either of those would silently fan out one call per
    character/byte, so passing one raises :class:`TypeError`. Wrap a single
    target in a list.

    * ``max_workers`` -- forwarded to the pool; ``None`` lets
      :class:`~concurrent.futures.ThreadPoolExecutor` choose (cap it to mirror a
      ``--parallel`` flag; ``max_workers=1`` serialises). Validated up front
      (``ValueError`` for a non-positive value) even when ``targets`` is empty,
      so a bad ``--parallel 0`` is caught immediately rather than only once
      there is work.
    * ``aggregate`` -- the reducer over the list of per-target codes. Pass
      :func:`any`/:func:`all` or a custom callable for a different policy; its
      result is coerced with ``int`` by this function before being returned.
    * ``logger`` -- where a target exception is reported and whose handlers carry
      the ``[<target>]`` prefix filter for the duration; defaults to this
      module's own logger, ``"duho.fanout"`` (a child of ``"duho"``, so a handler
      configured on ``"duho"`` or the root still sees it via propagation).

    An ``async def`` ``func`` is supported: its coroutine result is driven to
    completion the same way :func:`duho.run_command` drives one
    (the same coroutine-driving helper :func:`duho.run_command` uses), so
    ``run_targets`` and ``fan_out_command`` agree
    with the rest of duho on async targets.

    Per-target log prefixing is active only inside this call: the filter is
    installed on entry and removed on return (see :func:`target_logging`), so a
    log line emitted after ``run_targets`` returns is unprefixed.

    An interrupt (``KeyboardInterrupt``) or an escaping ``SystemExit`` while
    targets are still queued cancels every target that has not yet started
    (already-running ones are allowed to finish) and re-raises -- queued work is
    never silently drained to completion after an abort.
    """
    if isinstance(targets, (str, bytes)):
        raise TypeError(
            "run_targets: targets must be an iterable of targets, not a single "
            "%s (which would fan out one call per character/byte) -- wrap it in "
            "a list" % type(targets).__name__
        )
    if max_workers is not None and max_workers <= 0:
        raise ValueError("max_workers must be greater than 0")

    active_logger = logger if logger is not None else _LOGGER
    target_list = list(targets)
    if not target_list:
        return 0

    with target_logging(active_logger):
        with _futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            try:
                futures = [
                    pool.submit(_run_one, func, target, active_logger)
                    for target in target_list
                ]
                codes = [future.result() for future in futures]
            except BaseException:
                # Ctrl-C / an escaping SystemExit: drop every queued target, but
                # let one already running finish (wait=True) while the prefix
                # filter is still installed. The second shutdown() from
                # __exit__ afterwards is a no-op.
                pool.shutdown(wait=True, cancel_futures=True)
                raise

    return int(aggregate(codes))


def fan_out_command(
    command: "_Command",
    make_instance: "_ty.Callable[[object], object]",
    targets: "_ty.Iterable[object]",
    *,
    context: object = None,
    max_workers: "int | None" = None,
    aggregate: "_ty.Callable[[_ty.Sequence[int]], int]" = _worst,
    logger: "_logging.Logger | None" = None,
) -> int:
    """Fan a single duho ``command`` out over targets, one parsed instance each.

    Thin sugar over :func:`run_targets` for the common case "run this one resolved
    command once per target". Because a parsed args/command instance is
    app-specific, the caller supplies ``make_instance(target) -> instance`` to
    build the per-target instance (e.g. copy the parsed globals and set a
    ``--host`` for this target); each is dispatched via :func:`duho.run_command`
    with the optional shared ``context``. Aggregation, concurrency, exception
    handling, and per-target ``[<target>]`` log prefixing are exactly
    :func:`run_targets`'.

    ``command`` is a resolved :class:`~duho.discovery.Command` (a ``Cmd`` subclass
    or a :class:`~duho.discovery.ModuleCommand`) -- the same object ``app`` would
    hand a ``dispatch`` callback. Returns the aggregated exit code.
    """

    def _run_for(target: object) -> int:
        instance = make_instance(target)
        return _run_command(command, instance, context=context)

    return run_targets(
        _run_for,
        targets,
        max_workers=max_workers,
        aggregate=aggregate,
        logger=logger,
    )
