"""RunPath: ordered step commands from a directory of numbered ``.py`` files.

**Opt-in module.** Core duho never imports this; you activate it explicitly with
``import duho.runpath`` (which auto-registers a command provider as an import
side-effect) or with an explicit :func:`register` call. Either way it plugs into
the shipped :func:`duho.register_command_provider` hook and needs *zero*
core changes -- it is the first consumer of that seam.

What it adds
------------

A **RunPath** is a directory whose ``NN-name.py`` files are *steps* run in order:

* each step file ``NN-name.py`` contributes a step named ``name`` with numeric
  ordering key ``NN``;
* a step module may set module-level ``PRIORITY: int`` (overrides ``NN`` for
  ordering) and ``REQUIRED: list[str]`` (names of steps that must run first);
* a step's runnable body is its module-level entrypoint, resolved with the same
  ``main`` > ``run`` > ``call`` precedence discovery uses
  (``duho.discovery._ENTRYPOINT_NAMES``) -- no new convention is invented;
* an entrypoint written 1-arg (``def main(cmd)``, the historical shape) is
  called unchanged; a 2-arg entrypoint (``def main(cmd, ctx)``) additionally
  receives the ``ctx`` produced by the directory's optional ``__main__.py`` (see
  below) -- arity-detected, never guessed from a flag, and ONLY when a
  ``__main__.py`` actually exists (a 2-positional step with no lifecycle file
  keeps its own default for that parameter -- see "The optional __main__.py
  lifecycle" below).
* a step may return ``None`` (success) or a non-zero ``int`` (failure, handled
  exactly like a raised exception -- see "Step return codes" below).

:class:`RunPathCmd` is a ``Cmd`` subclass whose ``__call__`` runs the ordered,
selected steps, logging through ``self._logger_``. Its one CLI field is
``--rcopts`` (``-O``), a comma-separated list of fnmatch selection patterns.

Inheriting your app's shared root (``register(base=...)``)
------------------------------------------------------------

An app built with ``duho.app(root=MyLoggingArgsSubclass, commands=[...])``
threads the root's DATA fields onto every subcommand's parsed instance
(argparse's own ``parents=`` mechanism), but that is namespace-copying, not
class inheritance -- a METHOD declared on the root (like ``LoggingArgs``'s
own ``_logger_``/``_set_loglevels_``) is NOT callable on a class command's
instance unless the built class itself actually derives from that root.

By default every RunPath command this module builds ALSO inherits
``duho.LoggingArgs`` (alongside ``RunPathCmd``), so ``-v``/``_logger_``/
``_set_loglevels_`` work out of the box with zero configuration -- ``-v``
activates DEBUG-level colored stderr logging for a RunPath command exactly
like it does for any other ``LoggingArgs``-based command. Call
``register(base=MyAppRoot)`` once, early (before any RunPath command is
built), if your app's shared root is a custom subclass whose OWN methods you
want every RunPath command to inherit too -- not just its data fields.

A ``base`` that is itself an app ROOT (a ``Cli`` mixin, or any ``Cmd`` with its
own ``__call__``) is handled specially: the built subclass ALWAYS keeps
``RunPathCmd.__call__`` as its own ``__call__`` (set directly in the class
namespace, so it wins over anything ``base`` declares), and the app-root-only
attrs ``_subcommands_``/``_version_``/``_completion_``/``_config_``/
``_distribution_`` are masked back to their neutral defaults on the built
class. Without this, a RunPath command built over your app's own
``class MyApp(LoggingArgs, Cli)`` root would inherit the root's subcommand
tree (turning ``myapp rc`` into "pick a nested subcommand") or the root's own
``__call__`` (replacing the step runner entirely) -- `base` is meant to share
a root's *methods*, never its app-level identity.

The optional ``__main__.py`` lifecycle
------------------------------------

A RunPath directory may define a ``__main__.py`` file -- the same dunder Python
already uses for "this directory's entrypoint" (as in ``python -m package``), so
no new naming convention is invented. It is never treated as a step itself: its
leading ``_`` already excludes it from step discovery (the same guard that skips
any ``_``-prefixed file). **It is imported before any step file** (so its
module-level setup -- e.g. adding a sibling ``lib/`` to ``sys.path`` -- is
already in effect for every step import). It defines up to three module-level
callables, all optional:

* ``init(cmd, logger) -> ctx`` -- called once before any step runs; its return
  value is the ``ctx`` handed to every 2-arg step entrypoint. If ``init``
  raises, the whole run is fatal (log then re-raise unconditionally,
  regardless of ``--rcopts strict`` -- every step depends on ``ctx``, so there
  is no meaningful partial/resilient init);
* ``success(ctx, cmd, logger)`` -- called once, INSIDE the step loop's ``try``
  (before ``finally_``, matching ``discovery.ModuleCommand``'s own
  ``main``-then-``success`` order), and only when no step contributed a
  failure (no raised exception and no non-zero return, resilient or not --
  see "Step return codes");
* ``finally_(ctx, cmd, logger)`` -- called once unconditionally after the step
  loop, success or failure (a plain ``try/finally``); a raising ``finally_`` is
  logged and swallowed, exactly like ``discovery.run_command``'s own guarded
  ``finally_`` call, so a teardown error can never mask the real step failure
  or exit code.

A directory with no ``__main__.py`` behaves byte-identical to before this
lifecycle existed: ``ctx`` is never produced, and every step keeps being called
with just ``self`` regardless of its own declared arity (a step's own second,
possibly-defaulted parameter is simply never populated by RunPath in that
case -- it keeps whatever default the step itself gave it).

Steps and hooks must be synchronous: an ``async def`` step or ``__main__.py``
hook has its coroutine result closed immediately and raises ``TypeError`` --
this is the ONLY place in duho that awaits *anything* other than
``Cmd.__call__`` itself (via ``duho.args._maybe_await``, a separate, documented
seam), so an async step's body silently never running is a loud failure here
instead of a "coroutine was never awaited" warning nobody notices.

Step return codes
------------------

A step's return value follows the same convention ``discovery.ModuleCommand``
uses: ``None`` means success; a non-zero ``int`` means failure. Under strict
(the default) that logs the failure, runs ``__main__.py``'s ``finally_`` hook,
and returns the step's own code directly -- no exception, no traceback, unlike
a step that *raises*, which still propagates. Under resilient it is logged and
the run continues instead. Any other return value is not an exit code and is
ignored. ``RunPathCmd.__call__`` itself returns the WORST of every step's own
code, ranked by magnitude the same way :func:`duho.fanout.run_targets`
aggregates target codes (a raised, swallowed failure contributes ``1``, since
it has no numeric code of its own) -- so a resilient run whose steps all
failed non-fatally still reports that failure via its exit code, instead of
always returning ``0``, and a negative step code (e.g. a step reporting a
signal-like result) is never hidden by an earlier or later ``0``.
``__main__.py``'s ``success`` hook only fires when that aggregate is still
clean (no step failed, strictly or resiliently).

``REQUIRED`` and a failed dependency
--------------------------------------

``REQUIRED`` is more than ordering: if a step's ``REQUIRED`` names a step that
actually ran and failed (raised, or returned non-zero, resilient or not) OR
failed to import, the dependent step is skipped too (never run) rather than
running against a broken prerequisite -- fatality for the SKIPPED dependent
follows its OWN strict setting, same as any other step failure. This does not
apply when a ``REQUIRED`` dependency is simply missing or disabled (pure
presence/selection problems, unchanged -- see "Strict vs. resilient").

Filename-encoded per-step options
----------------------------------

Before a step file's ``NN-name`` prefix is parsed, its stem is checked for a
leading ``!`` and ``:``/``;``-separated option tokens (both stripped first, so
``!02-provision:key.py`` still yields prefix ``02``, name ``provision``):

* a leading ``!`` disables the step by default;
* everything after that is a ``:``- or ``;``-separated list of tokens, each
  ``key`` (true), ``!key`` (false), or ``key=value`` -- ``:`` and ``;`` are
  fully interchangeable (``a:b`` and ``a;b`` parse identically), so a step
  wanting Windows-authorable filenames can freely use ``;`` (``:`` is an
  invalid Windows filename character; ``;`` is valid on both Windows and
  POSIX). Two tokens are recognized specially:

  * ``strict``/``!strict`` -- a step's own default (absent this token) is
    **strict**; ``!strict`` opts that ONE step OUT of strict (its failure is
    logged and resilient even when nothing else changes);
  * ``enable``/``!enable`` -- an explicit alternative to the leading ``!``;
    ``!step1`` and ``step1:!enable`` disable the same step. If BOTH the
    leading ``!`` and an explicit token are somehow present, the TOKEN wins
    (more specific than the whole-name shorthand).

  Any other token is collected but not yet consumed by anything (a
  forward-compatible extension point).

This is the exact SAME token grammar ``--rcopts`` uses per comma-entry (see
below) -- one parser, not two.

Precedence for a step's effective strict setting (each layer overrides the
previous): the step's own filename default (``strict=True`` absent a
``!strict`` token), then a per-pattern ``--rcopts`` ``!strict`` token matching
that step's name -- ONLY when that pattern entry actually carries an explicit
``strict``/``!strict`` token, never guessed from the mere presence of some
OTHER token (``enable``, a ``key=value`` extra, or a bare trailing separator
all leave a step's own strict setting untouched) -- then an EXPLICIT bare
``--rcopts strict``/``!strict`` (no pattern attached -- the RUN-WIDE toggle),
which wins last of all and overrides every step uniformly. This is
symlink-transparent by construction: two symlinks (or plain copies) pointing
at the same physical step file, named differently in two different RunPath
directories, resolve to different effective enabled/strict defaults, because
the parse reads the **directory entry's name**, never the target file's
content.

``BEFORE``/``AFTER`` soft ordering
------------------------------------

Alongside the existing hard ``REQUIRED: list[str]`` (a step and its dependency
must both run; a missing/disabled dep is a warning or, under strict, an error),
a step module may also set:

* ``BEFORE: list[str]`` -- "I run before X, if X is present and enabled" (named
  from the declaring step's own side);
* ``AFTER: list[str]`` -- "I run after X, if X runs" (the mirror direction).

Both are pure ordering hints: a ``BEFORE``/``AFTER`` name that is missing, or
present but disabled, is silently a no-op for ordering -- never a warning
(contrast with ``REQUIRED``, whose missing/disabled-dep warning is unchanged).
``REQUIRED``'s hardness stays a fully independent axis from any step's own
filename modifiers and from the run-wide ``--rcopts strict`` flag. Only
ENABLED, SELECTED steps ever enter the ordering graph or get imported at all --
a disabled or deselected step's module body never runs, and its
``PRIORITY``/``REQUIRED``/``BEFORE``/``AFTER`` can never transitively reorder
an enabled step (consistent with the "silently dropped" rule above).

``--rcopts`` selection
----------------------

``--rcopts`` is a comma-separated list of entries, each an fnmatch pattern
matched against step *names*, optionally followed by ``:``/``;``-separated
option tokens -- the SAME grammar (and the same ``strict``/``enable`` special
tokens) a step's own filename uses, see above:

* a leading ``!`` **disables** matching steps (``!*`` disables everything;
  ``!*,build`` disables all then re-enables ``build``); ``pattern:!enable`` is
  an equivalent, more-explicit spelling of ``!pattern`` (wins if both are
  somehow present on one entry);
* a BARE entry that is exactly ``strict``/``!strict`` (no pattern, no other
  tokens) toggles the RUN-WIDE **strict mode** for the run;
* an entry WITH a pattern AND a ``strict``/``!strict`` token (e.g.
  ``build:!strict``) instead scopes that strict override to steps matching
  ``build`` only, without touching the run-wide flag or any other step.

Later entries win when several match the same step, so ``!*,build`` = "disable
all, then enable ``build``".

Strict vs. resilient
--------------------

Three independent cases, each with its own default:

* **Selection/presence problems** (an ``--rcopts`` pattern matching no step, a
  ``REQUIRED`` name that is missing or present-but-disabled) are **warnings**
  by default; a bare ``--rcopts strict`` (no pattern) makes them errors instead.
* **A step's own failure** (a raised exception, a non-zero return, or a
  skipped run because its own ``REQUIRED`` dependency failed) is **fatal by
  default** -- it aborts the run. A raised exception still propagates as a
  traceback; a non-zero *return* instead ends the run cleanly, returning that
  step's own code (see "Step return codes" above) with no exception raised.
  Opt ONE step out with its filename's ``!strict`` token or a matching
  ``--rcopts name:!strict`` entry; a bare ``--rcopts !strict`` (no pattern)
  makes every step resilient instead, and a bare ``--rcopts strict`` makes
  every step fatal regardless of its own filename token (the run-wide toggle
  wins last).
* **``__main__.py``'s ``init`` failing is always fatal**, regardless of any of
  the above -- every step depends on the ``ctx`` it produces, so there is no
  meaningful partial/resilient init.

All union annotations are quoted, and declared class-attr annotations avoid the
PEP-604 ``|`` operator (``typing.Union``/``Optional`` instead), so the module and
any ``RunPathCmd`` parser build cleanly on Python 3.9.
"""

