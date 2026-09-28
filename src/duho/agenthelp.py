"""Agent-oriented help: a detailed, machine-readable description of a CLI.

Where the normal ``--help`` renders a compact, human-facing usage block, this
module emits a **complete, structured (JSON) description** of a duho CLI --
enough for an AI agent (or any tool) to understand the whole command surface in
one shot: every subcommand, each option with its type/default/required/
repeatable flags, positionals, per-field environment-variable bindings,
mutually-exclusive conflict groups, examples, and exit codes.

**Two triggers, both wired up by ``args.py``:**

* **The ``AGENT_HELP``/``AGENTS_HELP`` environment variables (always on,
  zero-config; either truthy triggers).** When one is set to a truthy value,
  the ordinary ``-h``/``--help`` action emits this agent description instead
  of the human help. Nothing changes for a normal ``--help`` unless one of
  these env vars is deliberately set, so default human behavior is
  byte-identical. Both defaults are replaced (no aliasing) by an explicit
  ``_agent_help_env_`` class attribute naming a single variable.
* **An opt-in ``--help-agents`` flag.** Set ``_agent_help_ = True`` on the CLI
  root to add a discoverable flag that always emits the agent description,
  regardless of the environment.

**Built on duho's existing introspection.** The description is produced by
walking the *built* ``argparse`` parser tree (so injected flags -- ``--version``,
``--print-completion`` -- and the whole subcommand tree are included) and
enriching each command with duho's own field metadata (:func:`get_clsargs` /
:class:`ClsArgDeclaration` and the per-field :class:`ArgumentBuilder`) looked up
via the ``_duho_cls_`` handle ``_initparser_`` stashes on every parser. A
subparser with no duho class behind it (e.g. a ``duho.app`` module command) is
still described from its argparse actions alone -- just without the duho-only
metadata (env bindings, conflicts) that has no argparse equivalent.

This mirrors :mod:`duho.completion`: one parser-tree walk that produces plain
data, kept out of the ``import duho`` hot path (``args.py`` imports it lazily,
only when an agent-help trigger actually fires).
"""

import argparse as _argparse
import contextlib as _contextlib
import enum as _enum
import os as _os
import pathlib as _pathlib
import sys as _sys
import typing as _ty

from . import _compat as _compat
from . import _introspect as _introspect
from . import parsers as _parsers

if _ty.TYPE_CHECKING:
    # Function-local (not module-top) in the runtime code below, to avoid a
    # circular import (`args.py` imports THIS module lazily); safe here since
    # `TYPE_CHECKING` is always False at runtime.
    from .args import Args as _Args

__all__ = [
    "SCHEMA",
    "DEFAULT_ENV",
    "DEFAULT_ENVS",
    "agent_help_requested",
    "describe",
    "describe_parser",
    "render",
    "print_agent_help",
]

#: Version tag stamped at the top of every agent-help document so a consumer can
#: detect the format and pin to a shape.
SCHEMA = "duho/agent-help@1"

#: Default environment variable that switches ``--help`` into agent mode.
DEFAULT_ENV = "AGENT_HELP"

#: Both env var names checked when no ``_agent_help_env_`` override is set
#: (either truthy triggers agent help). An explicit ``_agent_help_env_``
#: replaces both -- no aliasing once a caller names their own variable.
DEFAULT_ENVS = (DEFAULT_ENV, "AGENTS_HELP")

_NOT_DEFINED = _introspect.NOT_DEFINED

_DEFAULT_EXIT_CODES = {
    "0": "Success -- the command returned None or 0.",
    "1": "Runtime error -- the command returned a non-zero code (commonly 1).",
    "2": "Usage error -- argparse rejected the command line "
    "(unknown/missing/invalid arguments).",
}


def _truthy(raw: "str | None") -> bool:
    if raw is None:
        return False
    return raw.strip().lower() not in _compat.BOOL_FALSE


