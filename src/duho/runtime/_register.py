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

    Delegates to the class's own ``_parser_(subparsers, parents=[base_parser])``:
    this reuses the shipped registration path (which installs the ``"#cls"``
    deepest-selection ``parse_known_args`` on the subparser and recurses into any
    nested ``_subcommands_``), while ``parents=`` makes the root/global options
    appear on the subcommand too. Returns the built subparser so the caller can
    link it to the app's root (``_duho_parent_parser_``) for the
    lazy env/config layering and provenance-merge machinery in ``duho.args``.

    ``inherited_config_hint`` is ``app()``'s own ``config is not None``
    (whether a config FILE is coming, whatever ``command`` itself declares) --
    forwarded to ``_parser_`` as ``_inherited_config_hint_``, the SAME hint a
    STATIC ``_subcommands_`` tree already gets recursively from its root's own
    ``_parser_`` call. Without it, a ``commands=``/``source=``/CMDS_PATH
    class command (resolved by :func:`_resolve_commands`, never reachable via
    ``root._subcommands_``) only got the reversible ``--no-*`` spelling for a
    bool field when the command's OWN class happened to declare its own
    ``_config_`` -- never from the app-level ``config=``/env layering that
    threads down to it regardless (see ``_apply_app_config_layers``), so a
    config/env value that later flips such a field back to ``True`` had no
    CLI-side way to override it back to ``False``.
    """
    return command._parser_(  # type: ignore[attr-defined]
        subparsers,
        parents=[base_parser],
        _inherited_config_hint_=inherited_config_hint,
    )


def _wants_logger_arg(register: _ty.Callable[..., object]) -> bool:
    """True if a module ``register`` hook accepts a resolved ``logger``.

    A module's ``register`` may be written 2-arg ``(parser, args)``, 3-arg
    ``(parser, args, logger)``, or with a keyword-only ``logger`` (e.g.
    ``(parser, args, *, logger)``). This inspects the hook's signature and
    returns ``True`` when it accepts a logger via any of those shapes --
    three (or more) positional parameters, a ``*args`` catch-all (which can
    absorb a logger positionally), or a parameter literally named ``logger``
    of any kind (see :func:`_wants_logger_by_keyword` for which of these
    calling conventions to actually use). If the signature cannot be
    introspected (a builtin / C callable / anything ``inspect`` refuses), we
    conservatively default to ``False`` (the 2-arg call), which never
    over-supplies an argument the hook can't take.

    Inspects with ``follow_wrapped=False``: a hook wrapped with
    ``functools.wraps`` (e.g. a user decorator around ``register``) must be
    read on its OWN signature, not the wrapped function's -- otherwise the
    wrapper's own extra/different parameters are invisible and the wrong
    calling convention is chosen (``runpath._step_wants_ctx`` follows the
    same rule).
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

    A keyword-only ``logger`` parameter (``def register(parser, args, *,
    logger)``) cannot be supplied positionally -- doing so raises
    ``TypeError: register() takes 2 positional arguments but 3 were given``.
    Only called after :func:`_wants_logger_arg` has already confirmed a
    logger slot exists; this just decides how to pass it.
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
    """Extract the actual conflicting option string(s) from an argparse
    ``ArgumentError`` raised by ``_ActionsContainer._handle_conflict_error``.

    That error's message is always one of ``"conflicting option string: %s"``
    or ``"conflicting option strings: %s"`` (stdlib ``argparse``, stable
    across the supported Python range), where ``%s`` is a comma-joined list
    of the option string(s) that actually collided -- e.g. ``"-q"`` or ``"-h,
    --help"``. Parsed from ``exc.message`` (not ``str(exc)``, which prepends
    an unrelated ``argument ...:`` prefix naming the NEW action, not the
    option strings). Returns ``[]`` if the message doesn't match this shape
    (a different ``ArgumentError`` entirely -- callers should treat that as
    "unknown, not a global-flag collision").
    """
    message = getattr(exc, "message", "") or ""
    prefix, sep, tail = message.partition("conflicting option string")
    if not sep or prefix:
        return []
    _, _, tail = tail.partition(":")
    return [s.strip() for s in tail.split(",") if s.strip()]


