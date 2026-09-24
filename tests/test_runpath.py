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

import functools
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
    what stops provider state from leaking between tests. ``_BASE`` (the class
    every provider-built RunPathCmd subclass ALSO inherits from, set via
    ``register(base=...)``) is module-global the same way -- snapshot/restore it
    too so a test that changes it never leaks into the next. ``_ADAPTER``
    (``register(step_adapter=...)``) is module-global for the same reason, and
    leaks harder: it is consulted per step run, so it would affect every
    already-built command in a later test, not just newly built ones.
    """
    saved = list(_discovery._PROVIDERS)
    saved_registered = runpath._REGISTERED
    saved_base = runpath._BASE
    saved_adapter = runpath._ADAPTER
    try:
        yield
    finally:
        _discovery._PROVIDERS[:] = saved
        runpath._REGISTERED = saved_registered
        runpath._BASE = saved_base
        runpath._ADAPTER = saved_adapter


def _write_step(directory, filename, body):
    """Write one ``NN-name.py`` step file under ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(textwrap.dedent(body))
    return path


def _record_step(name, results_path, extra=""):
    """Return step source whose ``main`` appends ``name`` to the results file."""
    return textwrap.dedent("""\
        {extra}
        def main(args):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("{name}\\n")
        """).format(name=name, results=str(results_path), extra=extra)


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


def _run_rc(directory, rcopts=None):
    """Like :func:`_run`, but also returns the actual ``__call__`` exit code."""
    cmd = _build_command(directory)
    instance = cmd()
    instance.rcopts = list(rcopts or [])
    code = instance()
    results_dir = directory.parent
    return _read_results(results_dir / "results.txt"), code


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
    _write_step(
        steps, "10-early.py", _record_step("early", results, extra="PRIORITY = 99")
    )
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
    _write_step(
        steps,
        "10-alpha.py",
        _record_step("alpha", results, extra='REQUIRED = ["beta"]'),
    )
    _write_step(steps, "20-beta.py", _record_step("beta", results))

    ran, _ = _run(steps)
    assert ran == ["beta", "alpha"]


def test_required_missing_step_warns_resilient(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps, "10-a.py", _record_step("a", results, extra='REQUIRED = ["ghost"]')
    )

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    # Resilient default: the missing dep is a warning, the step still runs.
    assert ran == ["a"]
    assert any("ghost" in rec.message for rec in caplog.records)


def test_required_missing_step_errors_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps, "10-a.py", _record_step("a", results, extra='REQUIRED = ["ghost"]')
    )

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


def test_rcopts_real_cli_comma_joined_flattens(tmp_path):
    # Regression: a single --rcopts '!*,two' from the REAL argparse parser
    # (not the _run() bypass helper, which sets .rcopts directly as a plain
    # list) used to produce a nested [['!*', 'two']] instead of a flat
    # ['!*', 'two'] -- Extend()'s nargs="*" + a comma-splitting type
    # double-collected. Parse through the actual built parser here.
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))
    _write_step(steps, "20-two.py", _record_step("two", results))

    cmd = _build_command(steps)
    parser = cmd._parser_()
    instance = parser.parse_args(["--rcopts", "!*,two"])
    assert instance.rcopts == ["!*", "two"]
    instance()
    assert _read_results(results) == ["two"]


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
    # A plain filename (no `?` suffix) is strict-by-default for THAT step
    # (restoring the predecessor's hardcoded `RcOptions(strict=True)` base),
    # independent
    # of the run-wide --rcopts flag. An explicit `!strict` on --rcopts (CLI,
    # wins last per the confirmed precedence) overrides every step's own
    # filename-derived strict setting back to resilient -- this is the
    # portable way to exercise "resilient continue" (a literal `?` filename
    # suffix is not a valid Windows path character, so the `?`-suffix override
    # itself is exercised directly against `_parse_file_modifiers`, see
    # test_file_modifiers_* below, and end-to-end via `!name` on POSIX-legal
    # filenames only).
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
        ran, _ = _run(steps, rcopts=["!strict"])
    # Resilient: the failing step is logged and skipped; later steps still run.
    assert ran == ["ok", "after"]
    assert any("boom" in rec.message for rec in caplog.records)


def test_failing_step_strict_by_default_even_without_run_wide_strict(tmp_path):
    # A plain filename's own strict-by-default stops the run even when
    # --rcopts never mentions `strict` at all (the per-step default,
    # independent of the run-wide flag).
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
        _run(steps)
    assert _read_results(tmp_path / "results.txt") == ["ok"]


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


# --------------------------------------------------------------------------
# `__main__.py` init/success/finally_ lifecycle
# --------------------------------------------------------------------------


