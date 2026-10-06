from __future__ import annotations

import fnmatch as _fnmatch
import logging as _logging
import typing as _ty

from ._steps import _Opts, _STRICT_TOKEN, _Step, _split_tokens, _strict_or_warn

# --------------------------------------------------------------------------
# --rcopts selection
# --------------------------------------------------------------------------


class _Selection:
    """A parsed ``--rcopts`` decision: per-step enable/disable plus a strict flag.

    ``patterns`` holds ``(pattern, _Opts)`` in order; later matches win.
    ``_Opts.strict`` is ``None`` unless the entry has its own strict token, which
    then scopes to its matches. ``strict``/``strict_explicit`` are the run-wide
    flag from a bare ``strict`` entry, which overrides every step's own setting.
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

        An entry is ``[!]pattern`` plus optional ``:``/``;`` option tokens, the
        grammar of :func:`_parse_file_modifiers`; the pattern is stripped. A
        leading ``!`` equals ``:!enable``, but an explicit ``enable`` token wins.
        A bare ``strict``/``!strict`` entry sets the run-wide flag; with a
        pattern (``step1:!strict``) it scopes to the matching steps.
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
                # Bare entry: the run-wide toggle.
                strict = not bang_disabled
                strict_explicit = True
                continue
            pattern_opts = _Opts.parse(raw_tokens)
            # An explicit token wins over the leading `!`.
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
        """Whether step ``name``'s own failure should be fatal.

        Precedence, last wins: the step's ``file_strict``, then the last matching
        ``--rcopts`` entry with an explicit ``strict`` token, then an explicit
        bare run-wide ``strict``/``!strict``.
        """
        result = file_strict
        for pattern, opts in self.patterns:
            if opts.strict is not None and _fnmatch.fnmatchcase(name, pattern):
                result = opts.strict
        if self.strict_explicit:
            return self.strict
        return result

    def decide(self, name: str, default: bool = True) -> bool:
        """Whether step ``name`` is enabled: the last matching pattern decides.

        ``default`` is the step's own base state (its filename ``!``); a step
        matched by no pattern keeps it.
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
