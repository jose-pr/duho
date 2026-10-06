"""A misspelled sandwich attribute is reported once, as a WARNING."""

import logging
import re
from pathlib import Path

import duho
from duho.args._guards import _KNOWN_ATTRS


def _warnings(caplog):
    return [r for r in caplog.records if r.name == "duho.args"]


def test_misspelled_version_is_reported_once(caplog):
    class Typo(duho.Cmd):
        _verison_ = "1.2"

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        duho.parser(Typo)
        duho.parser(Typo)
    records = _warnings(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    text = records[0].getMessage()
    assert "Typo" in text and "_verison_" in text and "_version_" in text


def test_misspelled_aliases_is_reported(caplog):
    class Root(duho.Cli):
        pass

    class Child(duho.Cmd):
        _parser_aliases_ = ["c"]

        def __call__(self):
            return 0

    Root._subcommands_ = [Child]
    with caplog.at_level(logging.WARNING, logger="duho.args"):
        duho.parser(Root)
    text = " ".join(r.getMessage() for r in _warnings(caplog))
    assert "_parser_aliases_" in text and "_parseraliases_" in text


def test_known_and_unrelated_names_are_silent(caplog):
    class Fine(duho.Cli):
        _version_ = "1"
        _parsername_ = "fine"
        _my_private_helper_ = 3
        _x_ = 1

        def __call__(self):
            return 0

    with caplog.at_level(logging.WARNING, logger="duho.args"):
        duho.parser(Fine)
    assert _warnings(caplog) == []


def test_header_attributes_are_known():
    header = Path(duho.__file__).parent / "AGENTS.md"
    text = header.read_text(encoding="utf-8")
    names = set(re.findall(r"`(_[a-z][a-z_]*_)`", text))
    names = {n for n in names if not n.startswith("_duho_")}
    assert names - set(_KNOWN_ATTRS) == set()
