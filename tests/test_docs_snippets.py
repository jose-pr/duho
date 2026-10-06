"""Executes README/docs code blocks marked runnable, so drift is caught.

A fenced python block is collected only when the nearest non-blank line before
its opening fence is a marker comment. Most blocks are illustrative fragments
(a partial class, one field in isolation) that do not stand alone as a script,
so opting a block in is a deliberate choice for whoever writes the doc.

``<!-- runnable -->``
    The block is a complete program: it is run bare and must exit 0.

``<!-- runnable: commands -->``
    The block is a complete program that needs arguments: it is not run bare,
    only through its command lines (below).

Command lines: the first fenced block after a marked block is its shell
block when its language is ``bash``/``console``/``sh``/``shell``. Each line of
it that reads ``python SCRIPT.py ARGS`` (an optional leading ``$ `` is
dropped) is run against the marked block, with ``SCRIPT.py`` standing for it,
and must exit 0. A line that documents a failure carries the comment
``# error`` and must exit non-zero instead. A line with shell plumbing
(``>``, ``|``, ``&&``, ``;``) is not run.

Each block is written to a real ``.py`` file under ``tmp_path`` (never run via
``exec()`` or ``python -c``: duho's flag/docstring introspection needs
retrievable source) and run as a real subprocess with the working tree's
``src`` on PYTHONPATH, from ``tmp_path``.

``test_marked_blocks_are_counted`` reports how many of the fenced python
blocks are marked (visible with ``pytest -rP``), and fails if that falls below
the floor, so a marker cannot be dropped unnoticed.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import subprocess_env

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FENCE = re.compile(r"^```(\w*)[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_MARKER = "<!-- runnable -->"
_MARKER_COMMANDS = "<!-- runnable: commands -->"
_SHELL_LANGUAGES = frozenset({"bash", "console", "sh", "shell"})
_COMMAND_LINE = re.compile(r"^(?:\$\s+)?python3?\s+(\S+\.py)(\s.*)?$")
_PLUMBING = frozenset({">", ">>", "<", "|", "||", "&", "&&", ";"})
_ERROR_COMMENT = re.compile(r"\s#\s*error\b")

#: Fewer marked blocks than this means markers were dropped.
_MARKED_FLOOR = 51


def _marker_before(lines: "list[str]", fence_line: int) -> str:
    """The nearest non-blank line above the 0-based ``fence_line``."""
    for i in range(fence_line - 1, -1, -1):
        if lines[i].strip():
            return lines[i].strip()
    return ""


def _command_lines(shell_block: str) -> "list[tuple[str, list[str], bool]]":
    """``(line, argv, expects_failure)`` for each runnable line of a shell block."""
    found = []
    for raw in shell_block.splitlines():
        match = _COMMAND_LINE.match(raw.strip())
        if match is None:
            continue
        try:
            argv = shlex.split(match.group(2) or "", comments=True)
        except ValueError:
            continue
        if any(token in _PLUMBING for token in argv):
            continue
        found.append((raw.strip(), argv, bool(_ERROR_COMMENT.search(raw))))
    return found


def _marked_blocks(markdown_path: Path) -> "list[dict]":
    """Every marked python fence: its line, source, mode and command lines."""
    text = markdown_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    fences = list(_FENCE.finditer(text))
    blocks = []
    for index, match in enumerate(fences):
        if match.group(1) != "python":
            continue
        start_line = text.count("\n", 0, match.start())
        marker = _marker_before(lines, start_line)
        if marker not in (_MARKER, _MARKER_COMMANDS):
            continue
        commands = []
        if index + 1 < len(fences) and fences[index + 1].group(1) in _SHELL_LANGUAGES:
            commands = _command_lines(fences[index + 1].group(2))
        blocks.append(
            {
                "line": start_line + 1,
                "source": match.group(2),
                "bare": marker == _MARKER,
                "commands": commands,
            }
        )
    return blocks


def _runnable_blocks(markdown_path: "Path") -> "list[tuple[int, str]]":
    """Return ``(line_number, source)`` for every python fence marked runnable."""
    return [(b["line"], b["source"]) for b in _marked_blocks(markdown_path)]


def _doc_files() -> "list[Path]":
    files = [_REPO_ROOT / "README.md"]
    docs_dir = _REPO_ROOT / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.rglob("*.md")))
    return [f for f in files if f.is_file()]


def _collect_cases():
    bare, commands = [], []
    for doc in _doc_files():
        rel = doc.relative_to(_REPO_ROOT).as_posix()
        for block in _marked_blocks(doc):
            if block["bare"]:
                bare.append(pytest.param(block["source"], id=f"{rel}:{block['line']}"))
            for line, argv, fails in block["commands"]:
                commands.append(
                    pytest.param(
                        block["source"],
                        argv,
                        fails,
                        id=f"{rel}:{block['line']}: {line}",
                    )
                )
    return bare, commands


_BARE_CASES, _COMMAND_CASES = _collect_cases()


def _run_snippet(tmp_path, source: str, argv=()) -> "subprocess.CompletedProcess":
    script = tmp_path / "snippet.py"
    script.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), *argv],
        capture_output=True,
        text=True,
        env=subprocess_env(),
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )


@pytest.mark.parametrize("source", _BARE_CASES)
def test_runnable_doc_block_executes_cleanly(source, tmp_path):
    proc = _run_snippet(tmp_path, source)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("source, argv, expects_failure", _COMMAND_CASES)
def test_documented_command_line_behaves_as_written(
    source, argv, expects_failure, tmp_path
):
    proc = _run_snippet(tmp_path, source, argv)
    if expects_failure:
        assert proc.returncode != 0, "documented as an error but exited 0"
    else:
        assert proc.returncode == 0, proc.stderr


def test_marked_blocks_are_counted(record_property):
    total = sum(
        len(re.findall(r"^```python\s*$", doc.read_text(encoding="utf-8"), re.M))
        for doc in _doc_files()
    )
    marked = sum(len(_marked_blocks(doc)) for doc in _doc_files())
    record_property("marked_python_blocks", f"{marked} of {total}")
    print(f"marked python blocks: {marked} of {total}")
    assert marked >= _MARKED_FLOOR, f"{marked} of {total} marked"


def test_harness_extracts_and_runs_a_marked_block(tmp_path):
    """Prove the extraction/execution mechanism itself works, independent of
    whatever README/docs currently mark runnable."""
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


def test_harness_runs_shell_lines_against_the_block(tmp_path):
    doc = tmp_path / "sample.md"
    doc.write_text(
        f"{_MARKER_COMMANDS}\n"
        "```python\n"
        "import sys\n"
        "sys.exit(int(sys.argv[1]))\n"
        "```\n\n"
        "Then:\n\n"
        "```bash\n"
        "$ python app.py 0\n"
        "python app.py 3   # error: three is not zero\n"
        "python app.py 0 > out.txt\n"
        "pip install duho\n"
        "```\n",
        encoding="utf-8",
    )
    (block,) = _marked_blocks(doc)
    assert block["bare"] is False
    assert [(argv, fails) for _, argv, fails in block["commands"]] == [
        (["0"], False),
        (["3"], True),
    ]
    for _, argv, fails in block["commands"]:
        proc = _run_snippet(tmp_path, block["source"], argv)
        assert (proc.returncode != 0) is fails
