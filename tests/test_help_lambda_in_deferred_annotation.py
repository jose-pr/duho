"""A ``help=`` callable in a field annotation reads its own module's globals
when the module defers annotations (``from __future__ import annotations``).

The class lives in a real file: the failure is in how annotation strings are
evaluated, and the callable only runs when help is rendered.
"""

import importlib.util
import sys
import textwrap
import uuid

import pytest

from duho.testing import invoke


def _load(tmp_path, source, name=None):
    name = name or "deferred_" + uuid.uuid4().hex
    path = tmp_path / (name + ".py")
    path.write_text(
        "from __future__ import annotations\n" + textwrap.dedent(source),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clean_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith("deferred_"):
            del sys.modules[name]


def test_meta_help_lambda_reads_a_module_global(tmp_path):
    m = _load(
        tmp_path,
        """
        import duho
        from duho import Arg, Meta

        WORDS = "gamma|delta"

        class App(duho.Cmd):
            mode: Arg[str, Meta(help=lambda: "one of " + WORDS)] = "gamma"
        """,
    )
    result = invoke(m.App, ["--help"])
    assert result.status == 0
    assert "one of gamma|delta" in result.stdout


def test_inherited_field_reads_the_global_of_its_own_module(tmp_path):
    _load(
        tmp_path,
        """
        import duho
        from duho import Arg, Meta

        WORDS = "from-base"

        class Base(duho.Cmd):
            first: Arg[str, Meta(help=lambda: "base " + WORDS)] = "a"
        """,
        name="deferred_base_mod",
    )
    child = _load(
        tmp_path,
        """
        from duho import Arg, Meta
        from deferred_base_mod import Base

        WORDS = "from-child"

        class Child(Base):
            second: Arg[str, Meta(help=lambda: "child " + WORDS)] = "b"
        """,
    )
    result = invoke(child.Child, ["--help"])
    assert result.status == 0
    assert "base from-base" in result.stdout
    assert "child from-child" in result.stdout


def test_a_field_named_like_a_module_global_resolves_to_the_module_object(tmp_path):
    m = _load(
        tmp_path,
        """
        import duho
        from pathlib import Path

        class App(duho.Cmd):
            Path: Path = Path(".")

            def __call__(self):
                print("resolved", type(self.Path).__module__)
        """,
    )
    assert invoke(m.App, ["--help"]).status == 0
    result = invoke(m.App, ["--path", "x"])
    assert result.status == 0
    assert "resolved pathlib" in result.stdout


def test_a_nested_class_named_in_a_string_annotation_resolves(tmp_path):
    m = _load(
        tmp_path,
        """
        import enum
        import duho
        from duho import Arg, Meta

        WORDS = "color"

        class App(duho.Cmd):
            class Color(enum.Enum):
                red = "red"
                blue = "blue"

            color: Arg[Color, Meta(help=lambda: "pick a " + WORDS)] = Color.red
        """,
    )
    result = invoke(m.App, ["--help"])
    assert result.status == 0
    assert "pick a color" in result.stdout
    assert "blue" in result.stdout
