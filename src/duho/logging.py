import argparse as _argparse
import copy as _copy
import logging as _logging
import os as _os
import re as _re
import sys as _sys
import typing as _ty

from ._compat import BOOL_TRUE as _BOOL_TRUE
from ._compat import get_level_names_mapping

if _ty.TYPE_CHECKING:
    from logging import *  # type: ignore

    TRACE: int

#: Cached colorama module (or ``None`` when unavailable / not yet resolved).
#: ``import colorama`` costs ~3-5 ms and is only ever needed to translate a
#: NAMED color spec ("red", "red+white") into an ANSI sequence -- the built-in
#: level colors are hard-coded ANSI (see ``DefaultFormatter.COLORS``), so a
#: plain ``import duho`` must not pay it. Resolved lazily on first use in
#: ``_getcolor`` and memoized here. ``False`` means "not yet probed";
#: once probed, this holds the real module, or ``None`` when colorama is not
#: installed.
_color: "object | bool | None" = False


def _resolve_colorama():
    """Return the imported ``colorama`` module, or ``None`` if unavailable.

    Imports ``colorama`` on first call and caches the result (the module or
    ``None``) on the module-global ``_color``, so the potentially-missing
    dependency is probed exactly once and only when a named color is actually
    requested. The sentinel ``False`` means "not yet probed".
    """
    global _color
    if _color is False:
        try:
            import colorama as _colorama  # type: ignore
        except ImportError:
            _colorama = None  # type: ignore
        _color = _colorama
    return _color


def __getattr__(name: str):
    # Only forward PUBLIC stdlib names: duho.logging is documented as
    # "wraps stdlib logging", not as a transparent re-export of it. Forwarding
    # dunders too made `duho.logging.__path__` resolve to STDLIB logging's own
    # package path, so the import system treated `duho.logging` as a package
    # and `import duho.logging.handlers` silently loaded a second copy of
    # stdlib `logging/handlers.py` under a new name (distinct classes,
    # separate module-level state) instead of raising the AttributeError a
    # module with no `handlers` submodule should give.
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(_logging, name)


def _asicode(*codes):
    return "".join(["\033[" + str(c) + "m" for c in codes])


_COLOR_NAME_RE = _re.compile(r"^[A-Za-z_]+(\+[A-Za-z_]+)?$")

#: Upper-cased names :func:`add_logging_level` has itself installed on
#: ``logging`` -- a repeat call for one of these is a harmless idempotent
#: no-op (unless ``force=True``), while a name colliding with something
#: ELSE (a plain stdlib attribute like ``logging.BASIC_FORMAT``, never
#: registered through here) still raises. An int constant (what
#: ``setattr(logging, NAME, level)`` installs) carries no marker of its own
#: the way the lower-cased method attributes do (``_duho_level_``), so
#: ownership is tracked here instead.
_installed_level_names: "set[str]" = set()


def _getcolor(color: str):
    """Resolve a color spec to an ANSI escape sequence.

    A named spec is ``"fore"`` or ``"fore+back"`` (e.g. ``"red"``,
    ``"red+white"``, or one of colorama's ``"..._EX"`` bright variants like
    ``"lightred_ex"``); it is resolved via colorama's ``Fore``/``Back``. When
    colorama is absent or a name does not resolve, an empty string is
    returned (never the raw name/compound string). Anything that is not a
    bare name (already an ANSI escape like ``"\\033[31m"``) is passed through
    unchanged.
    """
    # A named color spec is letters/underscores plus an optional single "+"
    # separator -- underscores so colorama's "_EX" bright variants
    # (LIGHTRED_EX, ...) are recognized as names too, instead of falling
    # through to the "already an ANSI code" passthrough and being rendered as
    # literal text in front of every log line.
    if _COLOR_NAME_RE.match(color):
        colorama = _resolve_colorama()
        if not colorama:
            return ""
        fore, back, *_ = color.split("+") + ["", ""]
        fore = (getattr(colorama.Fore, fore.upper(), "") or "") if fore else ""
        back = (getattr(colorama.Back, back.upper(), "") or "") if back else ""
        return fore + back

    return color


