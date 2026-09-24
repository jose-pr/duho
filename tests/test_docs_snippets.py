"""Executes README/docs code blocks marked runnable, so drift is caught.

Nothing else in the suite runs the fenced python blocks scattered across
README.md and docs/guide/*.md -- a behavior change can update the README and
miss a guide, or vice versa, and nothing notices until a user's copy-pasted
example breaks. A fenced block is collected here only when the line
immediately before its opening ``` fence is the literal comment
``<!-- runnable -->``: most blocks are illustrative fragments (a partial
class, one field in isolation) that don't stand alone as a runnable script,
so opting a whole block in is a deliberate choice for whoever writes the
doc, not something this harness assumes on its own.

Each runnable block is written to a REAL ``.py`` file under ``tmp_path``
(never executed via ``exec()`` or ``python -c`` -- duho's own flag/docstring
introspection needs retrievable source, the same AST/-c limitation the rest
of the suite works around) and run as a real subprocess with the working
tree's ``src`` on PYTHONPATH, asserting a clean exit.

As of this harness landing, no block in README.md/docs/**/*.md carries the
``<!-- runnable -->`` marker yet -- marking the ones that are genuinely
self-contained is separate follow-up work, tracked outside this file. Until
then the parametrized test below collects zero cases (a no-op, not a
failure); ``test_harness_extracts_and_runs_a_marked_block`` below proves the
extraction/execution mechanism itself works against a synthetic fixture, so
the harness has coverage independent of whether anything is marked yet.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import subprocess_env

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FENCE = re.compile(r"```python\n(.*?)```", re.DOTALL)
_MARKER = "<!-- runnable -->"


def _runnable_blocks(markdown_path: "Path") -> "list[tuple[int, str]]":
    """Return ``(line_number, source)`` for every python fence marked runnable.

    The marker must be the nearest non-blank line before the opening fence.
    """
    text = markdown_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks = []
    for match in _FENCE.finditer(text):
        start_line = text.count("\n", 0, match.start())
        preceding = ""
        for i in range(start_line - 1, -1, -1):
            if lines[i].strip():
                preceding = lines[i].strip()
                break
        if preceding == _MARKER:
            blocks.append((start_line + 1, match.group(1)))
    return blocks


def _doc_files() -> "list[Path]":
    files = [_REPO_ROOT / "README.md"]
    docs_dir = _REPO_ROOT / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.rglob("*.md")))
    return [f for f in files if f.is_file()]


def _collect_cases():
    cases = []
    for doc in _doc_files():
        for line, source in _runnable_blocks(doc):
            rel = doc.relative_to(_REPO_ROOT)
            cases.append(pytest.param(doc, line, source, id=f"{rel}:{line}"))
    return cases


def _run_snippet(tmp_path, source: str) -> "subprocess.CompletedProcess":
    script = tmp_path / "snippet.py"
    script.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env=subprocess_env(),
        timeout=30,
    )


@pytest.mark.parametrize("doc_path, line, source", _collect_cases())
def test_runnable_doc_block_executes_cleanly(doc_path, line, source, tmp_path):
    proc = _run_snippet(tmp_path, source)
    assert proc.returncode == 0, proc.stderr


def test_harness_extracts_and_runs_a_marked_block(tmp_path):
    """Prove the extraction/execution mechanism itself works, independent of
    whatever README/docs currently mark runnable (possibly nothing)."""
    doc = tmp_path / "sample.md"
    doc.write_text(
        "Some text.\n\n" f"{_MARKER}\n" "```python\n" "print(1 + 1)\n" "```\n",
        encoding="utf-8",
    )
    blocks = _runnable_blocks(doc)
    assert len(blocks) == 1
    _, source = blocks[0]
    proc = _run_snippet(tmp_path, source)
    assert proc.returncode == 0, proc.stderr


def test_harness_ignores_an_unmarked_block(tmp_path):
    doc = tmp_path / "sample.md"
    doc.write_text(
        "```python\nraise RuntimeError('must not run')\n```\n",
        encoding="utf-8",
    )
    assert _runnable_blocks(doc) == []


def test_harness_requires_the_marker_immediately_before_the_fence(tmp_path):
    doc = tmp_path / "sample.md"
    doc.write_text(
        f"{_MARKER}\n\nSome unrelated text in between.\n\n"
        "```python\nprint('not adjacent')\n```\n",
        encoding="utf-8",
    )
    assert _runnable_blocks(doc) == []
