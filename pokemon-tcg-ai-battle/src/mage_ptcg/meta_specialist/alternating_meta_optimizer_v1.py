"""Research-only alternating deck/policy optimization state machine.

The optimizer described here is an orchestration contract, not a CABT or
training runner.  It binds a candidate's deck/config identities to one exact
meta manifest and schedule, keeps the native BestKnown arm in every state, and
provides deterministic 96 -> 384 -> 768 -> 1536 successive-halving
transitions.  Files are written atomically and are re-hashed on resume.  All
execution, training, promotion, and submission authorities are permanently
false; callers must not mistake a checkpoint journal for a permission grant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    DeckMutationAuthorityV1,
    DeckMutationCandidateV1,
)
from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionError,
    load_meta_distribution_manifest_v1,
)


ALTERNATING_META_OPTIMIZER_SCHEMA_V1 = "meta-specialist-alternating-meta-optimizer-v1"
CHECKPOINT_SCHEMA_V1 = "meta-specialist-alternating-meta-checkpoint-v1"
SUCCESSIVE_HALVING_GAMES_V1 = (96, 384, 768, 1536)
POLICY_FIXED_SHORT_V1 = "POLICY_FIXED_SHORT"
DECK_FIXED_LONG_V1 = "DECK_FIXED_LONG"
PHASES_V1 = frozenset({POLICY_FIXED_SHORT_V1, DECK_FIXED_LONG_V1})
_STATUSES_V1 = frozenset({"DRY_RUN", "CHECKPOINTED", "RESUMED", "ROLLED_BACK"})
_SHA_CHARS = frozenset("0123456789abcdef")


class AlternatingMetaOptimizerError(ValueError):
    """Raised when a candidate state or checkpoint violates its contract."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise AlternatingMetaOptimizerError(f"{name} must be a non-empty string")
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise AlternatingMetaOptimizerError(f"{name} must be a lowercase SHA-256 string")
    return value