def agent_help_requested(
    env_name: "str | None" = None, environ: "_ty.Mapping[str, str] | None" = None
) -> bool:
    """True when a trigger env var is set truthy.

    ``env_name`` explicit (e.g. an app's ``_agent_help_env_`` override) checks
    only that one variable -- no aliasing once a caller names their own.
    ``env_name`` left ``None`` checks every name in :data:`DEFAULT_ENVS`
    (``AGENT_HELP`` and ``AGENTS_HELP``); either truthy triggers agent help.
    ``environ`` defaults to ``os.environ`` (injectable for tests). An unset
    variable is False; a set variable is True unless its stripped/lowercased
    value is one of :data:`duho._compat.BOOL_FALSE`.
    """
    environ = _os.environ if environ is None else environ
    if env_name is not None:
        return _truthy(environ.get(env_name))
    return any(_truthy(environ.get(name)) for name in DEFAULT_ENVS)


def _render_type(tp) -> str:
    """One canonical, version-independent spelling for a type, recursively.

    Used by :func:`_render_annotation` (never called with ``tp`` being
    ``None``/``NOT_DEFINED`` -- that's handled by the caller). Plain classes
    render by ``__name__`` even nested inside a generic (``Color``, never
    ``list[__main__.Color]``); a union always renders as ``X | Y`` (never
    ``Optional[X]``/``Union[X, Y]``) so the same field renders identically on
    every supported interpreter, unlike ``isinstance(tp, type)`` +
    ``str(tp)`` (3.9/3.10 render a bare ``list[str]`` as `list`, since a
    parameterized generic alias there passes ``isinstance(tp, type)``; 3.9
    spells a union ``Optional[int]``/``Union[int, str]``, 3.14 spells it
    ``int | None``/``int | str``).
    """
    if tp is Ellipsis:
        # The variadic marker inside `tuple[int, ...]` -- `_ty.get_args`
        # hands it back as the literal `Ellipsis` singleton, not a type, so
        # without this it recurses into the generic branch below and
        # stringifies as `Ellipsis` (`str(Ellipsis)`) instead of `...`.
        return "..."
    origin = _ty.get_origin(tp)
    if origin is None and isinstance(tp, type):
        return tp.__name__
    if origin is None:
        # A bare typing special form with no origin (rare here) -- fall back
        # to its own name, else its str() with the "typing." prefix dropped.
        return getattr(tp, "__name__", None) or str(tp).replace("typing.", "")
    args = _ty.get_args(tp)
    if origin in _compat.UNION_ORIGINS:
        members = [a for a in args if a is not type(None)]
        rendered = " | ".join(_render_type(m) for m in members)
        if len(members) != len(args):
            rendered += " | None"
        return rendered
    if origin is _ty.Literal:
        # A `Literal` arg is a VALUE (a str/int/bool/None/Enum member), not a
        # type -- recursing into this same function treated a string value as
        # an annotation and stringified it bare (`str("x, y")` drops the
        # quotes and is ambiguous with a literal comma-separated MEMBER
        # list); `repr()` keeps each member unambiguous and quoted (matching
        # Python's own literal syntax) regardless of interpreter version.
        return f"Literal[{', '.join(repr(a) for a in args)}]"
    origin_name = getattr(origin, "__name__", None) or str(origin).replace(
        "typing.", ""
    )
    if not args:
        return origin_name
    return f"{origin_name}[{', '.join(_render_type(a) for a in args)}]"


def _render_annotation(tp):
    """A readable, version-independent type string for a declared annotation
    (never raises) -- e.g. always ``list[int]``, ``int | None``.
    """
    if tp is _NOT_DEFINED or tp is None:
        return None
    return _render_type(tp)


def _jsonable(value):
    """Coerce an argparse default to a JSON-serialisable value.

    ``SUPPRESS`` (an inherited-and-suppressed root option on a child parser) and
    duho's ``NOT_DEFINED`` (a required field with no default) both map to
    ``None``. Enums render by member name, Paths by string, and
    list/set/tuple/dict recurse; anything else falls back to ``str()``.
    """
    if value is _argparse.SUPPRESS or value is _NOT_DEFINED:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _enum.Enum):
        return value.name
    if isinstance(value, _pathlib.PurePath):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _metavar(action):
    metavar = getattr(action, "metavar", None)
    if metavar is None:
        return None
    if isinstance(metavar, (list, tuple)):
        return " ".join(str(m) for m in metavar)
    return str(metavar)


