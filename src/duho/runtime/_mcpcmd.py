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
    root: "type | None", mcp_command: "str | bool | None"
) -> "str | None":
    """Resolve ``app()``'s opt-in MCP subcommand name.
    ``mcp_command`` is ``app()``'s own explicit kwarg (``None``
    means "use the class attribute instead" -- including to turn a
    class-level ``True``/non-empty ``str`` back OFF by passing ``False``
    explicitly); the class attribute is ``root``'s own ``_mcp_command_``
    (declared on ``Cli``, default ``False``; ``root=None`` has none).

    Returns ``None`` (no subcommand) for ``False``, the literal ``"mcp"``
    for ``True``, or the given name for a non-empty ``str`` -- validated
    here (non-empty, no whitespace, not starting with ``"-"``), raising
    ``ValueError`` naming the bad value otherwise. Does NOT check for a
    name collision or "does this root even have another subcommand" --
    :func:`app` does both once the full resolved command set is known.
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
    root: "type | None", resolved_commands: "_ty.Sequence[_Command]"
) -> "set[str]":
    """Every name (primary + aliases) already claimed by ``root``'s own
    static ``_subcommands_`` plus ``resolved_commands`` -- used to reject an
    ``mcp_command`` name that collides with one of them, the same "every
    name a command claims" accounting :func:`_full_names` gives
    :func:`_register_commands`'s own collision handling, just checked
    up front so a collision is a build-time ``ValueError`` rather than a
    silent override.
    """
    names: "set[str]" = set()
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
    root: "type | None",
    mcp_command: "str | bool | None",
    other_command_names: "set[str]",
    *,
    has_other_subcommand: bool,
) -> "type | None":
    """Resolve, validate, and build the dynamic ``McpCmd`` subclass for
    ``root``'s opt-in MCP subcommand (``mcp_command=``/``root``'s own
    ``_mcp_command_``) -- the ONE place :func:`app` and ``duho.main`` both
    go through (the latter via a lazy ``from . import runtime``, since
    ``args.py`` never imports this module at load time), so the resolution
    rules and the exact ``ValueError`` text never drift between the two
    entry points.

    Returns ``None`` when no subcommand should be registered
    (:func:`_resolve_mcp_command_name` resolved ``mcp_command``/the class
    attribute to "off"). Otherwise validates that ``has_other_subcommand``
    is true and that the resolved name isn't already in
    ``other_command_names``, then builds and returns a fresh, per-call
    ``duho.mcp.McpCmd`` subclass under that name.

    A dynamic, per-call subclass -- never a shared one -- so two apps (or
    the same app/``cls`` registering under two different names across
    calls, e.g. in a test) never clash over a class-level ``_parsername_``.
    Seeds ``_duho_constants_`` empty like ``_module_args_cls``'s own
    synthesized class does: ``type(...)`` gives this class ``__module__`` =
    this module, which has no class named ``_McpCmd`` in its OWN source to
    AST-parse for. Also sets an explicit ``__doc__`` -- ``type()`` does NOT
    inherit ``__doc__`` from a base class (unlike every other declarative
    attribute, which normal ``getattr``/MRO lookup finds fine), so without
    this the subcommand's own ``--help`` row came up blank.
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
