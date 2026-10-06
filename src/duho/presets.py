"""Pre-configured argument classes for common patterns."""

import argparse as _argparse
import logging as _logging
import typing as _ty

from . import logging as _duho_logging
from .args import Args, NS, UpdateAction
from .args._naming import _command_name as _command_name
from .args._entry import _logger_name_for
from .logging import parse_loglevels


def _loglevels_type(text: str) -> "dict[str, int]":
    return parse_loglevels(text)


def _loglevel_value(text: str) -> int:
    """One level (a name or an integer) as a number, for a config table value."""
    try:
        levels = parse_loglevels(text)
    except _argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from None
    if list(levels) != [""]:
        raise ValueError(f"expected a single level name or number, got {text!r}")
    return levels[""]


# Read by the layering code to convert each value of a `[loglevels]` table.
_loglevels_type.value_factory = _loglevel_value  # type: ignore[attr-defined]


def _stepped_level(base: "_ty.Union[int, str]", verbose: int, quiet: int) -> int:
    """The numeric level ``verbose`` ``-v`` and ``quiet`` ``-q`` steps reach from ``base``.

    ``base`` is a level number or name and must be a registered level.
    """
    number = _loglevel_value(base) if isinstance(base, str) else base
    levels = list(_duho_logging.VERBOSE_LEVELS.keys())
    if number not in levels:
        raise ValueError(f"_base_loglevel_ {base!r} is not a registered log level")
    index = levels.index(number) + verbose - quiet
    return levels[max(0, min(index, len(levels) - 1))]


def _apply_loglevels(
    ns: "Args", default_logger: str, root_cls: "_ty.Optional[type]" = None
) -> "dict[str, int]":
    """Apply parsed ``loglevels``/``verbose``/``quiet`` fields to loggers.

    Module-level so it works on any object carrying ``LoggingArgs``'s data
    fields, such as a plain ``Cmd`` leaf under a ``LoggingArgs`` root, which
    lacks ``_set_loglevels_``/``_logger_``. ``default_logger`` receives the
    -v/-q level (or a bare ``--loglevel LEVEL``). Prefers
    ``ns._verbose_loglevel_()``, so a subclass override is honored, over a step
    from ``root_cls``'s ``_base_loglevel_``.
    """
    loglevels = ns.loglevels.copy()
    # Names passed explicitly via `--loglevel name:LEVEL`, captured before
    # `default_logger` is added: only these get the descendant walk below.
    explicit_names = set(loglevels)
    # A bare `--loglevel LEVEL` ({"": LEVEL}) raises the app's own logger too,
    # unless -v/-q (which win) or an explicit entry already claims the default.
    default = loglevels.get("") if not (ns.verbose or ns.quiet) else None
    if default is None:
        verbose_loglevel = getattr(ns, "_verbose_loglevel_", None)
        if verbose_loglevel is not None:
            default = verbose_loglevel()
        else:
            base = getattr(root_cls, "_base_loglevel_", LoggingArgs._base_loglevel_)
            default = _stepped_level(base, ns.verbose, ns.quiet)
    # An explicit `--loglevel app:LEVEL` covers `app.*`, so skip the -v/-q default
    # for a dispatched logger inside it (`app.scan`): its own level would override
    # the ancestor the user named.
    covered = any(
        default_logger == name or default_logger.startswith(name + ".")
        for name in explicit_names
        if name
    )
    if not covered:
        loglevels.setdefault(default_logger, default)
    for name, level in loglevels.items():
        _logging.getLogger(name).setLevel(level)
        if name and name in explicit_names:
            # `--loglevel app:LEVEL` applies to the `app.*` subtree, so also set
            # existing descendants that have their own level. Only for explicit
            # names: the injected -v/-q default would pin a library's own levels.
            prefix = name + "."
            logger_dict = _logging.Logger.manager.loggerDict
            for existing_name in list(logger_dict):
                if not existing_name.startswith(prefix):
                    continue
                # Read the raw registry entry: `getLogger` would promote a
                # `PlaceHolder` to a real Logger. Skip anything that is not one.
                existing = logger_dict.get(existing_name)
                if not isinstance(existing, _logging.Logger):
                    continue
                # A NOTSET child already inherits from its parent; pinning it would
                # break the library's own later `.setLevel` on an ancestor.
                if existing.level == _logging.NOTSET:
                    continue
                existing.setLevel(level)
    return loglevels


