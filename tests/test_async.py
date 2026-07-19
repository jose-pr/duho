"""Tests for async ``__call__`` support (F4).

A ``Cmd`` whose ``__call__`` is ``async def`` returns a coroutine; ``duho.main``
and ``duho.run_command`` drive it to completion with ``asyncio.run`` at the call
site. Module-command lifecycle hooks stay synchronous by design.

All classes are declared at module level so AST-derived flag tuples resolve.
"""

import pytest

import duho
from duho import Cmd


class AsyncReturn(Cmd):
    """An async command returning an int exit code."""

    def __call__(self):
        return self._run()

    async def _run(self):
        return 3


class AsyncNone(Cmd):
    """An async command returning None."""

    async def __call__(self):
        return None


class AsyncRaises(Cmd):
    """An async command raising."""

    async def __call__(self):
        raise RuntimeError("boom")


def test_async_call_returns_exit_code():
    assert duho.main(AsyncReturn, []) == 3


def test_async_call_none_maps_to_zero():
    assert duho.main(AsyncNone, []) == 0


def test_async_call_exception_propagates():
    with pytest.raises(RuntimeError, match="boom"):
        duho.main(AsyncRaises, [])


def test_async_run_command_drives_coroutine():
    """run_command awaits a class command's coroutine result (fanout path)."""
    inst = AsyncNone()
    assert duho.run_command(type(inst), inst) == 0

    inst2 = AsyncReturn()
    assert duho.run_command(type(inst2), inst2) == 3


def test_async_fanout_gives_each_call_its_own_run():
    """A coroutine-returning command dispatched per target via run_command gets
    its own asyncio.run per call (no shared loop)."""
    from duho.fanout import run_targets

    def make_call(target):
        inst = AsyncReturn()
        return duho.run_command(type(inst), inst)

    rc = run_targets(make_call, ["a", "b"])
    # Each returns 3; aggregate is non-zero (last/any non-zero).
    assert rc != 0
