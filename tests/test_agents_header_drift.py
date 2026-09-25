"""Guard against `src/duho/AGENTS.md` drifting away from the real public API.

`duho/AGENTS.md` ships inside the installed wheel as a header-file-style
reference: every public export, across `duho`'s top-level `__all__` and every
public submodule's own `__all__`, is supposed to be documented there. This
test does not check the *prose* is accurate (that's a human/review job) -- it
only guards the cheap, mechanical regression: a name silently added to (or
still present in) an `__all__` list but never mentioned anywhere in the header
at all, which is exactly how the header rotted out of sync with the code
before.

The header is located via `duho.__file__` (the installed package directory),
not a hardcoded repo-relative path, so this test also passes against an
installed wheel, not just an editable checkout.
"""

import importlib
import re
from pathlib import Path

import pytest

import duho

_HEADER_PATH = Path(duho.__file__).resolve().parent / "AGENTS.md"

#: Public submodules whose own `__all__` is expected to be fully covered by
#: the shipped header, in addition to `duho.__all__` itself. Not every
#: submodule declares one (e.g. `formatters`'s three names are already
#: reachable off `duho.__all__` too); listing a module here that has no
#: `__all__` is simply a no-op for that module.
_SUBMODULES = (
    "duho.parsers",
    "duho.discovery",
    "duho.env",
    "duho.logging",
    "duho.agenthelp",
    "duho.completion",
    "duho.text",
    "duho.qualname",
    "duho.fanout",
    "duho.runpath",
    "duho.scaffold",
    "duho.mcp",
)


def _mentions(text: str, name: str) -> bool:
    """Whether `name` appears in `text` as a standalone identifier.

    Backticks (the header's own inline-code markup) are stripped first so
    `` `snakecase` `` still matches the bare word `snakecase`; a word-boundary
    regex then avoids a false positive from `name` merely being a substring
    of some unrelated, longer identifier.
    """
    stripped = text.replace("`", "")
    return re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", stripped) is not None


def _all_names(module_name: str) -> "list[str]":
    module = importlib.import_module(module_name)
    return list(getattr(module, "__all__", ()))


def test_header_file_exists():
    """The shipped header must exist next to the installed package."""
    assert _HEADER_PATH.is_file(), f"missing shipped header: {_HEADER_PATH}"


@pytest.mark.parametrize("module_name", ("duho",) + _SUBMODULES)
def test_every_exported_name_is_mentioned_in_header(module_name):
    """Every name in `module_name.__all__` must appear somewhere in the header.

    A name present in `__all__` but absent from the header is undocumented
    public API -- exactly the drift this test exists to catch before it ships
    again.
    """
    names = _all_names(module_name)
    assert names, f"{module_name}.__all__ is empty or missing -- nothing to check"

    text = _HEADER_PATH.read_text(encoding="utf-8")
    missing = [name for name in names if not _mentions(text, name)]
    assert (
        not missing
    ), f"{module_name}.__all__ names missing from {_HEADER_PATH.name}: {missing}"
