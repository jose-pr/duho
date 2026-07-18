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
import importlib.util as _importutil
import inspect as _inspect
import logging as _logging
import os as _os
import pkgutil as _pkgutil
import sys as _sys
import typing as _ty
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

from .args import Args as _Args, Cmd as _Cmd
from .qualname import PythonName as _PythonName

__all__ = [
    "Command",
    "ModuleCommand",
    "CmdBuilder",
    "register_command_provider",
    "discover_commands",
    "is_class_command",
    "is_module_command",
]

_LOGGER = _logging.getLogger("duho")

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


def _module_entrypoint(module: object) -> "_ty.Callable[..., object] | None":
    """Return a module's entrypoint callable (``main`` > ``run`` > ``call``), or None."""
    for candidate in _ENTRYPOINT_NAMES:
        fn = getattr(module, candidate, None)
        if callable(fn):
            return fn
    return None


def _resolved_module_name(module: object, stem: "str | None" = None) -> str:
    """Resolve a module command's subcommand name.

    A module-level ``_parsername_`` or ``_cli_name`` wins (explicit override);
    otherwise the module's file stem is used with ``_`` normalised to ``-``
    (e.g. ``deploy_all.py`` -> ``deploy-all``). ``stem`` overrides the derived
    stem when the caller already knows it (e.g. a synthesized ``sys.modules``
    name would otherwise be misleading).
    """
    override = getattr(module, "_parsername_", None) or getattr(module, "_cli_name", None)
    if override:
        return str(override)
    if stem is None:
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
    * ``_parsername_`` -- the resolved subcommand name (module ``_parsername_``/
      ``_cli_name`` override, else the file stem with ``_`` -> ``-``);
    * ``description`` / ``help`` -- from ``module.__doc__``;
    * the **entrypoint** -- ``module.main`` (primary), falling back to
      ``module.run`` / ``module.call``;
    * optional **lifecycle hooks** -- ``register`` (default no-op),
      ``init`` (default returns ``None`` -- no context), ``success`` /
      ``finally_`` (default no-ops).

    A ``ModuleCommand`` with no entrypoint raises ``NotImplementedError`` at
    construction (a module offering no ``main``/``run``/``call`` is not a
    command) -- ``discover_commands`` treats that as a skippable "not a command"
    signal, so a helpers-only module simply contributes nothing.

    **Hook signatures / logger source.** The lifecycle hooks (``init``/``success``/
    ``finally_``) and the entrypoint receive the parsed args instance and take
    their logger from that instance's ``_logger_`` (present on
    ``LoggingArgs``-based commands) rather than a separately threaded ``logger``
    argument. The resolved logger is available to hook authors as ``args._logger_``
    where the args class provides it, else this module's ``"duho"`` logger. The
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

        # Bind lifecycle hooks with contract defaults. ``init`` defaults to a
        # context-less builder (returns None); the others to no-ops. All
        # defaults swallow extra args so the driver's call shape need not match
        # a module's chosen arity exactly.
        self.register = getattr(module, "register", None) or _noop
        self.init = getattr(module, "init", None) or _init_noop
        self.success = getattr(module, "success", None) or _noop
        self.finally_ = getattr(module, "finally_", None) or _noop

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
        return _LOGGER

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


def _import_from_path(name: str, path: "_Path") -> "_ModuleType":
    """Import a ``.py`` file at ``path`` under module key ``name`` and return it.

    Uses ``spec_from_file_location`` + ``exec_module`` (stdlib only). The module
    is registered in ``sys.modules`` under ``name`` before execution so a module
    that inspects its own ``__name__`` / does relative-ish self-reference works.
    A missing/unloadable spec raises ``ImportError`` (skippable by discovery);
    an exception raised *by the module body* (e.g. ``SyntaxError``,
    ``NameError``) propagates unchanged.
    """
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
    return module


# --------------------------------------------------------------------------
# External provider injection hook
# --------------------------------------------------------------------------

#: Registry of (predicate, builder) pairs consulted by ``CmdBuilder`` for a
#: filesystem source before falling back to a normal import. A predicate takes
#: the resolved ``Path`` and returns True if its builder should handle it; the
#: builder takes ``(path, qualname)`` and returns a ``Command`` (or object
#: fulfilling it). Registered newest-first so a later registration can override
#: an earlier one for the same shape.
_PROVIDERS: "list[tuple[_ty.Callable[[_Path], bool], _ty.Callable[[_Path, str], object]]]" = []


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