def _repeatable(action, builder):
    """Whether the option may be supplied more than once / accumulates values."""
    if builder is not None and getattr(builder, "collection", None) is not None:
        return True
    if isinstance(action, _argparse._AppendAction):
        return True
    return action.nargs in ("*", "+")


def _type_of(dest, clsargs, action):
    """Readable type string, preferring the declared annotation."""
    decl = clsargs.get(dest)
    if decl is not None:
        name = _render_annotation(decl.type)
        if name:
            return name
    if action.nargs == 0:
        return "bool"
    factory = getattr(action, "type", None)
    if isinstance(factory, type):
        return factory.__name__
    return "str"


def _enum_members(tp):
    """Member names of an Enum annotation (directly or inside a Union), else None.

    An ``Enum`` field validates by member NAME through a factory + metavar rather
    than argparse ``choices`` (unlike ``Literal``, which sets real choices), so
    its valid values must be recovered from the declared annotation.
    """
    if isinstance(tp, type) and issubclass(tp, _enum.Enum):
        return [member.name for member in tp]
    for arg in _ty.get_args(tp):
        if isinstance(arg, type) and issubclass(arg, _enum.Enum):
            return [member.name for member in arg]
    return None


def _choices(action, decl):
    choices = getattr(action, "choices", None)
    if choices:
        return [str(c) for c in choices]
    if decl is not None and decl.type is not _NOT_DEFINED:
        return _enum_members(decl.type)
    return None


def _expand_action_help(action, prog: str, *, default=_NOT_DEFINED) -> str:
    """Expand ``%(default)s``-style placeholders in ``action.help``,
    matching what argparse itself shows in ``--help``.

    ``action.help`` is stored as an argparse HELP TEMPLATE (``%(default)s``,
    ``%(prog)s``, ...), `%`-expanded by ``HelpFormatter._expand_help`` only at
    RENDER time -- copying it verbatim (as this document otherwise would)
    leaked the raw placeholder text (``"API token (default: %(default)s)"``)
    into the agent document instead of the actual value. A literal ``%`` in
    help text is ALSO escaped to ``%%`` at the source (``Args._escape_help``)
    specifically so it survives this same expansion unharmed;
    mirror argparse's own ``_expand_help`` (params from ``vars(action)`` plus
    ``prog``, SUPPRESS values dropped, callables reduced to ``__name__``,
    ``choices`` joined) with a raw (``%%``-unescaped) fallback if expansion
    fails for any reason -- this is best-effort documentation output, never
    something that should raise.

    ``default`` -- when given (the caller's already-REDACTED value from
    :func:`_default_and_source`) -- overrides ``params["default"]`` before
    expansion. Without this, ``vars(action)["default"]`` is ``action.default``
    ITSELF, which may already be the CURRENT, live env/config-layered value
    for this invocation (staged by ``_stage_layers``/
    ``_apply_default_layers_one`` before this ever runs) -- a secret in the
    documented ``NS(env=...)`` example, leaking into help TEXT even though
    the sibling ``default``/``default_source`` JSON fields were already
    correctly redacted. ``_NOT_DEFINED`` (never a real field value) is the
    sentinel meaning "no override" so a caller can legitimately pass ``None``.
    """
    text = action.help
    if not text:
        return ""
    params = dict(vars(action), prog=prog)
    if default is not _NOT_DEFINED:
        params["default"] = default
    for key in list(params):
        if params[key] is _argparse.SUPPRESS:
            del params[key]
    for key in list(params):
        if hasattr(params[key], "__name__"):
            params[key] = params[key].__name__
    if params.get("choices") is not None:
        params["choices"] = ", ".join(str(c) for c in params["choices"])
    try:
        return text % params
    except (KeyError, ValueError, TypeError):
        return text.replace("%%", "%")


