#!/usr/bin/env python3
"""Regression tests for the benchmarks/ harness itself.

Not part of the main `tests/` suite (pytest's `testpaths` is `tests/`) --
run explicitly:

    PYTHONPATH=src python -m pytest benchmarks/test_bench_harness.py -q

Each test is written to fail against the harness code as it stood before the
fix its docstring describes.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# benchmarks/ is not a package; make its modules (and the sibling src/)
# importable the same way the scripts themselves do.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import duho  # noqa: E402
from duho import _introspect  # noqa: E402

import _bench  # noqa: E402
import bench_discovery  # noqa: E402
import bench_startup  # noqa: E402
import check_baseline  # noqa: E402
import compare_cache  # noqa: E402


def _child_env():
    import os

    env = dict(os.environ)
    src = str(_HERE.parent / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")
    return env


# ---------------------------------------------------------------------------
# Cold-build cache dropping must not delete the Args/Cmd/Cli framework seed
# ---------------------------------------------------------------------------


def test_drop_caches_preserves_framework_seed():
    """A fresh process always has Args/Cmd/Cli's pre-seeded
    ``_duho_constants_ = {}``. Pre-fix, ``drop_caches`` deleted it along with
    every other cache attribute reachable from the MRO, so this would raise
    AttributeError."""
    _bench.drop_caches(_bench.ComplexArgs)
    assert duho.Args._duho_constants_ == {}
    assert duho.Cmd._duho_constants_ == {}
    assert duho.Cli._duho_constants_ == {}


def test_drop_caches_never_reintrospects_framework_classes(monkeypatch):
    """Once the seed is dropped, the next build must AST-parse duho's own
    args.py to re-populate it for Args/Cmd/Cli -- work no real invocation ever
    does. Spy on ``getclsdef`` (what triggers that parse) and assert it is
    never called for the three framework classes after a cold drop+rebuild."""
    calls = []
    original = _introspect.getclsdef

    def spy(cls):
        calls.append(cls)
        return original(cls)

    monkeypatch.setattr(_introspect, "getclsdef", spy)
    _bench.drop_caches(_bench.ComplexArgs)
    duho.parser(_bench.ComplexArgs)

    assert duho.Args not in calls
    assert duho.Cmd not in calls
    assert duho.Cli not in calls


def test_compare_cache_drop_also_preserves_seed():
    """compare_cache.py used to keep its own copy of the drop logic with the
    same bug; it now imports _bench.drop_caches directly, so this is really
    the same fix verified through compare_cache's own entry point."""
    compare_cache.drop_caches(compare_cache.ComplexArgs)
    assert duho.Args._duho_constants_ == {}


# ---------------------------------------------------------------------------
# The gated e2e_delta must measure a real per-invocation cost
# ---------------------------------------------------------------------------

_E2E_ALIAS_CODE = (
    "import duho\n"
    "class A(duho.Args):\n"
    "    name: str\n"
    "    ('--name', '-n')\n"
    "duho.parse(A, ['-n', 'x'])\n"
)


def test_c_source_silently_drops_class_body_flags():
    """`-c` source has no `__file__`; duho's class-body flags-tuple scan is
    silently skipped for it, so a `-n` alias declared in the class body is
    never registered and argparse rejects it: an `e2e_delta` timed via `-c`
    never exercises the AST/getsource path it claims to measure."""
    result = subprocess.run(
        [sys.executable, "-c", _E2E_ALIAS_CODE], env=_child_env(), capture_output=True
    )
    assert result.returncode != 0


