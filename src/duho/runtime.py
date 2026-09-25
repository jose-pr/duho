"""Multi-command app runner: wire discovered commands into a runnable app.

This is the driver layer that turns a set of :class:`~duho.discovery.Command`
objects (class commands -- ``Cmd`` subclasses -- and module commands --
:class:`~duho.discovery.ModuleCommand`) into a real subcommand app:

* build a top-level parser for a *root* ``Cmd``/``Args`` (global options);
* add a subparsers tree and register every command under it;
* parse ``argv`` (with ``_passthrough_`` and the nested-help / shared-namespace
  behaviors preserved);
* dispatch exactly one selected command through the lifecycle
  ``init -> main -> success / finally_`` with a shared **context**.

**Composed on the shipped parser, not a parallel one.** The whole point of this
layer is that it reuses duho's existing ``_parser_``/``_initparser_``/``"#cls"``
machinery rather than introducing a second parser class. The four parser
behaviors clients rely on are reproduced on that path:

* **Parent-arg inheritance** -- every subcommand parser is built with argparse
  ``parents=[<root parser>]`` so global/root options appear on each subcommand.
* **Shared namespace** -- class commands already carry the ``"#cls"``
  deepest-selection contract (``_initparser_``), which yields one merged instance
  of the deepest selected class. A module command's parsed instance stays the
  ROOT instance (plus any fields a module ``register`` hook, or its own declared
  ``Args`` class, added directly) -- it is never itself constructed as a duho
  class, unlike a class command.
* **Nested-help suppression** -- the optional two-pass prepass uses the existing
  :func:`duho.parsers.prerun_parse` (``quiet=True``), which detaches the
  subparsers action and silences every terminal action (help, version,
  print-completion, help-agents) and any parse error for the duration of the
  call, restoring all of it before returning. No hand-patching.
* **``register`` hook** -- a module command may define ``register(parser, args)``
  (or the arity-tolerant ``register(parser, args, logger)``) to add arguments
  directly on the argparse object of its subcommand.

* **``_passthrough_``** -- argv after the first literal ``--`` is captured by the
  root parser's patched ``parse_known_args`` and reaches the dispatched command.

All union annotations are quoted so the module imports cleanly on Python 3.9.
No target fan-out / thread pools live here -- a single command is dispatched.
Parallel/fan-out patterns are a documented client wrapper and a future add-on.
"""

import argparse as _argparse
import inspect as _inspect
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from . import logging as _duho_logging
from . import parsers as _parsers
from .args import (
    Args as _Args,
    Cmd as _Cmd,
    _add_fields as _add_fields,
    _apply_default_layers_one as _apply_default_layers_one,
    _apply_layers as _apply_layers,
    _escape_description as _escape_description,
    _escape_help as _escape_help,
    _maybe_await as _maybe_await,
    _patch_parser_for_reorder as _patch_parser_for_reorder,
    _resolve_config_dict as _resolve_config_dict,
    _setup_instance_logging as _setup_instance_logging,
    _stash_layer_state as _stash_layer_state,
    _suppress_inherited_defaults as _suppress_inherited_defaults,
)
from .discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    _command_name as _command_name,
    _noop as _discovery_noop,
    discover_commands as _discover_commands,
    discover_entry_points as _discover_entry_points,
    is_class_command as _is_class_command,
    is_module_command as _is_module_command,
)
from .logging import log_exception as _log_exception

if _ty.TYPE_CHECKING:  # pragma: no cover - type-checking only
    from .env import Env as _Env

__all__ = ["run_command", "app"]

_LOGGER = _logging.getLogger(__name__)

# `_command_name` used to be a byte-for-byte copy of
# `discovery._command_name` (the same "`_parsername_` if set, else the class
# name" rule was ALSO inlined again in `args.py` and `mcp.py`); imported from
# `discovery` above instead so there is exactly one copy for this module and
# `discovery` to share, rather than two definitions that could silently drift
# apart (the import direction only allows it this way round: `discovery.py`
# already imports from `.args`, so `args.py`/`mcp.py` still keep their own).


