"""A test that spawns a child interpreter pins it to the tree under test.

A child ``sys.executable`` otherwise imports whichever ``duho`` its
environment resolves, so a test passes against a tree it does not exercise.
``conftest.subprocess_env`` is the one way to build the child's environment.
"""

from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_SPAWNERS = sorted(
    path
    for path in _TESTS.glob("test_*.py")
    if path != Path(__file__).resolve() and "sys.executable" in path.read_text("utf-8")
)


@pytest.mark.parametrize("path", _SPAWNERS, ids=lambda p: p.name)
def test_a_file_that_spawns_the_interpreter_builds_its_environment_with_subprocess_env(
    path,
):
    assert "subprocess_env" in path.read_text(
        "utf-8"
    ), f"{path.name} runs sys.executable without conftest.subprocess_env()"
