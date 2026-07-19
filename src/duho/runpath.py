"""RunPath: ordered step commands from a directory of numbered ``.py`` files.

**Opt-in module.** Core duho never imports this; you activate it explicitly with
``import duho.runpath`` (which auto-registers a command provider as an import
side-effect) or with an explicit :func:`register` call. Either way it plugs into
the shipped :func:`duho.register_command_provider` hook (Plan 13) and needs *zero*
core changes -- it is the first consumer of that seam.

What it adds
------------

A **RunPath** is a directory whose ``NN-name.py`` files are *steps* run in order:

* each step file ``NN-name.py`` contributes a step named ``name`` with numeric
  ordering key ``NN``;
* a step module may set module-level ``PRIORITY: int`` (overrides ``NN`` for
  ordering) and ``REQUIRED: list[str]`` (names of steps that must run first);
* a step's runnable body is its module-level entrypoint, resolved with the same
  ``main`` > ``run`` > ``call`` precedence discovery uses
  (``duho.discovery._ENTRYPOINT_NAMES``) -- no new convention is invented.

:class:`RunPathCmd` is a ``Cmd`` subclass whose ``__call__`` runs the ordered,
selected steps, logging through ``self._logger_``. Its one CLI field is
``--rcopts`` (``-O``), a comma-separated list of fnmatch selection patterns.

``--rcopts`` selection
----------------------

``--rcopts`` is a comma-separated list of fnmatch patterns matched against step
*names*, with two special markers:

* a leading ``!`` **disables** matching steps (``!*`` disables everything;
  ``!*,build`` disables all then re-enables ``build``);
* the literal token ``strict`` (or ``!strict`` to force-disable) toggles **strict
  mode** for the run.

Later patterns win, so ``!*,build`` = "disable all, then enable ``build``".

Strict vs. resilient
--------------------

The default is **resilient**, matching duho's discovery philosophy (a single bad
command is skipped, not fatal):

* an ``--rcopts`` pattern that matches **no** step is a warning, not an error;
* a step whose entrypoint **raises** is logged and skipped; the run continues.

Passing ``strict`` in ``--rcopts`` opts INTO erroring instead: an unmatched
pattern raises, and the first step to raise re-raises and stops the run. This is
the reconciliation of the predecessor design's strict flag with duho's resilient
default -- resilient unless you ask for strict.

All union annotations are quoted, and declared class-attr annotations avoid the
PEP-604 ``|`` operator (``typing.Union``/``Optional`` instead), so the module and
any ``RunPathCmd`` parser build cleanly on Python 3.9.
"""

import fnmatch as _fnmatch
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .args import Arg as _Arg, Cmd as _Cmd, Extend as _Extend
from . import discovery as _discovery

__all__ = ["RunPathCmd", "register", "unregister", "is_runpath_dir"]

_LOGGER = _logging.getLogger("duho")

#: The token in ``--rcopts`` that toggles strict mode (``strict`` enables,
#: ``!strict`` disables). A bare ``strict`` is a run-wide marker, not a step name.
_STRICT_TOKEN = "strict"


# --------------------------------------------------------------------------
# Step model
# --------------------------------------------------------------------------


class _Step:
    """One resolved step: a name, an ordering priority, deps, and an entrypoint.

    Built from a ``NN-name.py`` file. ``priority`` is ``PRIORITY`` if the module
    set one, else the ``NN`` numeric prefix. ``required`` is the module's
    ``REQUIRED`` list (step names that must run first), defaulting to empty.
    """

    __slots__ = ("name", "priority", "required", "entrypoint", "module")

    def __init__(
        self,
        name: str,
        priority: int,
        required: "_ty.Sequence[str]",
        entrypoint: "_ty.Callable[..., object]",
        module: object,
    ) -> None:
        self.name = name
        self.priority = priority
        self.required = list(required)
        self.entrypoint = entrypoint
        self.module = module

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "_Step(name=%r, priority=%r, required=%r)" % (
            self.name,
            self.priority,
            self.required,
        )


def _parse_step_filename(stem: str) -> "_ty.Optional[_ty.Tuple[int, str]]":
    """Parse a ``NN-name`` file stem into ``(NN, name)``, or None if not a step.

    A step file is ``<digits>-<name>.py``. The leading run of digits is the
    ordering prefix; everything after the first ``-`` is the step name. A stem
    with no numeric prefix, or no ``-``, is not a step file (helpers, ``__main__``,
    etc. are skipped).
    """
    if "-" not in stem:
        return None
    prefix, name = stem.split("-", 1)
    if not prefix.isdigit() or not name:
        return None
    return int(prefix), name


