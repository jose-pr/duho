from __future__ import annotations

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


def _own_callable(module: object, name: str) -> _ty.Callable[..., object] | None:
    """``module``'s attribute ``name`` if it is a callable defined there, else ``None``.

    Keeps an imported callable (``from subprocess import run``) from becoming
    the entrypoint or a hook. For a real module, a function, method, builtin or
    class counts only when its ``__module__`` is the module's own or ``name``
    is in ``__all__`` (a deliberate re-export). Any other callable, such as a
    ``functools.partial`` or a callable instance, is accepted: its
    ``__module__`` names where its type was defined, so the check cannot tell
    it from an import (and ``inspect.isroutine`` would wrongly match a
    partial). A non-module object is accepted if callable.
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


def _module_entrypoint(module: object) -> _ty.Callable[..., object] | None:
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


def _resolved_module_name(module: object, stem: str | None = None) -> str:
    """Resolve a module command's subcommand name.

    A module-level ``_parsername_`` wins; otherwise the file stem with ``_`` as
    ``-``. For a package (``__path__`` set) the stem is the last dotted segment
    of ``__name__``, not ``__init__``. ``stem`` overrides the derived one.
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
        name: _ty.Optional[str] = None,
        entrypoint: _ty.Optional[_ty.Callable[..., object]] = None,
    ) -> None:
        self.module = module
        self._parsername_ = name or _resolved_module_name(module)
        # A module-level `_mcp_ = False` excludes this command from the MCP
        # tool surface; `duho.mcp` reads this attribute, never the module.
        self._mcp_ = getattr(module, "_mcp_", True)

        entry = entrypoint if entrypoint is not None else _module_entrypoint(module)
        if entry is None:
            raise NotImplementedError(
                "module %r is not a command: it defines none of %s"
                % (getattr(module, "__name__", module), ", ".join(_ENTRYPOINT_NAMES))
            )
        self._entrypoint = entry

        # A plain class is mixed with the root class at registration. The
        # identity check rejects `Args`/`Cmd` themselves, which a module that
        # only imported them resolves here; subclasses pass.
        args_cls = getattr(module, "Args", None)
        self.args_cls: type | None = (
            args_cls
            if _inspect.isclass(args_cls) and args_cls not in (_Args, _Cmd)
            else None
        )

        # Bind hooks, defaulting to no-ops that swallow extra args so the
        # driver's call shape need not match a module's arity. `_own_callable`
        # keeps an imported function of the same name from becoming a hook.
        self.register = _own_callable(module, "register") or _noop
        self.init = _own_callable(module, "init") or _init_noop
        self.success = _own_callable(module, "success") or _noop
        self.finally_ = _own_callable(module, "finally_") or _noop

    @property
    def entrypoint(self) -> _ty.Callable[..., object]:
        """The resolved callable (``main``/``run``/``call`` or the one passed in)."""
        return self._entrypoint

    @property
    def description(self) -> str:
        """Full command help -- the wrapped module's docstring, stripped."""
        return (getattr(self.module, "__doc__", None) or "").strip()

    @property
    def help(self) -> str:
        """One-line help -- the first line of :attr:`description`."""
        lines = self.description.splitlines()
        return lines[0] if lines else ""

    def _logger_for(self, args: object) -> _logging.Logger:
        """Resolve the logger for a run: the args instance's ``_logger_`` if any."""
        logger = getattr(args, "_logger_", None)
        if isinstance(logger, _logging.Logger):
            return logger
        return _HOOK_LOGGER

    def main(self, args: _ty.Optional[object] = None) -> object:
        """Run the command by invoking the wrapped module's entrypoint.

        Called with the parsed args instance during dispatch. Kept
        arg-optional so a ``ModuleCommand`` is trivially callable in tests /
        direct use; the driver always passes the parsed args.
        """
        if args is None:
            return self._entrypoint()
        return self._entrypoint(args)

    def __call__(self, args: _ty.Optional[object] = None) -> object:
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
