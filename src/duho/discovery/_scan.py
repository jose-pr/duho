import importlib as _importlib
import logging as _logging
import os as _os
import sys as _sys
import typing as _ty
from pathlib import Path as _Path

from ..args import Args as _Args
from ..args._naming import _command_name as _command_name
from ..env import _BARE_DRIVE_RE as _BARE_DRIVE_RE
from ..logging import log_exception as _log_exception

from ._command import (
    Command,
    ModuleCommand,
    _LOGGER,
    _module_entrypoint,
    _resolved_module_name,
    is_class_command,
)
from ._importing import import_from_path

# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _iter_class_commands(module: object) -> "_ty.Iterator[type]":
    """Yield ``Cmd`` subclasses *defined in* ``module`` (module-boundary dedup).

    The ``obj.__module__ == module.__name__`` filter is what stops a naive
    ``vars(module)`` walk from re-registering ``Cmd`` itself (imported for
    subclassing) or a shared base command re-exported/imported into several
    command files -- only classes whose home module is this one count, so a
    class imported unchanged from elsewhere is not double-collected.

    A class whose ``__name__`` starts with ``_`` is also skipped -- the same
    "private, not a command" convention the file-level ``_`` prefix already
    gives discovery, so a shared base meant only for other command files to
    subclass (``class _RemoteBase(Cmd): ...``) is never itself listed/runnable.
    """
    module_name = getattr(module, "__name__", None)
    for obj in vars(module).values():
        if not is_class_command(obj):
            continue
        # Exclude the data ``Args`` base defensively (is_class_command already
        # requires a strict Cmd subclass, so Args -- not a Cmd -- is excluded,
        # but keep the intent explicit for readers).
        if obj is _Args:
            continue
        if getattr(obj, "__name__", "").startswith("_"):
            continue
        if getattr(obj, "__module__", None) != module_name:
            continue
        yield obj


def _commands_in_module(
    module: object, *, stem: "str | None" = None
) -> "list[Command]":
    """Collect BOTH command shapes from one already-imported module.

    * every class command defined in the module (``_iter_class_commands``);
    * plus one :class:`ModuleCommand` if the module has a top-level entrypoint
      (``main``/``run``/``call``).

    A module may contribute both (a file with a ``main`` *and* ``Cmd``
    subclasses) or neither (a helpers-only file -- silently nothing).
    """
    commands: "list[Command]" = list(_iter_class_commands(module))
    if _module_entrypoint(module) is not None:
        name = _resolved_module_name(module, stem=stem)
        commands.append(_ty.cast(Command, ModuleCommand(module, name=name)))
    return commands


def _importable_spec(name: str) -> object:
    """Return ``importlib.util.find_spec(name)`` if resolvable, else ``None``.

    Never raises: a name that ``find_spec`` cannot resolve (missing module, a
    bad/invalid name, a partially-initialised package) is treated the same as
    "not importable".
    """
    import importlib.util as _importutil

    try:
        return _importutil.find_spec(name)
    except (ImportError, ValueError):
        return None


def _namespace_locations(name: str) -> "list[_Path]":
    """Directories of ``name`` when it resolves only to a namespace package.

    A namespace package (no ``__init__.py``, no origin) is a directory of loose
    files, not an importable package, so its command files are imported by path.
    Empty when ``name`` is a regular package, a module, or unresolvable.
    """
    spec = _importable_spec(name)
    locations = getattr(spec, "submodule_search_locations", None)
    if spec is None or getattr(spec, "origin", None) is not None or not locations:
        return []
    return [_Path(entry) for entry in locations]


