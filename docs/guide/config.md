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

```python
from duho import Args, Arg, NS

class Deploy(Args):
    """Deploy the app."""

    token: Arg[str, NS(env="DEPLOY_TOKEN")] = ""
    "Auth token"
    ("--token",)
```

```bash
$ export DEPLOY_TOKEN=abc123
$ deploy                      # token == "abc123"
$ deploy --token override     # token == "override"   (CLI wins)
```

The env value is converted with the field's own type, so `NS(env="PORT")` on an
`int` field yields an `int` — and a bad value produces the same clear error
argparse would give.

## Config files

Set `_config_` on the class, or pass `config=` to `duho.parse` / `duho.main`
(the keyword argument wins):

```python
class Deploy(Args):
    _config_ = "~/.config/myapp/config.toml"

result = duho.parse(Deploy, config="./deploy.toml")
result = duho.main(Deploy, config="./deploy.toml")
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

### TOML support

Reading TOML uses the standard library's `tomllib` on Python 3.11+. On 3.9 and
3.10 it falls back to the third-party `tomli` package:

```bash
pip install duho[config]
```

duho stays zero-dependency by default — you only need this extra if you actually
use `_config_` / `config=` on an older interpreter. If neither backend is
available, duho raises a clear error telling you to install it, rather than
failing obscurely.

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

`json` is imported lazily (only when a `.json` config is actually loaded), and a
malformed file raises a clear error naming the file.

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

## Where did this value come from?

`duho.value_sources(parsed)` reports which layer won for each field:

```python
result = duho.parse(Deploy, [], config="./deploy.toml")

duho.value_sources(result)
# {"token": "env", "verbose": "config", "target": "default"}
```

Each value is one of `"cli"`, `"env"`, `"config"`, or `"default"`. This is the
fastest way to answer "why is this setting not what I expect".
