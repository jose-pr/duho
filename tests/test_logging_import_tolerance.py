"""``import duho`` never fails because the process already defines TRACE."""

import subprocess
import sys

import pytest
from conftest import subprocess_env

import duho


def _run(setup: str) -> "subprocess.CompletedProcess[str]":
    code = (
        "import logging\n"
        + setup
        + "\nimport duho\nimport duho.logging as dl\n"
        + "print('ok', logging.getLevelName(logging.TRACE) if hasattr(logging, 'TRACE') else '-')\n"
    )
    env = subprocess_env(remove=("PYTHONIOENCODING",))
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "setup",
    [
        "logging.TRACE = 5",
        "logging.TRACE = 7",
        "logging.Logger.trace = lambda self, *a, **k: None",
        "logging.trace = lambda *a, **k: None",
    ],
)
def test_import_tolerates_an_existing_trace(setup):
    result = _run(setup)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_existing_trace_number_is_reused():
    result = _run("logging.TRACE = 7")
    assert result.returncode == 0, result.stderr
    assert "ok TRACE" in result.stdout


def test_explicit_call_still_raises_on_a_foreign_name():
    import logging

    with pytest.raises(ValueError):
        duho.logging.add_logging_level("BASIC_FORMAT", 3)
    assert logging.BASIC_FORMAT  # untouched
