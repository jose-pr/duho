"""``discover_commands(..., sort=False)`` keeps discovery order."""

import pytest

from duho.discovery import discover_commands
from duho.parsers import command_name

CLASSES = """\
import duho


class Zed(duho.Cmd):
    def __call__(self):
        return 0


class Alpha(duho.Cmd):
    def __call__(self):
        return 0
"""


def _write(directory, name, body="def main(args):\n    return 1\n"):
    directory.mkdir(exist_ok=True)
    (directory / (name + ".py")).write_text(body)


def _names(commands):
    return [command_name(c) for c in commands]


def test_sources_keep_the_order_given(tmp_path):
    _write(tmp_path / "builtin", "zeta")
    _write(tmp_path / "extra", "alpha")
    sources = [tmp_path / "builtin", tmp_path / "extra"]
    assert _names(discover_commands(sources, sort=False)) == ["zeta", "alpha"]
    assert _names(discover_commands(sources)) == ["alpha", "zeta"]


def test_files_in_a_directory_come_by_name(tmp_path):
    for name in ("mid", "zeta", "alpha"):
        _write(tmp_path, name)
    assert _names(discover_commands(tmp_path, sort=False)) == ["alpha", "mid", "zeta"]


def test_a_file_gives_its_classes_in_definition_order(tmp_path):
    _write(tmp_path, "both", CLASSES + "\n\ndef main(args):\n    return 1\n")
    assert _names(discover_commands(tmp_path, sort=False)) == ["zed", "alpha", "both"]
    assert _names(discover_commands(tmp_path)) == ["alpha", "both", "zed"]


def test_a_shadowing_command_takes_the_earlier_ones_place(tmp_path):
    _write(tmp_path / "a", "mid", "def main(args):\n    return 1\n")
    _write(tmp_path / "a", "zeta")
    _write(tmp_path / "b", "alpha")
    _write(tmp_path / "b", "mid", "def main(args):\n    return 2\n")
    commands = discover_commands([tmp_path / "a", tmp_path / "b"], sort=False)
    assert _names(commands) == ["mid", "zeta", "alpha"]
    assert commands[0].main(object()) == 2


def test_a_package_comes_by_module_name(tmp_path, monkeypatch):
    package = tmp_path / "order_pkg_zz"
    _write(package, "__init__", "")
    _write(package, "second")
    _write(package, "first")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert _names(discover_commands("order_pkg_zz", sort=False)) == ["first", "second"]


def test_an_empty_sequence_and_the_guards_are_unchanged(tmp_path):
    assert discover_commands([], sort=False) == []
    with pytest.raises(ValueError):
        discover_commands([tmp_path, ""], sort=False)


def test_the_default_is_sorted(tmp_path):
    for name in ("mid", "zeta", "alpha"):
        _write(tmp_path, name)
    assert _names(discover_commands(tmp_path)) == ["alpha", "mid", "zeta"]
    assert _names(discover_commands(tmp_path, sort=True)) == ["alpha", "mid", "zeta"]
