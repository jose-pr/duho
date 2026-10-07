"""The env/config/instance layering pipeline.

Precedence CLI > instance-override > env > config > class default. Conversion
is deferred: :func:`_apply_layers` and :func:`_stash_layer_state` only stash
each class's raw config slice, env values and instance overrides on its parser
(recursing through a static ``_subcommands_`` tree); :func:`_stage_layers` and
:func:`_finalize_layers`, wired into ``Args._initparser_``'s patched
``parse_known_args``, convert them, so a value for a subcommand that is never
reached is never resolved. :func:`_apply_default_layers_one` converts at once,
for a parser with no such hook.

This module needs ``cls._getargs_()`` only by duck typing and imports ``.args``
inside functions, because ``duho.args`` re-exports this module's public names.
"""

from __future__ import annotations

import argparse as _argparse
import logging as _logging
import os as _os
import pathlib as _pathlib
import typing as _ty

from . import parsers as _parsers
from ._introspect import NOT_DEFINED
from ._fieldspec import _AppendAction as _AppendAction
from ._fieldspec import _CollectionAction as _CollectionAction
from ._fieldspec import _LayeredChoiceError as _LayeredChoiceError
from ._fieldspec import _NegatedBoolAction as _NegatedBoolAction
from ._fieldspec import UpdateAction as UpdateAction

_LOGGER = _logging.getLogger(__name__)


def _raw_env_values(cls, env=None) -> dict[str, object]:
    """{field_name: raw_string} for every declared ``Meta(env=...)`` var that is
    set, read from `env` (a ``Mapping``) when given, else ``os.environ``.

    Values stay unconverted; `_stage_layers` converts only fields the CLI does
    not supply. An empty string counts as unset, except for a bare ``str``
    field, which keeps it (as ``Env.list`` does, so an empty ``list[Path]`` var
    does not become ``[Path('.')]``).
    """
    source = env if env is not None else _os.environ
    resolved: dict[str, object] = {}
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


class _TomlBackendMissing(RuntimeError):
    """No TOML reader (``tomllib``/``tomli``) is importable."""


def _load_config(
    path: str | _pathlib.Path,
    loader: _ty.Callable[[_pathlib.Path], dict] | None = None,
) -> dict:
    """Read a config file into a plain dict.

    A `loader` (``_config_loader_``) is called with the expanded path and its
    result used verbatim. Otherwise ``.json`` goes through ``json`` (a parse
    error becomes a ``ValueError`` naming the file) and anything else through
    ``tomllib`` or ``tomli``; with neither importable it raises
    ``_TomlBackendMissing``. Both formats give the same nested-dict shape: top
    keys are root fields, a table named for a subcommand holds its fields.
    """
    p = _pathlib.Path(path).expanduser()

    if loader is not None:
        try:
            return loader(p)
        except ValueError as exc:
            # The application's own loader, the application's own error: marked
            # so `_resolve_config_or_error` lets it through unchanged.
            try:
                exc._duho_from_loader_ = True  # type: ignore[attr-defined]
            except AttributeError:
                pass
            raise

    if p.suffix.lower() == ".json":
        import json as _json  # lazy: only a JSON config pays json's import cost

        with p.open("rb") as f:
            try:
                return _json.load(f)
            except ValueError as exc:  # JSONDecodeError is a ValueError subclass
                raise ValueError(
                    f"duho: invalid JSON in config file {_os.fspath(p)}: {exc}"
                ) from None

    try:
        import tomllib as _toml  # type: ignore[import-not-found]
    except ImportError:
        try:
            import tomli as _toml  # type: ignore[import-not-found,no-redef]
        except ImportError:
            raise _TomlBackendMissing(
                "duho: reading a config file requires a TOML backend. "
                "Python 3.11+ has one built in (tomllib); on earlier "
                "versions, install the optional 'tomli' package "
                "(e.g. `pip install tomli` or `pip install duho[config]`)."
            ) from None

    with p.open("rb") as f:
        try:
            return _toml.load(f)
        except _toml.TOMLDecodeError as exc:
            raise ValueError(
                f"duho: invalid TOML in config file {_os.fspath(p)}: {exc}"
            ) from None


