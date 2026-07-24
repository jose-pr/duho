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
  of the deepest selected class. Module commands don't declare their own args, so
  the parsed instance is the root instance (plus any fields a module ``register``
  hook added directly).
* **Nested-help suppression** -- the optional two-pass prepass uses the existing
  :func:`duho.parsers.prerun_parse`, which already relaxes ``_HelpAction`` and
  subparser validation for the prepass and restores them. No hand-patching.
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
from .args import (
    Args as _Args,
    Cmd as _Cmd,
    _apply_default_layers_one as _apply_default_layers_one,
    _load_config as _load_config,
    _maybe_await as _maybe_await,
    _suppress_inherited_defaults as _suppress_inherited_defaults,
)
from .discovery import (
    Command as _Command,
    ModuleCommand as _ModuleCommand,
    _noop as _discovery_noop,
    discover_commands as _discover_commands,
    discover_entry_points as _discover_entry_points,
    is_class_command as _is_class_command,
    is_module_command as _is_module_command,
)

__all__ = ["run_command", "app"]

_LOGGER = _logging.getLogger("duho")


def _command_name(command: object) -> str:
    """Resolve a command's subcommand name (class or module command)."""
    if _is_class_command(command):
        return getattr(command, "_parsername_", None) or command.__name__  # type: ignore[union-attr]
    return getattr(command, "_parsername_", "")


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

    No separate ``logger`` argument is threaded: hooks read ``instance._logger_``
    when the args class provides one (``ModuleCommand`` resolves it internally).
    """
    if _is_module_command(command):
        module_command = _ty.cast(_ModuleCommand, command)
        ctx = context if context is not None else module_command.init(instance)
        try:
            result = module_command.main(instance)
            # `success` is the SUCCESS hook: run it only when main reported
            # success (None or exit code 0), not for a non-zero exit code (M22).
            if result is None or result == 0:
                module_command.success(ctx, instance)
        finally:
            # A raising `finally_` must not mask the original exception (if main
            # raised) nor the real exit code: log and swallow its error (M22).
            try:
                module_command.finally_(ctx, instance)
            except Exception:
                _LOGGER.exception(
                    "duho: finally_ hook for command %r raised; ignoring",
                    _command_name(command),
                )
        return 0 if result is None else result

    # Class command: the parsed instance is the command; run it via __call__.
    # An ``async def __call__`` returns a coroutine; drive it to completion with
    # its own ``asyncio.run`` per call (F4) -- so a fan-out worker dispatching
    # the command per target gets an independent loop each time.
    result = _maybe_await(instance())  # type: ignore[operator]
    return 0 if result is None else result


def _cmds_path_commands(env: object) -> "list[_Command]":
    """Resolve every command discoverable from ``env``'s ``CMDS_PATH``.

    Returns ``[]`` if ``env`` is ``None``, ``CMDS_PATH`` is unset/empty, or
    ``env`` doesn't support the expected interface -- all best-effort, never
    raises. Only touches ``CMDS_PATH`` when it is actually set and non-empty:
    a missing value must NOT be split/globbed -- that is what turned an unset
    var into "import every ``.py`` in the CWD" (C11). Splits on the OS path
    separator (``os.pathsep``; ``PATHSEP`` overrides), NOT a hard-coded
    ``":"`` -- otherwise a Windows ``"C:\\..."`` drive letter is mis-split
    into a bogus ``"C"`` path. See :meth:`duho.env.Env.paths`.
    """
    if env is None:
        return []
    raw = None
    try:
        raw = env.get("CMDS_PATH")  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - env is best-effort here
        raw = None
    if not raw:
        return []
    try:
        paths = env.paths("CMDS_PATH", ty=_Path)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - env is best-effort here
        paths = []
    discovered: "list[_Command]" = []
    for path in paths:
        if str(path):
            discovered.extend(_discover_commands(path))
    return discovered


def _merge_discovered(
    base: "list[_Command]", discovered: "list[_Command]"
) -> "list[_Command]":
    """Merge ``discovered`` on top of ``base``: discovered wins on a name clash.

    Keeps ``base``'s order for everything NOT overridden, then appends every
    discovered command; a name collision drops the ``base`` entry and logs the
    shadowing at INFO (the override story is intentional, but never silent).
    """
    if not discovered:
        return base
    override = {_command_name(c) for c in discovered if _command_name(c)}
    merged = []
    for cmd in base:
        name = _command_name(cmd)
        if name and name in override:
            _LOGGER.info("CMDS_PATH command %r overrides the built-in", name)
            continue
        merged.append(cmd)
    merged.extend(discovered)
    return merged


def _resolve_commands(
    root: "type | None",
    commands: "_ty.Sequence[_Command] | None",
    source: "str | _Path | None",
    env: object,
    entry_points: "str | None" = None,
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
    override story), and the shadowing is logged so it is never silent.

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
        base = list(getattr(root, "_subcommands_", []) or []) if root is not None else []

    return _merge_discovered(base, _cmds_path_commands(env))


def _register_class_command(
    subparsers: "_argparse._SubParsersAction",
    command: type,
    base_parser: "_argparse.ArgumentParser",
) -> None:
    """Register a class command under ``subparsers`` with parent-arg inheritance.

    Delegates to the class's own ``_parser_(subparsers, parents=[base_parser])``:
    this reuses the shipped registration path (which installs the ``"#cls"``
    deepest-selection ``parse_known_args`` on the subparser and recurses into any
    nested ``_subcommands_``), while ``parents=`` makes the root/global options
    appear on the subcommand too.
    """
    command._parser_(subparsers, parents=[base_parser])  # type: ignore[attr-defined]


def _wants_logger_arg(register: "_ty.Callable[..., object]") -> bool:
    """True if a module ``register`` hook takes a 3rd ``logger`` positional.

    A module's ``register`` may be written either 2-arg ``(parser, args)`` or
    3-arg ``(parser, args, logger)``. This inspects the hook's signature and
    returns ``True`` only when it accepts a third positional argument -- either
    because it declares three (or more) positional parameters, or because it
    declares a ``*args`` catch-all (which can absorb a logger). If the signature
    cannot be introspected (a builtin / C callable / anything ``inspect`` refuses),
    we conservatively default to ``False`` (the 2-arg call), which is the
    historical shape and never over-supplies an argument the hook can't take.
    """
    try:
        params = _inspect.signature(register).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return False
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


def _register_module_command(
    subparsers: "_argparse._SubParsersAction",
    command: "_ModuleCommand",
    base_parser: "_argparse.ArgumentParser",
    root_instance_args: object,
) -> None:
    """Register a module command as a subparser and run its ``register`` hook.

    The subparser inherits the root/global options via ``parents=[base_parser]``
    (parent-arg inheritance). If ``command.register`` is bound to a real hook
    (not ``ModuleCommand``'s ``_noop`` default), it is called so the hook adds
    its own arguments directly on the argparse object -- the "work directly
    with the argparse object" API. **Gated and introspected on
    ``command.register`` itself** (not a separate ``getattr(module,
    "register", ...)`` re-fetch), so a caller who wraps/reassigns
    ``command.register`` directly (a documented-looking seam -- it's a plain
    instance attribute) is always honored, including for a module that
    defines no ``register`` of its own. Two hook arities are accepted:

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
    module = command.module
    doc = (getattr(module, "__doc__", None) or "").strip()
    help_text = doc.splitlines()[0] if doc else ""
    parser = subparsers.add_parser(
        command._parsername_,
        parents=[base_parser],
        help=help_text,
        description=doc,
        add_help=True,
    )

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
                logger = getattr(root_instance_args, "_logger_", None)
                if not isinstance(logger, _logging.Logger):
                    logger = _LOGGER
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
            # re-raise naming the command and the cause.
            raise _argparse.ArgumentError(
                None,
                f"command {command._parsername_!r}: its register() hook added an "
                f"option that collides with a global flag inherited from the app "
                f"root ({exc}). Every subcommand parser inherits the root's global "
                f"options (e.g. -h, -v, -q, --version); pick a different flag in "
                f"register().",
            ) from exc


def _build_parser(
    root: "type | None",
    name: "str | None",
    description: "str | None",
) -> "tuple[_argparse.ArgumentParser, _argparse.ArgumentParser, type]":
    """Build the top-level parser and a help-free base parser for ``root``.

    Returns ``(parser, base_parser, root_cls)``. ``root`` may be any ``Cmd``/
    ``Args``/``LoggingArgs`` subclass supplying global options; ``None`` yields a
    bare data ``Args`` root so an app with only external commands still works.
    ``name`` / ``description`` override the parser prog / description when given.

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
    parser = root_cls._parser_(**parser_kwargs)  # type: ignore[attr-defined]
    base_parser = root_cls._parser_(add_help=False)  # type: ignore[attr-defined]
    # base_parser exists only to donate the root's *options* to each subcommand
    # via `parents=`. When the root carries `_subcommands_`, `_parser_` also gave
    # it a subparsers action -- inheriting that would nest the whole command tree
    # under every subcommand and make its `command` argument required again
    # ("Root greet ... {hello} ... error: the following arguments are required:
    # command"). Drop it; only optionals should flow downward.
    _strip_subparsers(base_parser)
    return parser, base_parser, root_cls


def _strip_subparsers(parser: "_argparse.ArgumentParser") -> None:
    """Remove any subparsers action from ``parser`` (used for parent donors)."""
    subs = [
        a for a in parser._actions  # type: ignore[attr-defined]
        if isinstance(a, _argparse._SubParsersAction)  # type: ignore[attr-defined]
    ]
    for action in subs:
        parser._actions.remove(action)  # type: ignore[attr-defined]
        for group in parser._action_groups:  # type: ignore[attr-defined]
            if action in group._group_actions:  # type: ignore[attr-defined]
                group._group_actions.remove(action)  # type: ignore[attr-defined]


def _existing_subparsers(
    parser: "_argparse.ArgumentParser",
) -> "_argparse._SubParsersAction | None":
    """The parser's already-registered subparsers action, if it has one.

    A root class carrying ``_subcommands_`` gets one from its own ``_parser_``;
    argparse permits only a single subparsers action per parser, so callers must
    reuse it instead of adding another."""
    for action in parser._actions:  # type: ignore[attr-defined]
        if isinstance(action, _argparse._SubParsersAction):  # type: ignore[attr-defined]
            return action
    return None


def _deregister_subparser(
    subparsers: "_argparse._SubParsersAction", name: str
) -> None:
    """Remove a previously-registered subparser ``name`` from ``subparsers``.

    argparse's ``add_parser`` raises ``ArgumentError('conflicting subparser')``
    on a duplicate name, so a later registration under the same name cannot
    simply overwrite an earlier one. This drops the earlier registration from
    the name->parser map and the help pseudo-actions so the later command can
    register cleanly and win (see the collision handling in :func:`app`, M6).
    """
    subparsers._name_parser_map.pop(name, None)  # type: ignore[attr-defined]
    subparsers._choices_actions = [  # type: ignore[attr-defined]
        a for a in subparsers._choices_actions  # type: ignore[attr-defined]
        if getattr(a, "dest", None) != name
    ]


def _apply_app_config_layers(
    parser: "_argparse.ArgumentParser",
    root_cls: type,
    subparsers: "_argparse._SubParsersAction",
    class_command_names: "dict[str, type]",
    raw_config: dict,
) -> None:
    """Thread env/config-file defaults down a ``Cli`` app's command tree.

    ``duho.main``/``duho.parse`` route through ``_apply_default_layers``, which
    walks a *statically declared* ``_subcommands_`` tree. ``app`` instead
    registers commands from precedence-resolved sources (``commands`` /
    ``discover_commands(source)`` / env), so its subcommand parsers are NOT
    reachable via ``root_cls._subcommands_``. This helper reproduces the same
    layering against the parsers ``app`` actually built:

    * the root TOML keys (top-level table) apply to ``root_cls``'s own fields on
      ``parser``;
    * each **class command**'s ``[<subcommand-name>]`` table applies to that
      command's fields on its own subparser (looked up in the live
      ``subparsers.choices``).

    Module commands declare no duho fields, so config tables don't apply to them
    (a module reads its own settings via ``env``/its ``register`` hook). Config
    is loaded ONCE. ``config`` (explicit arg) overrides ``root_cls._config_``,
    mirroring ``duho.main``. Precedence stays CLI > env > config > class default,
    and a supplied value un-requires its field, exactly as in ``args.py``.

    ``raw_config`` is the already-loaded TOML table (``app`` loads it once so the
    root layering can also run before the advisory prepass -- see C5).
    """
    # Root fields: top-level keys + root env(NS(env=...)) defaults.
    _apply_default_layers_one(parser, root_cls, raw_config)

    # Class commands: each gets its own [<name>] table applied to its subparser.
    choices = subparsers.choices or {}
    for name, command_cls in class_command_names.items():
        sub_parser = choices.get(name)
        if sub_parser is None:
            continue
        sub_table = raw_config.get(name)
        sub_table = sub_table if isinstance(sub_table, dict) else {}
        _apply_default_layers_one(sub_parser, command_cls, sub_table)
        # Merge the class command's provenance up into the root parser so
        # `value_sources` (which reads the root via `_duho_last_parser_`) sees a
        # config value on a subcommand field instead of mislabeling it (C14).
        parser._duho_value_sources_.update(  # type:ignore[attr-defined]
            getattr(sub_parser, "_duho_value_sources_", {})
        )
        parser._duho_merged_defaults_.update(  # type:ignore[attr-defined]
            getattr(sub_parser, "_duho_merged_defaults_", {})
        )


def app(
    root: "type | None" = None,
    *,
    commands: "_ty.Sequence[_Command] | None" = None,
    source: "str | _Path | None" = None,
    entry_points: "str | None" = None,
    argv: "_ty.Sequence[str] | None" = None,
    name: "str | None" = None,
    description: "str | None" = None,
    env: object = None,
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
    the layered instance construction all apply. When ``setup_logging`` and the
    parsed instance exposes ``_set_loglevels_`` (``LoggingArgs``), stderr logging
    is initialised (unless the root logger already has handlers) and verbosity
    applied -- identical to ``duho.main``.

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
    resolved_commands = _resolve_commands(root, commands, source, env, entry_points)

    parser, base_parser, root_cls = _build_parser(root, name, description)

    # Load the config table ONCE and apply the root-level layers up front, BEFORE
    # the advisory prepass. This lets a required global supplied by config/env
    # reach the prepass parse so it does not hard-exit with a usage error (C5);
    # `_apply_app_config_layers` re-applies it (idempotent) alongside each class
    # command's own table after registration.
    config_path = config if config is not None else getattr(root_cls, "_config_", None)
    config_loader = getattr(root_cls, "_config_loader_", None)
    raw_config: dict = (
        _load_config(config_path, config_loader) if config_path is not None else {}
    )
    _apply_default_layers_one(parser, root_cls, raw_config)

    # A prepass parsed root instance is offered to module ``register`` hooks so a
    # hook that wants the already-parsed globals can read them. It is a
    # best-effort, help-suppressed prepass (nested-help gotcha handled by the
    # existing prerun_parse); most register hooks ignore it and add static args.
    prepass_args: object = None
    if any(_is_module_command(c) for c in resolved_commands):
        try:
            from .parsers import prerun_parse as _prerun_parse

            prepass_args = _prerun_parse(parser, argv)
        except SystemExit:
            # The prepass is advisory (help is disabled inside prerun_parse, so a
            # SystemExit here is never a user-requested --help). A required- or
            # unknown-arg exit must not abort the whole app: degrade to no prepass
            # and let the real parse below report errors authoritatively (C5).
            prepass_args = None
        except Exception:  # pragma: no cover - prepass is advisory only
            prepass_args = None

    # Map each subcommand name to (kind, command) in ONE registry so registration
    # and dispatch agree. A name registered twice (e.g. a module command and a
    # class command sharing a name) warns naming both; the LAST registration wins
    # -- the earlier subparser is deregistered so argparse does not raise
    # `conflicting subparser`, and dispatch resolves via this same registry (M6).
    registry: "dict[str, tuple[str, object]]" = {}

    # A root class with `_subcommands_` already had them registered by its own
    # `_parser_`, which created a subparsers action. argparse allows only one per
    # parser ("cannot have multiple subparser arguments"), so reuse that action
    # rather than adding a second -- otherwise a root with built-ins could not
    # also take discovered commands (CMDS_PATH being additive depends on this).
    # Re-registering a name is safe: `_deregister_subparser` drops the earlier
    # entry so the later one wins.
    subparsers = _existing_subparsers(parser)
    if subparsers is None:
        subparsers = parser.add_subparsers(
            title="command", dest="command", required=True
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
            c for c in resolved_commands
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
        for name, builtin_command in builtin_by_name.items():
            if name in preregistered:
                registry[name] = ("class", builtin_command)
    for command in resolved_commands:
        if _is_class_command(command):
            cmd_name = _command_name(command)
            kind: str = "class"
        elif _is_module_command(command):
            cmd_name = _ty.cast(_ModuleCommand, command)._parsername_
            kind = "module"
        else:  # pragma: no cover - resolver only yields the two kinds
            continue

        if cmd_name in registry:
            prev_kind, prev_obj = registry[cmd_name]
            _LOGGER.warning(
                "duho.app: command name %r registered by more than one source "
                "(%s %r, then %s %r); the last registration wins.",
                cmd_name,
                prev_kind,
                getattr(prev_obj, "__name__", prev_obj),
                kind,
                getattr(command, "__name__", command),
            )
            _deregister_subparser(subparsers, cmd_name)

        if kind == "class":
            command_cls = _ty.cast(type, command)
            _register_class_command(subparsers, command_cls, base_parser)
        else:
            _register_module_command(
                subparsers, _ty.cast(_ModuleCommand, command), base_parser, prepass_args
            )
        registry[cmd_name] = (kind, command)

    # Suppress the root's own optional dests on every registered subparser so an
    # option given BEFORE the subcommand (or supplied by the root env/config
    # layer) is not clobbered by the child's inherited default (C4). This is the
    # `app()` analogue of the suppression `Args._parser_` performs for a static
    # `_subcommands_` tree.
    root_dests = {b.name for b in root_cls._getargs_()}  # type: ignore[attr-defined]
    for sub_parser in (subparsers.choices or {}).values():
        _suppress_inherited_defaults(sub_parser, root_dests)
        # `parents=[base_parser]` copies EVERY root option onto each subparser,
        # including *required* globals. `_suppress_inherited_defaults` skips
        # required actions (correct for the static tree, whose children don't
        # inherit root options as their own actions). Here the root parser owns
        # and enforces the required global; a child must not independently
        # re-require it (which would error even when it was given before the
        # subcommand or supplied by a config/env layer). Suppress + un-require
        # the child's inherited copy so the root's value flows through (C5/C4).
        for action in sub_parser._actions:
            if (
                action.dest in root_dests
                and action.option_strings
                and getattr(action, "required", False)
            ):
                action.required = False
                action.default = _argparse.SUPPRESS

    # Thread env/config-file defaults down the app's command tree (a Cli root's
    # `_config_`, or an explicit `config`, plus each command's NS(env=...)
    # fields). This is app()'s analogue of the `_apply_default_layers` call that
    # `duho.main`/`duho.parse` make; app() resolves commands from sources that
    # aren't reachable via `root._subcommands_`, so it layers against the
    # parsers actually built here. See `_apply_app_config_layers`.
    class_commands_by_name = {
        n: _ty.cast(type, obj) for n, (k, obj) in registry.items() if k == "class"
    }
    _apply_app_config_layers(
        parser, root_cls, subparsers, class_commands_by_name, raw_config
    )

    instance = parser.parse_args(argv)

    # Make the resolved app-wide `Env` reachable from the dispatched command via
    # the sandwich-named `_env_` handle (never a user field). A command reads
    # `self._env_` for app-level settings; None when no env was passed.
    try:
        instance._env_ = env  # type: ignore[attr-defined]
    except (AttributeError, TypeError):  # pragma: no cover - namespaces allow it
        pass

    if setup_logging and hasattr(instance, "_set_loglevels_"):
        root_logger = _logging.getLogger()
        if not root_logger.handlers:
            _duho_logging.init_stderr_logging()
        instance._set_loglevels_()  # type: ignore[attr-defined]

    # Resolve which command was selected. A class command selection yields a
    # constructed instance that IS the command (a Cmd subclass); a module command
    # selection leaves ``instance`` as the root instance and names the module via
    # the ``command`` dest.
    selected_name = getattr(instance, "command", None)
    entry = registry.get(selected_name) if selected_name else None
    module_command = (
        _ty.cast(_ModuleCommand, entry[1])
        if entry is not None and entry[0] == "module"
        else None
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
