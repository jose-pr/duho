from __future__ import annotations

import argparse as _argparse
import typing as _ty
import weakref as _weakref

from .. import _introspect as _introspect
from .. import agenthelp as _agenthelp
from .. import parsers as _parsers
from ..args import Cmd as _Cmd
from .._layers import _apply_layers as _apply_layers
from .._layers import _raw_config_values as _raw_config_values
from .._layers import _raw_env_values as _raw_env_values
from ..args._entry import _setup_instance_logging as _setup_instance_logging
from ..runtime._app import _build_app_core as _build_app_core
from ..runtime import run_command as _run_command

from ._command import McpCmd
from ._schema import _JSON_SCALARS, _MAX_ARRAY_ITEMS, json_schema_for_field

# --------------------------------------------------------------------------
# Step 2: describe_tools -- the cached command tree + schema merging
# --------------------------------------------------------------------------


class _Node:
    """One node of a command tree (class commands, and module commands under ``app()``).

    ``ancestors`` runs from the root to this node's parent. ``cls`` is the
    class of a class command, else ``None``; ``module_command`` is the
    :class:`~duho.discovery.ModuleCommand` of a module command, else ``None``;
    ``args_cls`` is a module command's declared ``Args``, if any.

    ``excluded`` marks a non-root node that opted out with ``_mcp_ = False``
    or inherited that: never listed, and an unknown name to :func:`call_tool`.
    """

    __slots__ = (
        "dotted_name",
        "own_name",
        "parser",
        "cls",
        "ancestors",
        "module_command",
        "args_cls",
        "excluded",
    )

    def __init__(
        self,
        dotted_name,
        own_name,
        parser,
        cls,
        ancestors,
        module_command=None,
        args_cls=None,
        excluded=False,
    ):
        self.dotted_name = dotted_name
        self.own_name = own_name
        self.parser = parser
        self.cls = cls
        self.ancestors = ancestors
        self.module_command = module_command
        self.args_cls = args_cls
        self.excluded = excluded


def _effective_cls(node_or_step: _Node) -> _ty.Optional[type]:
    """The class to read field declarations from for one tree node: ``cls``
    for a class command, else its module command's own declared ``args_cls``
    (``None`` when it declared none -- the bare-actions fallback then
    applies)."""
    return node_or_step.cls if node_or_step.cls is not None else node_or_step.args_cls


#: root class -> (root_parser, {dotted_name: _Node}), built once per root class.
#: Weak keys so a throwaway root class (as tests define) does not leak.
_TREE_CACHE: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()


#: Parsers of the nodes `_walk_tree` marked excluded (`_mcp_ = False`); their
#: names are never shown to, or compared against values from, an MCP client.
_EXCLUDED_PARSERS: _weakref.WeakSet = _weakref.WeakSet()


def _hidden_choice_names(parser: _argparse.ArgumentParser) -> frozenset:
    """Names (canonical and alias) under which ``parser`` registers a
    subcommand that is excluded from the MCP tool surface."""
    action = _parsers.find_subparsers(parser)
    if action is None:
        return frozenset()
    return frozenset(
        name for name, sub in (action.choices or {}).items() if sub in _EXCLUDED_PARSERS
    )


def _own_mcp_value(cls: type, root_cls: _ty.Optional[type]) -> bool:
    """``cls``'s ``_mcp_`` for the per-command exclusion test, inherited
    like any attribute except that a value it only gets from the served
    root class (or a base of it) is ignored: there ``_mcp_`` means "no
    environment trigger", not "hide the commands that share the root's
    fields"."""
    for klass in cls.__mro__:
        if root_cls is not None and issubclass(root_cls, klass):
            continue
        if "_mcp_" in vars(klass):
            return bool(vars(klass)["_mcp_"])
    return True