def test_real_file_source_registers_class_body_flags():
    """The same snippet, run from a real .py file, DOES get its class body
    scanned, so the alias works -- this is what bench_startup.py's fixed
    e2e_build_parse now measures."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "e2e_alias.py"
        path.write_text(_E2E_ALIAS_CODE)
        result = subprocess.run(
            [sys.executable, str(path)], env=_child_env(), capture_output=True
        )
    assert result.returncode == 0, result.stderr.decode()


def test_measure_times_gated_e2e_from_a_real_file(monkeypatch):
    """Unit-level regression: bench_startup.measure() must route the gated
    e2e_build_parse metric through source_ms() (a real .py file), not
    subprocess_ms() (`-c`). Pre-fix, measure() had no source_ms and timed e2e
    with the same `python -c` helper as everything else."""
    source_calls = []
    code_calls = []

    def spy_source_ms(path, n, env):
        source_calls.append(Path(path))
        return (1.0, 1.0, 1.0)

    def spy_subprocess_ms(code, n, env):
        code_calls.append(code)
        return (1.0, 1.0, 1.0)

    monkeypatch.setattr(bench_startup, "source_ms", spy_source_ms)
    monkeypatch.setattr(bench_startup, "subprocess_ms", spy_subprocess_ms)

    result = bench_startup.measure(1)

    assert len(source_calls) == 1
    assert source_calls[0].suffix == ".py"
    # The `-c` path survives only as the separately-named, informational
    # e2e_no_source metric (plus python_pass/import_argparse/import_duho).
    assert code_calls.count(bench_startup.E2E_CODE) == 1
    assert "e2e_no_source" in result["abs"]
    assert "e2e_build_parse" in result["abs"]


# ---------------------------------------------------------------------------
# Harness duplication, single-average leftover, backwards ratio wording
# ---------------------------------------------------------------------------


def test_compare_cache_imports_shared_workloads():
    assert compare_cache.SimpleArgs is _bench.SimpleArgs
    assert compare_cache.ComplexArgs is _bench.ComplexArgs
    assert compare_cache.sample is _bench.sample
    assert compare_cache.drop_caches is _bench.drop_caches


def test_bench_parsing_removed():
    assert not (_HERE / "bench_parsing.py").exists()


def test_compare_cache_ratio_wording_not_backwards():
    src = (_HERE / "compare_cache.py").read_text()
    assert "x faster" in src
    assert "than cold" in src
    assert "x the cold build" not in src


def test_bench_discovery_drops_unused_cls_kwarg():
    src = (_HERE / "bench_discovery.py").read_text()
    assert "cls=" not in src


# ---------------------------------------------------------------------------
# Results tracking/schema, and gate wording and stale baseline entries
# ---------------------------------------------------------------------------


def test_gitignore_no_longer_excludes_results():
    gitignore = (_HERE.parent / ".gitignore").read_text()
    assert "benchmarks/results/" not in gitignore


def test_readme_documents_schema_and_reproduce():
    readme = (_HERE / "README.md").read_text()
    assert "median_ms" in readme
    assert "tracked and committed" in readme
    assert "check_baseline.py" in readme


def test_result_envelope_schema():
    env = _bench.result_envelope(
        "demo", {"m": {"min_ms": 1.0, "median_ms": 2.0, "max_ms": 3.0}}
    )
    assert env["name"] == "demo"
    assert {
        "name",
        "python",
        "python_minor",
        "platform",
        "processor",
        "timestamp",
        "metrics",
    } <= set(env)


def test_save_result_writes_committed_shape(tmp_path):
    out = _bench.save_result(
        tmp_path, "demo", {"m": {"min_ms": 1.0, "median_ms": 2.0, "max_ms": 3.0}}
    )
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["metrics"]["m"]["median_ms"] == 2.0


def test_bench_startup_and_discovery_have_save_flag(capsys):
    import pytest

    for module in (bench_startup, bench_discovery):
        with pytest.raises(SystemExit):
            module.main(["--help"])
        out = capsys.readouterr().out
        assert "--save" in out


def test_startup_wording_no_longer_claims_normalized_runner_speed():
    src = (_HERE / "bench_startup.py").read_text()
    assert "normalizes away runner speed" not in src
    assert "normalizes out runner speed" not in src


def test_check_baseline_wording_no_longer_claims_normalized_runner_speed():
    src = (_HERE / "check_baseline.py").read_text()
    assert "normalizes away runner speed" not in src
    assert "normalizes out runner speed" not in src


def test_baseline_only_covers_ci_matrix_versions():
    """`.github/workflows/test.yml`'s benchmark job matrix is exactly
    ["3.9", "3.13", "3.14"]; baseline.json previously also carried unused
    3.10-3.12 entries (measured locally, never compared against)."""
    data = json.loads((_HERE / "baseline.json").read_text())
    assert set(data) <= {"3.9", "3.13", "3.14"}


def test_baseline_has_no_stale_e2e_delta():
    """The committed e2e_delta values pre-date the e2e-measurement fix (they were
    measured via the no-op `-c` path) and would read as a false regression
    against the now-correct, file-based measurement -- dropped until
    regenerated from an actual CI run."""
    data = json.loads((_HERE / "baseline.json").read_text())
    for entry in data.values():
        assert "e2e_delta" not in entry.get("startup", {})


# ---------------------------------------------------------------------------
# The regression gate must normalise for runner speed, not just raw ratios --
# a uniformly slower (or faster) CI runner must not trip it, while a genuine
# duho-only slowdown still must.
# ---------------------------------------------------------------------------


def _write_fake_baseline(tmp_path, entry):
    """Write a one-version baseline.json keyed under the REAL running
    interpreter's major.minor, so check_baseline.main() picks it up without
    needing to fake sys.version_info."""
    py_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({py_minor: entry}))
    return path


def _fake_measure(delta, python_pass):
    """Build a fake bench_startup.measure() return value with just the two
    fields check_baseline.py actually reads: the gated delta and the
    subprocess calibration reference (abs.python_pass)."""
    return {
        "deltas": {"import_duho_delta": delta},
        "abs": {"python_pass": {"median_ms": python_pass}},
    }


def test_check_baseline_normalises_uniform_runner_slowdown(tmp_path, monkeypatch):
    """A uniformly slower runner -- every timing (both calibration workloads,
    warm metrics, startup delta) scaled by the SAME constant factor -- must
    still pass: this is exactly what the calibration-ratio normalisation
    exists to cancel. Pre-fix (raw ratio, no calibration division) this
    would fail, since every raw ratio equals the factor, well above either
    threshold."""
    baseline_path = _write_fake_baseline(
        tmp_path,
        {
            "calibration_ms": 1.0,
            "calibration_subprocess_ms": 5.0,
            "warm": {"build.simple": 1.0, "build.complex": 2.0},
            "startup": {"import_duho_delta": 10.0},
        },
    )
    monkeypatch.setattr(check_baseline, "BASELINE", baseline_path)

    factor = 1.55  # above both WARM_THRESHOLD and STARTUP_THRESHOLD if raw
    monkeypatch.setattr(
        check_baseline._bench,
        "calibration_metric",
        lambda: {"median_ms": 1.0 * factor},
    )
    monkeypatch.setattr(
        check_baseline._bench,
        "warm_metrics",
        lambda: {
            "build.simple": {"median_ms": 1.0 * factor},
            "build.complex": {"median_ms": 2.0 * factor},
        },
    )
    monkeypatch.setattr(
        check_baseline.bench_startup,
        "measure",
        lambda n: _fake_measure(10.0 * factor, 5.0 * factor),
    )

    assert check_baseline.main([]) == 0


def test_check_baseline_still_catches_a_duho_only_regression(tmp_path, monkeypatch):
    """Both calibration references and every OTHER metric are unchanged
    (steady runner speed); only `build.complex` doubles. A genuine
    duho-specific slowdown must still fail the gate even with calibration
    normalisation active."""
    baseline_path = _write_fake_baseline(
        tmp_path,
        {
            "calibration_ms": 1.0,
            "calibration_subprocess_ms": 5.0,
            "warm": {"build.simple": 1.0, "build.complex": 2.0},
            "startup": {"import_duho_delta": 10.0},
        },
    )
    monkeypatch.setattr(check_baseline, "BASELINE", baseline_path)

    monkeypatch.setattr(
        check_baseline._bench, "calibration_metric", lambda: {"median_ms": 1.0}
    )
    monkeypatch.setattr(
        check_baseline._bench,
        "warm_metrics",
        lambda: {
            "build.simple": {"median_ms": 1.0},
            "build.complex": {"median_ms": 4.0},  # 2x its own baseline
        },
    )
    monkeypatch.setattr(
        check_baseline.bench_startup, "measure", lambda n: _fake_measure(10.0, 5.0)
    )

    assert check_baseline.main([]) == 1


def test_check_baseline_falls_back_to_unnormalised_without_calibration(
    tmp_path, monkeypatch
):
    """A baseline entry predating the calibration-ratio normalisation has
    neither calibration_ms nor calibration_subprocess_ms. The gate must not
    crash (e.g. divide by None/zero) and must fall back to the old,
    unnormalised raw-ratio behavior for both groups."""
    baseline_path = _write_fake_baseline(
        tmp_path,
        {"warm": {"build.simple": 1.0}, "startup": {"import_duho_delta": 10.0}},
    )
    monkeypatch.setattr(check_baseline, "BASELINE", baseline_path)

    monkeypatch.setattr(
        check_baseline._bench, "calibration_metric", lambda: {"median_ms": 1.0}
    )
    monkeypatch.setattr(
        check_baseline._bench,
        "warm_metrics",
        lambda: {"build.simple": {"median_ms": 1.0}},
    )
    monkeypatch.setattr(
        check_baseline.bench_startup, "measure", lambda n: _fake_measure(10.0, 5.0)
    )

    assert check_baseline.main([]) == 0


def test_check_baseline_warm_and_startup_calibrate_independently(tmp_path, monkeypatch):
    """Reproduces the exact failure caught on a real confirming CI run: a
    runner-speed swing moved the in-process calibration workload's ratio
    (0.61x) by a different amount than the subprocess
    python_pass ratio (0.87x) on the SAME run. Under a single shared
    calibration ratio, dividing the startup delta's own harmless raw ratio
    (0.87x, well under the 1.3x threshold) by the unrelated in-process ratio
    produced a false "1.39x REGRESSION". With each group normalised against
    its OWN domain-matched reference, the startup delta's ratio comes out
    close to its own raw ratio (~0.87x / ~0.87x ~= 1.0x) and must pass, even
    though the in-process calibration swung hard enough that it would have
    mis-normalised it."""
    baseline_path = _write_fake_baseline(
        tmp_path,
        {
            "calibration_ms": 1.0,
            "calibration_subprocess_ms": 12.71,
            "warm": {"build.simple": 1.0},
            "startup": {"import_duho_delta": 38.05},
        },
    )
    monkeypatch.setattr(check_baseline, "BASELINE", baseline_path)

    # In-process calibration workload got a lot faster (0.61x), and the warm
    # metric moved by the SAME amount -- a real in-process runner swing, not
    # a regression, correctly cancelled by the warm group's own reference.
    monkeypatch.setattr(
        check_baseline._bench, "calibration_metric", lambda: {"median_ms": 0.61}
    )
    monkeypatch.setattr(
        check_baseline._bench,
        "warm_metrics",
        lambda: {"build.simple": {"median_ms": 1.0 * 0.61}},
    )
    # Subprocess spawn only got modestly faster (0.87x), and the gated delta
    # moved by almost exactly the same amount -- a real duho-independent
    # runner swing, not a regression.
    monkeypatch.setattr(
        check_baseline.bench_startup,
        "measure",
        lambda n: _fake_measure(33.13, 11.06),  # 11.06/12.71 ~= 0.87x
    )

    assert check_baseline.main([]) == 0
