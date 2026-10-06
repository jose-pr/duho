from __future__ import annotations

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

from ..env import Env as _Env

_LOGGER = _logging.getLogger(__package__)


def _cmds_path_commands(
    env: _Env | None,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
) -> list[_Command]:
    """Resolve every command discoverable from ``env``'s ``CMDS_PATH``.

    Best effort: ``[]`` when ``env`` is ``None``, the variable is unset or empty
    (never split or globbed, which once meant "import every ``.py`` in the CWD"),
    or ``env`` lacks the interface. Splits on ``os.pathsep`` (see
    :meth:`duho.env.Env.paths`). Blank segments are dropped here as well, since
    ``env`` is duck-typed, and must never become ``Path('.')``. A segment that is
    not an existing directory is skipped with a WARNING, as is a command file
    raising ``ImportError``.

    ``strict=False`` lets :meth:`Env.paths` skip a rejected segment instead of
    raising, which the broad ``except`` below would turn into dropping every
    entry; rejections are logged at WARNING. An ``env`` that rejects those
    keywords is retried with the plain call.
    """
    if env is None:
        return []
    try:
        raw = env.get("CMDS_PATH")
    except Exception:  # pragma: no cover - env is best-effort here
        raw = None
    if not raw:
        return []
    rejected: list[tuple[str, str]] = []
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
    discovered: list[_Command] = []
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
            discovered.extend(_discover_commands(path, on_error=on_error))
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
    base: list[_Command],
    discovered: list[_Command],
    overridden: set[str] | None = None,
) -> list[_Command]:
    """Merge ``discovered`` on top of ``base``: discovered wins on a name clash.

    ``base`` keeps its order, minus overridden entries; discovered commands are
    appended. When ``overridden`` is given the name is recorded there instead of
    logged, because no handler exists yet when ``app()`` calls this and the
    INFO record would be lost; without it the notice is logged at once.
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
    root: type | None,
    commands: _ty.Sequence[_Command] | None,
    source: _ty.Union[str, _Path, _ty.Sequence[_ty.Union[str, _Path]], None],
    env: _Env | None,
    entry_points: str | None = None,
    overridden: set[str] | None = None,
    on_error: _ty.Optional[_ty.Callable[[object, BaseException], object]] = None,
) -> list[_Command]:
    """Resolve the command set for :func:`app` by precedence.

    Base source: explicit ``commands`` > ``discover_commands(source)`` >
    ``discover_entry_points(entry_points)`` > ``root._subcommands_``. ``CMDS_PATH``
    is always merged on top; a discovered command wins a name clash. ``app()``
    registers ``root``'s own ``_subcommands_`` regardless, so these are additive.

    ``overridden``, when given, receives each replaced base name (see
    :func:`_merge_discovered`) so ``app()`` can log it once.
    """
    if commands is not None:
        base = list(commands)
    elif source is not None:
        base = _discover_commands(source, on_error=on_error)
    elif entry_points is not None:
        base = _discover_entry_points(entry_points)
    else:
        base = (
            list(getattr(root, "_subcommands_", []) or []) if root is not None else []
        )

    return _merge_discovered(
        base, _cmds_path_commands(env, on_error), overridden=overridden
    )


def _full_names(command: object, cmd_name: str, kind: str) -> list[str]:
    """Every name ``command`` claims in a subparsers action.

    A class command claims ``cmd_name`` plus its ``_parseraliases_``; a module
    command only its own name. Checking every name catches an incoming alias
    clash, which argparse reports only at ``add_parser()`` (raising on 3.11+,
    overwriting on 3.9).
    """
    names = [cmd_name]
    if kind == "class":
        for alias in getattr(command, "_parseraliases_", None) or ():
            if alias not in names:
                names.append(alias)
    return names
