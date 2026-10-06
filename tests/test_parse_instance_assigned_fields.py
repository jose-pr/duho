"""`duho.parse(instance)`: a field counts as set when it was passed to the
constructor or its value now differs from the seeded one, whether it was
assigned after construction or the instance is a copy."""

import copy
from typing import List

import duho
from duho import Arg, Args, NS


class Deploy(Args):
    environment: str = "dev"
    retries: int = 1
    tags: List[str] = []
    url: Arg[str, NS(env="DUHO_T_ASSIGNED_URL")] = "http://default"


def test_field_assigned_after_construction_is_kept():
    base = Deploy()
    base.environment = "staging"
    base.retries = 5
    result = duho.parse(base, [])
    assert (result.environment, result.retries) == ("staging", 5)


def test_constructor_kwargs_still_count():
    assert duho.parse(Deploy(environment="prod"), []).environment == "prod"


def test_copy_of_assigned_instance_keeps_the_assigned_values():
    base = Deploy()
    base.environment = "staging"
    result = duho.parse(copy.copy(base), [])
    assert result.environment == "staging"


def test_seeded_default_does_not_outrank_env(monkeypatch):
    monkeypatch.setenv("DUHO_T_ASSIGNED_URL", "http://env")
    assert duho.parse(Deploy(), []).url == "http://env"


def test_copy_of_a_default_built_instance_does_not_outrank_env(monkeypatch):
    monkeypatch.setenv("DUHO_T_ASSIGNED_URL", "http://env")
    assert duho.parse(copy.copy(Deploy()), []).url == "http://env"


def test_assigned_field_outranks_env(monkeypatch):
    monkeypatch.setenv("DUHO_T_ASSIGNED_URL", "http://env")
    base = Deploy()
    base.url = "http://assigned"
    assert duho.parse(base, []).url == "http://assigned"


def test_in_place_mutation_of_a_seeded_collection_counts():
    base = Deploy()
    base.tags.append("x")
    assert duho.parse(base, []).tags == ["x"]
