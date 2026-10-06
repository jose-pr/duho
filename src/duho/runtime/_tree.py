from __future__ import annotations

import argparse as _argparse
import logging as _logging
import typing as _ty

from .. import parsers as _parsers
from ..args._parserfix import (
    _set_private_default as _set_private_default,
    _suppress_inherited_defaults as _suppress_inherited_defaults,
)
from ..discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    is_class_command as _is_class_command,
    is_module_command as _is_module_command,
)
from ..args._naming import _command_name as _command_name

from ._parser import _apply_app_config_layers, _deregister_subparser
from ._register import _register_class_command, _register_module_command
from ._resolve import _full_names


def _register_commands(
    root: type | None,
    resolved_commands: list[_Command],
    parser: _argparse.ArgumentParser,
    base_parser: _argparse.ArgumentParser,
    root_cls: type,
    prepass_args: object,
    cmds_path_overridden: set[str],
    inherited_config_hint: bool = False,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
) -> tuple[
    _argparse._SubParsersAction, dict[str, tuple[str, object]], list[tuple[int, str]]
]:
    """Register every resolved command on ``parser`` and resolve collisions.

    Returns ``(subparsers, registry, notices)``. ``registry`` (primary names
    only) feeds :func:`_apply_app_config_layers`; ``notices`` are override and
    collision log records for :func:`app` to flush once logging is configured.
    ``inherited_config_hint`` is forwarded to :func:`_register_class_command`.

    ``on_error(command, exc)``, when given, is called for an exception raised
    while building one command's parser: returning drops that command, raising
    aborts.
    """
    notices: list[tuple[int, str]] = []

    # One registry keeps registration and dispatch in agreement. A name registered
    # twice warns naming both and the LAST wins: the earlier subparser is
    # deregistered, or argparse raises `conflicting subparser`.
    # `registry` is keyed by primary names; `claimed` also carries class-command
    # aliases so the collision check catches an alias clash.
    registry: dict[str, tuple[str, object]] = {}
    claimed: dict[str, tuple[str, object]] = {}

    # A root with `_subcommands_` already created a subparsers action, and argparse
    # allows one per parser: reuse it so CMDS_PATH can add to built-ins.
    subparsers = _parsers.find_subparsers(parser)
    if subparsers is None:
        # Private dest, like `Args._parser_`, so a root field named `command` is
        # not overwritten. Dispatch does not read it; argparse uses it to enforce
        # "a subcommand is required".
        subparsers = parser.add_subparsers(
            title="command",
            dest="_duho_command_",
            required=bool(resolved_commands),
        )
    else:
        # Skip a resolved command already registered by the root's `_parser_` and
        # identical: re-registering would drop its `"#cls"` selection hook.
        preregistered = set(subparsers._name_parser_map)  # type: ignore[attr-defined]
        builtin_by_name = {
            _command_name(c): c
            for c in (getattr(root, "_subcommands_", []) or [])
            if _command_name(c)
        }
        resolved_commands = [
            c
            for c in resolved_commands
            if not (
                _command_name(c) in preregistered
                and builtin_by_name.get(_command_name(c)) is c
            )
        ]
        # Seed `registry` with the pre-registered builtins so an override of one
        # reaches `_deregister_subparser`; otherwise `add_parser` raises
        # `conflicting subparser`.
        for builtin_name, builtin_command in builtin_by_name.items():
            if builtin_name in preregistered:
                registry[builtin_name] = ("class", builtin_command)
                for n in _full_names(builtin_command, builtin_name, "class"):
                    if n in preregistered:
                        claimed[n] = ("class", builtin_command)
    for command in resolved_commands:
        if _is_class_command(command):
            cmd_name = _command_name(command)
            kind: str = "class"
        elif _is_module_command(command):
            cmd_name = _ty.cast(_ModuleCommand, command)._parsername_
            kind = "module"
        else:
            # `commands=`/`source=` can hand over anything; dropping a non-command
            # silently leaves argparse's bare "invalid choice" with no hint why.
            raise TypeError(
                f"app(): expected a Cmd subclass or a discovered ModuleCommand, "
                f"got {command!r} ({type(command).__name__})"
            )

        names = _full_names(command, cmd_name, kind)
        colliding: dict[int, tuple[str, object]] = {}
        for n in names:
            prev = claimed.get(n)
            if prev is not None:
                colliding.setdefault(id(prev[1]), prev)

        for prev_kind, prev_obj in colliding.values():
            prev_name = _command_name(prev_obj)
            if cmd_name not in cmds_path_overridden:
                # A CMDS_PATH override is reported separately via
                # `cmds_path_overridden`; this is a collision between two sources.
                notices.append(
                    (
                        _logging.WARNING,
                        "command name %r registered by more than one source "
                        "(%s %r, then %s %r); the last registration wins."
                        % (
                            prev_name,
                            prev_kind,
                            getattr(prev_obj, "__name__", prev_obj),
                            kind,
                            getattr(command, "__name__", command),
                        ),
                    )
                )
            _deregister_subparser(subparsers, prev_name)
            for n in list(registry):
                if registry[n][1] is prev_obj:
                    del registry[n]
            for n in list(claimed):
                if claimed[n][1] is prev_obj:
                    del claimed[n]

        try:
            if kind == "class":
                command_cls = _ty.cast(type, command)
                child_parser = _register_class_command(
                    subparsers,
                    command_cls,
                    base_parser,
                    inherited_config_hint=inherited_config_hint,
                )
                # Lets provenance merge upward once selected, as in `Args._parser_`.
                child_parser._duho_parent_parser_ = parser  # type: ignore[attr-defined]
            else:
                _register_module_command(
                    subparsers,
                    _ty.cast(_ModuleCommand, command),
                    base_parser,
                    prepass_args,
                    root_cls,
                )
        except (Exception, SystemExit) as exc:
            if on_error is None:
                raise
            on_error(command, exc)
            # Drop whatever the failed build left registered under its names.
            _deregister_subparser(subparsers, cmd_name)
            continue
        registry[cmd_name] = (kind, command)
        for n in names:
            claimed[n] = (kind, command)

    # Without a metavar argparse's error text shows the private `_duho_command_`
    # dest; sorted primary names keep the message deterministic.
    subparsers.metavar = "{" + ",".join(sorted(registry)) + "}"

    return subparsers, registry, notices


