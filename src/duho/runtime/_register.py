from __future__ import annotations

import argparse as _argparse
import inspect as _inspect
import typing as _ty

from ..args._argsclass import _add_fields as _add_fields
from ..args._helptext import (
    _escape_description as _escape_description,
    _escape_help as _escape_help,
)
from ..args._parserfix import (
    _keep_attached_double_dash as _keep_attached_double_dash,
    _patch_parser_for_reorder as _patch_parser_for_reorder,
)
from ..discovery import ModuleCommand as _ModuleCommand
from ..discovery._command import _noop as _discovery_noop
from ._arity import accepts_positional as _accepts_positional


def _register_class_command(
    subparsers: _argparse._SubParsersAction,
    command: type,
    base_parser: _argparse.ArgumentParser,
    *,
    inherited_config_hint: bool = False,
) -> _argparse.ArgumentParser:
    """Register a class command under ``subparsers`` with parent-arg inheritance.

    Delegates to the class's ``_parser_(subparsers, parents=[base_parser])`` and
    returns the subparser. ``inherited_config_hint`` (``app()``'s ``config is not
    None``) is forwarded as ``_inherited_config_hint_``: a dynamically resolved
    command is outside ``root._subcommands_`` and would otherwise get the
    reversible ``--no-*`` bool form only if its own class declares ``_config_``.
    """
    return command._parser_(  # type: ignore[attr-defined]
        subparsers,
        parents=[base_parser],
        _inherited_config_hint_=inherited_config_hint,
    )


def _wants_logger_arg(register: _ty.Callable[..., object]) -> bool:
    """True if a module ``register`` hook accepts a resolved ``logger``.

    That is: three or more positional parameters, a ``*args`` catch-all, or a
    keyword-only ``logger``. An un-introspectable hook gets the 2-arg call.
    The signature is read with ``follow_wrapped=False`` so a ``functools.wraps``
    wrapper is judged on its own parameters.
    """
    try:
        params = _inspect.signature(register, follow_wrapped=False).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return False
    logger_param = params.get("logger")
    if (
        logger_param is not None
        and logger_param.kind is _inspect.Parameter.KEYWORD_ONLY
    ):
        return True
    return _accepts_positional(register, 3)


def _wants_logger_by_keyword(register: _ty.Callable[..., object]) -> bool:
    """True if ``register``'s logger must be passed as ``logger=...``.

    A keyword-only ``logger`` cannot be supplied positionally. Called only after
    :func:`_wants_logger_arg` confirmed a logger slot exists.
    """
    try:
        params = _inspect.signature(register, follow_wrapped=False).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return False
    logger_param = params.get("logger")
    return (
        logger_param is not None
        and logger_param.kind is _inspect.Parameter.KEYWORD_ONLY
    )


def _conflicting_option_strings(exc: _argparse.ArgumentError) -> list[str]:
    """The option string(s) argparse reports as conflicting, or ``[]``.

    Parsed from ``exc.message`` ("conflicting option string(s): ..."), since
    ``str(exc)`` prefixes the name of the new action. ``[]`` means a different
    ``ArgumentError``.
    """
    message = getattr(exc, "message", "") or ""
    prefix, sep, tail = message.partition("conflicting option string")
    if not sep or prefix:
        return []
    _, _, tail = tail.partition(":")
    return [s.strip() for s in tail.split(",") if s.strip()]


def _module_args_cls(command: _ModuleCommand, root_cls: type) -> type | None:
    """The effective declarative ``Args`` class for a module command, or ``None``.

    A class already deriving from ``root_cls`` is used as is; otherwise one is
    synthesized as ``type("_Args", (command.args_cls, root_cls), {})`` so the
    parsed instance carries the app's shared root fields and methods.
    """
    args_cls = command.args_cls
    if args_cls is None:
        return None
    if issubclass(args_cls, root_cls):
        return args_cls
    # Seed `_duho_constants_` as `Cmd`/`Cli` do: `type()` sets `__module__` to
    # `duho.runtime`, and `_class_constants` would otherwise AST-parse that
    # source for a `_Args` ClassDef that does not exist (a cold-start cost).
    return type("_Args", (args_cls, root_cls), {"_duho_constants_": {}})


