# Testing a command line

`duho.testing.invoke` runs a command line in-process and hands back what it did, so a
test needs no subprocess, no `capsys` and no `monkeypatch` of the environment.

```python
from duho.testing import invoke
```

The module is opt-in: `import duho` never imports it.

<!-- runnable -->
```python
import duho
from duho.testing import invoke

class Greet(duho.Cmd):
    """Print a greeting."""

    name: str = "world"
    "Who to greet"

    def __call__(self):
        print(f"hello {self.name}")
        return 3

result = invoke(Greet, ["--name", "x"])
assert result.status == 3
assert result.stdout == "hello x\n"
assert result.stderr == ""

assert invoke(Greet, ["--nope"]).status == 2     # usage error: argparse exits 2
```

## `invoke(root, argv=(), *, env=None, stdin=None, **app_kwargs) -> Result`

- With no `app_kwargs` it runs `duho.main(root, argv)`. With any (`source=`, `name=`,
  `commands=`, ...) it runs `duho.app(root, argv=argv, **app_kwargs)`.
- It returns `Result(status, stdout, stderr)`, a named tuple, so it unpacks as well as
  reading by attribute.
- `env` is applied to `os.environ` for the call and restored afterwards, including a
  key the call added.
- `stdin` is the text the command reads from `sys.stdin`; it is empty when `None`.
- A `SystemExit` (`--help`, a usage error, `sys.exit`) becomes `status`: `None` is 0,
  an int is itself, and any other value is written to `stderr` with status 1. A
  returned `None` is 0.
- Any other exception propagates, with the environment and the streams restored.

An environment variable, a config file and the command line can be exercised
together:

<!-- runnable -->
```python
import duho
from duho import Arg, Meta
from duho.testing import invoke

class Deploy(duho.Cmd):
    region: Arg[str, Meta(env="DEPLOY_REGION")] = "local"

    def __call__(self):
        print(self.region)

assert invoke(Deploy).stdout == "local\n"
assert invoke(Deploy, env={"DEPLOY_REGION": "eu"}).stdout == "eu\n"
assert invoke(Deploy, ["--region", "us"], env={"DEPLOY_REGION": "eu"}).stdout == "us\n"
```