def _raw_config_values(cls, config_table: dict) -> dict[str, object]:
    """The subset of `config_table` naming this class's declared fields,
    unconverted (a native TOML/JSON value keeps its type). Unknown keys are
    ignored with a debug log, so one file can serve several versions.
    """
    if not config_table:
        return {}
    resolved: dict[str, object] = {}
    field_names = {b.name for b in cls._getargs_()}
    for key, raw in config_table.items():
        if key not in field_names:
            _LOGGER.debug("duho: ignoring unknown config key %r for %s", key, cls)
            continue
        resolved[key] = raw
    return resolved


def _resolve_config_dict(
    cls, config: str | _pathlib.Path | None, *, loaded: dict | None = None
) -> dict:
    """Resolve a class's config table.

    `loaded` is handed back verbatim (``duho.app`` loaded it already). An
    explicit `config` is strict: a missing or invalid file raises. A class
    `_config_` that does not exist yet is ``{}`` with a debug log (the normal
    first run), so it never blocks ``--help``, and its loader is not called.
    ``None`` from a loader becomes ``{}``; any other non-Mapping raises a
    ``ValueError`` naming the file.
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


def _resolve_config_or_error(
    parser: _argparse.ArgumentParser, cls, config: str | _pathlib.Path | None
) -> dict:
    """:func:`_resolve_config_dict`, reporting an unreadable or malformed
    config through ``parser.error`` (usage line, exit 2).

    A ``ValueError`` from the class's own ``_config_loader_`` propagates
    unchanged. A missing TOML backend is held on the parser and reported by
    :func:`_finalize_layers`, so ``--help`` and ``--version`` still work.
    """
    parser._duho_config_error_ = None  # type: ignore[attr-defined]
    try:
        return _resolve_config_dict(cls, config)
    except _TomlBackendMissing as exc:
        parser._duho_config_error_ = str(exc)  # type: ignore[attr-defined]
        return {}
    except ValueError as exc:
        if getattr(exc, "_duho_from_loader_", False):
            raise
        parser.error(str(exc))
        raise  # pragma: no cover - parser.error always raises SystemExit


class _LayeredDefault:
    """Placeholder installed by :func:`_stage_layers` for a field sourced from
    env, config or an instance override, not yet converted.

    It is never a ``str``, so argparse does not re-run it through ``type=``.
    After the parse, a dest that is still this exact object (``is``, since
    argparse replaces rather than mutates it) was untouched by the CLI and
    :func:`_finalize_layers` converts it; otherwise the CLI won and the raw
    value is never looked at.
    """

    __slots__ = ("raw", "kind")

    def __init__(self, raw, kind: str):
        self.raw = raw
        self.kind = kind

    def __repr__(self):
        return repr(self.raw)

    def __str__(self):
        return str(self.raw)


#: Actions whose CLI occurrence replaces the namespace value outright, so an
#: unconverted `_LayeredDefault` is safe (`_AppendAction` and `_CollectionAction`
#: keep elements on a sidecar). An allow-list: unknown actions accumulate.
_REPLACE_SEMANTICS_ACTION_TYPES = (
    _argparse._StoreAction,
    _argparse._StoreConstAction,  # covers store_true/store_false (subclasses)
    _argparse.BooleanOptionalAction,
    _CollectionAction,
    _AppendAction,
    _NegatedBoolAction,
    UpdateAction,
)


def _is_replace_semantics_action(action) -> bool:
    """True when `action`'s CLI occurrence replaces its dest outright.

    False for stdlib count/append/extend/append_const, which accumulate onto
    the namespace value and would fail on a `_LayeredDefault` (``-v`` with a
    config ``verbose = 1`` raises ``TypeError``), and for any action not in the
    allow-list above.
    """
    return isinstance(action, _REPLACE_SEMANTICS_ACTION_TYPES)


def _bound_lookup_choices_desc(factory) -> str | None:
    """Describe what a bound lookup factory (``Meta(type=MAPPING.__getitem__)`` or
    ``.get``) accepts, instead of its internal name.

    A mapping of up to 20 keys lists them sorted by ``repr`` (so unorderable
    keys never raise); a larger one names its type. Never echoes a value or the
    rejected input. ``None`` for any other factory.
    """
    if getattr(factory, "__name__", None) not in ("__getitem__", "get"):
        return None
    owner = getattr(factory, "__self__", None)
    if owner is None:
        return None
    try:
        keys = list(owner.keys())
    except (AttributeError, TypeError):
        return None
    if not keys:
        return None
    if len(keys) <= 20:
        ordered = sorted(keys, key=repr)
        shown = ", ".join(k if isinstance(k, str) else repr(k) for k in ordered)
        return f"one of: {shown}"
    return f"a key of {type(owner).__name__}"


def _field_type_desc(builder) -> str:
    """A short, non-sensitive description of the type a layered value for
    `builder` must convert to -- for an error message that names the
    field/variable but never echoes the malformed raw value or the
    conversion exception's own text: either one can itself carry whatever
    secret the env var or config value held, printed right back onto the
    CLI's stderr or an MCP ``isError`` result.
    """
    if builder.collection is dict:
        return "a KEY=VALUE mapping"
    if builder.collection is not None:
        elem = getattr(builder.type, "__name__", None) or "value"
        return f"a {builder.collection.__name__} of {elem}"
    lookup = _bound_lookup_choices_desc(builder.type)
    if lookup is not None:
        return lookup
    return getattr(builder.type, "__name__", None) or "value"


def _layered_error_detail(builder, exc: Exception) -> str:
    """The redacted detail half of a layered conversion-failure message.

    A :class:`_LayeredChoiceError` (raised by ``ArgumentBuilder.
    _check_layered_choices``) already carries a safe, value-free "invalid
    choice" message -- the SAME wording the CLI itself gives for a bad
    Literal/``Choice(...)`` value -- so it is used AS-IS. Any other
    conversion failure falls back to the generic "expected <type>" text:
    its own message/args could themselves carry whatever secret the env
    var or config value held, so it is never echoed.
    """
    if isinstance(exc, _LayeredChoiceError):
        return str(exc)
    return f"expected {_field_type_desc(builder)}"


def _layered_failure_message(builder, exc: Exception, kind: str, cls) -> str:
    """The one message for a layered (env/config) value that failed to convert.

    Neither the raw value nor a generic conversion exception's own text is
    echoed -- either can carry a secret (see :func:`_layered_error_detail`).
    """
    what = (
        f"environment variable {builder.env!r} for field {builder.name!r}"
        if kind == "env"
        else f"config value for field {builder.name!r} on {cls.__name__}"
    )
    return f"{what}: {_layered_error_detail(builder, exc)}"


def _convert_layered_or_error(parser, builder, raw, kind: str, cls):
    """Convert one layered value, reporting any failure through
    ``parser.error``.

    Catches ``Exception``: a field's own ``type=`` factory is caller code and
    can raise anything (``argparse.ArgumentTypeError`` is not a
    ``ValueError``; a mapping lookup raises ``KeyError``). The error is raised
    outside the ``except`` block so the failing exception, which can carry the
    raw value, is never chained onto it.
    """
    try:
        return builder.convert_layered(raw, source=kind)
    except Exception as exc:
        message = _layered_failure_message(builder, exc, kind, cls)
    parser.error(message)
    raise AssertionError("parser.error returned")  # pragma: no cover


def _restore_prior_layer_state(parser: _argparse.ArgumentParser) -> None:
    """Undo what the previous `_stage_layers` call on this reused parser did to
    its actions' and groups' ``default``/``required``.

    Without it a dest whose env var is unset on a later ``parse_args()`` would
    keep the stale placeholder and ``required=False``, and a required field
    would never re-raise.
    """
    prior_actions: dict[str, tuple[object, object]] | None = getattr(
        parser, "_duho_prior_action_state_", None
    )
    if prior_actions:
        actions_by_dest = {action.dest: action for action in parser._actions}
        for name, (default, required) in prior_actions.items():
            action = actions_by_dest.get(name)
            if action is not None:
                action.default = default
                action.required = required
    prior_groups: dict[object, bool] | None = getattr(
        parser, "_duho_prior_group_state_", None
    )
    if prior_groups:
        for key, group in (getattr(parser, "exclusive_groups", None) or {}).items():
            group_key = key[1] if isinstance(key, tuple) else key
            if group_key in prior_groups:
                group.required = prior_groups[group_key]


def _stage_layers(parser: _argparse.ArgumentParser, cls) -> None:
    """Install not-yet-converted env/config/instance placeholders on `parser`
    for `cls`'s own declared fields.

    Reads the slices `_stash_layer_state` attached; a parser built outside
    `main`/`parse`/`parse_globals`/`app` has none and layers ``os.environ`` and
    the class's own `_config_`. Precedence here is instance > env > config; the
    CLI wins because its value replaces the placeholder on the namespace. A
    field whose action accumulates (`_is_replace_semantics_action`) is converted
    eagerly, so the CLI action adds to a converted value.
    """
    _restore_prior_layer_state(parser)

    config_table = getattr(parser, "_duho_raw_config_table_", None)
    if config_table is None:
        # Not stashed: a parser built outside main/parse/parse_globals/app
        # resolves the class's own `_config_`, as every entry point does.
        config_table = _resolve_config_or_error(parser, cls, None)
    instance_overrides = getattr(parser, "_duho_instance_overrides_", None)
    env = getattr(parser, "_duho_env_", None)

    # name -> (raw, kind); later writes win: instance > env > config.
    raw_by_name: dict[str, tuple[object, str]] = {}
    for name, raw in _raw_config_values(cls, config_table).items():
        raw_by_name[name] = (raw, "config")
    for name, raw in _raw_env_values(cls, env).items():
        raw_by_name[name] = (raw, "env")
    if instance_overrides:
        # Instance values already ARE final Python objects (never
        # re-converted -- see `_finalize_layers`) and outrank env/config.
        for name, raw in instance_overrides.items():
            raw_by_name[name] = (raw, "instance")

    actions_by_dest = {action.dest: action for action in parser._actions}
    builders_by_name = {b.name: b for b in cls._getargs_()}

    sources: dict[str, str] = {}
    placeholders: dict[str, object] = {}
    eager: dict[str, object] = {}
    for name, (raw, kind) in raw_by_name.items():
        action = actions_by_dest.get(name)
        # Skip a dest SUPPRESS-suppressed here: it is a root field inherited by
        # a child parser, and a default would clobber the value the root parsed
        # from an option given before the subcommand.
        if action is not None and action.default is _argparse.SUPPRESS:
            continue
        sources[name] = kind
        if action is not None and _is_replace_semantics_action(action):
            placeholders[name] = _LayeredDefault(raw, kind)
            continue
        if kind == "instance":
            eager[name] = raw
            continue
        eager[name] = _convert_layered_or_error(
            parser, builders_by_name[name], raw, kind, cls
        )

    touched = {**placeholders, **eager}
    prior_actions: dict[str, tuple[object, object]] = {}
    prior_groups: dict[object, bool] = {}
    if touched:
        for name in touched:
            action = actions_by_dest.get(name)
            if action is not None:
                prior_actions[name] = (action.default, action.required)
        parser.set_defaults(**touched)
        for action in parser._actions:
            if action.dest in touched:
                action.required = False
        # argparse tracks a mutex group's requiredness on the group, so a
        # layered value un-requires the whole conflicts= group too.
        layered_conflicts = {
            getattr(builders_by_name[n], "conflicts", None)
            for n in touched
            if getattr(builders_by_name.get(n), "conflicts", None)
        }
        if layered_conflicts:
            for key, group in (getattr(parser, "exclusive_groups", None) or {}).items():
                group_key = key[1] if isinstance(key, tuple) else key
                if group_key in layered_conflicts and group_key not in prior_groups:
                    prior_groups[group_key] = group.required
                    group.required = False

    parser._duho_value_sources_ = sources  # type: ignore[attr-defined]
    parser._duho_merged_defaults_ = dict(eager)  # type: ignore[attr-defined]
    parser._duho_placeholders_ = placeholders  # type: ignore[attr-defined]
    parser._duho_builders_ = builders_by_name  # type: ignore[attr-defined]
    parser._duho_prior_action_state_ = prior_actions  # type: ignore[attr-defined]
    parser._duho_prior_group_state_ = prior_groups  # type: ignore[attr-defined]


def _finalize_layers(parser: _argparse.ArgumentParser, cls, parsed) -> None:
    """Convert whichever placeholders `_stage_layers` installed are still
    untouched on `parsed`, drop one whose ``conflicts=`` sibling was given a
    value on the CLI instead (part 2), and report a bad value the same
    way argparse reports a bad CLI one -- ``parser.error(...)`` (usage text +
    exit 2), never a raw traceback -- for THIS parser only, i.e. only
    once the user's invocation actually reached it.
    """
    config_error = getattr(parser, "_duho_config_error_", None)
    if config_error:
        parser.error(config_error)
    placeholders: dict[str, object] = getattr(parser, "_duho_placeholders_", None) or {}
    if not placeholders:
        return
    builders_by_name = getattr(parser, "_duho_builders_", None) or {
        b.name: b for b in cls._getargs_()
    }
    sources: dict[str, str] = parser._duho_value_sources_  # type: ignore[attr-defined]

    untouched: dict[str, _LayeredDefault] = {}
    for name, placeholder in placeholders.items():
        if getattr(parsed, name, None) is placeholder:
            untouched[name] = placeholder

    # An untouched layered member loses to a conflicts= sibling given on the
    # CLI. The sibling need not be layered: check identity against its
    # placeholder, else value against the effective default.
    relevant_conflicts = {
        getattr(builders_by_name[n], "conflicts", None) for n in untouched
    } - {None}
    if relevant_conflicts:
        given_conflicts: set[object] = set()
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

    merged: dict[str, object] = {}
    for name, placeholder in untouched.items():
        builder = builders_by_name[name]
        if placeholder.kind == "instance":
            value = placeholder.raw
        else:
            value = _convert_layered_or_error(
                parser, builder, placeholder.raw, placeholder.kind, cls
            )
        setattr(parsed, name, value)
        merged[name] = value

    # `.update`, never reassign: a child's `_finalize_layers` already merged
    # into this dict via `_merge_layers_upward` (children finish first).
    parser._duho_merged_defaults_.update(merged)  # type: ignore[attr-defined]


def _merge_layers_upward(parser: _argparse.ArgumentParser) -> None:
    """Merge this parser's finalized provenance, values and builders into its
    parent's, one level.

    `_duho_parent_parser_` is set at build time. A sibling that was not
    selected never parses, so only the selected chain merges; the root then
    holds the whole chain's maps, which `value_sources` reads.
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
    parser: _argparse.ArgumentParser,
    cls,
    config_table: dict | None,
    instance_overrides: dict | None = None,
    env=None,
) -> None:
    """Attach `cls`'s config-table slice (for the root call also the instance
    overrides and env mapping) to `parser`, then recurse through the static
    ``_subcommands_`` tree. Pure dict slicing, so it cannot raise.

    Conversion happens later in each node's patched ``parse_known_args``
    (`_stage_layers`/`_finalize_layers`); `duho.app` stashes its dynamic
    commands the same way from `runtime._apply_app_config_layers`.
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
    from .args._naming import _command_name  # lazy: avoids a circular import (duho.args

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


def _values_differ(a: object, b: object) -> bool:
    try:
        return bool(a != b)
    except Exception:
        return True


def _instance_overrides(instance: object) -> dict[str, object]:
    """The field values of `instance` that count as set by its caller.

    A field counts when it was passed to the constructor or when its value
    differs from what the constructor seeded. An instance with no record (a
    copy, or a class that cannot be weak-referenced) counts a field when its
    value differs from the field's effective default.
    """
    from .args._argsclass import (  # lazy: avoids a circular import
        _duho_explicit_instance_fields,
        _duho_seeded_instance_values,
    )

    explicit = _duho_explicit_instance_fields.get(id(instance))
    seeded = _duho_seeded_instance_values.get(id(instance))
    overrides: dict[str, object] = {}
    values = vars(instance)
    for builder in type(instance)._getargs_():
        name = builder.name
        if name not in values:
            continue
        value = values[name]
        if explicit is not None and name in explicit:
            overrides[name] = value
            continue
        if seeded is not None:
            baseline = seeded.get(name, NOT_DEFINED)
        else:
            baseline = builder._effective_default_()
        if baseline is NOT_DEFINED or _values_differ(value, baseline):
            overrides[name] = value
    return overrides


def _apply_layers(
    parser: _argparse.ArgumentParser,
    cls,
    *,
    env: _ty.Mapping[str, str] | None = None,
    config: str | _pathlib.Path | dict | None = None,
    instance: object = None,
) -> dict:
    """The one layering entry point, used by `duho.main`, `duho.parse`,
    `duho.parse_globals` and `duho.app`.

    Resolves `config` once (a path or None, or an already-loaded dict), takes
    `instance`'s overrides (`_instance_overrides`) and stashes both, plus
    `env`, across `cls`'s static ``_subcommands_`` tree; conversion is deferred.
    Returns the resolved config dict so `duho.app` need not load it twice.
    """
    raw_config = (
        config
        if isinstance(config, _ty.Mapping)
        else _resolve_config_or_error(parser, cls, config)
    )
    overrides = None
    if instance is not None:
        overrides = _instance_overrides(instance)
    _stash_layer_state(parser, cls, raw_config, overrides, env)
    return raw_config


def _apply_default_layers_one(
    parser: _argparse.ArgumentParser, cls, config_table: dict
) -> None:
    """Apply env/config layers to one parser immediately, not deferred.

    For `duho.app`'s module commands, whose subparser is a bare stdlib one with
    no patched ``parse_known_args`` to defer through. A bad value therefore
    raises before parsing, unlike the class-command path.
    """
    builders_by_name = {b.name: b for b in cls._getargs_()}
    sources: dict[str, str] = {}
    merged: dict[str, object] = {}

    for name, raw in _raw_config_values(cls, config_table).items():
        builder = builders_by_name[name]
        failure = None
        try:
            merged[name] = builder.convert_layered(raw, source="config")
        except Exception as exc:
            failure = _layered_failure_message(builder, exc, "config", cls)
        if failure is not None:
            # Raised outside the `except`, so the failing exception (which can
            # carry the raw value) is never chained; `app()`'s module commands
            # have no deferred seam to report through.
            raise ValueError(failure) from None
        sources[name] = "config"

    for name, raw in _raw_env_values(cls).items():
        builder = builders_by_name[name]
        failure = None
        try:
            merged[name] = builder.convert_layered(raw, source="env")
        except Exception as exc:
            failure = _layered_failure_message(builder, exc, "env", cls)
        if failure is not None:
            raise ValueError(failure) from None
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


def value_sources(parsed: object) -> dict[str, str]:
    """Report the origin layer ("cli", "env", "config", "instance", or
    "default") of each field on a parsed instance produced by
    `duho.parse`/`duho.main`.

    Looks up the owning parser via the per-INSTANCE
    `_duho_instance_last_parser_` linkage stashed during dispatch (see
    `_initparser_`) first -- falling back to the per-class
    `_duho_last_parser_` one, read through ``type(parsed).__dict__`` rather
    than ``getattr`` -- ``getattr`` follows the MRO, so a never-parsed
    SUBCLASS of an already-parsed base (or of bare ``Args``, parsed every
    time `duho.app` runs without a root) would otherwise inherit its base's
    stale parser and report bogus sources instead of the documented ``{}``.
    The per-instance lookup matters because the per-class one is shared by
    EVERY instance of that class: without it, parsing the same class a
    second time (a different config file, say) would silently change what an
    OLDER, already-returned instance reports here too. Returns `{}` when
    unavailable (e.g. the instance wasn't produced via a parser built by this
    framework, or no parse has happened yet for its class).

    For a SUBCOMMAND instance, root/global fields are included too:
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
    from .args._argsclass import _duho_instance_last_parser_  # lazy: avoids a circular

    # import (duho.args re-exports this module's own public names).
    parser = _duho_instance_last_parser_.get(id(parsed))
    if parser is None:
        parser = type(parsed).__dict__.get("_duho_last_parser_")
    if parser is None:
        return {}
    sources: dict[str, str] = getattr(parser, "_duho_value_sources_", None) or {}
    merged: dict[str, object] = getattr(parser, "_duho_merged_defaults_", None) or {}
    builders: dict[str, object] = dict(getattr(parser, "_duho_builders_", None) or {})
    builders.update({b.name: b for b in type(parsed)._getargs_()})

    result: dict[str, str] = {}
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
            # field left at its argparse default is mislabeled "cli".
            effective_default = builder._effective_default_()
            layer = "default"
        result[name] = layer if value == effective_default else "cli"
    return result
