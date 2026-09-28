"""Regression tests: `_maybe_await` must drive ANY awaitable to
completion (not just a native coroutine object), reject an async-generator
result outright instead of silently returning it as the exit code, and fail
loud -- with a fix pointed at -- instead of a bare `asyncio` internals error
when called from inside an already-running event loop.
"""

import asyncio

import pytest

from duho.args import _maybe_await


def test_native_coroutine_is_still_driven_to_completion():
    async def f():
        return 42

    assert _maybe_await(f()) == 42


def test_non_coroutine_result_passes_through_unchanged():
    assert _maybe_await(7) == 7
    assert _maybe_await(None) is None


class _CustomAwaitable:
    """An object implementing `__await__` without being a native coroutine
    (stands in for a Cython-/mypyc-compiled `async def` result, which
    registers under `collections.abc.Coroutine` without being a
    `types.CoroutineType`)."""

    def __await__(self):
        async def _inner():
            return "custom-result"

        return _inner().__await__()


def test_a_non_native_awaitable_is_also_driven_to_completion():
    assert _maybe_await(_CustomAwaitable()) == "custom-result"


def test_async_generator_result_raises_instead_of_being_returned_unchanged():
    async def gen():
        yield 1

    result = gen()
    with pytest.raises(TypeError, match="async generator"):
        _maybe_await(result)


def test_nesting_inside_a_running_loop_raises_a_clear_error_naming_the_fix():
    async def f():
        return 1

    async def host():
        coro = f()
        with pytest.raises(RuntimeError, match="asyncio.to_thread"):
            _maybe_await(coro)

    asyncio.run(host())


def test_nesting_inside_a_running_loop_closes_the_awaitable():
    """The un-awaited awaitable must be closed (no leaked ResourceWarning /
    "coroutine was never awaited" noise) when we refuse to run it. Uses a
    plain object (a native coroutine's `close` is read-only, so it can't be
    spied on directly) that records whether `close()` was called."""
    closed = []

    class _ClosableAwaitable:
        def __await__(self):
            async def _inner():
                return 1

            return _inner().__await__()

        def close(self):
            closed.append(True)

    async def host():
        with pytest.raises(RuntimeError):
            _maybe_await(_ClosableAwaitable())

    asyncio.run(host())
    assert closed == [True]


def test_sync_call_from_inside_a_running_loop_is_unaffected():
    async def host():
        return _maybe_await(5)

    assert asyncio.run(host()) == 5
