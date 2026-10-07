"""Property test: MCP tool arguments -> synthesized argv -> parsed command.

For a generated field declaration and a generated JSON value, ``call_tool``
either refuses with ``InvalidArgumentsError`` or runs the command, and the
field the command saw equals the value sent (after the field's own type
conversion). A refusal is only allowed when some value token would read as an
option (it starts with ``-`` or is a command name); any other refusal, a different value, a value
on another field, or a crash fails the test.

Three command shapes: a class command, a module command with a declared
``Args`` class, and a module command whose ``register`` hook adds plain
argparse options.
"""

import enum
import importlib.util
import keyword
import shutil
import string
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import duho  # noqa: E402
from duho.mcp import (  # noqa: E402
    InvalidArgumentsError,
    _core_for_app,
    call_tool,
)

_SETTINGS = dict(
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=list(HealthCheck),
)

_SEEN_MODULE = "_duho_roundtrip_seen"


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Root(duho.LoggingArgs, duho.Cmd):
    """Root supplying global options, as an app does."""

    _parsername_ = "root"

    def __call__(self):  # pragma: no cover - a namespace, never dispatched
        return 0


# ==========================================================================
# Values
# ==========================================================================

# Values an LLM might send: empty, option-shaped, separator-shaped, and names
# that read as commands or files.
_NASTY_TEXT = [
    "",
    "-",
    "--",
    "-x",
    "--x",
    "--help",
    "-1",
    "=",
    "a=b",
    "--k=v",
    " ",
    "a b",
    "0",
    "None",
    "null",
    "@file",
    "root",
    "cmd",
    "ADA",
    "conf",
    "sibling",
]

_text = st.one_of(
    st.sampled_from(_NASTY_TEXT),
    st.text(alphabet=string.ascii_letters + string.digits + "._-=/ ", max_size=8),
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=6),
)
_word = st.text(
    alphabet=string.ascii_letters + string.digits + "._", min_size=1, max_size=8
)
_ints = st.integers(min_value=-(10**12), max_value=10**12)
_floats = st.floats(allow_nan=False, allow_infinity=False)

_RESERVED = {
    "ty",
    "enum",
    "duho",
    "args",
    "path",
    "color",
    "conf",
    "field",
    "help",
    "h",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "set",
    "tuple",
    "sys",
    "main",
    "register",
    "root",
    "cmd",
    "toolbox",
    "parser",
    "self",
    "type",
    "verbose",
    "quiet",
    "loglevels",
    "seen",
    "optional",
    "literal",
}

_names = st.text(alphabet=string.ascii_lowercase, min_size=2, max_size=8).filter(
    lambda s: not keyword.iskeyword(s)
    and s not in _RESERVED
    and not hasattr(duho.Cmd, s)
    and not hasattr(duho.Cli, s)
)


