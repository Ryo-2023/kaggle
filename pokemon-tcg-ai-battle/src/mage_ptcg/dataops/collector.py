"""Collect privacy-safe actual-cabt decisions into the ``rule-bc-v1`` contract.

Design boundaries (see docs/evidence/c4-data-ops-v0.md):

* The only trainable row type is the existing :class:`RuleBCExample`.  This
  module never invents a schema and never persists a raw observation.
* Every row is built from :func:`build_rule_bc_example`, which projects the
  observation through the actor-visible :class:`DecisionState` allowlist.  The
  acting player's own hand is legal policy input and is kept only inside the
  Git-ignored private dataset; opponent hidden information is never read.
* The private candidate-to-option binding lives only in the private dataset.
  Public artifacts carry hashes, counts, schema, and privacy results only.
* The teacher is Rule Agent v0 (RULE_ONLY / RULE_IMITATION).  Rule v0 exposes a
  real complete ranking via :func:`rank_rule_indices`; no score is fabricated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re

from agents.rule_agent import choose_rule_indices
from mage_ptcg.competition.redaction import secret_scan
from mage_ptcg.decision_state import DecisionStateError, build_decision_state
from mage_ptcg.distillation.contracts import atomic_write_json, digest
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256, find_forbidden_keys
from mage_ptcg.student.dataset import (
    DATASET_SCHEMA_VERSION,
    DatasetValidationError,
    RuleBCExample,
    build_rule_bc_example,
    load_dataset,
    validate_example,
)
from mage_ptcg.student.features import (
    ACTION_FEATURE_DIM,
    FEATURE_VERSION,
    STATE_FEATURE_DIM,
    state_features_payload,
)
from mage_ptcg.student.artifact import feature_schema


COLLECTOR_SCHEMA_VERSION = "c4-data-ops-collector-v0"
BINDING_SCHEMA_VERSION = "c4-data-ops-binding-v0"
OPTION_INDEX_NAMESPACE = "cabt.select.option.index.v0"
FEATURE_DIMENSION = STATE_FEATURE_DIM + ACTION_FEATURE_DIM

SOURCE_AGENT = "rule"
SOURCE_AGENT_VERSION = "actual-viability-v0"
TEACHER_SOURCE = "Rule Agent v0"
TEACHER_QUALITY = "RULE_ONLY"
TRAINING_OBJECTIVE = "RULE_IMITATION"
EXPECTED_PERFORMANCE_CEILING = "RULE_LEVEL"
TEACHER_TARGET_TYPE = "rule_v0_choice"

_ABSOLUTE_PRIVATE_PATH = re.compile(r"(?:^|[\s\"'])/(?:home|tmp|Users)/")

# Public artifacts must not carry any of these key names.  The mapping mirrors
# the categories used by the actual-agent viability scanner so the two lanes
# report identical privacy semantics.
_PUBLIC_FORBIDDEN_KEYS: dict[str, str] = {
    "id": "raw_card_identity",
    "card_id": "raw_card_identity",
    "cardid": "raw_card_identity",
    "serial": "raw_card_identity",
    "hand": "own_hand_identity",
    "own_hand": "own_hand_identity",
    "hand_card_ids": "own_hand_identity",
    "opponent_hand": "opponent_hidden_information",
    "opponent_deck": "opponent_hidden_information",
    "prize": "opponent_hidden_information",
    "candidate_identity": "candidate_identity",
    "candidates": "private_candidate_binding",
    "private_bindings": "private_candidate_binding",
    "payload": "private_candidate_binding",
    "canonical_payload": "private_candidate_binding",
    "identity_hash": "identity_hash",
    "raw_observation": "raw_observation",
    "observation": "raw_observation",
    "exception": "raw_exception_message",
    "exception_message": "raw_exception_message",
    "terminal_reason": "raw_exception_message",
    "traceback": "raw_exception_message",
}


class DataOpsError(RuntimeError):
    """Raised on a privacy, identity, resumability, or integrity violation."""


class LineageValidationError(DataOpsError):
    """Raised when O2 lineage inputs are missing, inconsistent, or unsafe."""


@dataclass(frozen=True, slots=True)
class ActualEpisodeLineageInput:
    """One O2 match's lineage, merged into the existing private binding at commit time.

    This is an adapter input DTO only; it is never persisted as its own
    schema.  ``collect_actual_dataset`` merges its fields into the existing
    private binding dict and ``RuleBCExample.metadata``.
    """

    match_id: str
    plan_hash: str
    match_spec_hash: str
    backend_kind: str
    requested_seed: int
    engine_seed_supported: bool
    seat_index: int
    player_side: str
    own_agent_id: str
    opponent_agent_id: str
    own_implementation_hash: str
    opponent_implementation_hash: str
    own_deck_hash: str
    opponent_deck_hash: str
    pair_id: str | None = None


_SEAT_TO_SIDE = {0: "A", 1: "B"}


def _validate_episode_lineage_inputs(
    inputs: Sequence[ActualEpisodeLineageInput],
    *,
    games: int,
    own_deck_fingerprint: str,
    opponent_deck_fingerprint: str,
) -> None:
    """Fail closed on any count, identity, seat, or hash inconsistency.

    A single ``collect_actual_dataset`` run represents exactly one own agent
    and one opponent, so every entry must agree on their ids/hashes; only
    ``match_id``/``plan_hash``/``match_spec_hash``/``requested_seed``/
    ``seat_index``/``player_side``/``pair_id`` vary per match.
    """
    if len(inputs) != games:
        raise LineageValidationError("lineage_count_mismatch")
    seen_match_ids: set[str] = set()
    own_agent_ids: set[str] = set()
    opponent_agent_ids: set[str] = set()
    own_impl_hashes: set[str] = set()
    opponent_impl_hashes: set[str] = set()
    own_deck_hashes: set[str] = set()
    opponent_deck_hashes: set[str] = set()
    for entry in inputs:
        if not isinstance(entry.match_id, str) or not entry.match_id:
            raise LineageValidationError("missing_match_id")
        if entry.match_id in seen_match_ids:
            raise LineageValidationError("duplicate_match_id")
        seen_match_ids.add(entry.match_id)
        if entry.backend_kind != "cabt":
            raise LineageValidationError("fixture_backend_rejected")
        if entry.seat_index not in (0, 1) or entry.player_side != _SEAT_TO_SIDE[entry.seat_index]:
            raise LineageValidationError("seat_mismatch")
        own_agent_ids.add(entry.own_agent_id)
        opponent_agent_ids.add(entry.opponent_agent_id)
        own_impl_hashes.add(entry.own_implementation_hash)
        opponent_impl_hashes.add(entry.opponent_implementation_hash)
        own_deck_hashes.add(entry.own_deck_hash)
        opponent_deck_hashes.add(entry.opponent_deck_hash)
    if len(own_agent_ids) > 1:
        raise LineageValidationError("own_agent_mismatch")
    if len(opponent_agent_ids) > 1:
        raise LineageValidationError("opponent_mismatch")
    if len(own_impl_hashes) > 1:
        raise LineageValidationError("own_implementation_hash_mismatch")
    if len(opponent_impl_hashes) > 1:
        raise LineageValidationError("opponent_implementation_hash_mismatch")
    if own_deck_hashes != {own_deck_fingerprint}:
        raise LineageValidationError("own_deck_hash_mismatch")
    if opponent_deck_hashes != {opponent_deck_fingerprint}:
        raise LineageValidationError("opponent_deck_hash_mismatch")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_text(_canonical_json(value))


def _manifest_hash(value: Mapping[str, object]) -> str:
    payload = dict(value)
    payload.pop("manifest_hash", None)
    return _sha256_json(payload)


def _all_finite(value: object) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, Mapping):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_all_finite(item) for item in value)
    return True


# --------------------------------------------------------------------------- #
# Per-decision artifact construction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DecisionArtifacts:
    """One trainable row, its private binding, and the next history digest."""

    example: RuleBCExample
    binding: dict[str, object]
    public_state_digest: str


def build_decision_artifacts(
    observation: object,
    *,
    deck: Sequence[int],
    episode_group_id: str,
    decision_index: int,
    seat: int,
    source_revision: str,
    trace_provenance_hash: str,
    visible_history: tuple[str, ...] = (),
) -> DecisionArtifacts:
    """Build one ``RuleBCExample`` and its private binding from one decision.

    Raises :class:`DatasetValidationError`/:class:`DecisionStateError` for
    non-decision prompts (deck registration), which the caller skips.  Raises
    :class:`DataOpsError` only when the row and binding disagree, or when the
    actor seat does not match the observation.
    """
    state = build_decision_state(observation, visible_history=visible_history)
    if state.actor_view.actor != seat:
        raise DataOpsError("observation actor does not match the collected seat")

    base = build_rule_bc_example(
        observation,
        deck=list(deck),
        source_id=episode_group_id,
        source_revision=source_revision,
        visible_history=visible_history,
    )
    metadata = {
        **base.metadata,
        "episode_group_id": episode_group_id,
        "decision_index": str(decision_index),
        "seat": str(seat),
        "source_agent": SOURCE_AGENT,
        "source_agent_version": SOURCE_AGENT_VERSION,
        "feature_schema_version": FEATURE_VERSION,
        "trace_provenance_hash": trace_provenance_hash,
        "teacher_source": TEACHER_SOURCE,
        "teacher_quality": TEACHER_QUALITY,
    }
    example = replace(base, metadata=metadata)
    validate_example(example)

    # The source agent is Rule Agent v0 with no knowledge adapter, so the
    # chosen candidate is exactly the Rule v0 selection; teacher == source.
    target_indices = choose_rule_indices(observation)
    if target_indices is None:  # pragma: no cover - registration handled upstream
        raise DatasetValidationError("registration observations are not decision samples")
    by_index = {action.option_index: action for action in state.legal_actions}
    if not set(target_indices).issubset(by_index):
        raise DataOpsError("Rule v0 chose a non-legal option index")
    select = observation.get("select") if isinstance(observation, Mapping) else None
    if not isinstance(select, Mapping):  # pragma: no cover - DecisionState already rejects this
        raise DataOpsError("decision observation is missing its selection schema")
    try:
        ordered = is_ordered_selection(select.get("type"), select.get("context"))
    except ValueError as exc:
        raise DataOpsError("decision observation has an unrecognized selection schema") from exc
    # Only CABT SkillOrder is an ordered label.  All other selection schemas
    # are sets, so preserve their deterministic numeric index normalization.
    chosen_option_indices = list(target_indices) if ordered else sorted(target_indices)
    chosen_digests = [by_index[index].action_key.digest for index in chosen_option_indices]
    target_digests = list(example.target_action_digests)
    targets_match = (
        chosen_digests == target_digests
        if ordered
        else set(chosen_digests) == set(target_digests)
    )
    if not targets_match:
        raise DataOpsError("private binding chosen target does not match the row target")

    candidates = [
        {
            "option_index": action.option_index,
            "digest": action.action_key.digest,
            "payload": action.action_key.to_canonical_payload(),
        }
        for action in state.legal_actions
    ]
    binding = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "trace_provenance_hash": trace_provenance_hash,
        "episode_group_id": episode_group_id,
        "decision_index": decision_index,
        "seat": seat,
        "source_agent": SOURCE_AGENT,
        "source_agent_version": SOURCE_AGENT_VERSION,
        "option_index_namespace": OPTION_INDEX_NAMESPACE,
        "legal_candidate_count": len(candidates),
        "candidates": candidates,
        "chosen_option_indices": chosen_option_indices,
        "chosen_action_digests": chosen_digests,
        "teacher_target_type": TEACHER_TARGET_TYPE,
        "teacher_chosen_action_digests": target_digests,
        "teacher_ranking": [list(item) for item in example.teacher_ranking],
        "teacher_source": TEACHER_SOURCE,
        "teacher_quality": TEACHER_QUALITY,
        "feature_schema_version": FEATURE_VERSION,
    }
    return DecisionArtifacts(example=example, binding=binding, public_state_digest=state.actor_view.public_state_digest)


# --------------------------------------------------------------------------- #
# Capturing agent wrapper (one per seat)
# --------------------------------------------------------------------------- #


class DecisionCaptureAgent:
    """Wrap Rule Agent v0 and record each decision as it is actually played.

    The wrapper always returns the delegate's selection, so the game proceeds
    exactly as an uninstrumented Rule v0 game would.  Only actor-visible fields
    reach the sink; non-decision prompts (deck registration) are ignored.
    """

    def __init__(
        self,
        delegate: Callable[[dict], list[int]],
        *,
        deck: Sequence[int],
        episode_group_id: str,
        seat: int,
        source_revision: str,
        trace_provenance_hash: str,
        sink: Callable[[DecisionArtifacts], None],
    ) -> None:
        self._delegate = delegate
        self._deck = list(deck)
        self._episode_group_id = episode_group_id
        self._seat = seat
        self._source_revision = source_revision
        self._trace_provenance_hash = trace_provenance_hash
        self._sink = sink
        self._history: list[str] = []
        self.captured = 0
        self.skipped_non_decision = 0
        self.__name__ = getattr(delegate, "__name__", "rule_capture_agent")

    def __call__(self, observation: dict) -> list[int]:
        selection = self._delegate(observation)
        if not isinstance(observation, dict) or not isinstance(observation.get("select"), Mapping):
            return selection
        try:
            artifacts = build_decision_artifacts(
                observation,
                deck=self._deck,
                episode_group_id=self._episode_group_id,
                decision_index=self.captured,  # provisional; the run assigns the final index
                seat=self._seat,
                source_revision=self._source_revision,
                trace_provenance_hash=self._trace_provenance_hash,
                visible_history=tuple(self._history[-64:]),
            )
        except (DatasetValidationError, DecisionStateError):
            self.skipped_non_decision += 1
            return selection
        self._history.append(artifacts.public_state_digest)
        self.captured += 1
        self._sink(artifacts)
        return selection

    def as_runtime_function(self) -> Callable[[dict], list[int]]:
        def runtime_agent(observation: dict) -> list[int]:
            return self(observation)

        runtime_agent.__name__ = self.__name__
        return runtime_agent


# --------------------------------------------------------------------------- #
# Episode-group split
# --------------------------------------------------------------------------- #


def split_by_episode_group(
    examples: Sequence[RuleBCExample],
    *,
    seed: int,
    validation_percent: int = 20,
    group_key_by_source_id: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Split whole episode groups; never split one episode across partitions.

    Groups are the redacted ``source_id`` (one per episode) by default.  The
    assignment is deterministic in ``seed`` and guarantees a non-empty train
    and validation partition whenever at least two episodes exist.

    ``group_key_by_source_id``, when given, maps a ``source_id`` to a
    *split* group key coarser than the episode itself -- e.g. an O2
    seat-swapped match pair -- so every episode sharing that key lands in the
    same partition.  Per-episode identity (``source_id``) is unaffected;
    only the train/validation assignment is grouped.
    """
    if not 1 <= validation_percent < 100:
        raise DataOpsError("validation_percent must be between 1 and 99")
    order: list[str] = []
    seen: set[str] = set()
    for example in examples:
        if example.source_id not in seen:
            seen.add(example.source_id)
            order.append(example.source_id)
    if len(order) < 2:
        raise DataOpsError("episode-group split requires at least two episodes")

    def group_of(source_id: str) -> str:
        if group_key_by_source_id is None:
            return source_id
        return group_key_by_source_id.get(source_id, source_id)

    groups = sorted({group_of(source_id) for source_id in order})
    ranked_groups = sorted(groups, key=lambda group: _sha256_text(f"{seed}:{group}"))
    n_validation_groups = max(1, round(len(groups) * validation_percent / 100))
    n_validation_groups = min(n_validation_groups, len(groups) - 1) if len(groups) > 1 else 0
    validation_groups = set(ranked_groups[:n_validation_groups])
    validation_ids = {source_id for source_id in order if group_of(source_id) in validation_groups}
    train_ids = {source_id for source_id in order if group_of(source_id) not in validation_groups}
    train = [example for example in examples if example.source_id in train_ids]
    validation = [example for example in examples if example.source_id in validation_ids]
    overlap = sorted(train_ids & validation_ids)
    split_hash = digest(
        {
            "method": "episode_group_hash_v0",
            "seed": seed,
            "train": sorted(train_ids),
            "validation": sorted(validation_ids),
        },
        domain="c4-data-ops-split-v0",
    )
    return {
        "train": train,
        "validation": validation,
        "train_ids": sorted(train_ids),
        "validation_ids": sorted(validation_ids),
        "manifest": {
            "split_method": "episode_group_hash_v0",
            "split_seed": seed,
            "validation_percent": validation_percent,
            "episode_count": len(order),
            "train_episode_count": len(train_ids),
            "validation_episode_count": len(validation_ids),
            "split_overlap_count": len(overlap),
            "split_overlap_groups": overlap,
            "duplicate_episode_count": len(order) - len(seen),
            "split_hash": split_hash,
        },
    }