def _finalize_command_tree(
    parser: _argparse.ArgumentParser,
    subparsers: _argparse._SubParsersAction,
    root_cls: type,
    registry: dict[str, tuple[str, object]],
    raw_config: dict,
) -> list[_argparse.Action]:
    """Suppress inherited root defaults and thread config/env layers down.

    Returns the root's required-global actions, un-required here so a value
    given after the subcommand, or from config/env, is not rejected; :func:`app`
    re-checks them against the parsed instance.
    """
    from .. import formatters as _formatters

    # Keep the root's optional dests from being clobbered by a child's inherited
    # default (an option given before the subcommand, or from config/env).
    root_builders = {b.name: b for b in root_cls._getargs_()}  # type: ignore[attr-defined]
    root_dests = set(root_builders)
    # Effective defaults let a child's redeclared default survive suppression.
    root_defaults = {n: b._effective_default_() for n, b in root_builders.items()}
    # `parents=[base_parser]` shares the root's Action objects with every child,
    # so the root keeps its own `required=True` copy, which a flag given after
    # the subcommand never satisfies. Un-require it and enforce after parsing.
    required_root_actions = [
        a
        for a in parser._actions
        if a.dest in root_dests and a.option_strings and getattr(a, "required", False)
    ]
    for action in required_root_actions:
        action.required = False
        # Keeps usage and `--help` rendering it as required despite the above.
        action._duho_display_required_ = True  # type: ignore[attr-defined]
    _formatters._install_required_usage_formatter(parser)
    for sub_parser in (subparsers.choices or {}).values():
        # A class command's subparser shares the root's Action objects via
        # `parents=[base_parser]`; a default it redeclares goes on a private
        # copy so no sibling, and not the root, sees it.
        command_cls = getattr(sub_parser, "_duho_cls_", None)
        if command_cls is not None and command_cls is not root_cls:
            for b in command_cls._getargs_():
                if b.name in root_defaults:
                    value = b._effective_default_()
                    if value != root_defaults[b.name]:
                        _set_private_default(sub_parser, b.name, value)
        _suppress_inherited_defaults(sub_parser, root_dests, root_defaults)
        # The inherited copy of a required global must not be re-required by the
        # child (`_suppress_inherited_defaults` skips required actions): the
        # root enforces it, so its value flows through.
        for action in sub_parser._actions:
            if (
                action.dest in root_dests
                and action.option_strings
                and getattr(action, "required", False)
            ):
                action.required = False
                action.default = _argparse.SUPPRESS
                action._duho_display_required_ = True  # type: ignore[attr-defined]
        _formatters._install_required_usage_formatter(sub_parser)
        # A SUPPRESS default makes argparse drop `default` from its help format
        # params, so a root help text with `%(default)s` raised `KeyError` in any
        # subcommand's `-h`. Stash the root's class default for the help
        # redaction (`_redact_action_defaults`) to substitute back while rendering.
        for action in sub_parser._actions:
            if action.dest in root_dests and action.default is _argparse.SUPPRESS:
                action._duho_class_default_ = root_defaults.get(  # type: ignore[attr-defined]
                    action.dest
                )
                action._duho_default_source_ = None  # type: ignore[attr-defined]

    # app() resolves commands outside `root._subcommands_`, so env/config
    # defaults are layered against the parsers built here.
    _apply_app_config_layers(root_cls, subparsers, registry, raw_config)

    return required_root_actions
