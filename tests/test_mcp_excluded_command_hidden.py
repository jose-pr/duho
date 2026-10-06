"""A command excluded with ``_mcp_ = False`` is never named in a tool result."""

import datetime
import typing as ty

import pytest

from duho import Cli, Cmd, LoggingArgs
from duho.mcp import call_tool


class Visible(Cmd):
    """A visible command."""

    name: str = "n"
    "a positional"
    ("name",)

    def __call__(self):
        return {"name": self.name}


class TopSecretPurge(Cmd):
    """Hidden from MCP."""

    _mcp_ = False

    def __call__(self):
        return {"ran": "TopSecretPurge"}


class Root(LoggingArgs, Cli):
    """Root."""

    _parsername_ = "root"

    since: ty.Optional[datetime.date] = None
    "a date"
    ("--since",)

    target: str = "."
    "optional positional"
    ("target",)

    _subcommands_ = [Visible, TopSecretPurge]


@pytest.mark.parametrize(
    "arguments",
    [
        {"since": "not-a-date"},
        {"loglevels": {"x": "BOGUS"}},
        {"name": "zzz"},
    ],
)
def test_parse_failure_text_does_not_name_the_excluded_command(arguments):
    result = call_tool(Root, "root.visible", arguments)
    text = result["content"][0]["text"]
    assert result["isError"] is True
    assert "top-secret-purge" not in text
    assert "usage:" not in text


def test_parse_failure_text_still_says_what_was_wrong():
    result = call_tool(Root, "root.visible", {"since": "not-a-date"})
    assert "invalid date value" in result["content"][0]["text"]


def test_positional_value_equal_to_an_excluded_name_is_not_singled_out():
    guessed = call_tool(Root, "root.visible", {"target": "top-secret-purge"})
    other = call_tool(Root, "root.visible", {"target": "no-such-command"})
    assert "isError" not in other
    assert "isError" not in guessed
    assert guessed["content"][0]["text"] == other["content"][0]["text"]
