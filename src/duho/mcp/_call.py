from __future__ import annotations

import argparse as _argparse
import contextlib as _contextlib
import io as _io
import logging as _logging
import re as _re
import sys as _sys
import typing as _ty

from ..logging import _STDERR_HANDLER_TAG as _STDERR_HANDLER_TAG
from .._outcome import Result as _Result
from .._outcome import _error_status, _render
from ..logging import log_exception as _log_exception

from ._argv import _sibling_names, _synthesize_step_argv
from ._errors import InvalidArgumentsError, UnknownToolError
from ._tree import (
    _ServerCore,
    _core_for_class,
    _hidden_choice_names,
    _input_schema_for_node,
    _is_mcp_command_node,
    _is_namespace_node,
    _step_field_names,
)

_LOGGER = _logging.getLogger(__package__)


_SCHEMA_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    # A number with a zero fractional part is an integer (JSON Schema).
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool))
    or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, (list, tuple)),
    "object": lambda v: isinstance(v, dict),
}


def _matches_schema_type(value: object, expected: object) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    return any(_SCHEMA_TYPE_CHECKS.get(t, lambda v: True)(value) for t in types)


def _validate_arguments(schema: dict, arguments: dict) -> None:
    """Reject ``arguments`` that violate ``schema``, before any argv is built.

    Collects every problem: unknown properties, missing required ones (a JSON
    ``null`` counts as missing), a wrong JSON type (the string ``"false"``
    for a bool would be truthy and turn the flag ON), values outside ``enum``,
    ``maximum``/``minimum``, ``maxItems``/``maxProperties`` (so an oversized
    LLM-supplied collection is refused rather than dispatched) and non-string
    items in a string-array property. Values that are schema-valid but unsafe
    as argv are refused later, since that depends on the parser tree.
    """
    properties = schema.get("properties", {})
    required = schema.get("required", ())
    errors = []
    for key in arguments:
        if key not in properties:
            errors.append("unknown argument %r" % (key,))
    for key in required:
        if key not in arguments or arguments[key] is None:
            errors.append("missing required argument %r" % (key,))
    for key, value in arguments.items():
        prop = properties.get(key)
        if prop is None or value is None:
            continue
        expected = prop.get("type")
        if expected and not _matches_schema_type(value, expected):
            errors.append(
                "argument %r: expected %s, got %s"
                % (key, expected, type(value).__name__)
            )
            continue
        enum = prop.get("enum")
        if enum is not None and value not in enum:
            errors.append("argument %r: %r is not one of %r" % (key, value, enum))
            continue
        maximum = prop.get("maximum")
        if (
            maximum is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > maximum
        ):
            errors.append("argument %r: %r exceeds maximum %r" % (key, value, maximum))
        minimum = prop.get("minimum")
        if (
            minimum is not None
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value < minimum
        ):
            errors.append("argument %r: %r is below minimum %r" % (key, value, minimum))
        max_items = prop.get("maxItems")
        if (
            max_items is not None
            and isinstance(value, (list, tuple))
            and len(value) > max_items
        ):
            errors.append(
                "argument %r: has %d items, exceeds maxItems %r"
                % (key, len(value), max_items)
            )
        max_properties = prop.get("maxProperties")
        if (
            max_properties is not None
            and isinstance(value, dict)
            and len(value) > max_properties
        ):
            errors.append(
                "argument %r: has %d properties, exceeds maxProperties %r"
                % (key, len(value), max_properties)
            )
        items_schema = prop.get("items")
        if (
            items_schema
            and items_schema.get("type") == "string"
            and isinstance(value, (list, tuple))
            and any(not isinstance(item, str) for item in value)
        ):
            errors.append("argument %r: every item must be a string" % (key,))
    if errors:
        raise InvalidArgumentsError("; ".join(errors))


_ERROR_LINE = _re.compile(r"^(?!usage:)[^\n]*?: error: ", _re.MULTILINE)
_CHOICE_LIST = _re.compile(r"\(choose from ([^)]*)\)|\{([^}]*)\}")


def _client_visible_parse_error(text: str, hidden: frozenset) -> str:
    """argparse's error text for a client: the ``error:`` line only (no usage
    block) with the names of commands excluded from the tool surface removed
    from any list of choices it quotes."""
    found = _ERROR_LINE.search(text)
    if found:
        text = text[found.start() :]

    def _scrub(match: _re.Match) -> str:
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        sep = ", " if match.group(1) is not None else ","
        kept = [n for n in inner.split(sep) if n.strip("'\"") not in hidden]
        if match.group(1) is not None:
            return "(choose from %s)" % sep.join(kept)
        return "{%s}" % sep.join(kept)

    return _CHOICE_LIST.sub(_scrub, text)


