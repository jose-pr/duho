"""``run_command(..., context=)`` overrides the ``init`` result for a module command."""

import types

from duho.discovery import ModuleCommand
from duho.runtime import run_command


def _module(calls):
    module = types.ModuleType("_ctx_probe")

    def init(args):
        calls.append(("init",))
        return "from-init"

    def main(args):
        calls.append(("main",))
        return 0

    def success(ctx, args):
        calls.append(("success", ctx))

    def finally_(ctx, args):
        calls.append(("finally_", ctx))

    for hook in (init, main, success, finally_):
        hook.__module__ = module.__name__
        setattr(module, hook.__name__, hook)
    return module


def test_passed_context_reaches_success_and_finally_and_skips_init():
    calls: list = []
    command = ModuleCommand(_module(calls), name="probe")
    assert run_command(command, object(), context="passed-context") == 0
    assert calls == [
        ("main",),
        ("success", "passed-context"),
        ("finally_", "passed-context"),
    ]


def test_without_context_the_init_result_is_used():
    calls: list = []
    command = ModuleCommand(_module(calls), name="probe")
    assert run_command(command, object()) == 0
    assert calls == [
        ("init",),
        ("main",),
        ("success", "from-init"),
        ("finally_", "from-init"),
    ]
