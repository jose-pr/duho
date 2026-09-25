"""The env/config/instance layering pipeline.

Precedence CLI > env > config > instance-override > class default (A072).
Conversion is deliberately DEFERRED: :func:`_apply_layers`/
:func:`_stash_layer_state` only stash each class's own raw, unconverted
config-table slice / env values / instance overrides onto its parser (and
recurse into a statically declared ``_subcommands_`` tree so every node gets
its own slice); the actual env/config/instance-to-Python conversion happens
LAZILY inside :func:`_stage_layers`/:func:`_finalize_layers`, wired into
``Args._initparser_``'s patched ``parse_known_args``, so a value belonging to
a subcommand a given invocation never reaches is never even resolved (A033).
:func:`_apply_default_layers_one` is the one exception: an immediate,
non-deferred sibling for a parser with no such patched hook to defer through
(a ``duho.app`` module command's bare stdlib subparser).

Split out of ``args.py`` (A071): this pipeline, and the type->spec ladder in
``_fieldspec.py``, are the two subsystems ``duho.args`` itself and
``duho.runtime`` both depend on -- giving each its own small module (instead
of reaching into a single ~4000-line file for a handful of private names) is
what actually shrinks that file's maintainability problem. Every name here
only needs ``cls._getargs_()`` by duck typing, so this module has no import
of ``.args`` at module scope; the two spots that DO need something from
there (a class's canonical name, and the live instance-explicit-fields map)
import it lazily, function-local, to avoid a circular import (``args.py``
re-exports this module's public names).
"""

import argparse as _argparse
import logging as _logging
import os as _os
import pathlib as _pathlib
import typing as _ty

from . import parsers as _parsers

_LOGGER = _logging.getLogger(__name__)


def _raw_env_values(cls, env=None) -> "dict[str, object]":
    """{field_name: raw_string} for every declared ``NS(env=...)`` var that is
    currently set, read from `env` (a ``Mapping`` override -- e.g. a future
    ``duho.mcp`` caller resolving env vars from somewhere other than the
    process environment) when given, else ``os.environ``.

    Left UNCONVERTED on purpose (A028/A033): conversion happens later, only
    for a field the CLI turns out not to supply, inside the parser that
    actually gets reached -- see `_stage_layers`/`_finalize_layers`.

    A036: an env var set to the EMPTY string counts as unset for every field
    EXCEPT a bare scalar ``str`` field, which keeps the empty string as its
    value -- matching ``Env.list``'s own "empty means absent" rule elsewhere
    in duho (an empty ``list[Path]``/``Optional[int]`` env var must not turn
    into ``[Path('.')]`` or raise).
    """
    source = env if env is not None else _os.environ
    resolved: "dict[str, object]" = {}
    for builder in cls._getargs_():
        if not builder.env:
            continue
        raw = source.get(builder.env)
        if raw is None:
            continue
        if raw == "" and not (builder.collection is None and builder.type is str):
            continue
        resolved[builder.name] = raw
    return resolved


