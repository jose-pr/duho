from __future__ import annotations

import inspect as _inspect
import sys as _sys
import typing as _ty


#: Sentinel for "argument not supplied" where ``None`` is a meaningful value
#: (``step_adapter=None`` clears the adapter).
class _Keep:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<keep>"


_KEEP = _Keep()


def _adapt_step(
    entrypoint: _ty.Callable[..., object],
) -> _ty.Callable[..., object]:
    """Apply the app's ``step_adapter`` to ``entrypoint``, if one is set.

    A falsy return is ignored, so an adapter that forgets to return cannot
    delete a step. Arity detection inspects the adapted callable.
    """
    # The adapter is state on the package, where register() and callers set it.
    adapter = _sys.modules[__package__]._ADAPTER
    if adapter is None:
        return entrypoint
    return adapter(entrypoint) or entrypoint


def _is_bare_passthrough(params: _ty.Sequence[_inspect.Parameter]) -> bool:
    """True if ``params`` has only ``*args``/``**kwargs`` and no named parameter.

    That is the shape of a generic decorator relying on ``functools.wraps``;
    a shim that declares its own parameters is never bare pass-through.
    """
    if not params:
        return False
    kinds = {p.kind for p in params}
    return kinds <= {
        _inspect.Parameter.VAR_POSITIONAL,
        _inspect.Parameter.VAR_KEYWORD,
    }


def _step_wants_ctx(entrypoint: _ty.Callable[..., object]) -> bool:
    """True if a step's entrypoint accepts a 2nd positional ``ctx`` argument.

    A ``*args`` catch-all counts. An un-introspectable callable returns False.
    The signature is read with ``follow_wrapped=False`` so a ``step_adapter``
    shim's own signature wins over the callable it wraps; only a bare
    pass-through (see :func:`_is_bare_passthrough`) is followed to ``__wrapped__``.
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
