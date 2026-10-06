import argparse as _argparse
import logging as _logging
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from .. import _compat as _compat
from .. import logging as _duho_logging
from .._layers import _apply_layers as _apply_layers

from ._argsclass import Args, _duho_instance_last_parser_
from ._mcptrigger import _maybe_serve_mcp_trigger
from ._meta import _A
from ._naming import _app_name
from ._parserfix import _argv_before_subcommand


def _logger_name_for(instance, root_cls: "type | None" = None) -> str:
    """The logger a parsed command's ``-v``/``-q`` verbosity applies to.

    In order: a ``_logger_name_`` on the instance's own class (declared or
    inherited), then one on the root class that dispatched it, then that root
    parser's ``prog`` (the application's name, see :func:`_app_name`). An
    instance no parser produced falls back to the application name of
    ``root_cls``, else of its own class.
    """
    own = getattr(type(instance), "_logger_name_", None)
    if own:
        return own
    parser = _duho_instance_last_parser_.get(id(instance))
    root = getattr(parser, "_duho_cls_", None) or root_cls
    declared = getattr(root, "_logger_name_", None) if root is not None else None
    if declared:
        return declared
    if parser is not None:
        return parser.prog
    return _app_name(root_cls or type(instance))


def _setup_instance_logging(
    instance, setup_logging: bool, root_cls: "type | None" = None
) -> None:
    """Initialize stderr logging + apply verbosity for a parsed instance:
    the identical block ``duho.main`` and ``duho.app`` each ran
    inline, now shared by both entry points.

    A no-op unless `setup_logging` is true. Prefers the parsed instance's own
    ``_set_loglevels_`` (present when it mixes in ``LoggingArgs``). When the
    DEEPEST selected class is a plain ``Cmd`` with no such method, but
    `root_cls` -- the class `duho.main`/`duho.app` were actually called with
    -- IS a ``LoggingArgs``, the verbosity fields are still on `instance`
    (argparse copies the parent parser's parsed values onto the shared
    instance regardless of which class gets constructed); apply them under
    the application's logger (see :func:`_logger_name_for`) via the module-level
    :func:`duho.presets._apply_loglevels` instead of the missing bound method.
    This is the documented ``class MyApp(LoggingArgs, Cli)`` +
    plain ``Cmd`` leaves shape from the README, which previously left
    ``-v``/``-q``/``--loglevel`` silently doing nothing.

    ``init_stderr_logging()`` is only called when the root logger has no
    handlers OTHER than duho's own previously-installed one (the
    ``_STDERR_HANDLER_TAG``-marked handler `init_stderr_logging` itself
    tracks) -- matching 0.5.4's guard, which never added a stderr handler to
    an app/harness that already owns logging (``basicConfig``, pytest's
    capture handler, etc.). A root with only duho's own handler (a second
    dispatch in the same process, or a repeat call) still calls it, since
    `init_stderr_logging` is itself idempotent against its own handler; the
    guard here is what keeps duho from ever adding a SECOND handler
    alongside a foreign one.
    """
    if not setup_logging:
        return
    setter = getattr(instance, "_set_loglevels_", None)
    if setter is None and root_cls is not None:
        from .. import presets as _presets

        if issubclass(root_cls, _presets.LoggingArgs):
            logger_name = _logger_name_for(instance, root_cls)
            setter = lambda: _presets._apply_loglevels(instance, logger_name, root_cls)
    if setter is None:
        return
    root_handlers = _logging.getLogger().handlers
    if not any(
        not getattr(h, _duho_logging._STDERR_HANDLER_TAG, False) for h in root_handlers
    ):
        _duho_logging.init_stderr_logging()
    setter()


