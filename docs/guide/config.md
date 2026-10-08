# Configuration layers

Beyond CLI arguments, duho can pull defaults from **environment variables** and a
**config file** in JSON, TOML, YAML or INI. The full precedence ladder, highest first:

```
CLI args  >  instance values  >  env var  >  config file  >  class default
```

A value supplied by *any* layer also **un-requires** that field: a field with no
class default that's set in the config file no longer has to be passed on the
command line.

## Environment variables

Annotate a field with `Meta(env="VAR_NAME")`:

<!-- runnable -->
```python
from duho import Args, Arg, Meta

class Deploy(Args):
    """Deploy the app."""

    token: Arg[str, Meta(env="DEPLOY_TOKEN")] = ""
    "Auth token"
```

```bash
$ export DEPLOY_TOKEN=abc123
$ deploy                      # token == "abc123"
$ deploy --token override     # token == "override"   (CLI wins)
```

The env value is converted with the field's own type, so `Meta(env="PORT")` on an
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

### The `--config` flag: `ConfigArgs`

Most tools let the user name the file. `ConfigArgs` is a mixin that adds
`--config FILE` (`-c`) and reads the file it names, with no other attribute to set:

<!-- runnable -->
```python
from duho import Cli, ConfigArgs, LoggingArgs

class Deploy(ConfigArgs, LoggingArgs, Cli):
    """Deploy the app."""

    port: int = 8000
    "Port to listen on"
```

```bash
$ deploy -c prod.yaml          # port comes from prod.yaml
$ deploy -c prod.yaml --port 1 # the command line wins
$ deploy -c missing.yaml       # deploy: error: argument --config/-c: no such file: missing.yaml
```

The path has `~` expanded and must be a file, so a mistyped path is a usage error
(exit status 2) with no traceback. List `ConfigArgs` before `Cli`: `Cli` declares
`_config_field_ = None`, and `class Deploy(Cli, ConfigArgs)` is a `TypeError` naming the
class. Because the class names a config source, `--help` lists the reversible `--no-*`
form of its `bool` fields whether or not a file was given.

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

### Formats

The file's suffix picks the reader, from the `duho.config` backends:

| Format | Suffixes | Reads with | Writes with | Needs |
| --- | --- | --- | --- | --- |
| JSON | `.json` | `json` | `json` | nothing |
| TOML | `.toml` | `tomllib` (3.11+), else `tomli` | `tomli_w` | `pip install duho[config]` on 3.9 and 3.10, and to write |
| YAML | `.yaml`, `.yml` | `yaml.safe_load` | `yaml.safe_dump` | `pip install duho[yaml]` |
| INI | `.ini`, `.cfg` | `configparser` | `configparser` | nothing |

A name no backend claims (`app.conf`, or no suffix at all) is read as TOML. Every
format gives the same nested shape: top-level keys map to the root command's fields and a
table (object, mapping, section) named for a subcommand maps to that subcommand's fields.

```yaml
# deploy.yaml
token: abc123
verbose: true
install:
  target: prod
```

An INI file is plain text: the keys of `[DEFAULT]` are the top level, every other
section is a table, sections do not inherit `[DEFAULT]`, key case is kept, `%` is not
special, and every value is a string that duho converts with the field's type.

```ini
; deploy.ini
[DEFAULT]
token = abc123
verbose = true

[install]
target = prod
```

duho stays zero-dependency by default: a parser library is imported only when a file of
its format is read. If it is missing, reading that file is a usage error naming the extra
(`duho[yaml]`, `duho[config]`), exit status 2; `--help` and `--version` still work.

### Malformed config files

A malformed file, or one whose top level is not a table, is a usage error naming the
file and the format, with exit status 2 under `duho.main`, `duho.parse` and `duho.app`
alike, not a raised exception. The message carries the parser's problem and position and
never text of the document, so a secret in a bad file is not echoed:

```text
app: error: duho: invalid JSON in config file /etc/app.json: Expecting property name ...
app: error: duho: invalid YAML in config file /etc/app.yaml: found character '\t' ... (at line 2, column 1)
app: error: duho: config file /etc/app.json must contain a table/object at the top level, got list
```

## Reading and writing files yourself: `duho.config`

The backends are public. `load` and `dump` pick a backend by the file name, or by a format
name you pass; `loads` and `dumps` work on text:

