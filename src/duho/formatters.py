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
    suffix when the effective default is ``None``, ``""`` or ``False`` (an unset
    optional, a ``store_true`` flag) -- those contribute noise, not information.
    An explicit ``%(default)s`` already in the help text is left untouched, and a
    ``SUPPRESS``-defaulted action (``--help``/``--version``, inherited-suppressed
    fields) never gains a suffix.
    """

    def _get_help_string(self, action):
        help_text = action.help or ""
        if "%(default)" in help_text:
            return help_text
        default = action.default
        if (
            default is _argparse.SUPPRESS
            or default is None
            or default is False
            or default == ""
        ):
            return help_text
        if action.option_strings or action.nargs in (
            _argparse.OPTIONAL,
            _argparse.ZERO_OR_MORE,
        ):
            return help_text + " (default: %(default)s)"
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


class ColorHelpFormatter(_argparse.HelpFormatter):
    """ANSI-color section headings and option flags, when color is enabled.

    Color is resolved once at formatter construction via :func:`_color_enabled`
    (``NO_COLOR``/``FORCE_COLOR``/TTY). When it is OFF, every override falls
    through to the base :class:`argparse.HelpFormatter`, so the output -- and its
    column alignment -- is byte-identical to duho's default help. When ON, section
    headings are bold and option invocations (``-v, --verbose``) are colored.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._duho_color = _color_enabled()

    def start_section(self, heading):
        if self._duho_color and heading is not None:
            heading = f"{_HEADING_CODE}{heading}{_RESET}"
        super().start_section(heading)

    def _format_action_invocation(self, action):
        text = super()._format_action_invocation(action)
        if self._duho_color and text:
            return f"{_FLAG_CODE}{text}{_RESET}"
        return text


class ColorDefaultsFormatter(ColorHelpFormatter, DefaultsFormatter):
    """Compose :class:`ColorHelpFormatter` + :class:`DefaultsFormatter`.

    Colors headings/flags AND appends ``(default: X)`` -- the batteries-included
    pretty-help formatter. The two mix cleanly: color overrides ``start_section``
    / ``_format_action_invocation``, defaults overrides ``_get_help_string``.
    """
