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
    directory: "_Path",
) -> "_ty.Iterator[_ty.Tuple[int, str, _Path, _Opts]]":
    """Yield ``(NN, name, path, opts)`` for each step file in ``directory``.

    Filename modifiers (``!``/``:key`` -- see :func:`_parse_file_modifiers`)
    are stripped from the stem BEFORE the ``NN-name`` split, so a modifier never
    affects numeric-prefix/name parsing. Sorted by ``(NN, name)`` for a
    deterministic default order; ``_``-prefixed files are skipped (private/helper
    convention, same as discovery) -- checked against the RAW name so ``__main__.py``
    is never mistaken for a step regardless of modifiers.

    Matches the ``.py`` suffix with a case-SENSITIVE comparison via
    ``Path.suffix`` rather than ``Path.glob("*.py")``: on Windows, ``glob``
    matches ``10-a.PY`` too (the filesystem is case-insensitive), but Python's
    own import machinery refuses that spelling, so the file used to be
    imported-and-fail on Windows while POSIX silently ignored it -- the same
    directory behaved differently by OS.
    """
    found: "list[_ty.Tuple[int, str, _Path, _Opts]]" = []
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


def is_runpath_dir(path: "_Path") -> bool:
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
    path: "_Path",
    field: str,
    logger: "_logging.Logger",
) -> "list[str]":
    """Normalize a step's ``REQUIRED``/``BEFORE``/``AFTER`` to ``list[str]``.

    A bare string (``REQUIRED = "provision"``) is an easy slip: iterated
    directly it yields one-character "dependencies", and for ``BEFORE``/
    ``AFTER`` (whose missing names are a silent no-op) it does NOTHING with no
    diagnostic at all. Wrap it in a single-element list instead, and
    warn so the author notices.
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
    path: "_Path",
    strict: bool,
    logger: "_logging.Logger",
) -> int:
    """Resolve a step's ordering ``PRIORITY``, defaulting to its ``NN`` prefix.

    A non-numeric ``PRIORITY`` used to raise a bare, unattributed
    ``ValueError`` straight out of ``int()`` that aborted the run even in
    resilient mode; this names the step and file, and follows the
    normal strict-vs-resilient policy (falling back to the filename prefix
    when resilient).
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
    directory: "_Path",
    qualname: str,
    selection: "_Selection",
    logger: "_logging.Logger" = _LOGGER,
) -> "_ty.Tuple[list[_Step], list[str], set[str]]":
    """Resolve, filter, import and order one RunPath directory's steps.

    Returns ``(ordered_enabled_steps, present_names, broken_names)``:

    * ``present_names`` -- every step name found on disk, enabled or not (in
      file-listing order), so the caller's REQUIRED/``--rcopts`` diagnostics
      can tell "genuinely missing" from "present but disabled";
    * ``broken_names`` -- enabled steps whose IMPORT failed and were skipped
      resiliently, so a dependent step's ``REQUIRED`` can be resolved against
      them too (see the module docstring's "REQUIRED and a failed dependency").

    Only ENABLED steps (``selection.decide(name, opts.enabled)``) are ever
    imported: a disabled or deselected step's module body never runs,
    which also means it never enters :func:`_order_steps`'s graph, so it can
    never transitively reorder an enabled step via a stale ``PRIORITY``/
    ``BEFORE``/``AFTER``/``REQUIRED``.

    Two files that resolve to the SAME step name are a duplicate ONLY when both
    would actually be enabled (post ``--rcopts``): this always raises
    ``ValueError`` naming both files -- regardless of strict mode -- rather
    than one silently overwriting the other's ordering edges in the graph
    (which is keyed by name). A DISABLED duplicate never wins the name and
    never hides an enabled one, regardless of file order: whichever file among
    the same-named entries is enabled is the one that is loaded, and no
    warning/error is raised for the harmless disabled-vs-enabled case.

    A step whose **import** fails with an ``ImportError``/``NotImplementedError``
    (an *environmental* failure: a missing optional dependency, a not-yet-provided
    integration) is skipped/re-raised following the STEP'S OWN effective strict
    setting (``selection.step_strict``), not just the run-wide flag -- a step
    marked resilient by its own filename token stays resilient even when
    ``--rcopts strict`` is not given, and vice versa. Non-environmental body
    errors (``SyntaxError``, ``NameError``, ...) always surface -- they are
    bugs, not environment.
    """
    present_names: "list[str]" = []
    seen: "dict[str, _ty.Tuple[_Path, bool]]" = {}
    to_import: "list[_ty.Tuple[int, str, _Path, _Opts]]" = []
    for nn, name, path, opts in _iter_step_files(directory):
        enabled_here = selection.decide(name, opts.enabled)
        prior = seen.get(name)
        if prior is not None:
            prior_path, prior_enabled = prior
            if prior_enabled and enabled_here:
                # Two ENABLED steps racing for the same name is always an
                # error -- not just under `--rcopts strict` -- since letting
                # one silently drop out of the ordering graph regardless of
                # strictness would leave a REQUIRED/BEFORE/AFTER dependent
                # resolved against whichever file happened to be kept.
                raise ValueError(
                    "duho.runpath: duplicate step name %r: %s and %s"
                    % (name, prior_path, path)
                )
            if not enabled_here:
                # A disabled duplicate never displaces whatever is already
                # on record for this name (enabled or disabled) -- it simply
                # has no effect, so it can never hide an already-seen
                # enabled step of the same name.
                continue
            # `enabled_here` and not `prior_enabled`: this file takes over
            # the name from a disabled duplicate, which never competed for
            # it in the first place.
        else:
            present_names.append(name)
        seen[name] = (path, enabled_here)
        if enabled_here:
            to_import.append((nn, name, path, opts))

    steps: "list[_Step]" = []
    broken_names: "set[str]" = set()
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
    steps: "_ty.Sequence[_Step]",
    strict: bool = False,
    logger: "_logging.Logger" = _LOGGER,
) -> "list[_Step]":
    """Order steps via Kahn's algorithm over a merged REQUIRED/BEFORE/AFTER graph.

    Ties (no remaining predecessor) break by the step's RANK in the
    ``(priority, name)``-sorted base order, popped from a min-heap -- this is
    what makes the sort STABLE: the old implementation did whole
    "passes" over the remaining steps, which let a step reordered by one
    dependency jump ahead of every unrelated LATER step in the same pass
    instead of only past its own dependency. With the heap, only steps that
    are actually ready compete, always picking the lowest-ranked one among
    them, so an unrelated later step never overtakes a step whose dependency
    just became satisfied.

    Edge relations, merged into one predecessor graph exactly as before:

    * ``REQUIRED`` (hard dependency) and ``AFTER`` (soft ordering, same
      direction) both contribute directly as predecessors of the declaring
      step ("X before me");
    * ``BEFORE`` is the mirror direction ("me before X") and is rewritten onto
      the NAMED TARGET's predecessor set.

    A merged-graph name that matches no step in ``steps`` is silently dropped
    -- ordering never fails on a missing/disabled name; the run-time selection
    layer raises the missing/disabled ``REQUIRED`` warning/error (unchanged).
    Since :func:`_load_steps` now only ever passes ENABLED steps here, a
    disabled step is simply absent from this graph, so its edges can never
    reorder an enabled step -- this function no longer needs to know
    about "present but disabled" at all.

    A genuine cycle (spanning any mix of the three relations) is broken
    deterministically: when no ready step remains, the chain of unresolved
    predecessors starting from the smallest-ranked stuck step is walked until
    it revisits a node, which identifies the actual cycle (a step merely
    stuck BEHIND a cycle, without being part of it, is never included -- see
    below); the smallest-ranked ``(priority, name)`` step AMONG THAT CYCLE
    (not the smallest stuck step overall, which may not even be on the cycle)
    is then forced through, as if its remaining predecessors were satisfied,
    and a single warning names every step in the cycle. Forcing a step
    outside the cycle through would let it jump its own still-unsatisfied
    ``REQUIRED``/``AFTER`` predecessor, instead of only breaking the actual
    deadlock. Ordering then resumes normally: any step that was only blocked
    by the forced one gets emitted next via the heap, and only a NEW dead end
    (if the mix has more than one entangled cycle) triggers another
    warning+break.
    """
    ordered = sorted(steps, key=lambda s: (s.priority, s.name))
    by_name = {s.name: s for s in ordered}
    rank = {s.name: i for i, s in enumerate(ordered)}

    pending: "dict[str, set[str]]" = {}
    for s in ordered:
        deps = set(s.required) | set(s.after)
        pending[s.name] = {d for d in deps if d in by_name and d != s.name}
    for s in ordered:
        for target in s.before:
            if target in by_name and target != s.name:
                pending[target].add(s.name)

    successors: "dict[str, set[str]]" = {name: set() for name in by_name}
    for name, deps in pending.items():
        for dep in deps:
            successors[dep].add(name)

    heap = [rank[name] for name, deps in pending.items() if not deps]
    _heapq.heapify(heap)
    emitted: "list[_Step]" = []
    done: "set[str]" = set()

    def _emit(step: "_Step") -> None:
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
        # Name only the steps in the ACTUAL cycle containing the step about to
        # be forced through, not every step merely blocked behind it:
        # a step whose own dependency chain leads into a cycle, without being
        # part of it, must not be reported as if it were. Chase one unresolved
        # predecessor at a time from the step about to break; the walk must
        # eventually revisit a node (everything here is still pending), and
        # the loop from that revisit is the real cycle.
        start = stuck[0].name
        path: "list[str]" = []
        seen_at: "dict[str, int]" = {}
        node = start
        while node not in seen_at:
            seen_at[node] = len(path)
            path.append(node)
            remaining = pending.get(node) or set()
            if not remaining:
                break
            node = next(iter(remaining))
        cycle_names = path[seen_at[node] :] if node in seen_at else [start]
        # Force through the smallest-ranked step that is actually IN the
        # cycle, not `stuck[0]` (the smallest-ranked stuck step overall) --
        # `stuck[0]` can be a step merely blocked behind the cycle via its
        # own REQUIRED/AFTER on a cycle member, and forcing that one through
        # would let it run ahead of a predecessor it still legitimately
        # depends on, instead of only breaking the real deadlock.
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
