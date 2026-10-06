"""`Meta(flags=...)`/`NS(flags=...)` get the same flag normalisation as the
class-body tuple: the `"--"` shorthand expands, a list is coerced, a set or an
empty sequence is a build-time error naming the field."""

import pytest

import duho
from duho import Arg, Args, Meta, NS


def _options(cls):
    parser = duho.parser(cls)
    return sorted(
        s
        for a in parser._actions
        for s in a.option_strings
        if s not in ("-h", "--help")
    )


def test_meta_flags_expand_double_dash_shorthand():
    class A(Args):
        times: Arg[int, Meta(flags=("-n", "--"))] = 1

    assert _options(A) == ["--times", "-n"]
    assert duho.parse(A, ["--times", "3"]).times == 3
    assert duho.parse(A, ["-n", "4"]).times == 4


def test_ns_flags_lone_shorthand_is_the_default_long_flag():
    class A(Args):
        dry_run: Arg[bool, NS(flags=("--",))] = False

    assert _options(A) == ["--dry-run"]


def test_list_flags_with_env_on_negated_bool():
    class A(Args):
        no_verify: Arg[bool, NS(flags=["--no-verify"], env="DUHO_T_NV")] = False

    assert duho.parse(A, ["--no-verify"]).no_verify is True


@pytest.mark.parametrize("wrap", [NS, Meta])
def test_empty_flags_name_the_field(wrap):
    class A(Args):
        times: Arg[int, wrap(flags=())] = 1

    with pytest.raises(ValueError, match="times"):
        duho.parser(A)


def test_empty_class_body_tuple_names_the_field():
    class A(Args):
        times: int = 1
        ()

    with pytest.raises(ValueError, match="times"):
        duho.parser(A)


def test_set_flags_in_meta_name_the_field():
    class A(Args):
        times: Arg[int, Meta(flags={"-n", "--times"})] = 1

    with pytest.raises(ValueError, match="times"):
        duho.parser(A)
