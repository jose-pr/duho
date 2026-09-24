"""Regression tests for the single shared bool-token table (A074/C046/D052).

Four places used to define the truthy/falsy word sets independently, and had
already drifted: ``logging._FALSEY`` lacked "n"/"f" so ``DUHO_TRACEBACK=n``
turned tracebacks ON while ``AGENT_HELP=n`` was OFF, and ``Env.bool`` did not
strip whitespace while the layered (env/config) converter did. All four now
read from ``duho._compat.BOOL_TRUE``/``BOOL_FALSE``.

All classes are declared at module level in this real ``.py`` file so their
AST-derived flags/env resolve normally.
"""

import os

import pytest

from duho import Arg, Args, NS, _compat
from duho.args import ArgumentBuilder
from duho.env import Env
from duho.logging import traceback_enabled


def test_args_bool_tables_are_compat_tables():
    """A044/C046/D052: one shared table, not four hand-copied literals."""
    assert ArgumentBuilder._BOOL_TRUE is _compat.BOOL_TRUE
    assert ArgumentBuilder._BOOL_FALSE is _compat.BOOL_FALSE


@pytest.mark.parametrize("false_token", sorted(_compat.BOOL_FALSE - {""}))
def test_traceback_enabled_respects_shared_falsey_set(monkeypatch, false_token):
    # Previously logging._FALSEY == {"", "0", "false", "no", "off"} (no
    # "n"/"f"): DUHO_TRACEBACK=n turned tracebacks ON instead of OFF.
    monkeypatch.setenv("DUHO_TRACEBACK", false_token)
    assert traceback_enabled() is False


@pytest.mark.parametrize("true_token", sorted(_compat.BOOL_TRUE))
def test_traceback_enabled_respects_shared_truthy_set(monkeypatch, true_token):
    monkeypatch.setenv("DUHO_TRACEBACK", true_token)
    assert traceback_enabled() is True


class _EnvBoolArgs(Args):
    """A bool field layered from the same env var `Env.bool` reads."""

    debug: "Arg[bool, NS(env='WSAPP_DEBUG')]" = False
    ("--debug",)


def test_env_bool_strips_whitespace_like_the_layered_converter(monkeypatch):
    # The classic cmd.exe `set VAR=1 && ...` trailing-space pitfall: a
    # layered NS(env=...) bool field already saw "1 " as True (it strips);
    # Env.bool disagreed by not stripping, and read the same text as False.
    monkeypatch.setenv("WSAPP_DEBUG", "1 ")
    import duho

    inst = duho.parse(_EnvBoolArgs, [])
    assert inst.debug is True
    assert Env("wsapp", autoload=False).bool("DEBUG") is True
