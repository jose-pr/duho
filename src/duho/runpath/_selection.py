from __future__ import annotations

import fnmatch as _fnmatch
import logging as _logging
import typing as _ty

from ._steps import _Opts, _STRICT_TOKEN, _Step, _split_tokens, _strict_or_warn

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
    matches, NOT run-wide (an entry such as ``pattern:enable``, with no
    strict token, does not force every matching step strict).

    ``strict``/``strict_explicit`` are the separate RUN-WIDE flag, set only by
    a BARE standalone ``strict``/``!strict`` entry (no attached pattern). It
    governs run-wide fatality (an unmatched ``--rcopts`` pattern, a missing
    ``REQUIRED`` dep) and, when explicit, overrides every step's own filename/
    per-pattern strict setting (the outermost layer of the
    precedence: hardcoded base -> filename -> per-pattern ``--rcopts`` token ->
    the bare run-wide ``--rcopts strict`` token, which wins last of all).
    """

    def __init__(
        self,
        patterns: _ty.Sequence[_ty.Tuple[str, _Opts]],
        strict: bool,
        strict_explicit: bool = False,
    ) -> None:
        self.patterns = list(patterns)
        self.strict = strict
        self.strict_explicit = strict_explicit

    @classmethod
    def parse(cls, opts: _ty.Sequence[str]) -> _Selection:
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
        patterns: list[_ty.Tuple[str, _Opts]] = []
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
        entry's filename-derived ``opts.enabled`` (the ``!`` prefix), per the
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

    def unmatched_patterns(self, names: _ty.Sequence[str]) -> list[str]:
        """Return the patterns that matched none of ``names`` (for warnings)."""
        unmatched: list[str] = []
        for pattern, _opts in self.patterns:
            if not any(_fnmatch.fnmatchcase(name, pattern) for name in names):
                unmatched.append(pattern)
        return unmatched


def _validate_selection(
    selection: _Selection,
    present_names: _ty.Sequence[str],
    logger: _logging.Logger,
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
    steps: _ty.Sequence[_Step],
    present_names: _ty.Sequence[str],
    enabled_names: _ty.AbstractSet[str],
    selection: _Selection,
    logger: _logging.Logger,
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
