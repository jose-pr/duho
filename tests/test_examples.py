"""Smoke tests for examples/dotagents.py and examples/buildutils.py (Plan 09).

These exercise the example files as acceptance tests for duho's public API
surface: LoggingArgs, _subcommands_, __run__ dispatch via duho.main(), and
(for buildutils) positionals, Union types, NS(nargs="?"), a custom
action=UpdateAction, and NS(conflicts=...) mutually-exclusive grouping.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

import duho

import dotagents
import buildutils


def test_dotagents_install_parses_fields():
    result = duho.parse(dotagents.Install, ["--dest", "/tmp/x", "--dry-run"])
    assert result.dest == Path("/tmp/x")
    assert result.dry_run is True


def test_dotagents_main_install_dry_run_returns_0():
    assert duho.main(dotagents.Dotagents, ["install", "--dry-run"]) == 0


def test_buildutils_install_positionals_are_path():
    result = duho.parse(buildutils.Install, ["src", "dst"])
    assert isinstance(result.source, Path)
    assert isinstance(result.destination, Path)
    assert result.source == Path("src")
    assert result.destination == Path("dst")


def test_buildutils_install_type_flag():
    result = duho.parse(buildutils.Install, ["--type", "dir", "src", "dst"])
    assert result.type == "dir"


def test_buildutils_install_options_update_action():
    result = duho.parse(buildutils.Install, ["-O", "a=1", "-O", "b=2", "src", "dst"])
    assert result.options == {"a": "1", "b": "2"}


def test_buildutils_main_install_returns_0():
    assert duho.main(buildutils.Buildutils, ["install", "src", "dst"]) == 0