def _walk_tree(
    root_parser: _argparse.ArgumentParser,
    root_cls: _ty.Optional[type],
    root_name: str,
) -> dict[str, _Node]:
    """Walk an already-built, already-layered parser tree into ``{dotted_name: _Node}``.

    Shared by :func:`_tree_for` and :func:`_core_for_app`. A subparser without
    ``_duho_cls_`` is read for a module-command marker in ``parser._defaults``
    (no dry-run parse needed) and its resolved ``_duho_module_args_cls_``.
    Each node's :attr:`_Node.excluded` follows ``_mcp_ = False``: read with
    ``getattr`` for a class (so subclasses inherit it), from the module
    command otherwise, never for the root, and inherited by the subtree.
    """
    nodes: dict[str, _Node] = {}
    seen: set = set()

    def _walk(parser, cls, dotted_parts, own_name, ancestors, parent_excluded=False):
        module_command = None
        args_cls = None
        if cls is None:
            defaults = getattr(parser, "_defaults", None) or {}
            module_command = defaults.get("_duho_module_command_")
            args_cls = getattr(parser, "_duho_module_args_cls_", None)
        is_root = not ancestors
        own_mcp_disabled = False
        if not is_root:
            if cls is not None:
                own_mcp_disabled = not _own_mcp_value(cls, root_cls)
            elif module_command is not None:
                own_mcp_disabled = not getattr(module_command, "_mcp_", True)
        excluded = parent_excluded or own_mcp_disabled
        node = _Node(
            ".".join(dotted_parts),
            own_name,
            parser,
            cls,
            ancestors,
            module_command=module_command,
            args_cls=args_cls,
            excluded=excluded,
        )
        nodes[node.dotted_name] = node
        if excluded:
            _EXCLUDED_PARSERS.add(parser)
        # Dispatch-identity marker (see `call_tool`): every subparser is tagged
        # with its chain of names. A deeper subparser's `set_defaults` overwrites
        # it as parsing descends, so after a parse it names the one reached.
        parser.set_defaults(_duho_mcp_path_=dotted_parts)
        if module_command is not None:
            # Subparsers a module command's own register() hook adds are
            # hand-made, not duho commands: they are not tools.
            return
        for canonical, _aliases, subparser in _parsers.unique_subcommands(parser, seen):
            sub_cls = getattr(subparser, "_duho_cls_", None)
            _walk(
                subparser,
                sub_cls,
                dotted_parts + (canonical,),
                canonical,
                ancestors + (node,),
                parent_excluded=excluded,
            )

    _walk(root_parser, root_cls, (root_name,), root_name, ())
    return nodes


def _tree_for(root_cls: type[_Cmd]) -> tuple:
    """Return (and cache) ``root_cls``'s ``(root_parser, {dotted_name: _Node})``.

    The parser is built and env/config-layered once. Only the config-file
    content and the tree structure are cached: ``NS(env=...)`` is resolved
    live inside ``parse_args()``.
    """
    cached = _TREE_CACHE.get(root_cls)
    if cached is not None:
        return cached

    root_parser = root_cls._parser_()
    _apply_layers(root_parser, root_cls, config=None)

    nodes = _walk_tree(root_parser, root_cls, root_parser.prog)

    result = (root_parser, nodes)
    _TREE_CACHE[root_cls] = result
    return result


class _ServerCore:
    """One server's resolved ``(root_parser, nodes, dispatch, root_cls)``.

    The same shape whether the tree came from a class's static
    ``_subcommands_`` or a full ``app()`` build. ``dispatch(command, instance)``
    does the post-parse steps and ends in :func:`duho.runtime.run_command`;
    ``root_cls`` is always a real class, read by :func:`_server_info`.
    """

    __slots__ = ("root_parser", "nodes", "dispatch", "root_cls")

    def __init__(self, root_parser, nodes, dispatch, root_cls):
        self.root_parser = root_parser
        self.nodes = nodes
        self.dispatch = dispatch
        self.root_cls = root_cls


