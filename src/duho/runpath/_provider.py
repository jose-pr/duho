import typing as _ty
from pathlib import Path as _Path
from .. import discovery as _discovery
from .. import presets as _presets

from . import _adapter
from ._adapter import _KEEP
from ._command import RunPathCmd
from ._load import is_runpath_dir

# --------------------------------------------------------------------------
# Provider registration
# --------------------------------------------------------------------------


#: The base class every provider-built RunPathCmd subclass ALSO inherits from
#: (alongside RunPathCmd itself), so an app's LoggingArgs-based root's
#: METHODS (``_logger_``, ``_set_loglevels_``) -- not just its data fields --
#: reach the parsed instance. ``app()``'s ``parents=`` mechanism already
#: copies a root's data fields onto ANY subcommand's parsed namespace, but
#: that is namespace-copying, not class inheritance -- a method exists only
#: if the built class itself derives from it. Defaulting to ``LoggingArgs``
#: matches this module's own long-documented "the usual app shape" (see
#: ``_runpath_logger_``): with no configuration at all, ``-v``/``_logger_``/
#: ``_set_loglevels_`` now work out of the box for every RunPath command.
#: Configurable via :func:`register`'s ``base=`` so an app using a DIFFERENT
#: shared root class (its own ``LoggingArgs`` subclass, or something else
#: entirely) gets that inherited too -- set once per process/app, not
#: per-directory (every RunPath command in one app shares one base).
_BASE: "type" = _presets.LoggingArgs

#: Attrs that only make sense on an app ROOT (``Cli``'s own sandwich-named
#: config attrs), masked back to a neutral default on every built RunPathCmd
#: subclass: without this, ``register(base=<a Cli app root>)`` made a
#: RunPath command inherit the root's ``_subcommands_``/``_version_``/etc,
#: turning ``myapp rc`` into "pick a nested subcommand" or adding an
#: unintended ``--version`` flag. ``base`` is meant to share a root's
#: METHODS, never its app-level identity.
_MASKED_ROOT_ATTRS: "_ty.Dict[str, object]" = {
    "_subcommands_": None,
    "_version_": None,
    "_completion_": False,
    "_config_": None,
    "_distribution_": None,
}


def _build_runpath_command(path: "_Path", qualname: str) -> "type[RunPathCmd]":
    """Provider builder: make a per-directory :class:`RunPathCmd` subclass.

    Binds the resolved directory and a subcommand name (the directory's basename,
    ``_``->``-`` normalized, matching module-command naming) onto a fresh subclass
    so ``CmdBuilder`` gets a ready-to-register command (this is the ONLY way a
    RunPath directory resolves to a command -- ``discover_commands``/
    ``CMDS_PATH``/``app(source=...)`` never consult providers at all; see
    :func:`register`).

    Also inherits :data:`_BASE` (default ``LoggingArgs``, configurable via
    ``register(base=...)``) ALONGSIDE ``RunPathCmd``, so the built class
    actually has ``_logger_``/``_set_loglevels_`` as real inherited methods.
    ``__call__`` is set directly on the built class's own namespace to
    ``RunPathCmd.__call__`` (wins over anything ``_BASE`` declares, regardless
    of MRO order), and the app-root-only attrs in :data:`_MASKED_ROOT_ATTRS`
    are reset to neutral defaults, so a ``base`` that happens to be an app's
    own ``Cli`` root never turns this into anything but the step runner.
    """
    directory = _Path(path)
    name = directory.name.replace("_", "-")
    namespace: "dict[str, object]" = {
        "_runpath_dir_": directory,
        "_parsername_": name,
        "__doc__": "Run the %s step directory." % name,
        "__call__": RunPathCmd.__call__,
    }
    namespace.update(_MASKED_ROOT_ATTRS)
    if getattr(_BASE, "_logger_name_", None) is None:
        # Each step directory logs under its own name unless the base class
        # declares one.
        namespace["_logger_name_"] = name
    return _ty.cast(
        "type[RunPathCmd]",
        type("RunPathCmd_" + name.replace("-", "_"), (_BASE, RunPathCmd), namespace),
    )


