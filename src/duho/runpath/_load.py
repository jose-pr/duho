from __future__ import annotations

import heapq as _heapq
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .. import discovery as _discovery
from ..logging import log_exception as _log_exception

from ._selection import _Selection
from ._steps import (
    _Opts,
    _Step,
    _parse_file_modifiers,
    _parse_step_filename,
    _strict_or_warn,
)

_LOGGER = _logging.getLogger(__package__)


def _iter_step_files(
    directory: _Path,
) -> _ty.Iterator[_ty.Tuple[int, str, _Path, _Opts]]:
    """Yield ``(NN, name, path, opts)`` for each step file, sorted by ``(NN, name)``.

    Filename modifiers are stripped before the ``NN-name`` split; ``_``-prefixed
    files (checked on the raw name, so ``__main__.py``) are skipped. The ``.py``
    suffix is compared case-sensitively because ``Path.glob`` on Windows would
    match ``10-a.PY``, which the import machinery then refuses.
    """
    found: list[_ty.Tuple[int, str, _Path, _Opts]] = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix != ".py":
            continue
        if path.name.startswith("_"):
            continue
        clean_stem, opts = _parse_file_modifiers(path.stem)
        parsed = _parse_step_filename(clean_stem)
        if parsed is None:
            continue
        nn, name = parsed
        found.append((nn, name, path, opts))
    found.sort(key=lambda item: (item[0], item[1]))
    return iter(found)


def is_runpath_dir(path: _Path) -> bool:
    """True if ``path`` is a RunPath directory.

    A RunPath directory is a directory that (a) contains at least one
    ``NN-name.py`` step file and (b) is NOT a normal package (no ``__init__.py``)
    -- the "shape core doesn't understand" the provider predicate targets. A
    directory with an ``__init__.py`` is a package and is left to normal import.
    """
    if not path.is_dir():
        return False
    if (path / "__init__.py").exists():
        return False
    for _nn, _name, _p, _opts in _iter_step_files(path):
        return True
    return False


def _normalize_step_names(
    value: object,
    step_name: str,
    path: _Path,
    field: str,
    logger: _logging.Logger,
) -> list[str]:
    """Normalize a step's ``REQUIRED``/``BEFORE``/``AFTER`` to ``list[str]``.

    A bare string is wrapped in a list and warned about: iterated, it would
    yield one-character names (or silently do nothing for ``BEFORE``/``AFTER``).
    """
    if not value:
        return []
    if isinstance(value, str):
        logger.warning(
            "duho.runpath: step %r %s is a bare string %r in %s; treating it "
            "as a single name -- wrap it in a list to name more than one step",
            step_name,
            field,
            value,
            path,
        )
        return [value]
    return list(value)


def _resolve_priority(
    module: object,
    nn: int,
    name: str,
    path: _Path,
    strict: bool,
    logger: _logging.Logger,
) -> int:
    """A step's ordering ``PRIORITY``, defaulting to its ``NN`` prefix.

    A non-numeric value follows the strict-or-warn policy, falling back to ``NN``.
    """
    priority = getattr(module, "PRIORITY", None)
    if priority is None:
        return nn
    try:
        return int(priority)
    except (TypeError, ValueError) as exc:
        _strict_or_warn(
            "duho.runpath: step %r has a non-integer PRIORITY %r in %s: %s"
            % (name, priority, path, exc),
            strict,
            logger,
            warn_suffix="; using the filename prefix %d instead" % nn,
        )
        return nn


