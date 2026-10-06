import pkgutil as _pkgutil
import sys as _sys
import typing as _ty

from .. import _compat as _compat
from ..args import Cmd as _Cmd

from ._stdio import _real_stdio_streams, serve

# --------------------------------------------------------------------------
# Step 4: stdio JSON-RPC server
# --------------------------------------------------------------------------


def _resolve_app(spec: str) -> "type[_Cmd]":
    """Resolve the ``<app>`` CLI argument (a dotted qualname) to a root ``Cmd``/``Cli`` class.

    Uses the stdlib ``pkgutil.resolve_name`` (3.9+): it accepts BOTH the
    ``module.sub:ClassName`` colon syntax (the same convention this project's
    own entry-point tests/``discover_entry_points`` use) and the legacy
    dotted ``module.sub.ClassName`` form (progressively importing shorter
    prefixes as a module, the remainder as attribute access). This -- not
    ``discovery.CmdBuilder`` -- resolves the app, because ``CmdBuilder``
    always yields a ``Command`` (wrapping any module source in a
    ``ModuleCommand``), never the raw class :func:`describe_tools`/
    :func:`call_tool` need. Only a class's own STATIC ``_subcommands_`` tree
    is exposed over MCP in v1 -- ``<app>`` must be a ``Cmd``/``Cli`` subclass;
    a module command, or a command only reachable via ``duho.app``'s dynamic
    resolution, is out of scope.
    """
    obj = _pkgutil.resolve_name(spec)
    if not (isinstance(obj, type) and issubclass(obj, _Cmd)):
        raise TypeError(
            "%r does not resolve to a duho Cmd/Cli class (got %r)" % (spec, obj)
        )
    return obj


def main(argv: "_ty.Sequence[str] | None" = None) -> int:
    """``python -m duho.mcp <app>`` entry point: resolve ``<app>`` and run :func:`serve`.

    ``<app>`` is a dotted qualname to a ``Cmd``/``Cli`` subclass (see
    :func:`_resolve_app`). No arguments prints a usage line to stderr and
    returns ``2``; ``-h``/``--help`` prints the same usage line and returns
    ``0`` (previously treated as an ``<app>`` spec and reported as
    unresolvable) -- neither of these touches stdio at all. Otherwise, takes
    over the real stdio fds for the protocol channel via
    :func:`_real_stdio_streams` **before** resolving ``<app>`` (importing
    it), and never restores them in between: resolution can write to the
    ORIGINAL fd 1 directly -- a module-level ``print``, ``os.write(1, ...)``,
    a C extension, a background thread started at import time that keeps
    writing after import returns -- and every one of those writes now lands
    on the real fd 2 (stderr) for the rest of the process's life, because
    ``_real_stdio_streams`` already repointed fd 1 there before resolution
    ever ran. An earlier version resolved ``<app>`` through a SEPARATE,
    temporary fd-1-to-fd-2 redirect that RESTORED fd 1 to the original pipe
    immediately after import finished, then only isolated stdio once
    :func:`serve` started -- a window between those two steps during which a
    thread STILL RUNNING from import (daemon or otherwise) could write
    straight into the client-facing pipe ahead of the first protocol
    response. Prints a one-line error to stderr and returns a non-zero exit
    code if ``<app>`` does not resolve (stdio has already been taken over by
    then, but the process exits right after, so nothing depends on restoring
    it); otherwise runs the stdio loop against the already-captured protocol
    streams and returns its exit code.
    """
    args = list(argv) if argv is not None else _sys.argv[1:]
    if not args:
        _compat.write_human("usage: python -m duho.mcp <app>\n", _sys.stderr)
        return 2
    if args[0] in ("-h", "--help"):
        _compat.write_human("usage: python -m duho.mcp <app>\n", _sys.stderr)
        return 0
    stream_in, stream_out = _real_stdio_streams()
    try:
        root_cls = _resolve_app(args[0])
    except Exception as exc:  # noqa: BLE001 - report, don't traceback, a bad app spec
        # `args[0]`/`exc` can both carry arbitrary (env- or user-supplied)
        # text -- `write_human`, not a raw `print(..., file=sys.stderr)`,
        # so a non-ASCII app spec or exception message can't raise even on
        # a stderr this module cannot assume is UTF-8.
        _compat.write_human(
            "duho.mcp: could not resolve app %r: %s\n" % (args[0], exc), _sys.stderr
        )
        return 1
    return serve(root_cls, stdin=stream_in, stdout=stream_out)