def _maybe_await(result):
    """Drive a coroutine result to completion, returning its value.

    A ``Cmd.__call__`` (or a ``duho.main`` target) may be ``async def``; its
    invocation returns a coroutine. This runs it with ``asyncio.run`` at the
    call site so the awaited value becomes the command's result/exit code, and
    passes any non-coroutine result through unchanged.

    ``asyncio`` is imported lazily here (not at module top) so a plain
    ``import duho`` never pays its import cost -- only a command that actually
    returns a coroutine triggers the load (startup budget).

    Gates on ``inspect.isawaitable`` (not the narrower
    ``inspect.iscoroutine``, which only recognizes NATIVE coroutine objects)
    so a Cython-/mypyc-compiled ``async def`` -- whose result registers under
    ``collections.abc.Coroutine`` without being a ``types.CoroutineType`` --
    is still driven to completion instead of silently returned as-is (and
    then, e.g., used as a process exit code). An ``async def`` written as an
    async GENERATOR (``yield`` instead of ``return``) is rejected outright:
    it is not awaitable at all, so it would otherwise pass through unchanged
    and be returned as the command's result. Also refuses to nest
    ``asyncio.run`` inside an event loop that is already running (e.g. inside
    Jupyter, or an async host such as an MCP server) with a message naming
    the fix, instead of a bare ``asyncio`` internals error plus a leaked
    "coroutine was never awaited" warning.
    """
    import inspect as _inspect

    if _inspect.isasyncgen(result):
        raise TypeError(
            "a Cmd.__call__ (or duho.main target) must not be an async "
            "generator; write a plain `async def` that returns a value "
            f"instead of one that `yield`s (got {result!r})"
        )
    if not _inspect.isawaitable(result):
        return result

    import asyncio as _asyncio

    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "duho: an async command returned an awaitable, but an asyncio "
            "event loop is already running -- duho.main/run_command/"
            "call_tool cannot nest asyncio.run() inside one. Await the "
            "command yourself (`await cmd()`), or run the sync entry point "
            "via `asyncio.to_thread(...)`."
        )

    if _asyncio.iscoroutine(result):
        return _asyncio.run(result)

    async def _await_result(awaitable=result):
        return await awaitable

    return _asyncio.run(_await_result())