def _core_for_class(root_cls: type[_Cmd]) -> _ServerCore:
    """Build a :class:`_ServerCore` for a class's static ``_subcommands_``
    tree -- the ``serve(root_cls)``/``python -m duho.mcp <app>`` path.
    ``dispatch`` sets up instance logging (always), then calls
    :func:`duho.runtime.run_command`.
    """
    root_parser, nodes = _tree_for(root_cls)

    def _dispatch(command: object, instance: object) -> int:
        _setup_instance_logging(instance, True, root_cls)
        return _run_command(command, instance)

    return _ServerCore(root_parser, nodes, _dispatch, root_cls)


def _core_for_app(root: type | None = None, **app_kwargs: object) -> _ServerCore:
    """Build a :class:`_ServerCore` for a full ``app()`` command tree.

    Class and module commands resolve exactly as ``duho.app`` resolves them,
    built once per server rather than per tool call. ``**app_kwargs`` are
    ``duho.app``'s, except ``dispatch`` (replaces the final run step, as for a
    CLI run) and ``setup_logging`` (logging is set up per tool call).
    """
    parser, root_cls, dispatch = _build_app_core(root, **app_kwargs)
    # The root tool-name segment is the application's name, `parser.prog`.
    nodes = _walk_tree(parser, root_cls, parser.prog)
    return _ServerCore(parser, nodes, dispatch, root_cls)


def _is_namespace_node(parser: _argparse.ArgumentParser) -> bool:
    """True when ``parser`` owns a MANDATORY subparsers action.

    duho's static ``_subcommands_`` tree always registers
    ``add_subparsers(..., required=True)``, so a ``Cli`` with subcommands can
    never itself dispatch successfully -- calling it always fails with
    argparse's own "the following arguments are required: <command>". Such a
    node is not published as an MCP tool at all (its fields are still merged
    into every descendant's schema by :func:`_input_schema_for_node`).
    """
    subparsers_action = _parsers.find_subparsers(parser)
    if subparsers_action is None:
        return False
    return bool(getattr(subparsers_action, "required", False))


def _is_mcp_command_node(node: _Node) -> bool:
    """True when ``node``'s class IS (or subclasses) :class:`McpCmd` -- the
    self-serving command an app may register under any name. Checked
    alongside :func:`_is_namespace_node` everywhere a node's callability as
    an MCP tool matters (:func:`describe_tools`/:func:`call_tool`)."""
    return (
        node.cls is not None
        and isinstance(node.cls, type)
        and issubclass(node.cls, McpCmd)
    )


def _drop_layer_satisfied(
    required: list[str], cls: type, parser: _argparse.ArgumentParser
) -> list[str]:
    """Fields whose value can come from ``NS(env=...)``/``_config_`` even
    though the MCP call omits them are not required over MCP.

    Unlike a CLI user, an MCP client cannot see the server process's own
    environment or config file -- a required field satisfied by either would
    otherwise be unreachable (``tools/list`` demands it, yet supplying it
    would only ever override, never merely satisfy, the layer).
    """
    if not required:
        return required
    config_table = getattr(parser, "_duho_raw_config_table_", None) or {}
    satisfied = set(_raw_config_values(cls, config_table)) | set(_raw_env_values(cls))
    if not satisfied:
        return required
    return [name for name in required if name not in satisfied]


def _own_dests(parser: _argparse.ArgumentParser) -> _ty.Optional[set]:
    """The dest names ``runtime._register_module_command`` stashed as this
    module command's OWN (``_duho_module_own_dests_``) -- ``None`` for
    anything else (a class command, or the root of either tree kind), which
    callers read as "no filtering needed"."""
    return getattr(parser, "_duho_module_own_dests_", None)


def _step_field_names(step: _Node) -> list[str]:
    """Every field name ``step`` itself declares.

    Declared ``Args`` fields are filtered to dests present on the subparser:
    a module field that collided with an inherited global was skipped at
    registration and is not this step's. A bare module command has only its
    :func:`_own_dests`.
    """
    eff_cls = _effective_cls(step)
    if eff_cls is not None:
        own = _own_dests(step.parser)
        names = [b.name for b in eff_cls._getargs_()]
        if own is not None:
            names = [n for n in names if n in own]
        return names
    return sorted(_own_dests(step.parser) or ())