def _default_and_source(dest, builder, action, sources):
    """``(default, default_source)`` for one field.

    ``action.default`` may already have been overwritten by
    ``_stage_layers``/``_apply_default_layers_one`` with the CURRENT env var
    or config-file value -- a secret, in the flagship documented
    ``NS(env=...)`` example -- before ``--help``/``--help-agents`` renders.
    Report the CLASS-declared default instead (``builder._effective_default_()``,
    the same source ``duho.mcp`` already uses) -- ALWAYS, regardless of
    source: the class default is a fixed, code-level constant, never a live
    secret, so it is never itself redacted -- and -- when the field's value
    actually came from env or config -- ALSO a value-free provenance note
    alongside it, per the documented no-secrets-in-agent-help contract (that
    note is what says the value shown may not be the one actually in
    effect). Redacting the class default TOO (returning ``None`` instead of
    it) was the actual defect here: it lost the one piece of information
    that was always safe to show, showing JSON ``null``/human ``None``
    instead of a real, useful value. An ``instance=`` override is a
    caller-constructed Python value, not env/filesystem-sourced, so it keeps
    showing its class default with no note, same as an untouched field.

    A builder-less action (no duho class behind this parser at all, e.g. a
    ``duho.app`` module command) falls back to whatever
    :func:`stash_default_provenance` already stashed directly on the ACTION
    (``_duho_class_default_``/``_duho_default_source_``) -- that caller
    resolves its own builder/source data independently (it has no
    ``parser._duho_cls_`` to hand `describe_parser` either), so this is the
    only place its redaction can still reach the JSON document.
    """
    if builder is None:
        stashed_source = getattr(action, "_duho_default_source_", None)
        if stashed_source is not None:
            return getattr(action, "_duho_class_default_", None), stashed_source
        return _jsonable(action.default), None
    class_default = _jsonable(builder._effective_default_())
    source = (sources or {}).get(dest)
    if source == "env":
        env_var = getattr(builder, "env", None)
        return class_default, f"env {env_var}" if env_var else "env"
    if source == "config":
        return class_default, "config"
    return class_default, None


def _describe_option(action, clsargs, builders, prog: str, sources=None):
    dest = action.dest
    builder = builders.get(dest)
    default, default_source = _default_and_source(dest, builder, action, sources)
    info = {
        "names": list(action.option_strings),
        "dest": dest,
        "help": _expand_action_help(action, prog, default=default),
        "type": _type_of(dest, clsargs, action),
        "required": bool(getattr(action, "required", False)),
        "takes_value": action.nargs != 0,
        "repeatable": _repeatable(action, builder),
        "default": default,
        "choices": _choices(action, clsargs.get(dest)),
        "metavar": _metavar(action),
    }
    if default_source is not None:
        info["default_source"] = default_source
    if builder is not None:
        if getattr(builder, "env", None):
            info["env"] = builder.env
        conflicts = getattr(builder, "conflicts", None)
        if conflicts:
            info["conflicts"] = conflicts
    return info


def _describe_positional(action, clsargs, builders, prog: str, sources=None):
    dest = action.dest
    builder = builders.get(dest)
    default, default_source = _default_and_source(dest, builder, action, sources)
    info = {
        "name": dest,
        "help": _expand_action_help(action, prog, default=default),
        "type": _type_of(dest, clsargs, action),
        "nargs": action.nargs,
        "required": action.nargs not in ("?", "*"),
        "repeatable": action.nargs in ("*", "+"),
        "default": default,
        "choices": _choices(action, clsargs.get(dest)),
        "metavar": _metavar(action),
    }
    if default_source is not None:
        info["default_source"] = default_source
    return info


def _conflict_groups(builders):
    """Mutually-exclusive groups declared via ``NS(conflicts=...)``."""
    groups = {}
    for name, builder in builders.items():
        conflicts = getattr(builder, "conflicts", None)
        if not conflicts:
            continue
        group = groups.setdefault(
            conflicts, {"group": conflicts, "members": [], "required": False}
        )
        group["members"].append(name)
        if getattr(builder, "conflicts_required", False):
            group["required"] = True
    return list(groups.values())


