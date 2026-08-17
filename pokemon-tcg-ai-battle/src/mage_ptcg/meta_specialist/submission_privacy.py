"""Path- and type-aware privacy checks for submission package members.

Runtime source is deliberately not scanned for local-contract words: source
files may import or document those contracts without carrying local data.  Only
JSON-like submission assets are parsed for local-record/reveal/private-binding
shapes, and callers must supply the package's explicit member allowlist.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping, Sequence
import json
import math
from pathlib import PurePosixPath
import re
import string


class SubmissionPrivacyError(ValueError):
    """Raised when a submission member can carry local-only data."""


SPECIALIST_V2_MODEL_MANIFEST_ROLE = "specialist-v2-model-manifest"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_JSON_NODES = 100_000
_MAX_JSON_OBJECT_PAIRS = 100_000
_LOCAL_SCHEMA_MARKERS = frozenset(
    {
        "canonical-decision-v1",
        "canonical-specialist-decision-v2-local",
        "canonical-specialist-dataset-v2-local",
        "actor-visible-action-binding-v1",
        "c5-public-action-v1",
    }
)
_LOCAL_JSON_KEYS = frozenset(
    {
        "actor_payload",
        "action_key_digest",
        "action_key_payload",
        "actor_binding",
        "actor_identity_payload",
        "action_key_core",
        "c5_record_id",
        "context_card",
        "deck_reveal",
        "effect",
        "execution_index",
        "execution_indices",
        "hand_card_ids",
        "information_state",
        "local_action_id",
        "looking",
        "own_private_state",
        "own_private_state_json",
        "opponent_deck",
        "opponent_hand",
        "opponent_hand_ids",
        "option_index",
        "option_indices",
        "private_action_id",
        "private_action_key_digest",
        "raw_observation",
        "serial",
        "selection_view",
    }
)
_NORMALIZED_LOCAL_JSON_KEYS = frozenset(
    _NON_ALNUM.sub("", key.casefold()) for key in _LOCAL_JSON_KEYS
)
_FORBIDDEN_AUXILIARY_PATH_TOKENS = frozenset(
    {
        "actor",
        "binding",
        "dataset",
        "datasets",
        "local",
        "record",
        "records",
        "reveal",
        "training",
    }
)
_SPECIALIST_V2_MODEL_MANIFEST_FIELDS = frozenset(
    {"feature_domain", "c1_schema_version", "feature_schema_hash"}
)
_NOT_STATIC_SOURCE_VALUE = object()
_UNKNOWN_STATIC_KEY = object()
_UNKNOWN_STATIC_JSON_TEXT = object()
_MAX_STATIC_COMPOSITION_CHARS = 64 * 1024
_MAX_STATIC_CONTAINMENT_NODES = 2_048
_MAX_STATIC_CONTAINMENT_DEPTH = 64


def _normalize_json_key(key: str) -> str:
    """Compare JSON object keys without case or separator bypasses."""
    return _NON_ALNUM.sub("", key.casefold())


def _path_tokens(part: str) -> set[str]:
    """Split a non-source path component without trusting its punctuation/case."""
    camel_split = _CAMEL_CASE_BOUNDARY.sub(" ", part)
    return {
        token
        for token in _NON_ALNUM.split(camel_split.casefold())
        if token
    }


def _safe_member_path(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise SubmissionPrivacyError("submission member path is invalid")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or "\\" in name
        or path.as_posix() != name
    ):
        raise SubmissionPrivacyError("submission member path is unsafe")
    return name


def _reject_auxiliary_path(name: str) -> None:
    path = PurePosixPath(name)
    suffixes = "".join(path.suffixes).lower()
    if suffixes.endswith(".jsonl") or suffixes.endswith(".jsonl.gz"):
        raise SubmissionPrivacyError("submission member is a JSONL/training dataset")
    if path.suffix.lower() == ".py":
        return
    if any(
        _path_tokens(part).intersection(_FORBIDDEN_AUXILIARY_PATH_TOKENS)
        for part in path.parts
    ):
        raise SubmissionPrivacyError("submission member has a local-only auxiliary path")


class _StrictJSONError(ValueError):
    """Internal signal for a syntactically accepted but unsafe JSON value."""


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    if len(pairs) > _MAX_JSON_OBJECT_PAIRS:
        raise _StrictJSONError("object exceeds the JSON member pair bound")
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _StrictJSONError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(token: str) -> object:
    raise _StrictJSONError(f"JSON member contains forbidden non-finite constant {token}")


def _json_member(name: str, data: bytes) -> object:
    if len(data) > _MAX_JSON_BYTES:
        raise SubmissionPrivacyError(f"submission JSON member exceeds byte bound: {name}")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _StrictJSONError, RecursionError) as exc:
        raise SubmissionPrivacyError(f"submission JSON member is not strict JSON: {name}") from exc
    return value


def _reject_local_json(value: object) -> None:
    pending = [value]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > _MAX_JSON_NODES:
            raise SubmissionPrivacyError("submission JSON member exceeds the privacy scan bound")
        if isinstance(current, dict):
            for key, child in current.items():
                if (
                    isinstance(key, str)
                    and _normalize_json_key(key) in _NORMALIZED_LOCAL_JSON_KEYS
                ):
                    raise SubmissionPrivacyError("submission JSON member contains a local-only field")
                pending.append(child)
        elif isinstance(current, (list, tuple, set, frozenset)):
            pending.extend(current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise SubmissionPrivacyError("submission JSON member contains a non-finite number")
        elif isinstance(current, str) and current in _LOCAL_SCHEMA_MARKERS:
            raise SubmissionPrivacyError("submission JSON member contains a local-only schema")


def _reject_source_json_literal(name: str, value: str | bytes) -> None:
    """Reject JSON-shaped string/bytes literals carrying local-only data.

    Python sources may name or document contracts, so ordinary string literals
    remain valid.  Only a literal that is itself a complete JSON object/array
    is interpreted as data.
    """
    data = value.encode("utf-8") if isinstance(value, str) else value
    if not data.lstrip().startswith((b"{", b"[")):
        return
    try:
        parsed = _json_member(name, data)
    except SubmissionPrivacyError:
        # Duplicate JSON keys are invalid submission JSON, but a string literal
        # containing one can still be a private-data carrier.  Parse only for
        # shape detection here; malformed source text itself remains allowed.
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return
    _reject_local_json(parsed)


_STATIC_ITERABLE_TYPES = (dict, list, tuple, set, frozenset, str, bytes)
_STATIC_FORMAT_VALUE_TYPES = (str, int, float, bool, type(None))


def _is_static_analysis_sentinel(value: object) -> bool:
    return (
        value is _NOT_STATIC_SOURCE_VALUE
        or value is _UNKNOWN_STATIC_KEY
        or value is _UNKNOWN_STATIC_JSON_TEXT
    )


def _bounded_text_parts(parts: list[str]) -> str | object:
    if sum(len(part) for part in parts) > _MAX_STATIC_COMPOSITION_CHARS:
        return _UNKNOWN_STATIC_JSON_TEXT
    return "".join(parts)


def _static_string_method_value(node: ast.Call) -> object:
    """Evaluate a small bounded subset of pure built-in string methods.

    This subset exists for JSON-shaped string payloads, not as a growing
    catalogue of every method that might form a dictionary key.  Unknown
    computed keys are rejected separately by ``_UNKNOWN_STATIC_KEY``.
    """
    if not isinstance(node.func, ast.Attribute):
        return _NOT_STATIC_SOURCE_VALUE
    receiver = _static_source_value(node.func.value)
    if type(receiver) is not str or len(receiver) > _MAX_STATIC_COMPOSITION_CHARS:
        return _NOT_STATIC_SOURCE_VALUE
    arguments = _static_call_arguments(node.args)
    if _is_static_analysis_sentinel(arguments):
        return arguments

    if node.func.attr == "join" and not node.keywords and len(arguments) == 1:
        items = arguments[0]
        if not isinstance(items, (list, tuple)) or any(
            type(item) is not str for item in items
        ):
            return _NOT_STATIC_SOURCE_VALUE
        return _bounded_text_parts(
            [item + (receiver if index + 1 < len(items) else "") for index, item in enumerate(items)]
        )

    if node.func.attr == "replace" and not node.keywords and 2 <= len(arguments) <= 3:
        old, new = arguments[:2]
        count = arguments[2] if len(arguments) == 3 else -1
        if type(old) is not str or type(new) is not str or type(count) is not int:
            return _NOT_STATIC_SOURCE_VALUE
        replacements = receiver.count(old) if count < 0 else min(receiver.count(old), count)
        projected = len(receiver) + replacements * (len(new) - len(old))
        if projected > _MAX_STATIC_COMPOSITION_CHARS:
            return _UNKNOWN_STATIC_JSON_TEXT
        return receiver.replace(old, new, count)

    if node.func.attr == "format":
        positional = tuple(arguments)
        keywords: dict[str, object] = {}
        for keyword in node.keywords:
            value = _static_source_value(keyword.value)
            if _is_static_analysis_sentinel(value):
                return value
            if keyword.arg is None or keyword.arg in keywords:
                return _NOT_STATIC_SOURCE_VALUE
            keywords[keyword.arg] = value
        if any(type(value) not in _STATIC_FORMAT_VALUE_TYPES for value in (*positional, *keywords.values())):
            return _NOT_STATIC_SOURCE_VALUE
        parts: list[str] = []
        automatic_index = 0
        try:
            parsed = string.Formatter().parse(receiver)
            for literal, field_name, format_spec, conversion in parsed:
                parts.append(literal)
                if field_name is None:
                    continue
                if conversion is not None or format_spec not in {"", "s", "d"}:
                    return _NOT_STATIC_SOURCE_VALUE
                if field_name == "":
                    if automatic_index >= len(positional):
                        return _NOT_STATIC_SOURCE_VALUE
                    value = positional[automatic_index]
                    automatic_index += 1
                elif field_name.isdecimal():
                    index = int(field_name)
                    if index >= len(positional):
                        return _NOT_STATIC_SOURCE_VALUE
                    value = positional[index]
                elif field_name.isidentifier() and field_name in keywords:
                    value = keywords[field_name]
                else:
                    # Attribute/index traversal is intentionally outside the
                    # pure closed subset because it can execute user code.
                    return _NOT_STATIC_SOURCE_VALUE
                if format_spec == "d" and type(value) is not int:
                    return _NOT_STATIC_SOURCE_VALUE
                parts.append(str(value))
                if sum(len(part) for part in parts) > _MAX_STATIC_COMPOSITION_CHARS:
                    return _UNKNOWN_STATIC_JSON_TEXT
        except (ValueError, TypeError):
            return _NOT_STATIC_SOURCE_VALUE
        return _bounded_text_parts(parts)

    return _NOT_STATIC_SOURCE_VALUE


def _simple_static_percent_format(template: str, value: object) -> object:
    """Format only bounded ``%s``/``%d``/``%%`` strings without width specs."""
    values = value if isinstance(value, tuple) else (value,)
    parts: list[str] = []
    value_index = 0
    index = 0
    while index < len(template):
        marker = template.find("%", index)
        if marker < 0:
            parts.append(template[index:])
            break
        parts.append(template[index:marker])
        if marker + 1 >= len(template):
            return _NOT_STATIC_SOURCE_VALUE
        kind = template[marker + 1]
        if kind == "%":
            parts.append("%")
        elif kind in {"s", "d"}:
            if value_index >= len(values):
                return _NOT_STATIC_SOURCE_VALUE
            item = values[value_index]
            value_index += 1
            if kind == "d":
                if type(item) is not int:
                    return _NOT_STATIC_SOURCE_VALUE
            elif type(item) not in _STATIC_FORMAT_VALUE_TYPES:
                return _NOT_STATIC_SOURCE_VALUE
            parts.append(str(item))
        else:
            return _NOT_STATIC_SOURCE_VALUE
        if sum(len(part) for part in parts) > _MAX_STATIC_COMPOSITION_CHARS:
            return _UNKNOWN_STATIC_JSON_TEXT
        index = marker + 2
    if value_index != len(values):
        return _NOT_STATIC_SOURCE_VALUE
    return _bounded_text_parts(parts)


def _is_statically_contained_expression(node: ast.AST) -> bool | None:
    """Identify call syntax with no runtime data names, under explicit bounds.

    Callee names (for example ``zip``) describe the computation and are not
    data dependencies.  Any other ``Name`` proves that runtime input remains.
    ``None`` means the expression exceeded the bound and is therefore handled
    fail-closed by its dictionary constructor caller.
    """
    pending: list[tuple[ast.AST, int]] = [(node, 0)]
    visited = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > _MAX_STATIC_CONTAINMENT_NODES or depth > _MAX_STATIC_CONTAINMENT_DEPTH:
            return None
        if isinstance(current, ast.Name):
            return False
        if isinstance(current, ast.Call):
            if isinstance(current.func, ast.Attribute):
                pending.append((current.func.value, depth + 1))
            elif not isinstance(current.func, ast.Name):
                pending.append((current.func, depth + 1))
            pending.extend((argument, depth + 1) for argument in current.args)
            pending.extend((keyword.value, depth + 1) for keyword in current.keywords)
            continue
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(current))
    return True


def _static_iterable_items(node: ast.AST) -> tuple[object, ...] | object:
    """Reduce one ``*expr`` operand without executing its iterator.

    The source privacy scanner is intentionally a closed evaluator.  Calling
    ``iter`` on an arbitrary object would run application code, so unpacking is
    recognized only for values already produced by this evaluator's literal
    constructors.  ``dict`` uses its ordinary key iteration, matching Python's
    display/call semantics closely enough for the privacy scan.
    """
    value = _static_source_value(node)
    if value is _UNKNOWN_STATIC_KEY or value is _UNKNOWN_STATIC_JSON_TEXT:
        return value
    if value is _NOT_STATIC_SOURCE_VALUE or not isinstance(value, _STATIC_ITERABLE_TYPES):
        return _NOT_STATIC_SOURCE_VALUE
    try:
        return tuple(value)
    except TypeError:
        return _NOT_STATIC_SOURCE_VALUE


def _static_call_arguments(args: list[ast.expr]) -> tuple[object, ...] | object:
    """Reduce positional call arguments, including literal ``*`` expansion."""
    result: list[object] = []
    for argument in args:
        if isinstance(argument, ast.Starred):
            expanded = _static_iterable_items(argument.value)
            if expanded is _NOT_STATIC_SOURCE_VALUE:
                return _NOT_STATIC_SOURCE_VALUE
            if expanded is _UNKNOWN_STATIC_KEY or expanded is _UNKNOWN_STATIC_JSON_TEXT:
                return expanded
            result.extend(expanded)
            continue
        value = _static_source_value(argument)
        if value is _UNKNOWN_STATIC_KEY or value is _UNKNOWN_STATIC_JSON_TEXT:
            return value
        if value is _NOT_STATIC_SOURCE_VALUE:
            return _NOT_STATIC_SOURCE_VALUE
        result.append(value)
    return tuple(result)


def _static_source_value(node: ast.AST) -> object:
    """Return a value only when ``node`` is reducible without executing code.

    This deliberately recognizes ordinary literal-building syntax rather than
    names, attribute access, comprehensions, or calls outside the small closed
    constructor set below.  A source mapping with a runtime value therefore
    remains source code, while a fully encoded payload is inspected as data.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[object] = []
        for item in node.elts:
            if isinstance(item, ast.Starred):
                expanded = _static_iterable_items(item.value)
                if expanded is _NOT_STATIC_SOURCE_VALUE:
                    return _NOT_STATIC_SOURCE_VALUE
                if expanded is _UNKNOWN_STATIC_KEY or expanded is _UNKNOWN_STATIC_JSON_TEXT:
                    return expanded
                values.extend(expanded)
                continue
            value = _static_source_value(item)
            if value is _NOT_STATIC_SOURCE_VALUE:
                return _NOT_STATIC_SOURCE_VALUE
            if value is _UNKNOWN_STATIC_KEY or value is _UNKNOWN_STATIC_JSON_TEXT:
                return value
            values.append(value)
        try:
            if isinstance(node, ast.List):
                return list(values)
            if isinstance(node, ast.Tuple):
                return tuple(values)
            return set(values)
        except TypeError:
            return _NOT_STATIC_SOURCE_VALUE
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            value = _static_source_value(value_node)
            if value is _NOT_STATIC_SOURCE_VALUE:
                return _NOT_STATIC_SOURCE_VALUE
            if value is _UNKNOWN_STATIC_KEY or value is _UNKNOWN_STATIC_JSON_TEXT:
                return value
            if key_node is None:
                if not isinstance(value, dict):
                    return _NOT_STATIC_SOURCE_VALUE
                result.update(value)
                continue
            key = _static_source_value(key_node)
            if key is _NOT_STATIC_SOURCE_VALUE:
                # A direct runtime locator remains ordinary program logic.
                # Fail closed only when syntax computes the key from an
                # otherwise-static expression; this preserves existing tables
                # such as ``{_MAIN_SELECT_TYPE: "MAIN"}``.
                if isinstance(key_node, ast.Name):
                    return _NOT_STATIC_SOURCE_VALUE
                if isinstance(key_node, (ast.Attribute, ast.Subscript)) and (
                    _is_statically_contained_expression(key_node) is False
                ):
                    return _NOT_STATIC_SOURCE_VALUE
                return _UNKNOWN_STATIC_KEY
            if key is _UNKNOWN_STATIC_KEY or key is _UNKNOWN_STATIC_JSON_TEXT:
                return _UNKNOWN_STATIC_KEY
            try:
                result[key] = value
            except TypeError:
                return _NOT_STATIC_SOURCE_VALUE
        return result
    if isinstance(node, ast.UnaryOp):
        value = _static_source_value(node.operand)
        if value is _NOT_STATIC_SOURCE_VALUE:
            return _NOT_STATIC_SOURCE_VALUE
        try:
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.Invert):
                return ~value
        except TypeError:
            return _NOT_STATIC_SOURCE_VALUE
        return _NOT_STATIC_SOURCE_VALUE
    if isinstance(node, ast.BinOp):
        left = _static_source_value(node.left)
        right = _static_source_value(node.right)
        if left is _NOT_STATIC_SOURCE_VALUE or right is _NOT_STATIC_SOURCE_VALUE:
            return _NOT_STATIC_SOURCE_VALUE
        if left is _UNKNOWN_STATIC_KEY or left is _UNKNOWN_STATIC_JSON_TEXT:
            return left
        if right is _UNKNOWN_STATIC_KEY or right is _UNKNOWN_STATIC_JSON_TEXT:
            return right
        try:
            if isinstance(node.op, ast.Add) and type(left) is type(right) and isinstance(
                left, (str, bytes, tuple, list)
            ):
                return left + right
            if isinstance(node.op, ast.BitOr) and isinstance(left, dict) and isinstance(right, dict):
                return left | right
            if isinstance(node.op, ast.Mod) and type(left) is str:
                result = _simple_static_percent_format(left, right)
                if (
                    result is _NOT_STATIC_SOURCE_VALUE
                    and left.lstrip().startswith(("{", "["))
                ):
                    return _UNKNOWN_STATIC_JSON_TEXT
                return result
        except TypeError:
            return _NOT_STATIC_SOURCE_VALUE
        return _NOT_STATIC_SOURCE_VALUE
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return _NOT_STATIC_SOURCE_VALUE
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"list", "tuple", "set", "frozenset"}:
            if node.keywords:
                return _NOT_STATIC_SOURCE_VALUE
            arguments = _static_call_arguments(node.args)
            if arguments is _UNKNOWN_STATIC_KEY or arguments is _UNKNOWN_STATIC_JSON_TEXT:
                return arguments
            if arguments is _NOT_STATIC_SOURCE_VALUE or len(arguments) > 1:
                return _NOT_STATIC_SOURCE_VALUE
            value: object = () if not arguments else arguments[0]
            if not isinstance(value, _STATIC_ITERABLE_TYPES):
                return _NOT_STATIC_SOURCE_VALUE
            try:
                return {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}[node.func.id](value)
            except TypeError:
                return _NOT_STATIC_SOURCE_VALUE
        if node.func.id == "dict":
            arguments = _static_call_arguments(node.args)
            if arguments is _UNKNOWN_STATIC_KEY or arguments is _UNKNOWN_STATIC_JSON_TEXT:
                return arguments
            if arguments is _NOT_STATIC_SOURCE_VALUE:
                containment = tuple(
                    _is_statically_contained_expression(argument)
                    for argument in node.args
                )
                if containment and all(value is not False for value in containment):
                    return _UNKNOWN_STATIC_KEY
                return _NOT_STATIC_SOURCE_VALUE
            if len(arguments) > 1:
                return _NOT_STATIC_SOURCE_VALUE
            result: dict[object, object] = {}
            if arguments:
                source = arguments[0]
                if isinstance(source, dict):
                    result.update(source)
                elif isinstance(source, (list, tuple, set, frozenset)):
                    try:
                        for pair in source:
                            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                                return _NOT_STATIC_SOURCE_VALUE
                            result[pair[0]] = pair[1]
                    except TypeError:
                        return _NOT_STATIC_SOURCE_VALUE
                else:
                    return _NOT_STATIC_SOURCE_VALUE
            for keyword in node.keywords:
                value = _static_source_value(keyword.value)
                if value is _NOT_STATIC_SOURCE_VALUE:
                    return _NOT_STATIC_SOURCE_VALUE
                if value is _UNKNOWN_STATIC_KEY or value is _UNKNOWN_STATIC_JSON_TEXT:
                    return value
                if keyword.arg is None:
                    if not isinstance(value, dict):
                        return _NOT_STATIC_SOURCE_VALUE
                    result.update(value)
                else:
                    result[keyword.arg] = value
            return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        string_value = _static_string_method_value(node)
        if string_value is not _NOT_STATIC_SOURCE_VALUE:
            return string_value
        receiver = _static_source_value(node.func.value)
        if type(receiver) is str:
            containment = tuple(
                _is_statically_contained_expression(argument)
                for argument in node.args
            ) + tuple(
                _is_statically_contained_expression(keyword.value)
                for keyword in node.keywords
            )
            if all(value is not False for value in containment):
                # A pure-looking string computation outside the deliberately
                # small bounded evaluator is not silently reclassified as
                # runtime code.  This also covers future method spellings
                # without extending a security-sensitive method allowlist.
                return _UNKNOWN_STATIC_JSON_TEXT
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "dict"
        and node.func.attr == "fromkeys"
        and not node.keywords
    ):
        arguments = _static_call_arguments(node.args)
        if arguments is _UNKNOWN_STATIC_KEY or arguments is _UNKNOWN_STATIC_JSON_TEXT:
            return arguments
        if arguments is _NOT_STATIC_SOURCE_VALUE or not 1 <= len(arguments) <= 2:
            return _NOT_STATIC_SOURCE_VALUE
        keys = arguments[0]
        value = arguments[1] if len(arguments) == 2 else None
        if not isinstance(keys, _STATIC_ITERABLE_TYPES):
            return _NOT_STATIC_SOURCE_VALUE
        try:
            return dict.fromkeys(keys, value)
        except TypeError:
            return _NOT_STATIC_SOURCE_VALUE
    return _NOT_STATIC_SOURCE_VALUE


