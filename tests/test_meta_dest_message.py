"""`Meta(dest=...)` is refused with a message that says the dest is always the
field name."""

import pytest

from duho import Meta


def test_meta_dest_error_says_the_dest_is_the_field_name():
    with pytest.raises(TypeError) as info:
        Meta(dest="renamed")
    message = str(info.value)
    assert "field name" in message