def _looks_like_path(source: object) -> bool:
    """True if ``source`` should be treated as a filesystem path, not a dotted name.

    A ``Path``/``os.PathLike`` always is. A ``str`` containing a separator
    (``/`` or ``\\``) always is. A separator-free ``str`` is tried as a
    **dotted package name FIRST** -- if it resolves via ``sys.path``, that
    import wins -- and only falls back to a same-named CWD-relative directory
    when nothing is importable. This ordering matters: without it, a bare
    ``discover_commands("mycmds")`` (or ``app(source="mycmds")``) run from a
    directory that happens to contain an unrelated ``./mycmds/`` would import
    THAT directory's ``.py`` files -- silently executing code from wherever the
    user is standing and shadowing the intended package (a security-relevant concern).
    """
    if isinstance(source, _Path) or (
        isinstance(source, _os.PathLike) and not isinstance(source, str)
    ):
        return True
    if isinstance(source, str):
        if "/" in source or "\\" in source:
            return True
        if _importable_spec(source) is not None:
            return False
        if _Path(source).is_dir():
            return True
    return False


def _is_empty_source(source: object) -> bool:
    """True if ``source`` is a bare empty string, or an ``os.PathLike`` whose
    ``__fspath__()`` returns ``""``.

    Either would otherwise resolve to the current working directory as a
    SIDE EFFECT of a blank/uninitialised value (e.g. ``discover_commands(cfg
    .get("cmds_dir", ""))`` with the key unset), rather than the deliberate,
    explicit ``"."`` a caller writes to actually mean "scan my own current
    directory". A real ``pathlib.Path`` can never reach here empty:
    ``pathlib.Path("")`` already normalises to ``Path(".")`` at CONSTRUCTION
    time, before this function ever sees it -- so an already-built ``Path``,
    ``Path("")`` and ``Path(".")`` alike, is always the explicit, allowed
    spelling, never rejected here.
    """
    if isinstance(source, str):
        return source == ""
    if isinstance(source, _os.PathLike) and not isinstance(source, _Path):
        try:
            return _os.fspath(source) == ""
        except TypeError:  # pragma: no cover - a broken __fspath__
            return False
    return False


def _is_bare_drive_source(source: object) -> bool:
    """True if ``source`` is a bare Windows drive segment (``"C:"``, matching
    ``^[A-Za-z]:$`` -- no trailing separator/backslash).

    Windows resolves this to "the current directory on drive C", an implicit,
    ambient lookup -- ``Path("C:").is_dir()`` is true, and iterating it globs
    whatever the process happens to be running FROM, not a directory the
    caller actually named. ``discover_commands("C:")`` /
    ``app(source="C:")`` would otherwise silently glob-import that ambient
    CWD's ``.py`` files (a security-relevant fix; mirrors
    :data:`duho.env._BARE_DRIVE_RE`'s identical rejection of a bare-drive
    ``CMDS_PATH`` *segment* -- this guards a whole ``source=`` argument
    instead of one segment after splitting).
    """
    if isinstance(source, str):
        return bool(_BARE_DRIVE_RE.match(source))
    if isinstance(source, (_Path, _os.PathLike)):
        try:
            return bool(_BARE_DRIVE_RE.match(_os.fspath(source)))
        except TypeError:  # pragma: no cover - a broken __fspath__
            return False
    return False


