import copy as _copy
import logging as _logging
import sys as _sys
import typing as _ty

from ._compat import get_level_names_mapping

if _ty.TYPE_CHECKING:
    from logging import *  # type:ignore

    import colorama as _colorama  # type:ignore

    TRACE: int

#: Cached colorama module (or ``None`` when unavailable / not yet resolved).
#: ``import colorama`` costs ~3-5 ms and is only ever needed to translate a
#: NAMED color spec ("red", "red+white") into an ANSI sequence -- the built-in
#: level colors are hard-coded ANSI (see ``DefaultFormatter.COLORS``), so a
#: plain ``import duho`` must not pay it. Resolved lazily on first use in
#: ``_getcolor`` and memoized here (``False`` = "tried, absent") (P4).
_color: "object | bool | None" = False


def _resolve_colorama():
    """Return the imported ``colorama`` module, or ``None`` if unavailable.

    Imports ``colorama`` on first call and caches the result (the module or
    ``None``) on the module-global ``_color``, so the potentially-missing
    dependency is probed exactly once and only when a named color is actually
    requested (P4). The sentinel ``False`` means "not yet probed".
    """
    global _color
    if _color is False:
        try:
            import colorama as _colorama  # type:ignore
        except ImportError:
            _colorama = None  # type:ignore
        _color = _colorama
    return _color


def __getattr__(name: str):
    return getattr(_logging, name)


def _asicode(*codes):
    return "".join(["\033[" + str(c) + "m" for c in codes])


def _getcolor(color: str):
    """Resolve a color spec to an ANSI escape sequence.

    A named spec is ``"fore"`` or ``"fore+back"`` (e.g. ``"red"``, ``"red+white"``);
    it is resolved via colorama's ``Fore``/``Back``. When colorama is absent or a
    name does not resolve, an empty string is returned (never the raw name/compound
    string). Anything that is not a bare name (already an ANSI escape like
    ``"\\033[31m"``) is passed through unchanged.
    """
    # A named color spec is letters plus an optional single "+" separator; the
    # old ``color.isalpha()`` check rejected the documented "fore+back" form
    # (the "+" is not alpha), so the compound spec was returned verbatim (M9).
    if color.replace("+", "").isalpha():
        colorama = _resolve_colorama()
        if not colorama:
            return ""
        fore, back, *_ = color.split("+") + ["", ""]
        fore = (getattr(colorama.Fore, fore.upper(), "") or "") if fore else ""
        back = (getattr(colorama.Back, back.upper(), "") or "") if back else ""
        return fore + back

    return color


def add_logging_level(name: str, level: int, force=False, color: 'str | None' = None):
    """Register a custom log level."""
    name = name.upper()
    if hasattr(_logging, name) and not force:
        return
    setattr(_logging, name, level)
    _logging.addLevelName(level, name)

    def log_logger(self: _logging.Logger, message: str, *args, **kwargs):
        if self.isEnabledFor(level):
            self._log(level, message, args, **kwargs)

    name = name.lower()
    setattr(_logging.getLoggerClass(), name, log_logger)

    def log_root(msg, *args, **kwargs):
        _logging.log(level, msg, *args, **kwargs)

    if color is not None:
        DefaultFormatter.COLORS[level] = _getcolor(color)

    setattr(_logging, name, log_root)


class DefaultFormatter(_logging.Formatter):  # type:ignore
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
    ) -> None:
        self._levelsize: 'int | None' = None
        super().__init__(fmt, datefmt, style, validate)

    def format(self, record):
        record = _copy.copy(record)
        levelsize = self._levelsize if self._levelsize is not None else _LEVELSIZE
        record.levelname = record.levelname.center(levelsize)
        color = self.COLORS.get(record.levelno, None)
        if color:
            record.levelname = f"{color}{record.levelname}{self.RESET_ALL}"
        return super().format(record)


VERBOSE_LEVELS: 'dict[int, list[str]]' = {}
VERBOSE_HELP = ""
_LEVELSIZE = 4


def initverbose():
    """Initialize verbose level mappings."""
    global VERBOSE_LEVELS, VERBOSE_HELP, _LEVELSIZE

    for name, loglevel in get_level_names_mapping().items():
        if not loglevel:
            continue
        aliases: list[str] = VERBOSE_LEVELS.setdefault(loglevel, [])
        _LEVELSIZE = max(_LEVELSIZE, len(name))
        if name not in aliases:
            aliases.append(name)

    VERBOSE_LEVELS = dict(
        sorted(VERBOSE_LEVELS.items(), key=lambda l: l[0], reverse=True)
    )

    VERBOSE_HELP = ", ".join([aliases[0] for aliases in VERBOSE_LEVELS.values()])


def parse_loglevels(text: str, itemdivider: str = ",", valkey_separator=":"):
    """Parse a log level specification string."""
    levels: dict[str, int] = {}
    levelmapping = get_level_names_mapping()

    for entry in text.split(itemdivider):
        name, *level = entry.split(valkey_separator, maxsplit=1)
        if not level:
            level = name
            name = ""
        else:
            level = level[0]
        level = levelmapping.get(level)
        if level is not None:
            levels[name] = level
    return levels


def init_stderr_logging(name=None, level: 'int | None' = None):
    """Initialize logging to stderr with color support."""
    initverbose()
    handler = _logging.StreamHandler(_sys.stderr)
    logger = _logging.getLogger(name)
    if level:
        logger.setLevel(level)
    logger.addHandler(handler)
    handler.setFormatter(DefaultFormatter())
    return logger


#: Environment variable enabling framework tracebacks. When set to a truthy
#: value, every framework site that catches an exception and logs only its
#: ``str()`` instead logs the full traceback (``exc_info=True``).
TRACEBACK_ENV = "DUHO_TRACEBACK"

#: Values of :data:`TRACEBACK_ENV` that mean "off". Anything else (including the
#: empty-but-present case being absent from this set is deliberate: ``DUHO_TRACEBACK=``
#: with an empty value counts as off) enables tracebacks.
_FALSEY = frozenset({"", "0", "false", "no", "off"})


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
    import os as _os

    return _os.environ.get(TRACEBACK_ENV, "").strip().lower() not in _FALSEY


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
    """
    if traceback_enabled():
        logger.log(level, msg, *args, exc_info=True)
    else:
        logger.log(level, msg, *args)


add_logging_level("TRACE", _logging.DEBUG - 5, color=_asicode(36))
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
