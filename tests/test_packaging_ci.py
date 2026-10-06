"""Tests for packaging metadata and CI workflow configuration.

These check the *configuration* (pyproject.toml, .gitignore, the workflow
YAML files) rather than actual GitHub Actions runs -- nothing here executes
a workflow. Workflow files are read as plain text/regex rather than parsed
as YAML: PyYAML is not a project dependency and this suite must not gain one
just to assert on a few keys.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_PYPROJECT = _ROOT / "pyproject.toml"
_GITIGNORE = _ROOT / ".gitignore"
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _read(path: Path) -> str:
    if not path.is_file():
        pytest.skip(f"{path} not present (running against an installed package)")
    return path.read_text(encoding="utf-8")


def _step_script(text: str, step_name: str) -> str:
    """The ``run: |`` body of the workflow step named ``step_name``, dedented."""
    lines = text.splitlines()
    start = next(
        i for i, ln in enumerate(lines) if ln.strip() == f"- name: {step_name}"
    )
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body = []
    for ln in lines[run + 1 :]:
        if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
            break
        body.append(ln)
    return textwrap.dedent("\n".join(body)) + "\n"


# -- *.local.* neither gitignored nor excluded from sdist/wheel --------------


def test_gitignore_covers_every_local_file_not_just_md():
    text = _read(_GITIGNORE)
    assert "*.local.*" in text
    assert (
        "*.local.md" not in text
    ), "narrowed pattern should be replaced, not kept alongside"


def test_pyproject_sdist_excludes_local_files():
    text = _read(_PYPROJECT)
    assert '"*.local.*"' in text


# hatchling drops every .gitignore pattern when the checkout's own path matches
# one (a parent directory named `build`, `dist`, `site`, `.claude` or `.agents`),
# so the private files must be excluded by the build targets themselves.
@pytest.mark.parametrize("parent", ["plain", "build"])
def test_local_files_excluded_from_built_wheel_and_sdist(tmp_path, parent):
    """Plant non-.md `.local` files and a CLAUDE.md in a real copy of the
    project, build both artifacts, and confirm neither ships them -- while
    README.md/AGENTS.md, which must ship, still do.
    """
    hatchling_build = pytest.importorskip("hatchling.build")

    if not (_ROOT / "src" / "duho").is_dir():
        pytest.skip("no local checkout to copy (running against an installed package)")

    import shutil

    copy_root = tmp_path / parent / "duho"
    shutil.copytree(
        _ROOT,
        copy_root,
        ignore=shutil.ignore_patterns(".git", "dist", "build", "*.egg-info"),
    )
    (copy_root / "config.local.toml").write_text("x = 1\n", encoding="utf-8")
    (copy_root / "src" / "duho" / "settings.local.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (copy_root / "src" / "duho" / "AGENTS.local.md").write_text(
        "# notes\n", encoding="utf-8"
    )

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    cwd = Path.cwd()
    try:
        import os

        os.chdir(copy_root)
        wheel_name = hatchling_build.build_wheel(str(dist_dir))
        sdist_name = hatchling_build.build_sdist(str(dist_dir))
    finally:
        os.chdir(cwd)

    with zipfile.ZipFile(dist_dir / wheel_name) as wheel:
        wheel_names = wheel.namelist()
    with tarfile.open(dist_dir / sdist_name) as sdist:
        sdist_names = sdist.getnames()

    assert not [n for n in wheel_names if ".local." in n], wheel_names
    assert not [n for n in sdist_names if ".local." in n], sdist_names
    assert not [n for n in wheel_names if "CLAUDE" in n], wheel_names
    assert not [n for n in sdist_names if "CLAUDE" in n], sdist_names
    assert any(n.endswith("duho/AGENTS.md") for n in wheel_names)
    assert any(n.endswith("src/duho/AGENTS.md") for n in sdist_names)


# -- explicit wheel `packages` config dropped ---------------------------------


def test_pyproject_wheel_target_names_the_src_package_or_relies_on_detection():
    text = _read(_PYPROJECT)
    if "packages =" in text:
        assert 'packages = ["src/duho"]' in text
    else:
        assert (_ROOT / "src" / "duho" / "__init__.py").is_file()


# -- PEP 639 license fields -----------------------------------------------


def test_pyproject_uses_pep639_license_fields():
    text = _read(_PYPROJECT)
    assert 'license = "MIT"' in text
    assert 'license-files = ["LICENSE"]' in text
    assert (
        "License :: OSI Approved :: MIT License" not in text
    ), "the legacy classifier must not sit alongside the PEP 639 fields"


# -- stale pyproject comment about a root AGENTS.md ---------------------------


def test_pyproject_does_not_claim_a_root_agents_md_exists():
    text = _read(_PYPROJECT)
    assert "There is no committed repo-root AGENTS.md" in text


# -- .gitignore root category rule + missing entries ---------------------------


def test_gitignore_has_root_category_rule_and_reincludes():
    text = _read(_GITIGNORE)
    assert "/.*" in text
    assert "!/.gitignore" in text
    assert "!/.gitattributes" in text
    assert "!/.github/" in text


def test_gitignore_has_pyvenv_and_ruff_cache():
    text = _read(_GITIGNORE)
    assert ".pyvenv/" in text
    assert ".ruff_cache/" in text


def test_gitignore_claude_entry_is_slashless():
    text = _read(_GITIGNORE)
    assert "\n.claude\n" in text
    assert (
        ".claude/" not in text
    ), "a trailing slash would not match the .claude symlink"


# -- Python 3.14 present in CI matrices and classifiers -----------------------


def test_pyproject_classifies_python_3_14():
    text = _read(_PYPROJECT)
    assert '"Programming Language :: Python :: 3.14"' in text


def test_test_workflow_runs_python_3_14():
    text = _read(_WORKFLOWS / "test.yml")
    assert 'python-version: "3.14"' in text


def test_release_workflow_gate_covers_python_3_14():
    # release.yml no longer keeps its own matrix -- it reuses test.yml
    # -- so the 3.14 gate is exercised by asserting that reuse, not a second
    # copy of the version list.
    text = _read(_WORKFLOWS / "release.yml")
    assert "uses: ./.github/workflows/test.yml" in text


# -- Q5: black --check runs in CI ---------------------------------------------


def test_test_workflow_checks_formatting_with_black():
    text = _read(_WORKFLOWS / "test.yml")
    assert "black --check src tests examples benchmarks" in text


# -- publish-pypi is re-run-safe -----------------------------------------------


def test_release_workflow_publish_is_skip_existing():
    text = _read(_WORKFLOWS / "release.yml")
    assert "skip-existing: true" in text


# -- GitHub release objects pinned to the tagged commit ------------------------


def test_release_workflow_pins_target_commitish():
    text = _read(_WORKFLOWS / "release.yml")
    assert "target_commitish: ${{ github.sha }}" in text


# -- the release gate actually exercises test.yml's own checks ------------------


def test_release_workflow_reuses_test_workflow_via_workflow_call():
    release_text = _read(_WORKFLOWS / "release.yml")
    test_text = _read(_WORKFLOWS / "test.yml")
    assert "uses: ./.github/workflows/test.yml" in release_text
    assert "workflow_call" in test_text


# -- the built wheel is installed and inspected, not just built -----------------


def test_release_workflow_verifies_the_installed_wheel():
    text = _read(_WORKFLOWS / "release.yml")
    assert "verify-wheel" in text
    assert "py.typed" in text
    assert "README.md" in text
    assert "AGENTS.md" in text


# -- docs deploy ownership -- docs.yml deploys, release.yml only gates --------


def test_release_workflow_gates_on_docs_but_dispatches_the_deploy():
    text = _read(_WORKFLOWS / "release.yml")
    assert "deploy-pages" not in text
    assert 'gh workflow run docs.yml --repo "$GITHUB_REPOSITORY"' in text
    assert "actions: write" in text


def test_docs_workflow_owns_every_pages_trigger():
    text = _read(_WORKFLOWS / "docs.yml")
    assert "types: [published]" not in text
    assert "\n  release:" not in text
    assert "src/**" in text
    assert "CHANGELOG.md" in text
    assert "workflow_dispatch" in text
    assert "group: pages" in text


def test_docs_workflow_self_enables_pages():
    text = _read(_WORKFLOWS / "docs.yml")
    assert "enablement: true" in text
    assert "pages: write" in text


# -- the tag must name the version that was built -----------------------------


def _run_tag_check(tmp_path, tag, filenames):
    pytest.importorskip("packaging")
    script = _step_script(
        _read(_WORKFLOWS / "release.yml"), "The tag names the version that was built"
    )
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in filenames:
        (dist / name).write_text("", encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "TAG": tag},
        capture_output=True,
        text=True,
    )


_BUILT = ["duho-0.6.5-py3-none-any.whl", "duho-0.6.5.tar.gz"]


@pytest.mark.parametrize("tag", ["v0.6.5", "0.6.5"])
def test_release_build_accepts_a_tag_naming_the_built_version(tmp_path, tag):
    done = _run_tag_check(tmp_path, tag, _BUILT)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("tag", ["v0.6.6", "v0.6.5rc1"])
def test_release_build_rejects_a_tag_naming_another_version(tmp_path, tag):
    done = _run_tag_check(tmp_path, tag, _BUILT)
    assert done.returncode != 0
    assert "0.6.5" in done.stderr


def test_release_build_rejects_artifacts_of_two_versions(tmp_path):
    done = _run_tag_check(
        tmp_path, "v0.6.5", ["duho-0.6.5-py3-none-any.whl", "duho-0.6.4.tar.gz"]
    )
    assert done.returncode != 0


# -- release.yml hardening ----------------------------------------------------


def _run_scripts(text: str) -> "list[str]":
    """Every ``run:`` value in a workflow, inline or block."""
    lines = text.splitlines()
    scripts = []
    for i, line in enumerate(lines):
        stripped = line.strip().removeprefix("- ")
        if not stripped.startswith("run:"):
            continue
        inline = stripped[len("run:") :].strip()
        if inline != "|":
            scripts.append(inline)
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for ln in lines[i + 1 :]:
            if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
                break
            body.append(ln)
        scripts.append("\n".join(body))
    return scripts


def test_release_workflow_never_splices_github_context_into_a_script():
    for workflow in _WORKFLOWS.glob("*.yml"):
        for script in _run_scripts(_read(workflow)):
            assert "${{ github." not in script, (workflow.name, script)


def test_release_workflow_pins_the_write_scoped_release_action_by_commit():
    text = _read(_WORKFLOWS / "release.yml")
    assert re.search(r"uses: softprops/action-gh-release@[0-9a-f]{40}\b", text)


def test_release_workflow_runs_twine_check_on_the_built_files():
    assert "twine check dist/*" in _read(_WORKFLOWS / "release.yml")


def test_release_workflow_publishes_to_pypi_only_for_final_tags():
    text = _read(_WORKFLOWS / "release.yml")
    job = text[text.index("  publish-pypi:") :]
    job = job[: job.index("    steps:")]
    assert "if: ${{ !contains(github.ref_name, '-') }}" in job