def _load_steps(
    directory: _Path,
    qualname: str,
    selection: _Selection,
    logger: _logging.Logger = _LOGGER,
) -> _ty.Tuple[list[_Step], list[str], set[str]]:
    """Resolve, filter, import and order one RunPath directory's steps.

    Returns ``(ordered_enabled_steps, present_names, broken_names)``:
    every name on disk, and enabled steps whose import failed and were skipped.
    Only enabled steps are imported, so a disabled one never enters the graph.
    Two enabled files with one name raise ``ValueError``; a disabled duplicate
    never hides an enabled one. ``ImportError``/``NotImplementedError`` on import
    follows the step's own strict setting; any other error surfaces.
    """
    present_names: list[str] = []
    seen: dict[str, _ty.Tuple[_Path, bool]] = {}
    to_import: list[_ty.Tuple[int, str, _Path, _Opts]] = []
    for nn, name, path, opts in _iter_step_files(directory):
        enabled_here = selection.decide(name, opts.enabled)
        prior = seen.get(name)
        if prior is not None:
            prior_path, prior_enabled = prior
            if prior_enabled and enabled_here:
                # Always an error, not only under strict: the graph is keyed
                # by name, so one file would silently drop out of it.
                raise ValueError(
                    "duho.runpath: duplicate step name %r: %s and %s"
                    % (name, prior_path, path)
                )
            if not enabled_here:
                # A disabled duplicate never displaces what is on record.
                continue
            # Enabled over a disabled duplicate: this file takes the name.
        else:
            present_names.append(name)
        seen[name] = (path, enabled_here)
        if enabled_here:
            to_import.append((nn, name, path, opts))

    steps: list[_Step] = []
    broken_names: set[str] = set()
    for nn, name, path, opts in to_import:
        try:
            module = _discovery.import_from_path(
                "duho._runpath." + qualname.replace(".", "_") + "." + name, path
            )
        except (ImportError, NotImplementedError) as exc:
            broken_names.add(name)
            if selection.step_strict(name, opts.strict):
                raise
            _log_exception(
                logger,
                "step %s failed to import; skipping: %s",
                name,
                exc,
                level=_logging.WARNING,
            )
            continue
        entrypoint = _discovery._module_entrypoint(module)
        if entrypoint is None:
            logger.warning(
                "%s has no entrypoint (%s); not a step",
                path,
                ", ".join(_discovery._ENTRYPOINT_NAMES),
            )
            continue
        priority = _resolve_priority(module, nn, name, path, selection.strict, logger)
        required = _normalize_step_names(
            getattr(module, "REQUIRED", None), name, path, "REQUIRED", logger
        )
        before = _normalize_step_names(
            getattr(module, "BEFORE", None), name, path, "BEFORE", logger
        )
        after = _normalize_step_names(
            getattr(module, "AFTER", None), name, path, "AFTER", logger
        )
        steps.append(
            _Step(
                name,
                priority,
                required,
                entrypoint,
                before=before,
                after=after,
                opts=opts,
            )
        )
    ordered = _order_steps(steps, strict=selection.strict, logger=logger)
    return ordered, present_names, broken_names


def _order_steps(
    steps: _ty.Sequence[_Step],
    strict: bool = False,
    logger: _logging.Logger = _LOGGER,
) -> list[_Step]:
    """Order steps via Kahn's algorithm over a merged REQUIRED/BEFORE/AFTER graph.

    ``REQUIRED`` and ``AFTER`` are predecessors of the declaring step; ``BEFORE``
    is rewritten onto the named target. Names matching no step are dropped.
    Ties pop from a min-heap by rank in the ``(priority, name)`` order, so an
    unrelated later step never overtakes one whose dependency just resolved.

    A cycle is broken at its smallest-ranked member, with one warning (or an
    error when strict) naming the cycle's steps; ordering then resumes.
    """
    ordered = sorted(steps, key=lambda s: (s.priority, s.name))
    by_name = {s.name: s for s in ordered}
    rank = {s.name: i for i, s in enumerate(ordered)}

    pending: dict[str, set[str]] = {}
    for s in ordered:
        deps = set(s.required) | set(s.after)
        pending[s.name] = {d for d in deps if d in by_name and d != s.name}
    for s in ordered:
        for target in s.before:
            if target in by_name and target != s.name:
                pending[target].add(s.name)

    successors: dict[str, set[str]] = {name: set() for name in by_name}
    for name, deps in pending.items():
        for dep in deps:
            successors[dep].add(name)

    heap = [rank[name] for name, deps in pending.items() if not deps]
    _heapq.heapify(heap)
    emitted: list[_Step] = []
    done: set[str] = set()

    def _emit(step: _Step) -> None:
        emitted.append(step)
        done.add(step.name)
        for succ in successors.get(step.name, ()):
            deps = pending.get(succ)
            if deps is None:
                continue
            deps.discard(step.name)
            if not deps and succ not in done:
                _heapq.heappush(heap, rank[succ])

    while len(done) < len(ordered):
        if heap:
            idx = _heapq.heappop(heap)
            step = ordered[idx]
            if step.name in done:
                continue
            _emit(step)
            continue
        stuck = [s for s in ordered if s.name not in done]
        # Find the actual cycle, not steps merely blocked behind it: follow
        # one unresolved predecessor at a time until a node repeats; the loop
        # from that repeat is the cycle.
        start = stuck[0].name
        path: list[str] = []
        seen_at: dict[str, int] = {}
        node = start
        while node not in seen_at:
            seen_at[node] = len(path)
            path.append(node)
            remaining = pending.get(node) or set()
            if not remaining:
                break
            node = min(remaining, key=rank.__getitem__)
        cycle_names = path[seen_at[node] :] if node in seen_at else [start]
        # Break at a step in the cycle, not `stuck[0]`, which may only be
        # blocked behind it and would jump a predecessor it still depends on.
        break_name = min(cycle_names, key=lambda n: rank[n])
        message = (
            "duho.runpath: unresolved dependency cycle among %s "
            "(check REQUIRED/BEFORE/AFTER)" % ", ".join(sorted(cycle_names))
        )
        _strict_or_warn(
            message, strict, logger, warn_suffix="; breaking at %r" % break_name
        )
        _emit(by_name[break_name])
    return emitted
