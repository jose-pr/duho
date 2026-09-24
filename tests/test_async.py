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


class RecordingAsync(Cmd):
    """An async command that records which event loop it ran on."""

    def __call__(self):
        return self._run()

    async def _run(self):
        import asyncio

        RUN_LOOP_IDS[self.target] = id(asyncio.get_running_loop())
        return 3


#: Populated by RecordingAsync._run, keyed by the target name it was dispatched
#: with -- lets a test assert distinct calls actually ran on distinct loops.
RUN_LOOP_IDS: "dict[str, int]" = {}


def test_async_call_returns_exit_code():
    assert duho.main(AsyncReturn, []) == 3


def test_async_call_none_maps_to_zero():
    assert duho.main(AsyncNone, []) == 0


def test_async_call_exception_propagates():
    with pytest.raises(RuntimeError, match="boom"):
        duho.main(AsyncRaises, [])


def test_app_dispatches_an_async_class_command():
    """duho.app's dispatch path also drives an async __call__ to completion."""

    class Root(Cmd):
        """A root with one async subcommand."""

        _subcommands_ = [AsyncReturn]

        def __call__(self):  # pragma: no cover - root is not dispatched here
            return 0

    assert duho.app(Root, argv=["AsyncReturn"], setup_logging=False) == 3


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

    RUN_LOOP_IDS.clear()

    def make_call(target):
        inst = RecordingAsync()
        inst.target = target
        return duho.run_command(type(inst), inst)

    rc = run_targets(make_call, ["a", "b"])
    # Both targets return 3; the aggregate exit code is exactly that value,
    # not merely "some non-zero code".
    assert rc == 3
    # Each target actually ran its own coroutine, on its own event loop.
    assert set(RUN_LOOP_IDS) == {"a", "b"}
    assert RUN_LOOP_IDS["a"] != RUN_LOOP_IDS["b"]
