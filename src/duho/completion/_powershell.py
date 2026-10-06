from __future__ import annotations

import argparse as _argparse
import typing as _ty

from ._quoting import _psq, _validate_prog
from ._spec import _all_specs, _cmd_key, _flag_names, _value_flag_names, _walk

# --------------------------------------------------------------------------
# powershell
# --------------------------------------------------------------------------


def powershell(parser: _argparse.ArgumentParser, prog: _ty.Optional[str] = None) -> str:
    """Emit a PowerShell completion script for `parser`.

    Registers a ``Register-ArgumentCompleter -Native`` script block that
    reconstructs the (sub)command path from the command elements strictly
    BEFORE the cursor, descending only on words that are real subcommand
    names of the current node (a per-path table, mirroring bash), and
    tracks how many of the current node's own positionals
    have been consumed so only the pending one's choices are offered.

    Descends into a subcommand only once the current node's own positionals
    are already consumed (`$nposByPath`, mirroring bash's `own_pos` gate) --
    argparse itself consumes a node's own positionals before ever treating a
    word as its subparsers dispatch value, so a positional whose choices
    happen to include a real subcommand name is never mistaken for one.

    Every value-taking flag gets an explicit branch -- choices, an empty
    result for a Path flag (native file completion takes over), and an
    empty result for a free-value flag -- and command-path/flag
    comparisons are case-SENSITIVE (``-ceq``/``-ccontains``/``-cmatch``),
    matching argparse instead of PowerShell's default case-insensitivity.
    The three per-path LOOKUP TABLES (`$subsByPath`/`$vflagsByPath`/
    `$nposByPath`) are ordinal (case-sensitive) `Dictionary` instances, not
    PowerShell's own `@{}` hashtable literal, which compares keys
    case-INsensitively by default -- with a plain `@{}`, sibling subcommands
    differing only in case (``run``/``Run``) shared one slot and clobbered
    each other's positional/subcommand tables. The inserted completion TEXT
    is ALWAYS single-quoted (both the ASCII quote and PowerShell's Unicode
    "smart" single-quote range doubled), unconditionally rather than only
    when it contains whitespace or a metacharacter, so a candidate like
    ``dry run`` or ``$(rm)`` is inserted as one literal argument instead of
    being split or evaluated when the line is run -- this is a *different*
    protection from `_psq`, which only keeps the script BODY safe when it is
    first parsed.
    """
    root = _walk(parser, prog=prog)
    root_prog = _validate_prog(root.prog)
    specs = _all_specs(root)

    lines: list[str] = []
    lines.append(f"# PowerShell completion for {root_prog}")
    lines.append(
        f"Register-ArgumentCompleter -Native -CommandName {_psq(root_prog)} "
        f"-ScriptBlock {{"
    )
    lines.append("    param($wordToComplete, $commandAst, $cursorPosition)")
    lines.append("")
    lines.append("    $elements = @($commandAst.CommandElements)")
    # A plain `@{}` hashtable literal compares its string keys
    # case-INsensitively, so sibling subcommand paths differing only in case
    # (`run` vs `Run`) would fold to the SAME entry and clobber each other's
    # table -- an ordinal Dictionary keeps every distinct-case path separate.
    dict_ctor = (
        "[System.Collections.Generic.Dictionary[string,object]]::new("
        "[StringComparer]::Ordinal)"
    )
    lines.append(f"    $subsByPath = {dict_ctor}")
    for s in specs:
        if s.subcommands:
            names = ", ".join(_psq(n) for n in s.subcommands)
            lines.append(f"    $subsByPath[{_psq(_cmd_key(s))}] = @({names})")
    lines.append(f"    $vflagsByPath = {dict_ctor}")
    for s in specs:
        vflags = _value_flag_names(s)
        if vflags:
            values = ", ".join(_psq(f) for f in vflags)
            lines.append(f"    $vflagsByPath[{_psq(_cmd_key(s))}] = @({values})")
    lines.append(f"    $nposByPath = {dict_ctor}")
    for s in specs:
        if s.subcommands:
            lines.append(f"    $nposByPath[{_psq(_cmd_key(s))}] = {len(s.positionals)}")
    lines.append("")
    lines.append("    # Reconstruct the (sub)command path from the elements strictly")
    lines.append("    # before the cursor, descending only on real subcommand names")
    lines.append("    # of the current node and skipping a value flag's value.")
    lines.append("    $cmdPath = ''")
    lines.append("    $npos = 0")
    lines.append("    $skip = $false")
    lines.append("    $prev = ''")
    lines.append("    for ($i = 1; $i -lt $elements.Count; $i++) {")
    lines.append("        $el = $elements[$i]")
    lines.append("        # Skip any element whose extent reaches the cursor: the word")
    lines.append("        # currently being completed always ends there, whether it is")
    lines.append("        # empty or partially typed. A text-equality check here would")
    lines.append(
        "        # ALSO skip an earlier, already-typed element that happens to"
    )
    lines.append("        # repeat the same text (e.g. a subcommand named the same as")
    lines.append("        # the word being completed), dropping it from $cmdPath.")
    lines.append("        if ($el.Extent.EndOffset -ge $cursorPosition) { continue }")
    # Use the DEQUOTED value when the element is a literal string constant
    # (the overwhelming common case for a native command's arguments), not
    # its raw source text: a subcommand or value the user had to quote
    # (spaces, a shell metacharacter) would otherwise never match our own
    # unquoted comparison tables, since `.Extent.Text` keeps the user's
    # quote characters. Anything else (a variable, an expression) falls
    # back to the raw text, matching the previous behaviour.
    lines.append(
        "        $text = if ($el -is "
        "[System.Management.Automation.Language.StringConstantExpressionAst]) "
        "{ $el.Value } else { $el.Extent.Text }"
    )
    lines.append("        $prev = $text")
    lines.append("        if ($skip) { $skip = $false; continue }")
    lines.append("        if ($text -clike '-*') {")
    lines.append("            $vflags = @()")
    lines.append(
        "            if ($vflagsByPath.ContainsKey($cmdPath)) { $vflags = $vflagsByPath[$cmdPath] }"
    )
    lines.append("            if ($vflags -ccontains $text) { $skip = $true }")
    lines.append("            continue")
    lines.append("        }")
    lines.append("        $subs = @()")
    lines.append(
        "        if ($subsByPath.ContainsKey($cmdPath)) { $subs = $subsByPath[$cmdPath] }"
    )
    lines.append("        $ownPos = 0")
    lines.append(
        "        if ($nposByPath.ContainsKey($cmdPath)) { $ownPos = $nposByPath[$cmdPath] }"
    )
    lines.append("        if ($npos -ge $ownPos -and $subs -ccontains $text) {")
    lines.append(
        '            $cmdPath = if ($cmdPath) { "$cmdPath $text" } else { $text }'
    )
    lines.append("            $npos = 0")
    lines.append("        } else {")
    lines.append("            $npos++")
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    $candidates = @()")

    first = True
    for cspec in specs:
        key = _cmd_key(cspec)
        cond = "if" if first else "elseif"
        first = False
        lines.append(f"    {cond} ($cmdPath -ceq {_psq(key)}) {{")

        choice_opts = [o for o in cspec.options if o.takes_value and o.choices]
        other_value_opts = [o for o in cspec.options if o.takes_value and not o.choices]
        if choice_opts or other_value_opts:
            lines.append("        $matched = $false")
            lines.append("        switch -CaseSensitive -Exact ($prev) {")
            for opt in choice_opts:
                values = ", ".join(_psq(c) for c in opt.choices)
                for flag in opt.flags:
                    lines.append(
                        f"            {_psq(flag)} {{ $candidates = @({values}); $matched = $true }}"
                    )
            for opt in other_value_opts:
                # A Path flag: native file completion takes over. A free
                # (non-choice, non-Path) flag: no candidates of our own,
                # never the surrounding flags/subcommand names.
                for flag in opt.flags:
                    lines.append(
                        f"            {_psq(flag)} {{ $candidates = @(); $matched = $true }}"
                    )
            lines.append("        }")
            lines.append("        if (-not $matched) {")
            indent = "            "
        else:
            lines.append("        if ($true) {")
            indent = "            "

        lines.append(f"{indent}if ($wordToComplete -clike '-*') {{")
        flag_names = _flag_names(cspec)
        if flag_names:
            values = ", ".join(_psq(f) for f in flag_names)
            lines.append(f"{indent}    $candidates = @({values})")
        else:
            lines.append(f"{indent}    $candidates = @()")
        lines.append(f"{indent}}} else {{")
        n_pos = len(cspec.positionals)
        if n_pos:
            lines.append(f"{indent}    switch ($npos) {{")
            for idx, pos in enumerate(cspec.positionals):
                lines.append(f"{indent}        {idx} {{")
                if pos.choices:
                    values = ", ".join(_psq(c) for c in pos.choices)
                    lines.append(f"{indent}            $candidates = @({values})")
                else:
                    lines.append(f"{indent}            $candidates = @()")
                lines.append(f"{indent}        }}")
            lines.append(f"{indent}        default {{ $candidates = @() }}")
            lines.append(f"{indent}    }}")
        if cspec.subcommands:
            sub_names = ", ".join(_psq(n) for n in cspec.subcommands)
            lines.append(
                f"{indent}    if ($npos -eq {n_pos}) {{ $candidates = @({sub_names}) }}"
            )
        if not n_pos and not cspec.subcommands:
            lines.append(f"{indent}    $candidates = @()")
        lines.append(f"{indent}}}")
        lines.append("        }")
        lines.append("    }")

    lines.append("")
    lines.append("    $escaped = [regex]::Escape($wordToComplete)")
    lines.append(
        '    $candidates | Where-Object { $_ -cmatch "^$escaped" } '
        "| Sort-Object -Unique -CaseSensitive | ForEach-Object {"
    )
    lines.append("        $text = $_")
    # Always single-quote every inserted candidate, doubling both the ASCII
    # single quote and PowerShell's Unicode "smart" single-quote range
    # (U+2018-U+201B), which the tokenizer treats as equivalent quote
    # characters when it delimits a string. The old code only quoted a
    # candidate matching an ASCII metacharacter class and only doubled the
    # ASCII quote, so a candidate containing a smart quote (never in that
    # class) was inserted completely unquoted -- letting it close out of
    # the argument the moment the completed line was run. Quoting
    # unconditionally also means a bare `#` or `@` (a comment opener /
    # splat sigil at the start of a token) is never inserted unquoted
    # either, without needing its own special case.
    lines.append(
        '        $text = "\'" + ($text -replace '
        "'[''\\u2018\\u2019\\u201A\\u201B]', '$0$0') + \"'\""
    )
    lines.append(
        "        [System.Management.Automation.CompletionResult]::new("
        "$text, $_, 'ParameterValue', $_)"
    )
    lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
