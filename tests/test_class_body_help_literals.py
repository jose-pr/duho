"""Class-body help text: the first run of consecutive string literals after a
field is its help (joined with a space), wherever it stands relative to the
flag tuple; a lone flag-shaped string is a missing comma and fails at build."""

import importlib
import sys

import pytest

import duho
from duho import Args


def _help_of(cls, dest):
    for action in duho.parser(cls)._actions:
        if action.dest == dest:
            return action.help
    raise AssertionError(dest)


def test_consecutive_literals_are_joined_with_a_space():
    class Joined(Args):
        level: int = 0
        "First sentence of the help."
        "Second sentence, written as its own literal."

    assert (
        _help_of(Joined, "level")
        == "First sentence of the help. Second sentence, written as its own literal."
    )


def test_help_after_the_flag_tuple_is_the_help():
    class TupleFirst(Args):
        name: str = "x"
        ("-n", "--name")
        "the name"

    parser = duho.parser(TupleFirst)
    action = next(a for a in parser._actions if a.dest == "name")
    assert action.help == "the name"
    assert action.option_strings == ["-n", "--name"]


def test_help_before_the_flag_tuple_still_works():
    class DocFirst(Args):
        name: str = "x"
        "the name"
        ("-n", "--name")

    assert _help_of(DocFirst, "name") == "the name"


def test_a_later_separate_run_is_not_part_of_the_help():
    class TwoRuns(Args):
        name: str = "x"
        "the name"
        ("-n", "--name")
        "ignored"

    assert _help_of(TwoRuns, "name") == "the name"


@pytest.mark.parametrize("flag", ["--dry-run", "-d", "--"])
def test_flag_shaped_lone_literal_names_the_field_and_the_comma(
    tmp_path, monkeypatch, flag
):
    path = tmp_path / "duho_t_comma_module.py"
    path.write_text(
        "from duho import Args\n"
        "class Lone(Args):\n"
        "    dry_run: bool = False\n"
        f"    ({flag!r})\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("duho_t_comma_module")
    try:
        with pytest.raises(ValueError, match=r"dry_run.*trailing comma"):
            duho.parser(module.Lone)
    finally:
        sys.modules.pop("duho_t_comma_module", None)


def test_ordinary_one_word_help_is_not_mistaken_for_a_flag():
    class Plain(Args):
        n: int = 1
        "count"

    assert _help_of(Plain, "n") == "count"
