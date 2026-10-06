"""Reading one class's source (3.13+, where a class records ``__firstlineno__``)
and parsing the whole defining file give the same declarations.

Every class is introspected twice, once with the single-class reader as it is
and once with it switched off, and the two ClassDef nodes (positions included)
and the resulting declarations must be equal.
"""

import ast
import importlib.util
import sys

import pytest

import duho
from duho import Args, _introspect

import test_introspect as _existing

_HAS_FIRSTLINENO = sys.version_info >= (3, 13)


@pytest.fixture(autouse=True)
def _every_class_may_use_the_block_reader(monkeypatch):
    """The fixture files are small; with the default budget they would all use
    the whole-file index. One byte per read gives every file ample budget."""
    monkeypatch.setattr(_introspect, "_BYTES_PER_BLOCK_READ", 1)
    reader = _introspect._classdef_from_block
    _introspect._BLOCK_READS.clear()
    reader.cache_clear()
    yield
    _introspect._BLOCK_READS.clear()
    reader.cache_clear()


_SHAPES = '''\
"""Module docstring."""
import functools
import sys

from duho import Args


def tag(*names):
    def apply(cls):
        return cls

    return apply


@tag(
    "multi",
    "line",
)
@tag("second")
class Decorated(Args):
    """Decorated with two decorators, one of them spanning lines."""

    alpha: str = "a"
    "Alpha help."
    ("-a", "--alpha")


def make_local():
    class Local(Args):
        """Defined inside a function."""

        beta: int = 1
        "Beta help."
        ("--beta",)

        class Inner(Args):
            """Nested in a function-local class."""

            gamma: str = "g"
            ("--gamma",)

    return Local


class Outer(Args):
    """Outer class."""

    class Nested(Args):
        """Nested in a class."""

        delta: str = "d"
        "Delta help."
        ("--delta",)

        class Deeper(Args):
            deep: int = 3
            ("--deep",)


if sys.version_info >= (3, 0):

    class Branch(Args):
        """Live branch."""

        mode: str = "live"
        ("--live",)

else:

    class Branch(Args):
        """Dead branch."""

        mode: str = "dead"
        ("--dead",)


try:
    import json

    class Tried(Args):
        """Live try."""

        value: int = 1
        ("--tried",)

except ImportError:

    class Tried(Args):
        """Dead except."""

        value: int = 2
        ("--fallback",)


NOTE = """a module level string
spanning several lines
"""


class AfterString(Args):
    """Follows a multi-line string."""

    omega: str = "o"
    ("--omega",)


if True:

    class IndentedMultiline(Args):
        """Indented class whose strings continue on further lines.

            Docstring body keeps its own indentation.
        """

        text: str = "t"
        """First help line
            second help line, indented
        further, less indented"""
        ("--text",)

        other: int = 0
        "Other"
        " help"
        ("-o", "--other")


class OneLine(Args): kept: int = 1


class WithComments(Args):
    """Comments inside the body, some at column zero."""

    first: int = 1
    # a comment at the body's column
    ("--first",)
# a comment at column zero inside the body
    second: int = 2
    "Second help."
    ("--second",)
    # trailing comment after the last statement


class Continued(Args):
    value: str = "a" \\
        "b"
    ("--value",)
    other: int = (
        1
    )
    "Other help."


@functools.lru_cache(maxsize=None)
def _unused():
    return None


class AfterDef(Args):
    """After a function definition."""

    zed: int = 0
    ("--zed",)
'''

_NO_TRAILING_NEWLINE = (
    "from duho import Args\n"
    "\n"
    "\n"
    "class Last(Args):\n"
    '    """The last statement in the file, no newline."""\n'
    "\n"
    "    end: int = 1\n"
    '    "End help."\n'
    '    ("--end",)'
)

_CRLF = (
    "from duho import Args\r\n"
    "\r\n"
    "\r\n"
    "class Crlf(Args):\r\n"
    '    """Windows line endings."""\r\n'
    "\r\n"
    '    name: str = "n"\r\n'
    '    """Help over\r\n'
    '    two lines"""\r\n'
    '    ("--name",)\r\n'
)

_TABS = (
    "from duho import Args\n"
    "\n"
    "if True:\n"
    "\tclass Tabbed(Args):\n"
    '\t\t"""Tab indented."""\n'
    "\n"
    "\t\tname: str = 'n'\n"
    '\t\t"Tab help."\n'
    "\t\t('--name',)\n"
)

_LATIN1 = (
    "# -*- coding: latin-1 -*-\n"
    "from duho import Args\n"
    "\n"
    "\n"
    "class Cafe(Args):\n"
    '    """Caf\xe9."""\n'
    "\n"
    '    name: str = "caf\xe9"\n'
    '    "Help \xe9."\n'
    '    ("--name",)\n'
).encode("latin-1")

_BOM = (
    "﻿from duho import Args\n"
    "\n"
    "\n"
    "class Bom(Args):\n"
    '    """Starts with a BOM."""\n'
    "\n"
    '    name: str = "n"\n'
    '    "Help."\n'
    '    ("--name",)\n'
).encode("utf-8")