def _synthesized_example(spec):
    """A minimal invocation line built from a command's required arguments.

    Appends ``<command>`` whenever the spec has subcommands: duho's own
    subparsers are always built ``required=True``, so whether a command needs
    a subcommand has nothing to do with whether some OTHER option is also
    required -- the old ``and not any(required options)`` condition dropped
    ``<command>`` from the example the moment a root also had a required
    option, advertising an invocation that argparse itself rejects. Prefers
    the first ``--long`` option string for the flag (the previous
    ``names[-1]`` picked whichever spelling was declared last, often a terse
    short flag like ``-t``).
    """
    parts = [spec["prog"]]
    for option in spec["options"]:
        if not option["required"]:
            continue
        flag = next((n for n in option["names"] if n.startswith("--")), None)
        if flag is None:
            flag = option["names"][0] if option["names"] else option["dest"]
        if option["takes_value"]:
            parts.append(f"{flag} {option['metavar'] or option['dest'].upper()}")
        else:
            parts.append(flag)
    for positional in spec["positionals"]:
        token = f"<{positional['name']}>"
        parts.append(token if positional["required"] else f"[{positional['name']}]")
    if spec["subcommands"]:
        parts.append("<command>")
    return " ".join(parts)


def _examples(root_cls, spec):
    """Author-declared ``_examples_`` if present, else one synthesized line.

    ``_examples_`` may be a sequence of strings, or of ``(command, description)``
    pairs.
    """
    declared = getattr(root_cls, "_examples_", None) if root_cls is not None else None
    if declared:
        out = []
        for example in declared:
            if isinstance(example, (list, tuple)) and len(example) == 2:
                out.append({"command": str(example[0]), "description": str(example[1])})
            else:
                out.append({"command": str(example), "description": ""})
        return out
    return [
        {
            "command": _synthesized_example(spec),
            "description": "Minimal invocation (required arguments only).",
        }
    ]


def _exit_codes(root_cls):
    codes = dict(_DEFAULT_EXIT_CODES)
    override = getattr(root_cls, "_exit_codes_", None) if root_cls is not None else None
    if override:
        codes.update({str(k): str(v) for k, v in dict(override).items()})
    return codes


def _cls_metadata(parser):
    """``({dest: ArgumentBuilder}, {name: ClsArgDeclaration})`` for a parser.

    Read from the duho class ``_initparser_`` stashed on the parser as
    ``_duho_cls_``. Returns empty maps when there is no duho class behind the
    parser (a bare argparse parser, or a ``duho.app`` module command).
    """
    cls = getattr(parser, "_duho_cls_", None)
    if cls is None:
        return {}, {}
    try:
        builders = {b.name: b for b in cls._getargs_()}
        clsargs = _introspect.get_clsargs(cls)
    except Exception:  # pragma: no cover - introspection is best-effort here
        return {}, {}
    return builders, clsargs


@_contextlib.contextmanager
def _muted_color(parser):
    """Temporarily force ``parser.color = False`` while formatting.

    Argparse's own native color (3.14+, ``ArgumentParser(color=True)`` by
    default) still colors ``parser.format_usage()`` even when the caller
    never asked for colored HELP TEXT -- a machine-readable agent-help
    ``usage`` field must be plain regardless of the process's own
    TTY/``FORCE_COLOR`` state, since it is parsed by a tool, not displayed in
    a terminal. A no-op pre-3.14, where ``ArgumentParser`` has no ``color``
    attribute at all (mirrors ``duho.mcp``'s own ``_muted_color``, which does
    the same for a captured tool-call usage/error string).
    """
    has_color = hasattr(parser, "color")
    old = parser.color if has_color else None
    if has_color:
        parser.color = False
    try:
        yield
    finally:
        if has_color:
            parser.color = old


