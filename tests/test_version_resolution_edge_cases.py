"""Regression tests for `_version_ = duho.AUTO` edge cases and the unsupported
`parse_intermixed_args` path.

* Running as `python -m pkg` (`cls.__module__ == "__main__"`) must not
  make the AUTO lookup use the literal string `"__main__"` as a distribution
  name -- recover the real top-level package from
  `sys.modules["__main__"].__spec__.name` (what `runpy` sets for `-m`).
* The AUTO lookup must be cached per distribution name across builds.
* On 3.9, `importlib.metadata` does not treat `.` as a name separator
  the way 3.10+'s PEP 503 normalization does -- a dotted `_distribution_`
  must retry with `-`/`_`/`.` collapsed to `_`.
* `parse_intermixed_args`/`parse_known_intermixed_args` bypass duho's
  own instance-construction hook on at least one supported Python version;
  duho must refuse them with a clear error instead of crashing or silently
  returning a half-built result.
"""

import sys
import types

import pytest

import duho
from duho import Args


class _AutoDist(Args):
    _version_ = duho.AUTO

    name: str = "x"
    ("--name",)


@pytest.fixture(autouse=True)
def _reset_auto_version_cache(monkeypatch):
    monkeypatch.setattr(duho.args._naming, "_AUTO_VERSION_CACHE", {})


def test_auto_version_under_python_dash_m_recovers_the_real_package(monkeypatch):
    """Simulate `python -m acme` by giving `sys.modules["__main__"]` a
    `__spec__.name` of `acme.__main__`, matching what `runpy` actually sets,
    while `_AutoDist.__module__` stays the literal string `"__main__"`."""
    fake_main = types.ModuleType("__main__")
    fake_main.__spec__ = types.SimpleNamespace(name="acme.__main__")
    monkeypatch.setitem(sys.modules, "__main__", fake_main)
    monkeypatch.setattr(_AutoDist, "__module__", "__main__")

    calls = []

    def fake_version(dist):
        calls.append(dist)
        return "7.7.7"

    monkeypatch.setattr("importlib.metadata.version", fake_version)
    version = duho.args._resolve_version(_AutoDist)
    assert calls == ["acme"]
    assert version == "7.7.7"


def test_auto_version_without_a_main_spec_falls_back_to_dunder_module(monkeypatch):
    calls = []

    def fake_version(dist):
        calls.append(dist)
        return "1.0"

    monkeypatch.setattr("importlib.metadata.version", fake_version)
    version = duho.args._resolve_version(_AutoDist)
    assert calls == [_AutoDist.__module__.split(".")[0]]
    assert version == "1.0"


def test_auto_version_is_cached_across_builds(monkeypatch):
    calls = []

    def fake_version(dist):
        calls.append(dist)
        return "3.3.3"

    monkeypatch.setattr("importlib.metadata.version", fake_version)
    duho.args._resolve_version(_AutoDist)
    duho.args._resolve_version(_AutoDist)
    duho.args._resolve_version(_AutoDist)
    assert len(calls) == 1


def test_auto_version_dotted_distribution_retries_normalized_on_not_found(monkeypatch):
    import importlib.metadata as im

    calls = []

    def fake_version(dist):
        calls.append(dist)
        if dist == "acme.tools":
            raise im.PackageNotFoundError(dist)
        if dist == "acme_tools":
            return "3.1.4"
        raise im.PackageNotFoundError(dist)

    monkeypatch.setattr("importlib.metadata.version", fake_version)

    class DottedDist(Args):
        _version_ = duho.AUTO
        _distribution_ = "acme.tools"

    version = duho.args._resolve_version(DottedDist)
    assert calls == ["acme.tools", "acme_tools"]
    assert version == "3.1.4"


def test_auto_version_never_retries_when_the_verbatim_name_already_matched(
    monkeypatch,
):
    calls = []

    def fake_version(dist):
        calls.append(dist)
        return "9.9.9"

    monkeypatch.setattr("importlib.metadata.version", fake_version)

    class DottedDistOk(Args):
        _version_ = duho.AUTO
        _distribution_ = "acme.tools.ok"

    duho.args._resolve_version(DottedDistOk)
    assert calls == ["acme.tools.ok"]  # no normalized retry needed


def test_parse_known_intermixed_args_raises_a_clear_error():
    class Simple(Args):
        files: "list" = []
        ("files",)

        verbose: bool = False
        ("-v", "--verbose")

    parser = Simple._parser_()
    with pytest.raises(NotImplementedError, match="duho.parse"):
        parser.parse_intermixed_args(["a", "-v", "b"])


def test_parse_known_intermixed_args_directly_raises_too():
    class Simple2(Args):
        files: "list" = []
        ("files",)

    parser = Simple2._parser_()
    with pytest.raises(NotImplementedError):
        parser.parse_known_intermixed_args(["a"])