def _iter_step_files(directory: "_Path") -> "_ty.Iterator[_ty.Tuple[int, str, _Path]]":
    """Yield ``(NN, name, path)`` for each ``NN-name.py`` step file in ``directory``.

    Sorted by ``(NN, name)`` for a deterministic default order; ``_``-prefixed
    files are skipped (private/helper convention, same as discovery).
    """
    found: "list[_ty.Tuple[int, str, _Path]]" = []
    for path in directory.glob("*.py"):
        if path.name.startswith("_"):
            continue
        parsed = _parse_step_filename(path.stem)
        if parsed is None:
            continue
        nn, name = parsed
        found.append((nn, name, path))
    found.sort(key=lambda item: (item[0], item[1]))
    return iter(found)


def is_runpath_dir(path: "_Path") -> bool:
    """True if ``path`` is a RunPath directory.

    A RunPath directory is a directory that (a) contains at least one
    ``NN-name.py`` step file and (b) is NOT a normal package (no ``__init__.py``)
    -- the "shape core duho doesn't understand" the provider contract targets. A
    directory with an ``__init__.py`` is a package and is left to normal import.
    """
    if not path.is_dir():
        return False
    if (path / "__init__.py").exists():
        return False
    for _nn, _name, _p in _iter_step_files(path):
        return True
    return False


def _load_steps(
    directory: "_Path",
    qualname: str,
    strict: bool = False,
    logger: "_logging.Logger" = _LOGGER,
) -> "list[_Step]":
    """Import every step file and build ordered :class:`_Step` objects.

    Each file is imported under a synthesized-unique ``sys.modules`` key (reusing
    ``discovery._import_from_path`` / ``_unique_module_name`` so a loose
    ``json.py`` step never clobbers stdlib ``json``). Ordering priority is the
    module's ``PRIORITY`` if set, else the ``NN`` prefix; ``REQUIRED`` is read as
    the dep list. A file with no entrypoint (``main``/``run``/``call``) is not a
    runnable step -- skipped with a debug line.

    A step whose **import** fails with an ``ImportError``/``NotImplementedError``
    (an *environmental* failure: a missing optional dependency, a not-yet-provided
    integration) is skipped with a warning in the resilient default and re-raised
    under ``strict``. Non-environmental body errors (``SyntaxError``, ``NameError``,
    ...) always surface -- they are bugs, not environment.

    The returned list is ordered by ``(priority, name)`` and then reordered so
    every step runs after the present steps it ``REQUIRED`` (see
    :func:`_order_steps`).
    """
    steps: "list[_Step]" = []
    for nn, name, path in _iter_step_files(directory):
        mod_key = _discovery._unique_module_name(
            "duho._runpath." + qualname.replace(".", "_") + "." + name
        )
        try:
            module = _discovery._import_from_path(mod_key, path)
        except (ImportError, NotImplementedError) as exc:
            if strict:
                raise
            logger.warning(
                "duho.runpath: step %s failed to import; skipping: %s", name, exc
            )
            continue
        entrypoint = _discovery._module_entrypoint(module)
        if entrypoint is None:
            _LOGGER.debug(
                "duho.runpath: %s has no entrypoint (%s); not a step",
                path,
                ", ".join(_discovery._ENTRYPOINT_NAMES),
            )
            continue
        priority = getattr(module, "PRIORITY", None)
        if priority is None:
            priority = nn
        required = getattr(module, "REQUIRED", None) or []
        steps.append(_Step(name, int(priority), required, entrypoint, module))
    return _order_steps(steps, strict=strict, logger=logger)


def _order_steps(
    steps: "_ty.Sequence[_Step]",
    strict: bool = False,
    logger: "_logging.Logger" = _LOGGER,
) -> "list[_Step]":
    """Order steps by ``(priority, name)``, then topologically honor ``REQUIRED``.

    Starts from the priority/name order (the stable default) and does a
    dependency-respecting stable topological sort: a step is emitted only after
    every *present* step it ``REQUIRED`` has been emitted. A ``REQUIRED`` name
    that matches no present step is ignored here (the run-time selection layer is
    where a missing-dep warning/strict-error is raised, so ordering never fails).
    A dependency cycle is broken deterministically (a step whose deps can't all be
    satisfied is emitted in priority order once no further progress is possible),
    so ordering always terminates.
    """
    ordered = sorted(steps, key=lambda s: (s.priority, s.name))
    by_name = {s.name: s for s in ordered}
    emitted: "list[_Step]" = []
    done: "set[str]" = set()
    remaining = list(ordered)

    while remaining:
        progressed = False
        blocked: "list[_Step]" = []
        for step in remaining:
            unmet = [
                dep
                for dep in step.required
                if dep in by_name and dep not in done
            ]
            if unmet:
                blocked.append(step)
            else:
                emitted.append(step)
                done.add(step.name)
                progressed = True
        remaining = blocked
        if not progressed:
            # A cycle (or steps mutually blocked). Under strict this is an error;
            # in the resilient default emit the rest in priority order so ordering
            # is deterministic and always terminates.
            message = (
                "duho.runpath: unresolved REQUIRED cycle among %s"
                % ", ".join(s.name for s in remaining)
            )
            if strict:
                raise ValueError(message)
            logger.warning("%s; emitting in priority order", message)
            emitted.extend(remaining)
            break
    return emitted