def _load_config(
    path: "str | _pathlib.Path",
    loader: "_ty.Callable[[_pathlib.Path], dict] | None" = None,
) -> dict:
    """Read a config file into a plain dict, dispatching on shape (F7).

    Resolution order:

    * **``loader`` hook** (``Cli._config_loader_``, if declared) -- when given, it
      is called with the expanded ``Path`` and its result used verbatim. This is
      the zero-dependency escape hatch: a user who wants YAML plugs their own
      ``yaml.safe_load`` here without duho ever importing (or depending on) it.
    * **``.json`` suffix** -- parsed with the stdlib ``json`` module (imported
      lazily, so a non-JSON config never pays its import cost). A parse error is
      re-raised as a ``ValueError`` naming the file.
    * **``.toml`` / anything else** -- parsed with stdlib ``tomllib`` (3.11+) or
      the third-party ``tomli`` IFF installed. Neither is a hard dependency (duho
      stays zero-runtime-deps); if neither is importable a clear ``RuntimeError``
      tells the user to ``pip install tomli``.

    Both JSON and TOML yield the same nested-dict shape (top-level keys -> root
    fields; a nested table/object named for a subcommand -> that subcommand's
    fields), so the layering walk is format-agnostic.
    """
    p = _pathlib.Path(path).expanduser()

    if loader is not None:
        return loader(p)

    if p.suffix.lower() == ".json":
        import json as _json  # lazy: only a JSON config pays json's import cost

        with p.open("rb") as f:
            try:
                return _json.load(f)
            except ValueError as exc:  # JSONDecodeError is a ValueError subclass
                raise ValueError(
                    f"duho: invalid JSON in config file {_os.fspath(p)}: {exc}"
                ) from exc

    try:
        import tomllib as _toml  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as _toml  # type: ignore[import-not-found,no-redef]
        except ImportError:
            raise RuntimeError(
                "duho: reading a config file requires a TOML backend. "
                "Python 3.11+ has one built in (tomllib); on earlier "
                "versions, install the optional 'tomli' package "
                "(e.g. `pip install tomli` or `pip install duho[config]`)."
            ) from None

    with p.open("rb") as f:
        return _toml.load(f)


def _raw_config_values(cls, config_table: dict) -> "dict[str, object]":
    """The subset of `config_table` naming this class's own declared fields,
    left UNCONVERTED (a native TOML/JSON value keeps its type; conversion
    happens later -- see `_stage_layers`/`_finalize_layers`). Unknown keys are
    ignored (logged at debug), for forward-compat, same as before.
    """
    if not config_table:
        return {}
    resolved: "dict[str, object]" = {}
    field_names = {b.name for b in cls._getargs_()}
    for key, raw in config_table.items():
        if key not in field_names:
            _LOGGER.debug("duho: ignoring unknown config key %r for %s", key, cls)
            continue
        resolved[key] = raw
    return resolved


def _resolve_config_dict(
    cls, config: "str | _pathlib.Path | None", *, loaded: "dict | None" = None
) -> dict:
    """Resolve a class's config table (A011/A058).

    `loaded` lets a caller that already loaded the table once (`duho.app`)
    hand it over verbatim instead of reloading it.

    Otherwise: an explicit `config` kwarg is STRICT (today's behavior -- a
    missing/invalid file raises, since the caller named it deliberately). A
    class-level `_config_` that does not exist YET is treated as `{}` with a
    DEBUG log -- the ordinary first-run state for a per-user path like
    `~/.config/myapp/config.toml` -- so it never blocks `--help`/`--version`
    on a fresh machine; its `_config_loader_` hook (if any) is not called
    either in that case.

    The loaded shape is also normalized: `None` (an empty JSON file, or a
    `_config_loader_` such as `yaml.safe_load` returning `None` for an empty
    file) becomes `{}`; anything else that is not a Mapping raises a clear
    `ValueError` naming the file, instead of an opaque `AttributeError` deep
    inside the layering walk.
    """
    if loaded is not None:
        return loaded
    explicit = config is not None
    path = config if explicit else getattr(cls, "_config_", None)
    if path is None:
        return {}
    p = _pathlib.Path(path).expanduser()
    loader = getattr(cls, "_config_loader_", None)
    if not explicit and not p.exists():
        _LOGGER.debug("duho: class config %s not found; skipping config layer", p)
        return {}
    raw = _load_config(p, loader)
    if raw is None:
        raw = {}
    if not isinstance(raw, _ty.Mapping):
        raise ValueError(
            f"duho: config file {_os.fspath(p)} must contain a table/object "
            f"at the top level, got {type(raw).__name__}"
        )
    return raw


