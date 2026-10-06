"""Opt-in argparse help formatters: defaults-in-help + ANSI color.

Both formatters are plain :class:`argparse.HelpFormatter` subclasses a class opts
into via the sandwich-named ``_help_formatter_`` attribute, which
``Args._parser_`` plumbs into argparse's ``formatter_class``. They are **off by
default** -- duho's ``--help`` output is unchanged unless a class sets
``_help_formatter_``.

* :class:`DefaultsFormatter` -- append ``(default: X)`` to each option's help,
  but (unlike argparse's own ``ArgumentDefaultsHelpFormatter``) skip the noise of
  ``None``/``""``/``False`` defaults.
* :class:`ColorHelpFormatter` -- ANSI-color section headings and option flags,
  gated on a TTY (and ``NO_COLOR``/``FORCE_COLOR``). When color is off the output
  is byte-identical to the base formatter, so alignment and piping are unaffected.
* :class:`ColorDefaultsFormatter` -- both composed.

The ANSI codes reuse ``logging.py``'s ``_asicode`` (hard-coded escapes -- no
``colorama`` import, so ``import duho`` pays nothing for these).
"""

from __future__ import annotations

import argparse as _argparse
import os as _os
import sys as _sys
import typing as _ty

from ._compat import BOOL_TRUE as _BOOL_TRUE
from .logging import _asicode

__all__ = [
    "DefaultsFormatter",
    "ColorHelpFormatter",
    "ColorDefaultsFormatter",
]

#: Set by ``duho.runtime`` on a root-declared required global it un-required on
#: an ``app()`` subparser, so :class:`_RequiredForUsageFormatter` still shows it
#: as required in usage.
_DISPLAY_REQUIRED_ATTR = "_duho_display_required_"

_RESET = _asicode(0)
_HEADING_CODE = _asicode(1)  # bold
_FLAG_CODE = _asicode(36)  # cyan


class DefaultsFormatter(_argparse.HelpFormatter):
    """Append ``(default: X)`` to each option's help, skipping empty defaults.

    Like argparse's own ``ArgumentDefaultsHelpFormatter`` but it does NOT add the
    suffix when the effective default is ``None``, ``""``, ``False``, or an empty
    sized container (``[]``, ``{}``, ``()``, ``set()``) -- an unset optional, a
    ``store_true`` flag, or duho's own resting default for every list/set/dict
    field -- those contribute noise, not information. An explicit
    ``%(default)s`` already in the help text is left untouched, and a
    ``SUPPRESS``-defaulted action (``--help``/``--version``, inherited-suppressed
    fields) never gains a suffix.

    Shows the field's CLASS default, never a live env/config value:
    ``duho.agenthelp._stash_default_provenance`` -- called from ``duho.args``'s
    ``_AgentHelpAction`` right before it renders human help -- snapshots each
    action's declared default (and, when the value actually came from env or
    config, a value-free provenance note) as ``_duho_class_default_``/
    ``_duho_default_source_``. Falls back to plain ``action.default`` when
    those are absent (a parser built but never run through that print path,
    e.g. calling ``cls._parser_().format_help()`` directly in a test) --
    identical to the previous behavior there, since nothing has layered
    ``action.default`` away from its class default in that case either.
    """

    def _get_help_string(self, action):
        help_text = action.help or ""
        if "%(default)" in help_text:
            return help_text
        source = getattr(action, "_duho_default_source_", None)
        if source:
            return f"{help_text} (from {source})"
        default = getattr(action, "_duho_class_default_", action.default)
        if (
            default is _argparse.SUPPRESS
            or default is None
            or default is False
            or default == ""
            or (
                isinstance(default, (list, tuple, set, frozenset, dict)) and not default
            )
        ):
            return help_text
        if action.option_strings or action.nargs in (
            _argparse.OPTIONAL,
            _argparse.ZERO_OR_MORE,
        ):
            # Spliced in literally, not via argparse's `%(default)s` re-expansion
            # (which would read the live layered default); `%` is doubled so it
            # survives `_expand_help`'s `% params`.
            return f"{help_text} (default: {str(default).replace('%', '%%')})"
        return help_text


def _color_enabled(stream=None) -> bool:
    """Whether to emit ANSI for help output.

    ``NO_COLOR`` set to anything forces OFF; ``FORCE_COLOR`` forces ON when its
    value is a shared truthy token (``duho.text.BOOL_TRUE``), and any other
    value counts as unset. Otherwise ``TERM=dumb`` means OFF, and color follows
    ``stream.isatty()`` (default ``sys.stdout``).
    """
    if _os.environ.get("NO_COLOR") is not None:
        return False
    force = _os.environ.get("FORCE_COLOR")
    if force is not None and force.strip().lower() in _BOOL_TRUE:
        return True
    if _os.environ.get("TERM") == "dumb":
        return False
    stream = stream if stream is not None else _sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # pragma: no cover - defensive: a stream with no isatty
        return False