def _reject_coroutine(result: object, where: str) -> None:
    """Refuse a coroutine ``result`` from a module command's entrypoint/hooks.

    duho only ever awaits ``Cmd.__call__`` (via ``duho.args._maybe_await`` --
    a class command may declare ``async def __call__``, driven to completion
    with ``asyncio.run`` at the call site). A module command's ``main``/
    ``init``/``success``/``finally_`` are NOT awaited: before this, an
    ``async def`` hook silently produced a coroutine nothing ever ran, whose
    only symptom was an easy-to-miss "coroutine was never awaited"
    ``RuntimeWarning`` raised later from the coroutine's own ``__del__``. This
    turns that into an immediate, loud failure instead. Mirrors
    :func:`duho.runpath._reject_coroutine` -- a separate copy, since
    ``runtime.py`` and ``runpath.py`` intentionally don't import each other.
    """
    if _inspect.iscoroutine(result):
        result.close()
        raise TypeError(
            "duho.runtime: %s returned a coroutine; duho awaits only "
            "Cmd.__call__ -- module command hooks must be synchronous" % where
        )


def run_command(
    command: "_Command",
    instance: object,
    *,
    context: object = None,
) -> int:
    """Dispatch one already-resolved command against a parsed ``instance``.

    ``instance`` is the parsed args/command instance produced by parsing (for a
    class command it IS the command; for a module command it is the root/parent
    instance carrying the parsed globals). Returns an exit code: a command that
    returns ``None`` maps to ``0``; a returned int is propagated.

    * **Class command** (a ``Cmd``): the parsed ``instance`` is itself the
      command, so this calls ``instance()`` (``Cmd.__call__`` is the entrypoint).
      Parsing already owns building the instance; there is no separate parse here.
    * **Module command** (:class:`ModuleCommand`): runs the lifecycle --
      ``ctx = command.init(instance)`` (identity/no-op default returning
      ``None``), then ``command.main(instance)`` and ``command.success(ctx,
      instance)`` inside a ``try`` whose ``finally`` always runs
      ``command.finally_(ctx, instance)``. If ``context`` is passed it overrides
      the ``init`` result (the driver builds the context once and threads it in).
      ``main``'s return value (or ``None`` -> ``0``) is the exit code; an
      exception from ``main`` propagates after ``finally_`` runs.

    No separate ``logger`` argument is threaded: hooks read ``instance._logger_``.
    For a module command, THIS driver ensures it is present before any hook
    runs: when ``instance`` has no ``_logger_`` of its own (a plain root, not
    ``LoggingArgs``-based), it is set to ``module_command._logger_for(instance)``
    (the ``"duho"`` fallback) so a hook written against the documented
    ``args._logger_`` convention never hits ``AttributeError``. Setting
    the attribute is best-effort: a root whose ``_logger_`` is a read-only
    property simply keeps using its own resolution.

    A module command's ``init``/``main``/``success``/``finally_`` are never
    awaited (unlike a class command's ``__call__``, see :func:`_maybe_await`):
    a coroutine returned by any of them is closed immediately and raises
    ``TypeError`` (see :func:`_reject_coroutine`), rather than silently never
    running.
    """
    if _is_module_command(command):
        module_command = _ty.cast(_ModuleCommand, command)
        if not isinstance(getattr(instance, "_logger_", None), _logging.Logger):
            try:
                instance._logger_ = module_command._logger_for(instance)  # type: ignore[attr-defined]
            except (
                Exception
            ):  # pragma: no cover - a property-bearing root may refuse the write
                pass
        ctx = context if context is not None else module_command.init(instance)
        _reject_coroutine(ctx, "%s init()" % _command_name(command))
        try:
            result = module_command.main(instance)
            _reject_coroutine(result, "%s main()" % _command_name(command))
            # `success` is the SUCCESS hook: run it only when main reported
            # success (None or exit code 0), not for a non-zero exit code.
            if result is None or result == 0:
                success_result = module_command.success(ctx, instance)
                _reject_coroutine(
                    success_result, "%s success()" % _command_name(command)
                )
        finally:
            # A raising `finally_` must not mask the original exception (if main
            # raised) nor the real exit code: log and swallow its error.
            try:
                fin_result = module_command.finally_(ctx, instance)
                _reject_coroutine(fin_result, "%s finally_()" % _command_name(command))
            except Exception:
                _LOGGER.exception(
                    "finally_ hook for command %r raised; ignoring",
                    _command_name(command),
                )
        return 0 if result is None else result

    # Class command: the parsed instance is the command; run it via __call__.
    # An ``async def __call__`` returns a coroutine; drive it to completion with
    # its own ``asyncio.run`` per call -- so a fan-out worker dispatching
    # the command per target gets an independent loop each time.
    result = _maybe_await(instance())  # type: ignore[operator]
    return 0 if result is None else result