class _LayeredDefault:
    """Placeholder installed by :func:`_stage_layers` for a field sourced from
    env, config, or an instance override -- not yet converted or validated.

    argparse's own default-reconversion (``_parse_known_args``: a ``str``
    default is run through ``type=`` again when the CLI leaves it untouched)
    only fires for ``isinstance(action.default, str)``; this object never is
    one, so a layered value is never silently run through a non-idempotent
    factory a second time (A028).

    After the real parse, whichever dest on the namespace is STILL this exact
    object (``is``, not ``==`` -- reliable even for a collection/dict field,
    which argparse / ``_CollectionAction`` / ``UpdateAction`` REPLACE outright
    rather than mutate when the CLI does supply the flag) was untouched by the
    CLI and gets converted by :func:`_finalize_layers`; anything else means
    the CLI won and the raw layered value is never even looked at (A033,
    R020 -- a bad layered value for a field the CLI already supplies, or that
    belongs to a subcommand never reached, never raises).
    """

    __slots__ = ("raw", "kind")

    def __init__(self, raw, kind: str):
        self.raw = raw
        self.kind = kind

    def __repr__(self):
        return repr(self.raw)

    def __str__(self):
        return str(self.raw)


def _stage_layers(parser: "_argparse.ArgumentParser", cls) -> None:
    """Install not-yet-converted env/config/instance placeholders on `parser`
    for `cls`'s own declared fields (Design Q2/Q4 of the layering rewrite).

    Reads the raw config-table slice / instance overrides / env mapping
    `_stash_layer_state` already attached to `parser` (empty/`None` when this
    parser was built outside `main`/`parse`/`parse_globals`/`app`, in which
    case this is a no-op, same as before). Precedence recorded here: instance
    > env > config; CLI is enforced later, for free, by whichever value
    actually ends up on the parsed namespace (see `_finalize_layers`).

    Called from :meth:`Args._initparser_`'s patched ``parse_known_args``,
    every time it runs -- lazily, so a value that belongs to a subcommand the
    user's invocation never reaches is never even resolved (A033).
    """
    config_table = getattr(parser, "_duho_raw_config_table_", None) or {}
    instance_overrides = getattr(parser, "_duho_instance_overrides_", None)
    env = getattr(parser, "_duho_env_", None)

    sources: "dict[str, str]" = {}
    placeholders: "dict[str, object]" = {}

    for name, raw in _raw_config_values(cls, config_table).items():
        placeholders[name] = _LayeredDefault(raw, "config")
        sources[name] = "config"
    for name, raw in _raw_env_values(cls, env).items():
        placeholders[name] = _LayeredDefault(raw, "env")
        sources[name] = "env"
    if instance_overrides:
        # A031: instance values already ARE final Python objects (never
        # re-converted -- see `_finalize_layers`) and outrank env/config.
        for name, raw in instance_overrides.items():
            placeholders[name] = _LayeredDefault(raw, "instance")
            sources[name] = "instance"

    # Drop any dest whose action is SUPPRESS-suppressed on this parser: that
    # dest is a root field inherited by a child parser, suppressed precisely
    # so the value the root already parsed (from an option given BEFORE the
    # subcommand) survives. Installing a placeholder default here would
    # overwrite the SUPPRESS marker and clobber that parsed value (C3) -- the
    # root parser's own staging already covers the real (root) field.
    if placeholders:
        actions_by_dest = {action.dest: action for action in parser._actions}
        for name in list(placeholders):
            action = actions_by_dest.get(name)
            if action is not None and action.default is _argparse.SUPPRESS:
                del placeholders[name]
                sources.pop(name, None)

    if placeholders:
        parser.set_defaults(**placeholders)
        for action in parser._actions:
            if action.dest in placeholders:
                action.required = False
        # A032 (part 1): a layered/instance value also un-requires the WHOLE
        # conflicts= group it belongs to -- argparse tracks a mutex group's
        # requiredness on the GROUP object, not on the member action, so
        # un-requiring only the action (above) is not enough.
        builders_by_name = {b.name: b for b in cls._getargs_()}
        layered_conflicts = {
            getattr(builders_by_name[n], "conflicts", None)
            for n in placeholders
            if getattr(builders_by_name.get(n), "conflicts", None)
        }
        if layered_conflicts:
            for key, group in (getattr(parser, "exclusive_groups", None) or {}).items():
                group_key = key[1] if isinstance(key, tuple) else key
                if group_key in layered_conflicts:
                    group.required = False

    parser._duho_value_sources_ = sources  # type: ignore[attr-defined]
    parser._duho_merged_defaults_ = {}  # type: ignore[attr-defined]
    parser._duho_placeholders_ = placeholders  # type: ignore[attr-defined]
    parser._duho_builders_ = {b.name: b for b in cls._getargs_()}  # type: ignore[attr-defined]


