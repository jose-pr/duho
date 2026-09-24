"""The packaged version and ``duho.__version__`` must agree.

``pyproject.toml`` and ``src/duho/__init__.py`` each state the version literally
(a deliberate choice over hatchling's ``[tool.hatch.version] path = ...``, so that
``pyproject.toml`` keeps saying the version out loud). Nothing but this test keeps
the two in step: releases 0.5.2 and 0.5.3 bumped ``pyproject.toml`` and the
changelog only, so an installed ``duho==0.5.3`` reported ``__version__ == "0.5.1"``
and no test noticed -- ``tests/test_e2e.py`` consumes the dunder as a substring
source, which passes just as happily when it is wrong.

The version line is read with a regex rather than a TOML parser: the floor is 3.9,
where ``tomllib`` does not exist and ``tomli`` is only an optional extra.
"""

import re
from pathlib import Path

import pytest

import duho

_PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"

#: ``version = "X.Y.Z"`` at the start of a line -- anchored with MULTILINE so a
#: ``version`` key nested in some other table cannot match by accident.
_VERSION_RE = re.compile(r'^version = "(?P<v>[^"]+)"', re.MULTILINE)


def _pyproject_version() -> str:
    """Return the ``[project] version`` string, or skip if there is no checkout."""
    if not _PYPROJECT.is_file():
        pytest.skip("no pyproject.toml (running against an installed package)")
    match = _VERSION_RE.search(_PYPROJECT.read_text(encoding="utf-8"))
    assert match is not None, f'no `version = "..."` line in {_PYPROJECT}'
    return match.group("v")


def test_dunder_version_matches_pyproject():
    assert duho.__version__ == _pyproject_version(), (
        "duho.__version__ and pyproject.toml disagree -- bump BOTH when releasing "
        "(src/duho/__init__.py and pyproject.toml)"
    )


def test_dunder_version_is_a_release_number():
    """Guard the regex itself: a match that is not version-shaped means it drifted."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", _pyproject_version())
