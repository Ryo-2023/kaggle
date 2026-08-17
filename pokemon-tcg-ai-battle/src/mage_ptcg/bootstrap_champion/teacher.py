"""Privacy-preserving teacher examples for Bootstrap distillation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.continuous_league.contracts import atomic_write_json, content_id, require_sha256
from mage_ptcg.observability.cabt_trace import find_forbidden_keys

from .contracts import BootstrapContractError


_HIDDEN_KEY_FRAGMENTS = (
    "opponent_hand",
    "opponent_deck",
    "deck_order",
    "future_random",
    "rng_state",
    "search_begin_input",
)


def outcome_weight(outcome: str) -> float:
    weights = {"win": 1.0, "draw": 0.5, "loss": 0.25}
    try:
        return weights[outcome]
    except KeyError as exc:
        raise BootstrapContractError(f"unknown teacher outcome: {outcome}") from exc


@dataclass(frozen=True, slots=True)
class BootstrapTeacherExample:
    game_id: str
    decision_index: int
    public_state: Mapping[str, Any]
    own_private_state: Mapping[str, Any]
    visible_history: tuple[Mapping[str, Any], ...]
    legal_action_keys: tuple[str, ...]
    selected_action_key: str
    outcome: str
    behavior_weight: float
    teacher_candidate_id: str
    # These are the semantic encodings consumed by the distillation stage.
    # Keeping them with the sealed actor-visible record makes the boundary
    # auditable; raw CABT observations never enter the learner.
    encoded_state: tuple[float, ...] = ()
    encoded_actions: tuple[tuple[float, ...], ...] = ()
    selected_action: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hidden_key_paths(value: Any, *, prefix: str = "") -> list[str]:
    found = list(find_forbidden_keys(value))
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            path = f"{prefix}.{name}" if prefix else name
            normalized = name.lower()
            if any(fragment in normalized for fragment in _HIDDEN_KEY_FRAGMENTS):
                found.append(path)
            found.extend(_hidden_key_paths(child, prefix=path))
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            found.extend(_hidden_key_paths(child, prefix=f"{prefix}[{index}]"))
    return sorted(set(found))


def validate_actor_visible_example(example: BootstrapTeacherExample) -> None:
    if not example.game_id or example.decision_index < 0:
        raise BootstrapContractError("teacher example game_id and decision_index are invalid")
    try:
        require_sha256(example.teacher_candidate_id, "teacher_candidate_id")
    except ValueError as exc:
        raise BootstrapContractError(str(exc)) from exc
    if not example.legal_action_keys or example.selected_action_key not in example.legal_action_keys:
        raise BootstrapContractError("teacher selected action is not legal")
    if len(example.legal_action_keys) != len(set(example.legal_action_keys)):
        raise BootstrapContractError("teacher legal actions must be unique")
    if example.behavior_weight != outcome_weight(example.outcome):
        raise BootstrapContractError("teacher behavior weight differs from outcome contract")
    encoding_present = bool(example.encoded_state) or bool(example.encoded_actions) or example.selected_action is not None
    if encoding_present:
        if not example.encoded_state or not example.encoded_actions or example.selected_action is None:
            raise BootstrapContractError("teacher semantic encoding must be complete")
        if len(example.encoded_actions) != len(example.legal_action_keys):
            raise BootstrapContractError("teacher encoded action count differs from legal actions")
        if not 0 <= example.selected_action < len(example.encoded_actions):
            raise BootstrapContractError("teacher selected encoded action is out of range")
        if example.legal_action_keys[example.selected_action] != example.selected_action_key:
            raise BootstrapContractError("teacher encoded action differs from selected action key")
        state_width = len(example.encoded_state)
        action_widths = {len(action) for action in example.encoded_actions}
        if state_width == 0 or action_widths != {len(example.encoded_actions[0])} or 0 in action_widths:
            raise BootstrapContractError("teacher semantic encoding dimensions are invalid")
    forbidden = _hidden_key_paths(
        {
            "public_state": example.public_state,
            "own_private_state": example.own_private_state,
            "visible_history": example.visible_history,
        }
    )
    if forbidden:
        raise BootstrapContractError(f"teacher example contains forbidden information: {forbidden}")
    try:
        json.dumps(example.to_dict(), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BootstrapContractError("teacher example is not JSON serializable") from exc


def split_games(game_ids: Sequence[str], *, seed: int) -> tuple[set[str], set[str]]:
    """Stable 80/20 game-level split independent of input ordering."""

    values = sorted(set(game_ids))
    if not values:
        return set(), set()
    decorated = sorted(
        (content_id("bootstrap-teacher-split-v1", {"seed": seed, "game_id": game_id}), game_id)
        for game_id in values
    )
    validation_count = max(1, round(len(values) * 0.2)) if len(values) > 1 else 0
    validation = {game_id for _digest, game_id in decorated[:validation_count]}
    return set(values) - validation, validation


@dataclass(frozen=True, slots=True)
class TeacherDatasetManifest:
    teacher_dataset_id: str
    teacher_candidate_id: str
    deck_hash: str
    decision_count: int
    skipped_multi_select_decisions: int
    excluded_game_ids: tuple[str, ...]
    train_game_ids: tuple[str, ...]
    validation_game_ids: tuple[str, ...]
    train_file: str
    validation_file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bootstrap-teacher-dataset-v1",
            **asdict(self),
        }


def _write_jsonl(path: Path, examples: Sequence[BootstrapTeacherExample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = "".join(
        json.dumps(example.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        for example in examples
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def collect_teacher_dataset(
    *,
    examples: Sequence[BootstrapTeacherExample],
    excluded_game_ids: set[str] | None,
    skipped_multi_select_decisions: int,
    deck_hash: str,
    teacher_candidate_id: str,
    seed: int,
    output: Path,
) -> TeacherDatasetManifest:
    """Seal only fault-free single-action decisions into a reusable dataset."""

    try:
        require_sha256(deck_hash, "deck_hash")
        require_sha256(teacher_candidate_id, "teacher_candidate_id")
    except ValueError as exc:
        raise BootstrapContractError(str(exc)) from exc
    if skipped_multi_select_decisions < 0:
        raise BootstrapContractError("skipped_multi_select_decisions must be non-negative")
    excluded = set(excluded_game_ids or ())
    retained = [example for example in examples if example.game_id not in excluded]
    for example in retained:
        validate_actor_visible_example(example)
        if example.teacher_candidate_id != teacher_candidate_id:
            raise BootstrapContractError("teacher example candidate differs from dataset candidate")
    retained.sort(key=lambda item: (item.game_id, item.decision_index))
    game_ids = [example.game_id for example in retained]
    train_games, validation_games = split_games(game_ids, seed=seed)
    train = [example for example in retained if example.game_id in train_games]
    validation = [example for example in retained if example.game_id in validation_games]
    identity = {
        "teacher_candidate_id": teacher_candidate_id,
        "deck_hash": deck_hash,
        "seed": seed,
        "examples": [example.to_dict() for example in retained],
        "excluded_game_ids": sorted(excluded),
        "skipped_multi_select_decisions": skipped_multi_select_decisions,
    }
    dataset_id = content_id("bootstrap-teacher-dataset-v1", identity)
    output = Path(output)
    manifest = TeacherDatasetManifest(
        teacher_dataset_id=dataset_id,
        teacher_candidate_id=teacher_candidate_id,
        deck_hash=deck_hash,
        decision_count=len(retained),
        skipped_multi_select_decisions=skipped_multi_select_decisions,
        excluded_game_ids=tuple(sorted(excluded)),
        train_game_ids=tuple(sorted(train_games)),
        validation_game_ids=tuple(sorted(validation_games)),
        train_file="train.jsonl",
        validation_file="validation.jsonl",
    )
    existing = output / "manifest.json"
    if existing.exists():
        try:
            prior = json.loads(existing.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BootstrapContractError("teacher dataset manifest is corrupt") from exc
        if prior != manifest.to_dict():
            raise BootstrapContractError("teacher dataset output already has different content")
        return manifest
    _write_jsonl(output / manifest.train_file, train)
    _write_jsonl(output / manifest.validation_file, validation)
    atomic_write_json(existing, manifest.to_dict())
    return manifest


def load_teacher_trace(
    root: Path, *, teacher_candidate_id: str | None = None
) -> tuple[list[BootstrapTeacherExample], set[str], int]:
    """Read the per-game trace ledger produced by ``bootstrap-run``.

    Faulted games are returned as exclusions rather than being silently
    dropped, so the sealed manifest retains the reason they were not used.
    """

    games_dir = Path(root) / "games"
    if not games_dir.is_dir():
        raise BootstrapContractError(f"Bootstrap teacher trace is missing games/: {root}")
    examples: list[BootstrapTeacherExample] = []
    excluded: set[str] = set()
    skipped = 0
    seen: set[str] = set()
    if teacher_candidate_id is not None:
        try:
            require_sha256(teacher_candidate_id, "teacher_candidate_id")
        except ValueError as exc:
            raise BootstrapContractError(str(exc)) from exc
    for path in sorted(games_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema_version") != "bootstrap-teacher-trace-game-v1":
            raise BootstrapContractError(f"unsupported Bootstrap teacher trace: {path}")
        game_id = payload.get("game_id")
        if not isinstance(game_id, str) or not game_id or game_id in seen:
            raise BootstrapContractError("Bootstrap teacher trace has duplicate or invalid game ID")
        seen.add(game_id)
        candidate_id = payload.get("candidate_id")
        if not isinstance(candidate_id, str):
            raise BootstrapContractError("Bootstrap teacher trace candidate ID is invalid")
        if teacher_candidate_id is not None and candidate_id != teacher_candidate_id:
            continue
        skipped_value = payload.get("skipped_multi_select_decisions", 0)
        if type(skipped_value) is not int or skipped_value < 0:
            raise BootstrapContractError("Bootstrap teacher trace skipped count is invalid")
        skipped += skipped_value
        if payload.get("status") != "DONE":
            excluded.add(game_id)
            continue
        rows = payload.get("examples")
        if not isinstance(rows, list):
            raise BootstrapContractError("Bootstrap teacher trace examples must be a list")
        for row in rows:
            if not isinstance(row, Mapping):
                raise BootstrapContractError("Bootstrap teacher trace example must be an object")
            try:
                example = BootstrapTeacherExample(
                    game_id=str(row["game_id"]),
                    decision_index=int(row["decision_index"]),
                    public_state=dict(row["public_state"]),
                    own_private_state=dict(row["own_private_state"]),
                    visible_history=tuple(row.get("visible_history", ())),
                    legal_action_keys=tuple(row["legal_action_keys"]),
                    selected_action_key=str(row["selected_action_key"]),
                    outcome=str(row["outcome"]),
                    behavior_weight=float(row["behavior_weight"]),
                    teacher_candidate_id=str(row["teacher_candidate_id"]),
                    encoded_state=tuple(row.get("encoded_state", ())),
                    encoded_actions=tuple(tuple(item) for item in row.get("encoded_actions", ())),
                    selected_action=row.get("selected_action"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise BootstrapContractError(f"invalid Bootstrap teacher trace example: {path}") from exc
            if example.game_id != game_id:
                raise BootstrapContractError("Bootstrap teacher trace example has another game ID")
            examples.append(example)
    if not seen:
        raise BootstrapContractError("Bootstrap teacher trace has no game records")
    return examples, excluded, skipped


def encoded_examples_from_dataset(path: Path) -> list[dict[str, Any]]:
    """Load sealed teacher records as the exact input accepted by distillation."""

    examples: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BootstrapContractError(f"invalid sealed teacher example {path}:{line_number}") from exc
            if not isinstance(row, Mapping):
                raise BootstrapContractError("sealed teacher example must be an object")
            try:
                example = BootstrapTeacherExample(
                    game_id=str(row["game_id"]),
                    decision_index=int(row["decision_index"]),
                    public_state=dict(row["public_state"]),
                    own_private_state=dict(row["own_private_state"]),
                    visible_history=tuple(row.get("visible_history", ())),
                    legal_action_keys=tuple(row["legal_action_keys"]),
                    selected_action_key=str(row["selected_action_key"]),
                    outcome=str(row["outcome"]),
                    behavior_weight=float(row["behavior_weight"]),
                    teacher_candidate_id=str(row["teacher_candidate_id"]),
                    encoded_state=tuple(row.get("encoded_state", ())),
                    encoded_actions=tuple(tuple(item) for item in row.get("encoded_actions", ())),
                    selected_action=row.get("selected_action"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise BootstrapContractError(f"invalid sealed teacher example {path}:{line_number}") from exc
            validate_actor_visible_example(example)
            if not example.encoded_state:
                raise BootstrapContractError(
                    "sealed teacher example has no semantic encoding; collect it with the Bootstrap tracer"
                )
            examples.append(
                {
                    "state": list(example.encoded_state),
                    "actions": [list(action) for action in example.encoded_actions],
                    "legal_mask": [True] * len(example.encoded_actions),
                    "selected_action": example.selected_action,
                    "behavior_weight": example.behavior_weight,
                }
            )
    return examples