def _finalize_layers(parser: "_argparse.ArgumentParser", cls, parsed) -> None:
    """Convert whichever placeholders `_stage_layers` installed are still
    untouched on `parsed`, drop one whose ``conflicts=`` sibling was given a
    value on the CLI instead (A032 part 2), and report a bad value the same
    way argparse reports a bad CLI one -- ``parser.error(...)`` (usage text +
    exit 2), never a raw traceback (R020) -- for THIS parser only, i.e. only
    once the user's invocation actually reached it (A033).
    """
    placeholders: "dict[str, object]" = (
        getattr(parser, "_duho_placeholders_", None) or {}
    )
    if not placeholders:
        return
    builders_by_name = getattr(parser, "_duho_builders_", None) or {
        b.name: b for b in cls._getargs_()
    }
    sources: "dict[str, str]" = parser._duho_value_sources_  # type: ignore[attr-defined]

    untouched: "dict[str, _LayeredDefault]" = {}
    for name, placeholder in placeholders.items():
        if getattr(parsed, name, None) is placeholder:
            untouched[name] = placeholder

    # A032 (part 2): an untouched layered member whose conflicts= sibling WAS
    # given a value on the CLI loses to that sibling -- the group's
    # exactly-one invariant wins over a stale env/config value. The sibling
    # itself need not be layered at all (e.g. `--token-file f` with no
    # NS(env=...) of its own), so "given" is checked against EVERY member of
    # a relevant group, not just ones `_stage_layers` installed a placeholder
    # for: identity against its own placeholder when it has one, else the
    # same value-vs-effective-default heuristic `value_sources` itself uses.
    relevant_conflicts = {
        getattr(builders_by_name[n], "conflicts", None) for n in untouched
    } - {None}
    if relevant_conflicts:
        given_conflicts: "set[object]" = set()
        for name, builder in builders_by_name.items():
            conflicts = getattr(builder, "conflicts", None)
            if conflicts not in relevant_conflicts or name in untouched:
                continue
            sibling_placeholder = placeholders.get(name)
            is_given = (
                getattr(parsed, name, None) is not sibling_placeholder
                if sibling_placeholder is not None
                else getattr(parsed, name, None) != builder._effective_default_()
            )
            if is_given:
                given_conflicts.add(conflicts)
        for name in list(untouched):
            conflicts = getattr(builders_by_name[name], "conflicts", None)
            if conflicts in given_conflicts:
                del untouched[name]
                setattr(parsed, name, builders_by_name[name]._effective_default_())
                sources.pop(name, None)

    merged: "dict[str, object]" = {}
    for name, placeholder in untouched.items():
        builder = builders_by_name[name]
        if placeholder.kind == "instance":
            value = placeholder.raw
        else:
            try:
                value = builder.convert_layered(
                    placeholder.raw, source=placeholder.kind
                )
            except (TypeError, ValueError) as exc:
                what = (
                    f"environment variable {builder.env!r} for field {name!r}"
                    if placeholder.kind == "env"
                    else f"config value for field {name!r} on {cls.__name__}"
                )
                parser.error(f"{what}: invalid value {placeholder.raw!r} ({exc})")
                return  # pragma: no cover - parser.error always raises SystemExit
        setattr(parsed, name, value)
        merged[name] = value

    # `.update`, never reassign: a child parser's own `_finalize_layers` may
    # already have merged ITS results into this same dict via
    # `_merge_layers_upward` (children finish before a parent's own finalize
    # runs, since the real parse of a nested selection happens INSIDE this
    # parser's own `_argparse.ArgumentParser.parse_known_args` call, above) --
    # reassigning here would silently wipe that out (R021).
    parser._duho_merged_defaults_.update(merged)  # type: ignore[attr-defined]


