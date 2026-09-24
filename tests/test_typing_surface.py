"""Regression test for duho's public typing surface (py.typed).

The package ships ``py.typed`` and the ``Typing :: Typed`` classifier, so its
documented patterns (``Arg[...]``, ``Meta(...)``, ``@Cli.subcommand``,
``duho.parse``/``duho.parser``/``duho.main``, ``app(commands=[...])``,
``CmdBuilder.command``) must type-check cleanly under a consumer's own mypy
run -- previously several of them either failed outright or silently widened
to ``Any``/``type[Cmd]`` (see the typing-surface review findings).

duho does NOT depend on mypy (``PYTHON.md``: type-checking is opt-in, never a
project dependency), so this test never installs it -- it SKIPS whenever
``mypy`` is not importable in the running interpreter, exactly like an
optional-extra test guards a missing SDK. Run it explicitly by installing
mypy into a venv (project or scratch) that already has duho installed
editable, e.g. ``pip install mypy`` then ``pytest -k typing_surface``.
"""

import subprocess
import sys
import textwrap
import typing

import pytest

import duho


def test_arg_is_the_real_typing_annotated():
    """``duho.Arg`` must be ``typing.Annotated`` itself (A041), not a plain
    variable assigned it -- mypy does not treat the latter as a type alias,
    so every documented ``Arg[int, Meta(...)]`` field failed type-checking.
    """
    assert duho.Arg is typing.Annotated


CONSUMER_SOURCE = textwrap.dedent("""
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


    reveal_type(Build)  # type[Build], not type[Cmd] (A042)

    parsed = duho.parse(App, [])
    reveal_type(parsed)  # App, not Any (A042)

    p = duho.parser(App)
    reveal_type(p)  # a _Parser[App], not Any (A042)

    code = duho.run_command(Deploy, parsed)
    reveal_type(code)  # int

    builder = duho.CmdBuilder("mypkg.mod", Path("."))
    reveal_type(builder.command)  # Command, not object (D029)

    exit_code = duho.app(root=App, commands=[Deploy, Build])
    reveal_type(exit_code)  # int -- app(commands=[a Cmd subclass, ...]) (D029)

    tools = dmcp.describe_tools(App)
    reveal_type(tools)  # list[dict[...]]
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
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Success: no issues found" in output, output
