"""``expand`` accepts an optional ``:spec`` suffix and rejects a malformed one."""

import pytest

from duho.text import expand


def test_format_suffix_pads():
    assert list(expand("h[1-3:02d]")) == ["h01", "h02", "h03"]


def test_no_suffix_is_not_padded():
    assert list(expand("h[01-03]")) == ["h1", "h2", "h3"]


@pytest.mark.parametrize("expr", ["h[1-2:{}]", "h[1-2:{x}]", "h[1-2:}]"])
def test_braces_in_the_suffix_raise_value_error(expr):
    with pytest.raises(ValueError):
        list(expand(expr))


@pytest.mark.parametrize("expr", ["h[a-b:03d]", "h[1-2:!r]"])
def test_a_spec_that_does_not_fit_the_member_raises_value_error(expr):
    with pytest.raises(ValueError):
        list(expand(expr))