# --------------------------------------------------------------------------- #
# Privacy scanning
# --------------------------------------------------------------------------- #


def scan_public_artifact(value: object) -> dict[str, object]:
    """Scan a public-intended artifact; return categories only, never a value."""
    categories: dict[str, int] = {}

    def add(category: str) -> None:
        categories[category] = categories.get(category, 0) + 1

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                normalized = str(key).lower().replace("-", "_")
                category = _PUBLIC_FORBIDDEN_KEYS.get(normalized)
                if category is not None:
                    add(category)
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)
        elif isinstance(node, str):
            if _ABSOLUTE_PRIVATE_PATH.search(node):
                add("absolute_path")

    walk(value)
    for _key in find_forbidden_keys(value):
        add("opaque_observation_field")
    for finding in secret_scan(value):
        if finding.startswith("home_path:"):
            add("absolute_path")
        elif finding.startswith("email:"):
            add("email")
        elif finding.startswith("signed_url:"):
            add("signed_url")
        elif finding.startswith(("sensitive_key:", "secret_like_value:")):
            add("secret")
    return {
        "privacy_scan_executed": True,
        "privacy_violations": sum(categories.values()),
        "privacy_violation_categories": dict(sorted(categories.items())),
    }


# --------------------------------------------------------------------------- #
# Compute manifest
# --------------------------------------------------------------------------- #