def main(
    cls: "type[Args]",
    argv: "_ty.Sequence[str] | None" = None,
    *,
    setup_logging: bool = True,
    config: "str | _pathlib.Path | None" = None,
    utf8_stdio: "bool | None" = None,
) -> "_ty.Any":
    """Build a parser for cls, parse argv, and dispatch the selected Cmd.

    Module-level (not a classmethod) so the Args subclass namespace stays
    entirely user-owned. Steps: build parser (auto-registers _subcommands_),
    apply the env/config/class-default layers (`config` overrides `cls._config_`;
    precedence CLI > env > config > class default), parse argv (SystemExit from
    argparse propagates), optionally set up stderr logging + apply verbosity,
    then run the command and map a None return to 0. Logging setup runs when
    the resulting instance provides `_set_loglevels_` directly, OR -- when the
    selected leaf is a plain `Cmd` under a `LoggingArgs` root -- when `cls`
    itself is a `LoggingArgs` (see `_setup_instance_logging`).

    Since the Args/Cmd split, dispatch expects the selected class to be
    a ``Cmd`` (executable, defines ``__call__``). A bare data ``Args`` -- with
    no ``__call__`` -- raises a clear ``NotImplementedError`` ("Args holds data;
    make it a Cmd to run it") rather than silently doing nothing.

    Typed ``-> Any``, not ``-> int``: a ``None`` result maps to ``0``, but
    any OTHER value the command returns (an ``int``, or anything else destined
    for ``sys.exit``) passes straight through unchanged. ``Any`` (rather than
    ``object``) keeps ``sys.exit(duho.main(...))`` clean under a strict-mypy
    consumer, since ``sys.exit`` does not accept ``object``.

    **UTF-8 stdio**: :func:`duho.utf8_stdio` runs FIRST, before the MCP
    launch trigger and before ``argv`` is parsed, unless opted out --
    ``utf8_stdio=False`` here, or ``cls``'s own ``_utf8_stdio_ = False``
    when the kwarg is left at its default ``None``. Opted out, duho does not
    touch stdio at all (the app may call :func:`duho.utf8_stdio` itself).

    **MCP launch trigger**: checked next, before ``argv``
    is even parsed -- see :func:`_maybe_serve_mcp_trigger`. When the trigger
    fires this returns the MCP server's own exit code instead of running any
    command; otherwise nothing about the rest of this function changes.

    **Opt-in MCP subcommand** (``cls``'s own ``_mcp_command_``, mirroring
    ``duho.app(..., mcp_command=...)`` -- there is no separate kwarg here,
    since ``main`` has no other command-source parameters to sit next to).
    ``False`` (the default): unchanged behavior, and ``duho.mcp`` is never
    imported. Otherwise a fresh ``duho.mcp.McpCmd`` subclass is registered
    as an extra top-level subcommand under the resolved name, going through
    the exact same resolution/validation
    (:func:`duho.runtime._resolve_mcp_command_name`) and collision/leaf
    checks (:func:`duho.runtime._build_mcp_command_class`) ``app()`` uses,
    so the ``ValueError`` messages match. Running ``<prog> <name>`` serves
    ``cls``'s own static tree (excluding that subcommand itself) over
    stdio via :func:`duho.mcp.serve_running_app`, through the same
    ``_MCP_CONTEXT`` ContextVar ``app()`` sets.
    """
    if utf8_stdio if utf8_stdio is not None else getattr(cls, "_utf8_stdio_", True):
        _compat.utf8_stdio()

    served = _maybe_serve_mcp_trigger(cls)
    if served is not None:
        return served

    root_cls = cls
    extra_cmds: "list[type]" = []
    completion_cls = None
    if (
        getattr(cls, "_mcp_command_", False) is not False
        or getattr(cls, "_completion_command_", False) is not False
    ):
        # Lazy: `duho.runtime` (and, transitively, `duho.mcp`/`duho.completion`)
        # is imported only when a class attribute is anything other than the
        # literal `False` default -- an explicit empty string must still reach
        # the builders' validation and raise, exactly like `app()`'s own
        # unconditional call does, so this is `is not False`, not a truthiness
        # check (`""` is falsy but NOT a valid opt-out). A `main` call with the
        # defaults never pays for either import.
        from .. import runtime as _runtime

        has_other = bool(getattr(cls, "_subcommands_", None))
        mcp_cls = _runtime._build_mcp_command_class(
            cls,
            None,
            _runtime._existing_command_names(cls, ()),
            has_other_subcommand=has_other,
        )
        if mcp_cls is not None:
            extra_cmds.append(mcp_cls)
        completion_cls = _runtime._build_completion_command_class(
            cls,
            _runtime._existing_command_names(cls, ())
            | {c._parsername_ for c in extra_cmds},
            has_other_subcommand=has_other,
        )
        if completion_cls is not None:
            extra_cmds.append(completion_cls)
    if extra_cmds:
        # A fresh subclass of `cls` carrying the extra subcommands, built
        # fresh per call (never mutating `cls` itself, which would leak across
        # calls/threads) -- mirrors `duho.app`'s own per-call synthesis.
        # `_MCP_CONTEXT` below is set to `("class", cls)` -- the ORIGINAL
        # class, not this subclass -- so a nested `serve_running_app()` call
        # re-serves `cls`'s own tree, which never included the injected
        # subcommands to begin with (no separate exclusion logic needed).
        extra_attrs: "dict[str, object]" = {
            "__module__": cls.__module__,
            "__qualname__": cls.__qualname__,
            "_subcommands_": list(getattr(cls, "_subcommands_", None) or ())
            + extra_cmds,
            "_duho_constants_": {},
            "__doc__": cls.__doc__,
        }
        own_parsername = vars(cls).get("_parsername_")
        if own_parsername is not None:
            extra_attrs["_parsername_"] = own_parsername
        root_cls = type(cls.__name__, (cls,), extra_attrs)
        if completion_cls is not None:
            # The script describes the tree including its own subcommand.
            completion_cls._completion_tree_ = root_cls

    parser = root_cls._parser_(_inherited_config_hint_=config is not None)
    _apply_layers(parser, root_cls, config=config)
    instance = parser.parse_args(argv)

    _setup_instance_logging(instance, setup_logging, cls)

    run = getattr(instance, "__call__", None)
    if run is None:
        raise NotImplementedError(
            f"{type(instance).__name__} holds data but is not runnable "
            f"(no '__call__'); make it a Cmd (subclass duho.Cmd or "
            f"build one with duho.command(...)) to run it"
        )

    # Recorded so `duho.mcp.serve_running_app` (called from within a
    # dispatched command, e.g. a `duho.mcp.McpCmd` an app registered under
    # its own name) can serve THIS SAME already-built class tree -- reusing
    # `cls` costs nothing here; `duho.mcp._core_for_class(cls)` reuses its
    # own tree cache when it's actually asked for.
    token = _compat._MCP_CONTEXT.set(("class", cls))
    try:
        result = _maybe_await(run())
    finally:
        _compat._MCP_CONTEXT.reset(token)
    return 0 if result is None else result


