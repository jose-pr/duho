"""Command discovery: turn a class, module, import path, or directory into ``Cmd``s.

This module answers "give me the runnable commands living over there" for four
shapes of *there*:

* a :class:`~duho.Cmd` subclass -- already a command, used as-is;
* a command **module** -- a ``.py`` file whose top-level ``main``/``run``/``call``
  is the entrypoint, adapted to the command contract by :class:`ModuleCommand`
  (a plain wrapper -- it does NOT subclass ``types.ModuleType``);
* an **import path** (dotted qualname) or a **filesystem path** -- resolved to a
  module by :class:`CmdBuilder`;
* a **package or directory** -- walked by :func:`discover_commands`, which
  collects both class commands and module commands from every submodule/file.

Two design points worth calling out:

* **Resilience.** :func:`discover_commands` treats a single unimportable or
  unsupported command as skippable, not fatal: ``ImportError`` and
  ``NotImplementedError`` (and subclasses) on one command are logged and skipped
  so the rest still load. A genuinely broken command file (e.g. a
  ``SyntaxError``) is NOT swallowed -- it is a real bug the author wants
  surfaced. See :func:`discover_commands` for the exact caught set and rationale.
* **Injection hook.** :func:`register_command_provider` lets an external package
  teach :class:`CmdBuilder` how to build a command from a directory shape core
  duho does not itself understand (e.g. a directory of numbered step files),
  WITHOUT core duho importing that package. If no provider matches, a directory
  or module is imported normally.

All union annotations are quoted so the module imports cleanly on Python 3.9.
"""

import importlib as _importlib
import inspect as _inspect
import logging as _logging
import os as _os
import sys as _sys
import typing as _ty
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

from . import _compat as _compat
from .args import Args as _Args, Cmd as _Cmd, _command_name as _command_name
from .logging import log_exception as _log_exception
from .qualname import PythonName as _PythonName

# `_command_name` used to be a byte-for-byte copy of `args._command_name`
# (itself re-derived a THIRD time in `runtime.py` and inlined again in
# `mcp.py`) -- imported directly instead, so there is exactly one definition
# (the own-class-dict rule lives there) shared by every reader that needs
# a command's subcommand name: `args.py` itself, `runtime.py`, `mcp.py`, and
# `presets.LoggingArgs._logger_`.

__all__ = [
    "Command",
    "ModuleCommand",
    "CmdBuilder",
    "register_command_provider",
    "unregister_command_provider",
    "import_from_path",
    "discover_commands",
    "discover_entry_points",
    "is_class_command",
    "is_module_command",
]

_LOGGER = _logging.getLogger(__name__)

#: The logger handed to a command MODULE's hooks when its args instance
#: has no `_logger_`. The app-facing "duho" parent, not this module's own
#: `_LOGGER` -- a command module's output is the app's, not discovery's.
_HOOK_LOGGER = _logging.getLogger("duho")

#: Names, in priority order, looked up on a module to find its entrypoint.
#: ``main`` is primary (aligns with the ``__main__`` convention); ``run``/``call``
#: are accepted fallbacks for modules written against an older calling shape.
_ENTRYPOINT_NAMES = ("main", "run", "call")

#: Optional module-level lifecycle hooks a command module may define. All
#: default to no-ops (``init`` to an identity returning ``None`` context) when
#: absent, so a bare ``def main(...)`` module is a complete command.
_LIFECYCLE_NAMES = ("register", "init", "success", "finally_")


# --------------------------------------------------------------------------
# Command protocol + predicates
# --------------------------------------------------------------------------


@_ty.runtime_checkable
class Command(_ty.Protocol):
    """The shape ``discover_commands``/dispatch needs from a command.

    A command is anything that can name itself as a subcommand and be run.
    Two concrete kinds fulfil it:

    * a :class:`~duho.Cmd` **subclass** -- a *class command*; its
      ``_parsername_``/class name names the subcommand, ``_parser_`` builds its
      parser, and an instance is run via ``__call__``;
    * a :class:`ModuleCommand` -- a *module command* wrapping a ``.py`` module,
      exposing the same surface.

    This is a structural ``Protocol`` (not an ABC): the predicates
    :func:`is_class_command` / :func:`is_module_command` classify concrete
    objects, and callers branch on those rather than instantiating an ABC.
    """

    #: Subcommand name (``_parsername_`` for classes; the resolved stem/override
    #: name for modules).
    _parsername_: str

    def __call__(self) -> object:  # pragma: no cover - protocol stub
        ...


