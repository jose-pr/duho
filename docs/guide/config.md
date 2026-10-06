# Configuration layers

Beyond CLI arguments, duho can pull defaults from **environment variables** and a
**TOML config file**. The full precedence ladder, highest first:

```
CLI args  >  instance values  >  env var  >  config file  >  class default
```

A value supplied by *any* layer also **un-requires** that field: a field with no
class default that's set in the config file no longer has to be passed on the
command line.

## Environment variables

Annotate a field with `NS(env="VAR_NAME")`:

<!-- runnable -->
```python
from duho import Args, Arg, NS

class Deploy(Args):
    """Deploy the app."""

    token: Arg[str, NS(env="DEPLOY_TOKEN")] = ""
    "Auth token"
```

```bash
$ export DEPLOY_TOKEN=abc123
$ deploy                      # token == "abc123"
$ deploy --token override     # token == "override"   (CLI wins)
```

The env value is converted with the field's own type, so `NS(env="PORT")` on an
`int` field yields an `int` — and a bad value produces the same clear error
argparse would give. A `bool` field reads `1`, `true`, `yes`, `on`, `y`, `t` as
`True` and `0`, `false`, `no`, `off`, `n`, `f` or the empty string as `False`,
case-insensitively; `duho.parse_bool(text)` applies the same rule to any string
(`duho.text.BOOL_TRUE` and `BOOL_FALSE` are the token tables) and raises `ValueError`
for anything else.

## Config files

Set `_config_` on the class, or pass `config=` to `duho.parse` / `duho.main`
(the keyword argument wins; `duho.parse_globals` and `duho.app` take it too). Every
entry point layers env and the class's `_config_` the same way: `parse`, `main`,
`parse_globals`, `app`, and a parser from `duho.parser(cls)`:

```python
class Deploy(Args):
    _config_ = "~/.config/myapp/config.toml"

result = duho.parse(Deploy, config="./deploy.toml")
```

Top-level keys map to the root command's fields. A table named after a
subcommand maps to that subcommand's fields:

```toml
# deploy.toml
token = "abc123"
verbose = true

[install]
target = "prod"
```

Unknown keys are ignored (with a debug log line), so a config file can carry
settings for several versions of your tool without breaking older ones.

A `_config_` path that does not exist yet is skipped (with a debug log)
instead of raising — a class attribute is allowed to point at a file an app
hasn't written yet. An explicit `config=` argument stays strict: a missing
file passed that way still raises, since you named it directly.

### Choosing the config file at run time

`_config_` fixes the path in the class. To let the user or the environment choose it,
set `_config_env_` and/or `_config_field_` on the root:

<!-- runnable -->
```python
from typing import Optional
from duho import Cmd

class Tool(Cmd):
    _config_ = "~/.config/tool/config.toml"
    _config_env_ = "TOOL_CONFIG"      # the path is read from $TOOL_CONFIG
    _config_field_ = "config"         # ...or from --config

    config: Optional[str] = None
    "Config file"

    def __call__(self):
        print(self.config)
```

Highest first:

1. an explicit `config=` argument to `main`/`parse`/`parse_globals`/`app`;
2. the `_config_field_` field, when the user gave it on the command line or through its
   own env var (its class default does not count);
3. the `_config_env_` variable, when it is set and not empty;
4. `_config_`.

A path chosen by the field or the variable is strict, like `config=`: a missing file
raises `FileNotFoundError`, where a missing `_config_` is skipped. A `_config_field_`
naming a field the class does not declare is a `ValueError` naming the class, raised
before parsing. Neither attribute is applied to a tree served over
[MCP](https://github.com/jose-pr/duho/#mcp-tool-surface-duhomcp-opt-in).

An env var set to the empty string is treated as unset (falls through to
config/class default) for every field except a bare `str` field, which keeps
the empty string as its value.

### TOML support

Reading TOML uses the standard library's `tomllib` on Python 3.11+. On 3.9 and
3.10 it falls back to the third-party `tomli` package:

```bash
pip install duho[config]
```

duho stays zero-dependency by default — you only need this extra (`tomli>=2.0,<3`,
installed only below Python 3.11) if you actually use `_config_` / `config=` on an
older interpreter. If neither backend is available, reading a `.toml` config is a
usage error naming the extra (exit status 2); `--help` and `--version` still work.

### JSON support

A config path ending in `.json` is parsed as JSON using the standard library —
no extra dependency. JSON produces the same nested-dict shape as TOML, so
top-level keys map to the root and a nested object named for a subcommand maps to
that subcommand's fields:

```json
{
  "token": "abc123",
  "verbose": true,
  "install": { "target": "prod" }
}
```

`json` is imported lazily (only when a `.json` config is actually loaded).

### Malformed config files

A malformed JSON or TOML file, or one whose top level is not a table/object, is a
usage error naming the file, with exit status 2 — under `duho.main`, `duho.parse` and
`duho.app` alike, not a raised exception:

```text
app: error: duho: invalid JSON in config file /etc/app.json: Expecting property name ...
app: error: duho: config file /etc/app.json must contain a table/object at the top level, got list
```

### Any other format: `_config_loader_`

To read a format duho does not ship (YAML, INI, …) **without adding a
dependency**, set a class-level `_config_loader_` — a `Callable[[Path], dict]`
that duho calls *instead of* the built-in JSON/TOML dispatch. You bring the
parser; duho never imports it:

```python
import yaml  # your dependency, not duho's

class Deploy(duho.Cli):
    _config_ = "./deploy.yaml"
    _config_loader_ = staticmethod(
        lambda path: yaml.safe_load(path.read_text()) or {}
    )
```

The hook receives the expanded `Path` and must return the config `dict`; the
layering, precedence, and subcommand-table rules are identical to the built-in
loaders. This keeps duho's zero-runtime-dependency contract while supporting any
config format you like.

Errors raised inside the loader propagate to the caller unchanged: duho does not
convert them, so the application reports them its own way. Only the built-in JSON and
TOML readers, and the check that the loaded value is a mapping, report through the
parser: a loader that returns something that is not a mapping is a usage error naming
the file.

## Where did this value come from?

`duho.value_sources(parsed)` reports which layer won for each field of a parsed
instance — what `duho.parse` returns, or `self` inside a command's `__call__`:

```python
result = duho.parse(Deploy, [], config="./deploy.toml")

duho.value_sources(result)
# {"token": "env", "verbose": "config", "target": "default"}
```

Each value is one of `"cli"`, `"instance"` (a field that came from an instance
passed to `duho.parse`), `"env"`, `"config"`, or `"default"`. This is the
fastest way to answer "why is this setting not what I expect".
