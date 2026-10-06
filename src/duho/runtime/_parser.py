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

    Returns ``(parser, base_parser, root_cls)``. ``root`` may be any ``Cmd``/
    ``Args``/``LoggingArgs`` subclass supplying global options; ``None`` yields a
    bare data ``Args`` root so an app with only external commands still works.
    ``name`` / ``description`` override the parser prog / description when given.

    ``config`` -- ``app()``'s own ``config=`` kwarg -- is passed through as a
    hint (`_inherited_config_hint_`) even though it is applied to the parser
    LATER, by `_apply_app_config_layers`: a root class declares no `_config_`
    of its own still needs to know a config table is coming, so a layered
    bool field gets the reversible `--no-*` form instead of a bare
    ``store_true`` that can never turn a config-supplied ``True`` back off.

    The **base parser** carries the same global options but is built with
    ``add_help=False``. It is the one used as ``parents=`` for each subcommand:
    inheriting a parser that itself owns ``-h/--help`` would collide with the
    subparser's own auto-added help action (argparse ``conflicting option
    strings: -h``). The top-level ``parser`` keeps its own help; the base parser
    (help-suppressed) just donates the root's non-help options downward.
    """
    root_cls = root if root is not None else _Args
    parser_kwargs: dict[str, object] = {}
    if name is not None:
        parser_kwargs["name"] = name
    if description is not None:
        parser_kwargs["description"] = description
    elif root is None:
        # `root_cls` here is duho's OWN bare `Args` framework class (an app
        # with no root, only discovered/explicit `commands=`), never
        # something the user wrote -- `_parser_`'s `kwargs.setdefault
        # ("description", cls.__doc__)` would otherwise leak `Args`'s OWN
        # docstring (the framework's internal field-declaration contract) as
        # this app's top-level `--help` description. Passing an explicit
        # empty description here (rather than leaving it unset) pre-empts
        # that `setdefault` for exactly this synthesized-root case, while a
        # real user-supplied `root` class keeps using its own docstring as
        # before.
        parser_kwargs["description"] = ""
    has_config = config is not None
    parser = root_cls._parser_(  # type: ignore[attr-defined]
        **parser_kwargs, _inherited_config_hint_=has_config
    )
    # base_parser exists only to donate the root's *options* to each subcommand
    # via `parents=`. Inheriting a subparsers action would nest the whole command
    # tree under every subcommand and make its `command` argument required
    # again, so it is built without the root's `_subcommands_`.
    base_parser = root_cls._parser_(  # type: ignore[attr-defined]
        add_help=False, _inherited_config_hint_=has_config, _skip_subcommands_=True
    )
    return parser, base_parser, root_cls


def _deregister_subparser(subparsers: _argparse._SubParsersAction, name: str) -> None:
    """Remove a registered subparser ``name``, and every alias of
    the SAME subparser, from ``subparsers``.

    argparse's ``add_parser`` raises ``ArgumentError('conflicting subparser')``
    (or, for an alias specifically, ``'conflicting subparser alias'`` on
    3.11+) on a duplicate name/alias, so a later registration under the same
    name cannot simply overwrite an earlier one. argparse registers a
    class command's aliases (``_parseraliases_``) as EXTRA keys in
    ``_name_parser_map`` pointing at the very same subparser object as its
    primary name -- so an override that only popped ``name`` left every alias
    of the LOSING command still dispatching to it. Deleting every key
    whose value ``is`` that same parser object removes the primary name AND
    every alias in one pass, whatever they're named, without this function
    needing to know the losing command's own alias list.
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

    ``duho.main``/``duho.parse``/``duho.parse_globals`` route through
    ``args._apply_layers``, which stashes a class's own (and, recursively,
    every STATICALLY declared ``_subcommands_`` descendant's own) config-table
    slice on its parser, deferring actual conversion to that parser's own
    ``_initparser_``-patched ``parse_known_args``. ``app``
    registers commands from precedence-resolved sources instead of a static
    tree, so its top-level subcommand parsers are not reachable that way --
    this re-stashes against the parsers ``app`` actually built:

    * a **class command** (and, via that SAME recursive stash, any of ITS OWN
      nested ``_subcommands_``) is threaded the normal lazy way,
      since its subparser IS built through ``_parser_``/``_initparser_``
      (``_register_class_command`` already links it to the app root via
      ``_duho_parent_parser_``, so its provenance merges upward too);
    * a **module command** with a declared ``args_cls`` (a
      module command may declare a module-level ``Args`` class) has NO
      ``_initparser_`` hook at all (its subparser is a deliberately bare
      stdlib one -- see this module's own docstring), so its table is applied by
      a wrapper on that subparser's own ``parse_known_args``, i.e. only when
      that command is the one parsing (see :func:`_defer_module_layers`).

    A module command's own env/config-bound field gets the SAME "never show
    the live value" redaction a class command's does:
    ``duho.agenthelp._stash_default_provenance`` snapshots the class default
    (and, when applicable, a value-free provenance note) onto each action,
    passed this command's OWN ``args_cls`` explicitly because the subparser
    deliberately has no ``_duho_cls_`` (``duho.mcp`` reads that to decide
    whether a node is callable). ``duho.agenthelp`` is imported lazily.

    **A bad env/config value never raises a raw traceback.**
    ``_apply_default_layers_one`` raises ``ValueError`` `from None` (never
    chaining the conversion error, which may echo a secret); the wrapper
    reports it through this subcommand's own ``parser.error()`` (usage text,
    exit 2), so it fails only the command that declares the value.

    ``raw_config`` is the already-loaded TOML table (``app`` loads it once so
    the root layering can also run before the advisory prepass).
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
        # Every module command's `-h` -- whether or not it declares its own
        # `Args` -- gets this protection, not only one whose own field was
        # just laid on above: a module command's subparser is a plain
        # `add_parser()` instance with its own ordinary argparse `-h`/
        # `--help` action -- it never goes through `duho.args`'s
        # `_install_agent_help`/`_AgentHelpAction` (this command
        # deliberately has no `_duho_cls_` of its own; see this function's
        # own docstring) -- so without this its help text would render a
        # literal `%(default)s` straight from a live env/config value staged
        # above, OR raise `KeyError` for a root-inherited option whose class
        # default `_finalize_command_tree` already stashed onto it (see
        # there), exactly like an unprotected class command's `-h` would.
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

    Returns ``(parser, base_parser, root_cls, raw_config, prepass_args)``.
    Everything here happens BEFORE any command is actually registered:
    building the parser pair (:func:`_build_parser`), resolving and stashing
    the root's own config-layer slice, and -- only when at least one resolved
    command is a module command -- running the best-effort prepass that
    offers a module ``register`` hook the already-parsed globals. Split out
    of :func:`app`; no behavior change, the full suite is the guard.
    """
    parser, base_parser, root_cls = _build_parser(root, name, description, config)

    # Resolve the config table ONCE (a not-yet-created class-level
    # `_config_` is skipped, not a crash) and stash the root's own slice on
    # `parser` up front, BEFORE the advisory prepass. Actual conversion is
    # deferred to `parser`'s own `_initparser_`-patched `parse_known_args`,
    # which the prepass below already triggers -- so a
    # required global supplied by config/env still reaches it and does not
    # hard-exit with a usage error. `_apply_app_config_layers` (called
    # after registration) re-stashes it (idempotent) alongside each command's
    # own table.
    raw_config: dict = _resolve_config_or_error(parser, root_cls, config)
    _stash_layer_state(parser, root_cls, raw_config)

    # A prepass parsed root instance is offered to module ``register`` hooks so a
    # hook that wants the already-parsed globals can read them. It is a
    # best-effort prepass: `prerun_parse` detaches `parser`'s subparsers action
    # for the call (restoring it before returning, so registration below still
    # sees it) -- which is what makes this safe to run even when `root` already
    # has built-in `_subcommands_` (otherwise the relaxed subparsers action
    # re-enters this same parser's own patched parse_known_args and
    # double-pops the selection marker, a KeyError('#cls')) -- and
    # `quiet=True` so a required/unknown-arg error, and every terminal action
    # (--version, --print-completion, --help-agents, -h/--help), stays fully
    # silent here; the real parse below is what actually reports/prints,
    # exactly once. Most register hooks ignore the parsed globals
    # entirely and just add static args.
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
