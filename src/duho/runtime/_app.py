from __future__ import annotations

import argparse as _argparse
import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from .. import _compat as _compat
from .. import parsers as _parsers
from ..args import Args as _Args, Cmd as _Cmd
from ..args._mcptrigger import _maybe_serve_mcp_trigger as _maybe_serve_mcp_trigger
from ..args._entry import _class_config_location as _class_config_location
from ..args._entry import _setup_instance_logging as _setup_instance_logging
from ..discovery import Command as _Command, ModuleCommand as _ModuleCommand

from ._completioncmd import _build_completion_command_class
from ._mcpcmd import _build_mcp_command_class, _existing_command_names
from ._parser import _prepare_app_parser
from ._resolve import _resolve_commands
from ._run import run_command
from ._tree import _finalize_command_tree, _register_commands

from ..env import Env as _Env

_LOGGER = _logging.getLogger(__package__)


def _run_app(
    parser: _argparse.ArgumentParser,
    argv: _ty.Sequence[str] | None,
    env: _Env | None,
    setup_logging: bool,
    root_cls: type,
    required_root_actions: list[_argparse.Action],
    cmds_path_overridden: set[str],
    notices: list[tuple[int, str]],
    run: _ty.Callable[[_Command, object], _ty.Any],
) -> _ty.Any:
    """Parse ``argv``, finish per-invocation setup, and dispatch one command.

    Covers the real ``parse_args``, the required-global re-check, ``_env_``,
    logging setup, the deferred notices, and running the selected command.
    """
    instance = parser.parse_args(argv)

    # A required global un-required earlier must still have a value from the
    # root, a child, or a config/env layer.
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

    # The app-wide `Env` is reachable as `self._env_` (never a user field);
    # `None` when no env was passed.
    try:
        instance._env_ = env  # type: ignore[attr-defined]
    except (AttributeError, TypeError):  # pragma: no cover - namespaces allow it
        pass

    _setup_instance_logging(instance, setup_logging, root_cls)

    # Flushed here because an earlier INFO would be lost: no handler exists yet and
    # `logging.lastResort` prints only WARNING and above. The CMDS_PATH override is
    # INFO; any other collision is WARNING, so the intended override stays quiet.
    for overridden_name in sorted(cmds_path_overridden):
        _LOGGER.info("CMDS_PATH command %r overrides the built-in", overridden_name)
    for level, message in notices:
        _LOGGER.log(level, message)

    # A class command yields an instance that is the command. A module command
    # leaves the root instance, marked by the private `_duho_module_command_` its
    # subparser set, not by the shared `_duho_command_` dest, which a nested tree
    # or a root field named `command` could redirect. `pop` keeps the marker out
    # of `vars(instance)`.
    module_command = _ty.cast(
        "_ModuleCommand | None", vars(instance).pop("_duho_module_command_", None)
    )

    if module_command is not None:
        # `run_command` owns the lifecycle; building the context here would run
        # `init` twice. A custom `dispatch` replaces this step.
        return run(module_command, instance)

    # Class command, or a runnable root: the instance is the deepest selected Cmd.
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


def _default_run(
    dispatch: _ty.Optional[_ty.Callable[[_Command, object], _ty.Any]],
    adapter: _ty.Optional[_ty.Callable[..., object]],
) -> _ty.Callable[[_Command, object], _ty.Any]:
    """The final run step: ``dispatch``, else :func:`run_command` bound to ``adapter``."""
    if dispatch is not None:
        if adapter is not None:
            raise ValueError(
                "app(): adapter= applies to the default run step; with dispatch= "
                "pass adapter to run_command() from the dispatch callable"
            )
        return dispatch
    if adapter is None:
        return run_command
    return lambda command, instance: run_command(command, instance, adapter=adapter)