class LoggingArgs(Args):
    """Args subclass with built-in -v/-q and --loglevel support.

    ``LoggingArgs`` is a **data mixin** (verbosity fields + ``_set_loglevels_``
    + the ``_logger_`` property); it defines no ``__call__`` and is
    NOT itself runnable. Since the ``Args``/``Cmd`` split, combine it
    with ``Cmd`` to get a runnable command with logging::

        class MyApp(LoggingArgs, Cmd):
            _version_ = "1.2.3"

            def __call__(self):
                self._logger_.info("running")
                return 0

    **Recommended base order: ``(LoggingArgs, Cmd)``** -- data mixin first,
    executable base last (reads "add logging to a command"). Both orders
    resolve correctly because ``LoggingArgs`` overrides no ``Cmd`` member;
    ``_logger_``/``_set_loglevels_`` come from ``LoggingArgs`` and
    ``__call__`` from ``Cmd`` regardless of order.

    Set a class attr ``_version_`` to opt into a ``--version`` flag; no
    separate preset class is needed. ``--version`` prints
    ``"%(prog)s 1.2.3"`` and exits 0. It is skipped if a ``version``-dest
    action already exists (e.g. supplied by a parent parser).
    """

    # Seed `_duho_constants_` like `Args`/`Cmd`/`Cli`: every field carries its
    # flags in `NS(...)`, so no source scan is needed (and none is possible
    # in a build that ships no .py source).
    _duho_constants_: dict = {}

    loglevels: _ty.Annotated[
        dict[str, int],
        NS(
            type=_loglevels_type,
            action=UpdateAction,
            flags=("--loglevel",),
            metavar="[NAME:]LEVEL[,...]",
            help=lambda: (
                "Set a logger's level, e.g. --loglevel mypkg:DEBUG "
                f"(levels: {_duho_logging.VERBOSE_HELP})"
            ),
        ),
    ] = {}

    # Both the short and long spellings, as the shipped header documents.
    verbose: _ty.Annotated[
        int,
        NS(
            action="count",
            flags=("-v", "--verbose"),
            help="Increase verbosity (repeatable)",
        ),
    ] = 0

    quiet: _ty.Annotated[
        int,
        NS(
            action="count",
            flags=("-q", "--quiet"),
            help="Decrease verbosity (repeatable)",
        ),
    ] = 0

    #: The level ``-v``/``-q`` step from: a level number or name. A plain
    #: ``Cmd`` leaf under a ``LoggingArgs`` root uses the root's value.
    _base_loglevel_: _ty.Union[int, str] = _logging.INFO

    def _verbose_loglevel_(self) -> int:
        """Convert verbose/quiet count to a NUMERIC log level.

        ``VERBOSE_LEVELS`` is keyed by int, so this returns the level number
        (e.g. ``logging.DEBUG``), not a level name. The count steps from
        ``_base_loglevel_``.
        """
        return _stepped_level(self._base_loglevel_, self.verbose, self.quiet)

    def _set_loglevels_(self) -> "dict[str, int]":
        """Apply parsed log levels to loggers."""
        return _apply_loglevels(self, self._logger_.name)

    @property
    def _logger_(self) -> "_logging.Logger":
        """The logger this command's verbosity flags apply to.

        A ``_logger_name_`` declared on this class or on the root that
        dispatched it, else the application's name (see
        ``duho.args._logger_name_for``). Works for a directly constructed
        command whose parser was never built.
        """
        return _logging.getLogger(_logger_name_for(self))


__all__ = ["LoggingArgs"]
