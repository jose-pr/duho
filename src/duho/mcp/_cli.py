import pkgutil as _pkgutil
import sys as _sys
import typing as _ty

from .. import _compat as _compat
from ..args import AUTO as _AUTO
from ..args import Cli as _Cli
from ..args import Cmd as _Cmd
from ..args import main as _main

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


class _McpMain(_Cli):
    """Serve a duho CLI as an MCP server over stdio.

    <app> is the dotted name of the root Cmd/Cli class, as module:Class or
    module.Class. Usage: python -m duho.mcp <app>
    """

    _parsername_ = "duho.mcp"
    _version_ = _AUTO
    _distribution_ = "duho"
    _mcp_ = False

    app: str
    "Dotted name of the root Cmd/Cli class to serve (module:Class)."
    ("app",)  # type: ignore

    def __call__(self) -> int:
        # Stdio is taken over BEFORE the app is imported: resolution can
        # write to the original fd 1 directly (a module-level print, a C
        # extension, a thread started at import), and every such write then
        # lands on stderr for the rest of the process, never on the protocol
        # stream.
        stream_in, stream_out = _real_stdio_streams()
        try:
            root_cls = _resolve_app(self.app)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - report, don't traceback, a bad app spec
            # `self.app`/`exc` can carry arbitrary text; `write_human`
            # cannot raise on a stderr that is not UTF-8.
            _compat.write_human(
                "duho.mcp: could not resolve app %r: %s\n" % (self.app, exc),
                _sys.stderr,
            )
            stream_in.close()
            stream_out.close()
            return 1
        return serve(root_cls, stdin=stream_in, stdout=stream_out)


def main(argv: "_ty.Sequence[str] | None" = None) -> int:
    """``python -m duho.mcp <app>`` entry point: resolve ``<app>`` and run :func:`serve`.

    A duho CLI like any other: ``-h``/``--help`` and ``--version`` print to
    stdout and succeed, a missing ``<app>``, an unknown option or an extra
    argument is a usage error (exit ``2``, nothing on stdout). ``<app>`` is a
    dotted qualname to a ``Cmd``/``Cli`` subclass (see :func:`_resolve_app`);
    one that does not resolve prints a one-line error to stderr and returns
    ``1``. Only then are the real stdio fds taken over for the protocol
    channel (see :func:`_real_stdio_streams`), before ``<app>`` is imported;
    the exit code is that of the stdio loop.
    """
    return _main(_McpMain, argv)