def is_class_command(obj: object) -> bool:
    """True if ``obj`` is a class command: a ``Cmd`` subclass (not ``Cmd`` itself)."""
    return _inspect.isclass(obj) and issubclass(obj, _Cmd) and obj is not _Cmd


def is_module_command(obj: object) -> bool:
    """True if ``obj`` is a module command (a :class:`ModuleCommand` wrapper)."""
    return isinstance(obj, ModuleCommand)


def _own_callable(module: object, name: str) -> "_ty.Callable[..., object] | None":
    """Return ``module``'s attribute ``name`` only if it is genuinely DEFINED there.

    Guards the entrypoint (``main``/``run``/``call``) and the lifecycle hooks
    (``register``/``init``/``success``/``finally_``) against an *imported*
    callable of the same name -- ``from subprocess import run`` must not become
    a module command's entrypoint, and ``from colorama import init`` must not
    become its ``init`` hook. For a real ``types.ModuleType``, ``fn`` counts
    only when ``fn.__module__ == module.__name__`` (mirrors the class-command
    module-boundary filter in :func:`_iter_class_commands`) or when ``name`` is
    listed in the module's own ``__all__`` (the escape hatch for a deliberate
    re-export, e.g. ``from ._impl import main; __all__ = ["main"]``).

    A non-``ModuleType`` source (anything else exposing the same attributes,
    per :class:`ModuleCommand`'s "plain wrapper" contract) keeps the prior
    "just check it's callable" behavior, since there is no meaningful
    ``__module__`` boundary to enforce for a duck-typed object.
    """
    fn = getattr(module, name, None)
    if not callable(fn):
        return None
    if isinstance(module, _ModuleType):
        if getattr(fn, "__module__", None) == getattr(module, "__name__", None):
            return fn
        exported = getattr(module, "__all__", None)
        if exported and name in exported:
            return fn
        return None
    return fn


def _module_entrypoint(module: object) -> "_ty.Callable[..., object] | None":
    """Return a module's entrypoint callable (``main`` > ``run`` > ``call``), or None.

    Only a callable actually DEFINED in the module counts (see
    :func:`_own_callable`) -- an imported ``run``/``call``/``main`` (e.g.
    ``from subprocess import run``) is not a command entrypoint.
    """
    for candidate in _ENTRYPOINT_NAMES:
        fn = _own_callable(module, candidate)
        if fn is not None:
            return fn
    return None


def _resolved_module_name(module: object, stem: "str | None" = None) -> str:
    """Resolve a module command's subcommand name.

    A module-level ``_parsername_`` wins (explicit override); otherwise the
    module's file stem is used with ``_`` normalised to ``-`` (e.g.
    ``deploy_all.py`` -> ``deploy-all``). For a PACKAGE (``module.__path__``
    is set -- a dotted import or a directory with ``__init__.py``), the stem
    is instead the last dotted segment of ``module.__name__``, so a package's
    module command is named after the package, not ``--init--`` (the
    ``__init__.py`` file stem). ``stem`` overrides the derived stem when the
    caller already knows it (e.g. a synthesized ``sys.modules`` name would
    otherwise be misleading).
    """
    override = getattr(module, "_parsername_", None)
    if override:
        return str(override)
    if stem is None:
        if getattr(module, "__path__", None) is not None:
            stem = str(getattr(module, "__name__", "")).rsplit(".", 1)[-1]
        else:
            modfile = getattr(module, "__file__", None)
            if modfile:
                stem = _Path(modfile).stem
            else:
                stem = str(getattr(module, "__name__", "")).rsplit(".", 1)[-1]
    return stem.replace("_", "-")


# --------------------------------------------------------------------------
# Module -> Command wrapper
# --------------------------------------------------------------------------


