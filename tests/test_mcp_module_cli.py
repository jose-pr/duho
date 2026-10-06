"""``python -m duho.mcp`` follows the command-line conventions of a duho CLI."""

import subprocess
import sys

from conftest import subprocess_env


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "duho.mcp", *args],
        capture_output=True,
        text=True,
        env=subprocess_env(),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def test_help_goes_to_stdout_and_succeeds():
    proc = _run("--help")
    assert proc.returncode == 0
    assert "usage:" in proc.stdout
    assert proc.stderr == ""


def test_version_is_reported():
    proc = _run("--version")
    assert proc.returncode == 0
    assert proc.stdout.strip()
    assert "could not resolve" not in proc.stderr


def test_no_arguments_is_a_usage_error_with_nothing_on_stdout():
    proc = _run()
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "usage:" in proc.stderr


def test_unknown_flag_is_a_usage_error():
    proc = _run("-x")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "could not resolve" not in proc.stderr


def test_extra_arguments_are_a_usage_error():
    proc = _run("os:getcwd", "extra", "junk")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "unrecognized arguments" in proc.stderr