def discover_commands(source: "str | _os.PathLike | _Path") -> "list[Command]":
    """Discover commands from a package name or a directory, resiliently.

    ``source`` is dispatched by shape:

    * a ``Path``/``os.PathLike``, or a ``str`` containing ``/`` or ``\\`` ->
      **filesystem**: iterate ``sorted(dir.glob("*.py"))``, skip
      ``_``-prefixed files, import each under a synthesized unique
      ``sys.modules`` name, and collect its commands;
    * any other ``str`` -> tried FIRST as a **dotted package**:
      ``import_module`` it, require a ``__path__`` (it must be a package, not a
      plain module), walk its submodules with ``pkgutil.iter_modules``, import
      each, and collect. Only when the name does not resolve to an importable
      module/package does it fall back to a same-named directory relative to
      the current working directory (filesystem mode, as above). This
      ordering is deliberate: a bare name is usually an intended package, and
      trying it first prevents an unrelated same-named directory in the CWD
      from silently shadowing it and having its ``.py`` files imported/executed
      instead (see :func:`_looks_like_path`).

    From each module it collects BOTH class commands (``Cmd`` subclasses defined
    in that module) and, if the module has a top-level ``main``/``run``/``call``,
    one :class:`ModuleCommand`. A module with neither contributes nothing.

    **Resilience.** Per-command import/build is wrapped to catch **only**
    ``ImportError`` and ``NotImplementedError`` (and their subclasses): these
    mean "unsupported/optional-dep-missing" or "not actually a command", which
    are skippable -- logged and skipped so the *other* commands still load. Any
    other exception (notably ``SyntaxError`` -- a typo in a command file -- and
    unexpected runtime errors) propagates: a genuinely broken command file is a
    real bug the author wants surfaced, not silently swallowed.

    **An empty source is rejected outright** (``ValueError``): a bare ``""``
    or an ``os.PathLike`` whose ``__fspath__()`` is ``""`` would otherwise
    silently discover the current working directory as a side effect of a
    blank/uninitialised value (a security-relevant fix -- this is also the
    path :func:`duho.runtime.app`'s ``source=`` argument goes through, so
    ``app(source="")`` is covered the same way). Pass ``"."`` explicitly (a
    string, or any ``Path``/``os.PathLike`` -- ``Path("")`` included, since it
    already normalises to ``Path(".")`` before reaching here) to deliberately
    scan the current directory.

    **A bare drive letter is also rejected outright** (``ValueError``): a
    ``"C:"``-shaped source (see :func:`_is_bare_drive_source`) is Windows'
    own spelling for "the current directory on that drive" -- another,
    platform-specific way to smuggle the CWD in past the empty-source check
    above (a security-relevant fix, likewise covering ``app(source="C:")``).
    Spell an actual path (``"C:\\cmds"``, or ``"."`` for the CWD) instead.

    The result is sorted by resolved subcommand name for deterministic
    ``--help`` output (filesystem iteration order is OS-dependent).
    """
    if _is_empty_source(source):
        raise ValueError(
            "discover_commands(): an empty source is never valid -- it would "
            "silently discover the current working directory as a side "
            "effect of a blank/uninitialised value; pass '.' explicitly if "
            "that is intended"
        )
    if _is_bare_drive_source(source):
        raise ValueError(
            "discover_commands(): %r is a bare drive segment -- Windows "
            "resolves it to the current directory on that drive; use an "
            "actual path, or '.' for the current directory" % (source,)
        )
    namespace_dirs = (
        _namespace_locations(source)
        if isinstance(source, str) and not _looks_like_path(source)
        else []
    )
    if namespace_dirs:
        commands = [c for d in namespace_dirs for c in _discover_from_path(d)]
    elif _looks_like_path(source):
        commands = _discover_from_path(_Path(source))
    else:
        commands = _discover_from_package(str(source))
    return sorted(commands, key=_command_name)


def _discover_from_package(dotted_name: str) -> "list[Command]":
    import pkgutil as _pkgutil

    package = _importlib.import_module(dotted_name)
    search_path = getattr(package, "__path__", None)
    if not search_path:
        raise ImportError(
            "%r is a module, not a package: discover_commands needs a package "
            "(with __path__) or a directory path" % dotted_name,
            name=dotted_name,
        )

    commands: "list[Command]" = []
    prefix = dotted_name + "."
    for module_info in _pkgutil.iter_modules(search_path, prefix=prefix):
        sub_name = module_info.name
        stem = sub_name.rsplit(".", 1)[-1]
        if stem.startswith("_"):
            continue
        try:
            submodule = _importlib.import_module(sub_name)
            commands.extend(_commands_in_module(submodule, stem=stem))
        except (ImportError, NotImplementedError) as exc:
            _log_exception(
                _LOGGER,
                "skipping command module %r during discovery: %s",
                sub_name,
                exc,
                level=_logging.WARNING,
            )
            continue
    return commands


