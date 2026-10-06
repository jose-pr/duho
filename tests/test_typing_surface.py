"""Regression test for duho's public typing surface (py.typed).

The package ships ``py.typed`` and the ``Typing :: Typed`` classifier, so its
documented patterns (``Arg[...]``, ``Meta(...)``, ``@Cli.subcommand``,
``duho.parse``/``duho.parser``/``duho.main``, ``app(commands=[...])``,
``CmdBuilder.command``) must type-check cleanly under a consumer's own mypy
run -- previously several of them either failed outright or silently widened
to ``Any``/``type[Cmd]``.

mypy is a ``dev`` extra, not a runtime dependency, and the test skips when
``mypy`` is not importable in the running interpreter.
"""

import subprocess
import sys
import textwrap
import typing

import pytest
from conftest import subprocess_env

import duho


def test_arg_is_the_real_typing_annotated():
    """``duho.Arg`` must be ``typing.Annotated`` itself, not a plain
    variable assigned it -- mypy does not treat the latter as a type alias,
    so every documented ``Arg[int, Meta(...)]`` field failed type-checking.
    """
    assert duho.Arg is typing.Annotated


def _return_annotation(func):
    """Resolve just a function's ``return`` annotation.

    Not ``typing.get_type_hints(func)``: that resolves EVERY parameter's
    forward-ref string too, and ``duho.app`` has params annotated with
    names (``_Env``, ``_Path``, ``_Command``) that only exist under
    ``typing.TYPE_CHECKING`` -- resolving the full signature raises
    ``NameError`` at runtime for reasons unrelated to the return type.
    """
    raw = func.__annotations__["return"]
    if isinstance(raw, str):
        return eval(raw, vars(sys.modules[func.__module__]))
    return raw


def test_main_and_app_return_any_for_sys_exit_compat():
    """``duho.main``/``duho.app`` must be typed ``-> Any``, not ``-> object``
    or ``-> int``.

    A command's return value passes straight through unchanged when it is
    not ``None`` (see their docstrings), so ``int`` is inaccurate. ``object``
    was tried and broke a strict-mypy consumer doing the documented
    ``sys.exit(duho.main(App))`` -- ``sys.exit`` doesn't accept ``object``.
    ``Any`` is honest about the pass-through AND keeps ``sys.exit(...)`` clean.
    """
    assert _return_annotation(duho.main) is typing.Any
    assert _return_annotation(duho.app) is typing.Any


CONSUMER_SOURCE = textwrap.dedent("""
    import sys
    from pathlib import Path

    import duho
    import duho.mcp as dmcp
    from duho import Arg, Cli, Cmd, Extend, Meta


    class Deploy(Cmd):
        target: Arg[str, Meta(help="deploy target", env="TARGET")] = "prod"
        ("--target",)

        tags: Arg[list[str], Extend(",")] = []
        ("--tags",)

        times: Arg[int, Meta(flags=("-n", "--times"))] = 1

        def __call__(self) -> int:
            return 0


    class App(Cli):
        _version_ = "1.0.0"


    @App.subcommand
    class Build(Cmd):
        def __call__(self) -> int:
            return 0


    reveal_type(Build)  # type[Build], not type[Cmd]

    parsed = duho.parse(App, [])
    reveal_type(parsed)  # App, not Any

    p = duho.parser(App)
    reveal_type(p)  # a _Parser[App], not Any

    code = duho.run_command(Deploy, parsed)
    reveal_type(code)  # int

    builder = duho.CmdBuilder("mypkg.mod", Path("."))
    reveal_type(builder.command)  # Command, not object

    exit_code = duho.app(root=App, commands=[Deploy, Build])
    reveal_type(exit_code)  # Any -- a command's return passes through unchanged

    tools = dmcp.describe_tools(App)
    reveal_type(tools)  # list[dict[...]]

    # duho.main is typed `-> Any`, not `-> object`, so this must type-check
    # clean under strict mypy: `sys.exit` does not accept `object`.
    sys.exit(duho.main(App, []))
    """)


def test_documented_patterns_typecheck_clean(tmp_path):
    """mypy over the documented patterns above must report zero errors."""
    pytest.importorskip("mypy")
    consumer = tmp_path / "consumer.py"
    consumer.write_text(CONSUMER_SOURCE)
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental", str(consumer)],
        capture_output=True,
        text=True,
        # mypy ignores PYTHONPATH; MYPYPATH puts the tree under test first.
        env=subprocess_env(extra={"MYPYPATH": subprocess_env()["PYTHONPATH"]}),
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Success: no issues found" in output, output