def _cmds_path_commands(env: "_Env | None") -> "list[_Command]":
    """Resolve every command discoverable from ``env``'s ``CMDS_PATH``.

    Returns ``[]`` if ``env`` is ``None``, ``CMDS_PATH`` is unset/empty, or
    ``env`` doesn't support the expected interface -- all best-effort, never
    raises for a resolution problem (a per-entry issue is logged and that
    entry skipped; see below). Only touches ``CMDS_PATH`` when it is actually
    set and non-empty: a missing value must NOT be split/globbed -- that is
    what turned an unset var into "import every ``.py`` in the CWD".
    Splits on the OS path separator (``os.pathsep``; ``PATHSEP`` overrides),
    NOT a hard-coded ``":"`` -- otherwise a Windows ``"C:\\..."`` drive letter
    is mis-split into a bogus ``"C"`` path. See :meth:`duho.env.Env.paths`.

    **Empty segments never mean the CWD (a security-relevant fix).** ``env.paths``
    already drops an empty/whitespace-only segment before converting it to a
    ``Path`` (a leading, trailing, or doubled separator -- the common
    ``X="$X:/extra"`` append idiom run while ``X`` was unset -- must never
    resolve to ``Path('.')`` and glob-import/execute the current directory).
    This function does NOT trust that alone, since ``env`` is duck-typed and
    may not be a real :class:`duho.env.Env`: it re-requests the raw STRING
    segments (``ty=str``, no ``Path`` conversion yet) and filters blank ones
    itself before ever constructing a ``Path`` -- a defense-in-depth second
    layer that holds even for a caller-supplied ``env`` whose own ``paths()``
    does not filter. (An explicit ``"."`` segment is still honoured.)

    **A stale entry is skipped, not fatal.** Each entry is expanded
    with ``~`` (``Path.expanduser()``) and, if it does not resolve to an
    existing directory, logged at WARNING and skipped -- a removed plugin
    directory or an unexpanded ``~`` must not take down every invocation,
    built-ins and ``--help`` included. Discovery's own resilience still
    applies per entry (an ``ImportError`` from a single bad command file is
    logged and skipped; a ``SyntaxError`` still propagates).
    """
    if env is None:
        return []
    try:
        raw = env.get("CMDS_PATH")
    except Exception:  # pragma: no cover - env is best-effort here
        raw = None
    if not raw:
        return []
    try:
        segments = env.paths("CMDS_PATH", ty=str)
    except Exception:  # pragma: no cover - env is best-effort here
        segments = []
    discovered: "list[_Command]" = []
    for segment in segments:
        segment = segment.strip() if isinstance(segment, str) else str(segment)
        if not segment:
            # An empty/whitespace-only segment is never the current directory.
            continue
        path = _Path(segment).expanduser()
        if not path.is_dir():
            _LOGGER.warning("CMDS_PATH entry %r is not a directory; skipping", segment)
            continue
        try:
            discovered.extend(_discover_commands(path))
        except ImportError as exc:
            _log_exception(
                _LOGGER,
                "skipping CMDS_PATH entry %r: %s",
                segment,
                exc,
                level=_logging.WARNING,
            )
            continue
    return discovered


def _merge_discovered(
    base: "list[_Command]",
    discovered: "list[_Command]",
    overridden: "set[str] | None" = None,
) -> "list[_Command]":
    """Merge ``discovered`` on top of ``base``: discovered wins on a name clash.

    Keeps ``base``'s order for everything NOT overridden, then appends every
    discovered command. A name collision drops the ``base`` entry (the
    override story is intentional, but never silent).

    **Logging is deferred, not skipped.** Called from
    :func:`_resolve_commands` -- itself called before ``app()`` has set up any
    logging handler -- an immediate ``_LOGGER.info`` here is emitted into the
    void: Python's ``logging.lastResort`` handler only prints WARNING and
    above, so the override notice would be silently lost even under ``-vv``.
    When ``overridden`` is given, the overridden name is recorded into it
    instead of logged immediately, so the caller (``app()``) can log it once
    logging is actually configured. When ``overridden`` is omitted (a direct,
    non-``app()`` caller), the old immediate-INFO behavior is kept.
    """
    if not discovered:
        return base
    override = {_command_name(c) for c in discovered if _command_name(c)}
    merged = []
    for cmd in base:
        name = _command_name(cmd)
        if name and name in override:
            if overridden is not None:
                overridden.add(name)
            else:
                _LOGGER.info("CMDS_PATH command %r overrides the built-in", name)
            continue
        merged.append(cmd)
    merged.extend(discovered)
    return merged


