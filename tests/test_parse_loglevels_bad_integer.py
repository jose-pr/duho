"""``parse_loglevels`` names a malformed integer level in an ArgumentTypeError."""

import argparse

import pytest

from duho.logging import parse_loglevels


@pytest.mark.parametrize("text", ["--5", "²", "a:--5", "1-2"])
def test_malformed_integer_level_raises_argument_type_error(text):
    with pytest.raises(argparse.ArgumentTypeError) as exc:
        parse_loglevels(text)
    assert text.split(":")[-1].strip() in str(exc.value)


def test_negative_integer_level_still_parses():
    assert parse_loglevels("-5") == {"": -5}
