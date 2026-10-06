"""A property test leaves no example database in the directory pytest started in."""

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import subprocess_env

pytest.importorskip("hypothesis")

_ROOT = Path(__file__).resolve().parent.parent


def test_running_a_property_test_from_another_directory_creates_no_hypothesis_dir(
    tmp_path,
):
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(_ROOT / "pyproject.toml"),
            "--rootdir",
            str(_ROOT),
            str(_ROOT / "tests" / "test_property.py::test_field_round_trip"),
        ],
        cwd=tmp_path,
        env=subprocess_env(),
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert list(tmp_path.iterdir()) == []