def _resolve_commands(
    root: "type | None",
    commands: "_ty.Sequence[_Command] | None",
    source: "str | _Path | None",
    env: "_Env | None",
    entry_points: "str | None" = None,
    overridden: "set[str] | None" = None,
) -> "list[_Command]":
    """Resolve the command set for :func:`app` by precedence.

    Base-source order: an explicit ``commands`` list > ``discover_commands
    (source)`` > ``discover_entry_points(entry_points)`` > ``root._subcommands_``.
    ``env``-derived paths (``CMDS_PATH``) then ALWAYS merge on top of whichever
    base source produced the list -- a LAYER, not a branch reachable only when
    no other source was given. (Before this fix, passing an explicit
    ``commands=``/``source=``/``entry_points=`` silently disabled ``CMDS_PATH``
    entirely, even when ``env=`` was also passed -- the operator's exported
    variable did nothing, with no warning.)

    ``CMDS_PATH`` is additive: an app's base commands stay available and the
    discovered ones are added alongside. Setting it to drop the base commands
    would make every invocation depend on the variable being right, which is a
    footgun for a *supplementary* command directory -- the usual reason to point
    at one is "I have a few extra commands", not "replace this CLI". A discovered
    command whose name collides with a base command **wins** (that is the
    override story), and the shadowing is never silent (see ``overridden``).

    **Additive, not exclusive, w.r.t. a root's OWN declared subcommands.**
    This function only falls back to ``root._subcommands_`` as ITS
    OWN base when none of ``commands``/``source``/``entry_points`` is given.
    But ``app()`` separately, and always, registers ``root``'s own declared
    ``_subcommands_`` too (via ``root_cls._parser_()``, independent of this
    function) -- so passing ``commands=``/``source=``/``entry_points=``
    alongside a root that already declares ``_subcommands_`` does not remove
    or replace those; this function's result is layered on top of them, not
    instead of them. Pass an explicit, subcommand-free root (or ``root=None``)
    to get a command set with nothing but what this function resolves.

    ``overridden``, when given, receives the name of every base command a
    CMDS_PATH-discovered one replaced (see :func:`_merge_discovered`) --
    ``app()`` uses this to log the override once, after logging is set up,
    and to avoid a second, redundant collision warning when registering.

    Discovery is resilient (a bad command drops out with a warning -- see
    :func:`duho.discovery.discover_commands` /
    :func:`duho.discovery.discover_entry_points`).
    """
    if commands is not None:
        base = list(commands)
    elif source is not None:
        base = _discover_commands(source)
    elif entry_points is not None:
        base = _discover_entry_points(entry_points)
    else:
        base = (
            list(getattr(root, "_subcommands_", []) or []) if root is not None else []
        )

    return _merge_discovered(base, _cmds_path_commands(env), overridden=overridden)


def _full_names(command: object, cmd_name: str, kind: str) -> "list[str]":
    """Every name ``command`` claims in a subparsers action.

    A class command claims its primary ``cmd_name`` PLUS its own
    ``_parseraliases_`` (argparse's ``add_parser(..., aliases=...)`` registers
    each alias as an extra ``_name_parser_map`` key pointing at the same
    subparser object). A module command has no alias mechanism and claims
    only its primary name. Used to detect -- and, on an override, fully
    undo -- a collision against ANY of a command's names, not just its
    primary one: checking only ``cmd_name`` missed the case where an
    INCOMING command's alias collides with an already-registered name/alias,
    which argparse itself only reports at ``add_parser()`` time (raising on
    3.11+, silently overwriting on 3.9).
    """
    names = [cmd_name]
    if kind == "class":
        for alias in getattr(command, "_parseraliases_", None) or ():
            if alias not in names:
                names.append(alias)
    return names


