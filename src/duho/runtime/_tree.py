import argparse as _argparse
import logging as _logging
import typing as _ty
from .. import parsers as _parsers
from ..args import _suppress_inherited_defaults as _suppress_inherited_defaults
from ..discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    _command_name as _command_name,
    is_class_command as _is_class_command,
    is_module_command as _is_module_command,
)

from ._parser import _apply_app_config_layers, _deregister_subparser
from ._register import _register_class_command, _register_module_command
from ._resolve import _full_names


def _register_commands(
    root: "type | None",
    resolved_commands: "list[_Command]",
    parser: "_argparse.ArgumentParser",
    base_parser: "_argparse.ArgumentParser",
    root_cls: type,
    prepass_args: object,
    cmds_path_overridden: "set[str]",
    inherited_config_hint: bool = False,
) -> "tuple[_argparse._SubParsersAction, dict[str, tuple[str, object]], list[tuple[int, str]]]":
    """Register every resolved command on ``parser`` and resolve collisions.

    Returns ``(subparsers, registry, notices)``. ``registry`` (PRIMARY names
    only) is later consumed by :func:`_apply_app_config_layers`; ``notices``
    collects override/collision log records for :func:`app` to flush once
    logging is actually configured. Split out of :func:`app`;
    no behavior change, the full suite is the guard.

    ``inherited_config_hint`` is ``app()``'s own ``config is not None``,
    forwarded to :func:`_register_class_command` for every dynamically
    resolved class command (``commands=``/``source=``/CMDS_PATH) -- see that
    function's docstring for why a command resolved this way needs it too,
    not just one reachable via a root's static ``_subcommands_`` tree.
    """
    notices: "list[tuple[int, str]]" = []

    # Map each subcommand name to (kind, command) in ONE registry so registration
    # and dispatch agree. A name registered twice (e.g. a module command and a
    # class command sharing a name) warns naming both; the LAST registration wins
    # -- the earlier subparser is deregistered so argparse does not raise
    # `conflicting subparser`, and dispatch resolves via this same registry.
    # `registry` stays keyed by PRIMARY names only (its shape `_apply_app_config_
    # layers` below relies on, one config-table lookup per canonical subcommand
    # name). `claimed` mirrors it but also carries every class command's
    # ALIASES (`_full_names`), so the collision check below catches an alias
    # clash too, not just a primary-name one.
    registry: "dict[str, tuple[str, object]]" = {}
    claimed: "dict[str, tuple[str, object]]" = {}

    # A root class with `_subcommands_` already had them registered by its own
    # `_parser_`, which created a subparsers action. argparse allows only one per
    # parser ("cannot have multiple subparser arguments"), so reuse that action
    # rather than adding a second -- otherwise a root with built-ins could not
    # also take discovered commands (CMDS_PATH being additive depends on this).
    # Re-registering a name is safe: `_deregister_subparser` drops the earlier
    # entry so the later one wins.
    subparsers = _parsers.find_subparsers(parser)
    if subparsers is None:
        # A private dest -- matches the one a class root's own
        # static `_subcommands_` tree uses (`Args._parser_`) -- so a root
        # field a user happens to name `command` is never silently
        # overwritten by subcommand selection. Dispatch below no longer reads
        # this dest at all (a module command is identified by its own
        # `_duho_module_command_` marker instead); it exists purely so
        # argparse can enforce "a subcommand is required".
        subparsers = parser.add_subparsers(
            title="command", dest="_duho_command_", required=True
        )
    else:
        # Names the root's own `_parser_` already wired up. Re-registering one
        # here would drop its `"#cls"` selection hook and break dispatch, so skip
        # any resolved command that is already present and identical -- only a
        # genuinely different command (a CMDS_PATH override) re-registers.
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
        # Seed `registry` with the root's own pre-registered builtins so the
        # collision-check loop below (keyed on `cmd_name in registry`) also
        # catches a genuinely DIFFERENT command overriding one of THESE names
        # -- not just a collision between two commands both resolved in the
        # loop itself. Without this, a CMDS_PATH override of a preregistered
        # builtin skips `_deregister_subparser` entirely (registry looked
        # empty for that name) and argparse's own `add_parser` raises
        # `conflicting subparser` when the loop tries to register the
        # override under the same, still-occupied name.
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
            # A provider/caller can hand `commands=`/`source=` anything;
            # silently dropping a non-command (behind a "can't happen" pragma
            # that coverage proved wrong) left the user staring at argparse's
            # bare "invalid choice ... (choose from )" with no hint why.
            raise TypeError(
                f"app(): expected a Cmd subclass or a discovered ModuleCommand, "
                f"got {command!r} ({type(command).__name__})"
            )

        names = _full_names(command, cmd_name, kind)
        colliding: "dict[int, tuple[str, object]]" = {}
        for n in names:
            prev = claimed.get(n)
            if prev is not None:
                colliding.setdefault(id(prev[1]), prev)

        for prev_kind, prev_obj in colliding.values():
            prev_name = _command_name(prev_obj)
            if cmd_name not in cmds_path_overridden:
                # Not the documented CMDS_PATH-overrides-a-base-command story
                # (that one is reported once via `cmds_path_overridden` below,
                # after logging is set up) -- a genuine, otherwise-silent
                # collision between two independently-resolved commands.
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

        if kind == "class":
            command_cls = _ty.cast(type, command)
            child_parser = _register_class_command(
                subparsers,
                command_cls,
                base_parser,
                inherited_config_hint=inherited_config_hint,
            )
            # Link this class command's own parser to the app root
            # so its (and, recursively, any of ITS OWN nested subcommands')
            # provenance merges upward once actually selected -- the same
            # mechanism the static `_subcommands_` tree gets in `Args._parser_`.
            child_parser._duho_parent_parser_ = parser  # type: ignore[attr-defined]
        else:
            _register_module_command(
                subparsers,
                _ty.cast(_ModuleCommand, command),
                base_parser,
                prepass_args,
                root_cls,
            )
        registry[cmd_name] = (kind, command)
        for n in names:
            claimed[n] = (kind, command)

    # Same rule `Args._parser_` applies to its own static `_subcommands_`
    # tree: without an explicit `metavar`, argparse falls back to the
    # ACTION'S DEST (never `choices`) for its "required"/"invalid choice"
    # ERROR text, leaking the private `_duho_command_` dest. Set it from the
    # FINAL registry (primary names only, sorted for a deterministic
    # message) now that every command -- static builtins and CMDS_PATH-
    # discovered alike -- is registered.
    subparsers.metavar = "{" + ",".join(sorted(registry)) + "}"

    return subparsers, registry, notices


