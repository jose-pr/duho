"""``duho.text.parse_bool`` and the one shared token table behind it."""

import typing

import pytest

import duho
from duho import _compat, text
from duho.args import ArgumentBuilder


def test_tokens_are_one_table():
    assert _compat.BOOL_TRUE is text.BOOL_TRUE
    assert _compat.BOOL_FALSE is text.BOOL_FALSE
    assert ArgumentBuilder._BOOL_TRUE is text.BOOL_TRUE
    assert isinstance(text.BOOL_TRUE, frozenset)
    assert isinstance(text.BOOL_FALSE, frozenset)
    assert text.BOOL_TRUE == {"1", "true", "yes", "on", "y", "t"}
    assert text.BOOL_FALSE == {"0", "false", "no", "off", "n", "f", ""}


@pytest.mark.parametrize("token", sorted(text.BOOL_TRUE))
def test_true_tokens(token):
    assert text.parse_bool(token) is True
    assert text.parse_bool("  " + token.upper() + " ") is True


@pytest.mark.parametrize("token", sorted(text.BOOL_FALSE))
def test_false_tokens(token):
    assert text.parse_bool(token) is False
    assert text.parse_bool(" " + token.upper() + " ") is False


@pytest.mark.parametrize("bad", ["maybe", "2", "tru", "enable"])
def test_unknown_text_raises_listing_tokens(bad):
    with pytest.raises(ValueError) as excinfo:
        text.parse_bool(bad)
    message = str(excinfo.value)
    assert repr(bad) in message
    for token in text.BOOL_TRUE | (text.BOOL_FALSE - {""}):
        assert token in message


def test_non_string_raises_value_error():
    with pytest.raises(ValueError):
        text.parse_bool(1)  # type: ignore[arg-type]


def test_exported_from_root_and_module():
    assert duho.parse_bool is text.parse_bool
    assert "parse_bool" in duho.__all__
    assert {"parse_bool", "BOOL_TRUE", "BOOL_FALSE"} <= set(text.__all__)


def test_annotations_resolve():
    hints = typing.get_type_hints(text.parse_bool)
    assert hints == {"text": str, "return": bool}