def _register_class_command(
    subparsers: "_argparse._SubParsersAction",
    command: type,
    base_parser: "_argparse.ArgumentParser",
    *,
    inherited_config_hint: bool = False,
) -> "_argparse.ArgumentParser":
    """Register a class command under ``subparsers`` with parent-arg inheritance.

    Delegates to the class's own ``_parser_(subparsers, parents=[base_parser])``:
    this reuses the shipped registration path (which installs the ``"#cls"``
    deepest-selection ``parse_known_args`` on the subparser and recurses into any
    nested ``_subcommands_``), while ``parents=`` makes the root/global options
    appear on the subcommand too. Returns the built subparser so the caller can
    link it to the app's root (``_duho_parent_parser_``) for the
    lazy env/config layering and provenance-merge machinery in ``args.py``.

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


def _wants_logger_arg(register: "_ty.Callable[..., object]") -> bool:
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
    conservatively default to ``False`` (the 2-arg call), which is the
    historical shape and never over-supplies an argument the hook can't take.

    Inspects with ``follow_wrapped=False``: a hook wrapped with
    ``functools.wraps`` (e.g. a user decorator around ``register``) must be
    read on its OWN signature, not the wrapped function's -- otherwise the
    wrapper's own extra/different parameters are invisible and the wrong
    calling convention is chosen (the same bug ``runpath._step_wants_ctx``
    had before the fix).
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
    positional = 0
    for param in params.values():
        if param.kind is _inspect.Parameter.VAR_POSITIONAL:
            return True  # *args absorbs the extra logger positional
        if param.kind in (
            _inspect.Parameter.POSITIONAL_ONLY,
            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 3


def _wants_logger_by_keyword(register: "_ty.Callable[..., object]") -> bool:
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


def _conflicting_option_strings(exc: "_argparse.ArgumentError") -> "list[str]":
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


def _module_args_cls(command: "_ModuleCommand", root_cls: type) -> "type | None":
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
    # `_class_constants` would AST-parse `runtime.py` itself looking for a
    # `_Args` ClassDef that was never there, on every module command that
    # declares its own `Args` (a real, measured cold-start cost this class
    # has no source body to justify paying).
    return type("_Args", (args_cls, root_cls), {"_duho_constants_": {}})


def _add_module_declared_fields(
    parser: "_argparse.ArgumentParser", args_cls: type
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

    A module command's declared fields now support ``NS(conflicts=...)``/
    ``NS(group=...)`` the same as a class command's -- that support used to
    live only in ``_initparser_``'s own, separate copy of this wiring.
    """
    _add_fields(parser, args_cls, strict=False)


