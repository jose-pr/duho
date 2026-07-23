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
    what stops provider state from leaking between tests. ``_BASE`` (the class
    every provider-built RunPathCmd subclass ALSO inherits from, set via
    ``register(base=...)``) is module-global the same way -- snapshot/restore it
    too so a test that changes it never leaks into the next.
    """
    saved = list(_discovery._PROVIDERS)
    saved_registered = runpath._REGISTERED
    saved_base = runpath._BASE
    try:
        yield
    finally:
        _discovery._PROVIDERS[:] = saved
        runpath._REGISTERED = saved_registered
        runpath._BASE = saved_base


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
    # Phase 2 changed this default: a plain filename (no `?` suffix) is now
    # strict-by-default for THAT step (restoring the predecessor's hardcoded
    # `RcOptions(strict=True)` base, see the plan's Known Facts), independent
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
    # Phase 2: a plain filename's own strict-by-default now stops the run even
    # when --rcopts never mentions `strict` at all (the new per-step default,
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
# Phase 1: `__main__.py` init/success/finally_ lifecycle
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
        '''\
        def init(cmd, logger):
            return {"greeting": "hi"}
        ''',
    )
    _write_step(
        steps,
        "10-legacy.py",
        _record_step("legacy", results),
    )
    _write_step(
        steps,
        "20-modern.py",
        '''\
        def main(cmd, ctx):
            with open(r"{results}", "a", encoding="utf-8") as fh:
                fh.write(ctx["greeting"] + "\\n")
        '''.format(results=str(results)),
    )

    ran, _ = _run(steps)
    assert ran == ["legacy", "hi"]


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
        '''\
        def init(cmd, logger):
            return "ctx"

        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success:" + ctx + "\\n")

        def finally_(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("finally:" + ctx + "\\n")
        '''.format(calls=str(calls)),
    )
    _write_step(steps, "10-a.py", _record_step("a", results))

    _run(steps)
    lines = calls.read_text(encoding="utf-8").splitlines()
    # finally_ runs immediately after the step loop (a plain try/finally around
    # it); success fires after, once the run is confirmed non-aborted. Each
    # fires exactly once.
    assert lines == ["finally:ctx", "success:ctx"]


def test_init_finally_runs_even_when_a_step_raises_resilient(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    calls = tmp_path / "calls.txt"
    _write_init(
        steps,
        '''\
        def init(cmd, logger):
            return "ctx"

        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success\\n")

        def finally_(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("finally\\n")
        '''.format(calls=str(calls)),
    )
    _write_step(steps, "10-ok.py", _record_step("ok", results))
    _write_step(
        steps,
        "20-boom.py",
        'def main(cmd):\n    raise RuntimeError("boom")\n',
    )

    # boom is strict-by-default (plain filename, Phase 2), so this run raises;
    # explicit !strict makes it resilient again, and success() should NOT run
    # (resilient continue still counts as "not aborted"? no -- see below).
    _run(steps, rcopts=["!strict"])
    lines = calls.read_text(encoding="utf-8").splitlines()
    # finally_ always runs; success only fires on a clean run with no abort --
    # here nothing aborted (resilient continue), so success DOES fire too.
    assert "finally" in lines
    assert "success" in lines


def test_init_finally_runs_when_step_raises_and_aborts_strict(tmp_path):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    calls = tmp_path / "calls.txt"
    _write_init(
        steps,
        '''\
        def init(cmd, logger):
            return "ctx"

        def success(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("success\\n")

        def finally_(ctx, cmd, logger):
            with open(r"{calls}", "a", encoding="utf-8") as fh:
                fh.write("finally\\n")
        '''.format(calls=str(calls)),
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
        '''\
        def init(cmd, logger):
            raise RuntimeError("init boom")
        ''',
    )
    _write_step(steps, "10-a.py", "def main(cmd): pass\n")

    with pytest.raises(RuntimeError, match="init boom"):
        _run(steps)  # no --rcopts strict at all -- init failure is unconditional


# --------------------------------------------------------------------------
# Phase 2: filename-encoded per-step options
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
    assert opts.opts == {"key1": True, "key2": False, "key3": "val"}


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
        pytest.skip("symlink creation not permitted (needs elevated privileges on Windows)")

    register()
    from duho.runpath import _load_steps

    loaded = _load_steps(steps, "steps", strict=False)
    by_name = {s.name: s for s in loaded}
    assert by_name["step"].file_enabled is True
    assert by_name["step2"].file_enabled is False


# --------------------------------------------------------------------------
# Phase 3: BEFORE/AFTER soft ordering
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
    _write_step(steps, "10-a.py", _record_step("a", results, extra='BEFORE = ["ghost"]'))

    with caplog.at_level("WARNING", logger="duho"):
        ran, _ = _run(steps)
    assert ran == ["a"]
    # No warning for the missing BEFORE target (contrast with REQUIRED below).
    assert not any("ghost" in rec.message for rec in caplog.records)


def test_required_missing_name_still_warns_unlike_before_after(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    _write_step(steps, "10-a.py", _record_step("a", results, extra='REQUIRED = ["ghost"]'))

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
    assert not any("disabled" in rec.message.lower() and "b" in rec.message for rec in caplog.records)


def test_mixed_before_required_cycle_broken_deterministically(tmp_path, caplog):
    register()
    steps = tmp_path / "steps"
    results = tmp_path / "results.txt"
    # `x` REQUIREs `y`; `y` declares BEFORE=["x"] is fine (consistent), but here
    # make an actual cycle: `x` REQUIRES `y`, `y` REQUIRES `x` (mixed with a
    # BEFORE edge reinforcing the same cycle) -- must not hang, must emit both.
    _write_step(steps, "10-x.py", _record_step("x", results, extra='REQUIRED = ["y"]\nBEFORE = ["y"]'))
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