def _module_args_cls(command: _ModuleCommand, root_cls: type) -> type | None:
    """Resolve the effective declarative ``Args`` class for a module command.

    ``command.args_cls`` (see :class:`duho.discovery.ModuleCommand`) is
    whatever the module declared, as-is: a real ``Args`` subclass, or a plain
    class. If it's ``None``, there's nothing to add. If it's ALREADY a
    subclass of ``root_cls``, it's used directly (the module explicitly
    based its own ``Args`` on the app's shared root, e.g. ``class
    Args(MyAppRoot): ...`` -- the common convention). Otherwise a class is
    synthesized on the fly, ``type("_Args", (command.args_cls, root_cls), {})``
    -- the module's own class first/overriding, ``root_cls`` as the base every
    subcommand already gets -- so the module's declared fields work AND its
    parsed instance still carries the app's shared root fields/methods (e.g.
    ``_expanded_targets_``), without requiring the module author to
    explicitly subclass the root themselves.
    """
    args_cls = command.args_cls
    if args_cls is None:
        return None
    if issubclass(args_cls, root_cls):
        return args_cls
    # Seed an empty `_duho_constants_` in the synthesized namespace, the
    # same reason `Cmd`/`Cli` (and `duho.command()`) seed one on
    # themselves. `type(...)` gives this class `__module__` = wherever `type`
    # was actually called from -- `duho.runtime` -- so without a seed,
    # `_class_constants` would AST-parse the `duho.runtime` source itself looking for a
    # `_Args` ClassDef that was never there, on every module command that
    # declares its own `Args` (a real, measured cold-start cost this class
    # has no source body to justify paying).
    return type("_Args", (args_cls, root_cls), {"_duho_constants_": {}})


