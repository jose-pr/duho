import inspect as _inspect
import logging as _logging
import typing as _ty
from pathlib import Path as _Path
from types import ModuleType as _ModuleType

from ..args import Args as _Args, Cmd as _Cmd

_LOGGER = _logging.getLogger(__package__)

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
    become its ``init`` hook. For a real ``types.ModuleType``, a **plain
    function, a bound/unbound method, a builtin/C function or method** (e.g.
    ``atexit.register`` -- ``inspect.isfunction``/``ismethod``/``isbuiltin``),
    or a **class** ``fn`` counts only when ``fn.__module__ ==
    module.__name__`` (mirrors the class-command module-boundary filter in
    :func:`_iter_class_commands`) or when ``name`` is listed in the module's
    own ``__all__`` (the escape hatch for a deliberate re-export, e.g.
    ``from ._impl import main; __all__ = ["main"]``).

    **Any other callable -- a ``functools.partial``, or a plain callable
    instance (``class Runner: __call__ = ...; main = Runner()``) -- is
    accepted unconditionally**, without that ``__module__`` check: such an
    object's ``__module__`` reflects where its TYPE was defined, never where
    the particular instance was actually constructed (``functools.partial(
    ...).__module__`` is always the literal string ``"functools"``, no matter
    which module built the partial) -- comparing it against ``module.__name__``
    would reject a genuinely module-level ``main = functools.partial(_impl)``
    just as readily as a real cross-module import, with no way to tell the
    two apart. Deliberately NOT ``inspect.isroutine`` here: a
    ``functools.partial`` instance implements ``__get__`` (so it also passes
    as a method-descriptor to attribute lookup), which makes
    ``inspect.isroutine`` -- and therefore the ``__module__`` boundary check
    -- wrongly true for it too, right back to rejecting it; the four explicit
    predicates above cover every shape whose ``__module__`` genuinely tracks
    its own definition site, and nothing else.

    A non-``ModuleType`` source (anything else exposing the same attributes,
    per :class:`ModuleCommand`'s "plain wrapper" contract) keeps the prior
    "just check it's callable" behavior, since there is no meaningful
    ``__module__`` boundary to enforce for a duck-typed object.
    """
    fn = getattr(module, name, None)
    if not callable(fn):
        return None
    if isinstance(module, _ModuleType):
        if not (
            _inspect.isfunction(fn)
            or _inspect.isbuiltin(fn)
            or _inspect.ismethod(fn)
            or _inspect.isclass(fn)
        ):
            return fn
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
    for candidate in _ENTRYPOINT_NAMES:
        fn = getattr(module, candidate, None)
        if callable(fn) and (
            hasattr(fn, "__wrapped__") or "<locals>" in getattr(fn, "__qualname__", "")
        ):
            _LOGGER.warning(
                "module %r binds %r to a wrapped function (a decorator "
                "defined elsewhere), so it is not a command entrypoint; "
                "list %r in the module's __all__ to accept it",
                getattr(module, "__name__", module),
                candidate,
                candidate,
            )
            break
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
    * ``_mcp_`` -- a module-level ``_mcp_ = False`` opts this command (and,
      when it is a namespace, its whole subtree) out of the MCP tool
      surface -- ``duho.mcp``'s per-command exclusion. Default ``True``
      (read via ``getattr(module, "_mcp_", True)``, so a module that never
      mentions it is unaffected).

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
        # A module-level `_mcp_ = False` opts this command out of the MCP
        # tool surface (`duho.mcp`'s per-command exclusion), mirroring
        # `_parsername_`'s own "read a module-level override, stash it as a
        # plain instance attribute" pattern -- `duho.mcp` reads it straight
        # off this attribute, never the raw module.
        self._mcp_ = getattr(module, "_mcp_", True)

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
        # An identity check (not a subclass check) distinguishes "a real
        # declared class" from "the module did `from duho import Args` for
        # its own use but never subclassed it" -- `getattr(module, "Args",
        # None)` would resolve to `duho.args.Args` (or `Cmd`) itself there,
        # which this correctly treats as "nothing declared", not a usable
        # class. A subclass of either (including one that also mixes in
        # `Cli`) still passes, since `not in (_Args, _Cmd)` only excludes the
        # two bare base classes themselves.
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
