import copy as _copy
import logging as _logging
import sys as _sys
import typing as _ty

from ._compat import get_level_names_mapping

if _ty.TYPE_CHECKING:
    from logging import *  # type:ignore

    import colorama as __coloroma #type:ignore

    TRACE: int

try:
    import colorama as _color  # type: ignore

except ImportError:

    _color = _ty.cast("__coloroma", None)


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
        if not _color:
            return ""
        fore, back, *_ = color.split("+") + ["", ""]
        fore = (getattr(_color.Fore, fore.upper(), "") or "") if fore else ""
        back = (getattr(_color.Back, back.upper(), "") or "") if back else ""
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
]