def _load(tmp_path, name, content):
    path = tmp_path / (name + ".py")
    if isinstance(content, str):
        content = content.encode("utf-8")
    path.write_bytes(content)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shapes(tmp_path):
    names = []

    def load(name, content):
        names.append(name)
        return _load(tmp_path, name, content)

    modules = {
        "shapes": load("introspect_shapes", _SHAPES),
        "last": load("introspect_last", _NO_TRAILING_NEWLINE),
        "crlf": load("introspect_crlf", _CRLF),
        "tabs": load("introspect_tabs", _TABS),
        "latin1": load("introspect_latin1", _LATIN1),
        "bom": load("introspect_bom", _BOM),
    }
    yield modules
    for name in names:
        sys.modules.pop(name, None)
    _introspect._module_index.cache_clear()


def _all_classes(shapes):
    m = shapes["shapes"]
    local = m.make_local()
    return {
        "decorated": m.Decorated,
        "function-local": local,
        "nested-in-function-local": local.Inner,
        "nested-in-class": m.Outer.Nested,
        "nested-twice": m.Outer.Nested.Deeper,
        "outer": m.Outer,
        "if-branch": m.Branch,
        "try-branch": m.Tried,
        "after-multiline-string": m.AfterString,
        "indented-with-multiline-strings": m.IndentedMultiline,
        "one-line": m.OneLine,
        "comments-in-body": m.WithComments,
        "line-continuation": m.Continued,
        "after-function": m.AfterDef,
        "no-trailing-newline": shapes["last"].Last,
        "crlf": shapes["crlf"].Crlf,
        "tabs": shapes["tabs"].Tabbed,
        "latin1-cookie": shapes["latin1"].Cafe,
        "bom": shapes["bom"].Bom,
    }


def _existing_classes():
    local = _existing._make_local()
    return {
        "existing-if": _existing._UnderIf,
        "existing-try": _existing._UnderTry,
        "existing-try-else": _existing._UnderTryElse,
        "existing-finally": _existing._UnderFinally,
        "existing-local": local,
        "existing-local-inner": local._Inner,
        "existing-nested": _existing._Outer._Nested,
        "existing-dynamic-base": _existing._DynArgs,
        "dynamic": duho.command(_existing._DynArgs, lambda self: None, name="gen"),
        "duho-args": duho.Args,
        "duho-logging": duho.LoggingArgs,
    }


def _forget(cls):
    for base in cls.__mro__:
        if base.__module__.split(".")[0] == "duho":
            continue
        for attr in ("_duho_constants_", "_duho_clsargs_"):
            if attr in vars(base):
                delattr(base, attr)


def _node_dump(cls):
    node = _introspect.getclsdef(cls)
    return None if node is None else ast.dump(node, include_attributes=True)


def _declarations(cls):
    _forget(cls)
    declared = _introspect.get_clsargs(cls)
    return (
        _introspect.get_clsargs_constants(cls),
        {
            name: (d.default, d.type, d.annotations, d.docstring, d.exprs)
            for name, d in declared.items()
        },
    )


def _no_single_class_reader(*args, **kwargs):
    return None


def _both(monkeypatch, cls, measure):
    """``measure(cls)`` with the single-class reader, then without it."""
    _introspect._module_index.cache_clear()
    with_fast = measure(cls)
    _introspect._module_index.cache_clear()
    with monkeypatch.context() as patch:
        patch.setattr(
            _introspect, "_classdef_from_block", _no_single_class_reader, raising=False
        )
        without = measure(cls)
    return with_fast, without


def test_class_nodes_are_identical(shapes, monkeypatch):
    classes = {**_all_classes(shapes), **_existing_classes()}
    for label, cls in classes.items():
        fast, whole = _both(monkeypatch, cls, _node_dump)
        assert fast == whole, label


def test_declarations_are_identical(shapes, monkeypatch):
    classes = {**_all_classes(shapes), **_existing_classes()}
    for label, cls in classes.items():
        fast, whole = _both(monkeypatch, cls, _declarations)
        assert fast == whole, label


def test_shapes_declare_what_the_source_says(shapes):
    # Guards the fixtures: both paths agreeing on nothing would pass above.
    classes = _all_classes(shapes)
    decorated = _declarations(classes["decorated"])[1]["alpha"]
    assert decorated[3] == "Alpha help."
    assert decorated[4] == [("-a", "--alpha")]
    text = _declarations(classes["indented-with-multiline-strings"])[1]["text"]
    assert text[3] == (
        "First help line\n            second help line, indented\n"
        "        further, less indented"
    )
    assert _declarations(classes["if-branch"])[1]["mode"][4] == [("--live",)]
    assert _declarations(classes["try-branch"])[1]["value"][4] == [("--tried",)]
    assert _declarations(classes["no-trailing-newline"])[1]["end"][3] == "End help."
    assert _declarations(classes["crlf"])[1]["name"][3] == "Help over\n    two lines"
    assert _declarations(classes["latin1-cookie"])[1]["name"][3] == "Help \xe9."
    assert _declarations(classes["one-line"])[1]["kept"][0] == 1