def _register_module_command(
    subparsers: "_argparse._SubParsersAction",
    command: "_ModuleCommand",
    base_parser: "_argparse.ArgumentParser",
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
    # rather than relying on the shared `command`/`_duho_command_` subparsers
    # dest to name it. That dest is shared with every nested `_subcommands_`
    # tree (a class command's own subparsers) AND any root field a user
    # happens to declare -- `app()`'s dispatch used to read it via
    # `getattr(instance, "command", None)`, so a nested `remote list` class
    # subcommand silently ran the top-level `list.py` module command instead
    # (same dest, same name), and a root `--command` field's value was
    # overwritten by whichever subcommand ran. `set_defaults` only applies
    # when THIS subparser is the one argparse actually selected, so `app()`
    # can now identify "a module command was chosen, and this is which one"
    # directly, with no dependence on any dest a user or a nested tree could
    # ever collide with.
    parser.set_defaults(_duho_module_command_=command)

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
    # module-level singleton in `discovery.py`, so identity comparison is
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

    # A module command's subparser is a plain `add_parser()` instance --
    # never touched by `Args._initparser_`'s patching -- so it never got the
    # flag-between-positionals reorder fix declarative `Args`/`Cmd`
    # subcommands get. Patch it now that every field (declared + register
    # hook) is in place, so `_has_variadic_positional` sees the parser's
    # final shape.
    _patch_parser_for_reorder(parser)


def _build_parser(
    root: "type | None",
    name: "str | None",
    description: "str | None",
    config: "str | _Path | None" = None,
) -> "tuple[_argparse.ArgumentParser, _argparse.ArgumentParser, type]":
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
    parser_kwargs: "dict[str, object]" = {}
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
    base_parser = root_cls._parser_(  # type: ignore[attr-defined]
        add_help=False, _inherited_config_hint_=has_config
    )
    # base_parser exists only to donate the root's *options* to each subcommand
    # via `parents=`. When the root carries `_subcommands_`, `_parser_` also gave
    # it a subparsers action -- inheriting that would nest the whole command tree
    # under every subcommand and make its `command` argument required again
    # ("Root greet ... {hello} ... error: the following arguments are required:
    # command"). Drop it; only optionals should flow downward.
    _parsers.strip_subparsers(base_parser)
    return parser, base_parser, root_cls


def _deregister_subparser(subparsers: "_argparse._SubParsersAction", name: str) -> None:
    """Remove a previously-registered subparser ``name``, and every alias of
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


def _apply_app_config_layers(
    root_cls: type,
    subparsers: "_argparse._SubParsersAction",
    registry: "dict[str, tuple[str, object]]",
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
    * a **module command** with a declared ``args_cls`` (since 0.4.1 a
      module command may declare a module-level ``Args`` class) has NO
      ``_initparser_`` hook at all (its subparser is a deliberately bare
      stdlib one -- see this module's own docstring), so its table is applied
      EAGERLY, immediately, rather than deferred.

    A module command's own env/config-bound field, once laid on eagerly
    above, gets the SAME "never show the live value" redaction a class
    command's does: right after ``_apply_default_layers_one`` installs it,
    ``duho.agenthelp.stash_default_provenance`` snapshots the class default
    (and, when applicable, a value-free provenance note) onto each action,
    for its own ``--help``/agent-help description to read later -- BEFORE
    ``app()``'s own ``parser.parse_args(argv)`` runs (this whole function is
    called from command-tree assembly, always before that), so it is in
    place no matter which trigger fires. Passed this command's OWN
    ``args_cls`` explicitly rather than relying on ``parser._duho_cls_``: a
    module command's subparser deliberately has none (``duho.mcp`` also reads
    that same attribute, to decide whether a node is callable -- a decision
    this redaction has no business changing). ``duho.agenthelp`` is imported
    lazily so a plain ``duho.app()`` call with no module command declaring
    fields never pays for it.

    ``raw_config`` is the already-loaded TOML table (``app`` loads it once so
    the root layering can also run before the advisory prepass).
    """
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
            _apply_default_layers_one(sub_parser, args_cls, sub_table)
            from . import agenthelp as _agenthelp

            _agenthelp.stash_default_provenance(sub_parser, cls=args_cls)
            # A module command's subparser is a plain `add_parser()` instance
            # with its own ordinary argparse `-h`/`--help` action -- it never
            # goes through `args.py`'s `_install_agent_help`/
            # `_AgentHelpAction` (this command deliberately has no
            # `_duho_cls_` of its own; see this function's own docstring), so
            # without this its help text would still render a literal
            # `%(default)s` straight from the live env/config value the line
            # above just staged onto `action.default`.
            _agenthelp.install_help_redaction(sub_parser)


def _prepare_app_parser(
    root: "type | None",
    name: "str | None",
    description: "str | None",
    config: "str | _Path | None",
    argv: "_ty.Sequence[str] | None",
    resolved_commands: "list[_Command]",
) -> "tuple[_argparse.ArgumentParser, _argparse.ArgumentParser, type, dict, object]":
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
    raw_config: dict = _resolve_config_dict(root_cls, config)
    _stash_layer_state(parser, root_cls, raw_config)

    # A prepass parsed root instance is offered to module ``register`` hooks so a
    # hook that wants the already-parsed globals can read them. It is a
    # best-effort prepass: `prerun_parse` detaches `parser`'s subparsers action
    # for the call (restoring it before returning, so registration below still
    # sees it) -- which is what makes this safe to run even when `root` already
    # has built-in `_subcommands_` (previously a KeyError('#cls') here, from the
    # relaxed subparsers action re-entering this same parser's own patched
    # parse_known_args and double-popping the selection marker) -- and
    # `quiet=True` so a required/unknown-arg error, and every terminal action
    # (--version, --print-completion, --help-agents, -h/--help), stays fully
    # silent here; the real parse below is what actually reports/prints,
    # exactly once. Most register hooks ignore the parsed globals
    # entirely and just add static args.
    prepass_args: object = None
    if any(_is_module_command(c) for c in resolved_commands):
        try:
            from .parsers import prerun_parse as _prerun_parse

            prepass_args = _prerun_parse(parser, argv, quiet=True)
        except SystemExit:
            # A required- or unknown-arg error (raised silently, since
            # quiet=True) must not abort the whole app: degrade to no prepass
            # and let the real parse below report it authoritatively.
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
    from . import formatters as _formatters

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


def _run_app(
    parser: "_argparse.ArgumentParser",
    argv: "_ty.Sequence[str] | None",
    env: "_Env | None",
    setup_logging: bool,
    root_cls: type,
    required_root_actions: "list[_argparse.Action]",
    cmds_path_overridden: "set[str]",
    notices: "list[tuple[int, str]]",
    run: "_ty.Callable[[_Command, object], int]",
) -> int:
    """Parse ``argv``, finish per-invocation setup, and dispatch one command.

    The real ``parse_args`` call, the required-global re-check,
    attaching ``_env_``, logging setup, flushing the deferred override/
    collision notices, and resolving + running the selected command
    (module vs class). Split out of :func:`app`; no behavior change,
    the full suite is the guard.
    """
    instance = parser.parse_args(argv)

    # A root required global un-required above must still
    # have ended up with a real value from SOMEWHERE (the root itself, a child
    # given the flag after the subcommand, or a config/env layer) -- report it
    # the same way argparse's own required-arguments check would.
    missing_required = [
        a for a in required_root_actions if getattr(instance, a.dest, None) is None
    ]
    if missing_required:
        parser.error(
            "the following arguments are required: "
            + ", ".join(
                a.option_strings[0] if a.option_strings else a.dest
                for a in missing_required
            )
        )

    # Make the resolved app-wide `Env` reachable from the dispatched command via
    # the sandwich-named `_env_` handle (never a user field). A command reads
    # `self._env_` for app-level settings; None when no env was passed.
    try:
        instance._env_ = env  # type: ignore[attr-defined]
    except (AttributeError, TypeError):  # pragma: no cover - namespaces allow it
        pass

    _setup_instance_logging(instance, setup_logging, root_cls)

    # Flush every deferred override/collision notice now that logging is
    # actually configured -- an INFO emitted earlier, before any
    # handler existed, would have been silently lost even under `-vv`
    # (`logging.lastResort` only prints WARNING and above). The intentional,
    # documented CMDS_PATH-over-a-base-command override is INFO; anything
    # else the registration loop collected (two independently-resolved
    # commands genuinely colliding) is WARNING -- and it alone, not both, so
    # the documented override no longer warns on every run.
    for overridden_name in sorted(cmds_path_overridden):
        _LOGGER.info("CMDS_PATH command %r overrides the built-in", overridden_name)
    for level, message in notices:
        _LOGGER.log(level, message)

    # Resolve which command was selected. A class command selection yields a
    # constructed instance that IS the command (a Cmd subclass); a module
    # command selection leaves ``instance`` as the root instance, identified
    # by the private ``_duho_module_command_`` marker its OWN subparser set
    # via ``set_defaults`` -- NOT by any shared ``command``/
    # ``_duho_command_`` dest, which a nested ``_subcommands_`` tree sharing
    # a subcommand's name, or a root field a user happens to call ``command``,
    # could otherwise silently redirect dispatch through. ``pop`` (mirroring
    # the ``"#cls"`` sidecar convention) keeps this framework bookkeeping out
    # of ``vars(instance)``.
    module_command = _ty.cast(
        "_ModuleCommand | None", vars(instance).pop("_duho_module_command_", None)
    )

    if module_command is not None:
        # run_command owns the full lifecycle (init -> main -> success/finally_).
        # Don't pre-build the context here or init would run twice. When a custom
        # `dispatch` was supplied it replaces this final run step (default is
        # `run_command`); it receives the resolved ModuleCommand and the instance.
        return run(module_command, instance)

    # Class command (or the root itself if it is a runnable Cmd): dispatch the
    # parsed instance directly. It is already the deepest selected Cmd.
    if not isinstance(instance, _Cmd):
        raise NotImplementedError(
            f"{type(instance).__name__} holds data but is not runnable "
            f"(no '__call__'); make it a Cmd (subclass duho.Cmd or "
            f"build one with duho.command(...)) to run it, or register runnable "
            f"commands"
        )
    return run(_ty.cast(_Command, type(instance)), instance)


def app(
    root: "type | None" = None,
    *,
    commands: "_ty.Sequence[_Command] | None" = None,
    source: "str | _Path | None" = None,
    entry_points: "str | None" = None,
    argv: "_ty.Sequence[str] | None" = None,
    name: "str | None" = None,
    description: "str | None" = None,
    env: "_Env | None" = None,
    config: "str | _Path | None" = None,
    setup_logging: bool = True,
    dispatch: "_ty.Callable[[_Command, object], int] | None" = None,
) -> int:
    """Build a multi-command app, parse ``argv``, and dispatch one command.

    ``root`` is a ``Cmd``/``Args``/``LoggingArgs`` subclass supplying the app's
    global options (``None`` -> a bare data root, for an app whose commands all
    come from discovery). The BASE command set is resolved by precedence
    (:func:`_resolve_commands`): ``commands`` > ``discover_commands(source)`` >
    ``discover_entry_points(entry_points)`` > ``root._subcommands_``.
    ``env.paths("CMDS_PATH", ty=Path)`` then ALWAYS merges on top of whichever
    base was used -- a layer, not a branch reachable only when no other source
    is given -- extending the base rather than replacing it, with a discovered
    command overriding a same-named base command (logged, never silent).

    **Additive, not exclusive.** ``root``'s own declared
    ``_subcommands_`` are ALWAYS registered too (``app`` reuses the
    subparsers action ``root_cls._parser_()`` already built for them), no
    matter what ``commands``/``source``/``entry_points`` was passed --
    passing one of those does not remove or replace a root's built-ins, it
    only adds alongside them (see :func:`_resolve_commands` for the exact
    contract). Give ``root`` no ``_subcommands_`` of its own (or pass
    ``root=None``) for an app whose ONLY commands are the ones explicitly
    resolved here.

    ``entry_points`` is an installed-distribution entry-point **group** name
    (e.g. ``"myapp.commands"``): every entry point advertised in that group by an
    installed distribution is loaded and registered as a subcommand, so a
    separately-installed plugin package can contribute commands without the app
    knowing about it. Loading is resilient -- a broken plugin warns and is
    skipped, the rest still load. ``importlib.metadata`` is imported lazily, so
    an app that does not use ``entry_points=`` never pays its import cost.

    Each command is registered under a ``title="command"`` subparsers action:

    * a **class command** via its own ``_parser_(subparsers, parents=[root])`` --
      the shipped path, so ``"#cls"`` deepest-selection and any nested
      ``_subcommands_`` keep working, with global options inherited via
      ``parents=``;
    * a **module command** as a subparser (help/description from the module
      docstring), inheriting global options via ``parents=``; if the module
      defines ``register(parser, args)`` (or ``register(parser, args, logger)``)
      it is called so the module adds its own arguments directly.

    Parsing goes through the root parser's patched ``parse_known_args`` (from
    ``_initparser_``), so ``"#cls"`` selection, ``_passthrough_`` capture, and
    the layered instance construction all apply. When ``setup_logging``,
    stderr logging is initialised and verbosity applied -- identical to
    ``duho.main``, including its fallback for a plain ``Cmd`` command
    selected under a ``LoggingArgs`` root (see ``_setup_instance_logging``).

    **Config/env thread-down.** Before parsing, env/config-file defaults are
    layered onto the root and every class command's fields (precedence CLI > env
    > config > class default): ``config`` (or, if omitted, a ``Cli`` root's
    ``_config_``) is loaded once; its top-level keys apply to the root and each
    ``[<subcommand>]`` table to that command. This is app()'s analogue of the
    ``_apply_default_layers`` call ``duho.main``/``duho.parse`` make -- needed
    here because commands come from sources not reachable via
    ``root._subcommands_``. The resolved ``env`` (if any) is attached to the
    dispatched instance as the sandwich-named ``_env_`` handle, so a command can
    read app-wide settings via ``self._env_``.

    The selected command is dispatched via :func:`run_command`; its int return is
    this function's return (success -> ``0``, a ``main`` returning ``2`` ->
    ``2``). Discovery is resilient: a single unimportable command drops out with a
    warning and the rest still run.

    **The ``dispatch`` seam.** ``app`` owns discovery, parser build, command
    registration, config/env thread-down, parsing, and logging setup. The final
    "run the one selected command" step is the ONE point a consumer can override:
    pass ``dispatch`` to replace it. The callable receives the resolved
    :class:`~duho.discovery.Command` (a ``Cmd`` subclass for a class command; the
    :class:`~duho.discovery.ModuleCommand` for a module command) and the parsed
    ``instance``, and must return an ``int`` exit code, which becomes ``app``'s
    return. A dispatch may call :func:`run_command` itself (the default when
    ``dispatch is None``), fan the command out over targets via
    :mod:`duho.fanout`, build a per-invocation context threaded ahead of args, or
    anything else -- everything ``app`` already resolved (the same ``command`` and
    ``instance`` the default path would run) is reused rather than re-derived. When
    ``dispatch`` is ``None`` the behavior is byte-identical to calling
    :func:`run_command` directly, so existing callers are unaffected.
    """
    run = dispatch if dispatch is not None else run_command
    # Names CMDS_PATH overrode (see `_resolve_commands`/`_merge_discovered`).
    # Collected rather than logged immediately: at this point in `app()` no
    # logging handler has been installed yet, so an immediate `_LOGGER.info`
    # would be emitted into the void -- flushed once `_run_app` has set
    # up logging. Also used by `_register_commands` to recognize that a
    # registry collision for the SAME name is this very (intentional,
    # already-accounted-for) override, not a second, independent one worth
    # its own warning.
    cmds_path_overridden: "set[str]" = set()
    resolved_commands = _resolve_commands(
        root, commands, source, env, entry_points, overridden=cmds_path_overridden
    )

    parser, base_parser, root_cls, raw_config, prepass_args = _prepare_app_parser(
        root, name, description, config, argv, resolved_commands
    )

    subparsers, registry, notices = _register_commands(
        root,
        resolved_commands,
        parser,
        base_parser,
        root_cls,
        prepass_args,
        cmds_path_overridden,
        inherited_config_hint=config is not None,
    )

    required_root_actions = _finalize_command_tree(
        parser, subparsers, root_cls, registry, raw_config
    )

    return _run_app(
        parser,
        argv,
        env,
        setup_logging,
        root_cls,
        required_root_actions,
        cmds_path_overridden,
        notices,
        run,
    )
