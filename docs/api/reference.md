# API Reference

Generated from docstrings, organized by area:

- **[Args](args.md)** — `Args`, `Argument`, `ArgumentBuilder`, the `Arg`/`NS`
  annotation helpers, `Meta` (the typed, typo-safe metadata form), the argument
  factories (`Count`, `Append`, `Const`, `Choice`, `Extend`), `UpdateAction`,
  and the module-level entry points (`parser`, `parse`, `main`,
  `value_sources`).
- **[Discovery](discovery.md)** — `discover_commands`, `discover_entry_points`
  (installed-distribution plugins), `CmdBuilder`, `ModuleCommand`, and the
  `register_command_provider` extension seam.
- **[Formatters](formatters.md)** — opt-in `--help` formatters: `DefaultsFormatter`
  (append `(default: X)`), `ColorHelpFormatter` (ANSI), and `ColorDefaultsFormatter`
  (both), selected via a class's `_help_formatter_`.
- **[Presets](presets.md)** — `LoggingArgs`, the ready-made verbosity/log-level
  mixin.
- **[Logging](logging.md)** — colored formatting, custom levels, and stderr
  setup helpers.
- **[Completion](completion.md)** — bash/zsh/fish completion-script generation.
