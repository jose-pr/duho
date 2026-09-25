"""Regression tests: nothing under a duho app's current working directory is
ever imported/executed as a side effect of a misconfigured ``PATHSEP``, an
empty discovery source, or a bare-drive-letter segment.

Every scenario plants a canary ``evil.py`` in a throwaway CWD that WRITES A
MARKER FILE the instant it is imported -- at module level, not inside a hook
-- so "the marker never appears" is the one assertion that actually proves
``evil.py`` was never even discovered, regardless of which mechanism might
otherwise have let it through. ``duho.discover_commands``/``app`` are always
exercised as real entry points against real ``.py`` files under ``tmp_path``
(never ``python -c``), matching the rest of the suite's convention.
"""

import os
from pathlib import Path

import pytest

import duho
from duho.discovery import discover_commands
from duho.env import Env
from duho.runtime import app

_EVIL = '''\
"""A canary command: importing this file at all is the compromise."""
import pathlib

pathlib.Path(__file__).resolve().parent.parent.joinpath("MARKER").write_text(
    "pwned"
)


def main(args=None):
    return "evil"
'''

_GOOD = '''\
"""A legitimate command."""


def main(args=None):
    return "good"
'''


class Root(duho.LoggingArgs, duho.Cmd):
    """A root command supplying global options (verbosity)."""

    def __call__(self):  # pragma: no cover - root is not dispatched here
        return 0


@pytest.fixture
def evil_cwd(tmp_path, monkeypatch):
    """A throwaway CWD holding a canary ``evil.py``, plus a REAL, SEPARATE
    ``cmds/`` directory holding one legitimate command -- so a positive case
    (CMDS_PATH pointed at the real directory) can be told apart from the
    negative ones (the marker must still never appear for those)."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "evil.py").write_text(_EVIL)
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "good.py").write_text(_GOOD)
    monkeypatch.chdir(cwd)
    return tmp_path, cwd, cmds


def _marker(root: Path) -> Path:
    return root / "MARKER"


# --------------------------------------------------------------------------
# An empty discovery source must never mean "the CWD"
# --------------------------------------------------------------------------


def test_discover_commands_empty_string_never_imports_cwd(evil_cwd):
    tmp_path, cwd, cmds = evil_cwd
    with pytest.raises(ValueError, match="empty"):
        discover_commands("")
    assert not _marker(tmp_path).exists()


def test_app_source_empty_string_never_imports_cwd(evil_cwd):
    tmp_path, cwd, cmds = evil_cwd
    with pytest.raises(ValueError, match="empty"):
        app(Root, source="", argv=["evil"], setup_logging=False)
    assert not _marker(tmp_path).exists()


def test_discover_commands_empty_path_behaves_like_explicit_dot(evil_cwd):
    """``pathlib.Path("")`` normalises to ``Path(".")`` at CONSTRUCTION time
    (before ``discover_commands`` ever sees it -- ``Path("") == Path(".")``
    on every supported Python version), so the two are indistinguishable by
    the time they reach here. Both are therefore the same DELIBERATE,
    documented "scan the current directory" request an already-built
    ``Path`` represents -- unlike the bare string ``""`` above, which is
    rejected because it is far more likely to be a blank/uninitialised value
    than an intentional choice. This test asserts the achievable contract:
    ``Path("")`` and ``Path(".")`` resolve to THE SAME commands, not that
    ``Path("")`` is somehow rejected while ``Path(".")`` is not -- there is no
    way to tell them apart once constructed.
    """
    tmp_path, cwd, cmds = evil_cwd
    from_empty = discover_commands(Path(""))
    from_dot = discover_commands(Path("."))
    assert [c._parsername_ for c in from_empty] == [c._parsername_ for c in from_dot]
    # Both legitimately scan THIS cwd (which does contain evil.py) -- that is
    # the documented, explicit-"." behavior, not a bug; the marker DOES
    # appear here (importing evil.py IS the point of an explicit scan), and
    # that is by design, not by accident.
    assert "evil" in [c._parsername_ for c in from_dot]


# --------------------------------------------------------------------------
# PATHSEP is scoped to this app's own prefix, and dangerous segments
# (a bare drive letter, or anything else that resolves to the CWD) are
# rejected outright -- covers a backslash, a forward-slash, and a
# multi-character separator, whichever way the separator is spelled.
# --------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="drive-letter segments are Windows-only")
def test_pathsep_backslash_bare_drive_segment_never_imports_cwd(evil_cwd, monkeypatch):
    """End-to-end (through ``app()``) coverage of the entry-level validation
    for an app that legitimately sets its OWN ``MYAPP_PATHSEP`` -- a
    different case from ``test_bare_unprefixed_pathsep_no_longer_bypasses_
    the_fix`` below, which reproduces the actual historical exploit (a bare,
    global ``PATHSEP`` with no app prefix at all)."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "\\")
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmds))
    env = Env("myapp", autoload=False)
    # The malformed separator makes the whole CMDS_PATH layer best-effort
    # unusable (see duho.runtime._cmds_path_commands) rather than crashing
    # the app -- either way, evil.py in the CWD must never run.
    try:
        app(Root, env=env, argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


def test_pathsep_backslash_rejected_directly(evil_cwd, monkeypatch):
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "\\")
    # A native Windows `str(cmds)` already starts with a drive letter, so the
    # ORIGINAL reproduction just split it as-is. On POSIX there is no drive
    # letter to split on at all (`str(cmds)` has no backslash in it, so
    # PATHSEP="\\" would not mis-split it into anything). The bare-drive
    # check itself (`_BARE_DRIVE_RE`) is a plain string match with no
    # platform dependency, so prepending a synthetic "C:" segment ourselves
    # reproduces the identical mis-split on every OS instead of relying on
    # a real drive letter that only exists on one of them.
    monkeypatch.setenv("MYAPP_CMDS_PATH", "C:\\" + str(cmds))
    env = Env("myapp", autoload=False)
    with pytest.raises(ValueError, match="bare drive segment"):
        env.paths("CMDS_PATH")
    assert not _marker(tmp_path).exists()