from __future__ import annotations

import fnmatch as _fnmatch
import heapq as _heapq
import inspect as _inspect
import logging as _logging
import re as _re
import typing as _ty
from pathlib import Path as _Path

from ..args import Arg as _Arg, Cmd as _Cmd, Extend as _Extend
from .. import discovery as _discovery
from .. import presets as _presets
from ..fanout import _worst
from ..logging import log_exception as _log_exception

_LOGGER = _logging.getLogger(__package__)

from ._steps import (
    _STRICT_TOKEN,
    _strict_or_warn,
    _reject_coroutine,
    _Step,
    _TOKEN_SEPARATORS,
    _split_tokens,
    _ENABLED_TOKEN,
    _Opts,
    _parse_file_modifiers,
    _parse_step_filename,
)
from ._load import (
    _iter_step_files,
    is_runpath_dir,
    _normalize_step_names,
    _resolve_priority,
    _load_steps,
    _order_steps,
)
from ._selection import (
    _Selection,
    _validate_selection,
    _validate_required,
)
from ._lifecycle import (
    _LIFECYCLE_FILENAME,
    _Lifecycle,
    _load_lifecycle,
)
from ._adapter import (
    _Keep,
    _KEEP,
    _adapt_step,
    _is_bare_passthrough,
    _step_wants_ctx,
)
from ._command import (
    RunPathCmd,
)

