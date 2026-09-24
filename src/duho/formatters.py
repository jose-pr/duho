"""Opt-in argparse help formatters (F8): defaults-in-help + ANSI color.

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

import argparse as _argparse
import os as _os
import sys as _sys

from .logging import _asicode

__all__ = [
    "DefaultsFormatter",
    "ColorHelpFormatter",
    "ColorDefaultsFormatter",
]

_RESET = _asicode(0)
_HEADING_CODE = _asicode(1)  # bold
_FLAG_CODE = _asicode(36)  # cyan


class DefaultsFormatter(_argparse.HelpFormatter):
    """Append ``(default: X)`` to each option's help, skipping empty defaults.

    Like argparse's own ``ArgumentDefaultsHelpFormatter`` but it does NOT add the
    suffix when the effective default is ``None``, ``""``, ``False``, or an empty
    sized container (``[]``, ``{}``, ``()``, ``set()``) -- an unset optional, a
    ``store_true`` flag, or duho's own resting default for every list/set/dict
    field (C041) -- those contribute noise, not information. An explicit
    ``%(default)s`` already in the help text is left untouched, and a
    ``SUPPRESS``-defaulted action (``--help``/``--version``, inherited-suppressed
    fields) never gains a suffix.

    Shows the field's CLASS default, never a live env/config value (C001):
    ``duho.agenthelp.stash_default_provenance`` -- called from ``args.py``'s
    ``_AgentHelpAction`` right before it renders human help -- snapshots each
    action's declared default (and, when the value actually came from env or
    config, a value-free provenance note) as ``_duho_class_default_``/
    ``_duho_default_source_``. Falls back to plain ``action.default`` when
    those are absent (a parser built but never run through that print path,
    e.g. calling ``cls._parser_().format_help()`` directly in a test) --
    identical to the pre-C001 behavior there, since nothing has layered
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
            # The default is spliced in literally (not via argparse's own
            # `%(default)s` re-expansion, which would read the CURRENT,
            # possibly-still-layered `action.default` again) -- a literal `%`
            # in the value must survive `_expand_help`'s own `% params`
            # re-formatting of this string, hence the doubling.
            return f"{help_text} (default: {str(default).replace('%', '%%')})"
        return help_text


def _color_enabled(stream=None) -> bool:
    """Whether to emit ANSI for help output.

    ``NO_COLOR`` (set to anything) forces color OFF; ``FORCE_COLOR`` (truthy)
    forces it ON regardless of TTY (the convention the test-suite relies on);
    otherwise color follows ``stream.isatty()`` (default ``sys.stdout``). Mirrors
    the discipline duho's logging color machinery uses.
    """
    if _os.environ.get("NO_COLOR") is not None:
        return False
    if _os.environ.get("FORCE_COLOR"):
        return True
    stream = stream if stream is not None else _sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:  # pragma: no cover - defensive: a stream with no isatty
        return False


#: 3.14+ colors help itself (``ArgumentParser(color=True)`` is that version's
#: own default, honoring ``NO_COLOR``/``FORCE_COLOR``/``PYTHON_COLORS`` on its
#: own) -- duho's own coloring is skipped there entirely (C016) rather than
#: nesting ANSI codes around argparse's own theme (a duho reset cancelling an
#: argparse color code before the colon it was meant to color, etc).
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

    The invocation is colored strictly AFTER layout (C016): pre-3.14 argparse
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._duho_color_ = False if _NATIVE_HELP_COLOR else _color_enabled()

    def start_section(self, heading):
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
