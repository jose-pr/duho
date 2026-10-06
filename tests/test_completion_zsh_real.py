"""Generated zsh completion exercised by a real interactive zsh (pty).

Skipped where zsh or the ``pty`` module is missing. Each case types a command
line, presses Tab twice and reads what the terminal showed, so it sees what a
user sees: the candidates, an error from ``_arguments``, or a command it ran.
"""

import argparse
import os
import shutil
import sys
import time

import pytest

from duho import completion

_ZSH = shutil.which("zsh")

pytestmark = pytest.mark.skipif(
    _ZSH is None or sys.platform == "win32",
    reason="needs zsh and a pty (POSIX)",
)

_TAB, _NL, _CTRL_U = "\t", "\n", "\x15"


def _tab_output(tmp_path, script, prog, typed_lines):
    """Install ``script`` as ``_<prog>`` and return, per typed line, what the
    terminal showed after Tab Tab. The working directory is ``tmp_path``."""
    import pty
    import select

    fpath = tmp_path / "fp"
    fpath.mkdir()
    (fpath / ("_" + prog)).write_text(script)
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        ZDOTDIR=str(tmp_path),
        PS1="P> ",
        TERM="dumb",
        COLUMNS="200",
        LINES="60",
    )
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child
        os.chdir(tmp_path)
        os.execvpe(_ZSH, [_ZSH, "-f", "-i"], env)

    def drain(seconds):
        buf = b""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    break
        return buf.decode("utf-8", "replace")

    def send(text, wait):
        os.write(fd, text.encode())
        return drain(wait)

    results = []
    try:
        drain(1.0)
        send(
            "fpath=(%s $fpath); autoload -Uz compinit && compinit -u -d %s; "
            "zstyle ':completion:*' menu no%s" % (fpath, tmp_path / "zcompdump", _NL),
            1.5,
        )
        for typed in typed_lines:
            shown = send(typed + _TAB + _TAB, 1.5)
            send(_CTRL_U, 0.3)
            results.append(" ".join(shown.split()))
    finally:
        os.write(fd, ("exit" + _NL).encode())
        os.close(fd)
        os.waitpid(pid, 0)
    return results


def test_flag_name_with_a_colon_is_offered_with_its_choices(tmp_path):
    parser = argparse.ArgumentParser(prog="colon")
    parser.add_argument("--log:level", choices=["debug", "info"])
    (shown,) = _tab_output(
        tmp_path, completion.zsh(parser), "colon", ["colon --log:level "]
    )
    assert "debug" in shown and "info" in shown
    assert "not found" not in shown


def test_flag_name_text_is_never_run_as_a_command(tmp_path):
    parser = argparse.ArgumentParser(prog="flagx")
    parser.add_argument("--a:b: touch PWN_flagname:c")
    _tab_output(tmp_path, completion.zsh(parser), "flagx", ["flagx --a "])
    assert not [p for p in os.listdir(tmp_path) if p.startswith("PWN")]


def test_choices_starting_with_an_equals_sign_are_offered_as_written(tmp_path):
    parser = argparse.ArgumentParser(prog="eq")
    parser.add_argument("--op", choices=["alpha", "=ls", "==", "!=", "beta"])
    (shown,) = _tab_output(tmp_path, completion.zsh(parser), "eq", ["eq --op "])
    for choice in ("alpha", "=ls", "==", "!=", "beta"):
        assert choice in shown
    assert "not found" not in shown