def add_logging_level(
    name: str, level: int, force: bool = False, color: "str | None" = None
) -> None:
    """Register a custom log level.

    Installs ``NAME``/``name`` on both the ``logging`` module and
    ``logging.Logger`` (so ``logging.name(...)`` and ``logger.name(...)``
    both work), registers the level name, refreshes the ``-v``/``-q``
    verbosity table (:func:`initverbose`) so the new level immediately
    participates in it regardless of whether anything has configured logging
    yet, and optionally gives it a color for
    :class:`DefaultFormatter`.

    Unless ``force`` is set, refuses (``ValueError``) to clobber an existing
    ``logging``/``Logger`` attribute that duho itself did not install --
    guarding both the given ``NAME`` (as before) and its lower-cased method
    name: a level named e.g. ``LOG`` or ``EXCEPTION`` would otherwise
    silently replace ``logging.log``/``Logger.exception``. A repeat call for
    a name duho already installed itself (tracked in
    :data:`_installed_level_names`) at the same number is a harmless no-op
    either way, but at a different number it raises (renumber with
    ``force=True``); a name that collides with something duho did NOT install --
    an unrelated stdlib constant like ``logging.BASIC_FORMAT``, not just a level/method name --
    raises instead of silently no-oping, the same guard the lower-cased
    check below already gave method names.
    """
    name = name.upper()
    lname = name.lower()
    if not force:
        if name in _installed_level_names:
            if getattr(_logging, name, level) == level:
                return
            raise ValueError(
                f"add_logging_level: {name!r} is already registered at level "
                f"{getattr(_logging, name)}, not {level}; pass force=True to "
                "renumber it deliberately"
            )
        if hasattr(_logging, name):
            raise ValueError(
                f"add_logging_level: {name!r} already exists as a logging "
                "attribute that duho did not install; pass force=True to "
                "replace it deliberately"
            )
        for existing in (
            getattr(_logging, lname, None),
            getattr(_logging.getLoggerClass(), lname, None),
        ):
            if existing is not None and getattr(existing, "_duho_level_", None) is None:
                raise ValueError(
                    f"add_logging_level: {lname!r} already exists as a "
                    "logging attribute/method that duho did not install; "
                    "pass force=True to replace it deliberately"
                )

    setattr(_logging, name, level)
    _logging.addLevelName(level, name)

    def log_logger(self: _logging.Logger, message: str, *args, **kwargs):
        if self.isEnabledFor(level):
            # stacklevel=2 skips this wrapper's own frame so the record
            # attributes the CALLER (`logger.<name>(...)`), not
            # `duho/logging.py:log_logger`, as the log site.
            kwargs.setdefault("stacklevel", 2)
            self.log(level, message, *args, **kwargs)

    log_logger._duho_level_ = level  # type: ignore[attr-defined]

    def log_root(msg, *args, **kwargs):
        # Same stacklevel reasoning as log_logger, for the module-level
        # `logging.<name>(...)` form. Calls the ROOT LOGGER'S `.log()`
        # directly rather than the module-level `logging.log(...)` wrapper:
        # that wrapper is an extra stdlib frame on top of `Logger.log`, which
        # made `stacklevel=2` under-count by one and misattribute the record
        # to this wrapper itself on Python 3.9 (3.11+'s smarter frame-skipping
        # happened to paper over the extra hop, but not reliably enough to
        # depend on).
        kwargs.setdefault("stacklevel", 2)
        _logging.root.log(level, msg, *args, **kwargs)

    log_root._duho_level_ = level  # type: ignore[attr-defined]

    setattr(_logging.getLoggerClass(), lname, log_logger)

    if color is not None:
        DefaultFormatter.COLORS[level] = _getcolor(color)

    setattr(_logging, lname, log_root)
    _installed_level_names.add(name)
    initverbose()


class DefaultFormatter(_logging.Formatter):  # type: ignore
    """Log formatter with colored output."""

    COLORS: dict[int, str] = {
        _logging.DEBUG: _asicode(34),  # Fore.BLUE
        _logging.INFO: _asicode(32),  # Fore.GREEN
        _logging.WARNING: _asicode(33),  # Fore.YELLOW
        _logging.ERROR: _asicode(31),  # Fore.RED
        _logging.CRITICAL: _asicode(31, 47),  # Fore.RED + Back.WHITE
    }
    RESET_ALL = _asicode(0)

    def __init__(
        self,
        fmt="%(asctime)s | %(levelname)8s | %(name)s: %(message)s",
        datefmt=None,
        style: "_logging._FormatStyle" = "%",
        validate=True,
        *,
        color: bool = True,
    ) -> None:
        # Direct construction stays always-colored by default (existing
        # behavior, and what tests/test_logging_color.py pins); the one
        # place duho itself decides otherwise is `init_stderr_logging`, which
        # passes `color=False` when the target stream isn't a TTY or
        # NO_COLOR/FORCE_COLOR says so.
        self._duho_color_enabled = color
        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        record = _copy.copy(record)
        record.levelname = record.levelname.center(_LEVELSIZE)
        if self._duho_color_enabled:
            color = self.COLORS.get(record.levelno, None)
            if color:
                record.levelname = f"{color}{record.levelname}{self.RESET_ALL}"
        return super().format(record)


