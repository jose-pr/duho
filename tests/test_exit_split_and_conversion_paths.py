"""Three documented paths with no other test: a target that exits without a
code, ``Extend`` with a callable splitter, and a bad env value for a field
whose action accumulates (converted eagerly, so it must still be a usage
error)."""

import typing as ty

import pytest

import duho
from duho import Arg, Args, Count, Extend, Meta
from duho.fanout import run_targets


def test_a_target_exiting_without_a_code_counts_as_success():
    def func(target):
        raise SystemExit()

    assert run_targets(func, ["a", "b"]) == 0


def test_a_target_exiting_with_none_counts_as_success():
    def func(target):
        raise SystemExit(None)

    assert run_targets(func, ["a"]) == 0


def _semicolons(text):
    return tuple(text.split(";"))


class SplitterArgs(Args):
    """An extend field split by a callable."""

    tags: Arg[ty.List[str], Extend(_semicolons)] = []
    ("--tags",)


def test_extend_splits_each_occurrence_with_a_callable():
    assert duho.parse(SplitterArgs, ["--tags", "a;b"]).tags == ["a", "b"]


def test_extend_with_a_callable_flattens_repeated_occurrences():
    parsed = duho.parse(SplitterArgs, ["--tags", "a;b", "--tags", "c"])
    assert parsed.tags == ["a", "b", "c"]


class CountFromEnvArgs(Args):
    """A count field that can start from an environment variable."""

    verbose: "Arg[int, Meta(env='DUHO_TEST_COUNT_VERBOSE'), Count()]" = 0
    ("-v",)


def test_a_bad_env_value_for_a_count_field_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setenv("DUHO_TEST_COUNT_VERBOSE", "not-a-number")
    with pytest.raises(SystemExit) as excinfo:
        duho.parse(CountFromEnvArgs, [])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "environment variable 'DUHO_TEST_COUNT_VERBOSE' for field 'verbose'" in err
    assert "expected int" in err


def test_a_good_env_value_starts_a_count_field_that_the_flag_then_increments(
    monkeypatch,
):
    monkeypatch.setenv("DUHO_TEST_COUNT_VERBOSE", "2")
    assert duho.parse(CountFromEnvArgs, ["-v"]).verbose == 3
