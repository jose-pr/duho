# Scaffold

Opt-in launcher generator: `generate_launchers`, runnable as
`python -m duho.scaffold`. Core `duho` never imports this module.

::: duho.scaffold
    options:
      filters:
        - "!^_"
        - "!^ScaffoldCmd$"

## CLI

`duho.scaffold.ScaffoldCmd` is the `duho.Cli` command backing
`python -m duho.scaffold`; run `python -m duho.scaffold --help` for its full,
generated usage (`<app> [--root DIR] [--libdir lib] [--python PY] [--force]`).
