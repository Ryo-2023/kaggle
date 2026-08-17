"""Read-only audit of native opponent tuning surfaces.

The auditor parses source and deck bytes but never imports or edits a native
``main.py``.  Its output is a candidate-design input, not permission to mutate
or submit the upstream asset.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


class NativeTuningSurfaceError(ValueError):
    """Raised when a native pair cannot be audited safely."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise NativeTuningSurfaceError(f"{name} must be non-empty")
    return value


@dataclass(frozen=True, slots=True)
class NativeParameterV1:
    name: str
    kind: str
    default: Any
    line: int
    tunable_reason: str

    def __post_init__(self) -> None:
        _text(self.name, "parameter.name")
        _text(self.kind, "parameter.kind")
        if type(self.line) is not int or self.line <= 0:
            raise NativeTuningSurfaceError("parameter.line must be positive")
        _text(self.tunable_reason, "parameter.tunable_reason")


@dataclass(frozen=True, slots=True)
class NativeTuningSurfaceV1:
    asset_id: str
    main_path: str
    deck_path: str
    policy_sha256: str
    deck_sha256: str
    deck_card_count: int
    has_agent_entrypoint: bool
    has_native_fallback: bool
    native_fallback_reason: str
    has_search: bool
    has_score_functions: bool
    has_override_hook: bool
    parameters: tuple[NativeParameterV1, ...]
    classifications: tuple[str, ...]
    research_only: bool

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        _text(self.main_path, "main_path")
        _text(self.deck_path, "deck_path")
        for name, value in (("policy_sha256", self.policy_sha256), ("deck_sha256", self.deck_sha256)):
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise NativeTuningSurfaceError(f"{name} must be lowercase SHA-256")
        if type(self.deck_card_count) is not int or self.deck_card_count != 60:
            raise NativeTuningSurfaceError("deck_card_count must be exactly 60")
        for name in (
            "has_agent_entrypoint",
            "has_native_fallback",
            "has_search",
            "has_score_functions",
            "has_override_hook",
            "research_only",
        ):
            if type(getattr(self, name)) is not bool:
                raise NativeTuningSurfaceError(f"{name} must be bool")
        if not self.research_only:
            raise NativeTuningSurfaceError("native tuning surface is research-only")


_TUNABLE_TOKENS = (
    "PRIOR",
    "THRESH",
    "WEIGHT",
    "SCORE",
    "BUDGET",
    "CAND",
    "MAXD",
    "MARGIN",
    "NODE",
    "DEPTH",
    "SEARCH",
    "ENABLE_SEARCH",
    "USE_SEARCH",
)


def _is_tunable_name(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in _TUNABLE_TOKENS)


def _literal_or_repr(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        if isinstance(node, ast.Call) and node.args:
            try:
                return ast.literal_eval(node.args[-1])
            except (ValueError, TypeError, SyntaxError):
                return ast.unparse(node)
        return ast.unparse(node)


def _iter_module_assignments(tree: ast.Module):
    """Yield only module-scope assignments, including top-level try/if blocks."""

    def walk(statements):
        for node in statements:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        yield target.id, node.value, node.lineno
                continue
            if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
                yield from walk(node.body)
                yield from walk(node.orelse)
                continue
            if isinstance(node, ast.Try):
                yield from walk(node.body)
                for handler in node.handlers:
                    yield from walk(handler.body)
                yield from walk(node.orelse)
                yield from walk(node.finalbody)

    yield from walk(tree.body)


def _has_exception_fallback(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "agent":
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Try):
                continue
            for handler in child.handlers:
                if handler.type is None:
                    return True
                if isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}:
                    return True
    return False


def _read_deck(path: Path) -> int:
    try:
        tokens = path.read_text(encoding="utf-8").split()
    except OSError as exc:
        raise NativeTuningSurfaceError(f"cannot read deck.csv: {path}") from exc
    try:
        [int(token) for token in tokens]
    except ValueError as exc:
        raise NativeTuningSurfaceError(f"deck.csv contains non-integer card id: {path}") from exc
    if len(tokens) != 60:
        raise NativeTuningSurfaceError(f"deck.csv must contain exactly 60 cards, got {len(tokens)}")
    return len(tokens)