_MAX_FLOW_STATIC_BINDINGS = 512
_MAX_FLOW_STATIC_STATEMENTS = 10_000
_FLOW_STATIC_SCALAR_TYPES = (str, bytes, int, float, bool, type(None))


def _is_flow_bindable_static_value(value: object) -> bool:
    if type(value) in _FLOW_STATIC_SCALAR_TYPES:
        return not isinstance(value, float) or math.isfinite(value)
    if isinstance(value, (tuple, frozenset)):
        return len(value) <= _MAX_FLOW_STATIC_BINDINGS and all(
            _is_flow_bindable_static_value(item) for item in value
        )
    return False


def _flow_static_iterable_items(
    node: ast.AST,
    bindings: Mapping[str, object],
) -> tuple[object, ...] | object:
    """Expand a proven-static iterable without running submission code."""
    value = _flow_static_value(node, bindings)
    if _is_static_analysis_sentinel(value) or not isinstance(value, _STATIC_ITERABLE_TYPES):
        return _NOT_STATIC_SOURCE_VALUE
    try:
        return tuple(value)
    except TypeError:
        return _NOT_STATIC_SOURCE_VALUE


def _flow_static_call_arguments(
    arguments: Sequence[ast.expr],
    bindings: Mapping[str, object],
) -> tuple[object, ...] | object:
    """Resolve a bounded argument sequence, including literal ``*`` forms."""
    result: list[object] = []
    for argument in arguments:
        if isinstance(argument, ast.Starred):
            expanded = _flow_static_iterable_items(argument.value, bindings)
            if _is_static_analysis_sentinel(expanded):
                return _NOT_STATIC_SOURCE_VALUE
            result.extend(expanded)
            continue
        value = _flow_static_value(argument, bindings)
        if _is_static_analysis_sentinel(value):
            return _NOT_STATIC_SOURCE_VALUE
        result.append(value)
    return tuple(result)


