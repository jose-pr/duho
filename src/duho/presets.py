"""Pre-configured argument classes for common patterns."""

import logging as _logging
import typing as _ty

from . import logging as _duho_logging
from .args import Args, NS, UpdateAction, _command_name as _command_name
from .logging import parse_loglevels


def _apply_loglevels(ns: "Args", default_logger: str) -> "dict[str, int]":
    """Apply parsed ``loglevels``/``verbose``/``quiet`` fields to loggers.

    Module-level so it works on ANY object carrying
    ``LoggingArgs``'s data fields, not just a ``LoggingArgs`` instance --
    notably a plain ``Cmd`` leaf dispatched under a ``class MyApp(LoggingArgs,
    Cli)`` root (the README's recommended app shape). argparse copies the
    root's parsed ``-v``/``-q``/``--loglevel`` values onto the shared instance
    regardless of which class ends up constructed, but only a ``LoggingArgs``
    subclass has the ``_set_loglevels_``/``_logger_`` MEMBERS to apply them
    through -- previously that made verbosity flags a silent no-op on such a
    leaf. ``default_logger`` names the logger that receives the -v/-q-derived
    (or a bare ``--loglevel LEVEL``) level: ``LoggingArgs._set_loglevels_``
    passes its own ``_logger_.name``; ``duho.main``/``duho.app`` fall back to
    the ROOT class's own command name when dispatching a leaf that has no
    ``_logger_`` of its own.

    Prefers ``ns._verbose_loglevel_()`` -- a bound method, so a subclass
    override is honored -- over the base implementation, which is used
    only when ``ns``'s own class doesn't define one at all (again, the plain
    ``Cmd`` leaf case).
    """
    loglevels = ns.loglevels.copy()
    # Names the user EXPLICITLY passed via `--loglevel name:LEVEL` -- captured
    # before `default_logger` is defaulted in below. Only these get the
    # descendant-subtree walk; the -v/-q-derived (or bare `--loglevel LEVEL`)
    # entry for `default_logger` must not force levels onto a library's own
    # child loggers on every ordinary dispatch (see the walk below).
    explicit_names = set(loglevels)
    # A bare `--loglevel LEVEL` (parsed as {"": LEVEL}) should raise the
    # app's OWN logger, not just root -- but only when nothing more specific
    # (-v/-q, or an explicit `name:LEVEL` entry for this logger) already
    # claims the default. An explicit `-v`/`-q` still wins over a bare level.
    default = loglevels.get("") if not (ns.verbose or ns.quiet) else None
    if default is None:
        verbose_loglevel = getattr(ns, "_verbose_loglevel_", None)
        default = (
            verbose_loglevel()
            if verbose_loglevel is not None
            else LoggingArgs._verbose_loglevel_(ns)
        )
    # An explicit `--loglevel app:LEVEL` covers the whole `app.*` subtree, so
    # it must also reach the dispatched command's own logger when that logger
    # sits inside it (`app.scan`). Injecting the -v/-q default for that logger
    # here would give it its OWN level, applied after (and overriding) the
    # ancestor the user actually named.
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
            # Python's logging hierarchy only derives an unset child's
            # EFFECTIVE level from its parent -- a child that already has its
            # OWN explicit level (set by an earlier import, a library, or a
            # previous `--loglevel`) keeps it regardless of what happens to
            # `name` afterwards. `--loglevel app:LEVEL` is documented as
            # applying to the app.* SUBTREE, so also force the level onto
            # every ALREADY-EXISTING descendant logger -- but only for a name
            # the user EXPLICITLY named here (`name in explicit_names`), never
            # for the `default_logger` entry `setdefault` just injected from
            # -v/-q or a bare `--loglevel LEVEL`: that entry runs on every
            # ordinary dispatch, and forcing it onto every already-existing
            # `app.*` child would pin a library's own hierarchical logging
            # control (`logging.getLogger("app.child").setLevel(...)`) after
            # a single in-process dispatch. (No-op for the bare `""`
            # root-logger key -- every logger already descends from actual
            # root, and `""` is never in `explicit_names` as a subtree name.)
            prefix = name + "."
            logger_dict = _logging.Logger.manager.loggerDict
            for existing_name in list(logger_dict):
                if not existing_name.startswith(prefix):
                    continue
                # Look up the raw registry entry -- do NOT call
                # `logging.getLogger(existing_name)` here, which would
                # PROMOTE a `PlaceHolder` (an as-yet-undeclared ancestor
                # segment) into a real `Logger` as a side effect of this
                # walk. Skip anything that isn't already a real `Logger`.
                existing = logger_dict.get(existing_name)
                if not isinstance(existing, _logging.Logger):
                    continue
                # A child still at NOTSET already inherits its effective
                # level from its parent for free -- pinning it here is
                # exactly what breaks that hierarchical control the next
                # time the library itself calls `.setLevel(...)` on an
                # ancestor. Only touch a child that already has its OWN
                # explicit level (matching the comment above).
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

    # Seed `_duho_constants_` like `Args`/`Cmd`/`Cli` do. LoggingArgs
    # used to be deliberately left UNSEEDED so `_introspect._class_constants`
    # would AST-scan this class body for a trailing docstring + flags-tuple
    # after each field. Every field now carries its flags/help directly in
    # its own `NS(...)` instead, so that scan is no longer needed -- which
    # means duho's own `-v`/`-q`/`--loglevel` no longer silently change shape
    # (to `--verbose`/`--quiet`/`--loglevels`, derived from the bare field
    # names) under a PyInstaller/.pyc-only/Nuitka build that ships no .py
    # source for duho itself to scan.
    _duho_constants_: dict = {}

    loglevels: _ty.Annotated[
        dict[str, int],
        NS(
            type=parse_loglevels,
            action=UpdateAction,
            flags=("--loglevel",),
            metavar="[NAME:]LEVEL[,...]",
            help=lambda: (
                "Set a logger's level, e.g. --loglevel mypkg:DEBUG "
                f"(levels: {_duho_logging.VERBOSE_HELP})"
            ),
        ),
    ] = {}

    # The shipped header and this class's own docstring have long promised
    # `--verbose`/`--quiet` alongside `-v`/`-q`; only the short forms were
    # ever actually declared. Adding the long spellings (rather than
    # correcting the docs to match the shorter reality) is additive and a
    # PATCH pre-1.0.
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

    def _verbose_loglevel_(self) -> int:
        """Convert verbose/quiet count to a NUMERIC log level.

        ``VERBOSE_LEVELS`` is keyed by int, so this returns the level number
        (e.g. ``logging.DEBUG``), not a level name.
        """
        levels = list(_duho_logging.VERBOSE_LEVELS.keys())
        base = levels.index(_logging.INFO)
        index = base + self.verbose - self.quiet
        index = max(0, min(index, len(levels) - 1))
        return levels[index]

    def _set_loglevels_(self) -> "dict[str, int]":
        """Apply parsed log levels to loggers."""
        return _apply_loglevels(self, self._logger_.name)

    @property
    def _logger_(self) -> "_logging.Logger":
        """Get logger scoped to this parser's name.

        Resolved lazily and defensively, rather than
        ``getattr(self, "_logger_name_", self._parsername_)``: that default
        argument is evaluated EAGERLY (before the ``getattr`` lookup even
        runs), so it always read ``self._parsername_`` -- raising
        ``AttributeError`` for a directly-constructed command whose class
        parser was never built (a supported pattern), even when
        ``_logger_name_`` WAS declared and would have made the
        ``_parsername_`` read unnecessary. It also depended on `_parsername_`
        being PERSISTED onto the class by parser construction, which duho no
        longer does. Falls back to ``_command_name(type(self))`` -- the same
        own-class-dict rule every other subcommand-name reader uses --
        so a directly-built or freshly-declared command always has a working
        logger, parsed or not, and a subclass that never declared its own
        ``_parsername_`` is never misnamed after a sibling's/base's build.
        """
        name = getattr(self, "_logger_name_", None)
        if name is None:
            name = _command_name(type(self))
        return _logging.getLogger(name)


__all__ = ["LoggingArgs"]
