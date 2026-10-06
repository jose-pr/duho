from __future__ import annotations

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
from ._providers import _match_provider

# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _handle_error(
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]],
    source: object,
    exc: BaseException,
    message: str,
    *,
    skippable: tuple[type, ...] = (ImportError, NotImplementedError),
) -> None:
    """Skip ``exc`` per the error policy, or re-raise the exception being handled.

    With ``on_error`` the callback decides (it returns to skip, raises to abort).
    Without one, only ``skippable`` exceptions are logged and skipped.
    """
    if on_error is not None:
        on_error(source, exc)
        return
    if not isinstance(exc, skippable):
        raise
    _log_exception(_LOGGER, message, source, exc, level=_logging.WARNING)


def _iter_class_commands(module: object) -> _ty.Iterator[type]:
    """Yield ``Cmd`` subclasses defined in ``module``.

    The ``__module__`` check skips ``Cmd`` itself and bases imported from
    elsewhere. A ``_``-prefixed class is a private base, not a command.
    """
    module_name = getattr(module, "__name__", None)
    for obj in vars(module).values():
        if not is_class_command(obj):
            continue
        # `Args` is not a `Cmd`, but excluding it keeps the intent explicit.
        if obj is _Args:
            continue
        if getattr(obj, "__name__", "").startswith("_"):
            continue
        if getattr(obj, "__module__", None) != module_name:
            continue
        yield obj


def _commands_in_module(module: object, *, stem: str | None = None) -> list[Command]:
    """Collect BOTH command shapes from one already-imported module.

    * every class command defined in the module (``_iter_class_commands``);
    * plus one :class:`ModuleCommand` if the module has a top-level entrypoint
      (``main``/``run``/``call``).

    A module may contribute both (a file with a ``main`` *and* ``Cmd``
    subclasses) or neither (a helpers-only file -- silently nothing).
    """
    commands: list[Command] = list(_iter_class_commands(module))
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


def _namespace_locations(name: str) -> list[_Path]:
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

    A ``Path``/``os.PathLike``, or a ``str`` with ``/`` or ``\\``, always is. A
    separator-free ``str`` is tried as a dotted package first and falls back to
    a same-named directory only when nothing is importable; otherwise a bare
    name could execute an unrelated ``./name/`` in the current directory.
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
    """True if ``source`` is ``""`` or an ``os.PathLike`` whose path is ``""``.

    Either would resolve to the current directory by accident of a blank value.
    A built ``pathlib.Path`` is never empty (``Path("")`` is ``Path(".")``) and
    is always allowed.
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
    """True if ``source`` is a bare Windows drive segment (``"C:"``).

    That spelling means the ambient current directory on the drive, so it would
    glob-import whatever the process runs from; it mirrors the rejection of a
    bare-drive ``CMDS_PATH`` segment in :data:`duho.env._BARE_DRIVE_RE`.
    """
    if isinstance(source, str):
        return bool(_BARE_DRIVE_RE.match(source))
    if isinstance(source, (_Path, _os.PathLike)):
        try:
            return bool(_BARE_DRIVE_RE.match(_os.fspath(source)))
        except TypeError:  # pragma: no cover - a broken __fspath__
            return False
    return False


def discover_commands(
    source: _ty.Union[
        str, _os.PathLike, _Path, _ty.Sequence[_ty.Union[str, _os.PathLike, _Path]]
    ],
    *,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
    providers: bool = False,
) -> list[Command]:
    """Discover commands from a package name or a directory, resiliently.

    ``source`` may also be a list or tuple of sources: each is discovered on its
    own, a command of the same name in a later source replaces the earlier one,
    and the merged result is sorted by name as below. An empty sequence gives
    ``[]``. Each member goes through the same checks as a single source.

    ``on_error(source, exc)``, when given, replaces the resilience rule below:
    it is called for any exception (``SystemExit`` included) raised while
    importing one file or one package module, building its commands, or building
    a provider command, with ``source`` the file ``Path``, the dotted module
    name, or the provider directory. Returning skips that one; raising aborts
    discovery. ``None`` keeps the rule below.

    ``providers=True`` (filesystem sources only) first offers the source
    directory itself, then each of its child directories not starting with ``_``
    or ``.``, to the registered command providers
    (:func:`register_command_provider`); a matching one builds a command named
    from the directory (``_`` as ``-``). A source directory a provider claims
    yields that one command and its files are not scanned. Off by default.

    A single ``source`` is dispatched by shape:

    * a ``Path``/``os.PathLike``, or a ``str`` containing ``/`` or ``\\`` ->
      **filesystem**: iterate ``sorted(dir.glob("*.py"))``, skip
      ``_``-prefixed files, import each under a synthesized unique
      ``sys.modules`` name, and collect its commands;
    * any other ``str`` -> FIRST taken as a **dotted package**:
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
    if isinstance(source, (list, tuple)):
        merged: dict[str, Command] = {}
        for member in source:
            for command in discover_commands(
                member, on_error=on_error, providers=providers
            ):
                merged[_command_name(command)] = command
        return sorted(merged.values(), key=_command_name)
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
        commands = [
            c
            for d in namespace_dirs
            for c in _discover_from_path(d, on_error=on_error, providers=providers)
        ]
    elif _looks_like_path(source):
        commands = _discover_from_path(
            _Path(source), on_error=on_error, providers=providers
        )
    else:
        commands = _discover_from_package(str(source), on_error=on_error)
    return sorted(commands, key=_command_name)


