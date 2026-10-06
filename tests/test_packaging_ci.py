"""Tests for packaging metadata and CI workflow configuration.

These check the *configuration* (pyproject.toml, .gitignore, the workflow
YAML files) rather than actual GitHub Actions runs -- nothing here executes
a workflow. Workflow files are read as plain text/regex rather than parsed
as YAML: PyYAML is not a project dependency and this suite must not gain one
just to assert on a few keys.
"""

from __future__ import annotations

import tarfile
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


def test_pyproject_does_not_pin_wheel_packages():
    text = _read(_PYPROJECT)
    assert (
        'packages = ["src/duho"]' not in text
    ), "hatchling auto-detects the src/ layout; an explicit packages list can break editable builds"


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


def test_release_workflow_does_not_deploy_pages():
    text = _read(_WORKFLOWS / "release.yml")
    assert "deploy-pages" not in text
    assert "docs-deploy" not in text


def test_docs_workflow_owns_every_pages_trigger():
    text = _read(_WORKFLOWS / "docs.yml")
    assert "types: [published]" in text
    assert "src/**" in text
    assert "CHANGELOG.md" in text
    assert "workflow_dispatch" in text
    assert "group: pages" in text


def test_docs_workflow_self_enables_pages():
    text = _read(_WORKFLOWS / "docs.yml")
    assert "enablement: true" in text
    assert "pages: write" in text