VERBOSE_LEVELS: "dict[int, list[str]]" = {}
VERBOSE_HELP = ""
_LEVELSIZE = 4


def initverbose() -> None:
    """Initialize verbose level mappings."""
    global VERBOSE_LEVELS, VERBOSE_HELP, _LEVELSIZE

    levels: "dict[int, list[str]]" = {}
    for name, loglevel in get_level_names_mapping().items():
        if not loglevel:
            continue
        aliases: list[str] = levels.setdefault(loglevel, [])
        _LEVELSIZE = max(_LEVELSIZE, len(name))
        if name not in aliases:
            aliases.append(name)

    # Prefer the CANONICAL name (what `logging.getLevelName` returns, e.g.
    # "WARNING") as each level's first/displayed alias. Iteration order of
    # `get_level_names_mapping()` lists stdlib's deprecated "WARN" before
    # "WARNING", so without this the verbosity help text advertised the
    # deprecated alias.
    for loglevel, aliases in levels.items():
        canonical = _logging.getLevelName(loglevel)
        if canonical in aliases and aliases[0] != canonical:
            aliases.remove(canonical)
            aliases.insert(0, canonical)

    VERBOSE_LEVELS = dict(sorted(levels.items(), key=lambda l: l[0], reverse=True))
    VERBOSE_HELP = ", ".join([aliases[0] for aliases in VERBOSE_LEVELS.values()])


def parse_loglevels(
    text: str, itemdivider: str = ",", valkey_separator: str = ":"
) -> "dict[str, int]":
    """Parse a ``[NAME:]LEVEL[,NAME:LEVEL...]`` log level specification.

    ``LEVEL`` is matched against the registered level names -- first by its
    EXACT text (so a custom level registered under a lowercase/mixed-case
    name, e.g. ``logging.addLevelName(25, "notice")``, matches directly),
    then by its upper-cased form (so the standard ``DEBUG``, ``debug`` and
    ``Debug`` spellings are all still accepted) -- or may be a plain integer.
    Surrounding whitespace around a name or level is stripped. An entry that
    resolves to neither raises ``argparse.ArgumentTypeError`` naming the bad
    token -- argparse reports this as a normal "invalid value" usage error
    instead of the entry silently disappearing from the returned mapping.
    """
    levels: dict[str, int] = {}
    levelmapping = get_level_names_mapping()

    for entry in text.split(itemdivider):
        name, *level = entry.split(valkey_separator, maxsplit=1)
        if not level:
            level_text = name.strip()
            name = ""
        else:
            level_text = level[0].strip()
        name = name.strip()

        resolved = levelmapping.get(level_text)
        if resolved is None:
            resolved = levelmapping.get(level_text.upper())
        if resolved is None:
            if _re.fullmatch(r"-?[0-9]+", level_text):
                resolved = int(level_text)
            else:
                raise _argparse.ArgumentTypeError(
                    f"invalid log level {level_text!r} in {entry!r} (expected "
                    f"one of {', '.join(levelmapping)}, or an integer)"
                )
        levels[name] = resolved
    return levels


#: Tag set on the handler ``init_stderr_logging`` installs, so a later call
#: (direct or via ``duho.main``/``duho.app``) can tell it already ran for
#: this logger and skip adding a second one.
_STDERR_HANDLER_TAG = "_duho_stderr_handler_"


def init_stderr_logging(
    name: "str | None" = None, level: "int | None" = None
) -> "_logging.Logger":
    """Initialize logging to stderr with color support.

    Idempotent: a repeat call on the same logger (directly, or via
    ``duho.main``/``duho.app`` each time they run) finds the handler this
    function installed last time (tagged, never matched by identity/count)
    and does not add a second one -- calling it twice no longer duplicates
    every log line. ``level``, when given, is still (re)applied.

    Color is gated the same way duho's own ``--help`` formatters are:
    ANSI only when the stream is a TTY, off when ``NO_COLOR`` is set, forced
    on with ``FORCE_COLOR`` -- so redirecting/piping output, or a CI log,
    never receives raw escape bytes. When color is enabled and colorama is
    importable, ``colorama.just_fix_windows_console()`` is called so a legacy
    Windows console renders the codes instead of showing them literally.
    """
    initverbose()
    logger = _logging.getLogger(name)
    if not any(getattr(h, _STDERR_HANDLER_TAG, False) for h in logger.handlers):
        # Imported lazily (not at module top): `formatters` imports FROM this
        # module (`_asicode`), so a module-level import here would be a cycle.
        # By the time this function actually runs, `duho.logging` has already
        # finished initializing, so the delayed import is safe.
        from . import formatters as _formatters

        color = _formatters._color_enabled(_sys.stderr)
        if color:
            colorama = _resolve_colorama()
            just_fix = getattr(colorama, "just_fix_windows_console", None)
            if callable(just_fix):
                just_fix()
        handler = _logging.StreamHandler(_sys.stderr)
        setattr(handler, _STDERR_HANDLER_TAG, True)
        handler.setFormatter(DefaultFormatter(color=color))
        logger.addHandler(handler)
    if level:
        logger.setLevel(level)
    return logger


