import typing as _ty
from pathlib import Path as _Path

from ._command import _LOGGER

# --------------------------------------------------------------------------
# External provider injection hook
# --------------------------------------------------------------------------

#: Registry of (predicate, builder) pairs consulted by ``CmdBuilder`` for a
#: filesystem source before falling back to a normal import. A predicate takes
#: the resolved ``Path`` and returns True if its builder should handle it; the
#: builder takes ``(path, qualname)`` and returns a ``Command`` (or object
#: fulfilling it). Registered newest-first so a later registration can override
#: an earlier one for the same shape.
_PROVIDERS: (
    "list[tuple[_ty.Callable[[_Path], bool], _ty.Callable[[_Path, str], object]]]"
) = []


def register_command_provider(
    predicate: "_ty.Callable[[_Path], bool]",
    builder: "_ty.Callable[[_Path, str], object]",
) -> None:
    """Register an external provider that builds a ``Command`` from a directory.

    This is the extension seam that keeps directory-shaped command runtimes
    (e.g. an ordered "run-path" of numbered step files) OUT of core duho: an
    external package registers ``(predicate, builder)``; when ``CmdBuilder``
    resolves a filesystem source, it consults registered providers *before*
    importing the path normally, and the first matching provider's ``builder``
    produces the command. If no provider matches, the path is imported as a
    plain module/package.

    * ``predicate(path: Path) -> bool`` -- True if this provider handles ``path``.
    * ``builder(path: Path, qualname: str) -> Command`` -- build the command.

    Providers are consulted most-recently-registered first, so a later
    registration can take precedence over an earlier one for the same shape.
    """
    _PROVIDERS.insert(0, (predicate, builder))


def unregister_command_provider(
    predicate: "_ty.Callable[[_Path], bool]",
    builder: "_ty.Callable[[_Path, str], object]",
) -> None:
    """Remove a provider previously registered with
    :func:`register_command_provider` -- the exact ``(predicate, builder)``
    pair (matched the same way ``list.remove`` would).

    A no-op if that exact pair is not currently registered, so a caller does
    not need to track whether it already unregistered. Before this,
    the provider seam had no supported way to opt back out: a consumer
    needing one (test isolation, a plugin reloading itself) had no choice but
    to reach into ``_PROVIDERS`` directly.
    """
    try:
        _PROVIDERS.remove((predicate, builder))
    except ValueError:
        pass


def _match_provider(path: "_Path") -> "_ty.Callable[[_Path, str], object] | None":
    """Return the builder of the first provider whose predicate matches ``path``."""
    for predicate, builder in _PROVIDERS:
        try:
            if predicate(path):
                return builder
        except Exception:  # pragma: no cover - a broken predicate must not abort
            _LOGGER.debug(
                "command provider predicate raised for %s", path, exc_info=True
            )
    return None