def _add_module_declared_fields(
    parser: _argparse.ArgumentParser, args_cls: type
) -> None:
    """Add ``args_cls``'s declared fields directly to ``parser``.

    Skips the ``"#cls"`` dispatch patching, so the parsed instance stays the root
    instance. ``strict=False`` skips a dest the parser already has instead of
    raising, so a field never conflicts with an inherited global.
    """
    _add_fields(parser, args_cls, strict=False)


def _register_module_command(
    subparsers: _argparse._SubParsersAction,
    command: _ModuleCommand,
    base_parser: _argparse.ArgumentParser,
    root_instance_args: object,
    root_cls: type,
) -> None:
    """Register a module command as a subparser and run its ``register`` hook.

    Fields of the module's own ``Args`` (see :func:`_module_args_cls`) are added
    first, then ``register`` runs, so a shared positional added last by a hook
    still lands after the declared ones. The hook is gated and introspected on
    ``command.register`` itself, so a caller's wrapper is honored; ``_noop``
    means no hook. Forms: ``register(parser, args)``, where ``args`` is the
    best-effort prepass instance, and ``register(parser, args, logger)``, where
    ``logger`` is ``command._logger_for(args)``. A ``*args`` hook is the 3-arg form.
    """
    # `command.help`/`description` already derive from `module.__doc__`.
    # `help=` is always `%`-expanded by argparse (3.14 crashes registration of
    # every command on a stray `%`), so escape it; `description=` is formatted
    # only when it holds `%(prog)`, so escaping it would show a doubled `%`.
    parser = subparsers.add_parser(
        command._parsername_,
        parents=[base_parser],
        help=_escape_help(command.help),
        description=_escape_description(command.description),
        add_help=True,
    )
    # A private per-parser dest, not the shared `_duho_command_` one, which nested
    # `_subcommands_` trees and a root field named `command` also use; it is
    # applied only when this subparser is selected.
    parser.set_defaults(_duho_module_command_=command)

    # Dests present before this command's own fields go on (inherited globals,
    # `-h`); the difference is the command's own, for `duho.mcp`. A declared field
    # colliding with a global is skipped by `_add_fields(strict=False)`.
    dests_before = {a.dest for a in parser._actions}

    args_cls = _module_args_cls(command, root_cls)
    if args_cls is not None:
        _add_module_declared_fields(parser, args_cls)

    register = getattr(command, "register", None)
    # Gate and introspect `command.register`, the object that is called, not a
    # re-fetch from the module: a caller's wrapper may differ in arity, or be
    # the only hook. `_noop` is a shared singleton, so identity means "no hook".
    if callable(register) and register is not _discovery_noop:
        try:
            if _wants_logger_arg(register):
                # Same fallback as the rest of the module-command lifecycle.
                logger = command._logger_for(root_instance_args)
                if _wants_logger_by_keyword(register):
                    # A keyword-only `logger` cannot be passed positionally.
                    register(parser, root_instance_args, logger=logger)
                else:
                    register(parser, root_instance_args, logger)
            else:
                register(parser, root_instance_args)
        except _argparse.ArgumentError as exc:
            # argparse does not say the clash is with an inherited global. Blame a
            # global only when a conflicting string is the root's; a hook clashing
            # with its own field would otherwise send the author to the wrong place.
            conflicting = _conflicting_option_strings(exc)
            global_options = getattr(base_parser, "_option_string_actions", {})
            if conflicting and any(opt in global_options for opt in conflicting):
                raise _argparse.ArgumentError(
                    None,
                    f"command {command._parsername_!r}: its register() hook added "
                    f"an option that collides with a global flag inherited from "
                    f"the app root ({exc}). Every subcommand parser inherits the "
                    f"root's global options (e.g. -h, -v, -q, --version); pick a "
                    f"different flag in register().",
                ) from exc
            raise _argparse.ArgumentError(
                None, f"command {command._parsername_!r}: {exc}"
            ) from exc

    # For `duho.mcp`: the resolved `Args` class (``None`` without one) and the
    # dests this registration added, excluding inherited globals and `-h`.
    parser._duho_module_args_cls_ = args_cls  # type: ignore[attr-defined]
    parser._duho_module_own_dests_ = {  # type: ignore[attr-defined]
        a.dest for a in parser._actions
    } - dests_before

    # A plain `add_parser()` subparser never got `Args._initparser_`'s
    # flag-between-positionals reorder fix; patch it once all fields are in place.
    _patch_parser_for_reorder(parser)
    _keep_attached_double_dash(parser)