#: Environment variable enabling framework tracebacks. When set to a truthy
#: value, every framework site that catches an exception and logs only its
#: ``str()`` instead logs the full traceback (``exc_info=True``).
TRACEBACK_ENV = "DUHO_TRACEBACK"

#: Values of :data:`TRACEBACK_ENV` that mean "on" (case-insensitive, after
#: stripping) -- the shared ``_compat.BOOL_TRUE`` table, so this and every
#: other declared bool field agree on what "on" means. An empty/unset
#: variable, an explicit "off" spelling, AND an unrecognized value are all
#: off: unlike a declared bool field (which rejects an unrecognized
#: string), an env var this framework itself only ever reads as a same-
#: process safety switch defaults unknown input to the SAFER "off" reading
#: rather than raising.
_TRUTHY = _BOOL_TRUE


def traceback_enabled() -> bool:
    """Return whether framework error logs should carry a full traceback.

    Reads :data:`TRACEBACK_ENV` (``DUHO_TRACEBACK``) from the process
    environment on EVERY call rather than caching it, so a test (or an app that
    sets it mid-run) can flip the switch without re-importing duho. The read is
    a dict lookup -- cheap enough to sit on an error path.

    Off by default: a CLI user seeing a framework warning wants the message, not
    a stack. A developer debugging *where* a step/command/target actually failed
    exports ``DUHO_TRACEBACK=1`` and gets the traceback for free at every site.
    """
    return _os.environ.get(TRACEBACK_ENV, "").strip().lower() in _TRUTHY


def log_exception(
    logger: "_logging.Logger",
    msg: str,
    *args: object,
    level: int = _logging.ERROR,
) -> None:
    """Log a caught exception, with a traceback iff ``DUHO_TRACEBACK`` is set.

    The framework's resilient paths (discovery skipping a bad command, a runpath
    step failing, a fan-out target raising) deliberately do NOT propagate the
    exception, which means the stack -- the only thing that says *where* it broke
    -- is lost unless it is logged. Logging it unconditionally would bury an
    ordinary "optional dependency missing" warning under 30 frames, so this
    helper makes it opt-in via :func:`traceback_enabled`.

    Call it from inside an ``except`` block, where ``exc_info`` has an exception
    to render. When disabled the ``exc_info`` kwarg is omitted entirely rather
    than passed as ``False`` -- both suppress the traceback, but ``False`` is
    recorded verbatim on ``LogRecord.exc_info``, so a handler or test inspecting
    that attribute would see ``False`` where every other un-decorated record in
    the process carries ``None``.

    ``stacklevel=2`` skips this helper's own frame, so the record attributes
    the caller's except-block, not ``duho/logging.py:log_exception``, as the
    log site.
    """
    if traceback_enabled():
        logger.log(level, msg, *args, exc_info=True, stacklevel=2)
    else:
        logger.log(level, msg, *args, stacklevel=2)


def _register_trace_level() -> None:
    """Install ``TRACE`` at import, tolerating one the process already defines.

    An existing integer ``logging.TRACE`` is reused (named and coloured at its
    own number); a foreign ``trace`` attribute is left in place. Never raises.
    """
    try:
        add_logging_level("TRACE", _logging.DEBUG - 5, color=_asicode(36))
        return
    except ValueError:
        pass
    level = getattr(_logging, "TRACE", None)
    if isinstance(level, int) and not isinstance(level, bool):
        if _logging.getLevelName(level) == f"Level {level}":
            _logging.addLevelName(level, "TRACE")
        DefaultFormatter.COLORS.setdefault(level, _asicode(36))
    initverbose()


_register_trace_level()
initverbose()

__all__ = [
    "add_logging_level",
    "DefaultFormatter",
    "VERBOSE_LEVELS",
    "VERBOSE_HELP",
    "parse_loglevels",
    "init_stderr_logging",
    "initverbose",
    "TRACEBACK_ENV",
    "traceback_enabled",
    "log_exception",
]