def parse(
    spec: "type[_A] | _A",
    argv: "_ty.Sequence[str] | None" = None,
    *,
    parser_kwargs: "_ty.Optional[_ty.Mapping[str, object]]" = None,
    config: "str | _pathlib.Path | None" = None,
) -> "_A":
    """Build a parser from `spec` and parse `argv` into a new instance.

    `spec` may be:
    - An `Args` subclass (type): equivalent to `spec._parser_().parse_args(argv)`,
      with the env/config/class-default layers applied first (see below).
    - An instance of an `Args` subclass: the field values the caller EXPLICITLY
      passed to its `__init__` (not a placeholder `Args.__init__` itself seeded
      for an omitted field -- a bare `bool`/collection field left at its own
      default is not "an instance value" just because it materializes onto
      `vars(spec)`) are used as argparse defaults. CLI args still override
      those defaults. Returns a NEW instance of `type(spec)`; `spec` itself is
      never mutated.

    `config` (a path, or None to fall back to `cls._config_`) layers config-file
    and environment-variable defaults under the instance/CLI ones. Full
    precedence: CLI args > instance field values > env > config file > class
    defaults. Note this means a required field (no class default) that is
    supplied by *any* layer becomes effectively optional for this call.
    `duho.value_sources` reports such a field as ``"instance"``.
    """
    parser_kwargs = parser_kwargs or {}
    if isinstance(spec, type):
        cls = spec
        parser = cls._parser_(
            **parser_kwargs, _inherited_config_hint_=config is not None
        )
        _apply_layers(parser, cls, config=config)
        return parser.parse_args(argv)

    cls = type(spec)
    parser = cls._parser_(**parser_kwargs, _inherited_config_hint_=config is not None)
    _apply_layers(parser, cls, config=config, instance=spec)
    return parser.parse_args(argv)


