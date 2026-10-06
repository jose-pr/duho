import inspect as _inspect
import sys as _sys
import typing as _ty


#: Sentinel for "argument not supplied" where ``None`` is a meaningful value.
#: :func:`register`'s ``base`` uses ``None`` for "keep the current value", which
#: leaves no way to say "clear it"; ``step_adapter`` needs both, so it gets a
#: sentinel instead of repeating that limitation.
class _Keep:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<keep>"


_KEEP = _Keep()


def _adapt_step(
    entrypoint: "_ty.Callable[..., object]",
) -> "_ty.Callable[..., object]":
    """Apply the app's ``step_adapter`` to ``entrypoint``, if one is set.

    Kept deliberately dumb: no caching (an adapter is cheap and a step runs
    once per target), and a falsy return is ignored rather than treated as "no
    step", so an adapter that forgets to return cannot silently delete work.

    The adapted callable is what arity detection then inspects, so an adapter
    that changes the signature -- wrapping a ``(client, args, logger)`` body in
    a ``(cmd, ctx)`` shim, say -- is honored rather than second-guessed.
    """
    # The adapter is state on the package, where register() and callers set it.
    adapter = _sys.modules[__package__]._ADAPTER
    if adapter is None:
        return entrypoint
    return adapter(entrypoint) or entrypoint


def _is_bare_passthrough(params: "_ty.Sequence[_inspect.Parameter]") -> bool:
    """True if ``params`` is exactly a var-positional/var-keyword pass-through.

    i.e. the signature carries NO named parameter of its own -- ``(*args)``,
    ``(*args, **kwargs)``, or ``(**kwargs)`` -- the shape a generic decorator
    leaves when it doesn't declare its own explicit parameters, relying on
    ``@functools.wraps`` to publish the WRAPPED callable's real signature via
    ``__wrapped__`` instead. A decorator that DOES declare parameters of its
    own (e.g. duho's own ``step_adapter`` shims, ``def call(cmd, ctx=None)``)
    is never bare pass-through, so ITS signature is authoritative and must not
    be second-guessed by following ``__wrapped__`` underneath it.
    """
    if not params:
        return False
    kinds = {p.kind for p in params}
    return kinds <= {
        _inspect.Parameter.VAR_POSITIONAL,
        _inspect.Parameter.VAR_KEYWORD,
    }


def _step_wants_ctx(entrypoint: "_ty.Callable[..., object]") -> bool:
    """True if a step's entrypoint accepts a 2nd positional ``ctx`` argument.

    Inspects ``entrypoint`` itself first, with ``follow_wrapped=False``:
    ``inspect.signature`` follows ``__wrapped__`` by default, which reads the
    ORIGINAL wrapped callable's signature instead of an ``@functools.wraps``
    ``step_adapter`` shim's own, explicitly different one -- a documented
    contract ("the adapted callable is what arity detection inspects") that
    following ``__wrapped__`` silently broke: a ``(cmd, ctx=None)`` shim over
    a 1-arg step got called without ``ctx`` (silently ``None``), and a
    ``(cmd, ctx)`` shim (no default) raised a confusing ``TypeError`` naming
    the WRAPPED function, not the shim. Only when the shim's own signature is
    a BARE pass-through (see :func:`_is_bare_passthrough` -- a generic
    ``@decorator`` with no explicit parameters of its own) does this fall back
    to following ``__wrapped__``, so a plain, signature-preserving decorator on
    an ordinary 1-arg step still reads as 1-arg.

    A step written ``(cmd)`` (the historical, pre-lifecycle shape) keeps being
    called with just ``self``; a step written ``(cmd, ctx)`` (or with a
    ``*args`` catch-all) additionally receives the ``ctx`` -- but ONLY when a
    ``__main__.py`` was actually loaded (the caller gates on that separately;
    see the module docstring's "The optional __main__.py lifecycle"). If the
    signature cannot be introspected (a builtin/C callable), conservatively
    default to ``False`` (the historical 1-arg call), never over-supplying an
    argument the entrypoint can't take.
    """
    try:
        sig = _inspect.signature(entrypoint, follow_wrapped=False)
    except (TypeError, ValueError):  # pragma: no cover - builtins/C callables
        return False
    params = list(sig.parameters.values())
    if _is_bare_passthrough(params):
        try:
            sig = _inspect.signature(entrypoint)  # default: follows __wrapped__
        except (TypeError, ValueError):  # pragma: no cover
            return False
        params = list(sig.parameters.values())
    positional = 0
    for param in params:
        if param.kind is _inspect.Parameter.VAR_POSITIONAL:
            return True
        if param.kind in (
            _inspect.Parameter.POSITIONAL_ONLY,
            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 2