class ModuleCommand:
    """Adapt a command *module* to the :class:`Command` contract.

    Wraps a module (a ``types.ModuleType``, or anything exposing the same
    attributes) whose top-level function is the command body. It is a **plain
    wrapper** -- it does NOT subclass ``ModuleType`` -- so it stays a normal
    object with no import-system entanglement.

    It carries:

    * ``module`` -- the wrapped module;
    * ``_parsername_`` -- the resolved subcommand name (a module-level
      ``_parsername_`` override, else the file stem with ``_`` -> ``-``);
    * ``description`` / ``help`` -- from ``module.__doc__``;
    * the **entrypoint** -- ``module.main`` (primary), falling back to
      ``module.run`` / ``module.call``. Only a callable genuinely DEFINED in
      the module counts (see :func:`_own_callable`) -- an *imported* function
      of the same name (``from subprocess import run``) is never mistaken for
      the entrypoint;
    * optional **lifecycle hooks** -- ``register`` (default no-op),
      ``init`` (default returns ``None`` -- no context), ``success`` /
      ``finally_`` (default no-ops);
    * ``args_cls`` -- an optional module-level ``Args`` declaring this
      module's own CLI fields DECLARATIVELY (added to the subparser before
      ``register`` runs -- see ``runtime._register_module_command``), as an
      alternative to adding everything imperatively in ``register``. Accepts
      either an already-``Args``-subclassing ``Args`` (used directly) or a
      plain class with annotated fields (mixed in with ``Args`` on the fly so
      its annotations still work as CLI fields, with no explicit
      import/subclass of ``duho.Args`` required). ``None`` if the module
      declares no usable ``Args``.

    A ``ModuleCommand`` with no entrypoint raises ``NotImplementedError`` at
    construction (a module offering no ``main``/``run``/``call`` is not a
    command) -- ``discover_commands`` treats that as a skippable "not a command"
    signal, so a helpers-only module simply contributes nothing.

    Only a callable genuinely DEFINED in the module is bound to a hook name
    (see :func:`_own_callable`) -- ``from colorama import init`` never becomes
    the ``init`` hook.

    **Hook signatures / logger source.** The lifecycle hooks (``init``/``success``/
    ``finally_``) and the entrypoint receive the parsed args instance and read
    their logger from that instance's ``_logger_`` (present on
    ``LoggingArgs``-based commands) rather than a separately threaded ``logger``
    argument. The driver (``runtime.run_command``) ensures ``args._logger_`` is
    present before calling them: when the parsed instance has none, it sets one
    via :meth:`_logger_for` (the args instance's own ``_logger_`` if present,
    else this module's ``"duho"`` logger) so a hook written against the
    documented convention never hits ``AttributeError`` on a plain root. The
    concrete hook calls made by the driver are: ``ctx = init(args)``,
    ``main(args)`` / ``entrypoint(args)``, ``success(ctx, args)``,
    ``finally_(ctx, args)``; the defaults installed here accept ``*args, **kwargs``
    so a module may omit any hook (or define a narrower signature and simply not
    receive extras it does not declare).

    The ``register`` hook is the one exception, and is **arity-tolerant**: it may
    be written either ``register(parser, args)`` (2-arg) or
    ``register(parser, args, logger)`` (3-arg). The driver
    (``runtime._register_module_command``) inspects the hook's signature and, for
    a 3-arg hook, passes ``logger = getattr(args, "_logger_",
    logging.getLogger("duho"))``; a 2-arg hook is called unchanged. A ``*args``
    hook is treated as 3-arg-capable, and a non-introspectable hook falls back to
    the 2-arg call. Either way the hook adds its own arguments directly on the
    subcommand's argparse ``parser``.
    """

    def __init__(
        self,
        module: object,
        *,
        name: "str | None" = None,
        entrypoint: "_ty.Callable[..., object] | None" = None,
    ) -> None:
        self.module = module
        self._parsername_ = name or _resolved_module_name(module)

        entry = entrypoint if entrypoint is not None else _module_entrypoint(module)
        if entry is None:
            raise NotImplementedError(
                "module %r is not a command: it defines none of %s"
                % (getattr(module, "__name__", module), ", ".join(_ENTRYPOINT_NAMES))
            )
        self._entrypoint = entry

        # A module-level `Args` declares this module's own CLI fields,
        # combined with the app's shared root class (see
        # `runtime._register_module_command`, which does the actual mixing
        # since the root class is only known at registration time) and added
        # to the subparser before `register` runs. Stored as-is here: either
        # a real `Args` subclass (used directly by the caller) or a plain
        # class (mixed with the root at registration time so its own
        # annotated attrs still work as CLI fields -- `_introspect.get_clsargs`
        # walks the MRO -- without the author needing to import/subclass
        # `duho.Args` or the app's root explicitly).
        #
        # A STRICT-subclass check distinguishes "a real declared class" from
        # "the module did `from duho import Args` for its own use but never
        # subclassed it" -- `getattr(module, "Args", None)` would resolve to
        # `duho.args.Args` (or `Cmd`/`Cli`) itself there, which this correctly
        # treats as "nothing declared", not a usable class.
        args_cls = getattr(module, "Args", None)
        self.args_cls: "type | None" = (
            args_cls
            if _inspect.isclass(args_cls) and args_cls not in (_Args, _Cmd)
            else None
        )

        # Bind lifecycle hooks with contract defaults. ``init`` defaults to a
        # context-less builder (returns None); the others to no-ops. All
        # defaults swallow extra args so the driver's call shape need not match
        # a module's chosen arity exactly. `_own_callable` guards each hook the
        # same way as the entrypoint: an *imported* function of the same name
        # (`from atexit import register`, `from colorama import init`) is
        # never mistaken for the module's own hook.
        self.register = _own_callable(module, "register") or _noop
        self.init = _own_callable(module, "init") or _init_noop
        self.success = _own_callable(module, "success") or _noop
        self.finally_ = _own_callable(module, "finally_") or _noop

    @property
    def description(self) -> str:
        """Full command help -- the wrapped module's docstring, stripped."""
        return (getattr(self.module, "__doc__", None) or "").strip()

    @property
    def help(self) -> str:
        """One-line help -- the first line of :attr:`description`."""
        lines = self.description.splitlines()
        return lines[0] if lines else ""

    def _logger_for(self, args: object) -> "_logging.Logger":
        """Resolve the logger for a run: the args instance's ``_logger_`` if any."""
        logger = getattr(args, "_logger_", None)
        if isinstance(logger, _logging.Logger):
            return logger
        return _HOOK_LOGGER

    def main(self, args: "object | None" = None) -> object:
        """Run the command by invoking the wrapped module's entrypoint.

        Called with the parsed args instance during dispatch. Kept
        arg-optional so a ``ModuleCommand`` is trivially callable in tests /
        direct use; the driver always passes the parsed args.
        """
        if args is None:
            return self._entrypoint()
        return self._entrypoint(args)

    def __call__(self, args: "object | None" = None) -> object:
        """A ``ModuleCommand`` is directly callable; delegates to :meth:`main`."""
        return self.main(args)

    def __repr__(self) -> str:
        return "ModuleCommand(name=%r, module=%r)" % (
            self._parsername_,
            getattr(self.module, "__name__", self.module),
        )


