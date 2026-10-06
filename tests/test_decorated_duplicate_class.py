"""Two definitions of one class name: a decorated class resolves to the
branch that actually ran (3.13+ records the class's first line)."""

import importlib.util
import sys
import textwrap

import pytest

import duho

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 13),
    reason="before 3.13 duplicate class names resolve to the last definition",
)

_SOURCE = textwrap.dedent("""
    import duho
    from duho import Cmd

    def keep(cls):
        return cls

    LIVE = True

    if LIVE:
        @keep
        class First(Cmd):
            target: str = "a"
            ("--right",)
            def __call__(self):
                return 0
    else:
        @keep
        class First(Cmd):
            target: str = "a"
            ("--wrong",)
            def __call__(self):
                return 0

    if LIVE:
        class Plain(Cmd):
            target: str = "a"
            ("--right",)
    else:
        class Plain(Cmd):
            target: str = "a"
            ("--wrong",)
    """)


@pytest.fixture
def module(tmp_path):
    path = tmp_path / "duho_t_dupdeco.py"
    path.write_text(_SOURCE)
    spec = importlib.util.spec_from_file_location("duho_t_dupdeco", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["duho_t_dupdeco"] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop("duho_t_dupdeco", None)


def _flags(cls):
    return sorted(
        s
        for a in duho.parser(cls)._actions
        for s in a.option_strings
        if s not in ("-h", "--help")
    )


def test_decorated_class_uses_the_live_branch(module):
    assert _flags(module.First) == ["--right"]


def test_undecorated_class_uses_the_live_branch(module):
    assert _flags(module.Plain) == ["--right"]
