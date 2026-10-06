import os as _os
import sys as _sys
import typing as _ty

from .. import _compat as _compat

from ._naming import _app_name

#: Characters a normalized MCP env-var-name segment may contain; anything
#: else (a ``-``, a ``.``, whitespace, ...) becomes ``_``. See
#: :func:`_mcp_env_var_name`.
_MCP_NAME_ALLOWED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _mcp_env_var_name(
    cls: "_ty.Optional[type]",
    *,
    env: object = None,
    name: "_ty.Optional[str]" = None,
) -> str:
    """The environment variable name the MCP launch trigger reads/consumes:
    ``<PREFIX>MCP`` -- the same key
    ``env.get("MCP")`` would read -- when ``env`` (a :class:`duho.Env`) is
    given; otherwise ``<NAME>_MCP`` derived from :func:`_app_name`,
    upper-cased with every character outside ``[A-Z0-9]`` replaced by ``_``
    (``my-app`` -> ``MY_APP_MCP``). Only the REAL process environment is ever
    consulted for the resulting key (an ``Env`` companion-module default for
    ``MCP`` is deliberately never read here) -- the trigger must be something
    a caller can reliably set and have taken effect.
    """
    if env is not None:
        return env.prefix + "MCP"
    base = _app_name(cls, name)
    normalized = "".join(ch if ch in _MCP_NAME_ALLOWED else "_" for ch in base.upper())
    return normalized + "_MCP"


def _maybe_serve_mcp_trigger(
    cls: "_ty.Optional[type]",
    *,
    env: object = None,
    name: "_ty.Optional[str]" = None,
    core_factory: "_ty.Optional[_ty.Callable[[], object]]" = None,
) -> "_ty.Optional[int]":
    """Check and consume the ``<PREFIX>MCP``/``<NAME>_MCP`` launch trigger;
    called first thing by both :func:`main` and :func:`duho.runtime.app`,
    before anything else runs.

    Returns ``None`` when the caller should proceed with its own normal CLI
    run: the trigger is disabled (``cls``'s own ``_mcp_`` class attribute,
    default ``True`` -- checked via ``getattr`` so ANY class works, not just
    a ``Cli``; the variable is then left ENTIRELY untouched),
    the variable is unset, or it is set but empty (an explicit "no
    preference" spelling). Otherwise the variable is REMOVED from
    ``os.environ`` immediately (so neither this process nor any child it
    spawns ever sees it again) and either an MCP server ran to completion --
    returning ITS exit code -- or the value named an unsupported transport,
    in which case a usage message is printed to stderr and ``2`` is
    returned. ``argv`` is never consulted in server mode: the whole point of
    server mode is to serve the CLI's own tool tree, not run one command
    from it.

    ``core_factory`` -- a zero-arg callable returning either a ``Cmd``/``Cli``
    class or a ``duho.mcp._ServerCore`` -- lets the caller supply an
    ``app()``-built tree (:func:`duho.mcp._core_for_app`) instead of the
    default :func:`duho.mcp._core_for_class(cls)`. ``duho.mcp`` is imported
    lazily, ONLY inside the branch that actually serves (the value was
    exactly ``"stdio"``) -- a normal run, including one where the variable
    is merely unset, never imports it.
    """
    if not getattr(cls, "_mcp_", True):
        return None
    env_name = _mcp_env_var_name(cls, env=env, name=name)
    raw = _os.environ.pop(env_name, None)
    if raw is None:
        return None
    stripped = raw.strip()
    value = stripped.lower()
    if not value:
        return None
    if value != "stdio":
        # `stripped` is env-supplied text (the `<PREFIX>MCP`/`<NAME>_MCP`
        # value itself) -- `write_human` instead of a raw `print(...,
        # file=sys.stderr)` so a non-ASCII value can't raise even on a
        # stderr this module cannot assume is UTF-8 (opted out of
        # `duho.utf8_stdio`, or this trigger reached outside `main`/`app`).
        _compat.write_human(
            "unsupported MCP transport %r (supported: stdio)\n" % (stripped,),
            _sys.stderr,
        )
        return 2

    from .. import mcp as _mcp

    core = core_factory() if core_factory is not None else _mcp._core_for_class(cls)
    return _mcp.serve(core)