def _noop(*args: object, **kwargs: object) -> None:
    """Default lifecycle hook: accept anything, do nothing."""
    return None


def _init_noop(*args: object, **kwargs: object) -> None:
    """Default ``init`` hook: build no context (returns ``None``)."""
    return None


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
    (D037) so an external command-provider package (``duho.runpath`` is the
    first, and so far only, consumer) can import a ``.py`` file the exact
    same way without reaching into either private helper directly.
    """
    return _import_from_path(_unique_module_name(base_name), path)


# --------------------------------------------------------------------------
# External provider injection hook
# --------------------------------------------------------------------------

#: Registry of (predicate, builder) pairs consulted by ``CmdBuilder`` for a
#: filesystem source before falling back to a normal import. A predicate takes
#: the resolved ``Path`` and returns True if its builder should handle it; the
#: builder takes ``(path, qualname)`` and returns a ``Command`` (or object
#: fulfilling it). Registered newest-first so a later registration can override
#: an earlier one for the same shape.
_PROVIDERS: (
    "list[tuple[_ty.Callable[[_Path], bool], _ty.Callable[[_Path, str], object]]]"
) = []


def register_command_provider(
    predicate: "_ty.Callable[[_Path], bool]",
    builder: "_ty.Callable[[_Path, str], object]",
) -> None:
    """Register an external provider that builds a ``Command`` from a directory.

    This is the extension seam that keeps directory-shaped command runtimes
    (e.g. an ordered "run-path" of numbered step files) OUT of core duho: an
    external package registers ``(predicate, builder)``; when ``CmdBuilder``
    resolves a filesystem source, it consults registered providers *before*
    importing the path normally, and the first matching provider's ``builder``
    produces the command. If no provider matches, the path is imported as a
    plain module/package.

    * ``predicate(path: Path) -> bool`` -- True if this provider handles ``path``.
    * ``builder(path: Path, qualname: str) -> Command`` -- build the command.

    Providers are consulted most-recently-registered first, so a later
    registration can take precedence over an earlier one for the same shape.
    """
    _PROVIDERS.insert(0, (predicate, builder))


def unregister_command_provider(
    predicate: "_ty.Callable[[_Path], bool]",
    builder: "_ty.Callable[[_Path, str], object]",
) -> None:
    """Remove a provider previously registered with
    :func:`register_command_provider` -- the exact ``(predicate, builder)``
    pair (matched the same way ``list.remove`` would).

    A no-op if that exact pair is not currently registered, so a caller does
    not need to track whether it already unregistered (D037). Before this,
    the provider seam had no supported way to opt back out: a consumer
    needing one (test isolation, a plugin reloading itself) had no choice but
    to reach into ``_PROVIDERS`` directly.
    """
    try:
        _PROVIDERS.remove((predicate, builder))
    except ValueError:
        pass


def _match_provider(path: "_Path") -> "_ty.Callable[[_Path, str], object] | None":
    """Return the builder of the first provider whose predicate matches ``path``."""
    for predicate, builder in _PROVIDERS:
        try:
            if predicate(path):
                return builder
        except Exception:  # pragma: no cover - a broken predicate must not abort
            _LOGGER.debug(
                "command provider predicate raised for %s", path, exc_info=True
            )
    return None


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
    #: typed ``Command`` (D029), not the ``object`` a provider's own loose
    #: ``Callable[[Path, str], object]`` signature would otherwise infer.
    command: "Command"

    def __init__(
        self,
        qualname: "str | _PythonName",
        source: "_Path | str | _os.PathLike | _ModuleType | Command | None" = None,
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

    def _from_path(self, path: "_Path") -> "Command":
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
        # file, even though `json` itself was never imported yet (D005).
        module = import_from_path("duho._cmdbuilder." + self.qualname, path)
        return self._wrap_module(module, stem=path.stem)

    def _import_package_at(self, path: "_Path") -> "Command":
        """Import ``self.qualname`` as a package, verified to resolve to ``path``.

        ``find_spec`` on a dotted qualname is only consulted for its own
        ``sys.path``-derived resolution; if that resolution does not include
        the exact directory the caller pointed at, this raises instead of
        silently importing whatever OTHER same-named package ``sys.path``
        happens to resolve first (D041).
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

    def _from_import(self, qualname: str) -> "Command":
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

    def _wrap_module(
        self, module: object, stem: "str | None" = None
    ) -> "ModuleCommand":
        name = _resolved_module_name(module, stem=stem)
        return ModuleCommand(module, name=name)


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
    user is standing and shadowing the intended package (D019/security).
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

    The result is sorted by resolved subcommand name for deterministic
    ``--help`` output (filesystem iteration order is OS-dependent).
    """
    if _looks_like_path(source):
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

    **Sibling imports (D018).** While importing each file, ``directory`` is
    temporarily prepended to ``sys.path`` so a bare ``from _helpers import x``
    resolves -- the documented convention for factoring shared code into a
    ``_``-prefixed helper file that command files in the same directory can
    import (the ``_`` prefix means "not a command", not "unimportable"). The
    directory is removed from ``sys.path`` again immediately after, and any
    module this pulled in beyond the command file's own synthetic key (the
    helper module itself, imported under its own bare name) is popped back out
    of ``sys.modules`` -- so a *different* discovered directory that also ships
    a same-named helper is never served a stale cached one. This only supports
    the bare/absolute form (``from _helpers import x``); a relative
    ``from ._helpers import x`` still fails, since these files have no real
    parent package.
    """
    directory = _Path(directory)
    if not directory.is_dir():
        raise ImportError("not a directory: %s" % directory, path=_os.fspath(directory))

    commands: "list[Command]" = []
    dirstr = _os.fspath(directory)
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        stem = path.stem
        before_modules = set(_sys.modules)
        _sys.path.insert(0, dirstr)
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
            try:
                _sys.path.remove(dirstr)
            except ValueError:  # pragma: no cover - defensive
                pass
            own_key = module.__name__ if module is not None else None
            for extra in set(_sys.modules) - before_modules:
                if extra != own_key:
                    _sys.modules.pop(extra, None)
        commands.extend(_commands_in_module(module, stem=stem))
    return commands


