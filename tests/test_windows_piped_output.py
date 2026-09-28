"""Regression tests for piped-stdout encoding crashes on Windows.

Windows redirects/pipes ``sys.stdout`` through the console's ANSI code page
(``cp1252`` on this machine and on GitHub's ``windows-latest``) with STRICT
error handling by default (``PYTHONUTF8``/``PYTHONIOENCODING`` unset). Any
character outside that code page then raises ``UnicodeEncodeError`` before a
single byte is written -- empty output, exit 1 -- for a docstring/help string
that duho itself never controls the content of. These tests run a real
subprocess with stdout piped, deliberately WITHOUT ``PYTHONUTF8``/
``PYTHONIOENCODING`` set, so they exercise the actual default-Windows
behavior rather than an already-fixed environment.
"""

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the default Windows console/pipe code page specifically",
)


def _default_env(extra_pythonpath=None):
    """A copy of the current environment with PYTHONUTF8/PYTHONIOENCODING
    removed -- the actual default a user gets unless they deliberately set
    one, which is exactly the case that used to crash."""
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    if extra_pythonpath:
        env["PYTHONPATH"] = extra_pythonpath
    return env


def _write_uniapp(tmp_path):
    """A duho app whose docstrings contain characters outside cp1252 (an
    arrow, a checkmark) -- written as escape sequences so the fixture FILE
    itself stays pure ASCII on disk; the characters exist only once Python
    parses the string literal at import time."""
    app_file = tmp_path / "uniapp.py"
    app_file.write_text(
        "import sys\n"
        "from duho import Cli, Cmd, main\n"
        "\n"
        "class Deploy(Cmd):\n"
        '    """Outil \\u2192 d\\u00e9ploiement \\u2713."""\n'
        "\n"
        "    def __call__(self):\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """App with a non-ASCII (\\u2192) docstring."""\n'
        "\n"
        "    _agent_help_ = True\n"
        "    _subcommands_ = [Deploy]\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main(App, setup_logging=False))\n",
        encoding="ascii",
    )
    return app_file


def test_agent_help_json_survives_piped_non_ascii_docstring(tmp_path):
    app_file = _write_uniapp(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(app_file), "--help-agents"],
        capture_output=True,
        env=_default_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # Valid, UTF-8-decodable JSON straight off the wire -- no UnicodeEncodeError,
    # no console-code-page mangling.
    doc = json.loads(proc.stdout)
    dep = next(s for s in doc["subcommands"] if s["name"] == "Deploy")
    assert "\u2192" in dep["description"]


def test_human_help_survives_piped_non_ascii_docstring(tmp_path):
    app_file = _write_uniapp(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(app_file), "--help"],
        capture_output=True,
        env=_default_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    # `write_human` uses the stream's own encoding with `errors="backslashreplace"`
    # rather than raising -- decode the same way a cp1252 console would to
    # confirm the bytes are well-formed (never partial/crashed output).
    out = proc.stdout.decode("cp1252", errors="strict")
    assert "usage:" in out


def test_help_output_uses_windows_native_crlf_line_endings(tmp_path):
    """`--help`'s ordinary, fully-representable-character path must still
    get the platform's normal text-mode newline translation (``\\n`` ->
    ``\\r\\n``) -- 0.5.4's own behavior. A later fix for the
    ``UnicodeEncodeError`` case above (see the two tests above) routed
    EVERY ``write_human`` call through the stream's raw, untranslated
    ``.buffer`` unconditionally, silently dropping ``--help`` output to
    LF-only on Windows even for plain ASCII text that never needed the
    workaround at all.
    """
    app_file = tmp_path / "plainapp.py"
    app_file.write_text(
        "import sys\n"
        "from duho import Cli, Cmd, main\n"
        "\n"
        "class Deploy(Cmd):\n"
        '    """Deploy the thing."""\n'
        "\n"
        "    def __call__(self):\n"
        "        return 0\n"
        "\n"
        "class App(Cli):\n"
        '    """Plain ASCII app."""\n'
        "\n"
        "    _subcommands_ = [Deploy]\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main(App, setup_logging=False))\n",
        encoding="ascii",
    )
    proc = subprocess.run(
        [sys.executable, str(app_file), "--help"],
        capture_output=True,
        env=_default_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert b"\r\n" in proc.stdout
    # Every line ending is CRLF, not a mix and not LF-only.
    assert proc.stdout.count(b"\n") == proc.stdout.count(b"\r\n")


def test_scaffold_reports_written_paths_without_crashing_on_non_ascii_root(tmp_path):
    root = tmp_path / "\u03c9root"  # omega: outside cp1252
    proc = subprocess.run(
        [sys.executable, "-m", "duho.scaffold", "myapp", "--root", str(root)],
        capture_output=True,
        env=_default_env(),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert (root / "bin" / "myapp").exists()
    assert (root / "bin" / "myapp.cmd").exists()