#: 3.14+ colors help itself (honoring ``NO_COLOR``/``FORCE_COLOR``/
#: ``PYTHON_COLORS``), so duho's coloring is skipped there rather than nesting
#: ANSI codes inside argparse's theme.
_NATIVE_HELP_COLOR = _sys.version_info >= (3, 14)


class ColorHelpFormatter(_argparse.HelpFormatter):
    """ANSI-color section headings and option flags, when color is enabled.

    Color is resolved once at formatter construction via :func:`_color_enabled`
    (``NO_COLOR``/``FORCE_COLOR``/TTY), and unconditionally OFF on 3.14+ (see
    :data:`_NATIVE_HELP_COLOR`). When it is OFF, every override falls through
    to the base :class:`argparse.HelpFormatter`, so the output -- and its
    column alignment -- is byte-identical to duho's default help. When ON,
    section headings are bold and option invocations (``-v, --verbose``) are
    colored.

    The invocation is colored strictly AFTER layout: pre-3.14 argparse
    measures an action's invocation with plain ``len()`` -- both when tracking
    the widest invocation (``add_argument``, which drives the shared help
    column) and when padding an individual line to it (``_format_action``) --
    so if that measured string carried ANSI escape bytes (a previous version
    of this class colored inside ``_format_action_invocation``, the same
    method both call), the escapes counted toward its width: columns went
    ragged, and a longer invocation could wrap early. Overriding
    ``_format_action`` instead -- letting the base class format the ENTIRE
    line uncolored (correct alignment, guaranteed), then substituting a
    colored copy of the plain invocation text back into it -- colors the
    output without the measurement ever seeing a color code.
    """

    def __init__(self, *args: _ty.Any, **kwargs: _ty.Any) -> None:
        super().__init__(*args, **kwargs)
        self._duho_color_ = False if _NATIVE_HELP_COLOR else _color_enabled()

    def start_section(self, heading: _ty.Optional[str]) -> None:
        if self._duho_color_ and heading is not None:
            heading = f"{_HEADING_CODE}{heading}{_RESET}"
        super().start_section(heading)

    def _format_action(self, action):
        text = super()._format_action(action)
        if not self._duho_color_:
            return text
        invocation = _argparse.HelpFormatter._format_action_invocation(self, action)
        if invocation and invocation in text:
            text = text.replace(invocation, f"{_FLAG_CODE}{invocation}{_RESET}", 1)
        return text


class ColorDefaultsFormatter(ColorHelpFormatter, DefaultsFormatter):
    """Compose :class:`ColorHelpFormatter` + :class:`DefaultsFormatter`.

    Colors headings/flags AND appends ``(default: X)`` -- the batteries-included
    pretty-help formatter. The two mix cleanly: color overrides ``start_section``
    / ``_format_action``, defaults overrides ``_get_help_string`` -- called from
    inside the BASE ``_format_action`` that color's own override delegates to,
    so a colored line still gets its ``(default: X)`` suffix before color is
    spliced back in.
    """


class _RequiredForUsageFormatter(_argparse.HelpFormatter):
    """Show an action flagged :data:`_DISPLAY_REQUIRED_ATTR` as REQUIRED in
    USAGE text, though ``action.required`` is ``False``.

    ``app()`` un-requires a root-declared required global on its subparsers so
    the value can follow the subcommand, with enforcement moved to a post-parse
    check. argparse draws the ``[--opt]``-vs-``--opt`` usage bracket from the
    same flag, so ``--help`` would show it as optional. This sets
    ``action.required`` only while formatting usage, and is composed onto the
    parser's existing formatter, so an author's ``_help_formatter_`` still works.
    """

    def _format_usage(self, usage, actions, groups, prefix):
        flagged = [
            action
            for action in actions
            if getattr(action, _DISPLAY_REQUIRED_ATTR, False) and not action.required
        ]
        for action in flagged:
            action.required = True
        try:
            return super()._format_usage(usage, actions, groups, prefix)
        finally:
            for action in flagged:
                action.required = False


def _install_required_usage_formatter(parser) -> None:
    """Compose :class:`_RequiredForUsageFormatter` onto ``parser``'s
    ``formatter_class``, so any action flagged :data:`_DISPLAY_REQUIRED_ATTR`
    renders as required in USAGE text.

    ``formatter_class`` is read lazily, so a dynamic subclass mixing the
    formatter in with whatever is already in effect (including an author's
    ``_help_formatter_``) can replace it after the parser is built. Idempotent.
    """
    current = getattr(parser, "formatter_class", _argparse.HelpFormatter)
    if issubclass(current, _RequiredForUsageFormatter):
        return
    parser.formatter_class = type(
        "_DuhoRequiredUsage" + current.__name__,
        (_RequiredForUsageFormatter, current),
        {},
    )
