"""RunPath: ordered step commands from a directory of numbered ``.py`` files.

**Opt-in module.** Core duho never imports this; you activate it explicitly with
``import duho.runpath`` (which auto-registers a command provider as an import
side-effect) or with an explicit :func:`register` call. Either way it plugs into
the shipped :func:`duho.register_command_provider` hook (Plan 13) and needs *zero*
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
  below) -- arity-detected, never guessed from a flag.

:class:`RunPathCmd` is a ``Cmd`` subclass whose ``__call__`` runs the ordered,
selected steps, logging through ``self._logger_``. Its one CLI field is
``--rcopts`` (``-O``), a comma-separated list of fnmatch selection patterns.

The optional ``__main__.py`` lifecycle
------------------------------------

A RunPath directory may define a ``__main__.py`` file -- the same dunder Python
already uses for "this directory's entrypoint" (as in ``python -m package``), so
no new naming convention is invented. It is never treated as a step itself: its
leading ``_`` already excludes it from step discovery (the same guard that skips
any ``_``-prefixed file). It defines up to three module-level callables, all
optional:

* ``init(cmd, logger) -> ctx`` -- called once before any step runs; its return
  value is the ``ctx`` handed to every 2-arg step entrypoint. If ``init``
  raises, the whole run is fatal (log then re-raise unconditionally,
  regardless of ``--rcopts strict`` -- every step depends on ``ctx``, so there
  is no meaningful partial/resilient init);
* ``success(ctx, cmd, logger)`` -- called once after every enabled step has run
  without a strict-mode abort;
* ``finally_(ctx, cmd, logger)`` -- called once unconditionally after the step
  loop, success or failure (a plain ``try/finally``), mirroring
  ``discovery.ModuleCommand``'s existing ``init``/``success``/``finally_``
  triple but with ``logger`` passed explicitly (RunPath commands are often used
  without a ``LoggingArgs`` mixin, see ``_runpath_logger_``).

A directory with no ``__main__.py`` behaves byte-identically to before this
lifecycle existed: ``ctx`` is never produced, and every step keeps being called
with just ``self``.

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
  * ``enabled``/``!enabled`` -- an explicit alternative to the leading ``!``;
    ``!step1`` and ``step1:!enabled`` disable the same step. If BOTH the
    leading ``!`` and an explicit token are somehow present, the TOKEN wins
    (more specific than the whole-name shorthand).

  Any other token is collected but not yet consumed by anything (a
  forward-compatible extension point).

This is the exact SAME token grammar ``--rcopts`` uses per comma-entry (see
below) -- one parser, not two.

Precedence for a step's effective strict setting (each layer overrides the
previous): the step's own filename default (``strict=True`` absent a
``!strict`` token), then a per-pattern ``--rcopts`` ``!strict`` token matching
that step's name, then an EXPLICIT bare ``--rcopts strict``/``!strict`` (no
pattern attached -- the RUN-WIDE toggle), which wins last of all and overrides
every step uniformly. This is symlink-transparent by construction: two
symlinks (or plain copies) pointing at the same physical step file, named
differently in two different RunPath directories, resolve to different
effective enabled/strict defaults, because the parse reads the **directory
entry's name**, never the target file's content.

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
filename modifiers and from the run-wide ``--rcopts strict`` flag.

``--rcopts`` selection
----------------------

``--rcopts`` is a comma-separated list of entries, each an fnmatch pattern
matched against step *names*, optionally followed by ``:``/``;``-separated
option tokens -- the SAME grammar (and the same ``strict``/``enabled`` special
tokens) a step's own filename uses, see above:

* a leading ``!`` **disables** matching steps (``!*`` disables everything;
  ``!*,build`` disables all then re-enables ``build``); ``pattern:!enabled`` is
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

The default is **resilient**, matching duho's discovery philosophy (a single bad
command is skipped, not fatal):

* an ``--rcopts`` pattern that matches **no** step is a warning, not an error;
* a step whose entrypoint **raises** is logged and skipped; the run continues.

Passing ``strict`` in ``--rcopts`` opts INTO erroring instead: an unmatched
pattern raises, and the first step to raise re-raises and stops the run. This is
the reconciliation of the predecessor design's strict flag with duho's resilient
default -- resilient unless you ask for strict.

All union annotations are quoted, and declared class-attr annotations avoid the
PEP-604 ``|`` operator (``typing.Union``/``Optional`` instead), so the module and
any ``RunPathCmd`` parser build cleanly on Python 3.9.
"""