def compute_manifest() -> dict[str, object]:
    """Return CPU/GPU capability without any host, user, IP, or path value."""
    cpu_count = os.cpu_count() or 0
    cuda_available = False
    gpu_count = 0
    gpu_names: list[str] = []
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            gpu_count = int(torch.cuda.device_count())
            gpu_names = [str(torch.cuda.get_device_name(index)) for index in range(gpu_count)]
    except (ImportError, RuntimeError, OSError):
        cuda_available = False
        gpu_count = 0
        gpu_names = []
    return {
        "cpu_count": cpu_count,
        "cuda_available": cuda_available,
        "gpu_count": gpu_count,
        "gpu_names": gpu_names,
        "recommended_training_device": "cpu",
    }


# --------------------------------------------------------------------------- #
# Run orchestration with per-game resumability
# --------------------------------------------------------------------------- #


def _git_head(repository_root: Path) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository_root, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: Sequence[object]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(_canonical_json(row) + "\n" for row in rows), encoding="utf-8")
    tmp.replace(path)


def _load_jsonl(path: Path) -> list[object]:
    rows: list[object] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def collect_actual_dataset(
    *,
    run_id: str,
    games: int,
    base_seed: int,
    output_root: str | Path,
    canonical_base_sha: str,
    deck_path: str | Path,
    repository_root: str | Path,
    max_steps: int = 10_000,
    validation_percent: int = 20,
    split_seed: int = 0,
    match_runner: Callable[..., Mapping[str, object]] | None = None,
    capability_report: Mapping[str, object] | None = None,
    source_revision: str | None = None,
    episode_lineage_inputs: Sequence[ActualEpisodeLineageInput] | None = None,
    opponent_deck_path: str | Path | None = None,
    opponent_agent_factory: Callable[[Sequence[int], int], Callable[[dict], list[int]]] | None = None,
) -> dict[str, object]:
    """Run ``games`` actual-cabt games and build the dataset.

    Only ``DONE`` games are committed.  Completed games are recorded per game
    and never re-executed.  A resume with a different config is rejected.

    By default this drives Rule v0 self-play, exactly as before
    (``episode_lineage_inputs=None``).  When ``episode_lineage_inputs`` is
    given (O2 lineage mode), one entry per game is required; the own seat
    (``deck_path``/Rule v0, captured as usual) and the opponent seat
    (``opponent_deck_path``/``opponent_agent_factory``, not captured) are
    driven from each entry's ``seat_index``/``requested_seed``/``match_id``,
    and the match_id becomes the episode identity instead of a
    ``run_id``-derived one.
    """
    if not isinstance(run_id, str) or not run_id or "/" in run_id:
        raise DataOpsError("run_id must be a non-empty path-safe string")
    if type(games) is not int or games < 1:
        raise DataOpsError("games must be a positive integer")

    repository_root = Path(repository_root)
    deck_path = Path(deck_path)

    # Lazy imports keep this module importable without the full runtime chain.
    from main import make_rule_agent, read_deck_csv

    if match_runner is None:
        from scripts.test_sim import run_match

        match_runner = run_match

    report = dict(capability_report) if capability_report is not None else None
    if report is None:
        from scripts.cabt_capability import diagnose_cabt_capability

        report = dict(diagnose_cabt_capability())
    if report.get("status") != "READY":
        raise DataOpsError("cabt_capability_unavailable")
    engine_seed_supported = report.get("engine_seed_supported")

    deck = read_deck_csv(deck_path)
    deck_fingerprint = canonical_deck_sha256(deck)
    work_commit = _git_head(repository_root)
    revision = source_revision if source_revision is not None else work_commit

    if episode_lineage_inputs is not None:
        if opponent_deck_path is None or opponent_agent_factory is None:
            raise LineageValidationError("o2_mode_requires_opponent_wiring")
        opponent_deck_path = Path(opponent_deck_path)
        opponent_deck = read_deck_csv(opponent_deck_path)
        opponent_deck_fingerprint = canonical_deck_sha256(opponent_deck)
        _validate_episode_lineage_inputs(
            episode_lineage_inputs,
            games=games,
            own_deck_fingerprint=deck_fingerprint,
            opponent_deck_fingerprint=opponent_deck_fingerprint,
        )

    environment = {
        "environment_loader": "kaggle_environments.make",
        "environment_name": "cabt",
        "actual_execution_allowed": report.get("actual_execution_allowed") is True,
        "engine_seed_supported": engine_seed_supported,
    }
    config = {
        "collector_schema_version": COLLECTOR_SCHEMA_VERSION,
        "run_id": run_id,
        "base_seed": base_seed,
        "max_steps": max_steps,
        "validation_percent": validation_percent,
        "split_seed": split_seed,
        "canonical_base_sha": canonical_base_sha,
        "work_commit_sha": work_commit,
        "deck_fingerprint": deck_fingerprint,
        "environment_fingerprint": digest(environment, domain="c4-data-ops-environment-v0"),
        "source_agent": SOURCE_AGENT,
        "source_agent_version": SOURCE_AGENT_VERSION,
        "teacher_source": TEACHER_SOURCE,
        "teacher_quality": TEACHER_QUALITY,
        "training_objective": TRAINING_OBJECTIVE,
        "expected_performance_ceiling": EXPECTED_PERFORMANCE_CEILING,
        "feature_schema_version": FEATURE_VERSION,
        "feature_dimension": FEATURE_DIMENSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
    }
    config_hash = digest(config, domain="c4-data-ops-config-v0")

    run_dir = Path(output_root) / run_id
    games_dir = run_dir / "private_dataset" / "games"
    private_dir = run_dir / "private_dataset"
    games_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "collection_state.json"

    if state_path.exists():
        prior = _read_json(state_path)
        if not isinstance(prior, Mapping):
            raise DataOpsError("existing_run_has_different_config")
        if prior.get("config_hash") != config_hash:
            prior_config = prior.get("config")
            if not isinstance(prior_config, Mapping):
                raise DataOpsError("existing_run_has_different_config")
            comparable_prior = dict(prior_config)
            comparable_current = dict(config)
            comparable_prior.pop("work_commit_sha", None)
            comparable_current.pop("work_commit_sha", None)
            if comparable_prior != comparable_current:
                raise DataOpsError("existing_run_has_different_config")
            # Finalization-only contract hardening may land while a run is
            # paused.  Preserve the capture revision rather than rerunning
            # completed games solely because the repository HEAD changed.
            config = dict(prior_config)
            config_hash = str(prior["config_hash"])
        completed = {int(index) for index in prior.get("completed_game_indices", [])}
        if completed and games <= max(completed):
            raise DataOpsError("requested game count does not extend the existing run")
    else:
        completed = set()

    executed_this_call: list[int] = []
    for game_index in range(games):
        rows_path = games_dir / f"rows_g{game_index}.jsonl"
        binds_path = games_dir / f"binds_g{game_index}.jsonl"
        if game_index in completed and rows_path.exists() and binds_path.exists():
            continue  # never re-execute a completed game

        lineage_entry = episode_lineage_inputs[game_index] if episode_lineage_inputs is not None else None
        seed = lineage_entry.requested_seed if lineage_entry is not None else base_seed + game_index
        episode_group_id = lineage_entry.match_id if lineage_entry is not None else f"{run_id}-g{game_index}"
        if lineage_entry is not None:
            trace_provenance_hash = digest(
                {"match_spec_hash": lineage_entry.match_spec_hash, "plan_hash": lineage_entry.plan_hash, "environment": "cabt"},
                domain="c4-data-ops-trace-v0",
            )
        else:
            trace_provenance_hash = digest(
                {"config_hash": config_hash, "game_index": game_index, "seed": seed, "environment": "cabt"},
                domain="c4-data-ops-trace-v0",
            )
        collected: list[DecisionArtifacts] = []

        def sink(artifacts: DecisionArtifacts, *, _bucket: list[DecisionArtifacts] = collected) -> None:
            _bucket.append(artifacts)

        def make_factory(seat: int) -> Callable[[Sequence[int], int], Callable[[dict], list[int]]]:
            def factory(active_deck: Sequence[int], agent_seed: int) -> Callable[[dict], list[int]]:
                delegate = make_rule_agent(deck=active_deck, seed=agent_seed)
                wrapper = DecisionCaptureAgent(
                    delegate,
                    deck=active_deck,
                    episode_group_id=episode_group_id,
                    seat=seat,
                    source_revision=revision,
                    trace_provenance_hash=trace_provenance_hash,
                    sink=sink,
                )
                return wrapper.as_runtime_function()

            return factory

        if lineage_entry is not None:
            own_seat = lineage_entry.seat_index
            opp_seat = 1 - own_seat
            deck_by_seat = {own_seat: deck_path, opp_seat: opponent_deck_path}
            agent_name_by_seat = {own_seat: lineage_entry.own_agent_id, opp_seat: lineage_entry.opponent_agent_id}
            factory_by_seat = {own_seat: make_factory(own_seat), opp_seat: opponent_agent_factory}
            raw = match_runner(
                deck_a_path=deck_by_seat[0],
                deck_b_path=deck_by_seat[1],
                agent_a_name=agent_name_by_seat[0],
                agent_b_name=agent_name_by_seat[1],
                seed=seed,
                max_steps=max_steps,
                output_dir=run_dir / ".transient",
                save_html=False,
                save_result=False,
                agent_a_factory=factory_by_seat[0],
                agent_b_factory=factory_by_seat[1],
            )
        else:
            raw = match_runner(
                deck_a_path=deck_path,
                deck_b_path=deck_path,
                agent_a_name="rule",
                agent_b_name="rule",
                seed=seed,
                max_steps=max_steps,
                output_dir=run_dir / ".transient",
                save_html=False,
                save_result=False,
                agent_a_factory=make_factory(0),
                agent_b_factory=make_factory(1),
            )
        status = raw.get("status") if isinstance(raw, Mapping) else None
        if status != "DONE":
            # Do not commit a crashed/incomplete episode; leave it for a resume.
            continue

        # Assign the final per-episode decision index in capture order.  In
        # O2 lineage mode, merge the full lineage into the existing binding
        # dict and metadata here (commit time) rather than threading it
        # through build_decision_artifacts/DecisionCaptureAgent.
        rows: list[dict[str, object]] = []
        binds: list[dict[str, object]] = []
        for decision_index, artifacts in enumerate(collected):
            metadata = {**artifacts.example.metadata, "decision_index": str(decision_index)}
            binding = {**artifacts.binding, "decision_index": decision_index}
            if lineage_entry is not None:
                metadata["o2_match_id"] = lineage_entry.match_id
                metadata["o2_pair_id"] = lineage_entry.pair_id or ""
                binding["o2_lineage"] = {
                    "match_id": lineage_entry.match_id,
                    "plan_hash": lineage_entry.plan_hash,
                    "match_spec_hash": lineage_entry.match_spec_hash,
                    "backend_kind": lineage_entry.backend_kind,
                    "requested_seed": lineage_entry.requested_seed,
                    "engine_seed_supported": lineage_entry.engine_seed_supported,
                    "seat_index": lineage_entry.seat_index,
                    "player_side": lineage_entry.player_side,
                    "own_agent_id": lineage_entry.own_agent_id,
                    "opponent_agent_id": lineage_entry.opponent_agent_id,
                    "own_implementation_hash": lineage_entry.own_implementation_hash,
                    "opponent_implementation_hash": lineage_entry.opponent_implementation_hash,
                    "own_deck_hash": lineage_entry.own_deck_hash,
                    "opponent_deck_hash": lineage_entry.opponent_deck_hash,
                    "pair_id": lineage_entry.pair_id,
                }
            example = replace(artifacts.example, metadata=metadata)
            rows.append(example.to_dict())
            binds.append(binding)
        _write_jsonl(rows_path, rows)
        _write_jsonl(binds_path, binds)
        completed.add(game_index)
        executed_this_call.append(game_index)
        atomic_write_json(
            state_path,
            {
                "config_hash": config_hash,
                "config": config,
                "completed_game_indices": sorted(completed),
                "requested_game_count": games,
                "executed_last_call": executed_this_call,
            },
        )

    return _finalize_run(
        run_dir=run_dir,
        games_dir=games_dir,
        private_dir=private_dir,
        state_path=state_path,
        config=config,
        config_hash=config_hash,
        completed=sorted(completed),
        games=games,
        split_seed=split_seed,
        validation_percent=validation_percent,
        executed_this_call=executed_this_call,
    )


