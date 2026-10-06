"""The environment MCP trigger serves an app() tree through its own dispatch=."""

import json
import subprocess
import sys
import textwrap

from conftest import subprocess_env

_REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "dapp.hello", "arguments": {}},
    },
]


def run_app_script(tmp_path, source, env_extra):
    script = tmp_path / "dapp.py"
    script.write_text(textwrap.dedent(source))
    env = subprocess_env()
    env.pop("PYTHONIOENCODING", None)
    env.update(env_extra)
    payload = "".join(json.dumps(r) + "\n" for r in _REQUESTS).encode()
    return subprocess.run(
        [sys.executable, str(script)],
        input=payload,
        capture_output=True,
        env=env,
        timeout=60,
    )


_DISPATCH_APP = """
    import sys
    import duho


    class Hello(duho.Cmd):
        '''Say hello.'''

        def __call__(self):
            print("hello-ran")
            return 0


    def gate(command, instance):
        print("GATE-CALLED")
        return duho.run_command(command, instance)


    if __name__ == "__main__":
        sys.exit(duho.app(duho.Cli, commands=[Hello], name="dapp", dispatch=gate))
"""


def test_env_trigger_runs_commands_through_the_app_dispatch(tmp_path):
    proc = run_app_script(tmp_path, _DISPATCH_APP, {"DAPP_MCP": "stdio"})
    replies = [json.loads(line) for line in proc.stdout.decode().splitlines()]
    call = [r for r in replies if r.get("id") == 3][0]
    assert call["result"]["content"][0]["text"].split() == ["GATE-CALLED", "hello-ran"]
