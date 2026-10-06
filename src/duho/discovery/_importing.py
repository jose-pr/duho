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


#: Cache mapping a resolved absolute file path to ``(sys.modules key, mtime)``
#: for the last file imported from that path via :func:`_import_from_path`.
#: Repeated discovery of the SAME file (an unchanged in-process re-run of
#: ``discover_commands``/``CmdBuilder`` over the same directory, e.g. from an
#: MCP server or a fan-out dispatcher) reuses the cached module instead of
#: re-executing the file under an ever-longer synthesized name each time --
#: unbounded ``sys.modules`` growth and duplicate module-body side effects
#: were the failure mode this closes. Keyed on ``(path, mtime)`` so editing the
#: file (mtime changes) still gets a fresh import.
_IMPORTED_BY_PATH: "dict[str, tuple[str, object]]" = {}


#: Serialises the cache check, module execution and cache write so concurrent
#: first imports of one file run its body once. Reentrant: a module body may
#: itself import another file through :func:`import_from_path`.
_IMPORT_LOCK = _threading.RLock()


def _import_from_path(name: str, path: "_Path") -> "_ModuleType":
    """Import a ``.py`` file at ``path`` under module key ``name`` and return it.

    Uses ``spec_from_file_location`` + ``exec_module`` (stdlib only). The module
    is registered in ``sys.modules`` under ``name`` before execution so a module
    that inspects its own ``__name__`` / does relative-ish self-reference works.
    A missing/unloadable spec raises ``ImportError`` (skippable by discovery);
    an exception raised *by the module body* (e.g. ``SyntaxError``,
    ``NameError``) propagates unchanged.

    Re-importing the SAME file (matched by resolved path + mtime, see
    :data:`_IMPORTED_BY_PATH`) returns the previously-imported module instead
    of executing it again under a new key -- ``name`` is then unused for that
    call.
    """
    with _IMPORT_LOCK:
        return _import_from_path_locked(name, path)


def _import_from_path_locked(name: str, path: "_Path") -> "_ModuleType":
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
        raise ImportError(name=name, path=_os.fspath(path))
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


def import_from_path(base_name: str, path: "_Path") -> "_ModuleType":
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