def _finalize_run(
    *,
    run_dir: Path,
    games_dir: Path,
    private_dir: Path,
    state_path: Path,
    config: Mapping[str, object],
    config_hash: str,
    completed: Sequence[int],
    games: int,
    split_seed: int,
    validation_percent: int,
    executed_this_call: Sequence[int],
) -> dict[str, object]:
    dataset_path = private_dir / "rule-bc-v1.jsonl"
    bindings_path = private_dir / "private_bindings.jsonl"

    all_rows: list[object] = []
    all_binds: list[object] = []
    for game_index in completed:
        rows_path = games_dir / f"rows_g{game_index}.jsonl"
        binds_path = games_dir / f"binds_g{game_index}.jsonl"
        if rows_path.exists():
            all_rows.extend(_load_jsonl(rows_path))
        if binds_path.exists():
            all_binds.extend(_load_jsonl(binds_path))
    if len(all_rows) != len(all_binds):
        raise DataOpsError("private rows and bindings disagree before supervised filtering")
    # Optional prompts with no Rule target are valid engine decisions but not
    # Rule-BC supervision.  Exclude both sides of the pair so every persisted
    # training decision has chosen and teacher targets, as the consumer Gate
    # requires.  The raw per-game private files remain resumability evidence.
    captured_decision_count = len(all_rows)
    supervised_pairs = [
        (row, bind)
        for row, bind in zip(all_rows, all_binds, strict=True)
        if isinstance(row, Mapping) and bool(row.get("target_action_digests"))
    ]
    all_rows = [row for row, _bind in supervised_pairs]
    all_binds = [bind for _row, bind in supervised_pairs]
    _write_jsonl(dataset_path, all_rows)
    _write_jsonl(bindings_path, all_binds)

    # Public-safe subset of O2 lineage (match_id/plan_hash only -- both are
    # already public via O2's own match_plan.json/batch_manifest.json).  The
    # rest of ActualEpisodeLineageInput (seat, agent/deck hashes, ...) stays
    # in the private binding only, never in dataset_manifest/public_summary.
    o2_entries = [
        bind["o2_lineage"] for bind in all_binds
        if isinstance(bind, Mapping) and isinstance(bind.get("o2_lineage"), Mapping)
    ]
    o2_lineage_present = bool(o2_entries)
    o2_plan_hashes = sorted({str(entry["plan_hash"]) for entry in o2_entries})
    o2_match_ids = sorted({str(entry["match_id"]) for entry in o2_entries})

    # The consumer hashes parsed records, not JSONL formatting.  Keep the
    # file hash separately so either formatting or semantic tampering fails.
    dataset_hash = _sha256_json(all_rows)
    dataset_file_sha256 = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    bindings_hash = _sha256_text(bindings_path.read_text(encoding="utf-8"))

    # Re-validate every row against the trainer contract before publishing.
    examples = load_dataset(dataset_path) if all_rows else []
    decision_count = len(examples)
    candidate_count = sum(len(example.legal_actions) for example in examples)
    chosen_target_count = len(all_binds)

    episode_ids = sorted({example.source_id for example in examples})
    episode_count = len(episode_ids)

    # O2 seat-swapped match pairs must stay in the same split.  Group by
    # o2_pair_id (private metadata) when present; every other example keeps
    # its default per-episode grouping, so legacy self-play is unaffected.
    group_key_by_source_id: dict[str, str] = {}
    for example in examples:
        pair_id = example.metadata.get("o2_pair_id")
        if pair_id:
            group_key_by_source_id[example.source_id] = f"o2-pair:{pair_id}"

    dataset_status = "COLLECTION_SMOKE"
    split_manifest: dict[str, object]
    split_ok = False
    if episode_count >= 2:
        split = split_by_episode_group(
            examples, seed=split_seed, validation_percent=validation_percent,
            group_key_by_source_id=group_key_by_source_id or None,
        )
        raw_split = dict(split["manifest"])
        assignments = {
            **{source_id: "train" for source_id in split["train_ids"]},
            **{source_id: "validation" for source_id in split["validation_ids"]},
        }
        split_manifest = {
            "schema_version": "c4-actual-episode-split-v1",
            "method": "episode_group_hash_v0",
            "split_seed": split_seed,
            "validation_percent": validation_percent,
            "dataset_hash": dataset_hash,
            "assignments": dict(sorted(assignments.items())),
            "episode_count": episode_count,
            "train_episode_count": raw_split["train_episode_count"],
            "validation_episode_count": raw_split["validation_episode_count"],
            "split_overlap_count": raw_split["split_overlap_count"],
            "split_overlap_groups": raw_split["split_overlap_groups"],
            "duplicate_episode_count": raw_split["duplicate_episode_count"],
        }
        split_manifest["split_hash"] = _sha256_json({"assignments": split_manifest["assignments"], "dataset_hash": dataset_hash})
        split_ok = split_manifest["split_overlap_count"] == 0 and bool(split["train"]) and bool(split["validation"])
    else:
        split_manifest = {
            "schema_version": "c4-actual-episode-split-v1",
            "method": "episode_group_hash_v0",
            "split_seed": split_seed,
            "validation_percent": validation_percent,
            "episode_count": episode_count,
            "train_episode_count": 0,
            "validation_episode_count": 0,
            "split_overlap_count": 0,
            "split_overlap_groups": [],
            "duplicate_episode_count": 0,
            "dataset_hash": dataset_hash,
            "assignments": {},
            "split_hash": "0" * 64,
            "note": "insufficient_episodes_for_split",
        }

    # Duplicate decision detection over the private bindings.
    decision_keys = [
        (bind.get("episode_group_id"), bind.get("decision_index"))
        for bind in all_binds
        if isinstance(bind, Mapping)
    ]
    duplicate_decision_count = len(decision_keys) - len(set(decision_keys))
    split_manifest["duplicate_decision_count"] = duplicate_decision_count

    schema = feature_schema()
    compute = compute_manifest()

    trace_provenance_values = {example.metadata.get("trace_provenance_hash") for example in examples}
    if None in trace_provenance_values or any(not isinstance(value, str) for value in trace_provenance_values):
        raise DataOpsError("dataset row lacks trace provenance")
    trace_provenance_hashes = sorted(trace_provenance_values)
    engineering_gate = {
        "completed_episodes": episode_count,
        "train_episodes": split_manifest["train_episode_count"],
        "validation_episodes": split_manifest["validation_episode_count"],
        "decisions": decision_count,
        "candidate_records": candidate_count,
        "binding_records": len(all_binds),
        "chosen_targets": chosen_target_count,
        "teacher_targets": sum(1 for bind in all_binds if isinstance(bind, Mapping) and bind.get("teacher_target_type") == TEACHER_TARGET_TYPE),
        "split_overlap_count": split_manifest["split_overlap_count"],
        "duplicate_decision_count": duplicate_decision_count,
        "invalid_target_count": 0,
        "non_finite_count": 0,
    }
    performance_eligible = (
        engineering_gate["completed_episodes"] >= 24
        and engineering_gate["train_episodes"] >= 16
        and engineering_gate["validation_episodes"] >= 4
        and engineering_gate["decisions"] >= 800
        and engineering_gate["candidate_records"] >= 3000
        and engineering_gate["binding_records"] == decision_count
        and engineering_gate["chosen_targets"] == decision_count
        and engineering_gate["teacher_targets"] == decision_count
        and duplicate_decision_count == 0
        and split_ok
    )
    if performance_eligible:
        dataset_status = "ACTUAL_TRAINING"
    dataset_manifest: dict[str, object] = {
        "schema_version": "c4-actual-training-bundle-v1",
        "artifact_purpose": dataset_status,
        "dataset_status": dataset_status,
        "performance_eligible": performance_eligible,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "config_hash": config_hash,
        "canonical_base_sha": config["canonical_base_sha"],
        "work_commit_sha": config["work_commit_sha"],
        "deck_fingerprint": config["deck_fingerprint"],
        "environment_fingerprint": config["environment_fingerprint"],
        "dataset_hash": dataset_hash,
        "dataset_file_sha256": dataset_file_sha256,
        "dataset_path": "private_dataset/rule-bc-v1.jsonl",
        "bindings_hash": bindings_hash,
        "private_binding": {"path": "private_dataset/private_bindings.jsonl", "sha256": bindings_hash, "record_count": len(all_binds), "trainer_input": False},
        "episode_group_ids": episode_ids,
        "episode_count": episode_count,
        "decision_count": decision_count,
        "captured_decision_count": captured_decision_count,
        "unsupervised_decision_count": captured_decision_count - decision_count,
        "candidate_count": candidate_count,
        "chosen_target_count": chosen_target_count,
        "chosen_target_decision_count": chosen_target_count,
        "teacher_source": TEACHER_SOURCE,
        "teacher_quality": TEACHER_QUALITY,
        "training_objective": TRAINING_OBJECTIVE,
        "expected_performance_ceiling": EXPECTED_PERFORMANCE_CEILING,
        "source_agent": SOURCE_AGENT,
        "source_agent_version": SOURCE_AGENT_VERSION,
        "trace_provenance_hashes": trace_provenance_hashes,
        "o2_lineage_present": o2_lineage_present,
        "o2_plan_hashes": o2_plan_hashes,
        "o2_match_ids": o2_match_ids,
        **schema,
        "engineering_gate": engineering_gate,
    }

    # Privacy scan over public-intended artifacts only.
    scan = scan_public_artifact({"dataset_manifest": dataset_manifest, "split_manifest": split_manifest, "compute": compute})
    privacy_ok = scan["privacy_scan_executed"] is True and scan["privacy_violations"] == 0

    completeness_ok = (
        episode_count >= 2
        and decision_count > 0
        and candidate_count > 0
        and chosen_target_count == decision_count
        and duplicate_decision_count == 0
    )
    performance_eligible = performance_eligible and privacy_ok
    dataset_manifest["performance_eligible"] = performance_eligible
    if not performance_eligible:
        dataset_manifest["artifact_purpose"] = "COLLECTION_SMOKE"
        dataset_manifest["dataset_status"] = "COLLECTION_SMOKE"
    split_manifest["manifest_hash"] = _manifest_hash(split_manifest)
    dataset_manifest["privacy_scan_executed"] = scan["privacy_scan_executed"]
    dataset_manifest["privacy_violations"] = scan["privacy_violations"]
    dataset_manifest["manifest_hash"] = _manifest_hash(dataset_manifest)
    status = "PASS" if (privacy_ok and completeness_ok and split_ok) else "COLLECTION_INCOMPLETE"

    public_summary = {
        "schema_version": "c4-data-ops-public-summary-v0",
        "status": status,
        "dataset_status": dataset_manifest["dataset_status"],
        "artifact_purpose": dataset_manifest["artifact_purpose"],
        "performance_eligible": performance_eligible,
        "run_id": config["run_id"],
        "config_hash": config_hash,
        "games_requested": games,
        "games_committed": len(completed),
        "episode_count": episode_count,
        "decision_count": decision_count,
        "captured_decision_count": captured_decision_count,
        "unsupervised_decision_count": captured_decision_count - decision_count,
        "candidate_count": candidate_count,
        "chosen_target_count": chosen_target_count,
        "private_binding_count": chosen_target_count,
        "duplicate_decision_count": duplicate_decision_count,
        "dataset_hash": dataset_hash,
        "bindings_hash": bindings_hash,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_manifest_hash": dataset_manifest["manifest_hash"],
        "split_manifest_hash": split_manifest["manifest_hash"],
        **schema,
        "teacher_source": TEACHER_SOURCE,
        "teacher_quality": TEACHER_QUALITY,
        "training_objective": TRAINING_OBJECTIVE,
        "expected_performance_ceiling": EXPECTED_PERFORMANCE_CEILING,
        "split": {
            "split_method": split_manifest["method"],
            "train_episode_count": split_manifest["train_episode_count"],
            "validation_episode_count": split_manifest["validation_episode_count"],
            "split_overlap_count": split_manifest["split_overlap_count"],
            "duplicate_decision_count": duplicate_decision_count,
            "split_hash": split_manifest["split_hash"],
        },
        "o2_lineage_present": o2_lineage_present,
        "o2_plan_hashes": o2_plan_hashes,
        "o2_match_ids": o2_match_ids,
        "compute": compute,
        "privacy_scan_executed": scan["privacy_scan_executed"],
        "privacy_violations": scan["privacy_violations"],
        "privacy_violation_categories": scan["privacy_violation_categories"],
        "public_private_separation": True,
    }

    atomic_write_json(run_dir / "dataset_manifest.json", dataset_manifest)
    atomic_write_json(run_dir / "split_manifest.json", split_manifest)
    atomic_write_json(run_dir / "public_summary.json", public_summary)
    atomic_write_json(
        state_path,
        {
            "config_hash": config_hash,
            "config": dict(config),
                "completed_game_indices": list(completed),
            "requested_game_count": games,
            "executed_last_call": list(executed_this_call),
            "dataset_hash": dataset_hash,
            "split_hash": split_manifest["split_hash"],
        },
    )
    return public_summary


