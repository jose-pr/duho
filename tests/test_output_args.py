"""The OutputArgs preset: ``--format text|json``, ``--json`` and ``_output_``."""

import json

import pytest

import duho
from duho import Cli, Cmd, LoggingArgs, OutputArgs
from duho.mcp import call_tool
from duho.testing import invoke

DATA = {"name": "café", "n": [1, 2]}
JSON = json.dumps(DATA, indent=2, ensure_ascii=False)


class Show(OutputArgs, Cmd):
    _parsername_ = "show"
    mode: str = "plain"
    ("--mode",)

    def __call__(self):
        text = {
            "plain": None,
            "str": "hello",
            "fn": lambda value: f"name={value['name']}",
        }[self.mode]
        self._output_(DATA, text=text)
        return 0


class Strs(OutputArgs, Cmd):
    _parsername_ = "strs"

    def __call__(self):
        self._output_("just text")
        return 0


class Odd(OutputArgs, Cmd):
    _parsername_ = "odd"

    def __call__(self):
        self._output_({"when": __import__("datetime").date(2024, 1, 2)})
        return 0


class Root(LoggingArgs, Cli):
    _parsername_ = "outroot"
    _subcommands_ = [Show, Strs, Odd]


def _run(*argv):
    return invoke(Root, list(argv))


@pytest.mark.parametrize(
    "argv", [["--json"], ["--format", "json"], ["--json", "--format", "text"]]
)
def test_json_layout(argv):
    res = _run("show", *argv)
    assert res.status == 0 and res.stdout == JSON + "\n"


def test_non_ascii_is_kept():
    assert "café" in _run("show", "--json").stdout


@pytest.mark.parametrize("argv", [[], ["--format", "text"]])
def test_text_default_renders_with_duho(argv):
    assert _run("show", *argv).stdout == JSON + "\n"


def test_text_string_and_callable():
    assert _run("show", "--mode", "str").stdout == "hello\n"
    assert _run("show", "--mode", "fn").stdout == "name=café\n"
    assert _run("show", "--mode", "str", "--json").stdout == JSON + "\n"


def test_string_value_is_printed_as_is():
    assert _run("strs").stdout.startswith("just text\n")


def test_default_str_for_non_json_values():
    assert '"when": "2024-01-02"' in _run("odd", "--json").stdout


def test_bad_format_is_a_usage_error():
    res = _run("show", "--format", "xml")
    assert res.status == 2 and "invalid choice" in res.stderr


def test_help_lists_both_flags():
    out = _run("show", "--help").stdout
    assert "--format {text,json}" in out and "--json" in out


def test_tool_call_passes_the_flag():
    result = call_tool(Root, "outroot.show", {"format": "json"})
    assert result["content"][0]["text"].strip() == JSON
    result = call_tool(Root, "outroot.show", {"json": True})
    assert result["content"][0]["text"].strip() == JSON
    result = call_tool(Root, "outroot.show", {})
    assert result["content"][0]["text"].strip() == JSON


def test_class_with_its_own_format_is_refused():
    with pytest.raises(TypeError, match="format"):

        class Own(OutputArgs, Cmd):
            format: str = "x"


def test_class_inheriting_format_is_refused():
    class Base(Cmd):
        json: bool = False

    with pytest.raises(TypeError, match="json"):

        class Child(Base, OutputArgs):
            pass


def test_a_subclass_of_an_output_class_is_fine():
    class Child(Show):
        pass

    assert duho.parse(Child, []).format == "text"


def test_exported():
    assert "OutputArgs" in duho.__all__