def _text_result(text: str, *, is_error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def _is_error_flag(result: object, default: bool = False) -> bool:
    """A ``Result``'s own ``is_error``; a plain int keeps ``default``."""
    return result.is_error if isinstance(result, _Result) else default


def _declared_exit_meaning(root_cls: object, code: int) -> _ty.Optional[str]:
    """The meaning the root's own ``_exit_codes_`` declares for ``code``, if any."""
    declared = getattr(root_cls, "_exit_codes_", None)
    if declared:
        for key, meaning in dict(declared).items():
            if str(key) == str(code):
                return str(meaning)
    return None


def _systemexit_result(exc: SystemExit, stdout_text: str, stderr_text: str) -> dict:
    """Map a command's own run-time ``SystemExit`` to a tool result:
    ``None``/``0`` -> success; an int -> ``isError`` with an
    ``"exit code: N"`` trailer; anything else (e.g. ``sys.exit("message")``)
    -> ``isError`` with that message -- the same convention Python's own
    interpreter uses for an uncaught ``SystemExit``."""
    code = exc.code
    if code is None or code == 0:
        return _text_result(stdout_text)
    trailing = "exit code: %d" % code if isinstance(code, int) else str(code)
    parts = [part for part in (stdout_text, stderr_text.strip(), trailing) if part]
    return _text_result("\n".join(parts), is_error=True)


@_contextlib.contextmanager
def _muted_color(parsers: _ty.Iterable[_argparse.ArgumentParser]):
    """Force ``parser.color = False`` (argparse color, Python 3.14+) while
    captured output is machine-read, whatever the TTY or ``FORCE_COLOR``."""
    saved = [(p, p.color) for p in parsers if hasattr(p, "color")]
    for p, _old in saved:
        p.color = False
    try:
        yield
    finally:
        for p, old in saved:
            p.color = old


@_contextlib.contextmanager
def _rebound_stderr_logging(active_stream: object, idle_stream: object):
    """Point duho's stderr log handler at ``active_stream`` for one dispatch.

    ``init_stderr_logging`` never re-points a handler it already installed, so
    without this a later call would log to the first call's dead capture.
    Restores ``idle_stream`` on exit. Handlers are listed again after
    ``yield`` to catch one created during the call; only duho-tagged handlers
    are touched.
    """
    root_logger = _logging.getLogger()

    def _tagged():
        return [
            h
            for h in root_logger.handlers
            if getattr(h, _STDERR_HANDLER_TAG, False) and hasattr(h, "setStream")
        ]

    for handler in _tagged():
        handler.setStream(active_stream)
    try:
        yield
    finally:
        for handler in _tagged():
            handler.setStream(idle_stream)


def call_tool(
    root_cls: _ty.Union[type, _ServerCore], name: object, arguments: object
) -> dict:
    """Dispatch one MCP ``tools/call`` against ``root_cls``'s tree.

    ``root_cls`` is a ``Cmd``/``Cli`` class (the static ``_subcommands_``
    tree); the :class:`_ServerCore` form is built by duho for an ``app()``
    tree, not by a caller -- see :func:`describe_tools`.

    Resolves ``name`` to a node in the tree, raising
    :class:`UnknownToolError` for a name that is not in the tree, that names
    a namespace node (see :func:`_is_namespace_node`), or that names a node
    excluded via a per-command ``_mcp_ = False`` (:attr:`_Node.excluded`) --
    the same error either way, so an excluded command's existence is never
    disclosed to a caller probing for it. Raises :class:`InvalidArgumentsError` when
    ``arguments`` is not a JSON object (``None`` is treated as ``{}``), fails
    the tool's own merged ``inputSchema`` (:func:`_validate_arguments`), or
    (detected while synthesizing argv, since it depends on the built
    parser tree rather than the JSON schema alone) supplies a value that
    cannot be safely encoded at all -- an unsafe positional, an unsafe
    option value, or a negative counting-flag value (see
    :func:`_synthesize_argv`/:func:`_reject_unsafe_positional`/
    :func:`_emit_option`). All of these are request-level problems,
    mapped by :func:`serve` to a JSON-RPC error response rather than a tool
    result.

    Otherwise: synthesizes one argv per level of the tool's ancestry chain
    (root first) via :func:`_synthesize_argv`, with the next level's own
    subcommand name token in between, then -- when the JSON arguments carry
    a ``"--"`` array -- one literal ``--`` token followed by its items at
    the very end (see :func:`_input_schema_for_node`'s synthetic property),
    and parses the WHOLE THING through the tree's single shared ROOT parser
    -- exactly as ``duho.main``/``duho.parse`` would for the equivalent CLI
    invocation, so env/config layering, root globals, ``_passthrough_``, and
    (via :func:`duho.args._setup_instance_logging`) ``LoggingArgs``
    verbosity setup all reach the dispatched command. Captures stdout AND
    stderr during dispatch, and replaces ``sys.stdin`` with an empty stream
    for its duration (a command honoring '-' = stdin must not be able to
    read the MCP client's next request off the real stdin).

    **Dispatch-identity guard** (security-relevant: MCP tool arguments are
    LLM-controlled): an ancestor's own optional/variadic positional field can
    -- when a client omits it -- still absorb the LITERAL subcommand-name
    token this function inserts between levels, shifting a LATER token into
    that ancestor's own subparsers action and dispatching a DIFFERENT
    sibling than the one named by ``name`` (argparse itself has always
    allowed this; it is normally harmless on a real, human-typed CLI, but
    not when the argv comes from an LLM). The SAME class can also be reached
    from more than one place in the tree (shared between two parents, or
    both nested and top-level), so a class-identity check alone cannot tell
    a hijack from a legitimate dispatch. After a successful parse, the
    result is used ONLY when BOTH the intended command was actually selected
    (``type(instance) is node.cls`` for a class command, or the popped
    ``_duho_module_command_`` marker ``is node.module_command`` for a module
    command) AND the ``_duho_mcp_path_`` tag :func:`_walk_tree` attaches to
    every subparser (via ``set_defaults``, overwritten by the DEEPEST
    subparser actually reached as parsing descends) equals the tool's own
    chain of names exactly -- anything else (including a namespace class
    picked up mid-chain, or the right command reached through the WRONG
    chain) is treated as a dispatch failure, mapped to ``isError: true``, and
    the command is NEVER run. :func:`_synthesize_argv`/
    :func:`_synthesize_argv_from_actions` additionally refuse
    outright (before parsing, as an :class:`InvalidArgumentsError`) a value
    explicitly supplied FOR a field at any level that collides with a
    subcommand name/alias registered at THAT level or any ANCESTOR level.

    **Return convention**: ``run_command`` returns ``0`` for a
    ``None``/``0`` command return, an int for a non-zero return, or the raw
    object/list when the command returned one. Mapped here: ``0`` -> success,
    one text block of captured stdout; a non-zero int -> ``isError: true``,
    captured stdout + captured stderr + a trailing ``"exit code: N"`` line;
    anything else -> success, one text block holding its JSON dump. A
    ``SystemExit`` from argument PARSING (bad/missing argument) ->
    ``isError: true`` with the captured stderr text. A ``SystemExit`` raised
    by the command's OWN run time code is mapped by :func:`_systemexit_result`
    instead of escaping and killing the server. Any OTHER raised exception
    during dispatch -> ``isError: true`` with the exception's ``type:
    message`` text. ``KeyboardInterrupt`` is not caught and still propagates.
    """
    core = root_cls if isinstance(root_cls, _ServerCore) else _core_for_class(root_cls)
    root_parser, nodes = core.root_parser, core.nodes
    node = nodes.get(name) if isinstance(name, str) else None
    if (
        node is None
        or _is_namespace_node(node.parser)
        or _is_mcp_command_node(node)
        or node.excluded
    ):
        raise UnknownToolError("unknown tool: %r" % (name,))

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise InvalidArgumentsError(
            "arguments must be a JSON object, got %s" % (type(arguments).__name__,)
        )

    schema = _input_schema_for_node(node)
    _validate_arguments(schema, arguments)
    # An integral float passed validation for an integer field; argv needs
    # the integer's own spelling ("5", not "5.0").
    integer_fields = {
        key
        for key, prop in schema["properties"].items()
        if prop.get("type") == "integer"
    }
    arguments = {
        key: int(value) if key in integer_fields and isinstance(value, float) else value
        for key, value in arguments.items()
    }

    chain = node.ancestors + (node,)
    expected_path = tuple(step.own_name for step in chain)
    # A field name declared at several levels binds only at the deepest one
    # (as in `_input_schema_for_node`), so a leaf's value cannot flip an
    # ancestor's same-named flag such as a hidden `--force`.
    field_owner: dict[str, int] = {}
    for i, step in enumerate(chain):
        for fname in _step_field_names(step):
            field_owner[fname] = i
    try:
        argv: list[str] = []
        ancestor_forbidden: frozenset = frozenset()
        for i, step in enumerate(chain):
            shadowed = frozenset(
                fname for fname, owner in field_owner.items() if owner != i
            )
            argv.extend(
                _synthesize_step_argv(
                    step,
                    arguments,
                    skip=shadowed,
                    ancestor_forbidden=ancestor_forbidden,
                    pin_positionals=i + 1 < len(chain),
                )
            )
            ancestor_forbidden = ancestor_forbidden | _sibling_names(step.parser)
            if i + 1 < len(chain):
                argv.append(chain[i + 1].own_name)
        passthrough = arguments.get("--") or None
        if passthrough:
            argv.append("--")
            argv.extend(passthrough)
    except InvalidArgumentsError:
        raise
    except ValueError as exc:
        return _text_result(str(exc), is_error=True)
    # argparse expands any argv token starting with a parser's
    # `fromfile_prefix_chars` into the lines of the named file, so a value
    # must never start with one (long-flag values start with `-`, not here).
    prefixes = "".join(
        getattr(p, "fromfile_prefix_chars", None) or ""
        for p in [root_parser] + [step.parser for step in chain]
    )
    for token in argv:
        if token and token[0] in prefixes:
            raise InvalidArgumentsError(
                "value %r cannot be passed: it starts with a response-file "
                "prefix character that argparse would read as a file" % (token,)
            )

    out = _io.StringIO()
    err = _io.StringIO()
    all_parsers = [root_parser] + [n.parser for n in nodes.values()]
    real_stderr = _sys.stderr
    try:
        with _contextlib.redirect_stdout(out), _contextlib.redirect_stderr(err):
            old_stdin = _sys.stdin
            _sys.stdin = _io.StringIO("")
            try:
                with (
                    _muted_color(all_parsers),
                    _rebound_stderr_logging(err, real_stderr),
                ):
                    try:
                        instance = root_parser.parse_args(argv)
                    except SystemExit as exc:
                        hidden = frozenset().union(
                            *(_hidden_choice_names(n.parser) for n in nodes.values())
                        )
                        message = _client_visible_parse_error(
                            err.getvalue().strip(), hidden
                        ) or ("argument error (exit code %r)" % (exc.code,))
                        return _text_result(message, is_error=True)
                    # Popped, as `runtime._run_app` does, so the markers never
                    # reach a command's `vars(instance)`.
                    actual_path = vars(instance).pop("_duho_mcp_path_", None)
                    dispatched_module_command = vars(instance).pop(
                        "_duho_module_command_", None
                    )
                    if node.module_command is not None:
                        identity_ok = dispatched_module_command is node.module_command
                        target: object = node.module_command
                    else:
                        identity_ok = type(instance) is node.cls
                        target = node.cls
                    if not identity_ok or actual_path != expected_path:
                        actual_desc = (
                            ".".join(actual_path)
                            if isinstance(actual_path, tuple)
                            else type(instance).__name__
                        )
                        return _text_result(
                            "tool %r did not resolve to the requested command "
                            "(dispatched %r instead); refusing to run it"
                            % (name, actual_desc),
                            is_error=True,
                        )
                    try:
                        result = core.dispatch(target, instance)
                    except SystemExit as exc:
                        return _systemexit_result(exc, out.getvalue(), err.getvalue())
            finally:
                _sys.stdin = old_stdin
    except (
        Exception
    ) as exc:  # noqa: BLE001 - one broken command must not crash the server
        if _error_status(exc, core.root_cls) is not None:
            # An error the command meant to end with: its words, not a crash.
            parts = [out.getvalue(), err.getvalue().strip(), str(exc)]
            return _text_result("\n".join(p for p in parts if p), is_error=True)
        # The client only ever sees "Type: message"; the stack that says WHERE
        # the command broke exists nowhere else, so log it server-side too
        # (traceback under DUHO_TRACEBACK=1).
        _log_exception(
            _LOGGER,
            "tool %r raised: %s: %s",
            name,
            type(exc).__name__,
            exc,
        )
        return _text_result("%s: %s" % (type(exc).__name__, exc), is_error=True)

    stdout_text = out.getvalue()

    if isinstance(result, _Result) and (
        result.text is not None or result.value is not None
    ):
        words = result.text if result.text is not None else _render(result.value)
        parts = [stdout_text, words]
        return _text_result(
            "\n".join(part for part in parts if part), is_error=result.is_error
        )

    if isinstance(result, int):
        if result == 0:
            return _text_result(stdout_text, is_error=_is_error_flag(result))
        trailing = "exit code: %d" % result
        meaning = _declared_exit_meaning(core.root_cls, int(result))
        if meaning is not None:
            trailing += " (%s)" % meaning
        parts = [
            part for part in (stdout_text, err.getvalue().strip(), trailing) if part
        ]
        return _text_result("\n".join(parts), is_error=_is_error_flag(result, True))

    return _text_result(_render(result))
