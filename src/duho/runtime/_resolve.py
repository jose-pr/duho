import logging as _logging
import typing as _ty
from pathlib import Path as _Path

from ..discovery import (
    Command as _Command,
    discover_commands as _discover_commands,
    discover_entry_points as _discover_entry_points,
)
from ..args._naming import _command_name as _command_name
from ..logging import log_exception as _log_exception

if _ty.TYPE_CHECKING:  # pragma: no cover - type-checking only
    from ..env import Env as _Env


_LOGGER = _logging.getLogger(__package__)


def _cmds_path_commands(env: "_Env | None") -> "list[_Command]":
    """Resolve every command discoverable from ``env``'s ``CMDS_PATH``.

    Returns ``[]`` if ``env`` is ``None``, ``CMDS_PATH`` is unset/empty, or
    ``env`` doesn't support the expected interface -- all best-effort, never
    raises for a resolution problem (a per-entry issue is logged and that
    entry skipped; see below). Only touches ``CMDS_PATH`` when it is actually
    set and non-empty: a missing value must NOT be split/globbed -- that is
    what turned an unset var into "import every ``.py`` in the CWD".
    Splits on the OS path separator (``os.pathsep``; ``PATHSEP`` overrides),
    NOT a hard-coded ``":"`` -- otherwise a Windows ``"C:\\..."`` drive letter
    is mis-split into a bogus ``"C"`` path. See :meth:`duho.env.Env.paths`.

    **Empty segments never mean the CWD (a security-relevant fix).** ``env.paths``
    already drops an empty/whitespace-only segment before converting it to a
    ``Path`` (a leading, trailing, or doubled separator -- the common
    ``X="$X:/extra"`` append idiom run while ``X`` was unset -- must never
    resolve to ``Path('.')`` and glob-import/execute the current directory).
    This function does NOT trust that alone, since ``env`` is duck-typed and
    may not be a real :class:`duho.env.Env`: it re-requests the raw STRING
    segments (``ty=str``, no ``Path`` conversion yet) and filters blank ones
    itself before ever constructing a ``Path`` -- a defense-in-depth second
    layer that holds even for a caller-supplied ``env`` whose own ``paths()``
    does not filter. (An explicit ``"."`` segment is still honoured.)

    **A stale entry is skipped, not fatal.** Each entry is expanded
    with ``~`` (``Path.expanduser()``) and, if it does not resolve to an
    existing directory, logged at WARNING and skipped -- a removed plugin
    directory or an unexpanded ``~`` must not take down every invocation,
    built-ins and ``--help`` included. Discovery's own resilience still
    applies per entry (an ``ImportError`` from a single bad command file is
    logged and skipped; a ``SyntaxError`` still propagates).

    **One bad entry (a bare drive, or one resolving to the CWD -- see
    :meth:`duho.env.Env.paths`) must not drop every OTHER entry.** Requests
    ``strict=False`` so :meth:`Env.paths` skips a rejected segment instead of
    raising for the whole call (a security/robustness fix -- raising here used
    to be swallowed by the bare ``except Exception`` below, silently dropping
    the ENTIRE ``CMDS_PATH``, valid entries included, with no log at all).
    Each rejected segment is collected via ``on_reject`` and logged at
    WARNING once resolution succeeds. A duck-typed ``env`` (not a real
    :class:`duho.env.Env`) may not accept those keywords at all -- caught
    separately and retried with the plain two-arg call, so such a caller
    keeps its previous best-effort behavior unchanged.
    """
    if env is None:
        return []
    try:
        raw = env.get("CMDS_PATH")
    except Exception:  # pragma: no cover - env is best-effort here
        raw = None
    if not raw:
        return []
    rejected: "list[tuple[str, str]]" = []
    try:
        segments = env.paths(
            "CMDS_PATH",
            ty=str,
            strict=False,
            on_reject=lambda segment, reason: rejected.append((segment, reason)),
        )
    except TypeError:
        # A duck-typed `env` that doesn't support `strict`/`on_reject`.
        try:
            segments = env.paths("CMDS_PATH", ty=str)
        except Exception:  # pragma: no cover - env is best-effort here
            segments = []
    except Exception:  # pragma: no cover - env is best-effort here
        segments = []
    for bad_segment, reason in rejected:
        _LOGGER.warning(
            "CMDS_PATH entry %r rejected (%s); skipping", bad_segment, reason
        )
    discovered: "list[_Command]" = []
    for segment in segments:
        segment = segment.strip() if isinstance(segment, str) else str(segment)
        if not segment:
            # An empty/whitespace-only segment is never the current directory.
            continue
        path = _Path(segment).expanduser()
        if not path.is_dir():
            _LOGGER.warning("CMDS_PATH entry %r is not a directory; skipping", segment)
            continue
        try:
            discovered.extend(_discover_commands(path))
        except ImportError as exc:
            _log_exception(
                _LOGGER,
                "skipping CMDS_PATH entry %r: %s",
                segment,
                exc,
                level=_logging.WARNING,
            )
            continue
    return discovered


