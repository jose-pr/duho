"""Tests: nothing under a duho app's current working directory is
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

# Same canary as `_EVIL`, but writing its marker relative to `os.getcwd()`
# rather than `__file__`'s own location -- used below where the canary is
# planted a directory level DEEPER than the CWD itself (e.g. `cwd/C/evil.py`,
# the shape a mis-split ``PATHSEP`` produces), so `__file__.parent.parent`
# would not land back on the CWD the way it does for the shallower `_EVIL`
# scenarios above.
_EVIL_CWD_RELATIVE = '''\
"""A canary command: importing this file at all is the compromise."""
import os
import pathlib

pathlib.Path(os.getcwd(), "MARKER").write_text("pwned")


def main(args=None):
    return "evil"
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
    the_fix`` below, which covers a bare,
    global ``PATHSEP`` with no app prefix at all."""
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


def test_pathsep_backslash_rejected_as_invalid_separator(evil_cwd, monkeypatch, caplog):
    """``PATHSEP`` is validated BEFORE it is ever used to split anything:
    ``\\`` is one of the three values (with ``/`` and ``.``) rejected
    outright, closing the backslash-splits-a-Windows-path-on-its-own-
    drive-letter attack at its root, rather than downstream once it has
    already produced a bare ``"C:"`` segment (see
    ``test_pathsep_backslash_bare_drive_segment_never_imports_cwd`` above,
    and ``test_bare_drive_segment_via_default_separator_rejected`` below for
    the segment-shaped rejection that applies regardless). The rejected
    ``\\`` itself is never trusted as the separator either way -- what the
    REAL fallback (``os.pathsep``) then does with this literal value is
    platform-dependent (on POSIX, ``os.pathsep`` is ``":"``, which this
    Windows-shaped literal also happens to contain, so it may legitimately
    re-split and hit the segment-shaped rejection tested separately below);
    either outcome is safe, so only the WARNING and the end-to-end
    never-imports-the-CWD invariant are asserted here."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "\\")
    monkeypatch.setenv("MYAPP_CMDS_PATH", "C:\\" + str(cmds))
    env = Env("myapp", autoload=False)
    with caplog.at_level("WARNING", logger="duho.env"):
        try:
            env.paths("CMDS_PATH")
        except ValueError:
            pass
    assert any("PATHSEP" in rec.message for rec in caplog.records)
    try:
        app(Root, env=Env("myapp", autoload=False), argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


def test_pathsep_forward_slash_rejected_as_invalid_separator(
    evil_cwd, monkeypatch, caplog
):
    """Same fix, for ``/`` -- the other slash direction gets identical
    treatment, so the identical adversarial mis-split never gets the
    chance to happen regardless of which slash a caller picks."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "/")
    monkeypatch.setenv("MYAPP_CMDS_PATH", "C:/" + str(cmds).replace("\\", "/"))
    env = Env("myapp", autoload=False)
    with caplog.at_level("WARNING", logger="duho.env"):
        try:
            env.paths("CMDS_PATH")
        except ValueError:
            pass
    assert any("PATHSEP" in rec.message for rec in caplog.records)
    try:
        app(Root, env=Env("myapp", autoload=False), argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


def test_pathsep_multichar_rejected_as_invalid_separator(evil_cwd, monkeypatch, caplog):
    """A multi-character separator (``"::"``) is invalid for the same
    reason as ``/``/``\\``/``.``: it lets a value "separate on nothing at
    all" in a caller-chosen, attacker-shaped way. Rejected before ever
    splitting on the LITERAL ``"::"``; see
    ``test_cwd_segment_via_default_separator_rejected`` below for the
    segment-shaped rejection that survives this fix, using a
    single-character (therefore valid) separator."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.setenv("MYAPP_PATHSEP", "::")
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cwd) + "::" + str(cmds))
    env = Env("myapp", autoload=False)
    with caplog.at_level("WARNING", logger="duho.env"):
        try:
            env.paths("CMDS_PATH")
        except ValueError:
            pass
    assert any("PATHSEP" in rec.message for rec in caplog.records)
    try:
        app(Root, env=Env("myapp", autoload=False), argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


@pytest.mark.skipif(os.name != "nt", reason="drive-letter segments are Windows-only")
def test_bare_drive_segment_via_default_separator_rejected(evil_cwd, monkeypatch):
    """The bare-drive-segment rejection itself is still very much alive --
    reachable now via a literal ``"C:"`` entry under the ordinary, VALID
    default separator (the shape a caller reaches for directly, e.g. a
    ``CMDS_PATH`` typo, rather than via a malformed ``PATHSEP``)."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.delenv("MYAPP_PATHSEP", raising=False)
    monkeypatch.setenv("MYAPP_CMDS_PATH", "C:" + os.pathsep + str(cmds))
    env = Env("myapp", autoload=False)
    with pytest.raises(ValueError, match="bare drive segment"):
        env.paths("CMDS_PATH")
    assert not _marker(tmp_path).exists()


def test_cwd_segment_via_default_separator_rejected(evil_cwd, monkeypatch):
    """Likewise for a segment that resolves to the CWD itself, under the
    ordinary, valid default separator."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.delenv("MYAPP_PATHSEP", raising=False)
    monkeypatch.setenv("MYAPP_CMDS_PATH", str(cwd) + os.pathsep + str(cmds))
    env = Env("myapp", autoload=False)
    with pytest.raises(ValueError, match="current working directory"):
        env.paths("CMDS_PATH")
    try:
        app(Root, env=Env("myapp", autoload=False), argv=["evil"], setup_logging=False)
    except SystemExit:
        pass
    assert not _marker(tmp_path).exists()


@pytest.mark.skipif(os.name != "nt", reason="drive-letter segments are Windows-only")
def test_one_bad_cmds_path_entry_does_not_drop_the_good_one(evil_cwd, monkeypatch):
    """Through ``app()`` (which resolves ``CMDS_PATH`` with ``strict=False``,
    see ``duho.runtime._cmds_path_commands``): a bare-drive entry mixed with
    a real, valid one must not drop the valid one -- the point is
    that ONE bad entry is skipped and logged, not that the whole
    ``CMDS_PATH`` layer goes best-effort-unusable."""
    tmp_path, cwd, cmds = evil_cwd
    monkeypatch.delenv("MYAPP_PATHSEP", raising=False)
    monkeypatch.setenv("MYAPP_CMDS_PATH", "C:" + os.pathsep + str(cmds))
    env = Env("myapp", autoload=False)
    rc = app(Root, env=env, argv=["good"], setup_logging=False)
    assert rc == "good"
    assert not _marker(tmp_path).exists()


def test_bare_unprefixed_pathsep_no_longer_bypasses_the_fix(evil_cwd, monkeypatch):
    """A bare, GLOBAL, unprefixed ``PATHSEP`` (set for some
    wholly unrelated program on the same machine) must not affect this app's
    separator at all -- only this app's OWN ``MYAPP_PATHSEP`` does (see the
    tests above)."""
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
# explicit "." segment -- the validation must not overreach.
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


# --------------------------------------------------------------------------
# A custom PATHSEP that collides with a character INSIDE the real absolute
# path splits it into pieces that are individually neither a bare drive
# segment nor the CWD itself -- yet still resolve relative to the CWD. Each
# scenario plants its canary in the exact SUBDIRECTORY that mis-split
# produces (Windows: the bare drive LETTER left after the drive's own colon
# is split away; POSIX: whatever the separator character splits off the
# real directory name), rather than directly in the CWD.
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name != "nt", reason="a drive-letter colon split is Windows-only"
)
def test_pathsep_colon_splits_drive_letter_never_imports_cwd(tmp_path, monkeypatch):
    """``XA_PATHSEP=":"`` splits an absolute ``CMDS_PATH`` entry such as
    ``C:\\...\\cmds`` on its own drive-letter colon into ``"C"`` (relative --
    NOT ``"C:"``, so the bare-drive-segment rejection above does not match
    it) and ``"\\...\\cmds"`` (drive-relative, not absolute either). The
    first piece would resolve against the CWD as ``<cwd>\\C`` and be
    scanned for commands; a canary planted at exactly that path must never
    be imported."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    drive_letter_dir = cwd / (str(cwd.drive)[0] if cwd.drive else "C")
    drive_letter_dir.mkdir()
    (drive_letter_dir / "evil.py").write_text(_EVIL_CWD_RELATIVE)
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "good.py").write_text(_GOOD)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("XA_PATHSEP", ":")
    monkeypatch.setenv("XA_CMDS_PATH", str(cmds))
    env = Env("xa", autoload=False)
    # Splitting a Windows absolute path on ":" produces TWO bogus pieces --
    # the bare drive letter, AND the drive-relative remainder (a leading
    # backslash with no drive is not absolute either) -- so both are
    # rejected and "good" is not reachable through this misconfigured
    # PATHSEP at all. That is the correct, safe outcome: ":" is simply not
    # usable as a PATHSEP override for a Windows path; nothing here should
    # silently "work" by accident the way the vulnerability did. The one
    # invariant this test actually guards is that neither piece is ever
    # scanned/imported from the CWD.
    with pytest.raises(SystemExit):
        app(Root, env=env, argv=["good"], setup_logging=False)
    assert not (cwd / "MARKER").exists()


@pytest.mark.skipif(
    os.name == "nt", reason="hyphen-in-path splitting is exercised on POSIX"
)
def test_pathsep_hyphen_splits_real_directory_never_imports_cwd(tmp_path, monkeypatch):
    """POSIX equivalent: a custom separator that happens to occur INSIDE the
    real path (``"-"`` in ``/opt/my-tools``) splits it into ``/opt/my``
    (absolute, allowed on its own) and ``tools`` (relative, not explicitly
    so) -- the second piece would resolve against the CWD as
    ``<cwd>/tools`` and be scanned; a canary planted there must never be
    imported."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    split_off_dir = cwd / "tools"
    split_off_dir.mkdir()
    (split_off_dir / "evil.py").write_text(_EVIL_CWD_RELATIVE)
    cmds = tmp_path / "my-tools"
    cmds.mkdir()
    (cmds / "good.py").write_text(_GOOD)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("XA_PATHSEP", "-")
    monkeypatch.setenv("XA_CMDS_PATH", str(cmds))
    env = Env("xa", autoload=False)
    try:
        app(Root, env=env, argv=["good"], setup_logging=False)
    except SystemExit:
        pass
    assert not (cwd / "MARKER").exists()
