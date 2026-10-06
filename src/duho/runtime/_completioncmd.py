import typing as _ty


def _resolve_completion_command_name(root: "_ty.Optional[type]") -> "_ty.Optional[str]":
    """Resolve ``root``'s opt-in completion subcommand name from ``_completion_command_``.

    Returns ``None`` for ``False`` (or no root), ``"completion"`` for ``True``,
    or the given name for a non-empty ``str``. A name that is empty, contains
    whitespace or starts with ``"-"`` raises ``ValueError`` naming it.
    """
    value = getattr(root, "_completion_command_", False)
    if value is False or value is None:
        return None
    if value is True:
        return "completion"
    name = str(value)
    if not name or any(ch.isspace() for ch in name) or name.startswith("-"):
        raise ValueError(
            "_completion_command_=%r is not a valid subcommand name (it must be "
            "non-empty, contain no whitespace and not start with '-')" % (value,)
        )
    return name


def _build_completion_command_class(
    root: "_ty.Optional[type]",
    other_command_names: "set[str]",
    *,
    has_other_subcommand: bool,
) -> "_ty.Optional[type]":
    """Build the per-call ``CompletionCmd`` subclass for ``root``'s ``_completion_command_``.

    Returns ``None`` when the attribute is off. Otherwise requires another
    subcommand to exist and the name not to be in ``other_command_names``
    (``ValueError`` either way), like :func:`_build_mcp_command_class`. A fresh
    subclass per call, so two apps never share a class-level ``_parsername_``.
    """
    name = _resolve_completion_command_name(root)
    if name is None:
        return None
    if not has_other_subcommand:
        raise ValueError(
            "_completion_command_=%r requires this app to already have at "
            "least one other subcommand" % (name,)
        )
    if name in other_command_names:
        raise ValueError(
            "_completion_command_=%r collides with an existing command name "
            "or alias" % (name,)
        )
    from ..completion._cmd import CompletionCmd

    return type(
        "_CompletionCmd",
        (CompletionCmd,),
        {
            "_parsername_": name,
            "_duho_constants_": {},
            "__doc__": "Print a shell completion script for this CLI",
        },
    )