@pytest.mark.skipif(not _HAS_FIRSTLINENO, reason="needs __firstlineno__ (3.13+)")
def test_single_class_reader_is_taken_where_it_applies(shapes, monkeypatch):
    taken = {}
    original = _introspect._classdef_from_block

    def spy(*args, **kwargs):
        node = original(*args, **kwargs)
        taken[args[2]] = node is not None
        return node

    monkeypatch.setattr(_introspect, "_classdef_from_block", spy)
    classes = _all_classes(shapes)
    for label, cls in classes.items():
        _introspect._module_index.cache_clear()
        taken.clear()
        _introspect.getclsdef(cls)
        # Every shape here is written as a literal class statement.
        assert taken.get(cls.__name__) is True, label


@pytest.mark.skipif(not _HAS_FIRSTLINENO, reason="needs __firstlineno__ (3.13+)")
def test_dynamic_class_never_uses_the_single_class_reader(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a class built at run time has no source to read")

    monkeypatch.setattr(_introspect, "_classdef_from_block", refuse)
    generated = duho.command(_existing._DynArgs, lambda self: None, name="gen2")
    assert _introspect.getclsdef(generated) is None


@pytest.mark.skipif(_HAS_FIRSTLINENO, reason="3.13+ has __firstlineno__")
def test_single_class_reader_is_not_taken_without_firstlineno(shapes, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no __firstlineno__ on this interpreter")

    monkeypatch.setattr(_introspect, "_classdef_from_block", refuse, raising=False)
    for label, cls in _all_classes(shapes).items():
        _introspect._module_index.cache_clear()
        assert _introspect.getclsdef(cls) is not None, label


# --- the per-file budget of block reads -------------------------------------

_MANY = "from duho import Args\n\n\n" + "".join(
    "class C%d(Args):\n"
    '    """Command %d."""\n'
    "\n"
    "    field_%d: int = %d\n"
    '    "Help %d."\n'
    '    ("--field-%d",)\n'
    "\n\n" % ((i,) * 6)
    for i in range(8)
)


def _counting(monkeypatch):
    calls = []
    original = _introspect._classdef_from_block

    def spy(*args, **kwargs):
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(_introspect, "_classdef_from_block", spy)
    return calls


def test_small_file_never_uses_the_block_reader(tmp_path, monkeypatch):
    monkeypatch.setattr(_introspect, "_BYTES_PER_BLOCK_READ", 12_000)
    module = _load(tmp_path, "introspect_small", _MANY)
    try:
        calls = _counting(monkeypatch)
        for i in range(8):
            assert _introspect.getclsdef(getattr(module, "C%d" % i)) is not None
        assert calls == []
    finally:
        sys.modules.pop("introspect_small", None)
        _introspect._module_index.cache_clear()


@pytest.mark.skipif(not _HAS_FIRSTLINENO, reason="needs __firstlineno__ (3.13+)")
def test_a_file_gets_a_block_read_per_budget_then_the_index(tmp_path, monkeypatch):
    size = len(_MANY.encode("utf-8"))
    per_read = size // 3 + 1  # a budget of two reads
    assert size // per_read == 2
    monkeypatch.setattr(_introspect, "_BYTES_PER_BLOCK_READ", per_read)
    module = _load(tmp_path, "introspect_budget", _MANY)
    try:
        calls = _counting(monkeypatch)
        classes = [getattr(module, "C%d" % i) for i in range(8)]
        dumps = [
            ast.dump(_introspect.getclsdef(cls), include_attributes=True)
            for cls in classes
        ]
        assert calls == [vars(cls)["__firstlineno__"] for cls in classes[:2]]
        monkeypatch.setattr(
            _introspect, "_classdef_from_block", _no_single_class_reader
        )
        _introspect._module_index.cache_clear()
        whole = [
            ast.dump(_introspect.getclsdef(cls), include_attributes=True)
            for cls in classes
        ]
        assert dumps == whole
    finally:
        sys.modules.pop("introspect_budget", None)
        _introspect._module_index.cache_clear()


@pytest.mark.skipif(not _HAS_FIRSTLINENO, reason="needs __firstlineno__ (3.13+)")
def test_asking_for_the_same_class_again_uses_no_budget(tmp_path, monkeypatch):
    size = len(_MANY.encode("utf-8"))
    monkeypatch.setattr(_introspect, "_BYTES_PER_BLOCK_READ", size // 2 + 1)
    module = _load(tmp_path, "introspect_repeat", _MANY)
    try:
        calls = _counting(monkeypatch)
        for _ in range(3):
            assert _introspect.getclsdef(module.C0) is not None
        assert len(calls) == 3  # a budget of one, spent on the same class
        assert _introspect.getclsdef(module.C1) is not None
        assert len(calls) == 3  # spent: the index serves the next class
    finally:
        sys.modules.pop("introspect_repeat", None)
        _introspect._module_index.cache_clear()