def _write_init(directory, body):
    """Write ``__main__.py`` under ``directory`` (mirrors ``_write_step``)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "__main__.py"
    path.write_text(textwrap.dedent(body))
    return path


def test_init_hook_ctx_reaches_two_arg_step_one_arg_step_unaffected(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return {"greeting": "hi"}
        """,
    )
    _write_step(
        steps,
        "10-legacy.py",
        _record_step("legacy", results),
    )
    _write_step(
        steps,
        "20-modern.py",
        """\
        def main(cmd, ctx):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(ctx["greeting"] + "\\n")
        """.format(results=str(results)),
    )

    ran, _ = _run(steps)
    assert ran == ["legacy", "hi"]


def test_no_step_adapter_calls_steps_exactly_as_written(tmp_path):
    """The default must be indistinguishable from before the hook existed."""
    register()
    assert runpath._ADAPTER is None
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))
    _write_step(steps, "20-two.py", _record_step("two", results))

    ran, _ = _run(steps)
    assert ran == ["one", "two"]


def test_step_adapter_can_give_steps_an_app_specific_signature(tmp_path):
    """The point of the hook: an app defines what a step looks like.

    Here steps are written ``(ctx, cmd)`` -- context FIRST, duho's opposite --
    with no per-file decorator, which is what makes it an app-wide convention
    rather than something every step has to opt into.
    """
    results = tmp_path / "results.txt"

    def adapter(entrypoint):
        def call(cmd, ctx=None):
            return entrypoint(ctx, cmd)

        return call

    register(step_adapter=adapter)
    steps = tmp_path / "steps"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return "CTX"
        """,
    )
    _write_step(
        steps,
        "10-app-shape.py",
        """\
        def main(ctx, cmd):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(ctx + ":" + type(cmd).__name__ + "\\n")
        """.format(results=str(results)),
    )

    _run(steps)
    written = results.read_text(encoding="utf-8").strip()
    assert written.startswith("CTX:")


def test_step_adapter_may_pass_entrypoints_through_untouched(tmp_path):
    """An adapter that returns the entrypoint leaves duho-native steps alone.

    This is how an app supports BOTH shapes: adapt what it recognises, return
    everything else unchanged.
    """
    results = tmp_path / "results.txt"

    def adapter(entrypoint):
        return entrypoint

    register(step_adapter=adapter)
    steps = tmp_path / "steps"
    _write_step(steps, "10-one.py", _record_step("one", results))

    ran, _ = _run(steps)
    assert ran == ["one"]


def test_step_adapter_result_drives_arity_detection(tmp_path):
    """A wrapper that changes the signature is honored, not second-guessed.

    The adapted callable takes ``(cmd, ctx)``, so it must receive ctx even
    though the step underneath was written 1-arg.
    """
    results = tmp_path / "results.txt"

    def adapter(entrypoint):
        def call(cmd, ctx=None):
            return entrypoint("%s" % ctx)

        return call

    register(step_adapter=adapter)
    steps = tmp_path / "steps"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return "FROM-INIT"
        """,
    )
    _write_step(
        steps,
        "10-one.py",
        """\
        def main(seen):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(seen + "\\n")
        """.format(results=str(results)),
    )

    _run(steps)
    assert results.read_text(encoding="utf-8").strip() == "FROM-INIT"


def test_step_adapter_returning_nothing_does_not_delete_the_step(tmp_path):
    """A forgetful adapter must not silently turn a step into a no-op."""
    results = tmp_path / "results.txt"

    def adapter(entrypoint):
        return None  # forgot to return

    register(step_adapter=adapter)
    steps = tmp_path / "steps"
    _write_step(steps, "10-one.py", _record_step("one", results))

    ran, _ = _run(steps)
    assert ran == ["one"]


def test_step_adapter_can_be_cleared_and_omitted():
    """``None`` clears it; omitting the argument keeps whatever is set."""
    register(step_adapter=lambda entrypoint: entrypoint)
    assert runpath._ADAPTER is not None

    register()  # omitted -> unchanged
    assert runpath._ADAPTER is not None

    register(step_adapter=None)  # explicit -> cleared
    assert runpath._ADAPTER is None


