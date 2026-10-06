"""Cases run by test_environment_isolation.py in a child pytest; not collected
by a normal run (the file name does not match ``test_*.py``)."""

import os


def test_the_caller_environment_does_not_reach_a_test():
    assert os.environ.get("COLUMNS") == "80"
    for name in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTEST_MCP"):
        assert name not in os.environ, name