def _flow_static_string_method_value(
    node: ast.Call,
    bindings: Mapping[str, object],
) -> object:
    """Reduce the same bounded pure string subset with resolved name aliases."""
    if not isinstance(node.func, ast.Attribute):
        return _NOT_STATIC_SOURCE_VALUE
    receiver = _flow_static_value(node.func.value, bindings)
    if type(receiver) is not str or len(receiver) > _MAX_STATIC_COMPOSITION_CHARS:
        return _NOT_STATIC_SOURCE_VALUE
    arguments = _flow_static_call_arguments(node.args, bindings)
    if _is_static_analysis_sentinel(arguments):
        return _NOT_STATIC_SOURCE_VALUE

    if node.func.attr == "join" and not node.keywords and len(arguments) == 1:
        items = arguments[0]
        if not isinstance(items, (list, tuple)) or any(type(item) is not str for item in items):
            return _NOT_STATIC_SOURCE_VALUE
        return _bounded_text_parts(
            [item + (receiver if index + 1 < len(items) else "") for index, item in enumerate(items)]
        )

    if node.func.attr == "replace" and not node.keywords and 2 <= len(arguments) <= 3:
        old, new = arguments[:2]
        count = arguments[2] if len(arguments) == 3 else -1
        if type(old) is not str or type(new) is not str or type(count) is not int:
            return _NOT_STATIC_SOURCE_VALUE
        replacements = receiver.count(old) if count < 0 else min(receiver.count(old), count)
        if len(receiver) + replacements * (len(new) - len(old)) > _MAX_STATIC_COMPOSITION_CHARS:
            return _NOT_STATIC_SOURCE_VALUE
        return receiver.replace(old, new, count)

    if node.func.attr != "format":
        return _NOT_STATIC_SOURCE_VALUE
    keywords: dict[str, object] = {}
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg in keywords:
            return _NOT_STATIC_SOURCE_VALUE
        value = _flow_static_value(keyword.value, bindings)
        if _is_static_analysis_sentinel(value):
            return _NOT_STATIC_SOURCE_VALUE
        keywords[keyword.arg] = value
    if any(type(value) not in _STATIC_FORMAT_VALUE_TYPES for value in (*arguments, *keywords.values())):
        return _NOT_STATIC_SOURCE_VALUE
    parts: list[str] = []
    automatic_index = 0
    try:
        for literal, field_name, format_spec, conversion in string.Formatter().parse(receiver):
            parts.append(literal)
            if field_name is None:
                continue
            if conversion is not None or format_spec not in {"", "s", "d"}:
                return _NOT_STATIC_SOURCE_VALUE
            if field_name == "":
                if automatic_index >= len(arguments):
                    return _NOT_STATIC_SOURCE_VALUE
                value = arguments[automatic_index]
                automatic_index += 1
            elif field_name.isdecimal() and int(field_name) < len(arguments):
                value = arguments[int(field_name)]
            elif field_name.isidentifier() and field_name in keywords:
                value = keywords[field_name]
            else:
                return _NOT_STATIC_SOURCE_VALUE
            if format_spec == "d" and type(value) is not int:
                return _NOT_STATIC_SOURCE_VALUE
            parts.append(str(value))
    except (ValueError, TypeError):
        return _NOT_STATIC_SOURCE_VALUE
    return _bounded_text_parts(parts)


