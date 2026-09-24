"""Regression tests for A017: config/JSON layer value coercion was lossy and
inconsistent with the CLI. ``_convert_single`` ran the CLI text factory on
already-typed values too, so ``int(1.5)`` truncated instead of rejecting,
``str(["a", "b"])`` stringified a list instead of rejecting it, and a native
``bool`` passed through into a non-bool field (``bool`` subclasses ``int``).
The ``dict[str, V]`` table path skipped this rule entirely for non-string
values, disagreeing with what the same value would do through a scalar ``V``
field. A JSON config file is used throughout (no ``tomli``/``tomllib``
dependency, and it preserves the same native int/float/bool/list/dict
distinctions TOML does).

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags resolve normally.
"""

import json

import pytest

import duho
from duho import Args


def _assert_usage_error(exc_info, capsys):
    # A bad config value is reported the same way a bad CLI value would be --
    # usage text + exit 2, never a raw traceback (R020).
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