def _tokens(value):
    """The strings a JSON value turns into on the command line."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


# Command names of the generated trees: a positional equal to one is refused.
_COMMAND_NAMES = {"conf", "sibling"}


def _refusable(value):
    return any(tok.startswith("-") or tok in _COMMAND_NAMES for tok in _tokens(value))


# ==========================================================================
# Declared fields (class command and module command with Args)
# ==========================================================================


@st.composite
def _declared_case(draw):
    """(name, annotation, default literal, flags, json value, expected)."""
    name = draw(_names)
    kind = draw(
        st.sampled_from(
            [
                "str",
                "int",
                "float",
                "bool_false",
                "bool_true",
                "path",
                "literal",
                "enum",
                "list_int",
                "list_str",
                "set_str",
                "tuple_int",
                "optional_int",
                "optional_none",
                "dict",
                "pos_str",
                "pos_str_default",
                "pos_int",
                "pos_list",
                "count",
                "short_str",
                "short_int",
                "short_list",
            ]
        )
    )
    opt = '("--%s",)' % name
    pos = '("%s",)' % name
    if kind == "str":
        v = draw(_text)
        return name, "str", '""', opt, v, v
    if kind == "int":
        v = draw(_ints)
        return name, "int", "0", opt, v, v
    if kind == "float":
        v = draw(_floats)
        return name, "float", "0.0", opt, v, v
    if kind == "bool_false":
        v = draw(st.booleans())
        return name, "bool", "False", opt, v, v
    if kind == "bool_true":
        v = draw(st.booleans())
        return name, "bool", "True", opt, v, v
    if kind == "path":
        v = draw(_text)
        return name, "Path", 'Path(".")', opt, v, Path(v)
    if kind == "literal":
        values = draw(st.lists(_word, min_size=2, max_size=4, unique=True))
        v = draw(st.sampled_from(values))
        ann = "ty.Literal[%s]" % ", ".join(repr(x) for x in values)
        return name, ann, repr(values[0]), opt, v, v
    if kind == "enum":
        v = draw(st.sampled_from(["RED", "GREEN", "BLUE"]))
        return name, "Color", "Color.RED", opt, v, Color[v]
    if kind == "list_int":
        v = draw(st.lists(_ints, max_size=4))
        return name, "ty.List[int]", "[]", opt, v, v
    if kind == "list_str":
        v = draw(st.lists(_text, max_size=4))
        return name, "ty.List[str]", "[]", opt, v, v
    if kind == "set_str":
        v = draw(st.lists(_text, max_size=4))
        return name, "ty.Set[str]", "set()", opt, v, set(v)
    if kind == "tuple_int":
        v = draw(st.lists(_ints, max_size=4))
        return name, "ty.Tuple[int, ...]", "()", opt, v, tuple(v)
    if kind == "optional_int":
        v = draw(_ints)
        return name, "ty.Optional[int]", "None", opt, v, v
    if kind == "optional_none":
        return name, "ty.Optional[int]", "None", opt, None, None
    if kind == "dict":
        v = draw(st.dictionaries(_word, _word, max_size=3))
        return name, "ty.Dict[str, str]", "{}", opt, v, v
    if kind == "pos_str":
        v = draw(_text)
        return name, "str", None, pos, v, v
    if kind == "pos_str_default":
        v = draw(_text)
        return name, "str", '"dflt"', pos, v, v
    if kind == "pos_int":
        v = draw(_ints)
        return name, "int", "0", pos, v, v
    if kind == "count":
        v = draw(st.integers(min_value=0, max_value=5))
        return name, "Arg[int, Meta(action='count')]", "0", opt, v, v
    short = '("-z",)'
    if kind == "short_str":
        v = draw(_text)
        return name, "str", '""', short, v, v
    if kind == "short_int":
        v = draw(_ints)
        return name, "int", "0", short, v, v
    if kind == "short_list":
        v = draw(st.lists(_text, max_size=4))
        return name, "ty.List[str]", "[]", short, v, v
    v = draw(st.lists(_text, max_size=4))
    return name, "ty.List[str]", "[]", pos, v, v


_HEADER = """\
import enum
import sys
import typing as ty
from pathlib import Path

import duho
from duho import Arg, Cli, Cmd, Meta


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


def _seen(value):
    sys.modules["%s"].values.append(value)

""" % (_SEEN_MODULE)


def _field_lines(name, annotation, default, flags):
    default_part = "" if default is None else " = " + default
    return '    %s: %s%s\n    "doc"\n    %s\n' % (name, annotation, default_part, flags)


_CLASS_SOURCE = _HEADER + """
class Conf(Cmd):
    \"\"\"Generated command.\"\"\"

%(fields)s
    def __call__(self):
        _seen(self.%(name)s)
        return 0


class Toolbox(Cli):
    \"\"\"Root.\"\"\"

    _parsername_ = "toolbox"
    _subcommands_ = [Conf]
"""

_ARGS_SOURCE = _HEADER + """
class Args:
%(fields)s

def main(args):
    _seen(args.%(name)s)
    return 0
"""

_REGISTER_SOURCE = _HEADER + """
def register(parser, args):
    %(add)s


def main(args):
    _seen(args.%(name)s)
    return 0
