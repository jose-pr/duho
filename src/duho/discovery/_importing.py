from __future__ import annotations

import os as _os
import sys as _sys
import threading as _threading
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

# --------------------------------------------------------------------------
# Import helpers
# --------------------------------------------------------------------------


def _unique_module_name(base: str) -> str:
    """Return a ``sys.modules`` key based on ``base`` that is not already taken.

    Avoids clobbering a real installed module of the same name when importing a
    loose ``.py`` file: append ``_`` until the name is free.
    """
    name = base
    while name in _sys.modules:
        name += "_"
    return name


#: Resolved path -> ``(sys.modules key, mtime)`` of the last import, so an
#: unchanged file is reused rather than re-executed under a new name each time
#: (unbounded ``sys.modules`` growth, repeated side effects).
_IMPORTED_BY_PATH: dict[str, tuple[str, object]] = {}


#: Serialises the cache check, module execution and cache write so concurrent
#: first imports of one file run its body once. Reentrant: a module body may
#: itself import another file through :func:`import_from_path`.
_IMPORT_LOCK = _threading.RLock()


def _import_from_path(name: str, path: _Path) -> _ModuleType:
    """Import the ``.py`` file at ``path`` under ``sys.modules`` key ``name``.

    The module is registered before it executes, so self-references work. A
    missing spec raises ``ImportError`` (skippable by discovery); an exception
    from the module body propagates. An unchanged file (same resolved path and
    mtime, see :data:`_IMPORTED_BY_PATH`) returns the cached module and ``name``
    is unused.
    """
    with _IMPORT_LOCK:
        return _import_from_path_locked(name, path)


def _import_from_path_locked(name: str, path: _Path) -> _ModuleType:
    import importlib.util as _importutil

    resolved = _os.fspath(_Path(path).resolve())
    try:
        mtime: object = _Path(path).stat().st_mtime
    except OSError:  # pragma: no cover - path vanished between calls
        mtime = None
    cached = _IMPORTED_BY_PATH.get(resolved)
    if cached is not None:
        cached_name, cached_mtime = cached
        if cached_mtime == mtime:
            cached_module = _sys.modules.get(cached_name)
            if cached_module is not None:
                return cached_module
        # Stale (file changed or module evicted from sys.modules elsewhere).
        _IMPORTED_BY_PATH.pop(resolved, None)

    spec = _importutil.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(
            "cannot import %s: no loader for this file name (a command file "
            "needs a lower-case .py suffix)" % _os.fspath(path),
            name=name,
            path=_os.fspath(path),
        )
    module = _importutil.module_from_spec(spec)
    _sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # Do not leave a half-initialised module registered under our synthetic
        # name if execution blew up.
        _sys.modules.pop(name, None)
        raise
    _IMPORTED_BY_PATH[resolved] = (name, mtime)
    return module


def import_from_path(base_name: str, path: _Path) -> _ModuleType:
    """Import a ``.py`` file at ``path`` under a ``sys.modules`` key derived
    from ``base_name``, guaranteed not to clobber an existing module of that
    name (:func:`_unique_module_name`) and reused across repeat imports of
    the SAME file (see :func:`_import_from_path`).

    Public counterpart of the ``_unique_module_name`` + ``_import_from_path``
    pair this module already used internally for its own filesystem-based
    discovery (:class:`CmdBuilder`, :func:`discover_commands`) -- exposed
    so an external command-provider package (``duho.runpath`` is the
    first, and so far only, consumer) can import a ``.py`` file the exact
    same way without reaching into either private helper directly.
    """
    with _IMPORT_LOCK:
        return _import_from_path(_unique_module_name(base_name), path)