def _schema_for_action(action: _argparse.Action) -> tuple[dict, bool]:
    """Best-effort ``(json_schema, required)`` for one bare argparse
    ``Action``, with no duho field declaration behind it at all (a module
    command with no declared ``Args``, its fields added directly by a
    ``register()`` hook, or genuinely none). Mirrors
    ``duho.agenthelp``'s own builder-less fallback (`_describe_option`/
    `_describe_positional`) -- lower fidelity than :func:`json_schema_for_field`
    (no declared-annotation element types, no env/config provenance -- a
    bare action has neither), but enough for a client to call the tool.
    """
    is_positional = not action.option_strings
    choices = getattr(action, "choices", None)
    factory = getattr(action, "type", None)
    scalar = _JSON_SCALARS.get(factory) if isinstance(factory, type) else None
    if choices:
        schema: dict = {"type": scalar or "string", "enum": [str(c) for c in choices]}
    elif action.nargs == 0:
        schema = {"type": "boolean"}
    elif isinstance(action, _argparse._AppendAction) or action.nargs in ("*", "+"):
        schema = {
            "type": "array",
            "items": {"type": scalar or "string"},
            "maxItems": _MAX_ARRAY_ITEMS,
        }
    else:
        schema = {"type": scalar or "string"}
    if isinstance(action, _argparse._SubParsersAction):
        # A subparsers action a register() hook added: the property picks one
        # of its names, and is required only when the hook made it so.
        required = bool(action.required)
    elif is_positional:
        required = action.nargs not in ("?", "*")
    else:
        required = bool(getattr(action, "required", False))
    # No `default` is published: a bare action's default is whatever its
    # author's code computed (possibly an environment value), with no
    # declaration to say it is safe to show.
    help_text = action.help
    if help_text and help_text is not _argparse.SUPPRESS:
        schema["description"] = str(help_text).replace("%%", "%")
    return schema, required


def _merge_bare_actions(step: _Node, properties: dict) -> tuple[list, list]:
    """:func:`_input_schema_for_node`'s per-step merge, for a step with
    neither ``cls`` nor ``args_cls`` -- derives fields straight from this
    subparser's OWN actions (:func:`_own_dests`/:func:`_schema_for_action`)
    rather than any duho field declaration."""
    own = _own_dests(step.parser) or set()
    level_names: list[str] = []
    level_required: list[str] = []
    for action in step.parser._actions:
        if action.dest not in own:
            continue
        schema, is_required = _schema_for_action(action)
        properties[action.dest] = schema
        level_names.append(action.dest)
        if is_required:
            level_required.append(action.dest)
    return level_names, level_required