def _flow_static_value(node: ast.AST, bindings: Mapping[str, object]) -> object:
    """Resolve a bounded pure expression from immutable lexical bindings.

    This evaluator is deliberately closed: it never invokes a submission
    callable, property, iterator, import, or user-defined conversion.  It is
    used only for common literal/constructor/string-composition forms that the
    source gate treats as statically encoded data.
    """
    if isinstance(node, ast.Name):
        return bindings.get(node.id, _NOT_STATIC_SOURCE_VALUE)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values: list[object] = []
        for item in node.elts:
            if isinstance(item, ast.Starred):
                expanded = _flow_static_iterable_items(item.value, bindings)
                if _is_static_analysis_sentinel(expanded):
                    return _NOT_STATIC_SOURCE_VALUE
                values.extend(expanded)
                continue
            value = _flow_static_value(item, bindings)
            if _is_static_analysis_sentinel(value):
                return _NOT_STATIC_SOURCE_VALUE
            values.append(value)
        try:
            if isinstance(node, ast.Tuple):
                return tuple(values)
            if isinstance(node, ast.List):
                return list(values)
            return set(values)
        except TypeError:
            return _NOT_STATIC_SOURCE_VALUE
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            value = _flow_static_value(value_node, bindings)
            if _is_static_analysis_sentinel(value):
                return _NOT_STATIC_SOURCE_VALUE
            if key_node is None:
                if not isinstance(value, dict):
                    return _NOT_STATIC_SOURCE_VALUE
                result.update(value)
                continue
            key = _flow_static_value(key_node, bindings)
            if _is_static_analysis_sentinel(key):
                return _NOT_STATIC_SOURCE_VALUE
            try:
                result[key] = value
            except TypeError:
                return _NOT_STATIC_SOURCE_VALUE
        return result
    if isinstance(node, ast.UnaryOp):
        value = _flow_static_value(node.operand, bindings)
        if _is_static_analysis_sentinel(value):
            return _NOT_STATIC_SOURCE_VALUE
        try:
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.Invert):
                return ~value
        except TypeError:
            pass
        return _NOT_STATIC_SOURCE_VALUE
    if isinstance(node, ast.BinOp):
        left = _flow_static_value(node.left, bindings)
        right = _flow_static_value(node.right, bindings)
        if _is_static_analysis_sentinel(left) or _is_static_analysis_sentinel(right):
            return _NOT_STATIC_SOURCE_VALUE
        try:
            if isinstance(node.op, ast.Add) and type(left) is type(right) and isinstance(
                left, (str, bytes, tuple, list)
            ):
                return left + right
            if isinstance(node.op, ast.BitOr) and isinstance(left, dict) and isinstance(right, dict):
                return left | right
            if isinstance(node.op, ast.Mod) and type(left) is str:
                return _simple_static_percent_format(left, right)
        except TypeError:
            pass
        return _NOT_STATIC_SOURCE_VALUE
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value_node in node.values:
            if isinstance(value_node, ast.Constant) and type(value_node.value) is str:
                parts.append(value_node.value)
                continue
            if not isinstance(value_node, ast.FormattedValue) or value_node.conversion not in {-1, ord("s")}:
                return _NOT_STATIC_SOURCE_VALUE
            if value_node.format_spec is not None:
                return _NOT_STATIC_SOURCE_VALUE
            value = _flow_static_value(value_node.value, bindings)
            if _is_static_analysis_sentinel(value) or type(value) not in _STATIC_FORMAT_VALUE_TYPES:
                return _NOT_STATIC_SOURCE_VALUE
            parts.append(str(value))
        return _bounded_text_parts(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        arguments = _flow_static_call_arguments(node.args, bindings)
        if _is_static_analysis_sentinel(arguments):
            return _NOT_STATIC_SOURCE_VALUE
        if node.func.id in {"list", "tuple", "set", "frozenset"} and not node.keywords:
            if len(arguments) > 1:
                return _NOT_STATIC_SOURCE_VALUE
            value: object = () if not arguments else arguments[0]
            if not isinstance(value, _STATIC_ITERABLE_TYPES):
                return _NOT_STATIC_SOURCE_VALUE
            try:
                return {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}[node.func.id](value)
            except TypeError:
                return _NOT_STATIC_SOURCE_VALUE
        if node.func.id == "zip" and not node.keywords:
            if not arguments or any(not isinstance(value, (list, tuple, str, bytes)) for value in arguments):
                return _NOT_STATIC_SOURCE_VALUE
            if min(len(value) for value in arguments) > _MAX_FLOW_STATIC_BINDINGS:
                return _NOT_STATIC_SOURCE_VALUE
            return tuple(zip(*arguments, strict=False))
        if node.func.id == "dict" and len(arguments) <= 1:
            result: dict[object, object] = {}
            try:
                if arguments:
                    source = arguments[0]
                    if isinstance(source, dict):
                        result.update(source)
                    elif isinstance(source, (list, tuple, set, frozenset)):
                        for pair in source:
                            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                                return _NOT_STATIC_SOURCE_VALUE
                            result[pair[0]] = pair[1]
                    else:
                        return _NOT_STATIC_SOURCE_VALUE
                for keyword in node.keywords:
                    value = _flow_static_value(keyword.value, bindings)
                    if _is_static_analysis_sentinel(value):
                        return _NOT_STATIC_SOURCE_VALUE
                    if keyword.arg is None:
                        if not isinstance(value, dict):
                            return _NOT_STATIC_SOURCE_VALUE
                        result.update(value)
                    else:
                        result[keyword.arg] = value
            except TypeError:
                return _NOT_STATIC_SOURCE_VALUE
            return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        value = _flow_static_string_method_value(node, bindings)
        if value is not _NOT_STATIC_SOURCE_VALUE:
            return value
        if (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "dict"
            and node.func.attr == "fromkeys"
            and not node.keywords
        ):
            arguments = _flow_static_call_arguments(node.args, bindings)
            if _is_static_analysis_sentinel(arguments) or not 1 <= len(arguments) <= 2:
                return _NOT_STATIC_SOURCE_VALUE
            keys = arguments[0]
            value = arguments[1] if len(arguments) == 2 else None
            if not isinstance(keys, _STATIC_ITERABLE_TYPES):
                return _NOT_STATIC_SOURCE_VALUE
            try:
                return dict.fromkeys(keys, value)
            except TypeError:
                return _NOT_STATIC_SOURCE_VALUE
    value = _static_source_value(node)
    return value if not _is_static_analysis_sentinel(value) else _NOT_STATIC_SOURCE_VALUE


def _assignment_target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in target.elts:
            names.update(_assignment_target_names(item))
        return names
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        root = target.value
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        return {root.id} if isinstance(root, ast.Name) else set()
    return set()


class _ScopeAssignmentCollector(ast.NodeVisitor):
    """Collect bindings belonging to one lexical scope, excluding children."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802 - AST API
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        return

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        self.nonlocal_names.update(node.names)


def _scope_assigned_names(statements: Sequence[ast.stmt]) -> set[str]:
    collector = _ScopeAssignmentCollector()
    for statement in statements:
        collector.visit(statement)
    return collector.names.difference(collector.global_names, collector.nonlocal_names)


def _function_parameter_names(arguments: ast.arguments) -> set[str]:
    parameters = {
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }
    if arguments.vararg is not None:
        parameters.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        parameters.add(arguments.kwarg.arg)
    return parameters


def _scope_assignment_collector(
    statements: Sequence[ast.stmt],
) -> _ScopeAssignmentCollector:
    collector = _ScopeAssignmentCollector()
    for statement in statements:
        collector.visit(statement)
    return collector


def _scope_static_target_values(target: ast.AST, value: object) -> dict[str, object] | None:
    """Bind only simple immutable assignment targets from a pure value."""
    if isinstance(target, ast.Name):
        return {target.id: value} if _is_flow_bindable_static_value(value) else None
    if isinstance(target, (ast.Tuple, ast.List)):
        if (
            not isinstance(value, (tuple, list))
            or len(target.elts) != len(value)
            or any(isinstance(item, ast.Starred) for item in target.elts)
        ):
            return None
        result: dict[str, object] = {}
        for item, child_value in zip(target.elts, value, strict=True):
            child = _scope_static_target_values(item, child_value)
            if child is None or set(result).intersection(child):
                return None
            result.update(child)
        return result
    return None


def _scope_final_bindings(
    statements: Sequence[ast.stmt],
    inherited: Mapping[str, object],
) -> dict[str, object]:
    """Compute this lexical scope's provably final simple bindings.

    Only a name assigned exactly once by a direct ``Assign``/``AnnAssign`` in
    the scope can become a static alias.  Branches, loops, try/with/match
    blocks, augmented/deleted names, and ``global``/``nonlocal`` declarations
    remove a name from the environment rather than guessing a runtime path.
    The small fixed point handles forward aliases (including tuple unpacking)
    without evaluating submission code.
    """
    if len(statements) > _MAX_FLOW_STATIC_STATEMENTS:
        return {}
    collector = _scope_assignment_collector(statements)
    local_names = collector.names.difference(
        collector.global_names, collector.nonlocal_names
    )
    result = dict(inherited)
    for name in local_names | collector.global_names | collector.nonlocal_names:
        result.pop(name, None)

    ambiguous = set(collector.global_names) | set(collector.nonlocal_names)
    candidates: list[tuple[tuple[ast.AST, ...], ast.AST]] = []
    assignments: dict[str, int] = {}
    control_flow = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        getattr(ast, "TryStar", ast.Try),
        getattr(ast, "Match", ast.If),
    )
    for statement in statements:
        if isinstance(statement, control_flow):
            ambiguous.update(_scope_assigned_names((statement,)))
            continue
        if isinstance(statement, ast.Assign):
            targets = tuple(statement.targets)
            candidates.append((targets, statement.value))
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            candidates.append(((statement.target,), statement.value))
        else:
            # Augmented/deleted/named-expression/imported/function/class names
            # are not one-time immutable aliases.  Mask any inherited binding
            # rather than allowing a preceding literal assignment to survive.
            ambiguous.update(_scope_assigned_names((statement,)))
    for targets, _value in candidates:
        for target in targets:
            for name in _assignment_target_names(target):
                assignments[name] = assignments.get(name, 0) + 1
    for name, count in assignments.items():
        if count != 1:
            ambiguous.add(name)

    # Each pass can only add a direct one-time alias.  The hard bound avoids
    # unbounded source-driven work and also breaks cyclic aliases safely.
    for _ in range(len(candidates) + 1):
        changed = False
        for targets, expression in candidates:
            target_names = set().union(
                *(_assignment_target_names(target) for target in targets)
            )
            if not target_names or target_names.intersection(ambiguous):
                continue
            value = _flow_static_value(expression, result)
            if _is_static_analysis_sentinel(value):
                continue
            updates: dict[str, object] = {}
            for target in targets:
                resolved = _scope_static_target_values(target, value)
                if resolved is None:
                    updates = {}
                    break
                updates.update(resolved)
            if set(updates) != target_names:
                continue
            for name, bound_value in updates.items():
                if result.get(name, _NOT_STATIC_SOURCE_VALUE) != bound_value:
                    result[name] = bound_value
                    changed = True
        if not changed:
            break
    return result


class _FlowAwareStaticSourceScanner:
    """Conservative sequential analysis for immutable static name aliases."""

    def __init__(
        self,
        bindings: Mapping[str, object] | None = None,
        *,
        function_parent_bindings: Mapping[str, object] | None = None,
    ) -> None:
        self.bindings = dict(bindings or {})
        self.function_parent_bindings = (
            dict(function_parent_bindings)
            if function_parent_bindings is not None
            else None
        )
        self.statement_count = 0

    def _scan_expression(self, expression: ast.AST | None) -> None:
        if expression is None:
            return
        pending = [expression]
        while pending:
            current = pending.pop()
            resolved = _flow_static_value(current, self.bindings)
            if isinstance(resolved, (str, bytes)):
                _reject_source_json_literal("submission Python source", resolved)
            elif isinstance(resolved, (dict, list, tuple, set, frozenset)):
                _reject_local_json(resolved)
            if isinstance(current, ast.Dict):
                for key_node, value_node in zip(
                    current.keys, current.values, strict=True
                ):
                    if key_node is None:
                        continue
                    key = _flow_static_value(key_node, self.bindings)
                    value = _flow_static_value(value_node, self.bindings)
                    if _is_static_analysis_sentinel(key) or _is_static_analysis_sentinel(value):
                        continue
                    try:
                        payload = {key: value}
                    except TypeError:
                        continue
                    _reject_local_json(payload)
            if isinstance(current, ast.Call):
                value = _flow_static_value(current, self.bindings)
                if isinstance(value, dict):
                    _reject_local_json(value)
            if isinstance(current, ast.Lambda):
                # Lambda parameters create a new lexical scope.  The ordinary
                # source scan still checks all name-independent literals in it.
                pending.extend(current.args.defaults)
                pending.extend(
                    default
                    for default in current.args.kw_defaults
                    if default is not None
                )
                inherited = dict(self.bindings)
                for parameter in _function_parameter_names(current.args):
                    inherited.pop(parameter, None)
                child = _FlowAwareStaticSourceScanner(inherited)
                child._scan_expression(current.body)
                continue
            pending.extend(ast.iter_child_nodes(current))

    def _invalidate(self, names: Collection[str]) -> None:
        for name in names:
            self.bindings.pop(name, None)

    def _bind_target(self, target: ast.AST, value: object) -> None:
        if isinstance(target, ast.Name):
            if _is_flow_bindable_static_value(value):
                if len(self.bindings) >= _MAX_FLOW_STATIC_BINDINGS and target.id not in self.bindings:
                    self.bindings.clear()
                    return
                self.bindings[target.id] = value
            else:
                self.bindings.pop(target.id, None)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            if (
                isinstance(value, (tuple, list))
                and len(target.elts) == len(value)
                and not any(isinstance(item, ast.Starred) for item in target.elts)
            ):
                for item, child in zip(target.elts, value, strict=True):
                    self._bind_target(item, child)
            else:
                self._invalidate(_assignment_target_names(target))
            return
        self._invalidate(_assignment_target_names(target))

    def _scan_branch(self, statements: Sequence[ast.stmt]) -> None:
        child = _FlowAwareStaticSourceScanner(
            self.bindings,
            function_parent_bindings=self.function_parent_bindings,
        )
        child.scan_block(statements)

    def _scan_function_header(
        self,
        statement: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in statement.decorator_list:
            self._scan_expression(decorator)
        for default in statement.args.defaults:
            self._scan_expression(default)
        for default in statement.args.kw_defaults:
            self._scan_expression(default)
        for argument in (
            *statement.args.posonlyargs,
            *statement.args.args,
            *statement.args.kwonlyargs,
        ):
            self._scan_expression(argument.annotation)
        if statement.args.vararg is not None:
            self._scan_expression(statement.args.vararg.annotation)
        if statement.args.kwarg is not None:
            self._scan_expression(statement.args.kwarg.annotation)
        self._scan_expression(statement.returns)

    def scan_statement(self, statement: ast.stmt) -> None:
        self.statement_count += 1
        if self.statement_count > _MAX_FLOW_STATIC_STATEMENTS:
            self.bindings.clear()

        if isinstance(statement, ast.Assign):
            self._scan_expression(statement.value)
            return
        if isinstance(statement, ast.AnnAssign):
            self._scan_expression(statement.annotation)
            self._scan_expression(statement.value)
            return
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._scan_function_header(statement)
            inherited = dict(
                self.function_parent_bindings
                if self.function_parent_bindings is not None
                else self.bindings
            )
            local_names = _scope_assigned_names(statement.body) | _function_parameter_names(
                statement.args
            )
            for local_name in local_names:
                inherited.pop(local_name, None)
            child = _FlowAwareStaticSourceScanner(inherited)
            child.scan_block(statement.body)
            self.bindings.pop(statement.name, None)
            return
        if isinstance(statement, ast.ClassDef):
            for expression in (
                *statement.decorator_list,
                *statement.bases,
                *(keyword.value for keyword in statement.keywords),
            ):
                self._scan_expression(expression)
            outer = dict(self.bindings)
            child = _FlowAwareStaticSourceScanner(
                outer,
                function_parent_bindings=outer,
            )
            child.scan_block(statement.body)
            self.bindings.pop(statement.name, None)
            return
        if isinstance(statement, ast.If):
            self._scan_expression(statement.test)
            self._scan_branch(statement.body)
            self._scan_branch(statement.orelse)
            return
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            self._scan_expression(statement.iter)
            self._scan_branch(statement.body)
            self._scan_branch(statement.orelse)
            return
        if isinstance(statement, ast.While):
            self._scan_expression(statement.test)
            self._scan_branch(statement.body)
            self._scan_branch(statement.orelse)
            return
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            for item in statement.items:
                self._scan_expression(item.context_expr)
            self._scan_branch(statement.body)
            return
        if isinstance(statement, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            self._scan_branch(statement.body)
            for handler in statement.handlers:
                self._scan_expression(handler.type)
                handler_bindings = dict(self.bindings)
                if handler.name is not None:
                    handler_bindings.pop(handler.name, None)
                child = _FlowAwareStaticSourceScanner(handler_bindings)
                child.scan_block(handler.body)
            self._scan_branch(statement.orelse)
            self._scan_branch(statement.finalbody)
            blocks = [*statement.body, *statement.orelse, *statement.finalbody]
            for handler in statement.handlers:
                blocks.extend(handler.body)
            return

        # All remaining statements either do not establish a safe binding or
        # have expression-level assignment semantics that are path-dependent.
        # Scan their expressions, then invalidate every affected name.
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.expr):
                self._scan_expression(child)

    def scan_block(self, statements: Sequence[ast.stmt]) -> None:
        self.bindings = _scope_final_bindings(statements, self.bindings)
        for statement in statements:
            self.scan_statement(statement)


def _validate_python_source_member(name: str, data: bytes) -> None:
    """Permit runtime contracts while rejecting statically encoded data payloads."""
    try:
        source = data.decode("utf-8")
        tree = ast.parse(source, filename=name)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise SubmissionPrivacyError(f"submission Python member is invalid: {name}") from exc
    for node in ast.walk(tree):
        value = _static_source_value(node)
        if value is _UNKNOWN_STATIC_KEY:
            raise SubmissionPrivacyError(
                "submission Python member contains a computed key in an otherwise-static container"
            )
        if value is _UNKNOWN_STATIC_JSON_TEXT:
            raise SubmissionPrivacyError(
                "submission Python member contains an unknown static JSON composition"
            )
        if value is _NOT_STATIC_SOURCE_VALUE:
            continue
        if isinstance(value, (str, bytes)):
            _reject_source_json_literal(name, value)
        if isinstance(value, (dict, list, tuple, set, frozenset)):
            _reject_local_json(value)
    _FlowAwareStaticSourceScanner().scan_block(tree.body)


def _validate_specialist_v2_model_manifest(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _SPECIALIST_V2_MODEL_MANIFEST_FIELDS:
        raise SubmissionPrivacyError(
            "v2 model manifest must be an object with its exact closed fields"
        )
    if value["feature_domain"] != "actor-visible-action-v1":
        raise SubmissionPrivacyError(
            "v2 model manifest must bind actor-visible-action-v1"
        )
    if type(value["c1_schema_version"]) is not int or value["c1_schema_version"] != 2:
        raise SubmissionPrivacyError("v2 model manifest must bind C1 schema version 2")
    feature_hash = value["feature_schema_hash"]
    if not isinstance(feature_hash, str) or not _SHA256.fullmatch(feature_hash):
        raise SubmissionPrivacyError("v2 model manifest must bind its feature schema hash")


def _validate_json_role(role: str, value: object) -> None:
    if role == SPECIALIST_V2_MODEL_MANIFEST_ROLE:
        _validate_specialist_v2_model_manifest(value)
        return
    raise SubmissionPrivacyError("submission member has an unknown required JSON role")


def parse_submission_json_member(
    name: str,
    data: bytes,
    *,
    role: str | None = None,
) -> object:
    """Parse one strict JSON member and enforce submission privacy policy."""
    normalized = _safe_member_path(name)
    if not isinstance(data, bytes):
        raise SubmissionPrivacyError("submission member payload is not bytes")
    _reject_auxiliary_path(normalized)
    if PurePosixPath(normalized).suffix.lower() != ".json":
        raise SubmissionPrivacyError("submission JSON member path is not JSON")
    value = _json_member(normalized, data)
    _reject_local_json(value)
    if role is not None:
        _validate_json_role(role, value)
    return value


def validate_submission_members(
    members: Sequence[tuple[str, bytes]],
    *,
    allowed_members: Collection[str],
    required_json_roles: Mapping[str, str] | None = None,
) -> None:
    """Validate exact allowed member names and local-data privacy boundaries.

    The caller owns profile selection.  This function rejects any member not in
    that explicit allowlist and examines only JSON assets for local-only shapes;
    permitted source code is therefore free to contain contract identifiers.
    """
    allowed = frozenset(_safe_member_path(name) for name in allowed_members)
    roles: dict[str, str] = {}
    for name, role in (required_json_roles or {}).items():
        normalized = _safe_member_path(name)
        if normalized not in allowed:
            raise SubmissionPrivacyError("required JSON role path is not allowlisted")
        if PurePosixPath(normalized).suffix.lower() != ".json":
            raise SubmissionPrivacyError("required JSON role path is not a JSON member")
        if role != SPECIALIST_V2_MODEL_MANIFEST_ROLE:
            raise SubmissionPrivacyError("submission member has an unknown required JSON role")
        roles[normalized] = role
    seen: set[str] = set()
    for name, data in members:
        normalized = _safe_member_path(name)
        if normalized not in allowed:
            raise SubmissionPrivacyError("submission member is not allowlisted")
        if normalized in seen:
            raise SubmissionPrivacyError("submission member is duplicated")
        seen.add(normalized)
        if not isinstance(data, bytes):
            raise SubmissionPrivacyError("submission member payload is not bytes")
        _reject_auxiliary_path(normalized)
        suffix = PurePosixPath(normalized).suffix.lower()
        if suffix == ".py":
            _validate_python_source_member(normalized, data)
            continue
        if suffix != ".json":
            continue
        parse_submission_json_member(
            normalized,
            data,
            role=roles.get(normalized),
        )
    if roles.keys() - seen:
        raise SubmissionPrivacyError("required JSON role member is absent")
