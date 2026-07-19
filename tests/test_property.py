"""Property-based round-trip suites (Plan 03 T7, needs the `hypothesis` dev extra).

Four families of invariants:

* **Field round-trip** -- a generated field of a drawn type is written to a REAL
  temp module (so the AST scan path runs), parsed from generated argv, and the
  parsed value must equal the expected conversion.
* **duho.expand** -- generated ``[a-b]`` numeric/alpha ranges expand to exactly
  the product of the range sizes and match a naive reference implementation.
* **text.snakecase** -- output is a lower-case ``[a-z0-9_]*`` string for
  ASCII-identifier inputs (C13).
* **parse_loglevels** -- never raises on separator soup and returns a well-shaped
  mapping.
"""

import importlib.util
import keyword
import re
import string
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import duho  # noqa: E402
from duho import expand, snakecase  # noqa: E402
from duho.logging import parse_loglevels  # noqa: E402


# ==========================================================================
# Field round-trip
# ==========================================================================

_MODDIR = tempfile.mkdtemp(prefix="duho_prop_")
if _MODDIR not in sys.path:
    sys.path.insert(0, _MODDIR)

_HEADER = '''\
import enum
import typing as ty
from pathlib import Path

import duho
from duho import Args


class Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
'''


def _build_and_parse(name, annotation, default_literal, argv):
    """Write a one-field Args class to a real module, import it, parse argv."""
    mod_name = "m_" + uuid.uuid4().hex
    src = _HEADER + textwrap.dedent(
        f'''

class Conf(Args):
    """Generated field-round-trip class."""

    {name}: {annotation} = {default_literal}
    ("--{name}",)
'''
    )
    path = Path(_MODDIR) / (mod_name + ".py")
    path.write_text(src)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
        parsed = duho.parse(module.Conf, argv)
        return getattr(parsed, name), module
    finally:
        sys.modules.pop(mod_name, None)
        try:
            path.unlink()
        except OSError:
            pass


# A "safe" argv value: no leading dash (else argparse reads it as a flag), no
# whitespace, and drawn from an argparse-friendly alphabet.
_safe_value = st.text(
    alphabet=string.ascii_letters + string.digits + "._", min_size=1, max_size=8
)

# Names that would collide with something in the generated module namespace
# (the `import typing as ty` alias, the seeded `Color` enum, etc.).
_RESERVED_NAMES = {
    "ty", "enum", "duho", "args", "path", "color", "conf", "field",
    "help", "h",  # would produce --help/--h flags colliding with argparse
}

_identifiers = st.text(
    alphabet=string.ascii_lowercase, min_size=2, max_size=8
).filter(lambda s: not keyword.iskeyword(s) and s not in _RESERVED_NAMES)


@st.composite
def _field_case(draw):
    """Draw (name, annotation, default_literal, argv, expected)."""
    name = draw(_identifiers)
    kind = draw(
        st.sampled_from(
            ["str", "int", "float", "bool", "path", "literal", "list", "set", "tuple", "optional"]
        )
    )

    if kind == "str":
        value = draw(_safe_value)
        return name, "str", '""', ["--%s" % name, value], value
    if kind == "int":
        value = draw(st.integers(min_value=0, max_value=10**9))
        return name, "int", "0", ["--%s" % name, str(value)], value
    if kind == "float":
        value = draw(
            st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False)
        )
        # repr round-trips exactly in CPython: float(repr(x)) == x.
        return name, "float", "0.0", ["--%s" % name, repr(value)], value
    if kind == "bool":
        value = draw(st.booleans())
        argv = ["--%s" % name] if value else []
        return name, "bool", "False", argv, value
    if kind == "path":
        segs = draw(st.lists(_safe_value, min_size=1, max_size=3))
        raw = "/".join(segs)
        return name, "Path", 'Path(".")', ["--%s" % name, raw], Path(raw)
    if kind == "literal":
        values = draw(
            st.lists(_safe_value, min_size=2, max_size=4, unique=True)
        )
        chosen = draw(st.sampled_from(values))
        ann = "ty.Literal[%s]" % ", ".join(repr(v) for v in values)
        return name, ann, repr(values[0]), ["--%s" % name, chosen], chosen
    if kind == "list":
        values = draw(st.lists(st.integers(min_value=0, max_value=1000), max_size=4))
        argv = ["--%s" % name] + [str(v) for v in values]
        return name, "ty.List[int]", "[]", argv, values
    if kind == "set":
        values = draw(st.lists(_safe_value, max_size=4))
        argv = ["--%s" % name] + values
        return name, "ty.Set[str]", "set()", argv, set(values)
    if kind == "tuple":
        values = draw(st.lists(st.integers(min_value=0, max_value=1000), max_size=4))
        argv = ["--%s" % name] + [str(v) for v in values]
        return name, "ty.Tuple[int, ...]", "()", argv, tuple(values)
    # optional
    present = draw(st.booleans())
    if present:
        value = draw(st.integers(min_value=0, max_value=10**6))
        return name, "ty.Optional[int]", "None", ["--%s" % name, str(value)], value
    return name, "ty.Optional[int]", "None", [], None


