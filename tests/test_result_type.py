"""``duho.Result``: an int that also carries an answer and words for an MCP client."""

import json

import pytest

import duho
from duho import Cli, Cmd, Result


def test_is_an_int_with_the_code_as_value():
    r = Result(3)
    assert isinstance(r, int)
    assert int(r) == 3 == r
    assert r.code == 3
    assert Result().code == 0 and Result() == 0


def test_attributes_default_to_none():
    r = Result(1, value={"a": 1}, text="words")
    assert r.value == {"a": 1}
    assert r.text == "words"
    bare = Result(1)
    assert bare.value is None and bare.text is None


def test_is_error_defaults_to_nonzero_and_can_be_overridden():
    assert Result(0).is_error is False
    assert Result(2).is_error is True
    assert Result(1, is_error=False).is_error is False
    assert Result(0, is_error=True).is_error is True


@pytest.mark.parametrize("bad", [True, False, 1.5, "1", None])
def test_code_must_be_a_plain_int(bad):
    with pytest.raises(TypeError):
        Result(bad)


def test_repr_is_plain_and_honest():
    assert repr(Result(2)) == "Result(2)"
    assert repr(Result(0, value="v", text="t", is_error=True)) == (
        "Result(0, value='v', text='t', is_error=True)"
    )


def test_sys_exit_accepts_it():
    with pytest.raises(SystemExit) as info:
        raise SystemExit(Result(4, value="ignored"))
    assert info.value.code == 4


class Answers(Cmd):
    """Returns a Result."""

    _parsername_ = "answers"

    def __call__(self):
        return Result(3, value={"rows": 3}, text="three rows")


class Root(Cli):
    _parsername_ = "answerer"
    _subcommands_ = [Answers]


def test_main_returns_it_unchanged():
    out = duho.main(Root, ["answers"])
    assert isinstance(out, Result)
    assert out.value == {"rows": 3} and out == 3


def test_app_returns_it_unchanged():
    out = duho.app(Root, argv=["answers"])
    assert isinstance(out, Result) and out.text == "three rows"


def test_rendering_is_the_json_the_mcp_path_uses():
    from duho._outcome import _render

    assert _render("as is") == "as is"
    assert _render({"é": [1]}) == json.dumps({"é": [1]}, indent=2, ensure_ascii=False)
    assert _render(object) == json.dumps(str(object))
