from __future__ import annotations

import argparse as _argparse
import contextlib as _contextlib
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .. import logging as _duho_logging
from ..args import Args as _Args
from .._layers import (
    _apply_default_layers_one as _apply_default_layers_one,
    _resolve_config_or_error as _resolve_config_or_error,
    _stash_layer_state as _stash_layer_state,
)
from ..discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    is_module_command as _is_module_command,
)

from ._register import _module_args_cls

_LOGGER = _logging.getLogger(__package__)


def _build_parser(
    root: type | None,
    name: str | None,
    description: str | None,
    config: str | _Path | None = None,
) -> tuple[_argparse.ArgumentParser, _argparse.ArgumentParser, type]:
    """Build the top-level parser and a help-free base parser for ``root``.

    Returns ``(parser, base_parser, root_cls)``; ``root=None`` yields a bare
    ``Args`` root. ``config`` is passed on as ``_inherited_config_hint_`` though
    applied later: a bool field needs the reversible ``--no-*`` form, since
    ``store_true`` cannot undo a config ``True``. The base parser has
    ``add_help=False`` and is each subcommand's ``parents=``, whose own ``-h``
    would otherwise collide.
    """
    root_cls = root if root is not None else _Args
    parser_kwargs: dict[str, object] = {}
    if name is not None:
        parser_kwargs["name"] = name
    if description is not None:
        parser_kwargs["description"] = description
    elif root is None:
        # `root_cls` is duho's own `Args`; without an explicit empty description
        # `_parser_` would use its docstring as the app's `--help` description.
        parser_kwargs["description"] = ""
    has_config = config is not None
    parser = root_cls._parser_(  # type: ignore[attr-defined]
        **parser_kwargs, _inherited_config_hint_=has_config
    )
    # Donates the root's options via `parents=`. Built without `_subcommands_`:
    # inheriting a subparsers action would nest the command tree under every
    # subcommand and make `command` required again.
    base_parser = root_cls._parser_(  # type: ignore[attr-defined]
        add_help=False, _inherited_config_hint_=has_config, _skip_subcommands_=True
    )
    return parser, base_parser, root_cls


def _deregister_subparser(subparsers: _argparse._SubParsersAction, name: str) -> None:
    """Remove subparser ``name`` and every alias of the same subparser.

    ``add_parser`` raises ``conflicting subparser`` on a duplicate, so an
    override must remove the loser first. Aliases are extra keys for the same
    parser object, so every key whose value is it is deleted.
    """
    name_parser_map = subparsers._name_parser_map  # type: ignore[attr-defined]
    parser_obj = name_parser_map.get(name)
    if parser_obj is None:
        return
    dropped = [n for n, p in name_parser_map.items() if p is parser_obj]
    for n in dropped:
        name_parser_map.pop(n, None)
    subparsers._choices_actions = [  # type: ignore[attr-defined]
        a
        for a in subparsers._choices_actions  # type: ignore[attr-defined]
        if getattr(a, "dest", None) not in dropped
    ]


def _defer_module_layers(
    sub_parser: _argparse.ArgumentParser,
    args_cls: type,
    table: dict,
    agenthelp,
) -> None:
    """Apply a module command's env/config layers when its own parser parses.

    A bad value then fails only the command that declares it (usage error,
    exit 2), not every invocation, ``--help`` included. A first, forgiving
    pass at registration keeps root-level help/agent-help provenance notes.
    """
    try:
        _apply_default_layers_one(sub_parser, args_cls, table)
    except ValueError:
        pass
    agenthelp._stash_default_provenance(sub_parser, cls=args_cls)
    original = sub_parser.parse_known_args

    def parse_known_args(args=None, namespace=None):
        try:
            _apply_default_layers_one(sub_parser, args_cls, table)
        except ValueError as exc:
            sub_parser.error(str(exc))
        agenthelp._stash_default_provenance(sub_parser, cls=args_cls)
        return original(args, namespace)

    sub_parser.parse_known_args = parse_known_args  # type: ignore[method-assign]