import fnmatch as _fnmatch
import inspect as _inspect
import logging as _logging
import re as _re
import typing as _ty
from pathlib import Path as _Path

from .args import Arg as _Arg, Cmd as _Cmd, Extend as _Extend
from . import discovery as _discovery

__all__ = ["RunPathCmd", "register", "unregister", "is_runpath_dir"]

_LOGGER = _logging.getLogger("duho")

#: The token in ``--rcopts`` that toggles strict mode (``strict`` enables,
#: ``!strict`` disables). A bare ``strict`` is a run-wide marker, not a step name.
_STRICT_TOKEN = "strict"


# --------------------------------------------------------------------------
# Step model
# --------------------------------------------------------------------------


class _Step:
    """One resolved step: a name, an ordering priority, deps, and an entrypoint.

    Built from a ``NN-name.py`` file. ``priority`` is ``PRIORITY`` if the module
    set one, else the ``NN`` numeric prefix. ``required`` is the module's
    ``REQUIRED`` list (step names that must run first, hard dependency),
    ``before``/``after`` are the module's ``BEFORE``/``AFTER`` lists (soft
    ordering only, see :func:`_order_steps`), defaulting to empty.
    ``file_enabled``/``file_strict`` are the filename-modifier-derived defaults
    (Phase 2's ``!``/``:key`` token convention) for this specific directory
    entry, defaulting to ``True``/``True`` (enabled, strict) when the filename carries
    no modifier; ``file_opts`` holds any extra ``,key``/``,!key`` tokens not
    consumed by anything yet (forward compatibility, per the plan).
    """

    __slots__ = (
        "name",
        "priority",
        "required",
        "before",
        "after",
        "entrypoint",
        "module",
        "file_enabled",
        "file_strict",
        "file_opts",
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
        file_enabled: bool = True,
        file_strict: bool = True,
        file_opts: "_ty.Optional[_ty.Dict[str, bool]]" = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self.required = list(required)
        self.before = list(before)
        self.after = list(after)
        self.entrypoint = entrypoint
        self.module = module
        self.file_enabled = file_enabled
        self.file_strict = file_strict
        self.file_opts = dict(file_opts or {})

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


#: The token key that toggles a matcher's own enabled state (``enabled``/
#: ``!enabled``) -- the tokenized alternative to a leading ``!`` on the
#: matcher itself (``!step1`` and ``step1:!enabled`` are equivalent). Only
#: recognized as a bare boolean token (``enabled``/``!enabled``), never
#: ``enabled=...``, mirroring ``_STRICT_TOKEN``.
_ENABLED_TOKEN = "enabled"


class _Opts:
    """A parsed matcher's option tokens: ``strict``/``enabled`` plus extras.

    Shared by both :meth:`_Selection.parse` (a ``--rcopts`` comma-entry) and
    :func:`_parse_file_modifiers` (a step's own filename) -- ONE token
    grammar, not two. Each token after the matcher is ``key`` (``True``),
    ``!key`` (``False``), or ``key=value`` (a string value). Two keys are
    recognized specially: ``strict`` (per-matcher strict-override, default
    ``True`` absent a ``strict``/``!strict`` token) and ``enabled`` (an
    explicit alternative to a leading ``!`` on the matcher -- ``!step1`` and
    ``step1:!enabled`` mean the same thing; ``enabled`` is ``None`` unless a
    token actually set it, so a caller can tell "not specified" from
    "explicitly enabled"). Everything else lands in ``extra`` for forward
    compatibility (not yet consumed by anything).
    """

    __slots__ = ("strict", "enabled", "extra")

    def __init__(
        self,
        strict: bool = True,
        enabled: "_ty.Optional[bool]" = None,
        extra: "_ty.Optional[_ty.Dict[str, _ty.Union[bool, str]]]" = None,
    ) -> None:
        self.strict = strict
        self.enabled = enabled
        self.extra = dict(extra or {})

    @classmethod
    def parse(cls, tokens: "_ty.Sequence[str]") -> "_Opts":
        """Parse token strings (``key``/``!key``/``key=value``) into an :class:`_Opts`."""
        strict = True
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
                    # `strict=...`/`enabled=...` aren't recognized spellings
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


class _FileOpts:
    """Filename-encoded per-step options, parsed off a directory entry's stem.

    A leading ``!`` on the whole stem disables the step by default (mirrors
    ``--rcopts``'s own ``!pattern`` shorthand exactly -- one disable
    convention, not two); an explicit ``enabled``/``!enabled`` TOKEN wins over
    the leading ``!`` if both are somehow present (the token is the more
    specific spelling). Everything after that is tokenized by
    :meth:`_Opts.parse` (``key``/``!key``/``key=value``, ``:``/``;``
    separated) -- there is no more special-cased ``?`` suffix: non-strict is
    now the ``!strict`` token, the same spelling ``--rcopts`` uses.

    This operates on the **directory entry's name** (``path.stem``), not the
    step module's body -- two symlinks pointing at the same physical file, named
    differently, resolve to different :class:`_FileOpts` (symlink-transparent by
    construction: ``Path.glob``/``.stem`` already read the link's own name).
    """

    __slots__ = ("enabled", "strict", "opts")

    def __init__(
        self,
        enabled: bool = True,
        strict: bool = True,
        opts: "_ty.Optional[_ty.Dict[str, _ty.Union[bool, str]]]" = None,
    ) -> None:
        self.enabled = enabled
        self.strict = strict
        self.opts = dict(opts or {})


def _parse_file_modifiers(stem: str) -> "_ty.Tuple[str, _FileOpts]":
    """Strip filename modifiers from ``stem``; return ``(clean_stem, _FileOpts)``.

    Must run BEFORE :func:`_parse_step_filename`'s ``NN-name`` split, so
    ``!02-provision`` still yields the numeric prefix ``02`` after the leading
    ``!`` is stripped. A leading ``!`` disables the step (matching
    ``--rcopts``'s ``!pattern``); the first ``:``/``;`` in what remains splits
    the step's own name from its option tokens (``key``/``!key``/
    ``key=value``, see :class:`_Opts`) -- the SAME grammar ``--rcopts`` uses
    per comma-entry (:meth:`_Selection.parse`), reused rather than reinvented.
    """
    text = stem.strip()
    bang_disabled = text.startswith("!")
    if bang_disabled:
        text = text[1:]
    name, *raw_tokens = _split_tokens(text)
    opts = _Opts.parse(raw_tokens)
    # A leading `!` and an `enabled`/`!enabled` token are two spellings of
    # the same thing; an EXPLICIT token wins when both are somehow present,
    # since it's the more specific spelling (targets exactly `enabled`,
    # whereas the leading `!` is a whole-matcher shorthand) -- absent a
    # token, the leading `!` alone decides.
    enabled = (not bang_disabled) if opts.enabled is None else opts.enabled
    return name, _FileOpts(enabled=enabled, strict=opts.strict, opts=opts.extra)


def _parse_step_filename(stem: str) -> "_ty.Optional[_ty.Tuple[int, str]]":
    """Parse a ``NN-name`` file stem into ``(NN, name)``, or None if not a step.

    A step file is ``<digits>-<name>.py``. The leading run of digits is the
    ordering prefix; everything after the first ``-`` is the step name. A stem
    with no numeric prefix, or no ``-``, is not a step file (helpers, ``__main__``,
    etc. are skipped).
    """
    if "-" not in stem:
        return None
    prefix, name = stem.split("-", 1)
    if not prefix.isdigit() or not name:
        return None
    return int(prefix), name


def _iter_step_files(
    directory: "_Path",
) -> "_ty.Iterator[_ty.Tuple[int, str, _Path, _FileOpts]]":
    """Yield ``(NN, name, path, file_opts)`` for each step file in ``directory``.

    Filename modifiers (``!``/``:key`` -- see :func:`_parse_file_modifiers`)
    are stripped from the stem BEFORE the ``NN-name`` split, so a modifier never
    affects numeric-prefix/name parsing. Sorted by ``(NN, name)`` for a
    deterministic default order; ``_``-prefixed files are skipped (private/helper
    convention, same as discovery) -- checked against the RAW name so ``__main__.py``
    is never mistaken for a step regardless of modifiers.
    """
    found: "list[_ty.Tuple[int, str, _Path, _FileOpts]]" = []
    for path in directory.glob("*.py"):
        if path.name.startswith("_"):
            continue
        clean_stem, file_opts = _parse_file_modifiers(path.stem)
        parsed = _parse_step_filename(clean_stem)
        if parsed is None:
            continue
        nn, name = parsed
        found.append((nn, name, path, file_opts))
    found.sort(key=lambda item: (item[0], item[1]))
    return iter(found)


def is_runpath_dir(path: "_Path") -> bool:
    """True if ``path`` is a RunPath directory.

    A RunPath directory is a directory that (a) contains at least one
    ``NN-name.py`` step file and (b) is NOT a normal package (no ``__init__.py``)
    -- the "shape core duho doesn't understand" the provider contract targets. A
    directory with an ``__init__.py`` is a package and is left to normal import.
    """
    if not path.is_dir():
        return False
    if (path / "__init__.py").exists():
        return False
    for _nn, _name, _p, _opts in _iter_step_files(path):
        return True
    return False


def _load_steps(
    directory: "_Path",
    qualname: str,
    strict: bool = False,
    logger: "_logging.Logger" = _LOGGER,
) -> "list[_Step]":
    """Import every step file and build ordered :class:`_Step` objects.

    Each file is imported under a synthesized-unique ``sys.modules`` key (reusing
    ``discovery._import_from_path`` / ``_unique_module_name`` so a loose
    ``json.py`` step never clobbers stdlib ``json``). Ordering priority is the
    module's ``PRIORITY`` if set, else the ``NN`` prefix; ``REQUIRED`` is read as
    the dep list. A file with no entrypoint (``main``/``run``/``call``) is not a
    runnable step -- skipped with a debug line.

    A step whose **import** fails with an ``ImportError``/``NotImplementedError``
    (an *environmental* failure: a missing optional dependency, a not-yet-provided
    integration) is skipped with a warning in the resilient default and re-raised
    under ``strict``. Non-environmental body errors (``SyntaxError``, ``NameError``,
    ...) always surface -- they are bugs, not environment.

    The returned list is ordered by ``(priority, name)`` and then reordered so
    every step runs after the present steps it ``REQUIRED`` (see
    :func:`_order_steps`).
    """
    steps: "list[_Step]" = []
    for nn, name, path, file_opts in _iter_step_files(directory):
        mod_key = _discovery._unique_module_name(
            "duho._runpath." + qualname.replace(".", "_") + "." + name
        )
        try:
            module = _discovery._import_from_path(mod_key, path)
        except (ImportError, NotImplementedError) as exc:
            if strict:
                raise
            logger.warning(
                "duho.runpath: step %s failed to import; skipping: %s", name, exc
            )
            continue
        entrypoint = _discovery._module_entrypoint(module)
        if entrypoint is None:
            _LOGGER.debug(
                "duho.runpath: %s has no entrypoint (%s); not a step",
                path,
                ", ".join(_discovery._ENTRYPOINT_NAMES),
            )
            continue
        priority = getattr(module, "PRIORITY", None)
        if priority is None:
            priority = nn
        required = getattr(module, "REQUIRED", None) or []
        before = getattr(module, "BEFORE", None) or []
        after = getattr(module, "AFTER", None) or []
        steps.append(
            _Step(
                name,
                int(priority),
                required,
                entrypoint,
                module,
                before=before,
                after=after,
                file_enabled=file_opts.enabled,
                file_strict=file_opts.strict,
                file_opts=file_opts.opts,
            )
        )
    return _order_steps(steps, strict=strict, logger=logger)


def _order_steps(
    steps: "_ty.Sequence[_Step]",
    strict: bool = False,
    logger: "_logging.Logger" = _LOGGER,
) -> "list[_Step]":
    """Order steps by ``(priority, name)``, then topologically honor deps/ordering.

    Starts from the priority/name order (the stable default) and does a
    dependency-respecting stable topological sort against a MERGED predecessor
    graph combining all three relations:

    * ``REQUIRED`` (hard dependency: this step's own ``REQUIRED`` list) and
      ``AFTER`` (soft ordering, same direction) both contribute directly as
      predecessors of the declaring step ("X before me");
    * ``BEFORE`` is the mirror direction ("me before X") and is rewritten onto
      the NAMED TARGET's predecessor set -- i.e. ``a``'s ``BEFORE = ["b"]``
      becomes an entry "``a`` is a predecessor of ``b``", not an outgoing edge
      on ``a`` itself.

    A merged-graph name that matches no present step (or a present-but-disabled
    one for ``BEFORE``/``AFTER``) is silently dropped for ordering purposes --
    ordering never fails; the run-time selection layer is where a missing/
    disabled ``REQUIRED`` warning/strict-error is raised (unchanged, and NOT
    extended to ``BEFORE``/``AFTER``, which are ordering-only and never warn).
    A dependency cycle spanning any mix of the three relations is broken
    deterministically the same way a REQUIRED-only cycle already was (a step
    whose deps can't all be satisfied is emitted in priority order once no
    further progress is possible), so ordering always terminates.
    """
    ordered = sorted(steps, key=lambda s: (s.priority, s.name))
    by_name = {s.name: s for s in ordered}

    predecessors: "dict[str, set[str]]" = {
        s.name: set(s.required) | set(s.after) for s in ordered
    }
    for s in ordered:
        for target in s.before:
            predecessors.setdefault(target, set()).add(s.name)

    emitted: "list[_Step]" = []
    done: "set[str]" = set()
    remaining = list(ordered)

    while remaining:
        progressed = False
        blocked: "list[_Step]" = []
        for step in remaining:
            unmet = [
                dep
                for dep in predecessors.get(step.name, ())
                if dep in by_name and dep not in done
            ]
            if unmet:
                blocked.append(step)
            else:
                emitted.append(step)
                done.add(step.name)
                progressed = True
        remaining = blocked
        if not progressed:
            # A cycle (or steps mutually blocked). Under strict this is an error;
            # in the resilient default emit the rest in priority order so ordering
            # is deterministic and always terminates.
            message = (
                "duho.runpath: unresolved REQUIRED cycle among %s"
                % ", ".join(s.name for s in remaining)
            )
            if strict:
                raise ValueError(message)
            logger.warning("%s; emitting in priority order", message)
            emitted.extend(remaining)
            break
    return emitted


# --------------------------------------------------------------------------
# --rcopts selection
# --------------------------------------------------------------------------


class _Selection:
    """A parsed ``--rcopts`` decision: per-step enable/disable + a strict flag.

    ``patterns`` is a list of ``(pattern, enabled, strict_override)`` in
    declaration order; later entries win when several match a step.
    ``strict_override`` is ``None`` unless that entry carried its own
    ``strict``/``!strict`` token (e.g. ``step1:!strict``), in which case it
    overrides the matching step's own filename-derived strict setting --
    scoped to just that pattern's matches, NOT run-wide.

    ``strict``/``strict_explicit`` are the separate RUN-WIDE flag, set only by
    a BARE standalone ``strict``/``!strict`` entry (no attached pattern) --
    unchanged from before this grammar existed. It governs run-wide fatality
    (an unmatched ``--rcopts`` pattern, a missing ``REQUIRED`` dep) and, when
    explicit, overrides every step's own filename/per-pattern strict setting
    (the outermost layer of the confirmed precedence: hardcoded base ->
    filename -> per-pattern ``--rcopts`` token -> the bare run-wide
    ``--rcopts strict`` token, which wins last of all).
    """

    def __init__(
        self,
        patterns: "_ty.Sequence[_ty.Tuple[str, bool, _ty.Optional[bool]]]",
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
        matching ``pattern`` -- exactly equivalent to a ``pattern:!enabled``
        token (``!step1`` and ``step1:!enabled`` disable the same steps); if
        both are somehow present the EXPLICIT ``enabled``/``!enabled`` token
        wins (more specific than the whole-entry ``!`` shorthand), same
        precedence as the filename side. A BARE entry that is exactly
        ``strict``/``!strict`` (no pattern, no other tokens) toggles the
        RUN-WIDE strict flag, unchanged from before this grammar existed. An
        entry WITH a pattern AND a ``strict``/``!strict`` token (e.g.
        ``step1:!strict``) instead scopes that strict override to steps
        matching ``step1`` only -- the CLI-side equivalent of a filename's own
        ``!strict`` token.
        """
        patterns: "list[_ty.Tuple[str, bool, _ty.Optional[bool]]]" = []
        strict = False
        strict_explicit = False
        for raw in opts:
            entry = raw.strip()
            if not entry:
                continue
            bang_disabled = entry.startswith("!")
            rest = entry[1:] if bang_disabled else entry
            pattern, *raw_tokens = _split_tokens(rest)
            if pattern == _STRICT_TOKEN and not raw_tokens:
                # A bare `strict`/`!strict` entry (no pattern, no tokens of
                # its own) is the run-wide toggle, unchanged from before.
                strict = not bang_disabled
                strict_explicit = True
                continue
            pattern_opts = _Opts.parse(raw_tokens)
            # An explicit `enabled`/`!enabled` token wins over the leading
            # `!` when both are present (the token is more specific); absent
            # a token, the leading `!` alone decides.
            enabled = (not bang_disabled) if pattern_opts.enabled is None else pattern_opts.enabled
            strict_override = pattern_opts.strict if raw_tokens else None
            patterns.append((pattern, enabled, strict_override))
        return cls(patterns, strict, strict_explicit)

    def step_strict(self, name: str, file_strict: bool) -> bool:
        """Resolve whether step ``name``'s own failure should be fatal.

        Precedence (each layer overrides the previous): the step's own
        ``file_strict`` (filename-derived, default ``True``), then a
        per-pattern ``--rcopts`` strict token matching ``name`` (later
        matching entries win, same as :meth:`decide`), then an EXPLICIT bare
        ``--rcopts strict``/``!strict`` (run-wide, wins last of all).
        """
        result = file_strict
        for pattern, _enabled, strict_override in self.patterns:
            if strict_override is not None and _fnmatch.fnmatchcase(name, pattern):
                result = strict_override
        if self.strict_explicit:
            return self.strict
        return result

    def decide(self, name: str, default: bool = True) -> bool:
        """Return whether the step ``name`` is enabled under this selection.

        ``default`` is the step's own base enabled state before any
        ``--rcopts`` pattern is applied -- ``True`` unless the caller passes the
        step's filename-derived ``file_enabled`` (Phase 2's ``!`` prefix), per
        the confirmed precedence (filename default, then ``--rcopts`` on top,
        CLI wins last). With no patterns a step keeps exactly ``default``.
        Otherwise a step is enabled iff the last pattern that matches it is an
        enable pattern; a step matched by no pattern keeps ``default``, UNLESS
        the first pattern is a disable-**all** wildcard (``!*``) -- in that
        common ``!*,x`` idiom the base becomes disabled (regardless of
        ``default``) and only re-enabled names run. A *targeted* disable like
        ``!two`` disables only its own matches and leaves ``default`` in place
        for everything else.
        """
        # Base default: the caller-supplied default, unless the very first
        # pattern is a disable-all wildcard (`!*`) -- the "start from nothing"
        # idiom, which always wins over any per-step default.
        result = default
        if self.patterns:
            first_pattern, first_enabled, _first_strict = self.patterns[0]
            if not first_enabled and first_pattern == "*":
                result = False
        for pattern, enabled, _strict_override in self.patterns:
            if _fnmatch.fnmatchcase(name, pattern):
                result = enabled
        return result

    def unmatched_patterns(self, names: "_ty.Sequence[str]") -> "list[str]":
        """Return the patterns that matched none of ``names`` (for warnings)."""
        unmatched: "list[str]" = []
        for pattern, _enabled, _strict_override in self.patterns:
            if not any(_fnmatch.fnmatchcase(name, pattern) for name in names):
                unmatched.append(pattern)
        return unmatched


#: The magic per-directory lifecycle filename. Already excluded from step
#: discovery by ``_iter_step_files``'s leading-``_`` skip, so it can never
#: accidentally become a step itself.
_LIFECYCLE_FILENAME = "__main__.py"


class _Init:
    """The optional ``__main__.py`` lifecycle hooks for one RunPath directory.

    Each of ``init``/``success``/``finally_`` is an optional callable read off
    the ``__main__.py`` module (``getattr(module, name, None)``); a missing hook
    no-ops (mirrors ``ModuleCommand``'s existing default-hook precedent). See
    :func:`_load_init`.
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


def _load_init(
    directory: "_Path",
    qualname: str,
    logger: "_logging.Logger" = _LOGGER,
) -> "_ty.Optional[_Init]":
    """Load ``__main__.py`` from ``directory``, if present; else ``None``.

    ``None`` means "no lifecycle" -- callers must treat this as byte-identical
    to pre-Phase-1 behavior (no ``ctx``, steps called with ``self`` only). When
    present, imports it the same way steps are imported (unique
    ``sys.modules`` key via ``discovery._unique_module_name`` +
    ``discovery._import_from_path``) and reads the three optional hooks off it.
    """
    path = directory / _LIFECYCLE_FILENAME
    if not path.is_file():
        return None
    mod_key = _discovery._unique_module_name(
        "duho._runpath." + qualname.replace(".", "_") + "._init"
    )
    module = _discovery._import_from_path(mod_key, path)
    return _Init(
        init=getattr(module, "init", None),
        success=getattr(module, "success", None),
        finally_=getattr(module, "finally_", None),
    )


def _step_wants_ctx(entrypoint: "_ty.Callable[..., object]") -> bool:
    """True if a step's entrypoint accepts a 2nd positional ``ctx`` argument.

    Arity-detected the same way ``runtime._wants_logger_arg`` detects a
    module ``register`` hook's arity: a step written ``(cmd)`` (the historical,
    pre-Phase-1 shape) keeps being called with just ``self``; a step written
    ``(cmd, ctx)`` (or with a ``*args`` catch-all) additionally receives the
    ``__main__.py``-produced ``ctx``. If the signature cannot be introspected (a
    builtin/C callable), conservatively default to ``False`` (the historical
    1-arg call), never over-supplying an argument the entrypoint can't take.
    """
    try:
        params = _inspect.signature(entrypoint).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return False
    positional = 0
    for param in params.values():
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
    ``__call__`` loads an optional ``__main__.py`` lifecycle (``init``/``success``/
    ``finally_``), loads the steps (applying filename-encoded ``!``/``:key``
    per-step defaults, see the module docstring), applies ``--rcopts``
    selection on top, orders them (priority/name, honoring ``REQUIRED`` plus the
    soft ``BEFORE``/``AFTER`` relations), and runs each enabled step's
    entrypoint through ``self._logger_`` -- arity-detected so a step written
    ``(cmd, ctx)`` receives the ``__main__.py``-produced context and a step written
    ``(cmd)`` is unaffected.

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
        ``RunPathCmd`` with no logging mixin has none, so fall back to duho's
        ``"duho"`` logger. Mirrors ``ModuleCommand._logger_for``.
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
        logger = self._runpath_logger_()
        selection = _Selection.parse(getattr(self, "rcopts", None) or [])
        steps = _load_steps(
            _Path(directory), self._parsername_, selection.strict, logger
        )
        names = [s.name for s in steps]

        unmatched = selection.unmatched_patterns(names)
        if unmatched:
            message = "duho.runpath: --rcopts pattern(s) matched no step: %s" % (
                ", ".join(unmatched)
            )
            if selection.strict:
                raise ValueError(message)
            logger.warning(message)

        # Warn (or error, under strict) on REQUIRED naming a missing step.
        present = set(names)
        enabled = {
            step.name for step in steps if selection.decide(step.name, step.file_enabled)
        }
        for step in steps:
            missing = [dep for dep in step.required if dep not in present]
            if missing:
                message = (
                    "duho.runpath: step %r REQUIRED missing step(s): %s"
                    % (step.name, ", ".join(missing))
                )
                if selection.strict:
                    raise ValueError(message)
                logger.warning(message)

            # A present-but-DISABLED required dep is a different hazard: the dep
            # exists but the current --rcopts selection turned it off, so an
            # enabled step will run without a prerequisite it declared (M4).
            if step.name in enabled:
                disabled_deps = [
                    dep
                    for dep in step.required
                    if dep in present and dep not in enabled
                ]
                if disabled_deps:
                    message = (
                        "duho.runpath: enabled step %r REQUIRED disabled step(s): %s"
                        % (step.name, ", ".join(disabled_deps))
                    )
                    if selection.strict:
                        raise ValueError(message)
                    logger.warning(message)

        init_hooks = _load_init(_Path(directory), self._parsername_, logger)
        ctx = None
        if init_hooks is not None and init_hooks.init is not None:
            try:
                ctx = init_hooks.init(self, logger)
            except Exception as exc:
                # `init` failure is always fatal (Design decision): every step
                # depends on ctx, so there is no meaningful resilient partial
                # init -- log then re-raise unconditionally, regardless of
                # --rcopts strict.
                logger.error("duho.runpath: __main__.py init() failed: %s", exc)
                raise

        aborted = False
        try:
            for step in steps:
                if not selection.decide(step.name, step.file_enabled):
                    logger.debug(
                        "duho.runpath: skipping disabled step %s", step.name
                    )
                    continue
                logger.info("duho.runpath: running step %s", step.name)
                try:
                    if _step_wants_ctx(step.entrypoint):
                        step.entrypoint(self, ctx)
                    else:
                        step.entrypoint(self)
                except Exception as exc:
                    logger.error(
                        "duho.runpath: step %s failed: %s", step.name, exc
                    )
                    # Precedence: the step's own file_strict (filename
                    # default True, or overridden by a `!strict` token) ->
                    # a per-pattern --rcopts strict token matching this step
                    # -> an explicit bare --rcopts strict/!strict (run-wide,
                    # wins last of all).
                    if selection.step_strict(step.name, step.file_strict):
                        aborted = True
                        raise
        finally:
            if init_hooks is not None and init_hooks.finally_ is not None:
                init_hooks.finally_(ctx, self, logger)
        if init_hooks is not None and init_hooks.success is not None and not aborted:
            init_hooks.success(ctx, self, logger)
        return 0


# --------------------------------------------------------------------------
# Provider registration
# --------------------------------------------------------------------------


def _build_runpath_command(path: "_Path", qualname: str) -> "type[RunPathCmd]":
    """Provider builder: make a per-directory :class:`RunPathCmd` subclass.

    Binds the resolved directory and a subcommand name (the directory's basename,
    ``_``->``-`` normalized, matching module-command naming) onto a fresh subclass
    so ``CmdBuilder``/``discover_commands`` gets a ready-to-register command.
    """
    directory = _Path(path)
    name = directory.name.replace("_", "-")
    namespace = {
        "_runpath_dir_": directory,
        "_parsername_": name,
        "__doc__": "Run the %s step directory." % name,
    }
    return _ty.cast("type[RunPathCmd]", type("RunPathCmd_" + name.replace("-", "_"), (RunPathCmd,), namespace))


#: Records the exact (predicate, builder) pair this module registered, so
#: :func:`unregister` removes *ours* specifically (not merely the newest provider).
_REGISTERED: "_ty.Optional[_ty.Tuple[_ty.Callable, _ty.Callable]]" = None


def register() -> None:
    """Register the RunPath command provider (idempotent).

    After this, ``duho.discover_commands``/``CmdBuilder`` resolve a step
    directory (a dir with ``NN-name.py`` files and no ``__init__.py``) to a
    :class:`RunPathCmd`. Called automatically when ``duho.runpath`` is imported;
    call it explicitly if you prefer no import side effects (import the module
    then... it's already registered -- see :func:`unregister` to opt back out).
    """
    global _REGISTERED
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
    try:
        _discovery._PROVIDERS.remove(_REGISTERED)
    except ValueError:
        pass
    _REGISTERED = None


# Auto-register on import: importing ``duho.runpath`` is the opt-in activation.
register()
