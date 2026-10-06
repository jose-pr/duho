"""Tests for ``import duho``'s zero-eager-import contract on its own submodules.

``json``/``importlib.metadata`` are already covered elsewhere (their own
regression tests live next to the features that use them, e.g.
``tests/test_config_json.py``, ``tests/test_entry_points.py``). This file
covers the rest of ``import duho``'s avoidable cost: ``duho.completion`` (and
the ``shlex`` it pulls in) and ``duho.discovery``'s ``importlib.util``/
``pkgutil`` -- none of which a plain, discovery-and-completion-free CLI ever
touches, so none of them should be paid for by every ``import duho``.
"""

import subprocess
import sys

from conftest import subprocess_env


def test_plain_import_duho_does_not_load_completion_or_shlex():
    code = (
        "import sys, duho\n"
        "print('duho.completion' in sys.modules)\n"
        "print('shlex' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    ).splitlines()
    assert out == ["False", "False"]


def test_duho_completion_is_lazy_until_first_attribute_access():
    code = (
        "import sys, duho\n"
        "print('duho.completion' in sys.modules)\n"
        "duho.completion\n"
        "print('duho.completion' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    ).splitlines()
    assert out == ["False", "True"]


def test_duho_completion_importable_via_from_import():
    code = "from duho import completion; print(completion.__name__)"
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "duho.completion"


def test_duho_completion_importable_via_submodule_import():
    code = "import duho.completion as c; print(c.__name__)"
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    )
    assert out.strip() == "duho.completion"


def test_plain_import_duho_does_not_load_discovery_import_helpers():
    """``import importlib.util``/``pkgutil`` are only paid for by discovery calls.

    ``discovery.py`` used to import both at module top, so a plain ``import
    duho`` (which imports ``duho.discovery`` for its top-level re-exports)
    paid for them even when the app never discovers commands from a package/
    directory/import path.
    """
    code = (
        "import sys, duho\n"
        "print('importlib.util' in sys.modules)\n"
        "print('pkgutil' in sys.modules)\n"
    )
    out = subprocess.check_output(
        [sys.executable, "-c", code], text=True, env=subprocess_env()
    ).splitlines()
    assert out == ["False", "False"]


def test_plain_import_duho_does_not_load_the_opt_in_modules():
    names = ["testing", "mcp", "runpath", "completion", "fanout", "scaffold"]
    code = (
        "import sys, duho\n"
        f"for name in {names!r}:\n"
        "    print('duho.' + name in sys.modules)\n"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True).splitlines()
    assert out == ["False"] * len(names)


def test_duho_testing_imports_on_demand():
    code = "import sys, duho.testing; print('duho.testing' in sys.modules)"
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert out.strip() == "True"
