# API Reference

Generated from docstrings, organized by area:

- **[Args](args.md)** — `Args`, `Cmd`, `Cli`, `Argument`, `ArgumentBuilder`, the
  `Arg`/`NS` annotation helpers, `Meta` (the typed, typo-safe metadata form),
  the argument factories (`Count`, `Append`, `Const`, `Choice`, `Extend`),
  `UpdateAction`, and the module-level entry points (`parser`, `parse`,
  `parse_globals`, `main`, `finish_parse`, `command`, `subcommand`, `value_sources`,
  `print_agent_help`, `print_completion`).
- **[Runtime](runtime.md)** — `duho.app`, the multi-command app runner
  (discovery, config/env thread-down, and dispatch), `run_command`, and
  `utf8_stdio`.
- **[Discovery](discovery.md)** — `discover_commands`, `discover_entry_points`
  (installed-distribution plugins), `CmdBuilder`, `ModuleCommand`, and the
  `register_command_provider` extension seam.
- **[Env](env.md)** — `duho.Env`, the prefixed, app-wide environment accessor
  (`.bool`, `.list`, `.paths`).
- **[Formatters](formatters.md)** — opt-in `--help` formatters: `DefaultsFormatter`
  (append `(default: X)`), `ColorHelpFormatter` (ANSI), and `ColorDefaultsFormatter`
  (both), selected via a class's `_help_formatter_`.
- **[Presets](presets.md)** — `LoggingArgs`, the ready-made verbosity/log-level
  mixin.
- **[Logging](logging.md)** — colored formatting, custom levels, and stderr
  setup helpers.
- **[Agent help](agenthelp.md)** — the machine-readable `--help` document
  (`AGENT_HELP`/`--help-agents`, `describe`, `print_agent_help`).
- **[Completion](completion.md)** — bash/zsh/fish/PowerShell completion-script
  generation.
- **[Text](text.md)** — `expand`, `pysafe`, `snakecase`/`camelcase`/`kebabcase`, `parse_bool`, `gettext`.
- **[QualName](qualname.md)** — dotted-name algebra for command qualnames.
- **[Fanout](fanout.md)** *(opt-in, `import duho.fanout`)* — run one command
  against many targets and roll their exit codes into one.
- **[RunPath](runpath.md)** *(opt-in, `import duho.runpath`)* — ordered step
  commands from a directory of numbered `.py` files; see the
  [RunPath guide](../guide/runpath.md).
- **[Scaffold](scaffold.md)** *(opt-in, `import duho.scaffold`)* — generate a
  run-from-checkout launcher pair for an app.
- **[Testing](testing.md)** *(opt-in, `import duho.testing`)* — `invoke`, run a
  command line in-process and get its status and output; see the
  [testing guide](../guide/testing.md).
- **[MCP](mcp.md)** *(opt-in, `import duho.mcp`)* — expose a duho CLI as MCP
  tools.
- **[Parsers](parsers.md)** — subparser utilities and helper functions.