# --------------------------------------------------------------------------
# --rcopts selection
# --------------------------------------------------------------------------


class _Selection:
    """A parsed ``--rcopts`` decision: per-step enable/disable + a strict flag.

    ``patterns`` is a list of ``(pattern, enabled)`` in declaration order; later
    entries win when several match a step. ``strict`` is the run-wide strict flag
    (default resilient). ``explicit`` records the patterns the user actually typed
    (the ``strict`` marker excluded) so unmatched-pattern warnings can name them.
    """

    def __init__(
        self,
        patterns: "_ty.Sequence[_ty.Tuple[str, bool]]",
        strict: bool,
    ) -> None:
        self.patterns = list(patterns)
        self.strict = strict

    @classmethod
    def parse(cls, opts: "_ty.Sequence[str]") -> "_Selection":
        """Parse ``--rcopts`` tokens into a :class:`_Selection`.

        Each token is an fnmatch pattern, optionally ``!``-prefixed to disable.
        The bare token ``strict`` (or ``!strict``) toggles strict mode instead of
        naming a step. Whitespace around a token is ignored; empty tokens are
        dropped.
        """
        patterns: "list[_ty.Tuple[str, bool]]" = []
        strict = False
        for raw in opts:
            token = raw.strip()
            if not token:
                continue
            enabled = not token.startswith("!")
            pattern = token[1:] if token.startswith("!") else token
            if pattern == _STRICT_TOKEN:
                strict = enabled
                continue
            patterns.append((pattern, enabled))
        return cls(patterns, strict)

    def decide(self, name: str) -> bool:
        """Return whether the step ``name`` is enabled under this selection.

        With no patterns every step is enabled (default run-everything). Otherwise
        a step is enabled iff the last pattern that matches it is an enable
        pattern; a step matched by no pattern keeps the *default*, which is
        "enabled" unless the first pattern is a disable-**all** wildcard (``!*``)
        -- in that common ``!*,x`` idiom the base becomes disabled and only
        re-enabled names run. A *targeted* disable like ``!two`` disables only its
        own matches and leaves the base enabled.
        """
        # Base default: enabled, unless the very first pattern is a disable-all
        # wildcard (`!*`) -- the "start from nothing" idiom.
        result = True
        if self.patterns:
            first_pattern, first_enabled = self.patterns[0]
            if not first_enabled and first_pattern == "*":
                result = False
        for pattern, enabled in self.patterns:
            if _fnmatch.fnmatchcase(name, pattern):
                result = enabled
        return result

    def unmatched_patterns(self, names: "_ty.Sequence[str]") -> "list[str]":
        """Return the patterns that matched none of ``names`` (for warnings)."""
        unmatched: "list[str]" = []
        for pattern, _enabled in self.patterns:
            if not any(_fnmatch.fnmatchcase(name, pattern) for name in names):
                unmatched.append(pattern)
        return unmatched


# --------------------------------------------------------------------------
# The RunPath command
# --------------------------------------------------------------------------


