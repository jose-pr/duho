"""A call-shape adapter for module commands and a public arity helper."""

import functools
import types

import pytest

import duho
from duho.discovery import ModuleCommand
from duho.runtime import accepts_positional, run_command


def _module(**attrs):
    mod = types.ModuleType("adapter_demo")
    mod.__file__ = "adapter_demo.py"
    for name, fn in attrs.items():
        fn.__module__ = "adapter_demo"
        setattr(mod, name, fn)
    return mod


def _command(**attrs):
    return ModuleCommand(_module(**attrs), name="demo")


def test_entrypoint_is_the_resolved_callable():
    def main(args):
        return 7

    command = _command(main=main)
    assert command.entrypoint is main
    with pytest.raises(AttributeError):
        command.entrypoint = main


def test_adapter_replaces_the_entrypoint_call():
    seen = []

    def main(args):
        return 1

    def adapter(entrypoint):
        seen.append(entrypoint)
        return lambda args: entrypoint(args) + 10

    command = _command(main=main)
    assert run_command(command, types.SimpleNamespace(), adapter=adapter) == 11
    assert seen == [main]


def test_a_falsy_adapter_result_is_ignored():
    command = _command(main=lambda args: 3)
    assert run_command(command, types.SimpleNamespace(), adapter=lambda e: None) == 3


def test_the_adapter_never_touches_lifecycle_hooks():
    calls = []

    def init(args):
        calls.append("init")

    def main(args):
        calls.append("main")

    def success(ctx, args):
        calls.append("success")

    def finally_(ctx, args):
        calls.append("finally_")

    command = _command(init=init, main=main, success=success, finally_=finally_)
    adapted = []
    run_command(
        command,
        types.SimpleNamespace(),
        adapter=lambda e: adapted.append(e) or (lambda args: calls.append("adapted")),
    )
    assert calls == ["init", "adapted", "success", "finally_"]
    assert adapted == [command.entrypoint]


def test_the_adapter_is_not_applied_to_a_class_command():
    class Hello(duho.Cmd):
        def __call__(self):
            return 9

    def refuse(entrypoint):
        raise AssertionError("adapter must not see a class command")

    assert run_command(Hello, Hello(), adapter=refuse) == 9


def test_without_an_adapter_nothing_changes():
    command = _command(main=lambda args: 4)
    assert run_command(command, types.SimpleNamespace()) == 4


def test_app_threads_the_adapter_through(tmp_path):
    (tmp_path / "hello.py").write_text("def main(args):\n    return 2\n")
    code = duho.app(
        source=tmp_path,
        argv=["hello"],
        setup_logging=False,
        adapter=lambda entrypoint: lambda args: entrypoint(args) + 40,
    )
    assert code == 42


def test_app_refuses_an_adapter_beside_a_custom_dispatch(tmp_path):
    (tmp_path / "hello.py").write_text("def main(args):\n    return 2\n")
    with pytest.raises(ValueError, match="adapter"):
        duho.app(
            source=tmp_path,
            argv=["hello"],
            setup_logging=False,
            adapter=lambda e: e,
            dispatch=lambda command, instance: 0,
        )


def test_accepts_positional_counts_positionals():
    assert accepts_positional(lambda a, b: None, 2)
    assert accepts_positional(lambda a, b: None, 1)
    assert not accepts_positional(lambda a, b: None, 3)
    assert accepts_positional(lambda a, b=1: None, 2)
    assert not accepts_positional(lambda a, *, b: None, 2)
    assert accepts_positional(lambda *a: None, 5)
    assert accepts_positional(lambda: None, 0)


def test_accepts_positional_reads_a_wrapper_by_its_own_signature():
    def inner(a, b, c):
        pass

    @functools.wraps(inner)
    def shim(a):
        pass

    assert not accepts_positional(shim, 3)
    assert accepts_positional(shim, 1)


def test_accepts_positional_is_false_when_the_signature_is_unreadable():
    class Opaque:
        @property
        def __signature__(self):
            raise ValueError("no signature")

        def __call__(self, *args):
            pass

    assert not accepts_positional(Opaque(), 1)


def test_accepts_positional_is_public():
    from duho import runtime

    assert "accepts_positional" in runtime.__all__
