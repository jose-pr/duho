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
uses: ``None`` means success; a non-zero ``int`` means failure, handled through
the exact same ``--rcopts``/filename strict-vs-resilient path as a raised
exception (see "Strict vs. resilient" below) -- under strict it aborts the run,
otherwise it is logged and the run continues. Any other return value is not an
exit code and is ignored. ``RunPathCmd.__call__`` itself returns the MAXIMUM
of every step's own code (a raised, swallowed failure contributes ``1``, since
it has no numeric code of its own) -- so a resilient run whose steps all
failed non-fatally still reports that failure via its exit code, instead of
always returning ``0``. ``__main__.py``'s ``success`` hook only fires when
that aggregate is still clean (no step failed, strictly or resiliently).

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
  default** -- it aborts the run. Opt ONE step out with its filename's
  ``!strict`` token or a matching ``--rcopts name:!strict`` entry; a bare
  ``--rcopts !strict`` (no pattern) makes every step resilient instead, and a
  bare ``--rcopts strict`` makes every step fatal regardless of its own
  filename token (the run-wide toggle wins last).
* **``__main__.py``'s ``init`` failing is always fatal**, regardless of any of
  the above -- every step depends on the ``ctx`` it produces, so there is no
  meaningful partial/resilient init.

All union annotations are quoted, and declared class-attr annotations avoid the
PEP-604 ``|`` operator (``typing.Union``/``Optional`` instead), so the module and
any ``RunPathCmd`` parser build cleanly on Python 3.9.
"""

import fnmatch as _fnmatch
import heapq as _heapq
import inspect as _inspect
import logging as _logging
import re as _re
import typing as _ty
from pathlib import Path as _Path

from .args import Arg as _Arg, Cmd as _Cmd, Extend as _Extend
from . import discovery as _discovery
from . import presets as _presets
from .logging import log_exception as _log_exception

__all__ = ["RunPathCmd", "register", "unregister", "is_runpath_dir"]

_LOGGER = _logging.getLogger(__name__)

#: The token in ``--rcopts`` that toggles strict mode (``strict`` enables,
#: ``!strict`` disables). A bare ``strict`` is a run-wide marker, not a step name.
_STRICT_TOKEN = "strict"


def _strict_or_warn(
    message: str,
    strict: bool,
    logger: "_logging.Logger",
    warn_suffix: str = "",
) -> None:
    """Raise ``ValueError(message)`` if ``strict``, else log it as a warning.

    Consolidates the repeated "raise under strict, else warn" pattern used by
    every RunPath validation site (an unmatched ``--rcopts`` pattern, a
    missing/disabled ``REQUIRED`` dep, a duplicate step name, an invalid
    ``PRIORITY``, a dependency-cycle break, a dependent skipped because its own
    ``REQUIRED`` step failed, a step's own non-zero return) so these near-
    identical copies can't drift apart from each other. ``warn_suffix``
    is appended only on the warning path (some callers want extra detail there
    that would be redundant in the raised message).
    """
    if strict:
        raise ValueError(message)
    logger.warning(message + warn_suffix)


def _reject_coroutine(result: object, where: str) -> None:
    """Refuse a coroutine ``result`` from RunPath user code.

    RunPath predates async support and never awaits anything itself; the only
    place duho ever awaits user code is ``Cmd.__call__`` (via
    ``duho.args._maybe_await``), a separate, documented seam. Before this, an
    ``async def`` step or ``__main__.py`` hook silently produced a coroutine
    that nothing ever awaited -- its body never ran, and the only sign of
    trouble was a "coroutine was never awaited" ``RuntimeWarning`` raised from
    ``__del__`` (invisible under ``-W ignore``/``PYTHONWARNINGS=ignore``, and
    never an error). This turns that into an immediate, loud failure instead,
    routed through the normal step/hook strict-vs-resilient handling.
    """
    if _inspect.iscoroutine(result):
        result.close()
        raise TypeError(
            "duho.runpath: %s returned a coroutine; duho awaits only "
            "Cmd.__call__ -- RunPath steps and __main__.py hooks must be "
            "synchronous" % where
        )


# --------------------------------------------------------------------------
# Step model
# --------------------------------------------------------------------------


class _Step:
    """One resolved step: a name, an ordering priority, deps, and an entrypoint.

    Built from a ``NN-name.py`` file. ``priority`` is ``PRIORITY`` if the module
    set one (and it converts cleanly to ``int``), else the ``NN`` numeric prefix.
    ``required`` is the module's ``REQUIRED`` list (step names that must run
    first, hard dependency), ``before``/``after`` are the module's
    ``BEFORE``/``AFTER`` lists (soft ordering only, see :func:`_order_steps`),
    defaulting to empty. ``opts`` is the resolved, filename-modifier-derived
    :class:`_Opts` for this specific directory entry (``.enabled``/``.strict``
    always concrete booleans here, defaults folded in by
    :func:`_parse_file_modifiers` -- never ``None`` the way a ``--rcopts``
    pattern's own, still-optional override can be). ``file_enabled``/
    ``file_strict`` are read-only convenience properties over ``opts`` (one
    option record, not three).
    """

    __slots__ = (
        "name",
        "priority",
        "required",
        "before",
        "after",
        "entrypoint",
        "module",
        "opts",
    )

    def __init__(
        self,
        name: str,
        priority: int,
        required: "_ty.Sequence[str]",
        entrypoint: "_ty.Callable[..., object]",
        module: object,
        before: "_ty.Sequence[str]" = (),
        after: "_ty.Sequence[str]" = (),
        opts: "_ty.Optional[_Opts]" = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self.required = list(required)
        self.before = list(before)
        self.after = list(after)
        self.entrypoint = entrypoint
        self.module = module
        self.opts = opts if opts is not None else _Opts(strict=True, enabled=True)

    @property
    def file_enabled(self) -> bool:
        return bool(self.opts.enabled)

    @property
    def file_strict(self) -> bool:
        return bool(self.opts.strict)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "_Step(name=%r, priority=%r, required=%r)" % (
            self.name,
            self.priority,
            self.required,
        )


#: Token separators recognized between a matcher and its option tokens, and
#: between option tokens themselves. Both ``:`` and ``;`` work identically
#: everywhere -- NOT an OS-conditional split (``os.pathsep`` differs by
#: platform: ``;`` on Windows, ``:`` on POSIX, which would make a step
#: filename mean something different depending which OS parses it). Accepting
#: BOTH characters, on every platform, keeps a filename portable: author it
#: with either separator on any OS, it parses identically everywhere. ``:``
#: is invalid in a Windows filename, so a step wanting Windows-authorable
#: tokens uses ``;`` instead -- same grammar, different (both Windows-legal)
#: punctuation.
_TOKEN_SEPARATORS = ":;"


def _split_tokens(text: str) -> "_ty.List[str]":
    """Split ``text`` on any run of ``:``/``;`` (see :data:`_TOKEN_SEPARATORS`)."""
    return _re.split("[" + _TOKEN_SEPARATORS + "]", text)


#: The token key that toggles a matcher's own enabled state (``enable``/
#: ``!enable``) -- the tokenized alternative to a leading ``!`` on the
#: matcher itself (``!step1`` and ``step1:!enable`` are equivalent). Only
#: recognized as a bare boolean token (``enable``/``!enable``), never
#: ``enable=...``, mirroring ``_STRICT_TOKEN``.
_ENABLED_TOKEN = "enable"


class _Opts:
    """A parsed matcher's option tokens: ``strict``/``enable`` plus extras.

    Shared by both :meth:`_Selection.parse` (a ``--rcopts`` comma-entry) and
    :func:`_parse_file_modifiers` (a step's own filename) -- ONE token grammar
    AND one carrier class, not three (this used to be ``_Opts`` ->
    ``_FileOpts`` -> ``_Step.file_*``, a rename at each hop that hid they were
    the same record, and directly caused a forced-strict bug below).

    Each token after the matcher is ``key`` (``True``), ``!key`` (``False``),
    or ``key=value`` (a string value). Two token KEYS are recognized
    specially: ``strict`` and ``enable``. Both ``.strict`` and ``.enabled``
    are ``Optional[bool]``: ``None`` means "no such token was present", NOT
    "false" -- this is what lets a caller tell "not specified" from
    "explicitly set" (a ``--rcopts`` entry carrying some OTHER token, like
    ``enable`` or a ``key=value`` extra, must never be mistaken for an
    explicit ``strict``/``!strict`` override just because *some* token was
    present). Everything else lands in ``extra`` for forward compatibility
    (not yet consumed by anything).
    """

    __slots__ = ("strict", "enabled", "extra")

    def __init__(
        self,
        strict: "_ty.Optional[bool]" = None,
        enabled: "_ty.Optional[bool]" = None,
        extra: "_ty.Optional[_ty.Dict[str, _ty.Union[bool, str]]]" = None,
    ) -> None:
        self.strict = strict
        self.enabled = enabled
        self.extra = dict(extra or {})

    @classmethod
    def parse(cls, tokens: "_ty.Sequence[str]") -> "_Opts":
        """Parse token strings (``key``/``!key``/``key=value``) into an :class:`_Opts`.

        ``strict``/``enabled`` stay ``None`` unless the matching token is
        actually present -- callers resolve their own default (a step
        filename folds ``strict=None`` to ``True``; a ``--rcopts`` pattern
        keeps it ``None`` to mean "no override").
        """
        strict: "_ty.Optional[bool]" = None
        enabled: "_ty.Optional[bool]" = None
        extra: "dict[str, _ty.Union[bool, str]]" = {}
        for raw in tokens:
            token = raw.strip()
            if not token:
                continue
            if "=" in token:
                key, _, value = token.partition("=")
                key = key.strip()
                if key:
                    # `strict=...`/`enable=...` aren't recognized spellings
                    # (both are boolean-only, via `key`/`!key`); treat
                    # literally as an extra token like any other key=value.
                    extra[key] = value
                continue
            token_enabled = not token.startswith("!")
            key = token[1:] if token.startswith("!") else token
            if not key:
                continue
            if key == _STRICT_TOKEN:
                strict = token_enabled
            elif key == _ENABLED_TOKEN:
                enabled = token_enabled
            else:
                extra[key] = token_enabled
        return cls(strict=strict, enabled=enabled, extra=extra)


def _parse_file_modifiers(stem: str) -> "_ty.Tuple[str, _Opts]":
    """Strip filename modifiers from ``stem``; return ``(clean_stem, _Opts)``.

    Must run BEFORE :func:`_parse_step_filename`'s ``NN-name`` split, so
    ``!02-provision`` still yields the numeric prefix ``02`` after the leading
    ``!`` is stripped. A leading ``!`` disables the step (matching
    ``--rcopts``'s ``!pattern``); the first ``:``/``;`` in what remains splits
    the step's own name from its option tokens (``key``/``!key``/
    ``key=value``, see :class:`_Opts`) -- the SAME grammar ``--rcopts`` uses
    per comma-entry (:meth:`_Selection.parse`), reused rather than reinvented.

    The returned :class:`_Opts` is fully RESOLVED for a file (``.strict``/
    ``.enabled`` are always concrete ``bool``, never ``None``): a step's own
    default absent any token is ``enabled=True``/``strict=True``.
    """
    text = stem.strip()
    bang_disabled = text.startswith("!")
    if bang_disabled:
        text = text[1:]
    name, *raw_tokens = _split_tokens(text)
    name = name.strip()
    opts = _Opts.parse(raw_tokens)
    # A leading `!` and an `enable`/`!enable` token are two spellings of
    # the same thing; an EXPLICIT token wins when both are somehow present,
    # since it's the more specific spelling (targets exactly `enabled`,
    # whereas the leading `!` is a whole-matcher shorthand) -- absent a
    # token, the leading `!` alone decides.
    enabled = (not bang_disabled) if opts.enabled is None else opts.enabled
    strict = True if opts.strict is None else opts.strict
    return name, _Opts(strict=strict, enabled=enabled, extra=opts.extra)


def _parse_step_filename(stem: str) -> "_ty.Optional[_ty.Tuple[int, str]]":
    """Parse a ``NN-name`` file stem into ``(NN, name)``, or None if not a step.

    A step file is ``<digits>-<name>.py``. The leading run of digits is the
    ordering prefix; everything after the first ``-`` is the step name. A stem
    with no numeric prefix, or no ``-``, is not a step file (helpers, ``__main__``,
    etc. are skipped). Uses ``str.isdecimal()``, not ``.isdigit()``: a Unicode
    "digit" like a superscript ``'²'`` passes ``.isdigit()`` but makes
    ``int()`` raise, which used to crash :func:`is_runpath_dir` on an
    unrelated stray file; every ``isdecimal()`` string converts cleanly.
    """
    if "-" not in stem:
        return None
    prefix, name = stem.split("-", 1)
    if not prefix.isdecimal() or not name:
        return None
    return int(prefix), name


def _iter_step_files(
    directory: "_Path",
) -> "_ty.Iterator[_ty.Tuple[int, str, _Path, _Opts]]":
    """Yield ``(NN, name, path, opts)`` for each step file in ``directory``.

    Filename modifiers (``!``/``:key`` -- see :func:`_parse_file_modifiers`)
    are stripped from the stem BEFORE the ``NN-name`` split, so a modifier never
    affects numeric-prefix/name parsing. Sorted by ``(NN, name)`` for a
    deterministic default order; ``_``-prefixed files are skipped (private/helper
    convention, same as discovery) -- checked against the RAW name so ``__main__.py``
    is never mistaken for a step regardless of modifiers.

    Matches the ``.py`` suffix with a case-SENSITIVE comparison via
    ``Path.suffix`` rather than ``Path.glob("*.py")``: on Windows, ``glob``
    matches ``10-a.PY`` too (the filesystem is case-insensitive), but Python's
    own import machinery refuses that spelling, so the file used to be
    imported-and-fail on Windows while POSIX silently ignored it -- the same
    directory behaved differently by OS.
    """
    found: "list[_ty.Tuple[int, str, _Path, _Opts]]" = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix != ".py":
            continue
        if path.name.startswith("_"):
            continue
        clean_stem, opts = _parse_file_modifiers(path.stem)
        parsed = _parse_step_filename(clean_stem)
        if parsed is None:
            continue
        nn, name = parsed
        found.append((nn, name, path, opts))
    found.sort(key=lambda item: (item[0], item[1]))
    return iter(found)


def is_runpath_dir(path: "_Path") -> bool:
    """True if ``path`` is a RunPath directory.

    A RunPath directory is a directory that (a) contains at least one
    ``NN-name.py`` step file and (b) is NOT a normal package (no ``__init__.py``)
    -- the "shape core doesn't understand" the provider predicate targets. A
    directory with an ``__init__.py`` is a package and is left to normal import.
    """
    if not path.is_dir():
        return False
    if (path / "__init__.py").exists():
        return False
    for _nn, _name, _p, _opts in _iter_step_files(path):
        return True
    return False


def _normalize_step_names(
    value: object,
    step_name: str,
    path: "_Path",
    field: str,
    logger: "_logging.Logger",
) -> "list[str]":
    """Normalize a step's ``REQUIRED``/``BEFORE``/``AFTER`` to ``list[str]``.

    A bare string (``REQUIRED = "provision"``) is an easy slip: iterated
    directly it yields one-character "dependencies", and for ``BEFORE``/
    ``AFTER`` (whose missing names are a silent no-op) it does NOTHING with no
    diagnostic at all. Wrap it in a single-element list instead, and
    warn so the author notices.
    """
    if not value:
        return []
    if isinstance(value, str):
        logger.warning(
            "duho.runpath: step %r %s is a bare string %r in %s; treating it "
            "as a single name -- wrap it in a list to name more than one step",
            step_name,
            field,
            value,
            path,
        )
        return [value]
    return list(value)


def _resolve_priority(
    module: object,
    nn: int,
    name: str,
    path: "_Path",
    strict: bool,
    logger: "_logging.Logger",
) -> int:
    """Resolve a step's ordering ``PRIORITY``, defaulting to its ``NN`` prefix.

    A non-numeric ``PRIORITY`` used to raise a bare, unattributed
    ``ValueError`` straight out of ``int()`` that aborted the run even in
    resilient mode; this names the step and file, and follows the
    normal strict-vs-resilient policy (falling back to the filename prefix
    when resilient).
    """
    priority = getattr(module, "PRIORITY", None)
    if priority is None:
        return nn
    try:
        return int(priority)
    except (TypeError, ValueError) as exc:
        _strict_or_warn(
            "duho.runpath: step %r has a non-integer PRIORITY %r in %s: %s"
            % (name, priority, path, exc),
            strict,
            logger,
            warn_suffix="; using the filename prefix %d instead" % nn,
        )
        return nn


def _load_steps(
    directory: "_Path",
    qualname: str,
    selection: "_Selection",
    logger: "_logging.Logger" = _LOGGER,
) -> "_ty.Tuple[list[_Step], list[str], set[str]]":
    """Resolve, filter, import and order one RunPath directory's steps.

    Returns ``(ordered_enabled_steps, present_names, broken_names)``:

    * ``present_names`` -- every step name found on disk, enabled or not (in
      file-listing order), so the caller's REQUIRED/``--rcopts`` diagnostics
      can tell "genuinely missing" from "present but disabled";
    * ``broken_names`` -- enabled steps whose IMPORT failed and were skipped
      resiliently, so a dependent step's ``REQUIRED`` can be resolved against
      them too (see the module docstring's "REQUIRED and a failed dependency").

    Only ENABLED steps (``selection.decide(name, opts.enabled)``) are ever
    imported: a disabled or deselected step's module body never runs,
    which also means it never enters :func:`_order_steps`'s graph, so it can
    never transitively reorder an enabled step via a stale ``PRIORITY``/
    ``BEFORE``/``AFTER``/``REQUIRED``.

    Two files that resolve to the SAME step name are a duplicate: the later
    file is skipped (kept out of the ordering graph, which is keyed by name)
    with a warning/error naming both files, rather than one silently
    overwriting the other's ordering edges.

    A step whose **import** fails with an ``ImportError``/``NotImplementedError``
    (an *environmental* failure: a missing optional dependency, a not-yet-provided
    integration) is skipped/re-raised following the STEP'S OWN effective strict
    setting (``selection.step_strict``), not just the run-wide flag -- a step
    marked resilient by its own filename token stays resilient even when
    ``--rcopts strict`` is not given, and vice versa. Non-environmental body
    errors (``SyntaxError``, ``NameError``, ...) always surface -- they are
    bugs, not environment.
    """
    present_names: "list[str]" = []
    seen_paths: "dict[str, _Path]" = {}
    to_import: "list[_ty.Tuple[int, str, _Path, _Opts]]" = []
    for nn, name, path, opts in _iter_step_files(directory):
        if name in seen_paths:
            _strict_or_warn(
                "duho.runpath: duplicate step name %r: %s and %s"
                % (name, seen_paths[name], path),
                selection.strict,
                logger,
            )
            continue
        seen_paths[name] = path
        present_names.append(name)
        if selection.decide(name, opts.enabled):
            to_import.append((nn, name, path, opts))

    steps: "list[_Step]" = []
    broken_names: "set[str]" = set()
    for nn, name, path, opts in to_import:
        try:
            module = _discovery.import_from_path(
                "duho._runpath." + qualname.replace(".", "_") + "." + name, path
            )
        except (ImportError, NotImplementedError) as exc:
            broken_names.add(name)
            if selection.step_strict(name, opts.strict):
                raise
            _log_exception(
                logger,
                "step %s failed to import; skipping: %s",
                name,
                exc,
                level=_logging.WARNING,
            )
            continue
        entrypoint = _discovery._module_entrypoint(module)
        if entrypoint is None:
            logger.warning(
                "%s has no entrypoint (%s); not a step",
                path,
                ", ".join(_discovery._ENTRYPOINT_NAMES),
            )
            continue
        priority = _resolve_priority(module, nn, name, path, selection.strict, logger)
        required = _normalize_step_names(
            getattr(module, "REQUIRED", None), name, path, "REQUIRED", logger
        )
        before = _normalize_step_names(
            getattr(module, "BEFORE", None), name, path, "BEFORE", logger
        )
        after = _normalize_step_names(
            getattr(module, "AFTER", None), name, path, "AFTER", logger
        )
        steps.append(
            _Step(
                name,
                priority,
                required,
                entrypoint,
                module,
                before=before,
                after=after,
                opts=opts,
            )
        )
    ordered = _order_steps(steps, strict=selection.strict, logger=logger)
    return ordered, present_names, broken_names


def _order_steps(
    steps: "_ty.Sequence[_Step]",
    strict: bool = False,
    logger: "_logging.Logger" = _LOGGER,
) -> "list[_Step]":
    """Order steps via Kahn's algorithm over a merged REQUIRED/BEFORE/AFTER graph.

    Ties (no remaining predecessor) break by the step's RANK in the
    ``(priority, name)``-sorted base order, popped from a min-heap -- this is
    what makes the sort STABLE: the old implementation did whole
    "passes" over the remaining steps, which let a step reordered by one
    dependency jump ahead of every unrelated LATER step in the same pass
    instead of only past its own dependency. With the heap, only steps that
    are actually ready compete, always picking the lowest-ranked one among
    them, so an unrelated later step never overtakes a step whose dependency
    just became satisfied.

    Edge relations, merged into one predecessor graph exactly as before:

    * ``REQUIRED`` (hard dependency) and ``AFTER`` (soft ordering, same
      direction) both contribute directly as predecessors of the declaring
      step ("X before me");
    * ``BEFORE`` is the mirror direction ("me before X") and is rewritten onto
      the NAMED TARGET's predecessor set.

    A merged-graph name that matches no step in ``steps`` is silently dropped
    -- ordering never fails on a missing/disabled name; the run-time selection
    layer raises the missing/disabled ``REQUIRED`` warning/error (unchanged).
    Since :func:`_load_steps` now only ever passes ENABLED steps here, a
    disabled step is simply absent from this graph, so its edges can never
    reorder an enabled step -- this function no longer needs to know
    about "present but disabled" at all.

    A genuine cycle (spanning any mix of the three relations) is broken
    deterministically: when no ready step remains, the smallest-ranked
    (priority, name) step still stuck is forced through (as if its remaining
    predecessors were satisfied) and a single warning names every step still
    stuck at that point -- NOT the smallest step alone, but also not
    unrelated steps outside the stuck set (an earlier "dump everything
    remaining, in bulk" fallback broke a downstream step's own unrelated,
    non-cyclic dependency too). Ordering then resumes normally: any step that
    was only blocked by the forced one gets emitted next via the heap, and
    only a NEW dead end (if the mix has more than one entangled cycle)
    triggers another warning+break.
    """
    ordered = sorted(steps, key=lambda s: (s.priority, s.name))
    by_name = {s.name: s for s in ordered}
    rank = {s.name: i for i, s in enumerate(ordered)}

    pending: "dict[str, set[str]]" = {}
    for s in ordered:
        deps = set(s.required) | set(s.after)
        pending[s.name] = {d for d in deps if d in by_name and d != s.name}
    for s in ordered:
        for target in s.before:
            if target in by_name and target != s.name:
                pending[target].add(s.name)

    successors: "dict[str, set[str]]" = {name: set() for name in by_name}
    for name, deps in pending.items():
        for dep in deps:
            successors[dep].add(name)

    heap = [rank[name] for name, deps in pending.items() if not deps]
    _heapq.heapify(heap)
    emitted: "list[_Step]" = []
    done: "set[str]" = set()

    def _emit(step: "_Step") -> None:
        emitted.append(step)
        done.add(step.name)
        for succ in successors.get(step.name, ()):
            deps = pending.get(succ)
            if deps is None:
                continue
            deps.discard(step.name)
            if not deps and succ not in done:
                _heapq.heappush(heap, rank[succ])

    while len(done) < len(ordered):
        if heap:
            idx = _heapq.heappop(heap)
            step = ordered[idx]
            if step.name in done:
                continue
            _emit(step)
            continue
        stuck = [s for s in ordered if s.name not in done]
        # Name only the steps in the ACTUAL cycle containing the step about to
        # be forced through, not every step merely blocked behind it:
        # a step whose own dependency chain leads into a cycle, without being
        # part of it, must not be reported as if it were. Chase one unresolved
        # predecessor at a time from the step about to break; the walk must
        # eventually revisit a node (everything here is still pending), and
        # the loop from that revisit is the real cycle.
        start = stuck[0].name
        path: "list[str]" = []
        seen_at: "dict[str, int]" = {}
        node = start
        while node not in seen_at:
            seen_at[node] = len(path)
            path.append(node)
            remaining = pending.get(node) or set()
            if not remaining:
                break
            node = next(iter(remaining))
        cycle_names = path[seen_at[node] :] if node in seen_at else [start]
        message = (
            "duho.runpath: unresolved dependency cycle among %s "
            "(check REQUIRED/BEFORE/AFTER)" % ", ".join(sorted(cycle_names))
        )
        _strict_or_warn(message, strict, logger, warn_suffix="; breaking at %r" % start)
        _emit(stuck[0])
    return emitted


# --------------------------------------------------------------------------
# --rcopts selection
# --------------------------------------------------------------------------


class _Selection:
    """A parsed ``--rcopts`` decision: per-step enable/disable + a strict flag.

    ``patterns`` is a list of ``(pattern, _Opts)`` in declaration order; later
    entries win when several match a step. Each entry's ``_Opts.enabled`` is
    always a concrete ``bool`` (the leading ``!``/``enable`` token already
    folded in, same as a file's own resolved opts); ``_Opts.strict`` stays
    ``Optional[bool]`` -- ``None`` unless that entry carried its own explicit
    ``strict``/``!strict`` token, in which case it overrides the matching
    step's own effective strict setting, scoped to just that pattern's
    matches, NOT run-wide (this used to be guessed from whether the entry had
    ANY tokens at all, which made ``pattern:enable`` -- an entry with no
    strict token whatsoever -- silently force every matching step strict).

    ``strict``/``strict_explicit`` are the separate RUN-WIDE flag, set only by
    a BARE standalone ``strict``/``!strict`` entry (no attached pattern). It
    governs run-wide fatality (an unmatched ``--rcopts`` pattern, a missing
    ``REQUIRED`` dep) and, when explicit, overrides every step's own filename/
    per-pattern strict setting (the outermost layer of the confirmed
    precedence: hardcoded base -> filename -> per-pattern ``--rcopts`` token ->
    the bare run-wide ``--rcopts strict`` token, which wins last of all).
    """

    def __init__(
        self,
        patterns: "_ty.Sequence[_ty.Tuple[str, _Opts]]",
        strict: bool,
        strict_explicit: bool = False,
    ) -> None:
        self.patterns = list(patterns)
        self.strict = strict
        self.strict_explicit = strict_explicit

    @classmethod
    def parse(cls, opts: "_ty.Sequence[str]") -> "_Selection":
        """Parse ``--rcopts`` comma-entries into a :class:`_Selection`.

        Each entry is ``[!]pattern`` optionally followed by ``:``/``;``-separated
        option tokens (``key``/``!key``/``key=value`` -- see :class:`_Opts`),
        the SAME grammar a step's own filename uses
        (:func:`_parse_file_modifiers`): a leading ``!`` disables steps
        matching ``pattern`` -- exactly equivalent to a ``pattern:!enable``
        token (``!step1`` and ``step1:!enable`` disable the same steps); if
        both are somehow present the EXPLICIT ``enable``/``!enable`` token
        wins (more specific than the whole-entry ``!`` shorthand), same
        precedence as the filename side. A BARE entry that is exactly
        ``strict``/``!strict`` (no pattern, no other tokens) toggles the
        RUN-WIDE strict flag. An entry WITH a pattern AND a ``strict``/
        ``!strict`` token (e.g. ``step1:!strict``) instead scopes that strict
        override to steps matching ``step1`` only -- the CLI-side equivalent
        of a filename's own ``!strict`` token. The pattern itself is
        ``.strip()``-ed, so a spaced-out entry like ``build : !strict``
        still matches ``build``, not ``"build "``.
        """
        patterns: "list[_ty.Tuple[str, _Opts]]" = []
        strict = False
        strict_explicit = False
        for raw in opts:
            entry = raw.strip()
            if not entry:
                continue
            bang_disabled = entry.startswith("!")
            rest = entry[1:] if bang_disabled else entry
            pattern, *raw_tokens = _split_tokens(rest)
            pattern = pattern.strip()
            if pattern == _STRICT_TOKEN and not raw_tokens:
                # A bare `strict`/`!strict` entry (no pattern, no tokens of
                # its own) is the run-wide toggle, unchanged from before.
                strict = not bang_disabled
                strict_explicit = True
                continue
            pattern_opts = _Opts.parse(raw_tokens)
            # An explicit `enable`/`!enable` token wins over the leading
            # `!` when both are present (the token is more specific); absent
            # a token, the leading `!` alone decides.
            enabled = (
                (not bang_disabled)
                if pattern_opts.enabled is None
                else pattern_opts.enabled
            )
            patterns.append(
                (
                    pattern,
                    _Opts(
                        strict=pattern_opts.strict,
                        enabled=enabled,
                        extra=pattern_opts.extra,
                    ),
                )
            )
        return cls(patterns, strict, strict_explicit)

    def step_strict(self, name: str, file_strict: bool) -> bool:
        """Resolve whether step ``name``'s own failure should be fatal.

        Precedence (each layer overrides the previous): the step's own
        ``file_strict`` (filename-derived, default ``True``), then a
        per-pattern ``--rcopts`` entry matching ``name`` that carries an
        EXPLICIT ``strict``/``!strict`` token (later matching entries win,
        same as :meth:`decide`) -- an entry with no such token never touches
        this at all, regardless of what other tokens it has -- then an
        EXPLICIT bare ``--rcopts strict``/``!strict`` (run-wide, wins last of
        all).
        """
        result = file_strict
        for pattern, opts in self.patterns:
            if opts.strict is not None and _fnmatch.fnmatchcase(name, pattern):
                result = opts.strict
        if self.strict_explicit:
            return self.strict
        return result

    def decide(self, name: str, default: bool = True) -> bool:
        """Return whether the step ``name`` is enabled under this selection.

        ``default`` is the step's own base enabled state before any
        ``--rcopts`` pattern is applied -- ``True`` unless the caller passes the
        step's filename-derived ``file_enabled`` (the ``!`` prefix), per the
        confirmed precedence (filename default, then ``--rcopts`` on top, CLI
        wins last). With no patterns a step keeps exactly ``default``.
        Otherwise a step is enabled iff the last pattern that matches it is an
        enable pattern; a step matched by no pattern keeps ``default``.
        """
        result = default
        for pattern, opts in self.patterns:
            if _fnmatch.fnmatchcase(name, pattern):
                result = bool(opts.enabled)
        return result

    def unmatched_patterns(self, names: "_ty.Sequence[str]") -> "list[str]":
        """Return the patterns that matched none of ``names`` (for warnings)."""
        unmatched: "list[str]" = []
        for pattern, _opts in self.patterns:
            if not any(_fnmatch.fnmatchcase(name, pattern) for name in names):
                unmatched.append(pattern)
        return unmatched


def _validate_selection(
    selection: "_Selection",
    present_names: "_ty.Sequence[str]",
    logger: "_logging.Logger",
) -> None:
    """Warn (or, under strict, raise) on an ``--rcopts`` pattern matching nothing."""
    unmatched = selection.unmatched_patterns(present_names)
    if unmatched:
        _strict_or_warn(
            "duho.runpath: --rcopts pattern(s) matched no step: %s"
            % ", ".join(unmatched),
            selection.strict,
            logger,
        )


def _validate_required(
    steps: "_ty.Sequence[_Step]",
    present_names: "_ty.Sequence[str]",
    enabled_names: "_ty.AbstractSet[str]",
    selection: "_Selection",
    logger: "_logging.Logger",
) -> None:
    """Warn (or, under strict, raise) on a ``REQUIRED`` naming a missing/disabled step.

    ``steps`` here only ever holds ENABLED steps (see :func:`_load_steps`), so
    every one of them is, by construction, a member of ``enabled_names`` --
    this is purely about what THEIR ``REQUIRED`` list names, not about
    ``steps`` itself.
    """
    present = set(present_names)
    for step in steps:
        missing = [dep for dep in step.required if dep not in present]
        if missing:
            _strict_or_warn(
                "duho.runpath: step %r REQUIRED missing step(s): %s"
                % (step.name, ", ".join(missing)),
                selection.strict,
                logger,
            )
        # A present-but-DISABLED required dep is a different hazard: the dep
        # exists but the current --rcopts selection turned it off, so an
        # enabled step would otherwise run without a prerequisite it declared.
        disabled_deps = [
            dep for dep in step.required if dep in present and dep not in enabled_names
        ]
        if disabled_deps:
            _strict_or_warn(
                "duho.runpath: enabled step %r REQUIRED disabled step(s): %s"
                % (step.name, ", ".join(disabled_deps)),
                selection.strict,
                logger,
            )


#: The magic per-directory lifecycle filename. Already excluded from step
#: discovery by ``_iter_step_files``'s leading-``_`` skip, so it can never
#: accidentally become a step itself.
_LIFECYCLE_FILENAME = "__main__.py"


class _Lifecycle:
    """The optional ``__main__.py`` lifecycle hooks for one RunPath directory.

    Named ``_Lifecycle``/``_load_lifecycle`` (an earlier design used an
    ``_init.py`` file, renamed to ``__main__.py`` during execution, but the
    code kept saying ``_Init``/``_load_init``/a ``"._init"`` module key -- a
    traceback from this file used to show module ``..._init``, a file that
    does not exist).

    Each of ``init``/``success``/``finally_`` is an optional callable read off
    the ``__main__.py`` module (``getattr(module, name, None)``); a missing hook
    no-ops (mirrors ``ModuleCommand``'s existing default-hook precedent).
    """

    __slots__ = ("init", "success", "finally_")

    def __init__(
        self,
        init: "_ty.Optional[_ty.Callable[..., object]]",
        success: "_ty.Optional[_ty.Callable[..., object]]",
        finally_: "_ty.Optional[_ty.Callable[..., object]]",
    ) -> None:
        self.init = init
        self.success = success
        self.finally_ = finally_


def _load_lifecycle(
    directory: "_Path",
    qualname: str,
    logger: "_logging.Logger" = _LOGGER,
) -> "_ty.Optional[_Lifecycle]":
    """Load ``__main__.py`` from ``directory``, if present; else ``None``.

    ``None`` means "no lifecycle" -- callers must treat this as byte-identical
    to before this lifecycle existed (no ``ctx``, steps called with ``self``
    only). When present, imports it the same way steps are imported (the
    public ``discovery.import_from_path``, ending in ``.__main__`` rather than
    the stale ``._init``) and reads the three optional hooks off it.

    Called BEFORE :func:`_load_steps`: a ``__main__.py`` doing
    module-level setup (e.g. adding a sibling ``lib/`` to ``sys.path`` for
    shared step helpers) must already be in effect by the time step modules
    are imported, or those imports fail and are silently skipped as
    environmental errors.
    """
    path = directory / _LIFECYCLE_FILENAME
    if not path.is_file():
        return None
    module = _discovery.import_from_path(
        "duho._runpath." + qualname.replace(".", "_") + ".__main__", path
    )
    return _Lifecycle(
        init=getattr(module, "init", None),
        success=getattr(module, "success", None),
        finally_=getattr(module, "finally_", None),
    )


#: Sentinel for "argument not supplied" where ``None`` is a meaningful value.
#: :func:`register`'s ``base`` uses ``None`` for "keep the current value", which
#: leaves no way to say "clear it"; ``step_adapter`` needs both, so it gets a
#: sentinel instead of repeating that limitation.
class _Keep:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<keep>"


_KEEP = _Keep()

#: An app-supplied hook that rewrites a step's entrypoint before it is called.
#: ``None`` (the default) means "call the step exactly as written", which is
#: the behavior every RunPath app had before this existed. Configurable via
#: :func:`register`'s ``step_adapter=`` -- set once per process/app, like
#: :data:`_BASE`, since every RunPath command in one app shares it.
_ADAPTER: "_ty.Optional[_ty.Callable[..., object]]" = None


def _adapt_step(
    entrypoint: "_ty.Callable[..., object]",
) -> "_ty.Callable[..., object]":
    """Apply the app's ``step_adapter`` to ``entrypoint``, if one is set.

    Kept deliberately dumb: no caching (an adapter is cheap and a step runs
    once per target), and a falsy return is ignored rather than treated as "no
    step", so an adapter that forgets to return cannot silently delete work.

    The adapted callable is what arity detection then inspects, so an adapter
    that changes the signature -- wrapping a ``(client, args, logger)`` body in
    a ``(cmd, ctx)`` shim, say -- is honored rather than second-guessed.
    """
    if _ADAPTER is None:
        return entrypoint
    return _ADAPTER(entrypoint) or entrypoint


def _is_bare_passthrough(params: "_ty.Sequence[_inspect.Parameter]") -> bool:
    """True if ``params`` is exactly a var-positional/var-keyword pass-through.

    i.e. the signature carries NO named parameter of its own -- ``(*args)``,
    ``(*args, **kwargs)``, or ``(**kwargs)`` -- the shape a generic decorator
    leaves when it doesn't declare its own explicit parameters, relying on
    ``@functools.wraps`` to publish the WRAPPED callable's real signature via
    ``__wrapped__`` instead. A decorator that DOES declare parameters of its
    own (e.g. duho's own ``step_adapter`` shims, ``def call(cmd, ctx=None)``)
    is never bare pass-through, so ITS signature is authoritative and must not
    be second-guessed by following ``__wrapped__`` underneath it.
    """
    if not params:
        return False
    kinds = {p.kind for p in params}
    return kinds <= {
        _inspect.Parameter.VAR_POSITIONAL,
        _inspect.Parameter.VAR_KEYWORD,
    }


def _step_wants_ctx(entrypoint: "_ty.Callable[..., object]") -> bool:
    """True if a step's entrypoint accepts a 2nd positional ``ctx`` argument.

    Inspects ``entrypoint`` itself first, with ``follow_wrapped=False``:
    ``inspect.signature`` follows ``__wrapped__`` by default, which reads the
    ORIGINAL wrapped callable's signature instead of an ``@functools.wraps``
    ``step_adapter`` shim's own, explicitly different one -- a documented
    contract ("the adapted callable is what arity detection inspects") that
    following ``__wrapped__`` silently broke: a ``(cmd, ctx=None)`` shim over
    a 1-arg step got called without ``ctx`` (silently ``None``), and a
    ``(cmd, ctx)`` shim (no default) raised a confusing ``TypeError`` naming
    the WRAPPED function, not the shim. Only when the shim's own signature is
    a BARE pass-through (see :func:`_is_bare_passthrough` -- a generic
    ``@decorator`` with no explicit parameters of its own) does this fall back
    to following ``__wrapped__``, so a plain, signature-preserving decorator on
    an ordinary 1-arg step still reads as 1-arg.

    A step written ``(cmd)`` (the historical, pre-lifecycle shape) keeps being
    called with just ``self``; a step written ``(cmd, ctx)`` (or with a
    ``*args`` catch-all) additionally receives the ``ctx`` -- but ONLY when a
    ``__main__.py`` was actually loaded (the caller gates on that separately;
    see the module docstring's "The optional __main__.py lifecycle"). If the
    signature cannot be introspected (a builtin/C callable), conservatively
    default to ``False`` (the historical 1-arg call), never over-supplying an
    argument the entrypoint can't take.
    """
    try:
        sig = _inspect.signature(entrypoint, follow_wrapped=False)
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return False
    params = list(sig.parameters.values())
    if _is_bare_passthrough(params):
        try:
            sig = _inspect.signature(entrypoint)  # default: follows __wrapped__
        except (TypeError, ValueError):  # pragma: no cover
            return False
        params = list(sig.parameters.values())
    positional = 0
    for param in params:
        if param.kind is _inspect.Parameter.VAR_POSITIONAL:
            return True
        if param.kind in (
            _inspect.Parameter.POSITIONAL_ONLY,
            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 2


# --------------------------------------------------------------------------
# The RunPath command
# --------------------------------------------------------------------------


class RunPathCmd(_Cmd):
    """Run a directory of numbered ``NN-name.py`` steps in order.

    A ``Cmd`` subclass built by the RunPath provider for a step directory. Its
    ``__call__`` loads the optional ``__main__.py`` lifecycle FIRST (before any
    step import -- see the module docstring), loads the steps (applying
    filename-encoded ``!``/``:key`` per-step defaults, importing only enabled/
    selected ones), applies ``--rcopts`` selection, orders them (Kahn's
    algorithm honoring ``REQUIRED`` plus the soft ``BEFORE``/``AFTER``
    relations), and runs each one's entrypoint through ``self._logger_`` --
    arity-detected so a step written ``(cmd, ctx)`` receives the
    ``__main__.py``-produced context and a step written ``(cmd)`` is
    unaffected. Returns the aggregate exit code (max of every step's own,
    ``None``/success -> ``0``, a swallowed failure -> ``1``).

    Not instantiated directly by users; the provider (see :func:`register`)
    subclasses it per directory, binding the directory path and subcommand name.
    A direct subclass need only set the class attrs ``_runpath_dir_`` (the step
    directory :class:`~pathlib.Path`) and ``_parsername_`` (the subcommand name).
    """

    #: The RunPath directory whose ``NN-name.py`` files are the steps. Set by the
    #: provider-built subclass; ``None`` on the base (which is not runnable).
    _runpath_dir_: "_ty.Optional[_Path]" = None

    rcopts: "_Arg[_ty.List[str], _Extend(',')]"
    "Step selection, comma-separated fnmatch patterns; `!` disables, `strict` errors on miss (e.g. `!*,build`)."
    ("-O", "--rcopts")  # type: ignore

    def _runpath_logger_(self) -> "_logging.Logger":
        """Resolve the run logger: the instance's ``_logger_`` if it has one.

        A ``RunPathCmd`` combined with ``LoggingArgs`` (the usual app shape)
        exposes a ``_logger_`` property scoped to the parser name; a bare
        ``RunPathCmd`` with no logging mixin has none, so fall back to the
        ``duho.runpath`` module logger (the 0.5.3 logger-naming change).

        This deliberately differs from ``ModuleCommand._logger_for``, whose
        fallback stays the plain ``"duho"`` logger: that one is handed to USER
        hook code, which should not have to know duho's module layout.
        """
        logger = getattr(self, "_logger_", None)
        if isinstance(logger, _logging.Logger):
            return logger
        return _LOGGER

    def __call__(self) -> int:
        directory = getattr(type(self), "_runpath_dir_", None)
        if directory is None:
            raise NotImplementedError(
                "RunPathCmd has no _runpath_dir_; build it via duho.runpath's "
                "provider (register()) or subclass it with _runpath_dir_ set"
            )
        directory = _Path(directory)
        logger = self._runpath_logger_()
        selection = _Selection.parse(getattr(self, "rcopts", None) or [])

        # The lifecycle is imported before any step, so its module-level
        # setup is already in effect for every step import.
        lifecycle = _load_lifecycle(directory, self._parsername_, logger)

        steps, present_names, broken_names = _load_steps(
            directory, self._parsername_, selection, logger
        )
        enabled_names = {step.name for step in steps}

        _validate_selection(selection, present_names, logger)
        _validate_required(steps, present_names, enabled_names, selection, logger)

        ctx = None
        if lifecycle is not None and lifecycle.init is not None:
            try:
                ctx = lifecycle.init(self, logger)
                _reject_coroutine(ctx, "%s __main__.init()" % self._parsername_)
            except Exception as exc:
                # `init` failure is always fatal (Design decision): every step
                # depends on ctx, so there is no meaningful resilient partial
                # init -- log then re-raise unconditionally, regardless of
                # --rcopts strict.
                _log_exception(logger, "__main__.py init() failed: %s", exc)
                raise

        failed_names: "set[str]" = set(broken_names)
        codes: "list[int]" = [0]
        try:
            for step in steps:
                # A step whose own REQUIRED dependency actually ran (or
                # tried to import) and failed is skipped too, rather than
                # running against a broken prerequisite. Fatality for the
                # SKIPPED dependent follows its own strict setting.
                unmet = [dep for dep in step.required if dep in failed_names]
                if unmet:
                    failed_names.add(step.name)
                    _strict_or_warn(
                        "duho.runpath: skipping step %r: REQUIRED step(s) failed: %s"
                        % (step.name, ", ".join(unmet)),
                        selection.step_strict(step.name, step.file_strict),
                        logger,
                    )
                    codes.append(1)
                    continue

                logger.info("running step %s", step.name)
                entrypoint = _adapt_step(step.entrypoint)
                wants_ctx = lifecycle is not None and _step_wants_ctx(entrypoint)
                try:
                    result = entrypoint(self, ctx) if wants_ctx else entrypoint(self)
                    _reject_coroutine(result, "step %s" % step.name)
                except Exception as exc:
                    # A non-strict step failure is SWALLOWED (the run
                    # continues), so this log line is the only record of
                    # where it broke -- DUHO_TRACEBACK=1 turns it into a full
                    # traceback.
                    _log_exception(logger, "step %s failed: %s", step.name, exc)
                    failed_names.add(step.name)
                    codes.append(1)
                    if selection.step_strict(step.name, step.file_strict):
                        raise
                    continue

                # A step's return value follows ModuleCommand's own
                # convention: None -> success; a non-zero int -> failure,
                # routed through the exact same strict-vs-resilient path as
                # an exception. Anything else is not an exit code.
                code = result if isinstance(result, int) else 0
                if code:
                    failed_names.add(step.name)
                    _strict_or_warn(
                        "duho.runpath: step %r returned a non-zero exit code: %r"
                        % (step.name, result),
                        selection.step_strict(step.name, step.file_strict),
                        logger,
                    )
                codes.append(code)

            if (
                lifecycle is not None
                and lifecycle.success is not None
                and not failed_names
            ):
                result = lifecycle.success(ctx, self, logger)
                _reject_coroutine(result, "%s __main__.success()" % self._parsername_)
        finally:
            if lifecycle is not None and lifecycle.finally_ is not None:
                try:
                    result = lifecycle.finally_(ctx, self, logger)
                    _reject_coroutine(
                        result, "%s __main__.finally_()" % self._parsername_
                    )
                except Exception as exc:
                    # A raising finally_ must not mask the real step failure
                    # (if any is currently propagating) nor the aggregate exit
                    # code: log and swallow it (matches
                    # discovery.run_command's own guarded finally_).
                    _log_exception(logger, "__main__.py finally_() failed: %s", exc)
        return max(codes)


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
    Called automatically when ``duho.runpath`` is imported; call it explicitly
    if you prefer no import side effects (import the module then... it's
    already registered -- see :func:`unregister` to opt back out).

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
