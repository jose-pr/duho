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

## Where did this value come from?

`duho.value_sources(parsed)` reports which layer won for each field:

```python
result = duho.parse(Deploy, [], config="./deploy.toml")

duho.value_sources(result)
# {"token": "env", "verbose": "config", "target": "default"}
```

Each value is one of `"cli"`, `"env"`, `"config"`, or `"default"`. This is the
fastest way to answer "why is this setting not what I expect".