def _add_module_declared_fields(
    parser: _argparse.ArgumentParser, args_cls: type
) -> None:
    """Add ``args_cls``'s own declared fields directly to ``parser``.

    Thin wrapper around the shared ``duho.args._add_fields`` WITHOUT
    installing ``_initparser_``'s ``"#cls"`` dispatch-patching -- a module
    command's parsed instance must stay the ROOT instance (the existing
    module-command contract), not get hijacked into constructing an instance
    of this synthesized/declared class. ``strict=False`` skips (rather than
    raises on) a dest already present on the parser -- whether inherited from
    the root or added by duho itself -- so a declared field never re-adds/
    conflicts with an inherited global, matching a module command's existing
    contract.

    A module command's declared fields support ``NS(conflicts=...)``/
    ``NS(group=...)`` the same as a class command's.
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

    The subparser inherits the root/global options via ``parents=[base_parser]``
    (parent-arg inheritance). If the module declares its own ``Args``
    (``command.args_cls``, see :func:`_module_args_cls`), that class's fields
    are added to the subparser FIRST -- declaratively, the same "annotated
    class attr -> CLI field" story class commands get -- so a module can
    declare positionals/options instead of adding everything imperatively.
    ``register`` then runs AFTER (so, e.g., a shared trailing ``targets``
    positional a `register` wrapper adds app-wide still lands after the
    module's own declared positionals -- matching the existing convention of
    calling a shared-positional helper LAST inside `register`).

    If ``command.register`` is bound to a real hook (not ``ModuleCommand``'s
    ``_noop`` default), it is called so the hook adds its own arguments
    directly on the argparse object -- the "work directly with the argparse
    object" API. **Gated and introspected on ``command.register`` itself**
    (not a separate ``getattr(module, "register", ...)`` re-fetch), so a
    caller who wraps/reassigns ``command.register`` directly (a
    documented-looking seam -- it's a plain instance attribute) is always
    honored, including for a module that defines no ``register`` of its own.
    Two hook arities are accepted:

    * ``register(parser, args)`` -- the 2-arg form. ``args`` is a best-effort
      parsed root instance from the prepass; a hook that ignores it (the common
      case) simply adds static args.
    * ``register(parser, args, logger)`` -- the 3-arg form. ``logger`` is
      ``getattr(args, "_logger_", logging.getLogger("duho"))`` (the parsed args'
      own logger on a ``LoggingArgs``-based root, else duho's ``"duho"`` logger).

    The arity is detected via ``inspect.signature`` (a ``*args`` hook is treated as
    3-arg-capable, and a hook whose signature can't be introspected falls back to
    the 2-arg call). This lets a module written against the 3-arg shape work
    without change while staying fully backward-compatible with 2-arg hooks.
    """
    # Read `command.description`/`command.help` (the `ModuleCommand`
    # properties already deriving exactly this from `module.__doc__`) rather
    # than re-deriving it here from `module.__doc__` a second time -- one
    # source of truth for what a module command's docstring means.
    # `help=` is ALWAYS `%`-expanded by argparse (crashing subparser
    # registration itself on 3.14 for every OTHER command too, not just this
    # one, the moment any command file's docstring has a stray `%`); escape
    # it the same way a class command's docstring already is.
    # `description=` is left as-is: argparse only `%`-formats it when it
    # contains a literal `%(prog)`, so escaping unconditionally would show a
    # literal `%` doubled in this command's own `--help`.
    parser = subparsers.add_parser(
        command._parsername_,
        parents=[base_parser],
        help=_escape_help(command.help),
        description=_escape_description(command.description),
        add_help=True,
    )
    # Mark this subparser's selection with a PRIVATE, per-parser dest
    # rather than the shared `command`/`_duho_command_` subparsers dest, which
    # is shared with every nested `_subcommands_` tree AND any root field a
    # user declares: a nested `remote list` class subcommand would be taken
    # for the top-level `list.py` module command, and a root `--command`
    # value overwritten. `set_defaults` only applies when THIS subparser is
    # the one argparse selected, so `app()` can tell that a module command
    # was chosen and which one.
    parser.set_defaults(_duho_module_command_=command)

    # Snapshot the dest names already present (inherited root/global options
    # via `parents=[base_parser]`, plus the auto-added `-h`/`--help`) BEFORE
    # this command's own fields go on -- the difference is this subparser's
    # OWN dests, stashed below for `duho.mcp` to build a schema/argv mapping
    # from (a declared field that collides with an inherited global is
    # silently SKIPPED by `_add_fields(strict=False)` just below, so it must
    # not be treated as this command's own field either).
    dests_before = {a.dest for a in parser._actions}

    args_cls = _module_args_cls(command, root_cls)
    if args_cls is not None:
        _add_module_declared_fields(parser, args_cls)

    register = getattr(command, "register", None)
    # Gate AND introspect the SAME object we call: `command.register` (NOT a
    # fresh `getattr(module, "register", ...)` re-fetch, which is a different
    # object whenever a caller wraps/reassigns `command.register` directly --
    # a documented-looking seam, since `ModuleCommand` always binds `register`
    # to a callable, `_noop` by default. Re-deriving from `module` silently
    # skipped a caller's wrapper for any module with no register of its own
    # (module_register was None -> not callable -> wrapper never called) and
    # could introspect the WRONG arity for a wrapper whose signature differs
    # from the module's original hook. `is not _discovery_noop` is the
    # identity check for "a real hook was bound" (`_noop` is a shared
    # module-level singleton in `duho.discovery`, so identity comparison is
    # reliable even after a caller wraps `command.register` with something
    # else, since a caller-supplied wrapper is by definition not `_noop`).
    if callable(register) and register is not _discovery_noop:
        try:
            if _wants_logger_arg(register):
                # One resolution, shared with the rest of the module-command
                # lifecycle: `command._logger_for` (the args instance's own
                # `_logger_` if present, else the "duho" fallback) -- not a
                # second, separately-maintained copy of that fallback.
                logger = command._logger_for(root_instance_args)
                if _wants_logger_by_keyword(register):
                    # A keyword-only `logger` (`def register(parser, args, *,
                    # logger)`) cannot be supplied positionally.
                    register(parser, root_instance_args, logger=logger)
                else:
                    register(parser, root_instance_args, logger)
            else:
                register(parser, root_instance_args)
        except _argparse.ArgumentError as exc:
            # The subparser inherits every root/global option (parent-arg
            # inheritance via ``parents=[base_parser]``), so a ``register`` hook
            # that adds a flag already owned by the root (e.g. ``-q`` from
            # ``LoggingArgs``, or ``-h``/``-v``/``--version``) collides. argparse's
            # own message doesn't say it clashed with a *global*, and the crash
            # only appears once a command is moved onto ``app``'s inheritance --
            # re-raise naming the command and the cause. BUT only when the
            # option string(s) argparse actually reports as conflicting are
            # ones the ROOT owns (`base_parser`) -- a hook that collides with
            # its own module-declared field, or adds the same flag twice, has
            # nothing to do with an inherited global, and blaming one sends
            # the author to look in the wrong place.
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

    # Stashed for `duho.mcp`'s MCP tool tree:
    # `_duho_module_args_cls_` is the resolved declarative class (``None`` for
    # a module with no ``Args``/only a ``register`` hook), reused for a
    # richer JSON-Schema field mapping than the bare-action fallback;
    # `_duho_module_own_dests_` is every dest THIS registration actually added
    # (declared fields plus anything a ``register`` hook added directly),
    # excluding inherited globals and `-h`/`--help` -- the set MCP maps
    # tool-call arguments onto. Neither attribute is read anywhere else in
    # this module; a module command's own dispatch contract is unaffected.
    parser._duho_module_args_cls_ = args_cls  # type: ignore[attr-defined]
    parser._duho_module_own_dests_ = {  # type: ignore[attr-defined]
        a.dest for a in parser._actions
    } - dests_before

    # A module command's subparser is a plain `add_parser()` instance --
    # never touched by `Args._initparser_`'s patching -- so it never got the
    # flag-between-positionals reorder fix declarative `Args`/`Cmd`
    # subcommands get. Patch it now that every field (declared + register
    # hook) is in place, so `_has_variadic_positional` sees the parser's
    # final shape.
    _patch_parser_for_reorder(parser)
    _keep_attached_double_dash(parser)
