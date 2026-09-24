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
    ["3.9", "3.13"]; baseline.json previously also carried unused 3.10-3.12
    entries (measured locally, never compared against) and no 3.9 entry."""
    data = json.loads((_HERE / "baseline.json").read_text())
    assert set(data) <= {"3.9", "3.13"}


def test_baseline_has_no_stale_e2e_delta():
    """The committed e2e_delta values pre-date the e2e-measurement fix (they were
    measured via the no-op `-c` path) and would read as a false regression
    against the now-correct, file-based measurement -- dropped until
    regenerated from an actual CI run."""
    data = json.loads((_HERE / "baseline.json").read_text())
    for entry in data.values():
        assert "e2e_delta" not in entry.get("startup", {})
