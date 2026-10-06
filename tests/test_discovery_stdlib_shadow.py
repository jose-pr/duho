"""A command file named after an installed module never replaces it for its siblings."""

import sys

from duho.discovery import discover_commands


def test_command_named_like_stdlib_module_does_not_shadow_it(tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "colorsys", raising=False)
    (tmp_path / "colorsys.py").write_text("def main(args):\n    return 0\n")
    (tmp_path / "export.py").write_text(
        "import colorsys\n"
        "HSV = colorsys.rgb_to_hsv(1.0, 0.0, 0.0)\n"
        "def main(args):\n    return 0\n"
    )
    commands = discover_commands(tmp_path)
    assert len(commands) == 2
    hsv = [getattr(c.module, "HSV", None) for c in commands]
    assert (0.0, 1.0, 1.0) in hsv


def test_directory_already_on_sys_path_keeps_its_entry(tmp_path, monkeypatch):
    (tmp_path / "hello.py").write_text("def main(args):\n    return 0\n")
    monkeypatch.setattr(sys, "path", [str(tmp_path)] + list(sys.path))
    before = list(sys.path)
    discover_commands(tmp_path)
    assert sys.path == before
