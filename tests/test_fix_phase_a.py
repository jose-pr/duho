"""Phase A regression tests: the layered-defaults pipeline (C1, C2, C3, M13, M14).

Each test here is written to FAIL against the pre-fix source and pass after the
per-builder layered converter (``convert_layered``) and the child-parser
suppression / positional-un-require fixes land.
"""

import pathlib

import pytest

import duho
from duho import NS, Arg, Args


# -- A1: bool env conversion (C1) --------------------------------------------


class _BoolEnv(Args):
    dry: Arg[bool, NS(env="DUHO_A1_DRY")] = False
    "Dry run"
    ("--dry",)


def test_bool_env_false_string_is_false(monkeypatch):
    # Pre-fix: bool("false") is True -> dry wrongly becomes True.
    monkeypatch.setenv("DUHO_A1_DRY", "false")
    result = duho.parse(_BoolEnv, [])
    assert result.dry is False


def test_bool_env_zero_is_false(monkeypatch):
    monkeypatch.setenv("DUHO_A1_DRY", "0")
    result = duho.parse(_BoolEnv, [])
    assert result.dry is False


def test_bool_env_one_is_true(monkeypatch):
    monkeypatch.setenv("DUHO_A1_DRY", "1")
    result = duho.parse(_BoolEnv, [])
    assert result.dry is True


def test_bool_env_garbage_raises(monkeypatch):
    monkeypatch.setenv("DUHO_A1_DRY", "banana")
    with pytest.raises(ValueError) as exc:
        duho.parse(_BoolEnv, [])
    assert "DUHO_A1_DRY" in str(exc.value)
    assert "dry" in str(exc.value)


# -- A1: collection env conversion (C2) --------------------------------------


class _ListEnv(Args):
    files: Arg[list[str], NS(env="DUHO_A1_FILES")]
    "Files"
    ("--files",)


class _SetEnv(Args):
    tags: Arg[set[str], NS(env="DUHO_A1_TAGS")]
    "Tags"
    ("--tags",)


def test_list_env_single_element_wrapped(monkeypatch):
    # Pre-fix: element factory str runs on the whole string -> "a.txt" scalar.
    monkeypatch.setenv("DUHO_A1_FILES", "a.txt")
    result = duho.parse(_ListEnv, [])
    assert result.files == ["a.txt"]


def test_set_env_single_element_wrapped(monkeypatch):
    monkeypatch.setenv("DUHO_A1_TAGS", "x")
    result = duho.parse(_SetEnv, [])
    assert result.tags == {"x"}


# -- M14: non-string config conversion ---------------------------------------


class _TimeoutArgs(Args):
    timeout: float = 10.0
    "Timeout"
    ("--timeout",)

    paths: list[pathlib.Path]
    "Paths"
    ("--paths",)


def test_config_int_becomes_float(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text("timeout = 30\n")
    result = duho.parse(_TimeoutArgs, [], config=cfg)
    assert result.timeout == 30.0
    assert isinstance(result.timeout, float)


def test_config_list_of_paths(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('paths = ["a", "b"]\n')
    result = duho.parse(_TimeoutArgs, [], config=cfg)
    assert result.paths == [pathlib.Path("a"), pathlib.Path("b")]


# -- A2: child set_defaults must not clobber suppressed inherited dests (C3) --


class _SubC3(Args):
    verbose: Arg[int, NS(env="DUHO_A2_VERBOSE")] = 0
    "Verbosity"
    ("--verbose",)

    def __call__(self):
        return 0


class _RootC3(duho.Cli):
    verbose: Arg[int, NS(env="DUHO_A2_VERBOSE")] = 0
    "Verbosity"
    ("--verbose",)

    _subcommands_ = [_SubC3]

    def __call__(self):
        return 0


def test_cli_flag_before_subcommand_survives_env(monkeypatch):
    monkeypatch.setenv("DUHO_A2_VERBOSE", "5")
    result = duho.parse(_RootC3, ["--verbose", "3", "_SubC3"])
    assert result.verbose == 3


def test_env_applies_when_no_cli_flag(monkeypatch):
    monkeypatch.setenv("DUHO_A2_VERBOSE", "5")
    result = duho.parse(_RootC3, ["_SubC3"])
    assert result.verbose == 5


# -- A3 / M13: positional supplied by a layer --------------------------------
#
# M13 could NOT be reproduced: argparse's own ``action.required = False`` (which
# _apply_default_layers_one already sets for every layered dest) un-requires a
# positional correctly and the layered default is applied. These tests document
# the working contract and guard against regression.


class _PositionalEnv(Args):
    name: Arg[str, NS(env="DUHO_A3_NAME")]
    "Name"
    ("name",)


def test_positional_env_makes_optional(monkeypatch):
    monkeypatch.setenv("DUHO_A3_NAME", "from-env")
    result = duho.parse(_PositionalEnv, [])
    assert result.name == "from-env"


def test_positional_no_env_still_required(monkeypatch):
    monkeypatch.delenv("DUHO_A3_NAME", raising=False)
    with pytest.raises(SystemExit):
        duho.parse(_PositionalEnv, [])
