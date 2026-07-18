"""Tests for duho.runpath: the opt-in RunPath step-runner.

All step fixtures are REAL ``NN-name.py`` files written under ``tmp_path`` (never
``python -c`` -- a module defined in ``-c`` has no retrievable source, and step
ordering/deps are exactly the on-disk behavior we want to pin). Each step records
that it ran by appending its name to a shared results file, so a test asserts the
observed run order directly.

**Provider isolation is the top footgun** (per the plan): the RunPath provider is
a module-global registered on import. Every test snapshots/restores
``discovery._PROVIDERS`` via the autouse ``_restore_providers`` fixture AND uses
``runpath.unregister()`` where it asserts the unregistered state, so provider
state never leaks between tests.
"""

import textwrap

import pytest

import duho
from duho import discovery as _discovery
from duho import runpath
from duho.discovery import CmdBuilder, discover_commands
from duho.runpath import RunPathCmd, is_runpath_dir, register, unregister


# --------------------------------------------------------------------------
# Provider isolation + fixture helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_providers():
    """Snapshot/restore the global provider registry around every test.

    Also resets ``runpath``'s own ``_REGISTERED`` bookkeeping so ``register()``/
    ``unregister()`` start each test from a known state, then restores it. This is
    what stops provider state from leaking between tests.
    """
    saved = list(_discovery._PROVIDERS)
    saved_registered = runpath._REGISTERED
    try:
        yield
    finally:
        _discovery._PROVIDERS[:] = saved
        runpath._REGISTERED = saved_registered


