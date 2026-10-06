from __future__ import annotations

import importlib as _importlib
import os as _os
import typing as _ty
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

from ..qualname import PythonName as _PythonName

from ._command import (
    Command,
    ModuleCommand,
    _resolved_module_name,
    is_class_command,
    is_module_command,
)
from ._importing import import_from_path
from ._providers import _match_provider

# --------------------------------------------------------------------------
# CmdBuilder
# --------------------------------------------------------------------------


class CmdBuilder:
    """Build a :class:`Command` from an import path or a filesystem path.

    ``CmdBuilder(qualname, source=None)`` resolves ``source`` to a command:

    * ``source`` a ``Path`` (or path-like) -- a filesystem source. If a
      registered provider (:func:`register_command_provider`) matches it, that
      provider builds the command; otherwise it is imported. A ``.py`` file is
      imported via ``spec_from_file_location`` under a synthesized unique
      ``sys.modules`` key in a private ``duho._cmdbuilder.`` namespace (so a
      loose file NEVER clobbers a real installed module of the same dotted
      name, even one not imported yet -- e.g. ``CmdBuilder("json", ...)`` never
      becomes ``sys.modules["json"]``). A directory *with* ``__init__.py`` is a
      package and imported by qualname, but ONLY after verifying that
      ``qualname`` actually resolves (via ``sys.path``) to this same directory
      -- otherwise a same-named package elsewhere on ``sys.path`` would be
      silently wrapped instead, or a real ImportError with no message raised.
      A directory *without* ``__init__.py`` is offered to providers first (the
      seam where a run-path-style runtime plugs in) and, if unclaimed, raises
      ``ImportError`` (core duho has no meaning for a bare dir of files --
      that meaning is exactly what a provider supplies).
    * ``source`` omitted/None -- ``qualname`` is treated as a dotted import path
      and imported via ``importlib.import_module`` (after checking providers for
      a namespace-package directory, mirroring the path branch).
    * ``source`` already a module or ``Command`` -- used directly.

    The resolved command is exposed as :attr:`command`. For a module source it
    is a :class:`ModuleCommand`; for a provider it is whatever the provider
    returns; for an already-``Command`` source it is that object.
    """

    #: Declared so a type checker sees the documented
    #: ``duho.app(commands=[CmdBuilder(...).command])`` recipe as a properly
    #: typed ``Command``, not the ``object`` a provider's own loose
    #: ``Callable[[Path, str], object]`` signature would otherwise infer.
    command: Command

    def __init__(
        self,
        qualname: str | _PythonName,
        source: _Path | str | _os.PathLike | _ModuleType | Command | None = None,
    ) -> None:
        self.qualname = str(qualname)

        if isinstance(source, (_Path, _os.PathLike)) and not isinstance(source, str):
            self.command = self._from_path(_Path(source))
        elif source is None:
            self.command = self._from_import(self.qualname)
        elif isinstance(source, _ModuleType):
            self.command = self._wrap_module(source)
        elif is_class_command(source) or is_module_command(source):
            self.command = _ty.cast(Command, source)
        else:
            # A path given as a plain string.
            self.command = self._from_path(_Path(_ty.cast(str, source)))

    # -- resolution branches ------------------------------------------------

    def _from_path(self, path: _Path) -> Command:
        path = path.absolute()
        builder = _match_provider(path)
        if builder is not None:
            return _ty.cast("Command", builder(path, self.qualname))

        if path.is_dir():
            if (path / "__init__.py").exists():
                # A real package: import by qualname (so relative imports
                # work) but ONLY once it's verified that the qualname
                # actually resolves to THIS directory -- see
                # `_import_package_at`.
                return self._import_package_at(path)
            raise ImportError(
                "no command provider handles the directory %s (a bare directory "
                "without __init__.py has no built-in command meaning; register a "
                "provider to give it one)" % path,
                name=self.qualname,
                path=_os.fspath(path),
            )

        # Namespaced under a private `duho._cmdbuilder.` prefix (mirroring
        # `_discover_from_path`'s `duho._discovered.` prefix) so a loose file
        # is never registered under a real dotted name -- `self.qualname`
        # alone would clobber `sys.modules["json"]` for the rest of the
        # process the first time an app builds a command named "json" from a
        # file, even though `json` itself was never imported yet.
        module = import_from_path("duho._cmdbuilder." + self.qualname, path)
        return self._wrap_module(module, stem=path.stem)

    def _import_package_at(self, path: _Path) -> Command:
        """Import ``self.qualname`` as a package, verified to resolve to ``path``.

        ``find_spec`` on a dotted qualname is only consulted for its own
        ``sys.path``-derived resolution; if that resolution does not include
        the exact directory the caller pointed at, this raises instead of
        silently importing whatever OTHER same-named package ``sys.path``
        happens to resolve first.
        """
        import importlib.util as _importutil

        try:
            spec = _importutil.find_spec(self.qualname)
        except (ImportError, ValueError):
            spec = None
        resolved = path.resolve()
        locations = list(spec.submodule_search_locations or ()) if spec else []
        if not any(_Path(loc).resolve() == resolved for loc in locations):
            raise ImportError(
                "qualname %r does not resolve (via sys.path) to the given "
                "package directory %s (resolved instead to: %r) -- for a "
                "package directory, the qualname must be importable AND "
                "resolve to this exact directory" % (self.qualname, path, locations),
                name=self.qualname,
                path=_os.fspath(path),
            )
        module = _importlib.import_module(self.qualname)
        return self._wrap_module(module)

    def _from_import(self, qualname: str) -> Command:
        import importlib.util as _importutil

        try:
            spec = _importutil.find_spec(qualname)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            raise ImportError(
                "no module named %r" % qualname,
                name=qualname,
            )
        # A namespace-ish package (no module origin, has search locations) is a
        # directory -- give providers a chance before importing it as a package.
        if not spec.origin and spec.submodule_search_locations:
            location = _Path(list(spec.submodule_search_locations)[0])
            builder = _match_provider(location)
            if builder is not None:
                return _ty.cast("Command", builder(location, qualname))
        module = _importlib.import_module(qualname)
        return self._wrap_module(module)

    def _wrap_module(self, module: object, stem: str | None = None) -> ModuleCommand:
        name = _resolved_module_name(module, stem=stem)
        return ModuleCommand(module, name=name)