# --------------------------------------------------------------------------- #
# Standalone dataset validation
# --------------------------------------------------------------------------- #


def validate_run(run_dir: str | Path) -> dict[str, object]:
    """Validate a collected run's private dataset, bindings, and public split.

    Returns a report with ``valid`` and per-check counts.  Raises
    :class:`DataOpsError` with a specific reason on the first hard failure.
    """
    run_dir = Path(run_dir)
    dataset_path = run_dir / "private_dataset" / "rule-bc-v1.jsonl"
    bindings_path = run_dir / "private_dataset" / "private_bindings.jsonl"
    if not dataset_path.exists() or not bindings_path.exists():
        raise DataOpsError("run is missing its private dataset or bindings")

    examples = load_dataset(dataset_path)  # re-validates the RuleBCExample contract
    binds = [row for row in _load_jsonl(bindings_path)]
    if len(examples) != len(binds):
        raise DataOpsError("row count and binding count disagree")

    decision_keys: list[tuple[object, object]] = []
    for example, bind in zip(examples, binds):
        if not isinstance(bind, Mapping):
            raise DataOpsError("binding record must be an object")
        group = bind.get("episode_group_id")
        index = bind.get("decision_index")
        if not isinstance(group, str) or not group:
            raise DataOpsError("binding is missing its episode group")
        if type(index) is not int:
            raise DataOpsError("binding is missing its decision index")
        candidates = bind.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise DataOpsError("binding must record at least one candidate")
        candidate_digests: dict[int, str] = {}
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise DataOpsError("binding candidate must be an object")
            option_index = candidate.get("option_index")
            candidate_digest = candidate.get("digest")
            if (
                type(option_index) is not int
                or option_index < 0
                or not isinstance(candidate_digest, str)
                or option_index in candidate_digests
            ):
                raise DataOpsError("binding has an invalid or duplicate candidate option index")
            candidate_digests[option_index] = candidate_digest
        chosen = bind.get("chosen_option_indices")
        if not isinstance(chosen, list):
            raise DataOpsError("binding must record chosen option indices")
        if any(type(index) is not int or index not in candidate_digests for index in chosen):
            raise DataOpsError("chosen option index is out of range")
        if len(chosen) != len(set(chosen)):
            raise DataOpsError("chosen option indices must be unique")
        if example.metadata.get("episode_group_id") != group:
            raise DataOpsError("row and binding episode group disagree")
        chosen_digests = bind.get("chosen_action_digests")
        teacher_digests = bind.get("teacher_chosen_action_digests")
        if (
            not isinstance(chosen_digests, list)
            or not isinstance(teacher_digests, list)
            or any(not isinstance(value, str) for value in [*chosen_digests, *teacher_digests])
        ):
            raise DataOpsError("binding must record chosen and teacher action digests")
        try:
            ordered = is_ordered_selection(
                example.selection_type, example.selection_context
            )
        except ValueError as exc:  # pragma: no cover - RuleBCExample validates this
            raise DataOpsError("row selection schema is not recognized") from exc
        expected_digests = list(example.target_action_digests)
        indexed_digests = [candidate_digests[index] for index in chosen]
        if not (
            len(chosen)
            == len(chosen_digests)
            == len(teacher_digests)
            == len(expected_digests)
        ):
            raise DataOpsError("row target and binding chosen digest disagree")
        if ordered:
            digests_match = (
                indexed_digests == chosen_digests == teacher_digests == expected_digests
            )
        else:
            digests_match = (
                set(indexed_digests)
                == set(chosen_digests)
                == set(teacher_digests)
                == set(expected_digests)
            )
        if not digests_match:
            raise DataOpsError("row target and binding chosen digest disagree")
        # Feature dimension and finiteness on the real encoder.
        vector = state_features_payload(example.public_state, example.own_private_state, example.visible_history)
        if len(vector) != STATE_FEATURE_DIM or not _all_finite(vector):
            raise DataOpsError("feature dimension inconsistent or non-finite")
        if not _all_finite(example.to_dict()):
            raise DataOpsError("dataset row contains a non-finite value")
        decision_keys.append((group, index))

    duplicate_decision_count = len(decision_keys) - len(set(decision_keys))
    if duplicate_decision_count:
        raise DataOpsError("duplicate (episode_group_id, decision_index) detected")

    # Public summary must be scanned and clean.
    summary_path = run_dir / "public_summary.json"
    scan = {"privacy_scan_executed": False, "privacy_violations": None}
    if summary_path.exists():
        summary = _read_json(summary_path)
        scan = scan_public_artifact(summary)
        if scan["privacy_violations"]:
            raise DataOpsError("public summary carries a privacy violation")

    return {
        "valid": True,
        "row_count": len(examples),
        "binding_count": len(binds),
        "episode_count": len({example.source_id for example in examples}),
        "candidate_count": sum(len(example.legal_actions) for example in examples),
        "duplicate_decision_count": duplicate_decision_count,
        "feature_dimension": FEATURE_DIMENSION,
        "privacy_scan_executed": scan["privacy_scan_executed"],
        "privacy_violations": scan["privacy_violations"],
    }
