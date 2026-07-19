"""Phase D regression tests: fanout non-int isolation (M5) + runpath resilience (M4)."""

import textwrap

import pytest

from duho.discovery import CmdBuilder
from duho.fanout import run_targets
from duho import runpath as _runpath


@pytest.fixture(autouse=True)
def _register_runpath():
    _runpath.register()
    yield
    _runpath.unregister()


# -- D4 / M5: a non-int return from one target must not abort the fan-out -----


def test_non_int_return_is_isolated():
    def func(target):
        if target == "bad":
            return "not-an-int"
        return 0

    # Pre-fix: int("not-an-int") escaped run_targets. Post-fix: bad -> 1, others
    # -> 0, aggregate max -> 1, no exception escapes.
    rc = run_targets(func, ["a", "bad", "b"], max_workers=2)
    assert rc == 1


# -- D5 / M4: runpath resilience ---------------------------------------------


def _write_step(directory, filename, body):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(textwrap.dedent(body))
    return path


def _build_command(directory):
    return CmdBuilder(directory.name, directory).command


def _run(directory, rcopts=None):
    cmd = _build_command(directory)
    instance = cmd()
    instance.rcopts = list(rcopts or [])
    instance()
    return instance


def test_import_error_step_skipped_resilient(tmp_path, caplog):
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-good.py",
        f'''
        def main(args):
            with open(r"{results}", "a") as fh:
                fh.write("good\\n")
        ''',
    )
    _write_step(
        steps,
        "20-broken.py",
        '''
        import a_module_that_does_not_exist_xyz  # noqa: F401
        def main(args):
            pass
        ''',
    )
    with caplog.at_level("WARNING", logger="duho"):
        _run(steps)
    # good ran; broken was skipped (import error), no exception escaped.
    assert results.read_text().split() == ["good"]
    assert any("broken" in r.getMessage() for r in caplog.records)


def test_import_error_step_raises_strict(tmp_path):
    steps = tmp_path / "steps"
    _write_step(
        steps,
        "10-broken.py",
        '''
        import a_module_that_does_not_exist_xyz  # noqa: F401
        def main(args):
            pass
        ''',
    )
    with pytest.raises(ImportError):
        _run(steps, rcopts=["strict"])


def test_syntax_error_still_surfaces(tmp_path):
    # A SyntaxError is a bug, not environmental -- it must NOT be swallowed.
    steps = tmp_path / "steps"
    _write_step(steps, "10-bad.py", "def main(args)\n    pass\n")  # missing colon
    with pytest.raises(SyntaxError):
        _run(steps)


def test_required_disabled_dep_warns_resilient(tmp_path, caplog):
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-build.py",
        f'''
        def main(args):
            with open(r"{results}", "a") as fh:
                fh.write("build\\n")
        ''',
    )
    _write_step(
        steps,
        "20-deploy.py",
        f'''
        REQUIRED = ["build"]
        def main(args):
            with open(r"{results}", "a") as fh:
                fh.write("deploy\\n")
        ''',
    )
    with caplog.at_level("WARNING", logger="duho"):
        _run(steps, rcopts=["!*", "deploy"])
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "deploy" in messages and "build" in messages
