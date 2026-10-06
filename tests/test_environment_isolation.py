"""A test does not see the variables of the shell that runs the suite."""

import subprocess
import sys
from pathlib import Path

from conftest import subprocess_env

_ROOT = Path(__file__).resolve().parent.parent


def test_ambient_width_encoding_and_mcp_launch_variables_are_cleared():
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/env_probe_cases.py",
        ],
        cwd=_ROOT,
        env=subprocess_env(
            extra={
                "COLUMNS": "40",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8:surrogateescape",
                "PYTEST_MCP": "stdio",
            }
        ),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
