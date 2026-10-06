"""Tests that config/JSON layer value coercion is lossless and consistent
with the CLI. ``_convert_single`` must not run the CLI text factory on
already-typed values blindly: ``int(1.5)`` would truncate instead of
rejecting, ``str(["a", "b"])`` would stringify a list, and a native
``bool`` would pass through into a non-bool field (``bool`` subclasses
``int``). The ``dict[str, V]`` table path applies the same rule to
non-string values as a scalar ``V`` field does. A JSON config file is used throughout (no ``tomli``/``tomllib``
dependency, and it preserves the same native int/float/bool/list/dict
distinctions TOML does).

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import datetime
import json
import pathlib
import typing as _t

import pytest

import duho
from duho import Args


def _assert_usage_error(exc_info, capsys):
    # A bad config value is reported the same way a bad CLI value would be --
    # usage text + exit 2, never a raw traceback.
    assert exc_info.value.code == 2
    assert "usage:" in capsys.readouterr().err


class _IntArgs(Args):
    n: int = 0
    ("--n",)


def test_config_int_field_rejects_fractional_float(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"n": 1.5}))
    with pytest.raises(SystemExit) as exc:
        duho.parse(_IntArgs, [], config=cfg)
    _assert_usage_error(exc, capsys)


def test_config_int_field_widens_integral_float(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"n": 30.0}))
    assert duho.parse(_IntArgs, [], config=cfg).n == 30


def test_config_int_field_rejects_bool(tmp_path, capsys):
    # bool subclasses int -- isinstance(True, int) is True, which is exactly
    # why a naive "already an instance of the factory type" check let it
    # through silently.
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"n": True}))
    with pytest.raises(SystemExit) as exc:
        duho.parse(_IntArgs, [], config=cfg)
    _assert_usage_error(exc, capsys)


class _StrArgs(Args):
    name: str = "x"
    ("--name",)


def test_config_str_field_rejects_list(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"name": ["a", "b"]}))
    with pytest.raises(SystemExit) as exc:
        duho.parse(_StrArgs, [], config=cfg)
    _assert_usage_error(exc, capsys)


class _FloatArgs(Args):
    ratio: float = 0.0
    ("--ratio",)


def test_config_float_field_widens_int(tmp_path):
    # int -> float is always lossless, unlike float -> int -- this direction
    # stays allowed (a JSON/TOML `ratio = 30` for a float field).
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"ratio": 30}))
    inst = duho.parse(_FloatArgs, [], config=cfg)
    assert inst.ratio == 30.0
    assert isinstance(inst.ratio, float)


class _DictIntArgs(Args):
    d: "dict[str, int]" = None
    ("--d",)


def test_config_dict_table_values_widen_like_a_scalar_field(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"d": {"a": 1, "b": "2"}}))
    inst = duho.parse(_DictIntArgs, [], config=cfg)
    assert inst.d == {"a": 1, "b": 2}
    assert isinstance(inst.d["b"], int)


def test_config_dict_table_values_reject_fractional_float(tmp_path, capsys):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"d": {"a": 1.5}}))
    with pytest.raises(SystemExit) as exc:
        duho.parse(_DictIntArgs, [], config=cfg)
    _assert_usage_error(exc, capsys)


# --------------------------------------------------------------------------
# A Union/Literal field that ALSO accepts bool as one of its members must
# still accept a native bool from a config layer -- the identity check
# `_convert_non_str` uses to allow a bare `bool`/`_bool_from_text` factory
# through does not recognize a composite Union/Literal callable by identity,
# even though bool is one of its declared members.
# --------------------------------------------------------------------------


class _UnionBoolArgs(Args):
    b: "_t.Union[bool, int]" = 0
    ("--b",)


def test_config_union_field_accepts_native_bool(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"b": True}))
    assert duho.parse(_UnionBoolArgs, [], config=cfg).b is True


class _LiteralBoolArgs(Args):
    lit: "_t.Literal[True, 'auto']" = "auto"
    ("--lit",)


def test_config_mixed_literal_field_accepts_native_bool(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"lit": True}))
    assert duho.parse(_LiteralBoolArgs, [], config=cfg).lit is True


# --------------------------------------------------------------------------
# A `List[Optional[bool]]`/`Dict[str, Optional[bool]]` element must parse
# "false" strictly, the same as a bare `bool` element does -- the
# `Optional[bool]` element resolves to the raw `bool` builtin (a
# single-member Union adopts its member's spec verbatim), which is truthy
# for almost any non-empty string when called naively.
# --------------------------------------------------------------------------


class _OptBoolCollectionArgs(Args):
    flags: "list[_t.Optional[bool]]" = []
    ("--flags",)

    opts: "dict[str, _t.Optional[bool]]" = None
    ("--opts",)


def test_optional_bool_list_element_parses_false_strictly():
    result = duho.parse(_OptBoolCollectionArgs, ["--flags", "false"])
    assert result.flags == [False]


def test_optional_bool_dict_value_parses_false_strictly():
    result = duho.parse(_OptBoolCollectionArgs, ["--opts", "k=false"])
    assert result.opts == {"k": False}


# --------------------------------------------------------------------------
# A TOML `date`/`datetime` value (a native object, not a string) must not
# crash on Python 3.9/3.10, where the pre-3.11 isoformat factory calls
# `.endswith(...)` on `text` UNCONDITIONALLY before checking it is even a
# string -- 3.11+'s `fromisoformat` classmethod doesn't have this branch at
# all, so the crash was version-specific.
# --------------------------------------------------------------------------


class _DateConfigArgs(Args):
    start: datetime.date = datetime.date(2000, 1, 1)
    ("--start",)

    at: "_t.Optional[datetime.datetime]" = None
    ("--at",)


@pytest.mark.requires_toml
def test_config_native_toml_date_does_not_crash(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("start = 2024-01-02\nat = 2024-01-02T03:04:05Z\n")
    result = duho.parse(_DateConfigArgs, [], config=cfg)
    assert result.start == datetime.date(2024, 1, 2)
    assert result.at == datetime.datetime(
        2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc
    )


# --------------------------------------------------------------------------
# A trailing RFC 3339 UTC designator ("Z") must be accepted the SAME way
# regardless of case, on every supported Python version -- 3.11+'s own
# `fromisoformat` natively accepts an uppercase "Z" but rejects a lowercase
# "z"; the <3.11 shim (which rewrites the suffix itself) must normalize case
# too, so "--at ...z" behaves the same on every Python.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["Z", "z"])
def test_cli_datetime_accepts_either_case_of_the_utc_designator(suffix):
    result = duho.parse(_DateConfigArgs, ["--at", f"2024-01-02T03:04:05{suffix}"])
    assert result.at == datetime.datetime(
        2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc
    )


# --------------------------------------------------------------------------
# A Union[int, str] config float must not silently truncate through the
# UNION's own int candidate the way a direct int field is already protected
# against (test_config_int_field_rejects_fractional_float above) -- and a
# config int fed into a Path field must not pass through unconverted as a
# bare int either. Both were left unfixed by the original lossy-coercion
# work above (`_convert_non_str`'s generic fallback called the factory on
# the raw number directly, which only "worked" for `int`/`str` by accident
# of what those two builtins happen to accept from a non-string argument).
# --------------------------------------------------------------------------


class _UnionIntStrArgs(Args):
    n: "_t.Union[int, str]" = 0
    ("--n",)


def test_config_union_int_str_field_widens_whole_float_to_int(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"n": 30.0}))
    result = duho.parse(_UnionIntStrArgs, [], config=cfg)
    assert result.n == 30
    assert isinstance(result.n, int)


def test_config_union_int_str_field_falls_back_to_str_for_a_fractional_float(
    tmp_path,
):
    # 1.5 cannot losslessly become the union's `int` member (`int(1.5)`
    # truncates instead of raising, which is exactly the bug) -- it must
    # fall through to the union's OTHER, lossless member (`str`) instead,
    # rather than silently truncating to `1`.
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"n": 1.5}))
    result = duho.parse(_UnionIntStrArgs, [], config=cfg)
    assert result.n == "1.5"
    assert isinstance(result.n, str)


class _PathArgs(Args):
    p: pathlib.Path = pathlib.Path(".")
    ("--p",)


def test_config_int_into_path_field_converts_instead_of_passing_through(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"p": 5}))
    result = duho.parse(_PathArgs, [], config=cfg)
    assert result.p == pathlib.Path("5")
    assert isinstance(result.p, pathlib.Path)
