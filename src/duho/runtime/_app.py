import argparse as _argparse
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .. import _compat as _compat
from .. import parsers as _parsers
from ..args import Args as _Args, Cmd as _Cmd
from ..args._mcptrigger import _maybe_serve_mcp_trigger as _maybe_serve_mcp_trigger
from ..args._entry import _setup_instance_logging as _setup_instance_logging
from ..discovery import Command as _Command, ModuleCommand as _ModuleCommand

from ._mcpcmd import _build_mcp_command_class, _existing_command_names
from ._parser import _prepare_app_parser
from ._resolve import _resolve_commands
from ._run import run_command
from ._tree import _finalize_command_tree, _register_commands

if _ty.TYPE_CHECKING:  # pragma: no cover - type-checking only
    from ..env import Env as _Env


_LOGGER = _logging.getLogger(__package__)


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
        subparsers = _parsers.find_subparsers(parser)
        if subparsers is None or not subparsers.choices:
            parser.error(
                "no commands are available: none came from commands=, source=, "
                "entry_points= or CMDS_PATH, and the root is not runnable"
            )
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
    mcp: "bool | None" = None,
    mcp_command: "str | bool | None" = None,
    utf8_stdio: "bool | None" = None,
) -> "_ty.Any":
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

    The selected command is dispatched via :func:`run_command`; its return is
    this function's return (success -> ``0``, a ``main`` returning ``2`` ->
    ``2``, and any OTHER non-``None`` value a command returns passes straight
    through unchanged -- hence the ``Any`` return type, not ``int``). Discovery
    is resilient: a single unimportable command drops out with a warning and
    the rest still run.

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

    **MCP launch trigger.** Checked FIRST, before ``argv``
    is parsed or anything else here runs: a ``<PREFIX>MCP``/``<NAME>_MCP``
    environment variable (name derived from ``env``'s prefix, else from
    ``root``/``name``/``argv[0]``/``root``'s class name -- see
    ``duho.args._mcp_env_var_name``) set to ``"stdio"`` serves this app's
    FULL resolved tree (class and module commands alike) as an MCP server
    over stdio instead of running any command, returning the server's own
    exit code. ``mcp=False`` (or ``root``'s own ``_mcp_ = False``) disables
    this trigger entirely -- the variable, if set, is left untouched and a
    normal run proceeds. See :func:`duho.args._maybe_serve_mcp_trigger` for
    the full contract (env var removal, unsupported-transport handling,
    lazy ``duho.mcp`` import).

    **Opt-in MCP subcommand** (``mcp_command``). ``None`` (the
    default) uses ``root``'s own ``_mcp_command_`` class attribute
    (``False`` unless declared); an explicit ``True``/``False``/``str`` here
    wins over it. See :func:`_resolve_mcp_command_name` for the exact
    name/validation rules. When resolved to a name, ``duho.mcp.McpCmd`` is
    registered under it like any other class command -- it goes through the
    SAME collision/override accounting every other resolved command does
    (:func:`_register_commands`), except a collision with an EXISTING name
    is a build-time ``ValueError`` here (an explicit opt-in must not
    silently lose to it), and it requires this app to already have at least
    one other subcommand (a subparsers action that would ONLY ever offer
    ``mcp`` is not a meaningful CLI). ``duho.mcp`` is imported only once a
    name is actually resolved.

    **UTF-8 stdio.** :func:`duho.utf8_stdio` runs FIRST, before the MCP
    launch trigger and before anything else here, unless opted out --
    ``utf8_stdio=False`` here, or ``root``'s own ``_utf8_stdio_ = False``
    when this kwarg is left at its default ``None`` (a bare ``root=None``
    root is treated as opted in, matching ``_mcp_``'s own default-root
    handling). Opted out, duho does not touch stdio at all.
    """
    root_cls_for_mcp = root if root is not None else _Args
    if (
        utf8_stdio
        if utf8_stdio is not None
        else getattr(root_cls_for_mcp, "_utf8_stdio_", True)
    ):
        _compat.utf8_stdio()

    mcp_enabled = mcp if mcp is not None else getattr(root_cls_for_mcp, "_mcp_", True)
    if mcp_enabled:

        def _mcp_core_for_this_app() -> object:
            from .. import mcp as _mcp_module

            return _mcp_module._core_for_app(
                root,
                commands=commands,
                source=source,
                entry_points=entry_points,
                argv=argv,
                name=name,
                description=description,
                env=env,
                config=config,
            )

        served = _maybe_serve_mcp_trigger(
            root_cls_for_mcp, env=env, name=name, core_factory=_mcp_core_for_this_app
        )
        if served is not None:
            return served

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

    mcp_cls = _build_mcp_command_class(
        root,
        mcp_command,
        _existing_command_names(root, resolved_commands),
        has_other_subcommand=bool(resolved_commands)
        or bool(getattr(root, "_subcommands_", None)),
    )
    if mcp_cls is not None:
        resolved_commands = list(resolved_commands) + [mcp_cls]

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

    # Recorded so `duho.mcp.serve_running_app` (called from within a
    # dispatched command -- the whole point of the `mcp_command` subcommand
    # just above, but any command may call it) can serve THIS SAME
    # already-built tree, with no rediscovery: `parser`/`root_cls` and the
    # post-parse dispatch closure (env attach, logging, notices, then `run`)
    # are exactly what this call already resolved. Set only around the
    # actual dispatch step (`_run_app`), never left behind afterward.
    mcp_dispatch = _make_post_parse_dispatch(
        env, root_cls, notices, cmds_path_overridden, run
    )
    token = _compat._MCP_CONTEXT.set(("app", parser, root_cls, mcp_dispatch))
    try:
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
    finally:
        _compat._MCP_CONTEXT.reset(token)


def _make_post_parse_dispatch(
    env: "_Env | None",
    root_cls: type,
    notices: "list[tuple[int, str]]",
    cmds_path_overridden: "set[str]",
    run: "_ty.Callable[[object, object], int]" = run_command,
) -> "_ty.Callable[[object, object], int]":
    """Build a ``dispatch(command, instance) -> int`` closure replicating
    :func:`_run_app`'s POST-parse steps for one already-parsed instance:
    attaching the resolved ``env`` as ``instance._env_``, logging setup
    (unconditionally -- every caller of this closure, MCP serving, wants a
    served command's logging configured regardless of what a NORMAL CLI run
    of this same app would pass as its own ``setup_logging``), and flushing
    the deferred override/collision ``notices`` (once total across every
    call this ONE closure serves, not once per call). Shared by
    :func:`_build_app_core` (``duho.mcp._core_for_app``'s building block)
    and :func:`app` itself (which stashes an equivalent closure in
    ``duho.mcp.serve_running_app``'s context, reusing THIS SAME already-
    resolved ``env``/``notices``/``cmds_path_overridden`` rather than
    rebuilding them).
    """
    logged = False

    def _dispatch(command: object, instance: object) -> int:
        nonlocal logged
        try:
            instance._env_ = env  # type: ignore[attr-defined]
        except (AttributeError, TypeError):  # pragma: no cover - namespaces allow it
            pass
        _setup_instance_logging(instance, True, root_cls)
        if not logged:
            logged = True
            for overridden_name in sorted(cmds_path_overridden):
                _LOGGER.info(
                    "CMDS_PATH command %r overrides the built-in", overridden_name
                )
            for level, message in notices:
                _LOGGER.log(level, message)
        return run(_ty.cast(_Command, command), instance)

    return _dispatch


def _build_app_core(
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
) -> "tuple[_argparse.ArgumentParser, type, _ty.Callable[[object, object], int]]":
    """Build an ``app()`` command tree's parser, WITHOUT parsing ``argv`` or
    dispatching -- the building block :mod:`duho.mcp` needs to serve an
    ``app()``-based CLI's full tree (class AND module commands) over MCP.

    Runs the exact same discovery/parser-build/registration/config-thread-down
    steps :func:`app` itself calls (:func:`_resolve_commands`,
    :func:`_prepare_app_parser`, :func:`_register_commands`,
    :func:`_finalize_command_tree`) -- built ONCE, not per MCP tool call, same
    as a real ``app()`` invocation builds its parser once per process.
    ``argv`` here only feeds the advisory ``register``-hook prepass
    (:func:`_prepare_app_parser`); it is never parsed for real by this
    function -- an MCP tool call parses its own synthesized argv against the
    returned parser instead.

    Returns ``(parser, root_cls, dispatch)``. ``dispatch(command, instance)``
    replicates :func:`_run_app`'s POST-parse steps for one already-parsed
    instance: attaching the resolved ``env`` as ``instance._env_``, logging
    setup (identical to a real ``app()`` run), flushing the deferred
    override/collision notices (once, not once per call), and
    :func:`run_command`. The caller (``duho.mcp``) is responsible for parsing
    argv against ``parser`` and resolving which command to dispatch -- the
    same responsibility split :func:`_run_app` has, just with the parse step
    performed by the caller instead of internally, so a caller can verify
    IDENTITY (which command actually got selected) before ever calling
    ``dispatch`` -- a security-relevant check for MCP, whose arguments are
    LLM-controlled and must never be allowed to silently redirect dispatch to
    an unintended command (see ``duho.mcp``'s own dispatch-identity guard).
    """
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

    _finalize_command_tree(parser, subparsers, root_cls, registry, raw_config)

    dispatch = _make_post_parse_dispatch(env, root_cls, notices, cmds_path_overridden)
    return parser, root_cls, dispatch
