import logging as _logging
import typing as _ty
from types import ModuleType as _ModuleType
from .. import _compat as _compat
from ..args import _command_name as _command_name
from ..logging import log_exception as _log_exception

from ._command import (
    Command,
    ModuleCommand,
    _LOGGER,
    _resolved_module_name,
    is_class_command,
    is_module_command,
)

# --------------------------------------------------------------------------
# Entry-point discovery
# --------------------------------------------------------------------------


def _coerce_entry_point_command(obj: object, name: "str | None") -> "Command | None":
    """Coerce an ``EntryPoint.load()`` result to a :class:`Command`, or None.

    An entry point may resolve to any of the command shapes the other sources
    accept, run through the same coercion:

    * a :class:`~duho.Cmd` **subclass** or an already-:class:`Command` object --
      used as-is (a class command names itself via ``_parsername_``/class name).
      A **class** whose own ``__name__`` starts with ``_`` is refused, though
      (yields ``None``, same as "not a command" below) -- the identical
      "private, not a command" convention every other class-command source
      already enforces (:func:`_iter_class_commands`'s own ``_`` skip); an
      entry point is simply a different way to REACH the same class object,
      and had bypassed that convention entirely (an entry point advertising a
      private base class -- meant only for other command classes to
      subclass, never to be listed/run itself -- was still discovered and
      registered as a real subcommand);
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
    if is_class_command(obj):
        if _ty.cast(type, obj).__name__.startswith("_"):
            return None
        return _ty.cast(Command, obj)
    if is_module_command(obj):
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
    the load. The result is sorted by resolved subcommand name for
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