# --------------------------------------------------------------------------
# Entry-point discovery (F6)
# --------------------------------------------------------------------------


def _coerce_entry_point_command(obj: object, name: "str | None") -> "Command | None":
    """Coerce an ``EntryPoint.load()`` result to a :class:`Command`, or None.

    An entry point may resolve to any of the command shapes the other sources
    accept, run through the same coercion:

    * a :class:`~duho.Cmd` **subclass** or an already-:class:`Command` object --
      used as-is (a class command names itself via ``_parsername_``/class name);
    * a **module** (a plugin whose top-level ``main``/``run``/``call`` is the
      entrypoint) -- wrapped in a :class:`ModuleCommand`. The module's OWN
      ``_parsername_`` wins when set (matching every other command source);
      only when the module declares none is the entry point's advertised
      ``name`` used, falling back to :func:`_resolved_module_name` when even
      that is unavailable.

    Anything else (e.g. a bare function or a helpers-only module with no
    entrypoint) yields ``None`` so the caller logs and skips it. A module with no
    entrypoint surfaces as ``ModuleCommand``'s ``NotImplementedError``, caught by
    the caller.
    """
    if is_class_command(obj) or is_module_command(obj):
        return _ty.cast(Command, obj)
    if isinstance(obj, _ModuleType):
        resolved = (
            getattr(obj, "_parsername_", None) or name or _resolved_module_name(obj)
        )
        return _ty.cast(Command, ModuleCommand(obj, name=resolved))
    return None


