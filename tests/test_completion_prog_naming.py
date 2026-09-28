"""Regression tests: shell completion scripts must bind to a name the user
would actually type, `print_completion`/`--print-completion` must accept a
`prog=` override, an unknown shell must fail clearly, and the emitted script
must be written as raw UTF-8 bytes (not through the text layer, which
translates newlines and re-encodes into the console code page on Windows).
"""

import io
import sys

import pytest

import duho
from duho import Cli, Cmd


class _Serve(Cmd):
    """Serve it."""

    def __call__(self):
        return 0


class _App(Cli):
    """An app with completion enabled."""

    _completion_ = True
    _subcommands_ = [_Serve]

    def __call__(self):
        return 0


class _NamedApp(Cli):
    """Same shape, but with an explicit installed-command name."""

    _parsername_ = "myapp"
    _completion_ = True
    _subcommands_ = [_Serve]

    def __call__(self):
        return 0


def _registered_prog(bash_script: str) -> str:
    """The command name the final `complete ... -F <func> <prog>` line of a
    generated bash script actually registers completion for."""
    (line,) = [ln for ln in bash_script.splitlines() if ln.startswith("complete ")]
    return line.rsplit(" ", 1)[-1]


def test_print_completion_defaults_to_argv0_when_name_is_unset(monkeypatch):
    """The standalone function applies the EXACT same rule the injected
    `--print-completion` flag does (see the `_action_defaults_prog_to_argv0`
    test below): default to the invoked command's argv[0] stem, not the bare
    class name nobody types, when no `_parsername_` was declared."""
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/app"])
    buf = io.StringIO()
    duho.print_completion(_App, "bash", file=buf)
    assert _registered_prog(buf.getvalue()) == "app"


def test_print_completion_prog_override_wins():
    buf = io.StringIO()
    duho.print_completion(_App, "bash", file=buf, prog="myapp")
    assert _registered_prog(buf.getvalue()) == "myapp"


def test_print_completion_respects_an_explicit_parsername():
    buf = io.StringIO()
    duho.print_completion(_NamedApp, "bash", file=buf)
    assert _registered_prog(buf.getvalue()) == "myapp"


def test_print_completion_unknown_shell_raises_clear_value_error():
    with pytest.raises(ValueError, match="unknown shell"):
        duho.print_completion(_App, "tcsh")
    with pytest.raises(ValueError, match="unknown shell"):
        duho.print_completion(_App, "_walk")  # a real, but PRIVATE, module name


def test_print_completion_shell_choices_match_the_cli_flag():
    parser = _App._parser_()
    action = next(a for a in parser._actions if a.dest == "print_completion")
    assert set(action.choices) == {"bash", "zsh", "fish", "powershell"}


def test_print_completion_action_defaults_prog_to_argv0_when_name_is_unset(
    monkeypatch,
):
    """The CLI flag (unlike the standalone function) should bind
    to the command someone actually typed when the root's own name is only
    the class-name fallback nobody types."""
    monkeypatch.setattr(
        sys, "argv", ["/usr/local/bin/app", "--print-completion", "bash"]
    )
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        with pytest.raises(SystemExit) as exc:
            _App._parser_().parse_args(["--print-completion", "bash"])
    finally:
        sys.stdout = real_stdout
    assert exc.value.code == 0
    assert _registered_prog(buf.getvalue()) == "app"


def test_print_completion_action_keeps_explicit_parsername(monkeypatch):
    """An explicitly declared `_parsername_` always wins over sys.argv[0]."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["/usr/local/bin/whatever-the-shim-is-named", "--print-completion", "bash"],
    )
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        with pytest.raises(SystemExit):
            _NamedApp._parser_().parse_args(["--print-completion", "bash"])
    finally:
        sys.stdout = real_stdout
    assert _registered_prog(buf.getvalue()) == "myapp"


def test_completion_output_is_written_as_utf8_bytes_not_translated_text():
    """Writing through `.buffer` bypasses text-mode newline
    translation. Simulate a stream with a binary `.buffer` (as a real
    `sys.stdout` has) and confirm the bytes landed there, LF-only."""

    class _FakeBinaryStream:
        def __init__(self):
            self.buffer = io.BytesIO()
            self.flushed = 0

        def flush(self):
            self.flushed += 1

        def write(self, text):  # pragma: no cover - must not be used
            raise AssertionError(
                "text-mode write() must not be used when .buffer exists"
            )

    fake = _FakeBinaryStream()
    duho.print_completion(_App, "bash", file=fake)
    raw = fake.buffer.getvalue()
    assert b"\r\n" not in raw
    assert raw.decode("utf-8").startswith("# bash completion for")


def test_completion_falls_back_to_plain_write_without_a_buffer():
    """A StringIO (no `.buffer`) must still work -- the common test/capture case."""
    buf = io.StringIO()
    duho.print_completion(_App, "bash", file=buf)
    assert buf.getvalue().startswith("# bash completion for")