#: Records the exact (predicate, builder) pair this module registered, so
#: :func:`unregister` removes *ours* specifically (not merely the newest provider).
_REGISTERED: "_ty.Optional[_ty.Tuple[_ty.Callable, _ty.Callable]]" = None


def register(
    base: "_ty.Optional[type]" = None,
    step_adapter: "_ty.Any" = _KEEP,
) -> None:
    """Register the RunPath command provider (idempotent).

    After this, ``CmdBuilder`` resolves a step directory (a dir with
    ``NN-name.py`` files and no ``__init__.py``) to a :class:`RunPathCmd` --
    this is the only resolution path (``discover_commands``, ``CMDS_PATH``, and
    ``app(source=...)`` glob ``.py`` files directly and never consult command
    providers, so pointed at a step directory they register each step FILE as
    its own single-step ``ModuleCommand`` instead of an ordered RunPath).
    Called automatically (with the default ``base``/``step_adapter``) as a
    side effect of ``import duho.runpath`` -- most apps never need to call it
    at all. Call it explicitly, after the import, when you want a non-default
    ``base``/``step_adapter``; see :func:`unregister` to remove the provider
    entirely instead.

    ``base`` (default ``None`` -> keeps the CURRENT :data:`_BASE`, which is
    ``LoggingArgs`` until changed) sets the class every subsequently-built
    RunPathCmd subclass ALSO inherits from, alongside ``RunPathCmd`` itself --
    call ``register(base=MyAppRoot)`` once, early, if your app's shared root
    is a custom ``LoggingArgs`` subclass (or something else entirely) so its
    methods (not just its data fields) are real, inherited members of every
    RunPath command your app builds. Re-registering with a different ``base``
    on an ALREADY-active provider updates :data:`_BASE` for commands built
    from then on (existing built classes are unaffected -- they were already
    constructed). A ``base`` that is itself an app root (a ``Cli`` mixin or any
    ``Cmd`` with its own ``__call__``) is safe to pass: the built subclass
    always keeps ``RunPathCmd.__call__`` as its own, and the root-only config
    attrs are masked back to neutral defaults (see :data:`_MASKED_ROOT_ATTRS`).

    ``step_adapter`` (default: keep the current value, initially ``None``)
    sets a callable applied to every step's entrypoint just before it runs,
    receiving the entrypoint and returning the callable to call in its place.
    It exists so an app can accept step signatures of its own rather than
    duho's ``(cmd)``/``(cmd, ctx)`` -- an app whose module commands take
    ``run(client, args, logger)`` can let steps be written that way too,
    without every step file importing a decorator::

        def adapter(entrypoint):
            if not getattr(entrypoint, "_wants_app_shape_", False):
                return entrypoint          # leave duho-native steps alone
            def call(cmd, ctx=None):
                return entrypoint(ctx, cmd, cmd._logger_)
            return call

        register(base=MyAppRoot, step_adapter=adapter)

    The *adapted* callable is what arity detection inspects, so a wrapper is
    free to change the signature (including one that uses ``@functools.wraps``
    -- see :func:`_step_wants_ctx`). Pass ``None`` to clear it and go back to
    calling steps exactly as written; omit the argument entirely to leave it
    unchanged. Unlike ``base``, this affects every RunPath command in the
    process immediately, including already-built classes -- it is consulted per
    step run, not at class-build time.
    """
    global _REGISTERED, _BASE
    if base is not None:
        _BASE = base
    if step_adapter is not _KEEP:
        _adapter._ADAPTER = step_adapter
    if _REGISTERED is not None:
        return
    pair = (is_runpath_dir, _build_runpath_command)
    _discovery.register_command_provider(*pair)
    _REGISTERED = pair


def unregister() -> None:
    """Remove the RunPath provider this module registered (idempotent).

    The counterpart to :func:`register` -- essential for test isolation so
    provider state does not leak between tests. Removes the specific
    ``(predicate, builder)`` pair registered by this module; a no-op if not
    currently registered.
    """
    global _REGISTERED
    if _REGISTERED is None:
        return
    _discovery.unregister_command_provider(*_REGISTERED)
    _REGISTERED = None
