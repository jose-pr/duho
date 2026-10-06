from __future__ import annotations

import os as _os
import sys as _sys
import typing as _ty

from .. import _compat as _compat

from ._naming import _app_name

#: Characters an MCP env-var-name segment may contain; others become ``_``.
_MCP_NAME_ALLOWED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def _mcp_env_var_name(
    cls: _ty.Optional[type],
    *,
    env: object = None,
    name: _ty.Optional[str] = None,
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
    cls: _ty.Optional[type],
    *,
    env: object = None,
    name: _ty.Optional[str] = None,
    core_factory: _ty.Optional[_ty.Callable[[], object]] = None,
) -> _ty.Optional[int]:
    """Check and consume the ``<PREFIX>MCP``/``<NAME>_MCP`` launch trigger.

    Called first by :func:`main` and :func:`duho.runtime.app`. Returns ``None``
    when the caller should run its normal CLI: the class sets ``_mcp_ = False``
    (the variable is then left untouched), or the variable is unset or empty.
    Otherwise the variable is removed from ``os.environ`` (so no child sees it)
    and the result is the MCP server's exit code, or ``2`` after a stderr usage
    message for a transport other than ``stdio``. ``argv`` is never consulted.

    ``core_factory`` supplies an ``app()``-built tree instead of
    ``duho.mcp._core_for_class(cls)``. ``duho.mcp`` is imported only when
    serving.
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
        # `write_human`, not print(): a non-ASCII env value must not raise on a
        # stderr that may not be UTF-8.
        _compat.write_human(
            "unsupported MCP transport %r (supported: stdio)\n" % (stripped,),
            _sys.stderr,
        )
        return 2

    from .. import mcp as _mcp

    # Stdio is taken over BEFORE the tree is built: anything printed while
    # commands are discovered or registered then goes to stderr, not the
    # protocol stream.
    stream_in, stream_out = _mcp._real_stdio_streams()
    core = core_factory() if core_factory is not None else _mcp._core_for_class(cls)
    return _mcp.serve(core, stdin=stream_in, stdout=stream_out)