__all__ = ["RunPathCmd", "register", "unregister", "is_runpath_dir"]


#: An app-supplied hook that rewrites a step's entrypoint before it is called.
#: ``None`` (the default) means "call the step exactly as written", which is
#: the behavior every RunPath app had before this existed. Configurable via
#: :func:`register`'s ``step_adapter=`` -- set once per process/app, like
#: :data:`_BASE`, since every RunPath command in one app shares it.
_ADAPTER: _ty.Optional[_ty.Callable[..., object]] = None


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
_BASE: type = _presets.LoggingArgs

#: Attrs that only make sense on an app ROOT (``Cli``'s own sandwich-named
#: config attrs), masked back to a neutral default on every built RunPathCmd
#: subclass: without this, ``register(base=<a Cli app root>)`` made a
#: RunPath command inherit the root's ``_subcommands_``/``_version_``/etc,
#: turning ``myapp rc`` into "pick a nested subcommand" or adding an
#: unintended ``--version`` flag. ``base`` is meant to share a root's
#: METHODS, never its app-level identity.
_MASKED_ROOT_ATTRS: _ty.Dict[str, object] = {
    "_subcommands_": None,
    "_version_": None,
    "_completion_": False,
    "_config_": None,
    "_distribution_": None,
}


def _build_runpath_command(path: _Path, qualname: str) -> type[RunPathCmd]:
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
    namespace: dict[str, object] = {
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
_REGISTERED: _ty.Optional[_ty.Tuple[_ty.Callable, _ty.Callable]] = None


def register(
    base: _ty.Optional[type] = None,
    step_adapter: _ty.Any = _KEEP,
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
    global _REGISTERED, _BASE, _ADAPTER
    if base is not None:
        _BASE = base
    if step_adapter is not _KEEP:
        _ADAPTER = step_adapter
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


# Auto-register on import: importing ``duho.runpath`` is the opt-in activation.
register()
