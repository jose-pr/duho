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


# --- class-attribute table -------------------------------------------------

#: Sandwich-named attributes duho reads through ``getattr`` that ``Cli`` does
#: not declare; the header's attribute table documents each.
_EXTRA_CLASS_ATTRIBUTES = ("_parsername_", "_parseraliases_", "_logger_name_")

#: Names the source scan can match that are methods, instance members or
#: placeholders in a docstring rather than configuration attributes.
_NOT_CONFIGURATION = frozenset(
    {
        "_logger_",
        "_x_",
        "_register_subcmd_",
        "_argbuilder_",
        "_passthrough_",
        "_set_loglevels_",
        "_verbose_loglevel_",
        "_wants_app_shape_",
    }
)

_ATTRIBUTE_NAME = re.compile(r"_[a-z][a-z0-9]*(?:_[a-z0-9]+)*_")
_GETATTR_NAME = re.compile(
    r"""getattr\(\s*[\w.()]+\s*,\s*["'](_[a-z][a-z0-9_]*_)["']"""
)


def _attribute_table_names(text: str) -> "set[str]":
    """First-column names of the header's "Class attributes" table."""
    section = text.split("### Class attributes", 1)[1].split("\n#", 1)[0]
    names = set()
    for line in section.splitlines():
        if line.startswith("|"):
            cell = line.split("|")[1].strip().strip("`")
            if _ATTRIBUTE_NAME.fullmatch(cell):
                names.add(cell)
    return names


def _configuration_attributes() -> "set[str]":
    declared = {
        name
        for name in vars(duho.Cli)
        if _ATTRIBUTE_NAME.fullmatch(name) and not name.startswith("_duho_")
    }
    read = set()
    for path in sorted(_HEADER_PATH.parent.rglob("*.py")):
        read.update(_GETATTR_NAME.findall(path.read_text(encoding="utf-8")))
    read = {name for name in read if not name.startswith("_duho_")}
    return (declared | read | set(_EXTRA_CLASS_ATTRIBUTES)) - _NOT_CONFIGURATION


def test_class_attribute_table_covers_every_attribute_duho_reads():
    """Every ``Cli`` attribute and every name read via ``getattr`` is tabulated."""
    text = _HEADER_PATH.read_text(encoding="utf-8")
    missing = sorted(_configuration_attributes() - _attribute_table_names(text))
    assert not missing, f"class attributes missing from the header table: {missing}"
