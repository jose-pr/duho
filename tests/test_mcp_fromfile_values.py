"""A tool argument is never read as an argparse response file."""

import pytest

from duho import Cli, Cmd
from duho.mcp import InvalidArgumentsError, call_tool


class Show(Cmd):
    """Show a name."""

    name: str = "n"
    "a positional"
    ("name",)

    k: str = ""
    "short only"
    ("-k",)

    long: str = ""
    "long option"
    ("--long",)

    danger: bool = False
    "must never be switched on by a value"
    ("--danger",)

    def __call__(self):
        return {"name": self.name, "k": self.k, "danger": self.danger}


class Root(Cli):
    """Root with response files enabled."""

    _parsername_ = "root"
    _subcommands_ = [Show]

    @classmethod
    def _parser_(cls, *a, **kw):
        if not a and "subparser" not in kw:
            kw.setdefault("fromfile_prefix_chars", "@")
        return super()._parser_(*a, **kw)


@pytest.fixture
def secret(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_text("TOP-SECRET-LINE-1\n--danger\n")
    return path


def test_positional_value_with_a_response_file_prefix_is_refused(secret):
    with pytest.raises(InvalidArgumentsError):
        call_tool(Root, "root.show", {"name": "@%s" % secret})


def test_short_flag_value_with_a_response_file_prefix_is_refused(secret):
    with pytest.raises(InvalidArgumentsError):
        call_tool(Root, "root.show", {"k": "@%s" % secret})


def test_passthrough_item_with_a_response_file_prefix_is_refused(secret):
    with pytest.raises(InvalidArgumentsError):
        call_tool(Root, "root.show", {"--": ["@%s" % secret]})


def test_long_flag_value_with_the_prefix_stays_literal():
    result = call_tool(Root, "root.show", {"long": "@plain"})
    assert "isError" not in result