def stash_default_provenance(parser, cls=None) -> None:
    """Snapshot each of ``parser``'s actions' CLASS default (and env/config
    provenance) onto the action itself, for :class:`duho.formatters.DefaultsFormatter`
    and :func:`describe_parser`'s own builder-less fallback to read.

    ``DefaultsFormatter._get_help_string`` only ever receives ``action``, never
    ``parser`` -- argparse's own ``HelpFormatter`` API has no seam for it --
    so it cannot itself consult ``parser._duho_value_sources_``/``cls._getargs_()``
    the way :func:`describe_parser` does for the JSON document. Called from
    ``args.py``'s ``_AgentHelpAction`` right before it renders human help (the
    one place in the print path that still has both ``parser`` and the
    about-to-render actions), so ``--help`` never shows a live env/config
    value either, matching the JSON document's own redaction.

    ``cls`` defaults to ``parser._duho_cls_`` (the normal class-command case);
    an explicit ``cls`` lets a caller redact a parser that intentionally has
    NO ``_duho_cls_`` of its own -- a ``duho.app`` module command's subparser
    is a deliberately bare stdlib one (see ``duho.runtime``'s own module
    docstring), so it is never routed through ``_AgentHelpAction``/described
    with duho field metadata either; ``duho.runtime`` calls this directly,
    right after applying that command's own env/config layer, WITHOUT ever
    setting ``parser._duho_cls_`` itself (that attribute is also read by
    ``duho.mcp`` to decide whether a node is callable, a decision this
    redaction has no business changing).

    A no-op when no ``cls`` is available (explicit or via ``_duho_cls_``), or
    the parser was never layered (no ``_duho_value_sources_`` -- every
    field's ``action.default`` is already just its class default there,
    nothing to redact).
    """
    cls = cls if cls is not None else getattr(parser, "_duho_cls_", None)
    if cls is None:
        return
    try:
        builders = {b.name: b for b in cls._getargs_()}
    except Exception:  # pragma: no cover - introspection is best-effort here
        return
    sources = getattr(parser, "_duho_value_sources_", None) or {}
    for action in parser._actions:
        builder = builders.get(action.dest)
        if builder is None:
            continue
        default, source = _default_and_source(action.dest, builder, action, sources)
        action._duho_class_default_ = default  # type: ignore[attr-defined]
        action._duho_default_source_ = source  # type: ignore[attr-defined]


@_contextlib.contextmanager
def redact_action_defaults(parser, cls=None):
    """Temporarily replace each REDACTED action's ``.default`` with its class
    default for the duration of ``parser.format_help()``/``format_usage()``,
    restoring the original (possibly still env/config-layered) value on exit.

    argparse's OWN ``%(default)s`` expansion (``HelpFormatter._expand_help``,
    which runs for both the plain formatter and a ``DefaultsFormatter``-
    decorated one whenever help text already contains a literal
    ``%(default)s``) reads ``action.default`` DIRECTLY -- bypassing
    ``_get_help_string``/:func:`_expand_action_help` entirely -- so it is the
    one seam none of duho's other redaction (the ``default``/
    ``default_source`` JSON fields, ``DefaultsFormatter``'s own appended
    suffix) ever reaches. Swapping the attribute itself for the render is the
    only way to keep that expansion from reading the live value; restoring it
    unconditionally afterward means an actual parse (or a later ``--help``
    once a value changes) still starts from the true, layered default.

    Calls :func:`stash_default_provenance` first (idempotent -- a no-op if
    ``cls``/``parser._duho_cls_`` is unavailable or the parser was never
    layered), then only touches actions it actually stashed onto
    (``hasattr(action, "_duho_class_default_")``) -- a plain argparse action
    with no duho field behind it (``-h``, ``--version``) is left alone.
    """
    stash_default_provenance(parser, cls=cls)
    originals = []
    for action in parser._actions:
        if not hasattr(action, "_duho_class_default_"):
            continue
        originals.append((action, action.default))
        action.default = action._duho_class_default_
    try:
        yield
    finally:
        for action, original in originals:
            action.default = original


