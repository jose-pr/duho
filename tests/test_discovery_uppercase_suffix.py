"""An upper-case ``.PY`` file is ignored the same way on every platform."""

import logging

import pytest

from duho.args._naming import _command_name
from duho.discovery import discover_commands, import_from_path

_BODY = "def main(args):\n    return 0\n"


def test_uppercase_suffix_is_ignored_without_a_warning(tmp_path, caplog):
    (tmp_path / "hello.py").write_text(_BODY)
    (tmp_path / "SHOUT.PY").write_text(_BODY)
    with caplog.at_level(logging.WARNING, logger="duho.discovery"):
        commands = discover_commands(tmp_path)
    assert [_command_name(c) for c in commands] == ["hello"]
    assert not caplog.records


def test_unloadable_file_error_says_why(tmp_path):
    path = tmp_path / "SHOUT.PY"
    path.write_text(_BODY)
    with pytest.raises(ImportError) as info:
        import_from_path("duho._test_upper.shout", path)
    assert str(info.value)