def discover_entry_points(group: str) -> "list[Command]":
    """Discover commands from installed-distribution entry points in ``group``.

    This is the plugin-discovery source behind ``duho.app(root,
    entry_points="myapp.commands")``: every entry point advertised in ``group``
    by an installed distribution is loaded (``EntryPoint.load()``) and coerced to
    a :class:`Command` via :func:`_coerce_entry_point_command` -- a ``Cmd``
    subclass becomes a class command, a module becomes a module command.

    **Resilience** mirrors :func:`discover_commands`: an entry point that fails to
    load (a broken/renamed target, a missing optional dependency) or that does
    not resolve to a command is logged at ``WARNING`` and skipped, so one bad
    plugin never takes the app down -- the rest still load.

    ``importlib.metadata`` is imported lazily (inside :func:`_compat.iter_entry_points`)
    so a plain ``import duho`` never pays its cost -- only calling this triggers
    the load (plan 02 P1). The result is sorted by resolved subcommand name for
    deterministic ``--help`` output.
    """
    commands: "list[Command]" = []
    for entry_point in _compat.iter_entry_points(group):
        ep_name = getattr(entry_point, "name", None)
        try:
            loaded = entry_point.load()
            command = _coerce_entry_point_command(loaded, ep_name)
        except Exception as exc:  # noqa: BLE001 - a bad plugin must not abort the app
            _log_exception(
                _LOGGER,
                "skipping entry point %r in group %r: failed to load (%s)",
                ep_name if ep_name is not None else entry_point,
                group,
                exc,
                level=_logging.WARNING,
            )
            continue
        if command is None:
            _LOGGER.warning(
                "skipping entry point %r in group %r: %r is not a command "
                "(expected a Cmd subclass, a command module, or a Command)",
                ep_name if ep_name is not None else entry_point,
                group,
                loaded,
            )
            continue
        commands.append(command)
    return sorted(commands, key=_command_name)
