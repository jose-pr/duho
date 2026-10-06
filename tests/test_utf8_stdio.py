"""Tests for `duho.utf8_stdio` and its wiring into `duho.main`/`duho.app`.

Two halves:

* **Unit tests** (in-process, fake streams) exercise the five skip rules,
  the per-stream errors policy, idempotence, and "never raises" directly
  against `duho._compat.utf8_stdio` -- no subprocess needed.
* **Wiring tests** (in-process, monkeypatched `_compat.utf8_stdio`) confirm
  `duho.main`/`duho.app` call it first, honor the `_utf8_stdio_` class
  attribute, and let an explicit `utf8_stdio=` kwarg win over it.
* **Subprocess tests** (real child process, piped output, `PYTHONIOENCODING`/
  `PYTHONUTF8` stripped and -- on POSIX -- the locale forced to plain ``C``)
  prove the actual crash-proofing end to end: the default reconfigures a
  non-UTF-8 child's stdio to UTF-8 with no crash anywhere; the opt-out (class
  attribute or kwarg) leaves the child's locale encoding alone but still
  never crashes on `--version` (duho's own `_Utf8SafeVersionAction`); an
  explicit `PYTHONIOENCODING` in the child is never overridden.
"""

from __future__ import annotations

import io
import subprocess
import sys
import types

import pytest

from conftest import subprocess_env
from duho import Cli, Cmd, LoggingArgs, _compat, app, main

# --------------------------------------------------------------------------
# Unit tests: duho._compat.utf8_stdio
# --------------------------------------------------------------------------


class _SysWithoutUtf8Mode:
    """``sys`` as seen by ``duho._compat``, reporting Python's UTF-8 mode off."""

    flags = types.SimpleNamespace(utf8_mode=0)

    def __getattr__(self, name):
        return getattr(sys, name)


@pytest.fixture(autouse=True)
def _utf8_mode_off(monkeypatch):
    """The unit tests assume a locale-encoded interpreter, whatever
    ``PYTHONUTF8`` the suite was started with (the flag is fixed at startup)."""
    monkeypatch.setattr(_compat, "_sys", _SysWithoutUtf8Mode())