def _input_schema_for_node(node: _Node) -> dict:
    """The full ``inputSchema`` for one tree node.

    The node's fields merged with every ancestor's, root to leaf, so a deeper
    redeclaration shadows a shallower one (MCP has no other way to supply an
    ancestor's field). A field satisfiable from the server's environment or
    config is dropped from ``required``. Every tool also gets a synthetic
    optional ``"--"`` array for the passthrough argv, which :func:`call_tool`
    appends after a literal ``--``.
    """
    properties: dict = {}
    required: list[str] = []
    for step in node.ancestors + (node,):
        eff_cls = _effective_cls(step)
        if eff_cls is None:
            # A bare module command is always the leaf, so nothing deeper
            # can shadow it.
            level_names, level_required = _merge_bare_actions(step, properties)
        else:
            clsargs = _introspect.get_clsargs(eff_cls)
            own = _own_dests(step.parser)
            level_names = []
            level_required = []
            for builder in eff_cls._getargs_():
                name = builder.name
                if own is not None and name not in own:
                    # Skipped at registration: an inherited global already
                    # contributes this field.
                    continue
                level_names.append(name)
                decl = clsargs.get(name)
                schema, is_required = json_schema_for_field(decl, builder)
                properties[name] = schema
                if is_required:
                    level_required.append(name)
            # A module command's subparser has no `_duho_raw_config_table_`
            # (its config slice is applied eagerly), so a config-satisfied
            # field is still reported required: stricter, never unsafe.
            level_required = _drop_layer_satisfied(level_required, eff_cls, step.parser)
        for name in level_names:
            if name in required and name not in level_required:
                required.remove(name)
        for name in level_required:
            if name not in required:
                required.append(name)
    properties["--"] = {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": _MAX_ARRAY_ITEMS,
        "default": [],
        "description": (
            "Arguments captured after a literal '--' separator, forwarded "
            "verbatim as the command's own _passthrough_ list."
        ),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _conflict_note(cls: _ty.Optional[type]) -> str:
    """A short human-readable note for ``cls``'s ``NS(conflicts=...)`` groups.

    Exclusive groups are surfaced only as tool-description text in v1 (no
    ``oneOf``/``not`` JSON Schema encoding yet). Reuses
    ``duho.agenthelp._conflict_groups`` rather than re-deriving group
    membership. Returns ``""`` when the command declares no conflict groups,
    or (a bare module command with no declared ``Args`` at all) ``cls`` is
    ``None``.
    """
    if cls is None:
        return ""
    builders = {b.name: b for b in cls._getargs_()}
    groups = _agenthelp._conflict_groups(builders)
    if not groups:
        return ""
    parts = []
    for group in groups:
        qualifier = "exactly one required" if group["required"] else "at most one"
        parts.append("%s (%s)" % (", ".join(group["members"]), qualifier))
    return "Mutually exclusive: " + "; ".join(parts) + "."


def _tool_spec(node: _Node) -> dict:
    """Build one MCP ``{name, description, inputSchema}`` tool spec for ``node``."""
    description = (node.parser.description or "").replace("%%", "%").strip()
    input_schema = _input_schema_for_node(node)
    note = _conflict_note(_effective_cls(node))
    if note:
        description = (description + "\n\n" + note).strip() if description else note
    return {
        "name": node.dotted_name,
        "description": description,
        "inputSchema": input_schema,
    }


def describe_tools(root_cls: _ty.Union[type, _ServerCore]) -> list[dict]:
    """Describe every callable command in ``root_cls``'s tree as MCP tool specs.

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree). The other form, a :class:`_ServerCore`, is the server core duho
    itself builds for an ``app()`` tree (the environment trigger and
    ``_mcp_command_``); a caller does not construct one.

    Each command reached by walking the built parser tree -- the root itself
    (when it can itself be dispatched), and every subcommand, recursively --
    becomes one tool ``{name, description, inputSchema}``: a leaf/root tool is
    named after the application (its root parser's ``prog``, see
    :func:`duho.args._app_name`), a nested one ``parent.child``. A NAMESPACE
    node (one whose own subcommand is mandatory
    -- see :func:`_is_namespace_node`) is skipped: it can never itself
    dispatch successfully, so listing it would only ever waste a client's
    turn on a guaranteed usage error; its own fields are still reachable,
    merged into every one of its descendants' schemas. This applies equally
    to a class command AND a module command (an ``app()``-only concept --
    every ``ModuleCommand`` node is itself always a leaf, never a namespace).
    A node whose class is (or subclasses) :class:`McpCmd` -- the self-serving
    command an ``app()`` may register under any name -- is
    likewise skipped (see :func:`_is_mcp_command_node`): serving one MCP
    session from inside a tool call another MCP session made makes no sense.

    A node opted out via a per-command ``_mcp_ = False`` (see
    :attr:`_Node.excluded`, computed by :func:`_walk_tree`) is skipped too,
    together with its whole subtree -- the ROOT's own ``_mcp_`` is exempt
    (never treated as this kind of exclusion).
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    return [
        _tool_spec(node)
        for node in core.nodes.values()
        if not _is_namespace_node(node.parser)
        and not _is_mcp_command_node(node)
        and not node.excluded
    ]