def _merge_discovered(
    base: "list[_Command]",
    discovered: "list[_Command]",
    overridden: "set[str] | None" = None,
) -> "list[_Command]":
    """Merge ``discovered`` on top of ``base``: discovered wins on a name clash.

    Keeps ``base``'s order for everything NOT overridden, then appends every
    discovered command. A name collision drops the ``base`` entry (the
    override story is intentional, but never silent).

    **Logging is deferred, not skipped.** Called from
    :func:`_resolve_commands` -- itself called before ``app()`` has set up any
    logging handler -- an immediate ``_LOGGER.info`` here is emitted into the
    void: Python's ``logging.lastResort`` handler only prints WARNING and
    above, so the override notice would be silently lost even under ``-vv``.
    When ``overridden`` is given, the overridden name is recorded into it
    instead of logged immediately, so the caller (``app()``) can log it once
    logging is actually configured. When ``overridden`` is omitted (a direct,
    non-``app()`` caller), the old immediate-INFO behavior is kept.
    """
    if not discovered:
        return base
    override = {_command_name(c) for c in discovered if _command_name(c)}
    merged = []
    for cmd in base:
        name = _command_name(cmd)
        if name and name in override:
            if overridden is not None:
                overridden.add(name)
            else:
                _LOGGER.info("CMDS_PATH command %r overrides the built-in", name)
            continue
        merged.append(cmd)
    merged.extend(discovered)
    return merged


def _resolve_commands(
    root: "type | None",
    commands: "_ty.Sequence[_Command] | None",
    source: "str | _Path | None",
    env: "_Env | None",
    entry_points: "str | None" = None,
    overridden: "set[str] | None" = None,
) -> "list[_Command]":
    """Resolve the command set for :func:`app` by precedence.

    Base-source order: an explicit ``commands`` list > ``discover_commands
    (source)`` > ``discover_entry_points(entry_points)`` > ``root._subcommands_``.
    ``env``-derived paths (``CMDS_PATH``) then ALWAYS merge on top of whichever
    base source produced the list -- a LAYER, not a branch reachable only when
    no other source was given. (Before this fix, passing an explicit
    ``commands=``/``source=``/``entry_points=`` silently disabled ``CMDS_PATH``
    entirely, even when ``env=`` was also passed -- the operator's exported
    variable did nothing, with no warning.)

    ``CMDS_PATH`` is additive: an app's base commands stay available and the
    discovered ones are added alongside. Setting it to drop the base commands
    would make every invocation depend on the variable being right, which is a
    footgun for a *supplementary* command directory -- the usual reason to point
    at one is "I have a few extra commands", not "replace this CLI". A discovered
    command whose name collides with a base command **wins** (that is the
    override story), and the shadowing is never silent (see ``overridden``).

    **Additive, not exclusive, w.r.t. a root's OWN declared subcommands.**
    This function only falls back to ``root._subcommands_`` as ITS
    OWN base when none of ``commands``/``source``/``entry_points`` is given.
    But ``app()`` separately, and always, registers ``root``'s own declared
    ``_subcommands_`` too (via ``root_cls._parser_()``, independent of this
    function) -- so passing ``commands=``/``source=``/``entry_points=``
    alongside a root that already declares ``_subcommands_`` does not remove
    or replace those; this function's result is layered on top of them, not
    instead of them. Pass an explicit, subcommand-free root (or ``root=None``)
    to get a command set with nothing but what this function resolves.

    ``overridden``, when given, receives the name of every base command a
    CMDS_PATH-discovered one replaced (see :func:`_merge_discovered`) --
    ``app()`` uses this to log the override once, after logging is set up,
    and to avoid a second, redundant collision warning when registering.

    Discovery is resilient (a bad command drops out with a warning -- see
    :func:`duho.discovery.discover_commands` /
    :func:`duho.discovery.discover_entry_points`).
    """
    if commands is not None:
        base = list(commands)
    elif source is not None:
        base = _discover_commands(source)
    elif entry_points is not None:
        base = _discover_entry_points(entry_points)
    else:
        base = (
            list(getattr(root, "_subcommands_", []) or []) if root is not None else []
        )

    return _merge_discovered(base, _cmds_path_commands(env), overridden=overridden)


def _full_names(command: object, cmd_name: str, kind: str) -> "list[str]":
    """Every name ``command`` claims in a subparsers action.

    A class command claims its primary ``cmd_name`` PLUS its own
    ``_parseraliases_`` (argparse's ``add_parser(..., aliases=...)`` registers
    each alias as an extra ``_name_parser_map`` key pointing at the same
    subparser object). A module command has no alias mechanism and claims
    only its primary name. Used to detect -- and, on an override, fully
    undo -- a collision against ANY of a command's names, not just its
    primary one: checking only ``cmd_name`` missed the case where an
    INCOMING command's alias collides with an already-registered name/alias,
    which argparse itself only reports at ``add_parser()`` time (raising on
    3.11+, silently overwriting on 3.9).
    """
    names = [cmd_name]
    if kind == "class":
        for alias in getattr(command, "_parseraliases_", None) or ():
            if alias not in names:
                names.append(alias)
    return names