def _discover_from_package(
    dotted_name: str,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
) -> list[Command]:
    import pkgutil as _pkgutil

    package = _importlib.import_module(dotted_name)
    search_path = getattr(package, "__path__", None)
    if not search_path:
        raise ImportError(
            "%r is a module, not a package: discover_commands needs a package "
            "(with __path__) or a directory path" % dotted_name,
            name=dotted_name,
        )

    commands: list[Command] = []
    prefix = dotted_name + "."
    for module_info in _pkgutil.iter_modules(search_path, prefix=prefix):
        sub_name = module_info.name
        stem = sub_name.rsplit(".", 1)[-1]
        if stem.startswith("_"):
            continue
        try:
            submodule = _importlib.import_module(sub_name)
            commands.extend(_commands_in_module(submodule, stem=stem))
        except (Exception, SystemExit) as exc:
            _handle_error(
                on_error,
                sub_name,
                exc,
                "skipping command module %r during discovery: %s",
            )
    return commands


def _discover_from_path(
    directory: _Path,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
    providers: bool = False,
) -> list[Command]:
    """Import and collect commands from every top-level ``.py`` file in ``directory``.

    Only a lower-case ``.py`` suffix counts: Windows' case-insensitive ``glob``
    also matches ``X.PY``, which Python's import machinery refuses.

    While a file is imported, ``directory`` is appended to ``sys.path`` (after
    the standard library and installed packages, which a command file must not
    shadow) so ``from _helpers import x`` resolves; a relative import fails, as
    these files have no parent package. Afterwards the entry is removed and
    each module pulled in from inside ``directory`` is popped from
    ``sys.modules``, so a same-named helper in another discovered directory is
    not served a stale one. Modules from outside ``directory``, and those with
    no ``__file__``, are left alone: evicting a shared class or a stdlib module
    would break ``isinstance`` identity between command files.
    """
    directory = _Path(directory)
    if not directory.is_dir():
        raise ImportError("not a directory: %s" % directory, path=_os.fspath(directory))

    if providers:
        claimed = _provider_command(directory, on_error)
        if claimed is not None:
            return claimed

    resolved_dir = directory.resolve()
    commands: list[Command] = []
    if providers:
        for child in sorted(directory.iterdir()):
            if child.name.startswith(("_", ".")) or not child.is_dir():
                continue
            commands.extend(_provider_command(child, on_error) or ())
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
        except (Exception, SystemExit) as exc:
            _handle_error(
                on_error, path, exc, "skipping command file %s during discovery: %s"
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
        try:
            commands.extend(_commands_in_module(module, stem=stem))
        except (Exception, SystemExit) as exc:
            _handle_error(
                on_error,
                path,
                exc,
                "skipping command file %s during discovery: %s",
                skippable=(),
            )
    return commands


def _provider_command(
    directory: _Path,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]],
) -> _ty.Optional[list[Command]]:
    """The one-command list a registered provider builds for ``directory``, or ``None``.

    ``None`` means no provider claims it. A build that fails is handled like a
    failing command file, and gives ``[]`` when skipped.
    """
    builder = _match_provider(directory.absolute())
    if builder is None:
        return None
    try:
        return [
            _ty.cast(
                Command,
                builder(directory.absolute(), directory.name.replace("_", "-")),
            )
        ]
    except (Exception, SystemExit) as exc:
        _handle_error(
            on_error,
            directory,
            exc,
            "skipping command directory %s during discovery: %s",
        )
        return []


def _running_script() -> _Path | None:
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


def _module_inside(module: object, directory: _Path) -> bool:
    """True if ``module``'s own file resolves inside ``directory``.

    Tells a sibling helper (evicted from ``sys.modules`` so another directory's
    same-named helper is not served a stale one) from anything else the command
    file merely imported, which stays.
    """
    modfile = getattr(module, "__file__", None)
    if not modfile:
        return False
    try:
        resolved = _Path(modfile).resolve()
    except OSError:  # pragma: no cover - a vanished/unreadable path
        return False
    return resolved == directory or directory in resolved.parents