def _fake_stream(encoding="cp1252", isatty=False):
    """A real `TextIOWrapper` (so `.reconfigure()` is genuine) over an
    in-memory buffer, with `isatty` overridden per test."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
    stream.isatty = lambda: isatty  # type: ignore[method-assign]
    return stream


def test_switches_a_non_utf8_non_tty_stream():
    out = _fake_stream("cp1252")
    err = _fake_stream("cp1252")
    switched = _compat.utf8_stdio({"stdout": out, "stderr": err})
    assert switched == ["stdout", "stderr"]
    assert out.encoding.lower() == "utf-8"
    assert err.encoding.lower() == "utf-8"


def test_skips_a_tty():
    out = _fake_stream("cp1252", isatty=True)
    switched = _compat.utf8_stdio({"stdout": out})
    assert switched == []
    assert out.encoding.lower() != "utf-8"


def test_skips_a_stream_already_utf8():
    out = _fake_stream("utf-8")
    switched = _compat.utf8_stdio({"stdout": out})
    assert switched == []


def test_skips_when_pythonioencoding_set(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    out = _fake_stream("cp1252")
    switched = _compat.utf8_stdio({"stdout": out})
    assert switched == []
    assert out.encoding.lower() != "utf-8"


def test_skips_when_utf8_mode_is_on(monkeypatch):
    class _FakeFlags:
        utf8_mode = 1

    class _FakeSys:
        flags = _FakeFlags()

    monkeypatch.setattr(_compat, "_sys", _FakeSys())
    out = _fake_stream("cp1252")
    switched = _compat.utf8_stdio({"stdout": out})
    assert switched == []
    assert out.encoding.lower() != "utf-8"


def test_skips_a_stream_without_reconfigure():
    out = io.StringIO()
    switched = _compat.utf8_stdio({"stdout": out})
    assert switched == []


def test_is_idempotent():
    out = _fake_stream("cp1252")
    first = _compat.utf8_stdio({"stdout": out})
    second = _compat.utf8_stdio({"stdout": out})
    assert first == ["stdout"]
    assert second == []


def test_errors_policy_per_stream_name():
    out = _fake_stream("cp1252")
    err = _fake_stream("cp1252")
    other = _fake_stream("cp1252")
    _compat.utf8_stdio({"stdout": out, "stderr": err, "aux": other})
    assert out.errors == "surrogateescape"
    assert err.errors == "backslashreplace"
    assert other.errors == "backslashreplace"


def test_never_raises_when_reconfigure_itself_fails():
    out = _fake_stream("cp1252")

    def _boom(**kwargs):
        raise ValueError("nope")

    out.reconfigure = _boom  # type: ignore[method-assign]
    switched = _compat.utf8_stdio({"stdout": out})
    assert switched == []


def test_default_streams_target_real_stdout_and_stderr(monkeypatch):
    calls = {}

    def _record(**kwargs):
        calls.update(kwargs)
        raise ValueError("stop before actually touching the real streams")

    fake_out = _fake_stream("cp1252")
    fake_err = _fake_stream("cp1252")
    fake_out.reconfigure = _record  # type: ignore[method-assign]
    fake_err.reconfigure = _record  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)
    switched = _compat.utf8_stdio()
    assert switched == []  # both "failed" (by design) but neither raised
    assert calls == {"encoding": "utf-8", "errors": "backslashreplace"}


# --------------------------------------------------------------------------
# Wiring: duho.main / duho.app call utf8_stdio() first, with an opt-out
# --------------------------------------------------------------------------


@pytest.fixture
def _record_utf8_stdio(monkeypatch):
    calls = []

    def _fake(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(_compat, "utf8_stdio", _fake)
    return calls


class _Noop(Cmd):
    _parsername_ = "noop"

    def __call__(self):
        return 0


def test_main_calls_utf8_stdio_by_default(_record_utf8_stdio):
    main(_Noop, [], setup_logging=False)
    assert len(_record_utf8_stdio) == 1


def test_main_skips_utf8_stdio_when_class_attr_false(_record_utf8_stdio):
    class Root(_Noop):
        _utf8_stdio_ = False

    main(Root, [], setup_logging=False)
    assert _record_utf8_stdio == []


def test_main_kwarg_false_wins_over_true_class_attr(_record_utf8_stdio):
    class Root(_Noop):
        _utf8_stdio_ = True

    main(Root, [], setup_logging=False, utf8_stdio=False)
    assert _record_utf8_stdio == []


def test_main_kwarg_true_wins_over_false_class_attr(_record_utf8_stdio):
    class Root(_Noop):
        _utf8_stdio_ = False

    main(Root, [], setup_logging=False, utf8_stdio=True)
    assert len(_record_utf8_stdio) == 1


def test_app_calls_utf8_stdio_by_default(_record_utf8_stdio):
    class Root(LoggingArgs, Cli):
        _subcommands_ = [_Noop]

    app(Root, argv=["noop"], setup_logging=False)
    assert len(_record_utf8_stdio) == 1


def test_app_skips_utf8_stdio_when_class_attr_false(_record_utf8_stdio):
    class Root(LoggingArgs, Cli):
        _utf8_stdio_ = False
        _subcommands_ = [_Noop]

    app(Root, argv=["noop"], setup_logging=False)
    assert _record_utf8_stdio == []


def test_app_kwarg_false_wins_over_true_class_attr(_record_utf8_stdio):
    class Root(LoggingArgs, Cli):
        _utf8_stdio_ = True
        _subcommands_ = [_Noop]

    app(Root, argv=["noop"], setup_logging=False, utf8_stdio=False)
    assert _record_utf8_stdio == []


def test_app_with_no_root_still_calls_utf8_stdio_by_default(_record_utf8_stdio):
    app(commands=[_Noop], argv=["noop"], setup_logging=False)
    assert len(_record_utf8_stdio) == 1


# --------------------------------------------------------------------------
# Subprocess: the default actually reconfigures a non-UTF-8 child's stdio
# --------------------------------------------------------------------------


def _non_utf8_child_env():
    """A child environment with a non-UTF-8 default stdio encoding on every
    OS: Windows already defaults to `cp1252` for piped/redirected output once
    `PYTHONUTF8`/`PYTHONIOENCODING` are stripped. POSIX needs more: forcing
    the locale to plain `C` is not enough by itself -- PEP 540 has Python
    itself auto-ENABLE UTF-8 mode whenever it detects the `C`/`POSIX` locale
    (a deliberate stdlib fallback, since a `C` locale almost never reflects
    the actual desired text encoding), so `LC_ALL=C` alone still measured
    `sys.flags.utf8_mode == 1` / `sys.stdout.encoding == "utf-8"` on a real
    Linux build here. `PYTHONUTF8=0` explicitly overrides that auto-detection
    (confirmed: `LC_ALL=C` + `PYTHONUTF8=0` together give `utf8_mode == 0` /
    `stdout.encoding == "ascii"`); `PYTHONCOERCECLOCALE=0` additionally
    disables PEP 538's own separate `C` -> `C.UTF-8` locale coercion.
    """
    env = subprocess_env()
    env.pop("PYTHONIOENCODING", None)
    if sys.platform == "win32":
        env.pop("PYTHONUTF8", None)
    else:
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["LC_CTYPE"] = "C"
        env["PYTHONCOERCECLOCALE"] = "0"
        env["PYTHONUTF8"] = "0"
    return env


def _write_app(tmp_path, *, opt_out_class_attr=False, opt_out_kwarg=False):
    """A duho app whose version string and command output contain a
    character (Greek beta, U+03B2) outside BOTH `cp1252` and `ASCII` --
    written as an escape sequence so the fixture FILE stays pure ASCII on
    disk; the character exists only once Python parses the string literal.
    """
    opt_out_attr_line = "    _utf8_stdio_ = False\n" if opt_out_class_attr else ""
    utf8_stdio_kwarg = ", utf8_stdio=False" if opt_out_kwarg else ""
    app_file = tmp_path / "betaapp.py"
    app_file.write_text(
        "import sys\n"
        "from duho import Cli, main\n"
        "\n"
        "class Root(Cli):\n"
        '    """Root app."""\n'
        "\n"
        '    _parsername_ = "betaapp"\n'
        '    _version_ = "1.0 \\u03b2"\n' + opt_out_attr_line + "\n"
        "    def __call__(self):\n"
        '        print("print: \\u03b2")\n'
        "        return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        f"    sys.exit(main(Root{utf8_stdio_kwarg}))\n",
        encoding="ascii",
    )
    return app_file


def _run(app_file, *args, env):
    return subprocess.run(
        [sys.executable, str(app_file), *args],
        capture_output=True,
        env=env,
        timeout=30,
    )


def test_default_version_is_utf8_and_never_crashes(tmp_path):
    app_file = _write_app(tmp_path)
    env = _non_utf8_child_env()
    proc = _run(app_file, "--version", env=env)
    assert proc.returncode == 0, proc.stderr
    assert b"\\u03b2" not in proc.stdout  # not backslash-escaped
    assert "\u03b2" in proc.stdout.decode("utf-8")  # real UTF-8 bytes


def test_default_print_is_utf8_and_never_crashes(tmp_path):
    app_file = _write_app(tmp_path)
    env = _non_utf8_child_env()
    proc = _run(app_file, env=env)
    assert proc.returncode == 0, proc.stderr
    assert "\u03b2" in proc.stdout.decode("utf-8")


def test_class_attr_opt_out_keeps_locale_encoding_but_never_crashes(tmp_path):
    app_file = _write_app(tmp_path, opt_out_class_attr=True)
    env = _non_utf8_child_env()
    proc = _run(app_file, "--version", env=env)
    assert proc.returncode == 0, proc.stderr
    # Beta fits in neither cp1252 nor ASCII -- left alone, `write_human`
    # falls back to a backslash escape instead of crashing.
    assert b"\\u03b2" in proc.stdout


def test_main_kwarg_opt_out_keeps_locale_encoding_but_never_crashes(tmp_path):
    app_file = _write_app(tmp_path, opt_out_kwarg=True)
    env = _non_utf8_child_env()
    proc = _run(app_file, "--version", env=env)
    assert proc.returncode == 0, proc.stderr
    assert b"\\u03b2" in proc.stdout


def test_explicit_pythonioencoding_in_child_is_never_overridden(tmp_path):
    app_file = _write_app(tmp_path)
    env = subprocess_env()
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = "cp1252"
    proc = _run(app_file, "--version", env=env)
    # duho must respect the user's explicit choice (skip rule 1) -- stdio
    # stays cp1252, which can't represent beta, but `--version` still must
    # not crash.
    assert proc.returncode == 0, proc.stderr
    assert b"\\u03b2" in proc.stdout