def _discover_from_path(directory: "_Path") -> "list[Command]":
    """Import and collect commands from every top-level ``.py`` file in ``directory``.

    Only a lower-case ``.py`` suffix counts: Windows' case-insensitive ``glob``
    would also match ``X.PY``, which Python's import machinery then refuses.

    **Sibling imports.** While importing each file, ``directory`` is
    temporarily appended to ``sys.path`` (after the standard library and
    installed packages, which a same-named command file must not shadow) so a
    bare ``from _helpers import x`` resolves -- the documented convention for factoring shared code into a
    ``_``-prefixed helper file that command files in the same directory can
    import (the ``_`` prefix means "not a command", not "unimportable"). The
    directory is removed from ``sys.path`` again immediately after, and any
    module this pulled in that lives INSIDE ``directory`` (the helper module
    itself, imported under its own bare name) -- beyond the command file's own
    synthetic key -- is popped back out of ``sys.modules``, so a *different*
    discovered directory that also ships a same-named helper is never served a
    stale cached one. This only supports the bare/absolute form (``from
    _helpers import x``); a relative ``from ._helpers import x`` still fails,
    since these files have no real parent package.

    **Scoped to this directory.** Only a module whose ``__file__`` resolves
    *inside* ``directory`` is ever popped -- a command file routinely imports
    shared helper classes, or ordinary stdlib/third-party modules, as a normal
    side effect of executing its body; blindly popping every name added to
    ``sys.modules`` during the import (as an earlier version of this function
    did) evicted THOSE too, so a second discovered file sharing one of those
    classes lost its ``isinstance``/``is`` identity against the first (and a
    popped stdlib module simply reimported cleanly, but with a rebuilt C
    extension state, on the next access -- unnecessary churn ``duho.env``
    warned about on every run). A module with no resolvable ``__file__``
    (a namespace package, a C extension) is left alone -- there is no
    "inside/outside" ``directory`` to test, and leaving it in place is the
    safe default.
    """
    directory = _Path(directory)
    if not directory.is_dir():
        raise ImportError("not a directory: %s" % directory, path=_os.fspath(directory))

    resolved_dir = directory.resolve()
    commands: "list[Command]" = []
    dirstr = _os.fspath(directory)
    running = _running_script()
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_") or path.suffix != ".py":
            continue
        if running is not None and path.resolve() == running:
            continue
        stem = path.stem
        before_modules = set(_sys.modules)
        _sys.path.append(dirstr)
        module = None
        try:
            module = import_from_path("duho._discovered." + stem, path)
        except (ImportError, NotImplementedError) as exc:
            _log_exception(
                _LOGGER,
                "skipping command file %s during discovery: %s",
                path,
                exc,
                level=_logging.WARNING,
            )
            continue
        finally:
            # Drop the appended entry (the last one), not an earlier duplicate.
            for index in range(len(_sys.path) - 1, -1, -1):
                if _sys.path[index] == dirstr:
                    del _sys.path[index]
                    break
            own_key = module.__name__ if module is not None else None
            for extra in set(_sys.modules) - before_modules:
                if extra == own_key:
                    continue
                if _module_inside(_sys.modules.get(extra), resolved_dir):
                    _sys.modules.pop(extra, None)
        commands.extend(_commands_in_module(module, stem=stem))
    return commands


def _running_script() -> "_Path | None":
    """Resolved path of the script being run (``__main__``), or ``None``.

    A launcher that scans its own directory must not import itself as a command.
    """
    main_file = getattr(_sys.modules.get("__main__"), "__file__", None)
    if not main_file:
        return None
    try:
        return _Path(main_file).resolve()
    except OSError:  # pragma: no cover - a vanished/unreadable path
        return None


def _module_inside(module: object, directory: "_Path") -> bool:
    """True if ``module``'s own file resolves to a path inside ``directory``.

    Used by :func:`_discover_from_path` to decide whether a module pulled into
    ``sys.modules`` while importing a command file is a SIBLING helper (evict
    it, so a same-named helper in a different discovered directory is never
    served this stale one) or anything else the command file merely imported
    as a normal side effect (a shared helper class, a stdlib/third-party
    module) -- which must be left in ``sys.modules`` untouched.
    """
    modfile = getattr(module, "__file__", None)
    if not modfile:
        return False
    try:
        resolved = _Path(modfile).resolve()
    except OSError:  # pragma: no cover - a vanished/unreadable path
        return False
    return resolved == directory or directory in resolved.parents