def _merge_layers_upward(parser: "_argparse.ArgumentParser") -> None:
    """Merge THIS parser's own (already-finalized) provenance/values/builders
    up into its parent's, one level, if it has one.

    `_duho_parent_parser_` is set at BUILD time (`Args._parser_`'s
    `_subcommands_` loop; `runtime._register_class_command` for `duho.app`)
    for a genuine parent/child pair. Since a NON-selected sibling's own
    `parse_known_args` never runs at all (argparse only invokes the chosen
    subparser), this merge only ever happens for parsers actually reached
    during dispatch -- fixing A037's "last sibling in declaration order wins"
    (the old whole-tree merge happened eagerly, at BUILD time, across every
    sibling regardless of selection). By the time the ROOT's own closure
    resumes after the full recursive parse, its own maps hold the union of
    the whole SELECTED chain, which is what `value_sources` (via
    `_duho_last_parser_`, stashed as the root's `parser`) reads -- also
    giving a subcommand instance its inherited root fields for free (R021).
    """
    parent = getattr(parser, "_duho_parent_parser_", None)
    if parent is None:
        return
    parent_sources = getattr(parent, "_duho_value_sources_", None)
    if parent_sources is None:
        parent_sources = {}
        parent._duho_value_sources_ = parent_sources  # type: ignore[attr-defined]
    parent_sources.update(getattr(parser, "_duho_value_sources_", None) or {})

    parent_merged = getattr(parent, "_duho_merged_defaults_", None)
    if parent_merged is None:
        parent_merged = {}
        parent._duho_merged_defaults_ = parent_merged  # type: ignore[attr-defined]
    parent_merged.update(getattr(parser, "_duho_merged_defaults_", None) or {})

    parent_builders = getattr(parent, "_duho_builders_", None)
    if parent_builders is None:
        parent_builders = {}
        parent._duho_builders_ = parent_builders  # type: ignore[attr-defined]
    parent_builders.update(getattr(parser, "_duho_builders_", None) or {})


def _stash_layer_state(
    parser: "_argparse.ArgumentParser",
    cls,
    config_table: "dict | None",
    instance_overrides: "dict | None" = None,
    env=None,
) -> None:
    """Attach `cls`'s own config-table slice (and, for the ROOT call, any
    instance overrides / env mapping) to `parser`, then recurse into a
    STATICALLY declared ``_subcommands_`` tree so every node gets its own
    slice -- cheaply (pure dict slicing, so unlike the old whole-tree
    `_apply_default_layers` walk this replaces, it can never raise).

    The actual layering (env/config/instance conversion, choices/conflicts
    checks) happens LAZILY, inside each node's own `_initparser_`-patched
    ``parse_known_args`` (`_stage_layers`/`_finalize_layers`), so only the
    parser chain a given invocation actually reaches ever runs it (A033).
    `duho.app` stashes its dynamically-resolved commands the same way, from
    `runtime._apply_app_config_layers`.
    """
    parser._duho_raw_config_table_ = config_table or {}  # type: ignore[attr-defined]
    parser._duho_instance_overrides_ = instance_overrides  # type: ignore[attr-defined]
    parser._duho_env_ = env  # type: ignore[attr-defined]

    subcommands = getattr(cls, "_subcommands_", None)
    if not subcommands:
        return
    subparsers_action = _parsers.find_subparsers(parser)
    if subparsers_action is None:
        return
    from .args import _command_name  # lazy: avoids a circular import (args.py

    # re-exports this module's own public names)
    choices = subparsers_action.choices or {}
    for sub in subcommands:
        sub_name = _command_name(sub)
        sub_parser = choices.get(sub_name)
        if sub_parser is None:
            continue
        sub_table = config_table.get(sub_name) if config_table else None
        sub_table = sub_table if isinstance(sub_table, dict) else {}
        # Instance overrides and an explicit env mapping are root-only; a
        # nested subcommand only ever gets its own config-table slice.
        _stash_layer_state(sub_parser, sub, sub_table)


