"""User-facing error-message contracts in args.py (Plan 03 T5).

These assert the actual *message text* (not just the exception type) for the
layered-conversion and build-time errors a user is most likely to hit, so a
future refactor that silently degrades a message is caught.

All classes are declared in this real ``.py`` file so their AST-derived
flags/env/docstrings resolve normally.
"""

import enum
import re
import typing as ty

import pytest

import duho
from duho import Arg, Args, Choice, NS

# --------------------------------------------------------------------------
# Bad env value -> ValueError naming the variable and the field
# --------------------------------------------------------------------------


class _EnvArgs(Args):
    """A typed field backed by an environment variable."""

    port: Arg[int, NS(env="DUHO_T5_PORT")] = 8000
    "Server port"
    ("--port",)


def test_bad_env_value_message(monkeypatch, capsys):
    # R020: a bad env value is reported the same way a bad CLI value would be
    # -- usage text + exit 2, never a raw traceback.
    monkeypatch.setenv("DUHO_T5_PORT", "not-an-int")
    with pytest.raises(SystemExit) as excinfo:
        duho.parse(_EnvArgs, [])
    assert excinfo.value.code == 2
    msg = capsys.readouterr().err
    assert re.search(r"environment variable 'DUHO_T5_PORT' for field 'port'", msg)
    assert "not-an-int" in msg
    assert "usage:" in msg


# --------------------------------------------------------------------------
# Bad config value -> "config value for field ... on Cls"
# --------------------------------------------------------------------------


class _ConfigArgs(Args):
    """A typed field populated from a config file."""

    port: int = 8000
    "Server port"
    ("--port",)


@pytest.mark.requires_toml
def test_bad_config_value_message(tmp_path, capsys):
    # R020: same usage-text-and-exit-2 contract as a bad env value.
    config = tmp_path / "app.toml"
    config.write_text('port = "not-an-int"\n')
    with pytest.raises(SystemExit) as excinfo:
        duho.parse(_ConfigArgs, [], config=config)
    assert excinfo.value.code == 2
    msg = capsys.readouterr().err
    assert "config value for field 'port' on _ConfigArgs" in msg
    assert "not-an-int" in msg
    assert "usage:" in msg


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


def test_union_factory_exhaustion_message_reaches_the_user(capsys):
    # A user never calls `action.type(...)` directly -- go through the real
    # argparse path and check the message actually printed to stderr (A018:
    # argparse only preserves a factory's own message for ArgumentTypeError,
    # otherwise it substitutes its own generic "invalid <x> value").
    with pytest.raises(SystemExit):
        duho.parse(_UnionArgs, ["--value", "definitely-not-a-number"])
    err = capsys.readouterr().err
    assert "could not convert 'definitely-not-a-number' using any of" in err


# --------------------------------------------------------------------------
# dict[str, V] KEY=VALUE factory errors -> readable text, not an object repr
# --------------------------------------------------------------------------


class _DictArgs(Args):
    """A dict[str, str] field."""

    opt: "ty.Dict[str, str]" = None
    "String-valued options"
    ("-D",)


def test_dict_missing_equals_message_reaches_the_user(capsys):
    # Previously argparse printed a bare `_KVFactory` object repr and address
    # instead of the crafted "expected KEY=VALUE" message (A018).
    with pytest.raises(SystemExit):
        duho.parse(_DictArgs, ["-D", "noequals"])
    err = capsys.readouterr().err
    assert "expected KEY=VALUE" in err
    assert "noequals" in err
    assert "_KVFactory object at" not in err


class _MixedLiteralArgs(Args):
    """A mixed-type Literal field."""

    mix: "ty.Literal['auto', 1]" = "auto"
    ("--mix",)


def test_mixed_literal_exhaustion_message_reaches_the_user(capsys):
    with pytest.raises(SystemExit):
        duho.parse(_MixedLiteralArgs, ["--mix", "zzz"])
    err = capsys.readouterr().err
    assert "could not convert 'zzz' using any of" in err


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


# --------------------------------------------------------------------------
# R044 fix-readiness pins (finding show.py R044): exact "invalid choice"
# text for a BARE (non-Union) Literal/Choice/Enum field. A validating-factory
# fix for the Union case (A004) must not be applied to the bare single-type
# Literal factory too, or these degrade from argparse's own
# "invalid choice: ... (choose from ...)" to a generic "invalid <x> value".
# --------------------------------------------------------------------------


class _BareLiteralArgs(Args):
    """A bare (non-Union) Literal field."""

    mode: "ty.Literal['fast', 'slow']" = "fast"
    ("--mode",)


def test_bare_literal_invalid_choice_message(capsys):
    with pytest.raises(SystemExit):
        duho.parse(_BareLiteralArgs, ["--mode", "nope"])
    err = capsys.readouterr().err
    assert "invalid choice: 'nope' (choose from 'fast', 'slow')" in err


class _BareChoiceArgs(Args):
    """A bare Choice(...)-restricted field."""

    ch: "Arg[str, Choice('a', 'b')]" = "a"
    ("--ch",)


def test_bare_choice_invalid_choice_message(capsys):
    with pytest.raises(SystemExit):
        duho.parse(_BareChoiceArgs, ["--ch", "z"])
    err = capsys.readouterr().err
    assert "invalid choice: 'z' (choose from 'a', 'b')" in err


class _MsgColor(enum.Enum):
    RED = 1
    GREEN = 2


class _BareEnumArgs(Args):
    """A bare Enum field."""

    color: "_MsgColor" = _MsgColor.RED
    ("--color",)


def test_bare_enum_invalid_choice_message(capsys):
    # Pre-fix this printed argparse's generic "invalid _factory value: ..."
    # (A018/A053); fixed to a crafted "invalid choice" message naming the
    # valid member names, matching Literal/Choice.
    with pytest.raises(SystemExit):
        duho.parse(_BareEnumArgs, ["--color", "GREEN2"])
    err = capsys.readouterr().err
    assert "invalid choice: 'GREEN2' (choose from RED, GREEN)" in err