def _apply_app_config_layers(
    root_cls: type,
    subparsers: _argparse._SubParsersAction,
    registry: dict[str, tuple[str, object]],
    raw_config: dict,
) -> None:
    """Thread env/config-file defaults down a ``Cli`` app's command tree.

    ``app`` builds subparsers from resolved sources, outside the static
    ``_subcommands_`` tree that ``args._apply_layers`` reaches. Class commands are
    stashed the normal lazy way; a module command with ``args_cls`` has a bare
    subparser, so :func:`_defer_module_layers` wraps its ``parse_known_args``.
    Module fields get the same value-free redaction as class commands. A bad
    value becomes that subcommand's ``parser.error()`` (exit 2), never a
    traceback. ``raw_config`` is the TOML table ``app`` already loaded.
    """
    from .. import agenthelp as _agenthelp

    choices = subparsers.choices or {}
    for name, (kind, command) in registry.items():
        sub_parser = choices.get(name)
        if sub_parser is None:
            continue
        sub_table = raw_config.get(name) if raw_config else None
        sub_table = sub_table if isinstance(sub_table, dict) else {}
        if kind == "class":
            _stash_layer_state(sub_parser, command, sub_table)
            continue
        args_cls = _module_args_cls(_ty.cast(_ModuleCommand, command), root_cls)
        if args_cls is not None:
            _defer_module_layers(sub_parser, args_cls, sub_table, _agenthelp)
        # Every module command's `-h`, with or without its own `Args`, needs the
        # redaction: its plain subparser never gets `_AgentHelpAction`, so help
        # would render `%(default)s` from a live env/config value or raise
        # `KeyError` for a root default stashed by `_finalize_command_tree`.
        _agenthelp._install_help_redaction(sub_parser)


@_contextlib.contextmanager
def _unrequired_options(parser: _argparse.ArgumentParser, root_cls: type):
    """Treat the root's required options as optional for the duration.

    Lets the advisory prepass parse (and so hand a ``register`` hook an
    instance) when a required global is missing; the real parse reports it.
    """
    root_dests = {b.name for b in root_cls._getargs_()}  # type: ignore[attr-defined]
    saved = [
        a
        for a in parser._actions
        if a.dest in root_dests and a.option_strings and getattr(a, "required", False)
    ]
    for action in saved:
        action.required = False
    try:
        yield
    finally:
        for action in saved:
            action.required = True


def _prepare_app_parser(
    root: type | None,
    name: str | None,
    description: str | None,
    config: str | _Path | None,
    argv: _ty.Sequence[str] | None,
    resolved_commands: list[_Command],
) -> tuple[_argparse.ArgumentParser, _argparse.ArgumentParser, type, dict, object]:
    """Build :func:`app`'s top-level parser and run its advisory prepass.

    Returns ``(parser, base_parser, root_cls, raw_config, prepass_args)``. All of
    it happens before any command is registered; the prepass runs only when a
    resolved command is a module command.
    """
    parser, base_parser, root_cls = _build_parser(root, name, description, config)

    # Resolve the config table once and stash the root's slice before the
    # prepass, so a required global supplied by config/env does not hard-exit.
    # `_apply_app_config_layers` re-stashes it (idempotent) after registration.
    raw_config: dict = _resolve_config_or_error(parser, root_cls, config)
    _stash_layer_state(parser, root_cls, raw_config)

    # Offers module `register` hooks the parsed globals, best effort.
    # `prerun_parse` detaches the subparsers action for the call (otherwise it
    # re-enters this parser and double-pops the `#cls` marker, a `KeyError`) and
    # `quiet=True` silences every error and terminal action; the real parse
    # reports them once.
    prepass_args: object = None
    if any(_is_module_command(c) for c in resolved_commands):
        try:
            from ..parsers import prerun_parse as _prerun_parse

            with _unrequired_options(parser, root_cls):
                prepass_args = _prerun_parse(parser, argv, quiet=True)
        except SystemExit:
            # A required- or unknown-arg error (raised silently, since
            # quiet=True) must not abort the whole app: hand hooks the root's
            # defaults and let the real parse below report it authoritatively.
            try:
                prepass_args = root_cls()
            except Exception:
                prepass_args = None
        except Exception:
            # Fully swallowed by design, which also hides a genuinely broken
            # parser from the author; DUHO_TRACEBACK=1 surfaces it at DEBUG.
            _duho_logging.log_exception(
                _LOGGER,
                "advisory register prepass raised; continuing without it",
                level=_logging.DEBUG,
            )
            prepass_args = None

    return parser, base_parser, root_cls, raw_config, prepass_args