def test_pathsep_forward_slash_adversarial_split_never_imports_cwd(
    evil_cwd, monkeypatch
):
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "/")
    # Same idea as the backslash case above, with "/" as the separator: a
    # forward-slash-style absolute Windows path splits on its OWN drive
    # letter, but POSIX has no drive letter (and "/" is already its native
    # separator, so a real POSIX path splits into plain directory-name
    # fragments, none of which independently reproduces the attack).
    # Prepending a synthetic "C:" segment reproduces the same mis-split
    # (and the same rejection) on every OS.
    monkeypatch.setenv("MYAPP_CMDS_PATH", "C:/" + str(cmds).replace("\\", "/"))
    env = Env("myapp", autoload=False)
    with pytest.raises(ValueError):
        env.paths("CMDS_PATH")
    try:
        app(Root, env=Env("myapp", autoload=False), argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


def test_pathsep_multichar_segment_resolving_to_cwd_never_imports_cwd(
    evil_cwd, monkeypatch
):
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "::")
    # The CWD itself (a legitimate-looking absolute path) lands as one of
    # the split segments -- must be rejected the same as a bare drive is,
    # since resolving to the CWD is exactly what both attacks achieve.
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cwd) + "::" + str(cmds))
    env = Env("myapp", autoload=False)
    with pytest.raises(ValueError, match="current working directory"):
        env.paths("CMDS_PATH")
    try:
        app(Root, env=Env("myapp", autoload=False), argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


def test_bare_unprefixed_pathsep_no_longer_bypasses_the_fix(evil_cwd, monkeypatch):
    """A security fix: a bare, GLOBAL, unprefixed ``PATHSEP`` (set for some
    wholly unrelated program on the same machine) must not affect this app's
    separator at all -- only this app's OWN ``MYAPP_PATHSEP`` does (see the
    tests above). Before the fix, this alone was enough to reach the CWD."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("PATHSEP", "\\")
    monkeypatch.delenv("MYAPP_PATHSEP", raising=False)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmds))
    env = Env("myapp", autoload=False)
    # The real os.pathsep (";" on Windows) is used instead -- the bare
    # global PATHSEP is simply ignored, so the real command directory
    # resolves normally, as ONE unsplit entry.
    assert env.paths("CMDS_PATH") == [str(cmds)]
    rc = app(Root, env=Env("myapp", autoload=False), argv=["good"], setup_logging=False)
    assert rc == "good"
    assert not _marker(tmp_path).exists()


# --------------------------------------------------------------------------
# Positive cases: CMDS_PATH still works for a real directory, and for an
# explicit "." segment -- the fix must not overreach.
# --------------------------------------------------------------------------


def test_cmds_path_real_directory_still_works(evil_cwd, monkeypatch):
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.delenv("MYAPP_PATHSEP", raising=False)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cmds))
    env = Env("myapp", autoload=False)
    assert env.paths("CMDS_PATH") == [str(cmds)]
    rc = app(Root, env=env, argv=["good"], setup_logging=False)
    assert rc == "good"
    assert not _marker(tmp_path).exists()


def test_cmds_path_explicit_dot_segment_still_works(evil_cwd, monkeypatch):
    """An explicit ``"."`` CMDS_PATH segment deliberately means the CWD --
    still honoured. Here the CWD legitimately contains only ``evil.py``
    (renamed conceptually to "the deliberately-scanned file"), which is fine:
    the point of this test is that "." is not treated as an unresolvable/
    rejected segment, not that its target is inherently unsafe."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.delenv("MYAPP_PATHSEP", raising=False)
    monkeypatch.setenv("MYAPP_CMDS_PATH", ".")
    env = Env("myapp", autoload=False)
    assert env.paths("CMDS_PATH") == ["."]
    rc = app(Root, env=env, argv=["evil"], setup_logging=False)
    assert rc == "evil"
    assert _marker(tmp_path).exists()


def test_discover_commands_dot_string_still_works(evil_cwd):
    tmp_path, cwd, cmds = evil_cwd
    names = [c._parsername_ for c in discover_commands(".")]
    assert "evil" in names