class _RedactedHelpAction(_argparse._HelpAction):
    """Plain ``-h``/``--help``, with the redaction :func:`redact_action_defaults`
    performs applied around the render.

    Installed on a MODULE COMMAND's subparser (:func:`install_help_redaction`)
    -- a bare stdlib ``add_parser()`` instance that never goes through
    ``args.py``'s ``_install_agent_help``/``_AgentHelpAction`` (see this
    module's own docstring on why a module command's subparser deliberately
    has no ``_duho_cls_``), so it never got this protection any other way.
    Unlike :class:`_AgentHelpAction`, this never emits the agent-help JSON
    document -- module commands don't opt into that trigger -- it only keeps
    a live env/config value staged onto ``action.default`` for this run out
    of a literal ``%(default)s`` in plain human help text.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        with redact_action_defaults(parser):
            _compat.write_human(parser.format_help(), _sys.stdout)
        parser.exit()


def install_help_redaction(parser) -> None:
    """Swap every ``_HelpAction`` on ``parser`` to :class:`_RedactedHelpAction`.

    For a module command's subparser (the only caller today, from
    ``duho.runtime``'s ``_apply_app_config_layers``, right after it stashes
    that command's own provenance) -- its plain, argparse-added ``-h``/
    ``--help`` action would otherwise render a literal ``%(default)s`` in its
    help text straight from the live ``action.default``, same as any other
    unprotected parser (see :func:`redact_action_defaults`). A no-op for an
    action already swapped (idempotent, safe to call more than once).
    """
    for action in parser._actions:
        if isinstance(action, _argparse._HelpAction) and not isinstance(
            action, _RedactedHelpAction
        ):
            action.__class__ = _RedactedHelpAction


def describe_parser(
    parser: "_argparse.ArgumentParser",
    *,
    root: bool = False,
    root_cls: "type[_Args] | None" = None,
    name: "str | None" = None,
    aliases: "_ty.Sequence[str] | None" = None,
    _seen: "set | None" = None,
) -> "dict":
    """Describe one built ``ArgumentParser`` (and its subtree) as plain data.

    ``root`` adds the document-level keys (schema tag, version, exit codes,
    examples). ``name``/``aliases`` label a subcommand within its parent.
    ``_seen`` guards against re-describing a subparser reached under multiple
    (alias) names.
    """
    if _seen is None:
        _seen = set()
    builders, clsargs = _cls_metadata(parser)
    cls = getattr(parser, "_duho_cls_", None)
    # Which of THIS parser's fields are currently showing a live
    # env/config value on `action.default` -- populated by
    # `_stage_layers`/`_apply_default_layers_one` only once an actual parse
    # is underway (e.g. via `duho.main`/`duho.parse`/`duho.app`); absent
    # (``None``) for a freshly built, never-parsed parser, in which case
    # every field's `action.default` is already just its class default.
    sources = getattr(parser, "_duho_value_sources_", None)

    spec = {}
    if root:
        spec["schema"] = SCHEMA
    if name is not None:
        spec["name"] = name
        spec["aliases"] = list(aliases or [])
    spec["prog"] = parser.prog
    # `parser.description` holds the RAW (pre-expansion) text -- it is
    # only ``%%``-escaped at the source when it literally contains a
    # `%(prog)` placeholder (`Args._escape_description`), matching argparse's
    # own rule that a description is `%`-formatted only in that same case.
    # Un-escaping it UNCONDITIONALLY here (an earlier fix did) corrupted a
    # description that genuinely contains a literal `%%`; read it as stored.
    spec["description"] = (parser.description or "").strip()
    if root:
        # Version comes from the APP'S ROOT class, not this node's own
        # `cls` -- a subcommand-scoped document (``AGENT_HELP=1 app sub
        # --help``) would otherwise report `version: null` for a subcommand
        # that (like almost every subcommand) declares no `_version_` of its
        # own. `root_cls` is `None` only when `describe_parser` was called
        # directly on a raw/never-rooted parser, in which case `cls` is the
        # best available fallback (matches the earlier behavior there).
        version = None
        version_cls = root_cls if root_cls is not None else cls
        if version_cls is not None:
            from .args import _resolve_version

            try:
                version = _resolve_version(version_cls)
            except Exception:  # pragma: no cover - version resolution is best-effort
                version = None
        spec["version"] = version
    with _muted_color(parser):
        spec["usage"] = parser.format_usage().strip()

    options = []
    positionals = []
    subparsers_action = _parsers.find_subparsers(parser)
    for action in parser._actions:
        if action is subparsers_action:
            continue
        if action.option_strings:
            options.append(
                _describe_option(action, clsargs, builders, parser.prog, sources)
            )
        else:
            positionals.append(
                _describe_positional(action, clsargs, builders, parser.prog, sources)
            )
    spec["options"] = options
    spec["positionals"] = positionals
    spec["conflicts"] = _conflict_groups(builders)

    subcommands = []
    if subparsers_action is not None:
        # argparse registers alias names as extra keys pointing at the SAME
        # subparser object; `unique_subcommands` groups by identity so each
        # command is described once, under one canonical name (previously a
        # hand-copy of this exact grouping, kept separately in
        # `duho.mcp`, that could silently diverge from this one).
        for canonical, alias_names, subparser in _parsers.unique_subcommands(
            parser, seen=_seen
        ):
            subcommands.append(
                describe_parser(
                    subparser,
                    root=False,
                    name=canonical,
                    aliases=list(alias_names),
                    _seen=_seen,
                )
            )
    spec["subcommands"] = subcommands

    if root:
        # Exit codes, like version, come from the APP'S ROOT class --
        # a subcommand can still return one of the app's documented codes
        # even though it declares no `_exit_codes_` of its own. Examples stay
        # scoped to the CURRENT command (`cls`, not `root_cls`): an app's
        # root-level `_examples_` is not necessarily meaningful help for
        # "just this subcommand", so a subcommand with no `_examples_` of its
        # own keeps getting one synthesized from ITS OWN spec, as before.
        exit_cls = root_cls if root_cls is not None else cls
        spec["exit_codes"] = _exit_codes(exit_cls)
        spec["examples"] = _examples(cls, spec)
    return spec


def describe(cls: "type[_Args]", argv: "_ty.Sequence[str] | None" = None) -> "dict":
    """Build ``cls``'s parser and return its agent-help document (a dict).

    Standalone counterpart to the ``--help-agents`` flag / ``AGENT_HELP`` trigger:
    builds the parser tree fresh and describes it, independent of whether either
    trigger is wired up. ``argv`` is accepted for signature symmetry with the
    other entry points and currently unused (the description is static).
    """
    parser = cls._parser_()
    return describe_parser(parser, root=True, root_cls=cls)


def render(spec: "dict") -> str:
    """Serialise an agent-help document to a JSON string (trailing newline).

    ``json`` is imported lazily (not at module top) so ``import duho`` never pays
    its import cost -- only actually rendering an agent-help document does. This
    keeps the framework's zero-eager-``json`` contract (see
    ``tests/test_config_json.py::test_json_import_is_lazy``).

    ``ensure_ascii=True``: a non-ASCII character (an arrow in a
    docstring, a Latin-1 accent) written through a piped Windows stdout's
    text layer raises ``UnicodeEncodeError`` (empty output, exit 1) or comes
    out console-code-page-encoded instead of UTF-8. ASCII-escaping every
    non-ASCII character survives any stream encoding and decodes back to the
    identical string, at the cost of a few extra bytes and less readable raw
    JSON -- irrelevant for a document meant to be parsed, not read.
    """
    import json as _json

    return _json.dumps(spec, indent=2, ensure_ascii=True) + "\n"


def print_agent_help(
    cls: "type[_Args]", file: "_ty.Optional[_ty.TextIO]" = None
) -> None:
    """Print ``cls``'s agent-help JSON document to ``file`` (default stdout).

    Written via :func:`duho._compat.write_machine`: raw UTF-8
    bytes with LF-only newlines, bypassing ``file``'s text-mode encoding and
    newline translation, the same writer ``args.py``'s agent-help actions use
    -- so this standalone entry point and the ``-h``/``--help-agents``
    triggers behave identically on any host encoding.
    """
    _compat.write_machine(render(describe(cls)), file)
