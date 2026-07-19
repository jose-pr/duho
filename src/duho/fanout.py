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
scope (a future add-on if a real need appears).

**Per-target logging.** While a target's work runs, log records it emits are
tagged with a ``[<target>]`` prefix so interleaved concurrent output stays
attributable. This is done with a single prefixing :class:`logging.Filter`
installed on the app's existing stderr handler(s) for the duration of the
fan-out and removed afterwards (no per-target handler churn, no leaked filter,
no permanent mutation of global logging config). The current target is carried in
a :class:`contextvars.ContextVar` set at the top of each worker call, so
concurrent worker threads tag their own records without cross-talk.

**Exit-code aggregation.** Each call's result is normalised to an exit code
(``None`` -> ``0``; an ``int`` as-is; an unhandled exception -> logged and treated
as ``1``) and the per-target codes are reduced with ``aggregate`` (default
:func:`max` -- any non-zero surfaces the worst code, ``0`` only if all targets
succeed; empty target list -> ``0``). Pass ``aggregate=any`` or a custom reducer
to change the policy.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import concurrent.futures as _futures
import contextlib as _contextlib
import contextvars as _contextvars
import logging as _logging
import typing as _ty

from .runtime import run_command as _run_command

__all__ = [
    "run_targets",
    "fan_out_command",
    "current_target",
    "TargetPrefixFilter",
    "target_logging",
]

_LOGGER = _logging.getLogger("duho")

#: The target whose work is currently running in this context. Set at the top of
#: each worker call (so it is thread-local by virtue of each worker thread having
#: its own context) and read by :class:`TargetPrefixFilter` to tag records. The
#: default ``None`` means "no active target" -- records are then left unprefixed.
current_target: "_contextvars.ContextVar[object]" = _contextvars.ContextVar(
    "duho_fanout_current_target", default=None
)


class TargetPrefixFilter(_logging.Filter):
    """A :class:`logging.Filter` that prefixes records with the current target.

    While a target's work runs, :data:`current_target` names it; this filter
    reads that context var and, when set, rewrites the record's message to
    ``[<target>] <original>``. It never drops a record (``filter`` always returns
    ``True``) -- it only annotates. When no target is active it is a no-op, so it
    is safe to leave installed across code that is not fanning out (though
    :func:`target_logging` removes it promptly regardless).

    The prefix is applied to ``record.msg`` with ``record.args`` cleared after the
    record is rendered once via ``record.getMessage()`` -- this way a
    ``%``-style logging call (``log.info("x=%s", x)``) is formatted first and the
    prefix wraps the finished text, rather than corrupting the format string.
    """

    def filter(self, record: "_logging.LogRecord") -> bool:
        target = current_target.get()
        if target is not None and not getattr(record, "_duho_target_tagged", False):
            record.msg = "[%s] %s" % (target, record.getMessage())
            record.args = ()
            record._duho_target_tagged = True  # type: ignore[attr-defined]
        return True


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
    :class:`TargetPrefixFilter`), maps ``None`` -> ``0`` and an ``int`` through,
    and turns an unhandled exception into a logged nonzero code (``1``) rather
    than aborting the whole fan-out. Runs in the worker thread, so the context
    var it sets is isolated to that thread.
    """
    token = current_target.set(target)
    try:
        try:
            result = func(target)
        except Exception:
            logger.exception("duho.fanout: target %r failed", target)
            return 1
        # Normalise inside the isolation boundary: a target returning a non-int,
        # non-None value must not abort the whole fan-out via an escaping
        # ValueError/TypeError from int() -- it is that one target's failure (M5).
        try:
            return 0 if result is None else int(result)
        except (TypeError, ValueError):
            logger.exception(
                "duho.fanout: target %r returned non-int %r", target, result
            )
            return 1
    finally:
        current_target.reset(token)


def run_targets(
    func: "_ty.Callable[[object], object]",
    targets: "_ty.Iterable[object]",
    *,
    max_workers: "int | None" = None,
    aggregate: "_ty.Callable[[_ty.Sequence[int]], int]" = max,
    logger: "_logging.Logger | None" = None,
) -> int:
    """Run ``func(target)`` for each target concurrently; return an aggregate code.

    The primary, general fan-out primitive. Each ``func(target)`` call runs on a
    :class:`~concurrent.futures.ThreadPoolExecutor` worker; its result is
    normalised to an exit code (``None`` -> ``0``, an ``int`` as-is, an unhandled
    exception -> logged and treated as ``1`` -- one target failing never aborts the
    others). The per-target codes are reduced by ``aggregate`` (default
    :func:`max`: any non-zero surfaces the worst code, ``0`` only if all succeed).
    An empty ``targets`` returns ``0`` (``aggregate`` is not called).

    * ``max_workers`` -- forwarded to the pool; ``None`` lets
      :class:`~concurrent.futures.ThreadPoolExecutor` choose (cap it to mirror a
      ``--parallel`` flag; ``max_workers=1`` serialises).
    * ``aggregate`` -- the reducer over the list of per-target codes. Pass
      :func:`any`/:func:`all` or a custom callable for a different policy (its
      truthy/int return is coerced with ``int`` by the caller of this function's
      result if needed -- ``max``/``any``/``all`` already return usable values).
    * ``logger`` -- where a target exception is reported and whose handlers carry
      the ``[<target>]`` prefix filter for the duration; defaults to the ``"duho"``
      logger (the prefix filter is installed on the effective handlers, i.e. the
      root's when ``"duho"`` propagates, so app-configured stderr output is tagged).

    Per-target log prefixing is active only inside this call: the filter is
    installed on entry and removed on return (see :func:`target_logging`), so a
    log line emitted after ``run_targets`` returns is unprefixed.
    """
    active_logger = logger if logger is not None else _LOGGER
    target_list = list(targets)
    if not target_list:
        return 0

    with target_logging(active_logger):
        with _futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(_run_one, func, target, active_logger)
                for target in target_list
            ]
            codes = [future.result() for future in futures]

    return int(aggregate(codes))


def fan_out_command(
    command: object,
    make_instance: "_ty.Callable[[object], object]",
    targets: "_ty.Iterable[object]",
    *,
    context: object = None,
    max_workers: "int | None" = None,
    aggregate: "_ty.Callable[[_ty.Sequence[int]], int]" = max,
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
        return _run_command(
            _ty.cast("_ty.Any", command), instance, context=context
        )

    return run_targets(
        _run_for,
        targets,
        max_workers=max_workers,
        aggregate=aggregate,
        logger=logger,
    )