def _finalize_command_tree(
    parser: "_argparse.ArgumentParser",
    subparsers: "_argparse._SubParsersAction",
    root_cls: type,
    registry: "dict[str, tuple[str, object]]",
    raw_config: dict,
) -> "list[_argparse.Action]":
    """Suppress inherited root defaults and thread config/env layers down.

    Returns ``required_root_actions`` -- the root's own required-global
    actions un-required here so a value given AFTER the subcommand, or
    supplied by config/env, is not rejected; :func:`app` re-checks these
    against the parsed instance once parsing is done. Split out of
    :func:`app`; no behavior change, the full suite is the guard.
    """
    from .. import formatters as _formatters

    # Suppress the root's own optional dests on every registered subparser so an
    # option given BEFORE the subcommand (or supplied by the root env/config
    # layer) is not clobbered by the child's inherited default. This is the
    # `app()` analogue of the suppression `Args._parser_` performs for a static
    # `_subcommands_` tree.
    root_builders = {b.name: b for b in root_cls._getargs_()}  # type: ignore[attr-defined]
    root_dests = set(root_builders)
    # Pass each root field's EFFECTIVE default so `_suppress_inherited_defaults`
    # keeps a child's DELIBERATELY redeclared default instead of suppressing
    # it back to the root's -- `Args._parser_` already does this for the static
    # `_subcommands_` tree; app()'s own call site had not.
    root_defaults = {n: b._effective_default_() for n, b in root_builders.items()}
    # A required global given AFTER the subcommand is otherwise rejected --
    # `parents=[base_parser]` copies the root's option ACTIONS onto every child
    # (shared objects, not copies), so un-requiring only the child's copy below
    # leaves the ROOT's own separate action (built when `parser` itself was
    # constructed) still `required=True`; that one is never "seen" when the flag
    # arrives in the subcommand's argv slice, so argparse reports it missing.
    # Un-require the root's own copies here and enforce presence AFTER the real
    # parse instead (root value, child value, or a config/env layer all count).
    required_root_actions = [
        a
        for a in parser._actions
        if a.dest in root_dests and a.option_strings and getattr(a, "required", False)
    ]
    for action in required_root_actions:
        action.required = False
        # Un-requiring the action for enforcement's sake also made argparse's
        # own usage renderer show it as `[--opt]` (optional) -- flag it so
        # `formatters.install_required_usage_formatter` (installed on
        # `parser` below) still renders it as required in `--help`/usage
        # text without re-enabling argparse's own (now redundant, and
        # differently timed) rejection.
        action._duho_display_required_ = True  # type: ignore[attr-defined]
    _formatters.install_required_usage_formatter(parser)
    for sub_parser in (subparsers.choices or {}).values():
        _suppress_inherited_defaults(sub_parser, root_dests, root_defaults)
        # `parents=[base_parser]` copies EVERY root option onto each subparser,
        # including *required* globals. `_suppress_inherited_defaults` skips
        # required actions (correct for the static tree, whose children don't
        # inherit root options as their own actions). Here the root parser owns
        # and enforces the required global; a child must not independently
        # re-require it (which would error even when it was given before the
        # subcommand or supplied by a config/env layer). Suppress + un-require
        # the child's inherited copy so the root's value flows through.
        for action in sub_parser._actions:
            if (
                action.dest in root_dests
                and action.option_strings
                and getattr(action, "required", False)
            ):
                action.required = False
                action.default = _argparse.SUPPRESS
                action._duho_display_required_ = True  # type: ignore[attr-defined]
        _formatters.install_required_usage_formatter(sub_parser)
        # A root-inherited option's default may now be `_argparse.SUPPRESS`
        # (set just above for a formerly-required global, or by
        # `_suppress_inherited_defaults` for an optional one) so the child's
        # absence of the flag defers to whatever the root/parent actually
        # parsed. But argparse's OWN raw `%(default)s` expansion
        # (`HelpFormatter._expand_help`) reads `action.default` DIRECTLY and
        # deletes the `default` key from its format params whenever it is
        # SUPPRESS -- so a root global's help text that spells the
        # placeholder literally (e.g. `"root %(default)s"`) raised
        # `KeyError('default')` rendering ANY subcommand's `-h` under
        # `app()`, even with no env/config involved (this is real argparse
        # `parents=` inheritance, unlike the static `_subcommands_` tree,
        # which never copies a parent's Actions at all -- see this
        # function's own docstring). Stash the ROOT's own class default
        # directly on the action so `duho.agenthelp`'s
        # `redact_action_defaults` -- already installed on every class
        # command's `-h` via `_AgentHelpAction`, and on every module
        # command's via `install_help_redaction` in
        # `_apply_app_config_layers` -- substitutes a real value back onto
        # `action.default` for the duration of the render. This dest is
        # never in the CHILD class's own `_getargs_()` (it belongs to the
        # root), so `stash_default_provenance`'s own builder-keyed stash
        # never reaches it and never overwrites what's set here.
        for action in sub_parser._actions:
            if action.dest in root_dests and action.default is _argparse.SUPPRESS:
                action._duho_class_default_ = root_defaults.get(  # type: ignore[attr-defined]
                    action.dest
                )
                action._duho_default_source_ = None  # type: ignore[attr-defined]
        # A `commands=`/`source=` class command's subparser
        # shares the root's Action OBJECTS via `parents=[base_parser]` --
        # `_suppress_inherited_defaults` correctly leaves a differing child
        # default alone, but the shared action's OWN `.default` still needs
        # setting to THAT child's value (a static `_subcommands_` child, built
        # with its own dedicated actions, already gets this for free above).
        command_cls = getattr(sub_parser, "_duho_cls_", None)
        if command_cls is not None and command_cls is not root_cls:
            child_defaults = {
                b.name: b._effective_default_() for b in command_cls._getargs_()
            }
            differing = {
                n: v
                for n, v in child_defaults.items()
                if n in root_defaults and v != root_defaults[n]
            }
            if differing:
                sub_parser.set_defaults(**differing)

    # Thread env/config-file defaults down the app's command tree (a Cli root's
    # `_config_`, or an explicit `config`, plus each command's NS(env=...)
    # fields). This is app()'s analogue of the `args._apply_layers` call that
    # `duho.main`/`duho.parse` make; app() resolves commands from sources that
    # aren't reachable via `root._subcommands_`, so it layers against the
    # parsers actually built here. See `_apply_app_config_layers`.
    _apply_app_config_layers(root_cls, subparsers, registry, raw_config)

    return required_root_actions