"""


_SIBLING_SOURCE = '''"""A sibling command."""


def main(args=None):
    return 0
'''


def _fresh_seen():
    holder = types.ModuleType(_SEEN_MODULE)
    holder.values = []
    sys.modules[_SEEN_MODULE] = holder
    return holder.values


def _import(path):
    mod_name = "m_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return mod_name, module


def _text_of(result):
    return result["content"][0]["text"] if result.get("content") else ""


def _check(seen, name, call, value, expected):
    """Run ``call`` and hold the dispatched value to what was sent."""
    try:
        result = call()
    except InvalidArgumentsError:
        assert _refusable(
            value
        ), "refused a value with no option-shaped or command-name token: %r" % (value,)
        assert not seen
        hypothesis.event("refused")
        return
    assert not result.get("isError"), (value, _text_of(result))
    assert len(seen) == 1, (value, seen, _text_of(result))
    hypothesis.event("dispatched")
    got = seen[0]
    if isinstance(expected, enum.Enum):
        assert type(got).__name__ == "Color" and got.name == expected.name
        return
    assert got == expected, (name, value, got)
    assert type(got) is type(expected), (name, value, got)


def _run_dir(shape_call):
    workdir = tempfile.mkdtemp(prefix="duho_mcp_rt_")
    try:
        return shape_call(Path(workdir))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@settings(max_examples=150, **_SETTINGS)
@given(case=_declared_case())
def test_class_command_round_trip(case):
    name, annotation, default, flags, value, expected = case
    seen = _fresh_seen()
    source = _CLASS_SOURCE % {
        "fields": _field_lines(name, annotation, default, flags),
        "name": name,
    }

    def run(workdir):
        path = workdir / "genclass.py"
        path.write_text(source)
        mod_name, module = _import(path)
        try:
            _check(
                seen,
                name,
                lambda: call_tool(module.Toolbox, "toolbox.conf", {name: value}),
                value,
                expected,
            )
        finally:
            sys.modules.pop(mod_name, None)

    _run_dir(run)


def _app_round_trip(source, name, arguments, value, expected):
    seen = _fresh_seen()

    def run(workdir):
        stem = "c" + uuid.uuid4().hex[:12]
        (workdir / (stem + ".py")).write_text(source)
        (workdir / "sibling.py").write_text(_SIBLING_SOURCE)
        core = _core_for_app(Root, source=workdir, argv=[])
        _check(
            seen,
            name,
            lambda: call_tool(core, "root." + stem, arguments),
            value,
            expected,
        )

    _run_dir(run)


@settings(max_examples=100, **_SETTINGS)
@given(case=_declared_case())
def test_module_command_with_declared_args_round_trip(case):
    name, annotation, default, flags, value, expected = case
    source = _ARGS_SOURCE % {
        "fields": _field_lines(name, annotation, default, flags),
        "name": name,
    }
    _app_round_trip(source, name, {name: value}, value, expected)


# ==========================================================================
# Module command whose register hook adds plain argparse options
# ==========================================================================


@st.composite
def _register_case(draw):
    """(name, add_argument source, json value, expected)."""
    name = draw(_names)
    kind = draw(
        st.sampled_from(
            [
                "opt_str",
                "opt_int",
                "opt_float",
                "opt_choice",
                "store_true",
                "store_false",
                "count",
                "append_str",
                "append_int",
                "pos_str",
                "pos_int",
                "pos_star_int",
                "pos_plus_str",
                "pos_opt_str",
            ]
        )
    )
    flag = '"--%s"' % name
    if kind == "opt_str":
        v = draw(_text)
        return name, "parser.add_argument(%s)" % flag, v, v
    if kind == "opt_int":
        v = draw(_ints)
        return name, "parser.add_argument(%s, type=int)" % flag, v, v
    if kind == "opt_float":
        v = draw(_floats)
        return name, "parser.add_argument(%s, type=float)" % flag, v, v
    if kind == "opt_choice":
        choices = draw(st.lists(_word, min_size=2, max_size=4, unique=True))
        v = draw(st.sampled_from(choices))
        return name, "parser.add_argument(%s, choices=%r)" % (flag, choices), v, v
    if kind == "store_true":
        v = draw(st.booleans())
        return name, "parser.add_argument(%s, action='store_true')" % flag, v, v
    if kind == "store_false":
        v = draw(st.booleans())
        return name, "parser.add_argument(%s, action='store_false')" % flag, v, v
    if kind == "count":
        # A bare counting action is published as a boolean: it can be given
        # once or not at all.
        v = draw(st.booleans())
        return (
            name,
            "parser.add_argument(%s, action='count', default=0)" % flag,
            v,
            int(v),
        )
    if kind == "append_str":
        v = draw(st.lists(_text, max_size=4))
        return name, "parser.add_argument(%s, action='append', default=[])" % flag, v, v
    if kind == "append_int":
        v = draw(st.lists(_ints, max_size=4))
        return (
            name,
            "parser.add_argument(%s, action='append', type=int, default=[])" % flag,
            v,
            v,
        )
    if kind == "pos_str":
        v = draw(_text)
        return name, 'parser.add_argument("%s")' % name, v, v
    if kind == "pos_int":
        v = draw(_ints)
        return name, 'parser.add_argument("%s", type=int)' % name, v, v
    if kind == "pos_star_int":
        v = draw(st.lists(_ints, max_size=4))
        return name, 'parser.add_argument("%s", nargs="*", type=int)' % name, v, v
    if kind == "pos_plus_str":
        v = draw(st.lists(_text, min_size=1, max_size=4))
        return name, 'parser.add_argument("%s", nargs="+")' % name, v, v
    v = draw(_text)
    return name, 'parser.add_argument("%s", nargs="?", default="dflt")' % name, v, v


@settings(max_examples=150, **_SETTINGS)
@given(case=_register_case())
def test_register_only_module_command_round_trip(case):
    name, add, value, expected = case
    source = _REGISTER_SOURCE % {"add": add, "name": name}
    _app_round_trip(source, name, {name: value}, value, expected)