<!-- runnable -->
```python
from duho import config

config.dump({"port": 8000, "install": {"target": "prod"}}, "deploy.json")
settings = config.load("deploy.json")           # {"port": 8000, "install": {...}}

text = config.dumps(settings, "ini")            # "[DEFAULT]\nport = 8000\n..."
same = config.loads(text, "ini")                # every INI value is a string
```

`dump` builds the whole document before it touches the file, writes UTF-8, and leaves an
existing file as it was when the data cannot be written (an INI file cannot hold a list or
a table nested two deep). `config.backend_names()` lists the formats and
`config.backend_for(path)` returns the backend a file name selects. Failures are
`duho.config.ConfigError` (a `ValueError` with `path`, `lineno` and `colno`),
`UnsupportedFormatError` and `ConfigDependencyError` (with `.extra`).

### Adding a format

Subclass `ConfigBackend`, set `name`, and implement `loads` and `dumps`. Defining the
class registers it, so `config.load("a.hocon")`, and a `_config_` path ending `.hocon`,
use it:

<!-- runnable -->
```python
from duho import config

class KeyValueBackend(config.ConfigBackend):
    name = "kv"
    aliases = ("keyvalue",)
    suffixes = (".kv",)

    def loads(self, text):
        try:
            return dict(line.split("=", 1) for line in text.splitlines() if line)
        except ValueError:
            raise config.ConfigError("a line without '='") from None

    def dumps(self, data):
        return "".join(f"{key}={value}\n" for key, value in data.items())

assert config.loads("a=1\nb=2\n", "kv") == {"a": "1", "b": "2"}
```

A name, alias or suffix a registered backend already holds is a `ValueError`. To take over
a built-in format, pass `replace=True` in the class statement
(`class MyYaml(config.ConfigBackend, replace=True)` with `name = "yaml"`). A subclass that
sets no `name` of its own, such as `class Loud(config.JSONBackend)`, is not registered; use
it in a set, below.

### Choosing which formats a tool reads

By default a tool reads every registered format. Set `_config_backends_` on the root to
limit it to the ones you list, as registered names, backend classes or instances. A file
whose name no listed suffix matches is then a usage error that lists the accepted
suffixes:

<!-- runnable -->
```python
from duho import Cli, ConfigArgs

class Deploy(ConfigArgs, Cli):
    _config_backends_ = ["toml", "json"]   # no YAML, no INI
```

### Changing a backend's names and suffixes

An instance may carry other `name`, `aliases` and `suffixes` than its class, which is how
a tool reads `.conf` files as INI, or a suffix-less file as YAML. The suffix `""` matches
any file name, and always loses to a longer suffix:

<!-- runnable -->
```python
from duho import Cli, ConfigArgs, config

class Deploy(ConfigArgs, Cli):
    _config_backends_ = [
        "json",
        config.INIBackend(suffixes=(".conf", ".ini")),
        config.YAMLBackend(suffixes=(".yaml", "")),
    ]
```

A later item in the list wins a name or suffix an earlier one also claims, so a subclass
placed last replaces a built-in for this tool only, leaving the registry alone:

```python
class StrictJSON(config.JSONBackend):
    def loads(self, text):
        data = super().loads(text)
        if not isinstance(data, dict):
            raise config.ConfigError("the top level must be an object")
        return data

class Deploy(ConfigArgs, Cli):
    _config_backends_ = [StrictJSON, "toml"]
```

The same `backends=` keyword is accepted by `config.load`, `loads`, `dump`, `dumps`,
`backend_for`, `get_backend` and `backend_names`.

### A format with no backend: `_config_loader_`

For a one-off, set a class-level `_config_loader_`: a `Callable[[Path], dict]` that duho
calls *instead of* the backends. It receives the expanded `Path` and returns the config
`dict`; the layering, precedence and subcommand-table rules are the same.

```python
class Deploy(duho.Cli):
    _config_ = "./deploy.hjson"
    _config_loader_ = staticmethod(lambda path: my_hjson.loads(path.read_text()))
```

Errors raised inside the loader propagate to the caller unchanged: duho does not convert
them, so the application reports them its own way. Only the backends, and the check that
the loaded value is a mapping, report through the parser: a loader that returns something
that is not a mapping is a usage error naming the file. For anything you would use
twice, a `ConfigBackend` subclass is the better home.

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
