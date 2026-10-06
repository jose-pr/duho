import inspect as _inspect
import typing as _ty


def accepts_positional(func: "_ty.Callable[..., object]", count: int) -> bool:
    """True if ``func`` can be called with ``count`` positional arguments.

    That holds when it declares at least ``count`` positional parameters (with
    or without defaults) or a ``*args`` catch-all. A keyword-only parameter does
    not count. ``func`` is read on its own signature, not the one a
    ``functools.wraps`` wrapper points to. A callable whose signature cannot be
    read (a builtin, a C callable) gives ``False``, so a caller never supplies
    an argument it cannot take.
    """
    try:
        params = _inspect.signature(func, follow_wrapped=False).parameters
    except (TypeError, ValueError):
        return False
    positional = 0
    for param in params.values():
        if param.kind is _inspect.Parameter.VAR_POSITIONAL:
            return True
        if param.kind in (
            _inspect.Parameter.POSITIONAL_ONLY,
            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= count
