import inspect as _inspect
import logging as _logging
import re as _re
import typing as _ty

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
    most RunPath validation sites (an unmatched ``--rcopts`` pattern, a
    missing/disabled ``REQUIRED`` dep, an invalid ``PRIORITY``, a
    dependency-cycle break, a dependent skipped because its own ``REQUIRED``
    step failed, a step's own non-zero return) so these near-identical copies
    can't drift apart from each other. ``warn_suffix`` is appended only on the
    warning path (some callers want extra detail there that would be
    redundant in the raised message). A duplicate ENABLED step name is NOT one
    of these sites -- it always raises, regardless of strict mode (see
    :func:`_load_steps`).
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
    pattern's own, still-optional override can be). ``file_strict`` is a
    read-only convenience property over ``opts``.
    """

    __slots__ = (
        "name",
        "priority",
        "required",
        "before",
        "after",
        "entrypoint",
        "opts",
    )

    def __init__(
        self,
        name: str,
        priority: int,
        required: "_ty.Sequence[str]",
        entrypoint: "_ty.Callable[..., object]",
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
        self.opts = opts if opts is not None else _Opts(strict=True, enabled=True)

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
    """Split ``text`` on each ``:``/``;`` character (see :data:`_TOKEN_SEPARATORS`).

    A doubled separator (``"a::b"``) yields an empty-string token between
    them (``["a", "", "b"]``), not a collapsed run.
    """
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