def _finite_score(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise AlternatingMetaOptimizerError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise AlternatingMetaOptimizerError(f"{name} must be finite in [0,1]")
    return result


def _sha256_file(path: Path | str) -> str:
    source = Path(path)
    try:
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AlternatingMetaOptimizerError(f"cannot hash source file: {source}") from exc
    return digest.hexdigest()


def _require_file(path: Path | str, name: str) -> Path:
    candidate = Path(path).resolve()
    if not candidate.is_file():
        raise AlternatingMetaOptimizerError(f"{name} must be an existing regular file: {candidate}")
    return candidate


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AlternatingMetaOptimizerError(f"value is not canonically serializable: {exc}") from exc


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AlternatingMetaOptimizerError(f"cannot read JSON state: {path}") from exc
    if not isinstance(value, dict):
        raise AlternatingMetaOptimizerError(f"JSON state must be an object: {path}")
    return value


def _state_sha256(state: "CandidateStateV1") -> str:
    return hashlib.sha256(
        b"mage-ptcg:alternating-candidate-state:v1\0" + _canonical_bytes(state.to_dict())
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchAuthorityV1:
    """Explicitly denied authorities for a research candidate lineage."""

    execute_allowed: bool = False
    training_allowed: bool = False
    promotion_allowed: bool = False
    submission_allowed: bool = False
    longrun_allowed: bool = False

    def __post_init__(self) -> None:
        values = (
            self.execute_allowed,
            self.training_allowed,
            self.promotion_allowed,
            self.submission_allowed,
            self.longrun_allowed,
        )
        if any(type(value) is not bool for value in values):
            raise AlternatingMetaOptimizerError("authority flags must be bool")
        if any(values):
            raise AlternatingMetaOptimizerError(
                "alternating optimizer is research-only; execute/training/promotion/"
                "submission/longrun authority must remain false"
            )

    def to_dict(self) -> dict[str, bool]:
        return {
            "execute_allowed": self.execute_allowed,
            "training_allowed": self.training_allowed,
            "promotion_allowed": self.promotion_allowed,
            "submission_allowed": self.submission_allowed,
            "longrun_allowed": self.longrun_allowed,
        }


@dataclass(frozen=True, slots=True)
class NativeBaselineArmV1:
    """Required native BestKnown identity carried by every candidate state."""

    pair_id: str
    deck_sha256: str
    policy_sha256: str
    evaluator_sha256: str
    status: str = "PROVEN"

    def __post_init__(self) -> None:
        _text(self.pair_id, "native_baseline.pair_id")
        _sha(self.deck_sha256, "native_baseline.deck_sha256")
        _sha(self.policy_sha256, "native_baseline.policy_sha256")
        _sha(self.evaluator_sha256, "native_baseline.evaluator_sha256")
        if self.status not in {"PROVEN", "UNPROVEN"}:
            raise AlternatingMetaOptimizerError("native_baseline.status must be PROVEN or UNPROVEN")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CandidateStateV1:
    """One candidate at one fixed-timescale and evaluation stage."""

    schema_version: str
    candidate_id: str
    parent_candidate_id: str | None
    phase: str
    deck_sha256: str
    policy_config_sha256: str
    meta_manifest_sha256: str
    meta_schedule_sha256: str
    stage_games: int
    native_baseline: NativeBaselineArmV1
    authority: ResearchAuthorityV1
    revision: int = 0
    candidate_score: float | None = None
    native_score: float | None = None
    fault_count: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != ALTERNATING_META_OPTIMIZER_SCHEMA_V1:
            raise AlternatingMetaOptimizerError("wrong alternating optimizer schema")
        _text(self.candidate_id, "candidate_id")
        if self.parent_candidate_id is not None:
            _text(self.parent_candidate_id, "parent_candidate_id")
        if self.phase not in PHASES_V1:
            raise AlternatingMetaOptimizerError(f"unsupported candidate phase: {self.phase}")
        _sha(self.deck_sha256, "deck_sha256")
        _sha(self.policy_config_sha256, "policy_config_sha256")
        _sha(self.meta_manifest_sha256, "meta_manifest_sha256")
        _sha(self.meta_schedule_sha256, "meta_schedule_sha256")
        if self.stage_games not in SUCCESSIVE_HALVING_GAMES_V1:
            raise AlternatingMetaOptimizerError(
                f"stage_games must be one of {SUCCESSIVE_HALVING_GAMES_V1}"
            )
        if type(self.native_baseline) is not NativeBaselineArmV1:
            raise AlternatingMetaOptimizerError("native_baseline is mandatory")
        if type(self.authority) is not ResearchAuthorityV1:
            raise AlternatingMetaOptimizerError("authority must be ResearchAuthorityV1")
        if type(self.revision) is not int or isinstance(self.revision, bool) or self.revision < 0:
            raise AlternatingMetaOptimizerError("revision must be a nonnegative int")
        if self.candidate_score is not None:
            _finite_score(self.candidate_score, "candidate_score")
        if self.native_score is not None:
            _finite_score(self.native_score, "native_score")
        if type(self.fault_count) is not int or isinstance(self.fault_count, bool) or self.fault_count < 0:
            raise AlternatingMetaOptimizerError("fault_count must be a nonnegative int")

    @property
    def policy_fixed(self) -> bool:
        return self.phase == POLICY_FIXED_SHORT_V1

    @property
    def deck_fixed(self) -> bool:
        return self.phase == DECK_FIXED_LONG_V1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "parent_candidate_id": self.parent_candidate_id,
            "phase": self.phase,
            "policy_fixed": self.policy_fixed,
            "deck_fixed": self.deck_fixed,
            "deck_sha256": self.deck_sha256,
            "policy_config_sha256": self.policy_config_sha256,
            "meta_manifest_sha256": self.meta_manifest_sha256,
            "meta_schedule_sha256": self.meta_schedule_sha256,
            "stage_games": self.stage_games,
            "native_baseline": self.native_baseline.to_dict(),
            "authority": self.authority.to_dict(),
            "revision": self.revision,
            "candidate_score": self.candidate_score,
            "native_score": self.native_score,
            "fault_count": self.fault_count,
            "research_only": True,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateStateV1":
        if not isinstance(payload, Mapping):
            raise AlternatingMetaOptimizerError("candidate state must be an object")
        baseline_raw = payload.get("native_baseline")
        authority_raw = payload.get("authority")
        if not isinstance(baseline_raw, Mapping) or not isinstance(authority_raw, Mapping):
            raise AlternatingMetaOptimizerError("candidate state baseline/authority is malformed")
        return cls(
            schema_version=str(payload.get("schema_version", "")),
            candidate_id=str(payload.get("candidate_id", "")),
            parent_candidate_id=payload.get("parent_candidate_id"),
            phase=str(payload.get("phase", "")),
            deck_sha256=str(payload.get("deck_sha256", "")),
            policy_config_sha256=str(payload.get("policy_config_sha256", "")),
            meta_manifest_sha256=str(payload.get("meta_manifest_sha256", "")),
            meta_schedule_sha256=str(payload.get("meta_schedule_sha256", "")),
            stage_games=payload.get("stage_games"),
            native_baseline=NativeBaselineArmV1(
                pair_id=str(baseline_raw.get("pair_id", "")),
                deck_sha256=str(baseline_raw.get("deck_sha256", "")),
                policy_sha256=str(baseline_raw.get("policy_sha256", "")),
                evaluator_sha256=str(baseline_raw.get("evaluator_sha256", "")),
                status=str(baseline_raw.get("status", "")),
            ),
            authority=ResearchAuthorityV1(
                execute_allowed=authority_raw.get("execute_allowed", False),
                training_allowed=authority_raw.get("training_allowed", False),
                promotion_allowed=authority_raw.get("promotion_allowed", False),
                submission_allowed=authority_raw.get("submission_allowed", False),
                longrun_allowed=authority_raw.get("longrun_allowed", False),
            ),
            revision=payload.get("revision", 0),
            candidate_score=payload.get("candidate_score"),
            native_score=payload.get("native_score"),
            fault_count=payload.get("fault_count", 0),
        )


def advance_candidate_state_v1(
    state: CandidateStateV1,
    *,
    phase: str | None = None,
    deck_sha256: str | None = None,
    policy_config_sha256: str | None = None,
    next_stage_games: int | None = None,
    candidate_score: float | None = None,
    native_score: float | None = None,
    fault_count: int | None = None,
) -> CandidateStateV1:
    """Advance one candidate without changing the dimension declared fixed.

    ``POLICY_FIXED_SHORT`` allows a deck mutation while holding the policy
    config SHA; ``DECK_FIXED_LONG`` allows a policy update while holding the
    deck SHA.  Evaluation stages can only advance to the next exact
    successive-halving budget and require both candidate and native scores.
    """

    if type(state) is not CandidateStateV1:
        raise AlternatingMetaOptimizerError("state must be a CandidateStateV1")
    target_phase = state.phase if phase is None else phase
    if target_phase not in PHASES_V1:
        raise AlternatingMetaOptimizerError(f"unsupported candidate phase: {target_phase}")
    target_deck = state.deck_sha256 if deck_sha256 is None else _sha(deck_sha256, "deck_sha256")
    target_policy = (
        state.policy_config_sha256
        if policy_config_sha256 is None
        else _sha(policy_config_sha256, "policy_config_sha256")
    )
    if target_phase == POLICY_FIXED_SHORT_V1 and target_policy != state.policy_config_sha256:
        raise AlternatingMetaOptimizerError("policy-fixed short phase cannot change policy config SHA")
    if target_phase == DECK_FIXED_LONG_V1 and target_deck != state.deck_sha256:
        raise AlternatingMetaOptimizerError("deck-fixed long phase cannot change deck SHA")
    target_stage = state.stage_games if next_stage_games is None else next_stage_games
    target_candidate_score = state.candidate_score if candidate_score is None else _finite_score(candidate_score, "candidate_score")
    target_native_score = state.native_score if native_score is None else _finite_score(native_score, "native_score")
    if next_stage_games is not None:
        try:
            expected_next = SUCCESSIVE_HALVING_GAMES_V1[
                SUCCESSIVE_HALVING_GAMES_V1.index(state.stage_games) + 1
            ]
        except (ValueError, IndexError) as exc:
            raise AlternatingMetaOptimizerError("no next successive-halving stage exists") from exc
        if target_stage != expected_next:
            raise AlternatingMetaOptimizerError(
                f"next successive-halving stage must be {expected_next}, got {target_stage}"
            )
        if target_candidate_score is None or target_native_score is None:
            raise AlternatingMetaOptimizerError("stage advancement requires candidate and native scores")
    target_faults = state.fault_count if fault_count is None else fault_count
    if type(target_faults) is not int or isinstance(target_faults, bool) or target_faults < 0:
        raise AlternatingMetaOptimizerError("fault_count must be a nonnegative int")
    return CandidateStateV1(
        schema_version=state.schema_version,
        candidate_id=state.candidate_id,
        parent_candidate_id=state.parent_candidate_id,
        phase=target_phase,
        deck_sha256=target_deck,
        policy_config_sha256=target_policy,
        meta_manifest_sha256=state.meta_manifest_sha256,
        meta_schedule_sha256=state.meta_schedule_sha256,
        stage_games=target_stage,
        native_baseline=state.native_baseline,
        authority=state.authority,
        revision=state.revision + 1,
        candidate_score=target_candidate_score,
        native_score=target_native_score,
        fault_count=target_faults,
    )


def promote_successive_halving_v1(
    states: Sequence[CandidateStateV1],
    scores: Mapping[str, float],
    *,
    native_baseline_score: float,
    next_stage_games: int,
) -> tuple[CandidateStateV1, ...]:
    """Keep the top half and advance exactly one evaluation budget.

    Every state carries the same native baseline; ``native_baseline_score`` is
    required even though this function only ranks candidates, making omission
    of the comparison arm impossible at the call boundary.
    """

    values = tuple(states)
    if not values:
        raise AlternatingMetaOptimizerError("successive-halving requires at least one candidate")
    if any(type(state) is not CandidateStateV1 for state in values):
        raise AlternatingMetaOptimizerError("successive-halving states must be CandidateStateV1")
    _finite_score(native_baseline_score, "native_baseline_score")
    candidate_ids = tuple(state.candidate_id for state in values)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise AlternatingMetaOptimizerError("successive-halving candidate IDs must be unique")
    if set(scores) != set(candidate_ids):
        raise AlternatingMetaOptimizerError("successive-halving scores must cover exactly the candidates")
    baseline = values[0].native_baseline
    if any(state.native_baseline != baseline for state in values):
        raise AlternatingMetaOptimizerError("native baseline must be identical in every arm")
    try:
        expected_next = SUCCESSIVE_HALVING_GAMES_V1[
            SUCCESSIVE_HALVING_GAMES_V1.index(values[0].stage_games) + 1
        ]
    except (ValueError, IndexError) as exc:
        raise AlternatingMetaOptimizerError("no next successive-halving stage exists") from exc
    if next_stage_games != expected_next:
        raise AlternatingMetaOptimizerError(
            f"next successive-halving stage must be {expected_next}, got {next_stage_games}"
        )
    if any(state.stage_games != values[0].stage_games for state in values):
        raise AlternatingMetaOptimizerError("successive-halving candidates must share one stage")
    ordered = sorted(values, key=lambda state: (-_finite_score(scores[state.candidate_id], "candidate score"), state.candidate_id))
    keep_count = max(1, (len(ordered) + 1) // 2)
    return tuple(
        advance_candidate_state_v1(
            state,
            next_stage_games=next_stage_games,
            candidate_score=float(scores[state.candidate_id]),
            native_score=float(native_baseline_score),
        )
        for state in ordered[:keep_count]
    )


def _config_payload(
    *,
    run_dir: Path,
    candidate_id: str,
    meta_manifest_path: Path,
    meta_manifest_sha256: str,
    meta_schedule_path: Path,
    meta_schedule_sha256: str,
    native_baseline: NativeBaselineArmV1,
    authority: ResearchAuthorityV1,
    deck_mutation_candidate_id: str | None,
) -> dict[str, object]:
    return {
        "schema_version": ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
        "run_dir": str(run_dir),
        "candidate_id": candidate_id,
        "meta_manifest_path": str(meta_manifest_path),
        "meta_manifest_sha256": meta_manifest_sha256,
        "meta_schedule_path": str(meta_schedule_path),
        "meta_schedule_sha256": meta_schedule_sha256,
        "deck_mutation_candidate_id": deck_mutation_candidate_id,
        "native_baseline": native_baseline.to_dict(),
        "authority": authority.to_dict(),
        "research_only": True,
    }


def _state_path(run_dir: Path) -> Path:
    return run_dir / "optimizer_state.json"


def _progress_path(run_dir: Path) -> Path:
    return run_dir / "progress_summary.json"


def _config_from_journal(journal: Mapping[str, Any]) -> Mapping[str, Any]:
    config = journal.get("config")
    if not isinstance(config, Mapping):
        raise AlternatingMetaOptimizerError("optimizer journal config is malformed")
    if config.get("schema_version") != ALTERNATING_META_OPTIMIZER_SCHEMA_V1:
        raise AlternatingMetaOptimizerError("optimizer journal schema mismatch")
    return config


def _verify_journal(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    journal = _load_json(_state_path(root))
    if journal.get("schema_version") != ALTERNATING_META_OPTIMIZER_SCHEMA_V1:
        raise AlternatingMetaOptimizerError("optimizer journal schema mismatch")
    if journal.get("research_only") is not True:
        raise AlternatingMetaOptimizerError("optimizer journal must remain research-only")
    if journal.get("status") not in _STATUSES_V1:
        raise AlternatingMetaOptimizerError("optimizer journal status is invalid")
    config = _config_from_journal(journal)
    manifest = _require_file(config.get("meta_manifest_path", ""), "meta_manifest_path")
    schedule = _require_file(config.get("meta_schedule_path", ""), "meta_schedule_path")
    if _sha256_file(manifest) != config.get("meta_manifest_sha256"):
        raise AlternatingMetaOptimizerError("meta manifest SHA-256 changed")
    if _sha256_file(schedule) != config.get("meta_schedule_sha256"):
        raise AlternatingMetaOptimizerError("meta schedule SHA-256 changed")
    try:
        manifest_value = load_meta_distribution_manifest_v1(manifest, verify_sources=True)
    except MetaDistributionError as exc:
        raise AlternatingMetaOptimizerError(f"meta manifest is not a closed manifest: {exc}") from exc
    if not manifest_value.research_only or manifest_value.training_authority or manifest_value.promotion_authority or manifest_value.submission_authority:
        raise AlternatingMetaOptimizerError("meta manifest authority must remain research-only")
    state_raw = journal.get("state")
    if not isinstance(state_raw, Mapping):
        raise AlternatingMetaOptimizerError("optimizer journal candidate state is malformed")
    if state_raw.get("research_only") is not True:
        raise AlternatingMetaOptimizerError("candidate state must remain research-only")
    state = CandidateStateV1.from_dict(state_raw)
    if _state_sha256(state) != journal.get("state_sha256"):
        raise AlternatingMetaOptimizerError("candidate state SHA-256 does not match journal")
    if state.candidate_id != config.get("candidate_id"):
        raise AlternatingMetaOptimizerError("candidate ID differs from sealed optimizer config")
    if state.meta_manifest_sha256 != config.get("meta_manifest_sha256"):
        raise AlternatingMetaOptimizerError("candidate manifest SHA differs from sealed config")
    if state.meta_schedule_sha256 != config.get("meta_schedule_sha256"):
        raise AlternatingMetaOptimizerError("candidate schedule SHA differs from sealed config")
    baseline_raw = config.get("native_baseline")
    if not isinstance(baseline_raw, Mapping):
        raise AlternatingMetaOptimizerError("sealed native baseline is malformed")
    config_baseline = NativeBaselineArmV1(
        pair_id=str(baseline_raw.get("pair_id", "")),
        deck_sha256=str(baseline_raw.get("deck_sha256", "")),
        policy_sha256=str(baseline_raw.get("policy_sha256", "")),
        evaluator_sha256=str(baseline_raw.get("evaluator_sha256", "")),
        status=str(baseline_raw.get("status", "")),
    )
    if state.native_baseline != config_baseline:
        raise AlternatingMetaOptimizerError("candidate native baseline differs from sealed config")
    if config.get("authority") != ResearchAuthorityV1().to_dict():
        raise AlternatingMetaOptimizerError("sealed optimizer authority is not research-only")
    if state.authority != ResearchAuthorityV1():
        raise AlternatingMetaOptimizerError("candidate authority is not research-only")
    return journal


def _write_progress(run_dir: Path, journal: Mapping[str, Any]) -> None:
    _atomic_write_json(
        _progress_path(run_dir),
        {
            "schema_version": ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
            "status": journal.get("status"),
            "revision": journal.get("revision", 0),
            "candidate_id": journal.get("config", {}).get("candidate_id"),
            "stage_games": journal.get("state", {}).get("stage_games"),
            "phase": journal.get("state", {}).get("phase"),
            "state_sha256": journal.get("state_sha256"),
            "active_checkpoint_sha256": journal.get("active_checkpoint_sha256"),
            "restart_contract": "atomic_checkpoint_resume_rollback_v1",
            "research_only": True,
        },
    )


def initialize_alternating_meta_optimizer_v1(
    *,
    run_dir: Path | str,
    candidate_id: str,
    deck_sha256: str,
    policy_config_sha256: str,
    meta_manifest_path: Path | str,
    meta_schedule_path: Path | str,
    native_baseline: NativeBaselineArmV1,
    deck_mutation_candidate: DeckMutationCandidateV1 | None = None,
    phase: str = POLICY_FIXED_SHORT_V1,
    execute: bool = False,
) -> dict[str, Any]:
    """Create a sealed dry-run journal; ``execute=True`` always fails closed."""

    if execute:
        raise AlternatingMetaOptimizerError(
            "execute is disabled for the research-only alternating optimizer"
        )
    _text(candidate_id, "candidate_id")
    _sha(deck_sha256, "deck_sha256")
    _sha(policy_config_sha256, "policy_config_sha256")
    if type(native_baseline) is not NativeBaselineArmV1:
        raise AlternatingMetaOptimizerError("native baseline is mandatory")
    if deck_mutation_candidate is not None:
        if type(deck_mutation_candidate) is not DeckMutationCandidateV1:
            raise AlternatingMetaOptimizerError("deck_mutation_candidate must be a DeckMutationCandidateV1")
        if deck_mutation_candidate.deck_multiset_sha256 != deck_sha256:
            raise AlternatingMetaOptimizerError("candidate deck SHA differs from deck mutation exact multiset SHA")
        if deck_mutation_candidate.authority != DeckMutationAuthorityV1():
            raise AlternatingMetaOptimizerError("deck mutation candidate authority is not research-only")
    authority = ResearchAuthorityV1()
    root = Path(run_dir).resolve()
    manifest = _require_file(meta_manifest_path, "meta_manifest_path")
    schedule = _require_file(meta_schedule_path, "meta_schedule_path")
    try:
        manifest_value = load_meta_distribution_manifest_v1(manifest, verify_sources=True)
    except MetaDistributionError as exc:
        raise AlternatingMetaOptimizerError(f"meta manifest is not a closed manifest: {exc}") from exc
    if not manifest_value.research_only or manifest_value.training_authority or manifest_value.promotion_authority or manifest_value.submission_authority:
        raise AlternatingMetaOptimizerError("meta manifest authority must remain research-only")
    manifest_sha = _sha256_file(manifest)
    schedule_sha = _sha256_file(schedule)
    config = _config_payload(
        run_dir=root,
        candidate_id=candidate_id,
        meta_manifest_path=manifest,
        meta_manifest_sha256=manifest_sha,
        meta_schedule_path=schedule,
        meta_schedule_sha256=schedule_sha,
        native_baseline=native_baseline,
        authority=authority,
        deck_mutation_candidate_id=(deck_mutation_candidate.candidate_id if deck_mutation_candidate is not None else None),
    )
    state = CandidateStateV1(
        schema_version=ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
        candidate_id=candidate_id,
        parent_candidate_id=None,
        phase=phase,
        deck_sha256=deck_sha256,
        policy_config_sha256=policy_config_sha256,
        meta_manifest_sha256=manifest_sha,
        meta_schedule_sha256=schedule_sha,
        stage_games=SUCCESSIVE_HALVING_GAMES_V1[0],
        native_baseline=native_baseline,
        authority=authority,
    )
    path = _state_path(root)
    if path.exists():
        existing = _verify_journal(root)
        if existing.get("config") != config:
            raise AlternatingMetaOptimizerError(
                "run directory is already sealed to a different candidate configuration"
            )
        return existing
    journal: dict[str, Any] = {
        "schema_version": ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
        "status": "DRY_RUN",
        "revision": 0,
        "config": config,
        "state": state.to_dict(),
        "state_sha256": _state_sha256(state),
        "authority": authority.to_dict(),
        "active_checkpoint_path": None,
        "active_checkpoint_sha256": None,
        "active_checkpoint_descriptor": None,
        "latest_checkpoint_path": None,
        "latest_checkpoint_sha256": None,
        "latest_checkpoint_descriptor": None,
        "checkpoint_count": 0,
        "research_only": True,
    }
    _atomic_write_json(path, journal)
    _write_progress(root, journal)
    return journal


def load_alternating_meta_optimizer_v1(run_dir: Path | str) -> CandidateStateV1:
    """Reload and hash-check the current candidate state."""

    journal = _verify_journal(Path(run_dir).resolve())
    state_raw = journal["state"]
    if not isinstance(state_raw, Mapping):  # pragma: no cover - guarded above
        raise AlternatingMetaOptimizerError("optimizer journal candidate state is malformed")
    return CandidateStateV1.from_dict(state_raw)


def checkpoint_alternating_meta_optimizer_v1(
    run_dir: Path | str,
    *,
    state: CandidateStateV1,
    checkpoint_path: Path | str,
    stage: str,
    metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically publish a content-addressed checkpoint and journal update."""

    if type(state) is not CandidateStateV1:
        raise AlternatingMetaOptimizerError("state must be a CandidateStateV1")
    _text(stage, "checkpoint stage")
    root = Path(run_dir).resolve()
    journal = _verify_journal(root)
    current = load_alternating_meta_optimizer_v1(root)
    config = _config_from_journal(journal)
    if state.candidate_id != current.candidate_id:
        raise AlternatingMetaOptimizerError("checkpoint candidate ID differs from active state")
    if state.revision < current.revision:
        raise AlternatingMetaOptimizerError("checkpoint state revision is stale")
    if state.meta_manifest_sha256 != config.get("meta_manifest_sha256") or state.meta_schedule_sha256 != config.get("meta_schedule_sha256"):
        raise AlternatingMetaOptimizerError("checkpoint meta SHA differs from sealed config")
    artifact = _require_file(checkpoint_path, "checkpoint_path")
    artifact_sha = _sha256_file(artifact)
    count = int(journal.get("checkpoint_count", 0)) + 1
    descriptor_path = root / "checkpoints" / f"{count:04d}-{_state_sha256(state)[:16]}.json"
    descriptor = {
        "schema_version": CHECKPOINT_SCHEMA_V1,
        "ordinal": count,
        "stage": stage,
        "state": state.to_dict(),
        "state_sha256": _state_sha256(state),
        "checkpoint_path": str(artifact),
        "checkpoint_sha256": artifact_sha,
        "config_candidate_id": config.get("candidate_id"),
        "meta_manifest_sha256": config.get("meta_manifest_sha256"),
        "meta_schedule_sha256": config.get("meta_schedule_sha256"),
        "metrics": dict(metrics or {}),
        "research_only": True,
    }
    _atomic_write_json(descriptor_path, descriptor)
    updated = dict(journal)
    updated.update(
        {
            "status": "CHECKPOINTED",
            "revision": int(journal.get("revision", 0)) + 1,
            "state": state.to_dict(),
            "state_sha256": _state_sha256(state),
            "active_checkpoint_path": str(artifact),
            "active_checkpoint_sha256": artifact_sha,
            "active_checkpoint_descriptor": str(descriptor_path),
            "latest_checkpoint_path": str(artifact),
            "latest_checkpoint_sha256": artifact_sha,
            "latest_checkpoint_descriptor": str(descriptor_path),
            "checkpoint_count": count,
        }
    )
    _atomic_write_json(_state_path(root), updated)
    _write_progress(root, updated)
    return updated


def _load_checkpoint_descriptor(path: Path, *, config: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = _load_json(path)
    if descriptor.get("schema_version") != CHECKPOINT_SCHEMA_V1:
        raise AlternatingMetaOptimizerError("checkpoint descriptor schema mismatch")
    if descriptor.get("config_candidate_id") != config.get("candidate_id"):
        raise AlternatingMetaOptimizerError("checkpoint candidate ID differs from sealed config")
    if descriptor.get("meta_manifest_sha256") != config.get("meta_manifest_sha256") or descriptor.get("meta_schedule_sha256") != config.get("meta_schedule_sha256"):
        raise AlternatingMetaOptimizerError("checkpoint meta SHA differs from sealed config")
    state_raw = descriptor.get("state")
    if not isinstance(state_raw, Mapping):
        raise AlternatingMetaOptimizerError("checkpoint state is malformed")
    state = CandidateStateV1.from_dict(state_raw)
    if descriptor.get("state_sha256") != _state_sha256(state):
        raise AlternatingMetaOptimizerError("checkpoint state SHA-256 does not match bytes")
    artifact = _require_file(descriptor.get("checkpoint_path", ""), "checkpoint_path")
    if _sha256_file(artifact) != descriptor.get("checkpoint_sha256"):
        raise AlternatingMetaOptimizerError("checkpoint artifact SHA-256 changed")
    return descriptor


def resume_alternating_meta_optimizer_v1(
    run_dir: Path | str,
    *,
    execute: bool = False,
) -> dict[str, Any]:
    """Reload a journal without starting CABT, learning, or a runner."""

    if execute:
        raise AlternatingMetaOptimizerError(
            "execute is disabled for the research-only alternating optimizer"
        )
    root = Path(run_dir).resolve()
    journal = _verify_journal(root)
    return {
        "schema_version": ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
        "status": "RESUMED",
        "resumed": True,
        "execute": False,
        "candidate_id": journal["config"]["candidate_id"],
        "state": journal["state"],
        "state_sha256": journal["state_sha256"],
        "active_checkpoint_path": journal.get("active_checkpoint_path"),
        "active_checkpoint_sha256": journal.get("active_checkpoint_sha256"),
        "authority": ResearchAuthorityV1().to_dict(),
        "research_only": True,
    }


def rollback_alternating_meta_optimizer_v1(
    run_dir: Path | str,
    *,
    checkpoint_descriptor: Path | str | None = None,
) -> dict[str, Any]:
    """Atomically select a previously published checkpoint as active state."""

    root = Path(run_dir).resolve()
    journal = _verify_journal(root)
    config = _config_from_journal(journal)
    descriptor_path = (
        Path(checkpoint_descriptor).resolve()
        if checkpoint_descriptor is not None
        else Path(journal.get("latest_checkpoint_descriptor", "")).resolve()
    )
    checkpoints_root = (root / "checkpoints").resolve()
    try:
        descriptor_path.relative_to(checkpoints_root)
    except ValueError as exc:
        raise AlternatingMetaOptimizerError(
            "rollback checkpoint descriptor must be inside this run's checkpoints directory"
        ) from exc
    if not descriptor_path.is_file():
        raise AlternatingMetaOptimizerError("rollback checkpoint descriptor does not exist")
    descriptor = _load_checkpoint_descriptor(descriptor_path, config=config)
    updated = dict(journal)
    updated.update(
        {
            "status": "ROLLED_BACK",
            "revision": int(journal.get("revision", 0)) + 1,
            "state": descriptor["state"],
            "state_sha256": descriptor["state_sha256"],
            "active_checkpoint_path": descriptor["checkpoint_path"],
            "active_checkpoint_sha256": descriptor["checkpoint_sha256"],
            "active_checkpoint_descriptor": str(descriptor_path),
            "rollback_count": int(journal.get("rollback_count", 0)) + 1,
        }
    )
    _atomic_write_json(_state_path(root), updated)
    _write_progress(root, updated)
    return updated


def execute_alternating_meta_optimizer_v1(
    run_dir: Path | str,
    *,
    execute: bool = False,
    runner: Callable[[CandidateStateV1], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Return a dry-run descriptor; execution is deliberately impossible."""

    if execute:
        raise AlternatingMetaOptimizerError(
            "execute is disabled for the research-only alternating optimizer"
        )
    state = load_alternating_meta_optimizer_v1(run_dir)
    # ``runner`` is intentionally ignored in dry-run mode.  This explicit
    # branch prevents an accidental callback from starting a process.
    del runner
    return {
        "schema_version": ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
        "status": "DRY_RUN",
        "execute": False,
        "launch_allowed": False,
        "candidate_id": state.candidate_id,
        "stage_games": state.stage_games,
        "phase": state.phase,
        "reason": "execution, CABT, learning, promotion, and submission are disabled",
        "authority": ResearchAuthorityV1().to_dict(),
        "research_only": True,
    }


__all__ = [
    "ALTERNATING_META_OPTIMIZER_SCHEMA_V1",
    "CHECKPOINT_SCHEMA_V1",
    "DECK_FIXED_LONG_V1",
    "PHASES_V1",
    "POLICY_FIXED_SHORT_V1",
    "SUCCESSIVE_HALVING_GAMES_V1",
    "AlternatingMetaOptimizerError",
    "CandidateStateV1",
    "NativeBaselineArmV1",
    "ResearchAuthorityV1",
    "advance_candidate_state_v1",
    "checkpoint_alternating_meta_optimizer_v1",
    "execute_alternating_meta_optimizer_v1",
    "initialize_alternating_meta_optimizer_v1",
    "load_alternating_meta_optimizer_v1",
    "promote_successive_halving_v1",
    "resume_alternating_meta_optimizer_v1",
    "rollback_alternating_meta_optimizer_v1",
]
