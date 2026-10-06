from __future__ import annotations

import logging as _logging
import typing as _ty
from types import ModuleType as _ModuleType

from .. import _compat as _compat
from ..args._naming import _command_name as _command_name
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


def _coerce_entry_point_command(obj: object, name: str | None) -> Command | None:
    """Coerce an ``EntryPoint.load()`` result to a :class:`Command`, or ``None``.

    A ``Cmd`` subclass or ``Command`` object is used as-is, except a class
    whose name starts with ``_``: a private base is not a command here either
    (``None``). A module becomes a :class:`ModuleCommand`, named by its own
    ``_parsername_``, else the entry point's ``name``, else
    :func:`_resolved_module_name`. Anything else yields ``None`` for the caller
    to log and skip; a module with no entrypoint raises ``NotImplementedError``,
    which the caller also handles.
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


def discover_entry_points(group: str) -> list[Command]:
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
    commands: list[Command] = []
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
