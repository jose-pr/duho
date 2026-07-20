"""Tests for the ``duho.mcp`` type -> JSON Schema emitter.

``json_schema_for_field``/``input_schema_for_command`` are standalone -- they
read a command class's own field declarations (``cls._getargs_()`` +
``duho._introspect.get_clsargs``) directly, no argparse parser required.

Fixtures are declared at module level (a real source file) because duho's
flags-tuple / docstring introspection is AST-based and needs one -- same
convention as ``test_agenthelp.py``.
"""

import enum
import pathlib
import typing as ty

import pytest

from duho import Arg, Cmd, NS
from duho.mcp import input_schema_for_command


class Color(enum.Enum):
    RED = 1
    GREEN = 2


class Widget(Cmd):
    """A command exercising the full field-type surface."""

    name: str
    "Required string"
    ("--name",)

    count: int = 1
    "Defaulted int"
    ("--count",)

    ratio: float = 0.5
    "Defaulted float"
    ("--ratio",)

    flag: bool = False
    "Bare bool (store_true)"
    ("--flag",)

    always: bool = True
    "Bool defaulting True (BooleanOptionalAction)"
    ("--always",)

    mode: ty.Literal["fast", "slow"] = "fast"
    "Literal choice"
    ("--mode",)

    mixed: ty.Literal["auto", 1, 2.5]
    "Mixed-type literal"
    ("--mixed",)

    color: Color = Color.RED
    "Enum by member name"
    ("--color",)

    tags: ty.List[str]
    "Repeatable string list"
    ("--tag",)

    ports: "ty.Set[int]"
    "A set of ints"
    ("--port",)

    coords: "ty.Tuple[float, ...]"
    "A variadic tuple"
    ("--coord",)

    labels: "ty.Dict[str, str]"
    "A dict field"
    ("--label",)

    maybe_name: "ty.Optional[str]" = None
    "Optional with explicit default"
    ("--maybe-name",)

    maybe_bare: "ty.Optional[str]"
    "Optional with NO explicit default"
    ("--maybe-bare",)

    either: "ty.Union[int, str]"
    "A real (non-Optional) union"
    ("--either",)

    source: pathlib.Path
    "Required positional Path"
    ("source",)

    dest: str = "."
    "Optional positional"
    ("dest",)

    def __call__(self):  # pragma: no cover - not dispatched here
        return 0


def _props(cls=Widget):
    return input_schema_for_command(cls)["properties"]


def _required(cls=Widget):
    return set(input_schema_for_command(cls)["required"])


# --------------------------------------------------------------------------
# Top-level shape
# --------------------------------------------------------------------------


def test_schema_top_level_shape():
    schema = input_schema_for_command(Widget)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert isinstance(schema["properties"], dict)
    assert isinstance(schema["required"], list)


# --------------------------------------------------------------------------
# str / int / float / bool
# --------------------------------------------------------------------------


def test_str_int_float_bool():
    props = _props()
    assert props["name"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["count"]["default"] == 1
    assert props["ratio"]["type"] == "number"
    assert props["ratio"]["default"] == 0.5
    assert props["flag"]["type"] == "boolean"
    assert props["flag"]["default"] is False


def test_bool_defaulting_true_still_boolean():
    props = _props()
    assert props["always"]["type"] == "boolean"
    assert props["always"]["default"] is True


# --------------------------------------------------------------------------
# Literal / Enum
# --------------------------------------------------------------------------


def test_literal_single_type():
    props = _props()
    mode = props["mode"]
    assert mode["type"] == "string"
    assert mode["enum"] == ["fast", "slow"]
    assert mode["default"] == "fast"


def test_literal_mixed_type_has_no_scalar_type_key():
    props = _props()
    mixed = props["mixed"]
    assert mixed["enum"] == ["auto", 1, 2.5]
    assert "type" not in mixed


def test_enum_by_member_name():
    props = _props()
    color = props["color"]
    assert color["type"] == "string"
    assert color["enum"] == ["RED", "GREEN"]
    assert color["default"] == "RED"


# --------------------------------------------------------------------------
# list / set / tuple / dict
# --------------------------------------------------------------------------


def test_list_field_is_array_of_items():
    props = _props()
    tags = props["tags"]
    assert tags["type"] == "array"
    assert tags["items"] == {"type": "string"}
    assert tags["default"] == []


def test_set_field_is_array_with_unique_items():
    props = _props()
    ports = props["ports"]
    assert ports["type"] == "array"
    assert ports["items"] == {"type": "integer"}
    assert ports["uniqueItems"] is True


def test_tuple_field_is_array():
    props = _props()
    coords = props["coords"]
    assert coords["type"] == "array"
    assert coords["items"] == {"type": "number"}


def test_dict_field_is_object_with_additional_properties():
    props = _props()
    labels = props["labels"]
    assert labels["type"] == "object"
    assert labels["additionalProperties"] == {"type": "string"}
    assert labels["default"] == {}


def test_list_field_is_repeatable_and_required_when_no_default():
    # `tags` has no explicit default -> duho still gives list fields an
    # implicit [] default (never required).
    assert "tags" not in _required()


# --------------------------------------------------------------------------
# Optional / Union
# --------------------------------------------------------------------------


def test_optional_with_default_unwraps_and_is_not_required():
    props = _props()
    assert props["maybe_name"]["type"] == "string"
    assert props["maybe_name"]["default"] is None
    assert "maybe_name" not in _required()


def test_optional_without_default_is_still_not_required():
    props = _props()
    assert props["maybe_bare"]["type"] == "string"
    assert props["maybe_bare"]["default"] is None
    assert "maybe_bare" not in _required()


def test_real_union_becomes_anyof():
    props = _props()
    either = props["either"]
    assert "anyOf" in either
    kinds = {frozenset(m.items()) for m in either["anyOf"]}
    assert frozenset({("type", "integer")}) in kinds
    assert frozenset({("type", "string")}) in kinds
    # A real (non-Optional) Union with no default is still required.
    assert "either" in _required()


# --------------------------------------------------------------------------
# Path
# --------------------------------------------------------------------------


def test_path_field_is_string():
    props = _props()
    assert props["source"]["type"] == "string"


# --------------------------------------------------------------------------
# required vs defaulted
# --------------------------------------------------------------------------


def test_required_set_matches_no_default_fields():
    required = _required()
    assert "name" in required
    assert "source" in required
    assert "count" not in required
    assert "dest" not in required


def test_positional_with_default_is_optional_positional():
    props = _props()
    assert props["dest"]["type"] == "string"
    assert props["dest"]["default"] == "."
    assert "dest" not in _required()


# --------------------------------------------------------------------------
# descriptions
# --------------------------------------------------------------------------


def test_description_from_docstring():
    props = _props()
    assert props["name"]["description"] == "Required string"
    assert props["source"]["description"] == "Required positional Path"