def parse_globals(
    cls: "type[_A]",
    argv: "_ty.Sequence[str] | None" = None,
    *,
    config: "str | _pathlib.Path | None" = None,
    **parser_kwargs: object,
) -> "_A":
    """Parse ONLY a root command's global args, ignoring/relaxing subcommands.

    Builds ``cls``'s root parser (``cls._parser_(**parser_kwargs)``), applies
    the same env/config/class-default layers ``duho.main``/``duho.parse`` do
    (``config`` overrides ``cls._config_``; precedence CLI > env > config >
    class default), and parses ``argv`` with help/version/print-
    completion suppressed and subcommand descent skipped entirely, so a
    consumer can resolve config-file-driven command search paths (or any
    other global, including one backed by ``NS(env=...)`` or only made
    non-required by a layer) BEFORE building/committing to the full
    subcommand parser. This is the documented, public form of the internal
    prepass ``duho.app`` already runs -- it wraps
    :func:`duho.parsers.prerun_parse` verbatim rather than reimplementing the
    subparser-detach/terminal-action patching it performs and restores in a
    ``finally`` (this used to duplicate a buggy, dead-branch version of
    that same detach here; ``prerun_parse`` now does it once, correctly, for
    every caller).

    Returns the parsed root instance with globals set. Subcommand arguments are
    NOT validated in this pass: a missing subcommand does not error, and an
    unknown trailing token does not crash the globals parse (it is simply
    ignored here). A caller that also wants the leftover argv should call
    ``parser.parse_known_args`` directly -- ``parse_globals`` deliberately
    returns a single value (the globals-only instance), mirroring the shape
    ``prerun_parse`` yields.

    ``**parser_kwargs`` are forwarded to ``cls._parser_`` (e.g. ``add_help=False``),
    mirroring :func:`duho.parser`.
    """
    from ..parsers import prerun_parse as _prerun_parse

    parser = cls._parser_(**parser_kwargs, _inherited_config_hint_=config is not None)
    _apply_layers(parser, cls, config=config)
    # A static tree's real parse hands everything after the subcommand name to
    # the subcommand, so a root option written there is not a global.
    argv = _argv_before_subcommand(
        parser, list(_sys.argv[1:] if argv is None else argv)
    )
    return _prerun_parse(parser, argv)


def finish_parse(namespace: "_argparse.Namespace") -> "Args":
    """Build the selected ``Cmd``/``Args`` instance from a ``Namespace``
    produced by attaching a duho subparser to a PLAIN, non-duho argparse
    parser -- the documented "manual subparsers" recipe
    (``Serve._parser_(subparsers, name="serve")`` attached to a
    hand-built ``argparse.ArgumentParser()`` root).

    A duho root (``Args._parser_``'s own patched ``parse_known_args``) does
    this automatically as part of normal dispatch: the internal ``"#cls"``
    marker (recording which subcommand class argparse selected) is popped and
    used to construct the real instance. A PLAIN argparse root has no such
    hook, so ``root.parse_args(...)`` returns a raw ``Namespace`` still
    carrying ``"#cls"`` -- not a ``Serve`` instance, with no ``_passthrough_``
    and no methods, and a stray ``"#cls"`` key that breaks
    ``func(**vars(args))``/``json.dumps(vars(args))``. Call this once, right
    after ``parse_args``, to get the real instance the manual recipe was
    always meant to produce.

    Raises ``ValueError`` if `namespace` carries no ``"#cls"`` marker (e.g. no
    duho subparser was ever reached for this invocation).
    """
    ns = vars(namespace)
    if "#cls" not in ns:
        raise ValueError(
            "finish_parse: no duho command was selected -- 'namespace' has "
            "no '#cls' marker. Pass the Namespace argparse.parse_args() "
            "returned when a duho `_parser_(subparsers, ...)` subcommand was "
            "attached to a plain argparse parser."
        )
    cls = ns.pop("#cls")
    # Drop the `_CollectionAction`/`UpdateAction` sidecars
    # (`_duho_items_<dest>`/`_duho_dict_seen_<dest>`) the same way the
    # internal `parse_known_args` patch does before constructing an instance
    # -- this manual-recipe path builds the instance itself, so it must strip
    # them itself too, or this bookkeeping leaks into vars(instance) and the
    # documented `type(self)(**self._get_kwargs())` clone pattern.
    for sidecar in [
        k
        for k in ns
        if k.startswith("_duho_items_") or k.startswith("_duho_dict_seen_")
    ]:
        del ns[sidecar]
    ns.pop("_duho_command_", None)
    return cls(**ns)