def test_init_absent_behaves_byte_identical_to_before(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-one.py", _record_step("one", results))
    _write_step(steps, "20-two.py", _record_step("two", results))

    ran, _ = _run(steps)
    assert ran == ["one", "two"]


def test_init_success_and_finally_fire_exactly_once_on_clean_run(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    calls = tmp_path / "calls.txt"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return "ctx"

        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success:" + ctx + "\\n")

        def finally_(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("finally:" + ctx + "\\n")
        """.format(calls=str(calls)),
    )
    _write_step(steps, "10-a.py", _record_step("a", results))

    _run(steps)
    lines = calls.read_text(encoding="utf-8").splitlines()
    # success() runs INSIDE the try, before finally_ -- matching
    # discovery.run_command's own main-then-success-then-finally_ order (D008;
    # [minor] behavior change: this used to be finally_-then-success, which
    # left success() seeing a ctx that finally_ had already torn down). Each
    # fires exactly once.
    assert lines == ["success:ctx", "finally:ctx"]


def test_init_finally_runs_even_when_a_step_raises_resilient(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    calls = tmp_path / "calls.txt"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return "ctx"

        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success\\n")

        def finally_(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("finally\\n")
        """.format(calls=str(calls)),
    )
    _write_step(steps, "10-ok.py", _record_step("ok", results))
    _write_step(
        steps,
        "20-boom.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )

    # boom is strict-by-default (plain filename), so this run raises;
    # explicit !strict makes it resilient again (the run completes).
    _run(steps, rcopts=["!strict"])
    lines = calls.read_text(encoding="utf-8").splitlines()
    # finally_ always runs; success() is gated on the aggregate outcome (D007)
    # -- a step that failed, even resiliently, means success() does NOT fire,
    # matching discovery.run_command's own "success only on a clean result"
    # contract (M22).
    assert "finally" in lines
    assert "success" not in lines


def test_init_finally_runs_when_step_raises_and_aborts_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    calls = tmp_path / "calls.txt"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return "ctx"

        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success\\n")

        def finally_(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("finally\\n")
        """.format(calls=str(calls)),
    )
    _write_step(steps, "10-ok.py", _record_step("ok", results))
    _write_step(
        steps,
        "20-boom.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )

    with pytest.raises(RuntimeError, match="boom"):
        _run(steps)  # plain filename: strict by default, aborts the run
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert lines == ["finally"]  # finally_ runs unconditionally; success does not


def test_init_raising_is_always_fatal_even_without_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            raise RuntimeError("init boom")
        """,
    )
    _write_step(steps, "10-a.py", "def main(cmd): pass\n")

    with pytest.raises(RuntimeError, match="init boom"):
        _run(steps)  # no --rcopts strict at all -- init failure is unconditional


# --------------------------------------------------------------------------
# Filename-encoded per-step options
# --------------------------------------------------------------------------


def test_file_modifiers_parse_bang_prefix_disables():
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("!provision")
    assert clean == "provision"
    assert opts.enabled is False
    assert opts.strict is True


def test_file_modifiers_parse_strict_token_suffix_non_strict():
    # `?` is gone -- non-strict is the `!strict` token, same spelling
    # --rcopts uses, split on `:` or `;` (both accepted, see _split_tokens).
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("provision:!strict")
    assert clean == "provision"
    assert opts.enabled is True
    assert opts.strict is False


def test_file_modifiers_parse_semicolon_separator_equivalent():
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("provision;!strict")
    assert clean == "provision"
    assert opts.strict is False


def test_file_modifiers_parse_plain_stem_strict_enabled():
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("provision")
    assert clean == "provision"
    assert opts.enabled is True
    assert opts.strict is True


def test_file_modifiers_parse_combined_bang_and_strict_token():
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("!provision:!strict")
    assert clean == "provision"
    assert opts.enabled is False
    assert opts.strict is False


def test_file_modifiers_parse_extra_tokens_and_key_value():
    # `key`/`!key`/`key=value` tokens, same grammar --rcopts uses per entry.
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("provision:key1:!key2:key3=val")
    assert clean == "provision"
    assert opts.extra == {"key1": True, "key2": False, "key3": "val"}


def test_file_modifiers_enabled_token_equivalent_to_bang_prefix():
    from duho.runpath import _parse_file_modifiers

    clean1, opts1 = _parse_file_modifiers("!provision")
    clean2, opts2 = _parse_file_modifiers("provision:!enable")
    assert clean1 == clean2 == "provision"
    assert opts1.enabled is opts2.enabled is False


def test_file_modifiers_explicit_enabled_token_wins_over_bang_prefix():
    # More specific wins: an explicit `enable`/`!enable` token overrides a
    # leading `!` when both are somehow present on the same filename.
    from duho.runpath import _parse_file_modifiers

    clean, opts = _parse_file_modifiers("!provision:enable")
    assert clean == "provision"
    assert opts.enabled is True


def test_rcopts_bang_prefix_equivalent_to_enabled_token():
    from duho.runpath import _Selection

    sel_bang = _Selection.parse(["!step1"])
    sel_token = _Selection.parse(["step1:!enable"])
    assert sel_bang.decide("step1") is sel_token.decide("step1") is False


def test_rcopts_explicit_enabled_token_wins_over_bang_prefix():
    from duho.runpath import _Selection

    sel = _Selection.parse(["!step1:enable"])
    assert sel.decide("step1") is True


def test_rcopts_per_pattern_strict_override():
    # step1:!strict scopes non-strict to steps matching step1 only; an
    # unrelated step's strict handling is untouched.
    from duho.runpath import _Selection

    sel = _Selection.parse(["!*", "step1:!strict:key=test"])
    assert sel.decide("step1") is True
    assert sel.decide("other") is False
    assert sel.step_strict("step1", True) is False
    assert sel.step_strict("other", True) is True


def test_file_modifiers_stripped_before_nn_name_split():
    from duho.runpath import _parse_file_modifiers, _parse_step_filename

    clean, opts = _parse_file_modifiers("!02-provision")
    assert clean == "02-provision"
    assert _parse_step_filename(clean) == (2, "provision")
    assert opts.enabled is False


def test_filename_bang_disables_step_by_default(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "01-one.py", _record_step("one", results))
    _write_step(steps, "!02-two.py", _record_step("two", results))
    _write_step(steps, "03-three.py", _record_step("three", results))

    ran, _ = _run(steps)
    assert ran == ["one", "three"]


def test_filename_bang_disable_overridden_by_rcopts(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "!01-two.py", _record_step("two", results))

    # CLI --rcopts enabling `two` overrides the filename-level disable.
    ran, _ = _run(steps, rcopts=["two"])
    assert ran == ["two"]


def test_filename_no_modifier_step_is_strict_by_default(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "01-boom.py", 'def main(cmd):\n    raise RuntimeError("x")\n')

    with pytest.raises(RuntimeError):
        _run(steps)


def test_two_symlinks_one_file_different_effective_options(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    target = tmp_path / "_shared_step_body.py"
    target.write_text("def main(cmd):\n    pass\n")
    try:
        (steps / "02-step.py").symlink_to(target)
        (steps / "!02-step2.py").symlink_to(target)
    except OSError:
        pytest.skip(
            "symlink creation not permitted (needs elevated privileges on Windows)"
        )

    register()
    from duho.runpath import _load_steps, _Selection

    loaded, present, _broken = _load_steps(steps, "steps", _Selection.parse([]))
    # Both directory entries resolve to different effective enabled state from
    # the SAME physical file -- symlink-transparent, since the parse reads the
    # entry's own name. `step2` is disabled, so (D010) it is never imported
    # and never appears in `loaded`; it is still `present` on disk though.
    by_name = {s.name: s for s in loaded}
    assert by_name["step"].file_enabled is True
    assert "step2" not in by_name
    assert set(present) == {"step", "step2"}


# --------------------------------------------------------------------------
# BEFORE/AFTER soft ordering
# --------------------------------------------------------------------------


def test_before_after_reorder_independent_of_priority(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    # `a` (prefix 90, would sort LAST) declares BEFORE=["c"]; `b` (prefix 50)
    # declares AFTER=["a"]; `c` (prefix 10, would sort FIRST) has no relations.
    # Expected effective order: a, then b and c in some relative order that
    # respects a-before-both.
    _write_step(steps, "90-a.py", _record_step("a", results, extra='BEFORE = ["c"]'))
    _write_step(steps, "50-b.py", _record_step("b", results, extra='AFTER = ["a"]'))
    _write_step(steps, "10-c.py", _record_step("c", results))

    ran, _ = _run(steps)
    assert ran.index("a") < ran.index("b")
    assert ran.index("a") < ran.index("c")


def test_before_after_missing_name_is_silent_noop(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps, "10-a.py", _record_step("a", results, extra='BEFORE = ["ghost"]')
    )

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    assert ran == ["a"]
    # No warning for the missing BEFORE target (contrast with REQUIRED below).
    assert not any("ghost" in rec.message for rec in caplog.records)


def test_required_missing_name_still_warns_unlike_before_after(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps, "10-a.py", _record_step("a", results, extra='REQUIRED = ["ghost"]')
    )

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    assert ran == ["a"]
    assert any("ghost" in rec.message for rec in caplog.records)


def test_before_after_disabled_target_is_silent_noop(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results, extra='AFTER = ["b"]'))
    _write_step(steps, "!20-b.py", _record_step("b", results))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    # `b` is disabled by its filename; `a`'s AFTER=["b"] is a silent no-op.
    assert ran == ["a"]
    assert not any(
        "disabled" in rec.message.lower() and "b" in rec.message
        for rec in caplog.records
    )


def test_mixed_before_required_cycle_broken_deterministically(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    # `x` REQUIREs `y`; `y` declares BEFORE=["x"] is fine (consistent), but here
    # make an actual cycle: `x` REQUIRES `y`, `y` REQUIRES `x` (mixed with a
    # BEFORE edge reinforcing the same cycle) -- must not hang, must emit both.
    _write_step(
        steps,
        "10-x.py",
        _record_step("x", results, extra='REQUIRED = ["y"]\nBEFORE = ["y"]'),
    )
    _write_step(steps, "20-y.py", _record_step("y", results, extra='REQUIRED = ["x"]'))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    assert set(ran) == {"x", "y"}


# --------------------------------------------------------------------------
# register(base=...): the built RunPathCmd subclass inherits a custom base
# --------------------------------------------------------------------------


def test_default_base_is_loggingargs_gives_real_logger_and_set_loglevels(tmp_path):
    # Regression: a bare RunPathCmd built via the provider used to have NO
    # _logger_/_set_loglevels_ as real inherited methods (only data fields
    # propagate via app()'s parents= namespace-copying, not class
    # inheritance) -- so -v/stderr logging setup never activated for any
    # RunPath command. The default base is now LoggingArgs.
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "10-a.py", "def main(cmd): pass\n")

    cmd = _build_command(steps)
    instance = cmd()
    assert hasattr(instance, "_logger_")
    assert hasattr(instance, "_set_loglevels_")


def test_register_base_lets_a_custom_root_class_be_inherited(tmp_path):
    from duho import LoggingArgs

    class MyRoot(LoggingArgs):
        label: str = "custom"
        ("--label",)

        def greet(self):
            return "hi " + self.label

    unregister()
    register(base=MyRoot)
    steps = tmp_path / "steps"
    _write_step(steps, "10-a.py", "def main(cmd): pass\n")

    cmd = _build_command(steps)
    instance = cmd()
    assert isinstance(instance, MyRoot)
    assert instance.greet() == "hi custom"


# --------------------------------------------------------------------------
# Ordering stability (Kahn's algorithm): a regression that only shows up
# with 3+ steps, where a naive "whole pass" sort lets a reordered step jump
# past every unrelated LATER step, not just its own dependency.
# --------------------------------------------------------------------------


def test_ordering_only_jumps_its_own_dependency_not_unrelated_later_steps(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-migrate.py",
        _record_step("migrate", results, extra='AFTER = ["backup"]'),
    )
    _write_step(steps, "30-backup.py", _record_step("backup", results))
    _write_step(steps, "90-cleanup.py", _record_step("cleanup", results))

    ran, _ = _run(steps)
    # migrate is reordered after backup, but must NOT also jump ahead of
    # cleanup (an unrelated, later, unconnected step).
    assert ran == ["backup", "migrate", "cleanup"]


def test_required_ordering_does_not_jump_past_unrelated_later_step(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-alpha.py",
        _record_step("alpha", results, extra='REQUIRED = ["beta"]'),
    )
    _write_step(steps, "20-beta.py", _record_step("beta", results))
    _write_step(steps, "99-teardown.py", _record_step("teardown", results))

    ran, _ = _run(steps)
    assert ran == ["beta", "alpha", "teardown"]


# --------------------------------------------------------------------------
# Step return codes: a non-zero int return is a failure, handled through the
# same strict-vs-resilient path as an exception; the aggregate exit code is
# the max of every step's own code.
# --------------------------------------------------------------------------


def test_nonzero_step_return_is_strict_by_default(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-boom.py", "def main(cmd):\n    return 1\n")
    _write_step(steps, "20-after.py", _record_step("after", results))

    with pytest.raises(ValueError, match="non-zero"):
        _run(steps)
    assert _read_results(results) == []


def test_nonzero_step_return_resilient_continues_and_sets_exit_code(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-boom;!strict.py", "def main(cmd):\n    return 3\n")
    _write_step(steps, "20-after.py", _record_step("after", results))

    ran, code = _run_rc(steps)
    assert ran == ["after"]
    assert code == 3


def test_exit_code_is_max_of_step_codes_and_zero_on_clean_run(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "10-a;!strict.py", "def main(cmd):\n    return 2\n")
    _write_step(steps, "20-b;!strict.py", "def main(cmd):\n    return 5\n")
    _write_step(steps, "30-c.py", "def main(cmd):\n    return 0\n")

    _ran, code = _run_rc(steps)
    assert code == 5

    clean_steps = steps.parent / "clean"
    _write_step(clean_steps, "10-a.py", "def main(cmd):\n    pass\n")
    _, clean_code = _run_rc(clean_steps)
    assert clean_code == 0


def test_swallowed_exception_failure_contributes_exit_code_one(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(
        steps, "10-boom;!strict.py", 'def main(cmd):\n    raise RuntimeError("x")\n'
    )

    _ran, code = _run_rc(steps)
    assert code == 1


def test_success_hook_does_not_fire_when_a_step_returned_nonzero_resilient(tmp_path):
    register()
    steps = tmp_path / "steps"
    calls = tmp_path / "calls.txt"
    _write_init(
        steps,
        """\
        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success\\n")
        """,
    )
    _write_step(steps, "10-boom;!strict.py", "def main(cmd):\n    return 1\n")

    _run(steps)
    assert not calls.exists() or "success" not in calls.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# REQUIRED and a failed dependency: a step whose REQUIRED dependency actually
# ran (or tried to import) and failed is skipped too, not just reordered.
# --------------------------------------------------------------------------


def test_dependent_is_skipped_when_required_step_raises_resilient(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps, "10-build;!strict.py", 'def main(cmd):\n    raise RuntimeError("boom")\n'
    )
    _write_step(
        steps,
        "20-deploy;!strict.py",
        _record_step("deploy", results, extra='REQUIRED = ["build"]'),
    )

    with caplog.at_level("WARNING", logger="duho"):
        ran, code = _run_rc(steps)
    assert ran == []
    assert code != 0
    assert any(
        "deploy" in rec.message and "build" in rec.message for rec in caplog.records
    )


def test_dependent_aborts_strict_when_required_step_fails(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(
        steps, "10-build;!strict.py", 'def main(cmd):\n    raise RuntimeError("boom")\n'
    )
    _write_step(steps, "20-deploy.py", "REQUIRED = ['build']\ndef main(cmd): pass\n")

    with pytest.raises(ValueError, match="build"):
        _run(steps)


def test_dependent_is_skipped_when_required_step_import_fails(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-build;!strict.py",
        "import a_module_that_does_not_exist_xyz\ndef main(cmd): pass\n",
    )
    _write_step(
        steps,
        "20-deploy;!strict.py",
        _record_step("deploy", results, extra='REQUIRED = ["build"]'),
    )
    with caplog.at_level("WARNING", logger="duho"):
        ran, _code = _run_rc(steps)
    assert ran == []


# --------------------------------------------------------------------------
# --rcopts token parsing no longer forces strict from an unrelated token.
# --------------------------------------------------------------------------


def test_rcopts_enable_token_does_not_force_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-report;!strict.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )
    _write_step(steps, "20-after.py", _record_step("after", results))

    # `report:enable` carries no strict token at all; the step's own !strict
    # filename default must be left alone.
    ran, _ = _run(steps, rcopts=["report:enable"])
    assert ran == ["after"]


def test_rcopts_key_value_extra_token_does_not_force_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-report;!strict.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )
    _write_step(steps, "20-after.py", _record_step("after", results))

    ran, _ = _run(steps, rcopts=["report:channel=ops"])
    assert ran == ["after"]


def test_rcopts_trailing_separator_does_not_force_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-report;!strict.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )
    _write_step(steps, "20-after.py", _record_step("after", results))

    ran, _ = _run(steps, rcopts=["report:"])
    assert ran == ["after"]


def test_rcopts_explicit_strict_token_still_overrides(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(
        steps,
        "10-report;!strict.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )

    with pytest.raises(RuntimeError, match="boom"):
        _run(steps, rcopts=["report:strict"])


def test_rcopts_pattern_whitespace_is_stripped(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-build.py", _record_step("build", results))

    ran, _ = _run(steps, rcopts=[" build : !strict "])
    assert ran == ["build"]


# --------------------------------------------------------------------------
# Disabled/deselected steps are never imported (their module body never runs).
# --------------------------------------------------------------------------


def test_disabled_step_module_body_never_executes(tmp_path):
    register()
    steps = tmp_path / "steps"
    marker = tmp_path / "marker.txt"
    _write_step(
        steps,
        "!20-gpu.py",
        f"""
        with open(r"{marker}", "a", encoding="utf-8") as fh:
            fh.write("imported\\n")
        def main(cmd):
            pass
        """,
    )
    _write_step(steps, "10-build.py", "def main(cmd): pass\n")

    _run(steps)
    assert not marker.exists()


def test_deselected_step_module_body_never_executes(tmp_path):
    register()
    steps = tmp_path / "steps"
    marker = tmp_path / "marker.txt"
    _write_step(
        steps,
        "20-gpu.py",
        f"""
        with open(r"{marker}", "a", encoding="utf-8") as fh:
            fh.write("imported\\n")
        def main(cmd):
            pass
        """,
    )
    _write_step(steps, "10-build.py", "def main(cmd): pass\n")

    _run(steps, rcopts=["!*", "build"])
    assert not marker.exists()


def test_disabled_step_broken_import_never_aborts_a_strict_run(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "!20-gpu.py",
        "import a_module_that_does_not_exist_xyz\ndef main(cmd): pass\n",
    )
    _write_step(steps, "10-build.py", _record_step("build", results))

    ran, _ = _run(steps, rcopts=["strict"])
    assert ran == ["build"]


# --------------------------------------------------------------------------
# A step's own strict setting governs an import failure, not just the
# run-wide flag.
# --------------------------------------------------------------------------


def test_plain_steps_import_failure_aborts_even_without_run_wide_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-broken.py",
        "import a_module_that_does_not_exist_xyz\ndef main(cmd): pass\n",
    )
    _write_step(steps, "20-after.py", _record_step("after", results))

    with pytest.raises(ImportError):
        _run(steps)
    assert _read_results(results) == []


# --------------------------------------------------------------------------
# register(base=<a Cli app root>): the built RunPathCmd stays the step
# runner, never the root's own subcommand tree or __call__.
# --------------------------------------------------------------------------


def test_register_base_cli_root_does_not_turn_rc_into_a_subcommand_tree(tmp_path):
    from duho import Cli, LoggingArgs

    class Hello(duho.Cmd):
        def __call__(self):
            return 0

    class MyApp(LoggingArgs, Cli):
        _version_ = "9.9.9"
        _subcommands_ = [Hello]

    unregister()
    register(base=MyApp)
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results))

    ran, code = _run_rc(steps)
    assert ran == ["a"]
    assert code == 0

    # No nested-subcommand requirement and no inherited --version flag.
    cmd = _build_command(steps)
    parser = cmd._parser_()
    help_text = parser.format_help()
    assert "--version" not in help_text


def test_register_base_cli_root_custom_call_does_not_replace_step_runner(tmp_path):
    from duho import Cli, LoggingArgs

    class MyApp(LoggingArgs, Cli):
        def __call__(self):
            return 7

    unregister()
    register(base=MyApp)
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results))

    ran, code = _run_rc(steps)
    assert ran == ["a"]
    assert code == 0


# --------------------------------------------------------------------------
# Arity detection follows a step_adapter's OWN signature, not a
# functools.wraps-preserved original.
# --------------------------------------------------------------------------


def test_step_adapter_with_functools_wraps_still_receives_ctx(tmp_path):
    results = tmp_path / "results.txt"

    def adapter(entrypoint):
        @functools.wraps(entrypoint)
        def call(cmd, ctx=None):
            return entrypoint("%s" % ctx)

        return call

    register(step_adapter=adapter)
    steps = tmp_path / "steps"
    _write_init(
        steps,
        """\
        def init(cmd, logger):
            return "FROM-INIT"
        """,
    )
    _write_step(
        steps,
        "10-one.py",
        """\
        def main(seen):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(seen + "\\n")
        """.format(results=str(results)),
    )

    _run(steps)
    assert results.read_text(encoding="utf-8").strip() == "FROM-INIT"


def test_step_adapter_bare_passthrough_falls_back_to_wrapped_arity(tmp_path):
    # A generic decorator with NO explicit params of its own (bare
    # *args/**kwargs) must still read the wrapped step's real arity.
    results = tmp_path / "results.txt"

    def generic_decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    def adapter(entrypoint):
        return generic_decorator(entrypoint)

    register(step_adapter=adapter)
    steps = tmp_path / "steps"
    _write_init(steps, "def init(cmd, logger):\n    return 'CTX'\n")
    _write_step(
        steps,
        "10-one.py",
        """\
        def main(cmd, ctx):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(ctx + "\\n")
        """.format(results=str(results)),
    )

    _run(steps)
    assert results.read_text(encoding="utf-8").strip() == "CTX"


# --------------------------------------------------------------------------
# ctx is only ever supplied when a __main__.py actually exists.
# --------------------------------------------------------------------------


def test_no_lifecycle_defaulted_second_param_keeps_its_own_default(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-one.py",
        """\
        def main(cmd, dry_run=False):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("dry_run=%s\\n" % dry_run)
        """.format(results=str(results)),
    )

    _run(steps)
    assert results.read_text(encoding="utf-8").strip() == "dry_run=False"


def test_with_lifecycle_defaulted_second_param_receives_ctx(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_init(steps, "def init(cmd, logger):\n    return {'conn': 1}\n")
    _write_step(
        steps,
        "10-one.py",
        """\
        def main(cmd, dry_run=False):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write("dry_run=%s\\n" % dry_run)
        """.format(results=str(results)),
    )

    _run(steps)
    assert results.read_text(encoding="utf-8").strip() == "dry_run={'conn': 1}"


# --------------------------------------------------------------------------
# __main__.py is imported before any step (its module-level setup, e.g. a
# sys.path mutation, is already in effect for step imports).
# --------------------------------------------------------------------------


def test_lifecycle_module_setup_is_visible_to_step_imports(tmp_path):
    register()
    steps = tmp_path / "steps"
    helper_dir = tmp_path / "lib"
    helper_dir.mkdir()
    (helper_dir / "shared_helper_xyz.py").write_text("VALUE = 42\n")
    results = tmp_path / "results.txt"
    _write_init(
        steps,
        f"""\
        import sys
        sys.path.insert(0, r"{helper_dir}")
        def init(cmd, logger):
            return None
        """,
    )
    _write_step(
        steps,
        "10-use.py",
        """\
        import shared_helper_xyz
        def main(cmd):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(str(shared_helper_xyz.VALUE) + "\\n")
        """.format(results=str(results)),
    )

    _run(steps)
    assert results.read_text(encoding="utf-8").strip() == "42"


# --------------------------------------------------------------------------
# Duplicate step names are detected, not silently accepted.
# --------------------------------------------------------------------------


def test_duplicate_step_name_warns_resilient(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-setup.py",
        _record_step("setup10", results, extra='REQUIRED = ["x"]'),
    )
    _write_step(steps, "50-x.py", _record_step("x", results))
    _write_step(steps, "90-setup.py", _record_step("setup90", results))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    names = [s for s in ran]
    # Only the FIRST "setup" file is kept in the ordering graph; the second
    # is skipped with a warning naming both files, so REQUIRED is honored.
    assert "setup10" in names
    assert "x" in names
    assert "setup90" not in names
    assert any("duplicate step name" in rec.message for rec in caplog.records)


def test_duplicate_step_name_errors_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "10-setup.py", "def main(cmd): pass\n")
    _write_step(steps, "90-setup.py", "def main(cmd): pass\n")

    with pytest.raises(ValueError, match="duplicate step name"):
        _run(steps, rcopts=["strict"])


# --------------------------------------------------------------------------
# Step metadata (REQUIRED/BEFORE/AFTER as a bare string, a non-integer
# PRIORITY) is normalized/validated instead of silently misbehaving.
# --------------------------------------------------------------------------


def test_required_as_bare_string_is_normalized_to_one_name(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "10-deploy.py",
        _record_step("deploy", results, extra='REQUIRED = "provision"'),
    )

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    # A single missing dep named "provision" -- not iterated character by
    # character into "p", "r", "o", ...
    assert ran == ["deploy"]
    messages = " ".join(rec.message for rec in caplog.records)
    assert "provision" in messages
    assert "'p'" not in messages


def test_after_as_bare_string_is_normalized_not_a_silent_noop(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(
        steps,
        "90-early.py",
        _record_step("early", results, extra='AFTER = "provision"'),
    )
    _write_step(steps, "10-provision.py", _record_step("provision", results))

    ran, _ = _run(steps)
    assert ran.index("provision") < ran.index("early")


def test_invalid_priority_warns_and_falls_back_to_filename_prefix(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "20-a.py", _record_step("a", results, extra="PRIORITY = 'high'"))
    _write_step(steps, "10-b.py", _record_step("b", results))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    # `a`'s bad PRIORITY falls back to its filename prefix (20), so `b` (10)
    # still sorts first.
    assert ran == ["b", "a"]
    assert any("PRIORITY" in rec.message for rec in caplog.records)


def test_invalid_priority_errors_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "20-a.py", "PRIORITY = 'high'\ndef main(cmd): pass\n")

    with pytest.raises(ValueError, match="PRIORITY"):
        _run(steps, rcopts=["strict"])


# --------------------------------------------------------------------------
# A dependency-cycle break names only the steps still stuck, not unrelated
# downstream steps that merely required a cycle member.
# --------------------------------------------------------------------------


def test_cycle_break_does_not_name_an_unrelated_downstream_step(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-x.py", _record_step("x", results, extra='REQUIRED = ["y"]'))
    _write_step(steps, "20-y.py", _record_step("y", results, extra='REQUIRED = ["x"]'))
    _write_step(steps, "30-z.py", _record_step("z", results, extra='REQUIRED = ["x"]'))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    assert set(ran) == {"x", "y", "z"}
    cycle_warnings = [
        rec.message for rec in caplog.records if "dependency cycle" in rec.message
    ]
    assert cycle_warnings
    assert "z" not in cycle_warnings[0]


# --------------------------------------------------------------------------
# Async steps/hooks are rejected loudly instead of silently never running.
# --------------------------------------------------------------------------


def test_async_step_raises_type_error_instead_of_silently_not_running(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_step(steps, "10-a.py", "async def main(cmd):\n    pass\n")

    with pytest.raises(TypeError, match="coroutine"):
        _run(steps)


def test_async_init_hook_raises_type_error(tmp_path):
    register()
    steps = tmp_path / "steps"
    _write_init(steps, "async def init(cmd, logger):\n    return None\n")
    _write_step(steps, "10-a.py", "def main(cmd): pass\n")

    with pytest.raises(TypeError, match="coroutine"):
        _run(steps)


# --------------------------------------------------------------------------
# Filename/pattern grammar edge cases (D062).
# --------------------------------------------------------------------------


def test_is_runpath_dir_does_not_crash_on_a_non_decimal_digit_filename(tmp_path):
    steps = tmp_path / "steps"
    steps.mkdir()
    # U+00B2 SUPERSCRIPT TWO: `.isdigit()` is True but `int()` raises; must
    # not crash `is_runpath_dir` -- the file is simply not a step.
    (steps / "²-odd.py").write_text("def main(cmd): pass\n")
    (steps / "10-real.py").write_text("def main(cmd): pass\n")
    assert is_runpath_dir(steps) is True


def test_uppercase_py_suffix_is_never_treated_as_a_step(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.PY", _record_step("a", results))
    _write_step(steps, "20-b.py", _record_step("b", results))

    ran, _ = _run(steps)
    # `10-a.PY` is never picked up as a step (case-sensitive suffix match),
    # consistently across platforms.
    assert ran == ["b"]
