"""A default with no duho field behind it (a register hook's own argparse
call) is never published, since it may have been read from the environment."""

import json

import duho
from duho.agenthelp import describe_parser
from duho.mcp import _core_for_app, describe_tools


class Root(duho.LoggingArgs, duho.Cmd):
    """Root supplying global options."""

    _parsername_ = "root"

    def __call__(self):  # pragma: no cover - root is a namespace
        return 0


_TOK = '''\
"""Bare module command using the plain argparse env-default idiom."""
import os


def register(parser, args):
    parser.add_argument("--api-key", default=os.environ.get("PROBE_API_KEY"))


def main(args):
    return 0
'''


def _build(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_API_KEY", "sk-live-S3CRET")
    (tmp_path / "tok.py").write_text(_TOK)
    return _core_for_app(Root, source=tmp_path, argv=[])


def test_tools_list_does_not_carry_the_environment_value(tmp_path, monkeypatch):
    core = _build(tmp_path, monkeypatch)
    assert "S3CRET" not in json.dumps(describe_tools(core))


def test_agent_help_does_not_carry_the_environment_value(tmp_path, monkeypatch):
    core = _build(tmp_path, monkeypatch)
    assert "S3CRET" not in json.dumps(describe_parser(core.root_parser))