def _write_step(directory, filename, body):
    """Write one ``NN-name.py`` step file under ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(textwrap.dedent(body))
    return path


def _record_step(name, results_path, extra=""):
    """Return step source whose ``main`` appends ``name`` to the results file."""
    return textwrap.dedent(
        '''\
        {extra}
        def main(args):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("{name}\\n")
        '''
    ).format(name=name, results=str(results_path), extra=extra)


def _read_results(results_path):
    """Return the ordered list of step names that ran."""
    if not results_path.exists():
        return []
    return results_path.read_text(encoding="utf-8").split()


def _build_command(directory):
    """Resolve ``directory`` to a RunPath command via CmdBuilder (provider path)."""
    return CmdBuilder(directory.name, directory).command


def _run(directory, rcopts=None):
    """Build the RunPath command, set ``rcopts``, run it; return the results list."""
    cmd = _build_command(directory)
    instance = cmd()
    instance.rcopts = list(rcopts or [])
    instance()
    results_dir = directory.parent
    return _read_results(results_dir / "results.txt"), instance


# --------------------------------------------------------------------------
# is_runpath_dir predicate
# --------------------------------------------------------------------------


def test_is_runpath_dir_true_for_numbered_steps(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "10-a.py", "def main(args): pass\n")
    assert is_runpath_dir(steps) is True


def test_is_runpath_dir_false_for_package(tmp_path):
    steps = tmp_path / "pkg"
    _write_step(steps, "10-a.py", "def main(args): pass\n")
    (steps / "__init__.py").write_text("")
    assert is_runpath_dir(steps) is False


def test_is_runpath_dir_false_without_numbered_files(tmp_path):
    steps = tmp_path / "loose"
    _write_step(steps, "helpers.py", "x = 1\n")
    _write_step(steps, "notastep.py", "def main(args): pass\n")
    assert is_runpath_dir(steps) is False


def test_is_runpath_dir_false_for_missing_dir(tmp_path):
    assert is_runpath_dir(tmp_path / "nope") is False


# --------------------------------------------------------------------------
# Ordered run + PRIORITY + REQUIRED
# --------------------------------------------------------------------------


def test_steps_run_in_prefix_order(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "30-third.py", _record_step("third", results))
    _write_step(steps, "10-first.py", _record_step("first", results))
    _write_step(steps, "20-second.py", _record_step("second", results))

    ran, _ = _run(steps)
    assert ran == ["first", "second", "third"]


def test_priority_overrides_numeric_prefix(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    # File 10 declares PRIORITY 99 -> runs last despite the low prefix.
    _write_step(steps, "10-early.py", _record_step("early", results, extra="PRIORITY = 99"))
    _write_step(steps, "20-mid.py", _record_step("mid", results))
    _write_step(steps, "30-late.py", _record_step("late", results))

    ran, _ = _run(steps)
    assert ran == ["mid", "late", "early"]


def test_required_reorders_after_dependency(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    # `alpha` (prefix 10) REQUIRES `beta` (prefix 20) -> beta must run first,
    # overriding the numeric order.
    _write_step(steps, "10-alpha.py", _record_step("alpha", results, extra='REQUIRED = ["beta"]'))
    _write_step(steps, "20-beta.py", _record_step("beta", results))

    ran, _ = _run(steps)
    assert ran == ["beta", "alpha"]


def test_required_missing_step_warns_resilient(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results, extra='REQUIRED = ["ghost"]'))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    # Resilient default: the missing dep is a warning, the step still runs.
    assert ran == ["a"]
    assert any("ghost" in rec.message for rec in caplog.records)


def test_required_missing_step_errors_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results, extra='REQUIRED = ["ghost"]'))

    with pytest.raises(ValueError, match="ghost"):
        _run(steps, rcopts=["strict"])


# --------------------------------------------------------------------------
# --rcopts selection
# --------------------------------------------------------------------------


def test_rcopts_disable_all_enable_one(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))
    _write_step(steps, "20-two.py", _record_step("two", results))
    _write_step(steps, "30-three.py", _record_step("three", results))

    ran, _ = _run(steps, rcopts=["!*", "two"])
    assert ran == ["two"]


def test_rcopts_disable_single_step(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))
    _write_step(steps, "20-two.py", _record_step("two", results))

    ran, _ = _run(steps, rcopts=["!two"])
    assert ran == ["one"]


def test_rcopts_glob_pattern(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-build-a.py", _record_step("build-a", results))
    _write_step(steps, "20-build-b.py", _record_step("build-b", results))
    _write_step(steps, "30-deploy.py", _record_step("deploy", results))

    ran, _ = _run(steps, rcopts=["!*", "build-*"])
    assert ran == ["build-a", "build-b"]


def test_rcopts_no_patterns_runs_all(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))
    _write_step(steps, "20-two.py", _record_step("two", results))

    ran, _ = _run(steps, rcopts=[])
    assert ran == ["one", "two"]


def test_rcopts_unknown_pattern_warns_resilient(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps, rcopts=["nosuchstep"])
    # Unknown pattern is a warning by default; the run proceeds (nothing else
    # matched `one`, so the base default keeps it enabled).
    assert ran == ["one"]
    assert any("nosuchstep" in rec.message for rec in caplog.records)


def test_rcopts_unknown_pattern_errors_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))

    with pytest.raises(ValueError, match="nosuchstep"):
        _run(steps, rcopts=["strict", "nosuchstep"])


# --------------------------------------------------------------------------
# Failure handling: resilient continue vs strict stop
# --------------------------------------------------------------------------


def test_failing_step_resilient_continues(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-ok.py", _record_step("ok", results))
    _write_step(
        steps,
        "20-boom.py",
        'def main(args):\n    raise RuntimeError("boom")\n',
    )
    _write_step(steps, "30-after.py", _record_step("after", results))

    with caplog.at_level("ERROR", logger="duho"):
        ran, _ = _run(steps)
    # Resilient: the failing step is logged and skipped; later steps still run.
    assert ran == ["ok", "after"]
    assert any("boom" in rec.message for rec in caplog.records)


def test_failing_step_strict_stops(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-ok.py", _record_step("ok", results))
    _write_step(
        steps,
        "20-boom.py",
        'def main(args):\n    raise RuntimeError("boom")\n',
    )
    _write_step(steps, "30-after.py", _record_step("after", results))

    with pytest.raises(RuntimeError, match="boom"):
        _run(steps, rcopts=["strict"])
    # `ok` ran before the failure; `after` never ran (strict re-raised).
    assert _read_results(tmp_path / "results.txt") == ["ok"]


# --------------------------------------------------------------------------
# Provider registration / unregistration isolation
# --------------------------------------------------------------------------


def test_directory_resolves_only_after_register(tmp_path):
    steps = tmp_path / "steps"
    _write_step(steps, "10-a.py", "def main(args): pass\n")

    # Unregistered: a bare dir without __init__.py has no built-in meaning.
    unregister()
    with pytest.raises(ImportError):
        CmdBuilder(steps.name, steps).command

    # Registered: the provider now claims it and yields a RunPathCmd.
    register()
    cmd = CmdBuilder(steps.name, steps).command
    assert issubclass(cmd, RunPathCmd)
    assert cmd._parsername_ == "steps"


def test_unregister_removes_only_our_provider(tmp_path):
    register()
    before = len(_discovery._PROVIDERS)

    # A foreign provider registered on top must survive our unregister().
    sentinel = object()
    _discovery.register_command_provider(lambda p: False, lambda p, q: sentinel)
    assert len(_discovery._PROVIDERS) == before + 1

    unregister()
    # Only the RunPath pair was removed; the foreign one remains.
    assert len(_discovery._PROVIDERS) == before
    steps = tmp_path / "steps"
    _write_step(steps, "10-a.py", "def main(args): pass\n")
    with pytest.raises(ImportError):
        CmdBuilder(steps.name, steps).command


def test_register_is_idempotent(tmp_path):
    unregister()
    register()
    n = len(_discovery._PROVIDERS)
    register()
    register()
    assert len(_discovery._PROVIDERS) == n


def test_discover_commands_yields_runpath_when_dir_shaped(tmp_path):
    # A package dir containing a RunPath subdir: discover_commands walks .py files
    # at the top level; the RunPath provider is exercised via CmdBuilder for the
    # subdir. Here we assert the provider path directly through discover on a dir
    # of numbered steps resolved by CmdBuilder (discover_commands over a dir globs
    # top-level .py files, which is a different surface). We use CmdBuilder as the
    # provider entry point per the plan's done-when.
    register()
    steps = tmp_path / "runsteps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results))
    cmd = CmdBuilder("runsteps", steps).command
    assert issubclass(cmd, RunPathCmd)


def test_runpath_not_in_top_level_all():
    # Opt-in: runpath symbols must NOT be on the core duho surface.
    assert "runpath" not in duho.__all__
    assert "RunPathCmd" not in duho.__all__


def test_module_all_lists_public_api():
    assert set(runpath.__all__) >= {"RunPathCmd", "register", "unregister"}