def audit_native_pair_v1(asset_id: str, main_path: Path | str, deck_path: Path | str) -> NativeTuningSurfaceV1:
    _text(asset_id, "asset_id")
    main = Path(main_path)
    deck = Path(deck_path)
    if not main.is_file():
        raise NativeTuningSurfaceError(f"main.py is missing: {main}")
    if not deck.is_file():
        raise NativeTuningSurfaceError(f"deck.csv is missing: {deck}")
    card_count = _read_deck(deck)
    source = main.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(main))
    except SyntaxError as exc:
        raise NativeTuningSurfaceError(f"syntax error in native main.py: {exc}") from exc
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    has_agent = "agent" in function_names
    if not has_agent:
        raise NativeTuningSurfaceError("native main.py must expose agent(obs_dict)")
    has_score = any(name.startswith("score_") for name in function_names)
    has_override = "apply_overrides" in function_names
    has_search = any(token in source for token in ("search_begin", "search_step", "ENABLE_SEARCH"))
    has_fallback = _has_exception_fallback(tree)
    parameters: list[NativeParameterV1] = []
    for name, value, line in _iter_module_assignments(tree):
        if not _is_tunable_name(name):
            continue
        if name.startswith("__"):
            continue
        if not name.lstrip("_").isupper():
            continue
        if name in {"_SEARCH_OK", "USE_SEARCH"}:
            continue
        kind = "mapping" if isinstance(value, (ast.Dict, ast.Set, ast.List, ast.Tuple)) else "scalar"
        reason = "native scoring/threshold/search configuration; research copy only"
        parameters.append(
            NativeParameterV1(
                name=name,
                kind=kind,
                default=_literal_or_repr(value),
                line=int(line),
                tunable_reason=reason,
            )
        )
    unique_parameters = {(param.name, param.line): param for param in parameters}
    parameters = sorted(unique_parameters.values(), key=lambda param: (param.line, param.name))
    classifications: list[str] = []
    if has_score and parameters:
        classifications.extend(("DIRECT_PARAMETER_TUNABLE", "RULE_EDIT_TUNABLE"))
    elif has_score:
        classifications.append("RULE_EDIT_TUNABLE")
    if has_search:
        classifications.append("SEARCH_ROLLOUT_READY")
    if has_fallback:
        classifications.append("NATIVE_FALLBACK_READY")
    else:
        classifications.append("FALLBACK_UNVERIFIED")
    if not parameters:
        classifications.append("DISTILLATION_ONLY")
    return NativeTuningSurfaceV1(
        asset_id=asset_id,
        main_path=str(main.resolve()),
        deck_path=str(deck.resolve()),
        policy_sha256=_sha256(main),
        deck_sha256=_sha256(deck),
        deck_card_count=card_count,
        has_agent_entrypoint=has_agent,
        has_native_fallback=has_fallback,
        native_fallback_reason=(
            "agent catches Exception and returns a bounded action fallback"
            if has_fallback
            else "agent fallback could not be proven by AST"
        ),
        has_search=has_search,
        has_score_functions=has_score,
        has_override_hook=has_override,
        parameters=tuple(parameters),
        classifications=tuple(dict.fromkeys(classifications)),
        research_only=True,
    )


def surface_to_dict_v1(surface: NativeTuningSurfaceV1) -> dict[str, Any]:
    if type(surface) is not NativeTuningSurfaceV1:
        raise NativeTuningSurfaceError("surface must be exact NativeTuningSurfaceV1")
    result = asdict(surface)
    result["parameters"] = [asdict(param) for param in surface.parameters]
    result["classifications"] = list(surface.classifications)
    return result


def dump_surface_json_v1(surface: NativeTuningSurfaceV1, path: Path | str) -> str:
    payload = json.dumps(surface_to_dict_v1(surface), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
