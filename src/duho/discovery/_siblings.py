from __future__ import annotations

import builtins as _builtins
import importlib.machinery as _machinery
import os as _os
import sys as _sys
import threading as _threading
import types as _types
import typing as _ty
import weakref as _weakref
from pathlib import Path as _Path

# --------------------------------------------------------------------------
# Sibling helpers of a command directory
#
# Each scanned directory D is one private package, ``_ROOT.<tag>``, whose
# ``__path__`` is ``[D]``. A command file's bare ``import helper`` is routed to
# ``_ROOT.<tag>.helper`` by an ``__import__`` bound to D and installed in the
# module's own ``__builtins__``, so the helper is one module object for every
# file of D, stays in ``sys.modules`` and never appears under its bare name.
# --------------------------------------------------------------------------

_ROOT = "duho._discovered._dir_"
_MARK = "_duho_dir_state_"
_LOCK = _threading.RLock()

#: Every live directory state, for :func:`refresh_builtins`.
_STATES: _weakref.WeakSet[_DirState] = _weakref.WeakSet()


class _DirState:
    """Per-directory state, kept on the directory's package module."""

    def __init__(self, directory: _Path, prefix: str) -> None:
        self.directory = directory
        self.prefix = prefix
        self.verdicts: dict[str, bool] = {}
        self.importer = self._import
        # ONE mapping for every module of the directory, updated in place:
        # a frame keeps the object it was created with.
        self.builtins: dict[str, object] = {}
        self.refresh()

    def is_sibling(self, first: str) -> bool:
        """Whether the top-level name ``first`` is a file or package of the directory.

        The standard library, installed packages and anything already imported
        win, so only a name found nowhere else can be a sibling.
        """
        verdict = self.verdicts.get(first)
        if verdict is not None:
            return verdict
        verdict = _decide(self, first)
        self.verdicts[first] = verdict
        return verdict

    def _import(
        self,
        name: str,
        globals: _ty.Optional[_ty.Mapping[str, object]] = None,
        locals: _ty.Optional[_ty.Mapping[str, object]] = None,
        fromlist: _ty.Sequence[str] = (),
        level: int = 0,
    ) -> object:
        """``__import__`` that resolves a sibling's bare name under the package."""
        if level == 0 and name:
            first = name.partition(".")[0]
            if self.is_sibling(first):
                module = _builtins.__import__(
                    self.prefix + "." + name, globals, locals, fromlist, 0
                )
                if fromlist:
                    return module
                return _sys.modules[self.prefix + "." + first]
        return _builtins.__import__(name, globals, locals, fromlist, level)

    def namespace(self) -> dict[str, object]:
        """The directory's builtins: the real ones, with ``__import__`` bound to it."""
        return self.builtins

    def refresh(self) -> None:
        """Bring the directory's builtins in line with the real ones.

        A copy goes stale when ``builtins`` is patched afterwards (a test
        replacing ``builtins.input``), so it is renewed before each dispatch.
        """
        live = dict(vars(_builtins))
        live["__import__"] = self.importer
        ns = self.builtins
        # One `update`, so no reader sees the real `__import__` in between.
        ns.update(live)
        for stale in [key for key in ns if key not in live]:
            del ns[stale]


def _decide(state: _DirState, first: str) -> bool:
    if first in _sys.modules:
        return False
    try:
        if _importutil().find_spec(first) is not None:
            return False
    except (ImportError, ValueError, AttributeError):
        pass
    spec = _machinery.PathFinder.find_spec(
        state.prefix + "." + first, [_os.fspath(state.directory)]
    )
    return spec is not None


class _Loader:
    """Runs a loader's ``exec_module`` after giving the module its own builtins."""

    def __init__(self, inner: object, state: _DirState) -> None:
        self._inner = inner
        self._state = state

    def create_module(self, spec: object) -> object:
        return self._inner.create_module(spec)  # type: ignore[attr-defined]

    def exec_module(self, module: _types.ModuleType) -> None:
        module.__dict__["__builtins__"] = self._state.namespace()
        self._inner.exec_module(module)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _Finder:
    """Meta-path finder for names under a directory package, and only those."""

    @classmethod
    def find_spec(
        cls,
        fullname: str,
        path: _ty.Optional[_ty.Sequence[str]] = None,
        target: object = None,
    ) -> _ty.Optional[_machinery.ModuleSpec]:
        if not fullname.startswith(_ROOT) or path is None:
            return None
        root = ".".join(fullname.split(".", 3)[:3])
        package = _sys.modules.get(root)
        state = getattr(package, _MARK, None)
        if state is None or fullname == root:
            return None
        spec = _machinery.PathFinder.find_spec(fullname, list(path))
        loader = None if spec is None else spec.loader
        if loader is not None and hasattr(loader, "get_code"):
            spec.loader = _Loader(loader, state)  # type: ignore[assignment]
        return spec


def _install_finder() -> None:
    if _Finder not in _sys.meta_path:
        _sys.meta_path.insert(0, _Finder)  # type: ignore[arg-type]


def _tag(directory: _Path) -> str:
    import hashlib  # here, so `import duho` does not load it

    key = _os.path.normcase(_os.fspath(directory))
    return hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:12]


def refresh_builtins() -> None:
    """Renew the builtins of every scanned directory (see :meth:`_DirState.refresh`)."""
    if _STATES:
        for state in list(_STATES):
            state.refresh()


def dir_state(directory: _Path) -> _DirState:
    """The state of ``directory``'s private package, creating the package once."""
    resolved = _Path(directory).resolve()
    name = _ROOT + _tag(resolved)
    with _LOCK:
        _install_finder()
        package = _sys.modules.get(name)
        state = getattr(package, _MARK, None)
        if state is not None:
            state.refresh()
            return state
        spec = _machinery.ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = [_os.fspath(resolved)]
        package = _importutil().module_from_spec(spec)
        state = _DirState(resolved, name)
        _STATES.add(state)
        setattr(package, _MARK, state)
        _sys.modules[name] = package
        return state


def _builtins_setter(
    state: _DirState,
) -> _ty.Callable[[_types.ModuleType], None]:
    """A ``prepare`` hook giving a command file the directory's ``__import__``."""

    def prepare(module: _types.ModuleType) -> None:
        module.__dict__["__builtins__"] = state.namespace()

    return prepare


def _importutil() -> _types.ModuleType:
    """``importlib.util``, loaded on first use so ``import duho`` stays light."""
    import importlib.util

    return importlib.util