def _apply_layers(
    parser: "_argparse.ArgumentParser",
    cls,
    *,
    env: "_ty.Mapping[str, str] | None" = None,
    config: "str | _pathlib.Path | dict | None" = None,
    instance: object = None,
) -> dict:
    """The one env/config/instance layering entry point (A072), used by
    `duho.main`, `duho.parse`, `duho.parse_globals` (A015), and `duho.app`
    (also exposed for a future `duho.mcp` caller).

    Resolves `config` once (a path/None via `_resolve_config_dict`, or an
    already-loaded dict, as `duho.app` passes having loaded it itself),
    computes `instance`'s EXPLICITLY-passed field overrides (A031 -- a
    placeholder `Args.__init__` seeded for a field the caller never actually
    set is not an override), and stashes both (plus `env`, an optional
    Mapping override for where `NS(env=...)` vars are read from) across
    `cls`'s static `_subcommands_` tree via `_stash_layer_state`. Actual
    conversion is deferred -- see `_stage_layers`/`_finalize_layers` for why
    and how (wired into `Args._initparser_`'s patched ``parse_known_args``).

    Returns the resolved config dict, so a caller that also needs it for its
    own (non-static-tree) bookkeeping -- `duho.app`, whose commands are not
    reachable via `cls._subcommands_` -- does not have to load it twice.
    """
    raw_config = (
        config if isinstance(config, _ty.Mapping) else _resolve_config_dict(cls, config)
    )
    overrides = None
    if instance is not None:
        from .args import (  # lazy: avoids a circular import (args.py
            _duho_explicit_instance_fields,
        )

        # re-exports this module's own public names)
        field_names = {b.name for b in type(instance)._getargs_()}
        touched = _duho_explicit_instance_fields.get(id(instance))
        overrides = {
            name: value
            for name, value in vars(instance).items()
            if name in field_names and (touched is None or name in touched)
        }
    _stash_layer_state(parser, cls, raw_config, overrides, env)
    return raw_config


