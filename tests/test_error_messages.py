"""User-facing error-message contracts in args.py (Plan 03 T5).

These assert the actual *message text* (not just the exception type) for the
layered-conversion and build-time errors a user is most likely to hit, so a
future refactor that silently degrades a message is caught.

All classes are declared in this real ``.py`` file so their AST-derived
flags/env/docstrings resolve normally.
"""

import re
import typing as ty

import pytest

import duho
from duho import Arg, Args, NS


# --------------------------------------------------------------------------
# Bad env value -> ValueError naming the variable and the field
# --------------------------------------------------------------------------


class _EnvArgs(Args):
    """A typed field backed by an environment variable."""

    port: Arg[int, NS(env="DUHO_T5_PORT")] = 8000
    "Server port"
    ("--port",)


def test_bad_env_value_message(monkeypatch):
    monkeypatch.setenv("DUHO_T5_PORT", "not-an-int")
    with pytest.raises(ValueError) as excinfo:
        duho.parse(_EnvArgs, [])
    msg = str(excinfo.value)
    assert re.search(r"environment variable 'DUHO_T5_PORT' for field 'port'", msg)
    assert "not-an-int" in msg


# --------------------------------------------------------------------------
# Bad config value -> "config value for field ... on Cls"
# --------------------------------------------------------------------------


class _ConfigArgs(Args):
    """A typed field populated from a config file."""

    port: int = 8000
    "Server port"
    ("--port",)


def test_bad_config_value_message(tmp_path):
    config = tmp_path / "app.toml"
    config.write_text('port = "not-an-int"\n')
    with pytest.raises(ValueError) as excinfo:
        duho.parse(_ConfigArgs, [], config=config)
    msg = str(excinfo.value)
    assert "config value for field 'port' on _ConfigArgs" in msg
    assert "not-an-int" in msg


# --------------------------------------------------------------------------
# Union multi-factory exhaustion -> "could not convert ... using any of"
# --------------------------------------------------------------------------


class _UnionArgs(Args):
    """A field whose value must parse as one of several factories."""

    value: "ty.Union[int, float]" = 0
    "An int-or-float value"
    ("--value",)


def test_union_factory_exhaustion_message():
    parser = _UnionArgs._parser_()
    action = next(a for a in parser._actions if "--value" in a.option_strings)
    # The built type factory tries each union member and, on exhaustion, raises
    # a clear ValueError naming the value and the factories tried.
    with pytest.raises(ValueError) as excinfo:
        action.type("definitely-not-a-number")
    msg = str(excinfo.value)
    assert "could not convert 'definitely-not-a-number' using any of" in msg


# --------------------------------------------------------------------------
# Fixed-length tuple annotation -> build-time error naming the field
# --------------------------------------------------------------------------


class _FixedTupleArgs(Args):
    """A fixed-length heterogeneous tuple field (unsupported)."""

    pair: "ty.Tuple[int, str]"
    "A fixed-length pair"
    ("--pair",)


def test_fixed_length_tuple_message():
    with pytest.raises(ValueError) as excinfo:
        _FixedTupleArgs._parser_()
    msg = str(excinfo.value)
    assert "pair" in msg
    assert "fixed-length tuple" in msg
    assert "tuple[T, ...]" in msg


# --------------------------------------------------------------------------
# main() on a bare data Args -> "holds data but is not runnable"
# --------------------------------------------------------------------------


class _DataArgs(Args):
    """A data-only Args with no __call__."""

    name: str = "x"
    ("--name",)


def test_main_on_non_runnable_args_message():
    with pytest.raises(NotImplementedError) as excinfo:
        duho.main(_DataArgs, [])
    msg = str(excinfo.value)
    assert "_DataArgs holds data but is not runnable" in msg
    assert "__call__" in msg