def app(
    root: _ty.Optional[type] = None,
    *,
    commands: _ty.Optional[_ty.Sequence[_Command]] = None,
    source: _ty.Union[str, _Path, _ty.Sequence[_ty.Union[str, _Path]], None] = None,
    entry_points: _ty.Optional[str] = None,
    argv: _ty.Optional[_ty.Sequence[str]] = None,
    name: _ty.Optional[str] = None,
    description: _ty.Optional[str] = None,
    env: _ty.Optional[_Env] = None,
    config: _ty.Optional[_ty.Union[str, _Path]] = None,
    setup_logging: bool = True,
    dispatch: _ty.Optional[_ty.Callable[[_Command, object], _ty.Any]] = None,
    mcp: _ty.Optional[bool] = None,
    mcp_command: _ty.Optional[_ty.Union[str, bool]] = None,
    utf8_stdio: _ty.Optional[bool] = None,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
    adapter: _ty.Optional[
        _ty.Callable[
            [_ty.Callable[..., object]], _ty.Optional[_ty.Callable[..., object]]
        ]
    ] = None,
) -> _ty.Any:
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

    ``source`` may also be a list or tuple of sources (a later source replaces
    an earlier command of the same name; see :func:`~duho.discovery.discover_commands`).
    ``on_error(source, exc)`` is the per-command error policy: it is passed to
    discovery (``source`` is then the file or module that failed) and called
    when registering one command raises (``source`` is then the command); returning
    skips that command, raising aborts. ``None`` keeps the default: discovery skips
    only ``ImportError``/``NotImplementedError`` with a warning and registration
    errors propagate.

    ``adapter(entrypoint)`` is applied to a module command's entrypoint by the
    default run step (see :func:`run_command`). It cannot be combined with
    ``dispatch``, which replaces that step: a custom dispatch passes ``adapter``
    to :func:`run_command` itself (``ValueError`` otherwise).

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
                dispatch=dispatch,
                on_error=on_error,
                adapter=adapter,
            )

        served = _maybe_serve_mcp_trigger(
            root_cls_for_mcp, env=env, name=name, core_factory=_mcp_core_for_this_app
        )
        if served is not None:
            return served

    if config is None and root is not None:
        config = _class_config_location(root, argv)

    run = _default_run(dispatch, adapter)
    # Names CMDS_PATH overrode, logged by `_run_app` once logging is set up; also
    # lets `_register_commands` treat a collision on that name as this override.
    cmds_path_overridden: set[str] = set()
    resolved_commands = _resolve_commands(
        root,
        commands,
        source,
        env,
        entry_points,
        overridden=cmds_path_overridden,
        on_error=on_error,
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

    completion_cls = _build_completion_command_class(
        root,
        _existing_command_names(root, resolved_commands),
        has_other_subcommand=bool(resolved_commands)
        or bool(getattr(root, "_subcommands_", None)),
    )
    if completion_cls is not None:
        resolved_commands = list(resolved_commands) + [completion_cls]

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
        on_error=on_error,
    )

    required_root_actions = _finalize_command_tree(
        parser, subparsers, root_cls, registry, raw_config
    )

    # Lets `duho.mcp.serve_running_app`, called from a dispatched command, serve
    # this already-built tree. Set only around the dispatch.
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
    env: _Env | None,
    root_cls: type,
    notices: list[tuple[int, str]],
    cmds_path_overridden: set[str],
    run: _ty.Callable[[object, object], int] = run_command,
) -> _ty.Callable[[object, object], int]:
    """Build a ``dispatch(command, instance) -> int`` closure for the post-parse steps.

    Attaches ``env`` as ``instance._env_``, sets up logging unconditionally (MCP
    serving wants it whatever a CLI run would pass), flushes the deferred
    ``notices`` once per closure, and runs the command. Shared by
    :func:`_build_app_core` and :func:`app`.
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
    root: type | None = None,
    *,
    commands: _ty.Sequence[_Command] | None = None,
    source: _ty.Union[str, _Path, _ty.Sequence[_ty.Union[str, _Path]], None] = None,
    entry_points: str | None = None,
    argv: _ty.Sequence[str] | None = None,
    name: str | None = None,
    description: str | None = None,
    env: _Env | None = None,
    config: str | _Path | None = None,
    dispatch: _ty.Callable[[_Command, object], _ty.Any] | None = None,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
    adapter: _ty.Optional[
        _ty.Callable[
            [_ty.Callable[..., object]], _ty.Optional[_ty.Callable[..., object]]
        ]
    ] = None,
) -> tuple[_argparse.ArgumentParser, type, _ty.Callable[[object, object], int]]:
    """Build an ``app()`` command tree's parser without parsing ``argv`` or dispatching.

    The building block :mod:`duho.mcp` uses to serve a full tree, built once.
    ``argv`` only feeds the advisory ``register`` prepass.

    Returns ``(parser, root_cls, dispatch)``. ``dispatch`` is the post-parse
    closure of :func:`_make_post_parse_dispatch`. The caller parses against
    ``parser`` and checks which command was selected before dispatching, since
    MCP arguments are model-controlled and must not redirect dispatch.
    """
    cmds_path_overridden: set[str] = set()
    resolved_commands = _resolve_commands(
        root,
        commands,
        source,
        env,
        entry_points,
        overridden=cmds_path_overridden,
        on_error=on_error,
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
        on_error=on_error,
    )

    _finalize_command_tree(parser, subparsers, root_cls, registry, raw_config)

    run = _default_run(dispatch, adapter)
    post_parse = _make_post_parse_dispatch(
        env, root_cls, notices, cmds_path_overridden, run
    )
    return parser, root_cls, post_parse
