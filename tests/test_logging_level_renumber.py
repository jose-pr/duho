"""Registering an already-installed level name at another number is a conflict."""

import logging

import pytest

from duho.logging import add_logging_level


def test_same_name_same_number_is_a_noop():
    add_logging_level("RENUMSAME", 23)
    add_logging_level("RENUMSAME", 23)
    assert logging.RENUMSAME == 23


def test_same_name_other_number_raises_and_keeps_the_first():
    add_logging_level("RENUMCLASH", 23)
    with pytest.raises(ValueError, match="RENUMCLASH"):
        add_logging_level("RENUMCLASH", 24)
    assert logging.RENUMCLASH == 23
    assert logging.getLevelName(24) == "Level 24"


def test_force_renumbers():
    add_logging_level("RENUMFORCE", 23)
    add_logging_level("RENUMFORCE", 24, force=True)
    assert logging.RENUMFORCE == 24