class RunPathCmd(_Cmd):
    """Run a directory of numbered ``NN-name.py`` steps in order.

    A ``Cmd`` subclass built by the RunPath provider for a step directory. Its
    ``__call__`` loads the steps, applies ``--rcopts`` selection, orders them
    (priority/name, honoring ``REQUIRED``), and runs each enabled step's
    entrypoint through ``self._logger_``.

    Not instantiated directly by users; the provider (see :func:`register`)
    subclasses it per directory, binding the directory path and subcommand name.
    A direct subclass need only set the class attrs ``_runpath_dir_`` (the step
    directory :class:`~pathlib.Path`) and ``_parsername_`` (the subcommand name).
    """

    #: The RunPath directory whose ``NN-name.py`` files are the steps. Set by the
    #: provider-built subclass; ``None`` on the base (which is not runnable).
    _runpath_dir_: "_ty.Optional[_Path]" = None

    rcopts: "_Arg[_ty.List[str], _Extend(',')]"
    "Step selection, comma-separated fnmatch patterns; `!` disables, `strict` errors on miss (e.g. `!*,build`)."
    ("-O", "--rcopts")  # type: ignore

    def _runpath_logger_(self) -> "_logging.Logger":
        """Resolve the run logger: the instance's ``_logger_`` if it has one.

        A ``RunPathCmd`` combined with ``LoggingArgs`` (the usual app shape)
        exposes a ``_logger_`` property scoped to the parser name; a bare
        ``RunPathCmd`` with no logging mixin has none, so fall back to duho's
        ``"duho"`` logger. Mirrors ``ModuleCommand._logger_for``.
        """
        logger = getattr(self, "_logger_", None)
        if isinstance(logger, _logging.Logger):
            return logger
        return _LOGGER

    def __call__(self) -> int:
        directory = getattr(type(self), "_runpath_dir_", None)
        if directory is None:
            raise NotImplementedError(
                "RunPathCmd has no _runpath_dir_; build it via duho.runpath's "
                "provider (register()) or subclass it with _runpath_dir_ set"
            )
        logger = self._runpath_logger_()
        selection = _Selection.parse(getattr(self, "rcopts", None) or [])
        steps = _load_steps(
            _Path(directory), self._parsername_, selection.strict, logger
        )
        names = [s.name for s in steps]

        unmatched = selection.unmatched_patterns(names)
        if unmatched:
            message = "duho.runpath: --rcopts pattern(s) matched no step: %s" % (
                ", ".join(unmatched)
            )
            if selection.strict:
                raise ValueError(message)
            logger.warning(message)

        # Warn (or error, under strict) on REQUIRED naming a missing step.
        present = set(names)
        enabled = {name for name in names if selection.decide(name)}
        for step in steps:
            missing = [dep for dep in step.required if dep not in present]
            if missing:
                message = (
                    "duho.runpath: step %r REQUIRED missing step(s): %s"
                    % (step.name, ", ".join(missing))
                )
                if selection.strict:
                    raise ValueError(message)
                logger.warning(message)

            # A present-but-DISABLED required dep is a different hazard: the dep
            # exists but the current --rcopts selection turned it off, so an
            # enabled step will run without a prerequisite it declared (M4).
            if step.name in enabled:
                disabled_deps = [
                    dep
                    for dep in step.required
                    if dep in present and dep not in enabled
                ]
                if disabled_deps:
                    message = (
                        "duho.runpath: enabled step %r REQUIRED disabled step(s): %s"
                        % (step.name, ", ".join(disabled_deps))
                    )
                    if selection.strict:
                        raise ValueError(message)
                    logger.warning(message)

        for step in steps:
            if not selection.decide(step.name):
                logger.debug("duho.runpath: skipping disabled step %s", step.name)
                continue
            logger.info("duho.runpath: running step %s", step.name)
            try:
                step.entrypoint(self)
            except Exception as exc:
                logger.error(
                    "duho.runpath: step %s failed: %s", step.name, exc
                )
                if selection.strict:
                    raise
        return 0


# --------------------------------------------------------------------------
# Provider registration
# --------------------------------------------------------------------------


def _build_runpath_command(path: "_Path", qualname: str) -> "type[RunPathCmd]":
    """Provider builder: make a per-directory :class:`RunPathCmd` subclass.

    Binds the resolved directory and a subcommand name (the directory's basename,
    ``_``->``-`` normalized, matching module-command naming) onto a fresh subclass
    so ``CmdBuilder``/``discover_commands`` gets a ready-to-register command.
    """
    directory = _Path(path)
    name = directory.name.replace("_", "-")
    namespace = {
        "_runpath_dir_": directory,
        "_parsername_": name,
        "__doc__": "Run the %s step directory." % name,
    }
    return _ty.cast("type[RunPathCmd]", type("RunPathCmd_" + name.replace("-", "_"), (RunPathCmd,), namespace))


#: Records the exact (predicate, builder) pair this module registered, so
#: :func:`unregister` removes *ours* specifically (not merely the newest provider).
_REGISTERED: "_ty.Optional[_ty.Tuple[_ty.Callable, _ty.Callable]]" = None


def register() -> None:
    """Register the RunPath command provider (idempotent).

    After this, ``duho.discover_commands``/``CmdBuilder`` resolve a step
    directory (a dir with ``NN-name.py`` files and no ``__init__.py``) to a
    :class:`RunPathCmd`. Called automatically when ``duho.runpath`` is imported;
    call it explicitly if you prefer no import side effects (import the module
    then... it's already registered -- see :func:`unregister` to opt back out).
    """
    global _REGISTERED
    if _REGISTERED is not None:
        return
    pair = (is_runpath_dir, _build_runpath_command)
    _discovery.register_command_provider(*pair)
    _REGISTERED = pair


def unregister() -> None:
    """Remove the RunPath provider this module registered (idempotent).

    The counterpart to :func:`register` -- essential for test isolation so
    provider state does not leak between tests. Removes the specific
    ``(predicate, builder)`` pair registered by this module; a no-op if not
    currently registered.
    """
    global _REGISTERED
    if _REGISTERED is None:
        return
    try:
        _discovery._PROVIDERS.remove(_REGISTERED)
    except ValueError:
        pass
    _REGISTERED = None


# Auto-register on import: importing ``duho.runpath`` is the opt-in activation.
register()
