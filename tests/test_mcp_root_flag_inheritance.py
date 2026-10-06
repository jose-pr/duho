"""A root's ``_mcp_ = False`` does not hide commands that subclass the root."""

from duho import Cli, Cmd
from duho.mcp import describe_tools


class Base(Cli):
    """Root with the environment trigger off."""

    _parsername_ = "base"
    _mcp_ = False

    def __call__(self):  # pragma: no cover
        return 0


class Child(Base):
    """Shares the root's fields by subclassing it."""

    _subcommands_ = []

    def __call__(self):  # pragma: no cover
        return 0


class Other(Cmd):
    """Unrelated command."""

    def __call__(self):  # pragma: no cover
        return 0


class Hidden(Child):
    """Opts out for itself."""

    _mcp_ = False

    def __call__(self):  # pragma: no cover
        return 0


Base._subcommands_ = [Child, Other, Hidden]


def test_command_subclassing_the_root_is_listed():
    names = sorted(t["name"] for t in describe_tools(Base))
    assert names == ["base.child", "base.other"]