@settings(deadline=None, max_examples=120)
@given(case=_field_case())
def test_field_round_trip(case):
    name, annotation, default_literal, argv, expected = case
    result, _module = _build_and_parse(name, annotation, default_literal, argv)
    assert result == expected
    assert type(result) is type(expected)


@settings(deadline=None, max_examples=40)
@given(member=st.sampled_from(["RED", "GREEN", "BLUE"]), name=_identifiers)
def test_enum_field_round_trip(member, name):
    """An Enum field parses by member NAME back to the matching member."""
    result, module = _build_and_parse(
        name, "Color", "Color.RED", ["--%s" % name, member]
    )
    assert result is getattr(module.Color, member)


# ==========================================================================
# duho.expand
# ==========================================================================


def _expand_reference(segments):
    """Naive Cartesian-product reference for a list of (literal|values) parts."""
    outputs = [""]
    for seg in segments:
        if isinstance(seg, str):  # literal
            outputs = [o + seg for o in outputs]
        else:  # a list of range members
            outputs = [o + v for o in outputs for v in seg]
    return outputs


@st.composite
def _range_template(draw):
    """Draw (template_string, segments) with 1-2 ranges separated by letters."""
    n_ranges = draw(st.integers(min_value=1, max_value=2))
    template_parts = []
    segments = []
    # Always start with a letter literal so numeric ranges never merge with a
    # leading digit, and so distinct expansions stay distinct.
    for i in range(n_ranges):
        lit = draw(st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=3))
        template_parts.append(lit)
        segments.append(lit)
        if draw(st.booleans()):
            # numeric range
            lo = draw(st.integers(min_value=0, max_value=15))
            hi = draw(st.integers(min_value=lo, max_value=lo + 8))
            template_parts.append("[%d-%d]" % (lo, hi))
            segments.append([str(v) for v in range(lo, hi + 1)])
        else:
            # alpha range (uppercase to stay unambiguous)
            a = draw(st.integers(min_value=ord("A"), max_value=ord("T")))
            b = draw(st.integers(min_value=a, max_value=min(a + 6, ord("Z"))))
            template_parts.append("[%s-%s]" % (chr(a), chr(b)))
            segments.append([chr(c) for c in range(a, b + 1)])
    # Trailing letter literal.
    tail = draw(st.text(alphabet=string.ascii_lowercase, min_size=0, max_size=3))
    template_parts.append(tail)
    segments.append(tail)
    return "".join(template_parts), segments


@settings(deadline=None, max_examples=200)
@given(tpl=_range_template())
def test_expand_matches_reference_and_product(tpl):
    template, segments = tpl
    result = list(expand(template))
    reference = _expand_reference(segments)
    # Output length equals the product of the range sizes.
    product = 1
    for seg in segments:
        if not isinstance(seg, str):
            product *= len(seg)
    assert len(result) == product
    assert sorted(result) == sorted(reference)


@settings(deadline=None, max_examples=100)
@given(s=st.text(alphabet=string.ascii_lowercase + string.digits, max_size=10))
def test_expand_no_range_passes_through(s):
    # A string with no bracketed range is yielded unchanged (single element).
    assert list(expand(s)) == [s]


# ==========================================================================
# text.snakecase
# ==========================================================================

_SNAKE_OK = re.compile(r"^[a-z0-9_]*$")


@settings(deadline=None, max_examples=200)
@given(
    name=st.text(
        alphabet=string.ascii_letters + string.digits + "_", min_size=0, max_size=20
    )
)
def test_snakecase_is_lower_word_string(name):
    """For ASCII-identifier-ish inputs, snakecase output is [a-z0-9_]* (C13)."""
    out = snakecase(name)
    assert _SNAKE_OK.match(out), (name, out)


@settings(deadline=None, max_examples=100)
@given(name=st.text(alphabet=string.ascii_uppercase, min_size=1, max_size=10))
def test_snakecase_lowercases_all_letters(name):
    out = snakecase(name)
    assert out == out.lower()


# ==========================================================================
# parse_loglevels
# ==========================================================================


@settings(deadline=None, max_examples=200)
@given(
    text=st.text(
        alphabet=string.ascii_letters + string.digits + ":,", max_size=30
    )
)
def test_parse_loglevels_never_raises_and_shapes(text):
    result = parse_loglevels(text)
    assert isinstance(result, dict)
    for key, value in result.items():
        assert isinstance(key, str)
        assert isinstance(value, int)


@settings(deadline=None, max_examples=100)
@given(
    names=st.lists(
        st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=6),
        min_size=1,
        max_size=4,
    )
)
def test_parse_loglevels_maps_known_levels(names):
    # "name:DEBUG,name2:INFO,..." -> each maps to its int level.
    spec = ",".join("%s:DEBUG" % n for n in names)
    result = parse_loglevels(spec)
    import logging

    for n in names:
        assert result.get(n) == logging.DEBUG
