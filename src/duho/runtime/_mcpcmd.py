from __future__ import annotations

import typing as _ty

from ..discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    is_class_command as _is_class_command,
    is_module_command as _is_module_command,
)
from ..args._naming import _command_name as _command_name

from ._resolve import _full_names


def _resolve_mcp_command_name(
    root: type | None, mcp_command: str | bool | None
) -> str | None:
    """Resolve ``app()``'s opt-in MCP subcommand name.

    An explicit ``mcp_command`` wins, ``False`` included; ``None`` falls back to
    ``root``'s ``_mcp_command_`` (default ``False``). Returns ``None`` for off,
    ``"mcp"`` for ``True``, else the name, which must be non-empty, without
    whitespace and not start with ``"-"`` (``ValueError`` otherwise). Collisions
    and the need for another subcommand are checked by the caller.
    """
    value = (
        mcp_command
        if mcp_command is not None
        else getattr(root, "_mcp_command_", False)
    )
    if value is False or value is None:
        return None
    if value is True:
        return "mcp"
    name = str(value)
    if not name or any(ch.isspace() for ch in name):
        raise ValueError(
            "mcp_command=%r is not a valid subcommand name (it must be "
            "non-empty and contain no whitespace)" % (value,)
        )
    if name.startswith("-"):
        raise ValueError(
            "mcp_command=%r is not a valid subcommand name (it must not "
            "start with '-')" % (value,)
        )
    return name


def _existing_command_names(
    root: type | None, resolved_commands: _ty.Sequence[_Command]
) -> set[str]:
    """Every name (primary and aliases) already claimed by ``root``'s
    ``_subcommands_`` and ``resolved_commands``, so an ``mcp_command`` collision
    is a build-time ``ValueError`` instead of a silent override.
    """
    names: set[str] = set()
    for sub in getattr(root, "_subcommands_", None) or ():
        cmd_name = _command_name(sub)
        if cmd_name:
            names.update(_full_names(sub, cmd_name, "class"))
    for command in resolved_commands:
        if _is_class_command(command):
            cmd_name = _command_name(command)
            kind = "class"
        elif _is_module_command(command):
            cmd_name = _ty.cast(_ModuleCommand, command)._parsername_
            kind = "module"
        else:  # pragma: no cover - app() itself rejects this shape later
            continue
        names.update(_full_names(command, cmd_name, kind))
    return names


def _build_mcp_command_class(
    root: type | None,
    mcp_command: str | bool | None,
    other_command_names: set[str],
    *,
    has_other_subcommand: bool,
) -> type | None:
    """Resolve, validate and build the dynamic ``McpCmd`` subclass, or ``None``.

    Shared by :func:`app` and ``duho.main``, so the rules and ``ValueError`` text
    stay identical. ``ValueError`` if there is no other subcommand or the name
    is in ``other_command_names``. The subclass is fresh per call so two apps
    never share a ``_parsername_``; ``_duho_constants_`` is seeded empty (no
    ``_McpCmd`` in this module's source to AST-parse) and ``__doc__`` is set
    because ``type()`` does not inherit it, which would blank the ``--help`` row.
    """
    mcp_command_name = _resolve_mcp_command_name(root, mcp_command)
    if mcp_command_name is None:
        return None
    if not has_other_subcommand:
        raise ValueError(
            "mcp_command=%r requires this app to already have at least "
            "one other subcommand" % (mcp_command_name,)
        )
    if mcp_command_name in other_command_names:
        raise ValueError(
            "mcp_command=%r collides with an existing command name or "
            "alias" % (mcp_command_name,)
        )
    from .. import mcp as _mcp_module

    return type(
        "_McpCmd",
        (_mcp_module.McpCmd,),
        {
            "_parsername_": mcp_command_name,
            "_duho_constants_": {},
            "__doc__": "Serve this CLI as an MCP server",
        },
    )
