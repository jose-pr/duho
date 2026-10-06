"""A launcher scanning its own directory is not one of its own commands."""

import sys

from duho.args._naming import _command_name
from duho.discovery import discover_commands

_TOOL = """\
raise RuntimeError("the launcher must not be imported as a command")
"""

_HELLO = "def main(args):\n    return 0\n"


def test_running_script_is_skipped_by_directory_discovery(tmp_path, monkeypatch):
    tool = tmp_path / "tool.py"
    tool.write_text(_TOOL)
    (tmp_path / "hello.py").write_text(_HELLO)
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(tool), raising=False)
    commands = discover_commands(tmp_path)
    assert [_command_name(c) for c in commands] == ["hello"]


def test_other_files_are_unaffected_when_main_is_elsewhere(tmp_path, monkeypatch):
    (tmp_path / "hello.py").write_text(_HELLO)
    (tmp_path / "tool.py").write_text(_HELLO)
    monkeypatch.setattr(
        sys.modules["__main__"], "__file__", str(tmp_path / "other.py"), raising=False
    )
    commands = discover_commands(tmp_path)
    assert [_command_name(c) for c in commands] == ["hello", "tool"]
