"""The root exports each public discovery helper; internal helpers are underscored."""

import inspect

import pytest

import duho
from duho import agenthelp, discovery, formatters, scaffold


@pytest.mark.parametrize(
    "name",
    [
        "unregister_command_provider",
        "is_class_command",
        "is_module_command",
        "import_from_path",
    ],
)
def test_root_exports_discovery_helper(name):
    assert name in duho.__all__
    assert getattr(duho, name) is getattr(discovery, name)


def test_scaffold_main_is_public():
    assert "main" in scaffold.__all__


@pytest.mark.parametrize(
    "module, name",
    [
        (agenthelp, "stash_default_provenance"),
        (agenthelp, "redact_action_defaults"),
        (agenthelp, "install_help_redaction"),
        (formatters, "install_required_usage_formatter"),
    ],
)
def test_internal_helper_is_underscored(module, name):
    assert not hasattr(module, name)
    assert callable(getattr(module, "_" + name))


def test_describe_parser_signature_has_no_private_parameter():
    params = inspect.signature(agenthelp.describe_parser).parameters
    assert not [p for p in params if p.startswith("_")]
