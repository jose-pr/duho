"""Real subprocess end-to-end tests (Plan 03 T2).

Nothing else in the suite runs a duho CLI as an actual child process, so the
production entry path -- ``sys.argv[1:]`` parsing, ``SystemExit`` propagation to
the shell exit code, the real ``--`` passthrough split, ``--version`` -- was
unobserved. These tests spawn ``sys.executable`` with ``PYTHONPATH=src`` so the
CLI runs exactly as an installed console script would.

Fixture CLIs that need AST-derived flags/docstrings are written as REAL ``.py``
files (never ``python -c``), matching the project's AST/-c limitation.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO_ROOT / "examples"


def _run(args, *, cwd=None, extra_path=None):
    """Run a duho CLI as a child process with src on PYTHONPATH."""
    src = str(_REPO_ROOT / "src")
    pythonpath = src if extra_path is None else os.pathsep.join([extra_path, src])
    env = {**os.environ, "PYTHONPATH": pythonpath}
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------
# examples/dotagents.py -- a real shipped multi-command CLI
# --------------------------------------------------------------------------


def test_install_dry_run_exits_zero_and_logs():
    result = _run([str(_EXAMPLES / "dotagents.py"), "install", "--dry-run"])
    assert result.returncode == 0, result.stderr
    # LoggingArgs logs to stderr; the dry-run path emits its two info lines.
    assert "would install payload into" in result.stderr
    assert "dry-run: no files will be written" in result.stderr


def test_help_exits_zero_and_lists_subcommands():
    result = _run([str(_EXAMPLES / "dotagents.py"), "--help"])
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "install" in result.stdout  # the subcommand list


def test_bad_args_exit_2_with_argparse_error_on_stderr():
    result = _run([str(_EXAMPLES / "dotagents.py"), "install", "--nope"])
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "--nope" in result.stderr


def test_version_exits_zero_and_prints_prog_and_version():
    result = _run([str(_EXAMPLES / "dotagents.py"), "--version"])
    assert result.returncode == 0, result.stderr
    import duho

    combined = result.stdout + result.stderr
    assert "Dotagents" in combined
    assert duho.__version__ in combined


# --------------------------------------------------------------------------
# Passthrough: the real sys.argv + `--` split interaction
# --------------------------------------------------------------------------

_PASSTHROUGH_CLI = '''\
"""A tiny CLI that prints its passthrough argv, one token per line."""
import sys

import duho
from duho import Cmd


class Root(Cmd):
    """Print passthrough argv."""

    def __call__(self) -> int:
        for token in self._passthrough_:
            print(token)
        return 0


if __name__ == "__main__":
    sys.exit(duho.main(Root))
'''


def test_passthrough_after_double_dash_reaches_command(tmp_path):
    """`prog -- -k foo --weird` -> exactly ['-k', 'foo', '--weird'] via real argv."""
    cli = tmp_path / "pass_cli.py"
    cli.write_text(_PASSTHROUGH_CLI)
    result = _run([str(cli), "--", "-k", "foo", "--weird"])
    assert result.returncode == 0, result.stderr
    tokens = result.stdout.splitlines()
    assert tokens == ["-k", "foo", "--weird"]


_VERSION_CLI = '''\
"""A CLI carrying a _version_ on the root."""
import sys

import duho
from duho import Cmd


class Root(Cmd):
    """Root with a version."""

    _version_ = "9.9.9"

    def __call__(self) -> int:
        return 0


if __name__ == "__main__":
    sys.exit(duho.main(Root))
'''


def test_version_on_version_carrying_root(tmp_path):
    cli = tmp_path / "ver_cli.py"
    cli.write_text(_VERSION_CLI)
    result = _run([str(cli), "--version"])
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "Root" in combined
    assert "9.9.9" in combined