def _apply_default_layers_one(
    parser: "_argparse.ArgumentParser", cls, config_table: dict
) -> None:
    """Apply env/config layers to a single parser IMMEDIATELY (not deferred).

    Used where no `_initparser_`-patched ``parse_known_args`` hook exists to
    defer conversion through -- `duho.app`'s MODULE commands (D021/D022),
    whose subparser is deliberately a bare stdlib one (see `runtime`'s module
    docstring), so `_stage_layers`/`_finalize_layers` never run for it. Shares
    `convert_layered`'s choices check (A008), the empty-env-is-unset rule
    (A036), and the conflicts-group un-require (A032 part 1) with the lazy
    path used everywhere else; it does NOT defer conversion, so a bad value
    here still raises before parsing -- a narrower, known gap than the
    class-command path, which has no such seam available.
    """
    builders_by_name = {b.name: b for b in cls._getargs_()}
    sources: "dict[str, str]" = {}
    merged: "dict[str, object]" = {}

    for name, raw in _raw_config_values(cls, config_table).items():
        try:
            merged[name] = builders_by_name[name].convert_layered(raw, source="config")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"config value for field {name!r} on {cls.__name__}: "
                f"invalid value {raw!r} ({exc})"
            ) from exc
        sources[name] = "config"

    for name, raw in _raw_env_values(cls).items():
        builder = builders_by_name[name]
        try:
            merged[name] = builder.convert_layered(raw, source="env")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"environment variable {builder.env!r} for field {name!r}: "
                f"invalid value {raw!r} ({exc})"
            ) from exc
        sources[name] = "env"

    if merged:
        actions_by_dest = {action.dest: action for action in parser._actions}
        for name in list(merged):
            action = actions_by_dest.get(name)
            if action is not None and action.default is _argparse.SUPPRESS:
                del merged[name]
                sources.pop(name, None)

    if merged:
        parser.set_defaults(**merged)
        for action in parser._actions:
            if action.dest in merged:
                action.required = False
        layered_conflicts = {
            getattr(builders_by_name[n], "conflicts", None)
            for n in merged
            if getattr(builders_by_name.get(n), "conflicts", None)
        }
        if layered_conflicts:
            for key, group in (getattr(parser, "exclusive_groups", None) or {}).items():
                group_key = key[1] if isinstance(key, tuple) else key
                if group_key in layered_conflicts:
                    group.required = False

    parser._duho_value_sources_ = sources  # type: ignore[attr-defined]
    parser._duho_merged_defaults_ = merged  # type: ignore[attr-defined]


def value_sources(parsed) -> "dict[str, str]":
    """Report the origin layer ("cli", "env", "config", "instance", or
    "default") of each field on a parsed instance produced by
    `duho.parse`/`duho.main`.

    Looks up the owning parser via the per-class `_duho_last_parser_`
    linkage stashed during dispatch (see `_initparser_`), read through
    ``type(parsed).__dict__`` rather than ``getattr`` (A038) -- ``getattr``
    follows the MRO, so a never-parsed SUBCLASS of an already-parsed base (or
    of bare ``Args``, parsed every time `duho.app` runs without a root) would
    otherwise inherit its base's stale parser and report bogus sources
    instead of the documented ``{}``. Returns `{}` when unavailable (e.g. the
    instance wasn't produced via a parser built by this framework, or no
    parse has happened yet for its class).

    For a SUBCOMMAND instance, root/global fields are included too (R021):
    `_merge_layers_upward` folds each actually-selected parser's own
    provenance/builders up into its parent's as dispatch unwinds, so by the
    time the root's own `_duho_last_parser_` linkage is read here it already
    holds the whole selected chain, not just the deepest class's own fields.

    A field is "cli" if its parsed value differs from the effective default
    that was in effect for that parse -- the merged env/config/instance value
    when the field was touched by one of those layers, else the class
    default. Otherwise it's whatever layer contributed that default
    ("env"/"config"/"instance"), or "default" if no layer touched it (value
    == the untouched class default).
    """
    parser = type(parsed).__dict__.get("_duho_last_parser_")
    if parser is None:
        return {}
    sources: "dict[str, str]" = getattr(parser, "_duho_value_sources_", None) or {}
    merged: "dict[str, object]" = getattr(parser, "_duho_merged_defaults_", None) or {}
    builders: "dict[str, object]" = dict(getattr(parser, "_duho_builders_", None) or {})
    builders.update({b.name: b for b in type(parsed)._getargs_()})

    result: "dict[str, str]" = {}
    for name, builder in builders.items():
        if not hasattr(parsed, name):
            continue
        value = getattr(parsed, name)
        if name in merged:
            effective_default = merged[name]
            layer = sources.get(name, "default")
        else:
            # Use the builder's EFFECTIVE default (e.g. False for an undeclared
            # store_true bool), not the raw declared default (NOT_DEFINED), or a
            # field left at its argparse default is mislabeled "cli" (C14).
            effective_default = builder._effective_default_()
            layer = "default"
        result[name] = layer if value == effective_default else "cli"
    return result