def _match_provider(path: "_Path") -> "_ty.Callable[[_Path, str], object] | None":
    """Return the builder of the first provider whose predicate matches ``path``."""
    for predicate, builder in _PROVIDERS:
        try:
            if predicate(path):
                return builder
        except Exception:  # pragma: no cover - a broken predicate must not abort
            _LOGGER.debug("command provider predicate raised for %s", path, exc_info=True)
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
      ``sys.modules`` key (so a loose file never clobbers a real installed
      module of the same dotted name). A directory *with* ``__init__.py`` is a
      package and imported by qualname; a directory *without* one is offered to
      providers first (the seam where a run-path-style runtime plugs in) and, if
      unclaimed, raises ``ImportError`` (core duho has no meaning for a bare dir
      of files -- that meaning is exactly what a provider supplies).
    * ``source`` omitted/None -- ``qualname`` is treated as a dotted import path
      and imported via ``importlib.import_module`` (after checking providers for
      a namespace-package directory, mirroring the path branch).
    * ``source`` already a module or ``Command`` -- used directly.

    The resolved command is exposed as :attr:`command`. For a module source it
    is a :class:`ModuleCommand`; for a provider it is whatever the provider
    returns; for an already-``Command`` source it is that object.
    """

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

    def _from_path(self, path: "_Path") -> object:
        path = path.absolute()
        builder = _match_provider(path)
        if builder is not None:
            return builder(path, self.qualname)

        if path.is_dir():
            if (path / "__init__.py").exists():
                # A real package: import by qualname so relative imports work.
                return self._from_import(self.qualname)
            raise ImportError(
                "no command provider handles the directory %s (a bare directory "
                "without __init__.py has no built-in command meaning; register a "
                "provider to give it one)" % path,
                name=self.qualname,
                path=_os.fspath(path),
            )

        name = _unique_module_name(self.qualname)
        module = _import_from_path(name, path)
        return self._wrap_module(module, stem=path.stem)

    def _from_import(self, qualname: str) -> object:
        spec = _importutil.find_spec(qualname)
        if spec is None:
            raise ImportError(name=qualname)
        # A namespace-ish package (no module origin, has search locations) is a
        # directory -- give providers a chance before importing it as a package.
        if not spec.origin and spec.submodule_search_locations:
            location = _Path(list(spec.submodule_search_locations)[0])
            builder = _match_provider(location)
            if builder is not None:
                return builder(location, qualname)
        module = _importlib.import_module(qualname)
        return self._wrap_module(module)

    def _wrap_module(self, module: object, stem: "str | None" = None) -> "ModuleCommand":
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
        if getattr(obj, "__module__", None) != module_name:
            continue
        yield obj


def _commands_in_module(module: object, *, stem: "str | None" = None) -> "list[Command]":
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


def _command_name(command: object) -> str:
    """Resolve a command's subcommand name, for sorting/dedup.

    Class commands: ``_parsername_`` if set, else the class name (the same rule
    ``args.py``'s ``_parser_`` applies). Module commands: their resolved
    ``_parsername_``.
    """
    if is_class_command(command):
        return getattr(command, "_parsername_", None) or command.__name__  # type: ignore[union-attr]
    return getattr(command, "_parsername_", "")


def _looks_like_path(source: object) -> bool:
    """True if ``source`` should be treated as a filesystem path, not a dotted name.

    A ``Path``/``os.PathLike`` always is. A ``str`` is a path if it contains a
    separator (``/`` or ``\\``) or already names an existing directory;
    otherwise it is a dotted package name to import.
    """
    if isinstance(source, _Path) or (
        isinstance(source, _os.PathLike) and not isinstance(source, str)
    ):
        return True
    if isinstance(source, str):
        if "/" in source or "\\" in source:
            return True
        if _Path(source).is_dir():
            return True
    return False


def discover_commands(source: "str | _os.PathLike | _Path") -> "list[Command]":
    """Discover commands from a package name or a directory, resiliently.

    ``source`` is dispatched by shape:

    * a ``Path``/``os.PathLike``, or a ``str`` containing ``/`` or ``\\`` or
      naming an existing directory -> **filesystem**: iterate
      ``sorted(dir.glob("*.py"))``, skip ``_``-prefixed files, import each under
      a synthesized unique ``sys.modules`` name, and collect its commands;
    * any other ``str`` -> **dotted package**: ``import_module`` it, require a
      ``__path__`` (it must be a package, not a plain module), walk its
      submodules with ``pkgutil.iter_modules``, import each, and collect.

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
            _LOGGER.warning(
                "duho: skipping command module %r during discovery: %s",
                sub_name,
                exc,
            )
            continue
    return commands


def _discover_from_path(directory: "_Path") -> "list[Command]":
    directory = _Path(directory)
    if not directory.is_dir():
        raise ImportError(
            "not a directory: %s" % directory, path=_os.fspath(directory)
        )

    commands: "list[Command]" = []
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        stem = path.stem
        name = _unique_module_name("duho._discovered." + stem)
        try:
            module = _import_from_path(name, path)
            commands.extend(_commands_in_module(module, stem=stem))
        except (ImportError, NotImplementedError) as exc:
            _LOGGER.warning(
                "duho: skipping command file %s during discovery: %s", path, exc
            )
            continue
    return commands
