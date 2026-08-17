"""Native-bound alternating state bridge for the meta-overfit research loop.

The existing :mod:`alternating_meta_optimizer_v1` owns candidate-state and
successive-halving invariants.  This module only binds those states to the
Task 2 iteration manifest, its public-advantage/protocol identities, native
control, and evaluation/rollback evidence.  It deliberately has no runner,
training, promotion, submission, or longrun authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .alternating_meta_optimizer_v1 import (
    ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
    DECK_FIXED_LONG_V1,
    PHASES_V1,
    POLICY_FIXED_SHORT_V1,
    SUCCESSIVE_HALVING_GAMES_V1,
    AlternatingMetaOptimizerError,
    CandidateStateV1,
    NativeBaselineArmV1,
    ResearchAuthorityV1,
    _state_sha256,
    advance_candidate_state_v1,
    promote_successive_halving_v1,
)
from .deck_mutation_v1 import DeckMutationAuthorityV1, DeckMutationCandidateV1
from .native_meta_overfit_iteration_v1 import (
    NativeMetaOverfitIterationError,
    verify_native_meta_overfit_iteration_v1,
)
from .native_public_advantage_v1 import NativePublicAdvantageError, PublicAdvantageTableV1


BRIDGE_SCHEMA_V1 = "meta-specialist-native-meta-overfit-alternating-v1"
EXACT_STAGE_GAMES_V1 = SUCCESSIVE_HALVING_GAMES_V1
NATIVE_REGRESSION_STOP_AFTER_V1 = 2
DEFAULT_MAX_SEAT_GAP_V1 = 0.05
AUTHORITY_FALSE_V1 = {
    "execute_allowed": False,
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}
_TASK2_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}
_SHA_CHARS = frozenset("0123456789abcdef")
_WDL_VALUE = {"win": 1.0, "draw": 0.5, "loss": 0.0}
_SUMMARY_RECORD_KEYS = frozenset(
    {"game_id", "seed", "opponent_id", "family", "seat", "outcome", "fault"}
)


class _ImmutableDict(dict):
    """Small JSON-compatible mapping that rejects in-place mutation."""

    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("content-bound mapping is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class NativeMetaOverfitAlternatingError(ValueError):
    """Raised when the bridge cannot prove a safe native-bound transition."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NativeMetaOverfitAlternatingError("value is not canonical JSON") from exc


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NativeMetaOverfitAlternatingError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    raise NativeMetaOverfitAlternatingError(f"non-finite JSON constant: {token}")


def _read_canonical_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except NativeMetaOverfitAlternatingError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeMetaOverfitAlternatingError(f"cannot read JSON source: {path}") from exc
    if type(value) is not dict or raw != _canonical_bytes(value):
        raise NativeMetaOverfitAlternatingError(f"source must be canonical JSON: {path}")
    return value


def _sha_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise NativeMetaOverfitAlternatingError(f"cannot hash source: {path}") from exc


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise NativeMetaOverfitAlternatingError(f"{name} must be a lowercase SHA-256 string")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise NativeMetaOverfitAlternatingError(f"{name} must be a non-empty string")
    return value


def _finite_unit(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise NativeMetaOverfitAlternatingError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise NativeMetaOverfitAlternatingError(f"{name} must be finite in [0,1]")
    return result


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise NativeMetaOverfitAlternatingError(f"{name} must be a nonnegative int")
    return value


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value <= 0:
        raise NativeMetaOverfitAlternatingError(f"{name} must be a positive int")
    return value


def _authority_false(value: object, name: str) -> None:
    if value != _TASK2_AUTHORITY_FALSE:
        raise NativeMetaOverfitAlternatingError(f"{name} authority grants permission")


def _semantic_sha(schema: str, value: Mapping[str, object]) -> str:
    return hashlib.sha256(schema.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _native_control_artifact_sha256(
    *,
    stage_games: int,
    native_pair_id: str,
    native_policy_sha256: str,
    native_deck_sha256: str,
    native_evaluator_sha256: str,
    protocol_sha256: str,
    game_id_universe_sha256: str,
    seed_universe_sha256: str,
    strata_sha256: str,
    native_game_records: Sequence[Mapping[str, object]],
) -> str:
    """Hash the canonical native control ledger and all identity bindings."""

    return _semantic_sha(
        "mage-ptcg:native-control-artifact:v1",
        {
            "stage_games": stage_games,
            "native_pair_id": native_pair_id,
            "native_policy_sha256": native_policy_sha256,
            "native_deck_sha256": native_deck_sha256,
            "native_evaluator_sha256": native_evaluator_sha256,
            "protocol_sha256": protocol_sha256,
            "game_id_universe_sha256": game_id_universe_sha256,
            "seed_universe_sha256": seed_universe_sha256,
            "strata_sha256": strata_sha256,
            "native_game_records": sorted(
                (dict(record) for record in native_game_records),
                key=lambda record: str(record["game_id"]),
            ),
        },
    )


def _native_control_block_sha256(
    *,
    stage_games: int,
    native_score: float,
    native_control_artifact_sha256: str,
    protocol_sha256: str,
    game_id_universe_sha256: str,
    seed_universe_sha256: str,
    strata_sha256: str,
) -> str:
    """Hash the native score/control block used by every candidate arm."""

    return _semantic_sha(
        "mage-ptcg:native-control-block:v1",
        {
            "stage_games": stage_games,
            "native_score": float(native_score),
            "native_control_artifact_sha256": native_control_artifact_sha256,
            "protocol_sha256": protocol_sha256,
            "game_id_universe_sha256": game_id_universe_sha256,
            "seed_universe_sha256": seed_universe_sha256,
            "strata_sha256": strata_sha256,
        },
    )


def _state_binding_payload(state: CandidateStateV1) -> dict[str, object]:
    """Stable state identity used to validate journal lineage transitions."""

    return {
        "revision": state.revision,
        "stage_games": state.stage_games,
        "phase": state.phase,
        "deck_sha256": state.deck_sha256,
        "policy_config_sha256": state.policy_config_sha256,
        "meta_manifest_sha256": state.meta_manifest_sha256,
        "meta_schedule_sha256": state.meta_schedule_sha256,
        "native_baseline": state.native_baseline.to_dict(),
    }


def _validate_summary_state_identity(
    summary: EvaluationSummaryV1,
    state: CandidateStateV1,
) -> None:
    if summary.candidate_id != state.candidate_id:
        raise NativeMetaOverfitAlternatingError("journal summary candidate_id differs from state")
    if summary.stage_games != state.stage_games:
        raise NativeMetaOverfitAlternatingError("journal summary stage_games differs from state")
    expected = {
        "candidate_policy_sha256": state.policy_config_sha256,
        "candidate_deck_sha256": state.deck_sha256,
        "candidate_evaluator_sha256": state.native_baseline.evaluator_sha256,
        "native_pair_id": state.native_baseline.pair_id,
        "native_policy_sha256": state.native_baseline.policy_sha256,
        "native_deck_sha256": state.native_baseline.deck_sha256,
        "native_evaluator_sha256": state.native_baseline.evaluator_sha256,
    }
    for name, value in expected.items():
        if getattr(summary, name) != value:
            raise NativeMetaOverfitAlternatingError(
                f"journal summary {name} differs from state/native baseline"
            )


def _evaluation_summary_sha_v1(summary: EvaluationSummaryV1) -> str:
    return hashlib.sha256(
        b"mage-ptcg:native-meta-overfit-evaluation-summary:v1\0" + _canonical_bytes(summary.to_dict())
    ).hexdigest()


def _inside_root(root: Path, value: str | Path, label: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise NativeMetaOverfitAlternatingError(f"{label} escapes repo_root") from exc
    if not path.is_file():
        raise NativeMetaOverfitAlternatingError(f"{label} is missing: {path}")
    return path


def _infer_repo_root(path: Path) -> Path:
    """Find the nearest repository root without widening outside the workspace."""
    current = path.resolve().parent
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            return candidate
    return current


def _summary_sha(summary: "EvaluationSummaryV1") -> str:
    return _semantic_sha(
        "mage-ptcg:native-meta-overfit-evaluation-summary:v2",
        summary.to_dict(),
    )


def _iteration_binding(
    iteration_manifest_path: str | Path,
    *,
    public_advantage_table_path: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, object]:
    path = Path(iteration_manifest_path).resolve()
    if not path.is_file():
        raise NativeMetaOverfitAlternatingError(f"iteration manifest is missing: {path}")
    root = Path(repo_root).resolve() if repo_root is not None else _infer_repo_root(path)
    _inside_root(root, path, "iteration manifest")
    manifest = _read_canonical_json(path)
    try:
        verified = verify_native_meta_overfit_iteration_v1(path, root)
    except (NativeMetaOverfitIterationError, OSError, ValueError) as exc:
        raise NativeMetaOverfitAlternatingError(
            f"strict Task2 iteration verification failed: {exc}"
        ) from exc
    if verified != manifest:
        raise NativeMetaOverfitAlternatingError("strict Task2 verifier returned a different manifest")
    if manifest.get("schema_version") != "meta-specialist-native-meta-overfit-iteration-v1":
        raise NativeMetaOverfitAlternatingError("iteration manifest schema is invalid")
    if manifest.get("purpose") != "NATIVE_PRESERVING_META_OVERFIT_RESEARCH_ONLY":
        raise NativeMetaOverfitAlternatingError("iteration manifest purpose is invalid")
    _authority_false(manifest.get("authority"), "iteration manifest")
    if manifest.get("ready_for_evaluation") is not False:
        raise NativeMetaOverfitAlternatingError("iteration manifest cannot grant evaluation readiness")
    gate_status = manifest.get("gate_status")
    if type(gate_status) is not dict:
        raise NativeMetaOverfitAlternatingError("iteration manifest gate status is missing")
    for gate in (
        "curriculum_verified",
        "outcome_adapter_verified",
        "public_advantage_table_verified",
        "native_control_bound",
        "meta_train_only",
        "heldout_zero_exposure",
        "authority_false",
    ):
        if gate_status.get(gate) is not True:
            raise NativeMetaOverfitAlternatingError(f"iteration manifest gate is not closed: {gate}")
    for gate in ("package_closure", "evaluator_closure", "performance_gate"):
        if gate_status.get(gate) is not False:
            raise NativeMetaOverfitAlternatingError(f"iteration manifest gate must remain false: {gate}")
    supplied_semantic = _sha(manifest.get("iteration_sha256"), "iteration_sha256")
    expected_semantic = _semantic_sha(
        str(manifest["schema_version"]),
        {key: value for key, value in manifest.items() if key != "iteration_sha256"},
    )
    if supplied_semantic != expected_semantic:
        raise NativeMetaOverfitAlternatingError("iteration manifest semantic SHA mismatch")
    sources = manifest.get("sources")
    if type(sources) is not list or not sources:
        raise NativeMetaOverfitAlternatingError("iteration source bindings are missing")
    for source in sources:
        if type(source) is not dict or set(source) != {"path", "file_sha256", "role"}:
            raise NativeMetaOverfitAlternatingError("iteration source binding is invalid")
        source_path = _inside_root(root, source["path"], "iteration source")
        if _sha_file(source_path) != source["file_sha256"]:
            raise NativeMetaOverfitAlternatingError("iteration source SHA mismatch")
    adapter = manifest.get("outcome_adapter_identity")
    table = manifest.get("public_advantage_identity")
    baseline = manifest.get("native_baseline")
    if type(adapter) is not dict or type(table) is not dict or type(baseline) is not dict:
        raise NativeMetaOverfitAlternatingError("iteration manifest identity bindings are incomplete")
    protocol_sha = _sha(adapter.get("protocol_sha256"), "iteration protocol_sha256")
    execution_sha = _sha(adapter.get("execution_closure_sha256"), "iteration execution_closure_sha256")
    table_sha = _sha(table.get("table_sha256"), "public advantage table_sha256")
    table_file_sha = _sha(table.get("file_sha256"), "public advantage table file_sha256")
    baseline_id = baseline.get("candidate_id") or baseline.get("pair_id")
    _text(baseline_id, "iteration native baseline candidate_id")
    _sha(baseline.get("policy_sha256"), "iteration native baseline policy_sha256")
    _sha(baseline.get("deck_sha256"), "iteration native baseline deck_sha256")
    _sha(baseline.get("evaluator_sha256"), "iteration native baseline evaluator_sha256")
    _authority_false(baseline.get("authority"), "iteration native baseline")
    if baseline.get("research_only") is not True:
        raise NativeMetaOverfitAlternatingError("iteration native baseline must be research_only")
    table_path: Path | None = None
    if public_advantage_table_path is not None or table.get("path"):
        table_path = (
            _inside_root(root, public_advantage_table_path, "public advantage table")
            if public_advantage_table_path is not None
            else _inside_root(root, table["path"], "bound public advantage table")
        )
        if _sha_file(table_path) != table_file_sha:
            raise NativeMetaOverfitAlternatingError("public advantage table file SHA mismatch")
        try:
            table_object = PublicAdvantageTableV1.from_dict(_read_canonical_json(table_path))
        except (NativePublicAdvantageError, NativeMetaOverfitAlternatingError, TypeError, ValueError) as exc:
            raise NativeMetaOverfitAlternatingError("bound public advantage table is invalid") from exc
        if table_object.table_sha256 != table_sha:
            raise NativeMetaOverfitAlternatingError("public advantage table semantic SHA mismatch")
        if table_object.baseline_policy_sha256 != baseline.get("policy_sha256"):
            raise NativeMetaOverfitAlternatingError("public advantage table baseline policy differs from native")
    return {
        "path": path,
        "file_sha256": _sha_file(path),
        "manifest": manifest,
        "iteration_sha256": supplied_semantic,
        "protocol_sha256": protocol_sha,
        "execution_closure_sha256": execution_sha,
        "table_sha256": table_sha,
        "table_file_sha256": table_file_sha,
        "native_baseline": baseline,
        "repo_root": root,
        "public_advantage_table_path": table_path,
    }


def _bind_baseline(manifest_baseline: Mapping[str, object], baseline: NativeBaselineArmV1) -> None:
    if baseline.status != "PROVEN":
        raise NativeMetaOverfitAlternatingError(
            "native baseline must be PROVEN before it can be used as control"
        )
    manifest_id = manifest_baseline.get("candidate_id") or manifest_baseline.get("pair_id")
    if manifest_id != baseline.pair_id:
        raise NativeMetaOverfitAlternatingError("native baseline pair_id differs from iteration manifest")
    for field in ("deck_sha256", "policy_sha256", "evaluator_sha256"):
        if manifest_baseline.get(field) != getattr(baseline, field):
            raise NativeMetaOverfitAlternatingError(f"native baseline {field} differs from iteration manifest")


def _validate_state_binding(
    state: CandidateStateV1,
    *,
    iteration_manifest_path: str | Path,
    meta_schedule_path: str | Path | None = None,
    public_advantage_table_path: str | Path | None = None,
) -> dict[str, object]:
    if type(state) is not CandidateStateV1:
        raise NativeMetaOverfitAlternatingError("state must be a CandidateStateV1")
    if state.authority != ResearchAuthorityV1():
        raise NativeMetaOverfitAlternatingError("candidate state authority must remain false")
    if state.native_baseline.status != "PROVEN":
        raise NativeMetaOverfitAlternatingError(
            "native baseline must be PROVEN before evaluation"
        )
    binding = _iteration_binding(
        iteration_manifest_path,
        public_advantage_table_path=public_advantage_table_path,
    )
    if state.meta_manifest_sha256 != binding["file_sha256"]:
        raise NativeMetaOverfitAlternatingError("candidate state iteration manifest SHA mismatch")
    _bind_baseline(binding["native_baseline"], state.native_baseline)
    if meta_schedule_path is not None:
        schedule = Path(meta_schedule_path).resolve()
        if not schedule.is_file() or _sha_file(schedule) != state.meta_schedule_sha256:
            raise NativeMetaOverfitAlternatingError("candidate state meta schedule SHA mismatch")
    return binding


@dataclass(frozen=True, slots=True)
class EvaluationSummaryV1:
    """One candidate/native common24 stage summary with replayable WDL evidence."""

    candidate_id: str
    native_pair_id: str
    stage_games: int
    protocol_sha256: str
    candidate_score: float
    native_score: float
    candidate_fault_count: int
    native_fault_count: int
    candidate_seat0_games: int
    candidate_seat1_games: int
    candidate_seat0_score: float
    candidate_seat1_score: float
    native_seat0_games: int
    native_seat1_games: int
    native_seat0_score: float
    native_seat1_score: float
    candidate_policy_sha256: str | None = None
    candidate_deck_sha256: str | None = None
    candidate_evaluator_sha256: str | None = None
    native_policy_sha256: str | None = None
    native_deck_sha256: str | None = None
    native_evaluator_sha256: str | None = None
    native_control_artifact_sha256: str | None = None
    native_control_block_sha256: str | None = None
    candidate_game_records: tuple[Mapping[str, object], ...] = ()
    native_game_records: tuple[Mapping[str, object], ...] = ()
    game_id_universe_sha256: str | None = None
    seed_universe_sha256: str | None = None
    strata_sha256: str | None = None
    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id")
        _text(self.native_pair_id, "native_pair_id")
        if self.stage_games not in EXACT_STAGE_GAMES_V1:
            raise NativeMetaOverfitAlternatingError("evaluation stage must be 96, 384, 768, or 1536")
        _sha(self.protocol_sha256, "protocol_sha256")
        _finite_unit(self.candidate_score, "candidate_score")
        _finite_unit(self.native_score, "native_score")
        _nonnegative_int(self.candidate_fault_count, "candidate_fault_count")
        _nonnegative_int(self.native_fault_count, "native_fault_count")
        if self.candidate_fault_count > self.stage_games or self.native_fault_count > self.stage_games:
            raise NativeMetaOverfitAlternatingError("fault count cannot exceed stage games")
        for prefix in ("candidate", "native"):
            seat0_games = getattr(self, f"{prefix}_seat0_games")
            seat1_games = getattr(self, f"{prefix}_seat1_games")
            _positive_int(seat0_games, f"{prefix}_seat0_games")
            _positive_int(seat1_games, f"{prefix}_seat1_games")
            if seat0_games + seat1_games != self.stage_games:
                raise NativeMetaOverfitAlternatingError(f"{prefix} seat evidence must cover both seats")
            _finite_unit(getattr(self, f"{prefix}_seat0_score"), f"{prefix}_seat0_score")
            _finite_unit(getattr(self, f"{prefix}_seat1_score"), f"{prefix}_seat1_score")
        for name in (
            "candidate_policy_sha256",
            "candidate_deck_sha256",
            "candidate_evaluator_sha256",
            "native_policy_sha256",
            "native_deck_sha256",
            "native_evaluator_sha256",
            "native_control_artifact_sha256",
            "native_control_block_sha256",
            "game_id_universe_sha256",
            "seed_universe_sha256",
            "strata_sha256",
        ):
            _sha(getattr(self, name), f"summary.{name}")
        candidate_records = self._normalize_records(self.candidate_game_records, "candidate")
        native_records = self._normalize_records(self.native_game_records, "native")
        if len(candidate_records) != self.stage_games or len(native_records) != self.stage_games:
            raise NativeMetaOverfitAlternatingError("summary game records must cover the exact stage")
        object.__setattr__(self, "candidate_game_records", candidate_records)
        object.__setattr__(self, "native_game_records", native_records)
        candidate_meta = self._metadata(candidate_records)
        native_meta = self._metadata(native_records)
        if candidate_meta != native_meta:
            raise NativeMetaOverfitAlternatingError("candidate/native common24 strata differ")
        expected_game_sha = _semantic_sha("mage-ptcg:common24-game-id-universe:v1", {"game_ids": sorted(candidate_meta["game_ids"])})
        expected_seed_sha = _semantic_sha("mage-ptcg:common24-seed-universe:v1", {"seeds": sorted(candidate_meta["seeds"])})
        expected_strata_sha = _semantic_sha("mage-ptcg:common24-strata:v1", {"strata": candidate_meta["strata"]})
        if self.game_id_universe_sha256 != expected_game_sha:
            raise NativeMetaOverfitAlternatingError("game-id universe SHA does not reproduce")
        if self.seed_universe_sha256 != expected_seed_sha:
            raise NativeMetaOverfitAlternatingError("seed universe SHA does not reproduce")
        if self.strata_sha256 != expected_strata_sha:
            raise NativeMetaOverfitAlternatingError("common24 strata SHA does not reproduce")
        expected_control_artifact = _native_control_artifact_sha256(
            stage_games=self.stage_games,
            native_pair_id=self.native_pair_id,
            native_policy_sha256=self.native_policy_sha256,
            native_deck_sha256=self.native_deck_sha256,
            native_evaluator_sha256=self.native_evaluator_sha256,
            protocol_sha256=self.protocol_sha256,
            game_id_universe_sha256=self.game_id_universe_sha256,
            seed_universe_sha256=self.seed_universe_sha256,
            strata_sha256=self.strata_sha256,
            native_game_records=native_records,
        )
        if self.native_control_artifact_sha256 != expected_control_artifact:
            raise NativeMetaOverfitAlternatingError(
                "native control artifact SHA does not reproduce from the control ledger"
            )
        expected_control_block = _native_control_block_sha256(
            stage_games=self.stage_games,
            native_score=self.native_score,
            native_control_artifact_sha256=expected_control_artifact,
            protocol_sha256=self.protocol_sha256,
            game_id_universe_sha256=self.game_id_universe_sha256,
            seed_universe_sha256=self.seed_universe_sha256,
            strata_sha256=self.strata_sha256,
        )
        if self.native_control_block_sha256 != expected_control_block:
            raise NativeMetaOverfitAlternatingError(
                "native control block SHA does not reproduce from score and control identity"
            )
        derived = self._derived_metrics(candidate_records, native_records)
        for name, expected in derived.items():
            actual = getattr(self, name)
            if isinstance(expected, float):
                if abs(float(actual) - expected) > 1e-12:
                    raise NativeMetaOverfitAlternatingError(f"{name} is not derived from game records")
            elif actual != expected:
                raise NativeMetaOverfitAlternatingError(f"{name} is not derived from game records")

    @staticmethod
    def _normalize_records(
        records: Sequence[Mapping[str, object]], prefix: str
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(records, (tuple, list)) or not records:
            raise NativeMetaOverfitAlternatingError(f"{prefix} game records are required")
        normalized: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in records:
            if not isinstance(raw, Mapping) or set(raw) != _SUMMARY_RECORD_KEYS:
                raise NativeMetaOverfitAlternatingError(f"{prefix} game record schema is invalid")
            item = dict(raw)
            for name in ("game_id", "seed", "opponent_id", "family"):
                _text(item.get(name), f"{prefix}.{name}")
            if item["game_id"] in seen:
                raise NativeMetaOverfitAlternatingError(f"{prefix} game ids are duplicated")
            seen.add(str(item["game_id"]))
            if type(item["seat"]) is not int or item["seat"] not in (0, 1):
                raise NativeMetaOverfitAlternatingError(f"{prefix}.seat is invalid")
            if item["outcome"] not in _WDL_VALUE:
                raise NativeMetaOverfitAlternatingError(f"{prefix}.outcome is invalid")
            if type(item["fault"]) is not bool:
                raise NativeMetaOverfitAlternatingError(f"{prefix}.fault is invalid")
            normalized.append(item)
        return tuple(
            _ImmutableDict(row)
            for row in sorted(normalized, key=lambda row: str(row["game_id"]))
        )

    @staticmethod
    def _metadata(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
        return {
            "game_ids": [str(row["game_id"]) for row in records],
            "seeds": [str(row["seed"]) for row in records],
            "strata": [
                {
                    "game_id": str(row["game_id"]),
                    "seed": str(row["seed"]),
                    "opponent_id": str(row["opponent_id"]),
                    "family": str(row["family"]),
                    "seat": int(row["seat"]),
                }
                for row in records
            ],
        }

    @classmethod
    def _derived_metrics(
        cls,
        candidate_records: Sequence[Mapping[str, object]],
        native_records: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for prefix, records in (("candidate", candidate_records), ("native", native_records)):
            seat0 = [row for row in records if row["seat"] == 0]
            seat1 = [row for row in records if row["seat"] == 1]
            result[f"{prefix}_fault_count"] = sum(1 for row in records if row["fault"])
            result[f"{prefix}_seat0_games"] = len(seat0)
            result[f"{prefix}_seat1_games"] = len(seat1)
            result[f"{prefix}_seat0_score"] = sum(_WDL_VALUE[row["outcome"]] for row in seat0) / len(seat0)
            result[f"{prefix}_seat1_score"] = sum(_WDL_VALUE[row["outcome"]] for row in seat1) / len(seat1)
            result[f"{prefix}_score"] = sum(_WDL_VALUE[row["outcome"]] for row in records) / len(records)
        return result

    @property
    def candidate_delta(self) -> float:
        return float(self.candidate_score) - float(self.native_score)

    @property
    def candidate_seat_gap(self) -> float:
        return abs(float(self.candidate_seat0_score) - float(self.candidate_seat1_score))

    @property
    def native_seat_gap(self) -> float:
        return abs(float(self.native_seat0_score) - float(self.native_seat1_score))

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {
            "candidate_delta": self.candidate_delta,
            "candidate_seat_gap": self.candidate_seat_gap,
            "native_seat_gap": self.native_seat_gap,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "EvaluationSummaryV1":
        if type(payload) is not dict:
            raise NativeMetaOverfitAlternatingError("evaluation summary must be an object")
        fields = {field for field in cls.__dataclass_fields__}
        derived_fields = {"candidate_delta", "candidate_seat_gap", "native_seat_gap"}
        if set(payload).difference(fields | derived_fields):
            raise NativeMetaOverfitAlternatingError("evaluation summary has unknown fields")
        if not fields.issubset(payload):
            raise NativeMetaOverfitAlternatingError("evaluation summary is missing required fields")
        values = {field: payload[field] for field in fields}
        for field in ("candidate_game_records", "native_game_records"):
            values[field] = tuple(values[field])
        item = cls(**values)
        expected = item.to_dict()
        for field in derived_fields.intersection(payload):
            supplied = payload[field]
            if type(supplied) not in (int, float) or isinstance(supplied, bool) or not math.isfinite(float(supplied)):
                raise NativeMetaOverfitAlternatingError(f"evaluation summary {field} is not finite")
            if abs(float(supplied) - float(expected[field])) > 1e-12:
                raise NativeMetaOverfitAlternatingError(f"evaluation summary {field} does not reproduce")
        return item


def _coerce_summary(summary: EvaluationSummaryV1 | Mapping[str, object]) -> EvaluationSummaryV1:
    if type(summary) is EvaluationSummaryV1:
        # Reconstruct even an in-memory instance so a hostile low-level record
        # mutation cannot bypass aggregate/control SHA derivation.
        return EvaluationSummaryV1.from_dict(summary.to_dict())
    if isinstance(summary, Mapping):
        return EvaluationSummaryV1.from_dict(dict(summary))
    raise NativeMetaOverfitAlternatingError("evaluation summary has an unsupported type")


def _native_control_identity(summary: EvaluationSummaryV1) -> tuple[object, ...]:
    """Return the complete common24 native-control identity for cross-arm checks."""

    return (
        summary.stage_games,
        summary.native_pair_id,
        summary.native_policy_sha256,
        summary.native_deck_sha256,
        summary.native_evaluator_sha256,
        summary.native_control_artifact_sha256,
        summary.native_control_block_sha256,
        float(summary.native_score),
        summary.protocol_sha256,
        summary.game_id_universe_sha256,
        summary.seed_universe_sha256,
        summary.strata_sha256,
    )


@dataclass(frozen=True, slots=True)
class NativeRegressionJournalV1:
    """Immutable authority/lineage record with internal transition methods."""

    candidate_id: str
    state_sha256: str
    consecutive_native_regressions: int = 0
    last_summary_sha256: str | None = None
    last_decision_sha256: str | None = None
    authority: Mapping[str, bool] = field(default_factory=lambda: dict(AUTHORITY_FALSE_V1))
    state_binding: Mapping[str, object] = field(default_factory=dict)
    _seal_sha256: str = field(init=False, default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        _text(self.candidate_id, "journal.candidate_id")
        _sha(self.state_sha256, "journal.state_sha256")
        _nonnegative_int(self.consecutive_native_regressions, "journal.consecutive_native_regressions")
        if self.last_summary_sha256 is not None:
            _sha(self.last_summary_sha256, "journal.last_summary_sha256")
        if self.last_decision_sha256 is not None:
            _sha(self.last_decision_sha256, "journal.last_decision_sha256")
        if dict(self.authority) != AUTHORITY_FALSE_V1:
            raise NativeMetaOverfitAlternatingError("journal authority grants permission")
        object.__setattr__(self, "authority", _ImmutableDict(AUTHORITY_FALSE_V1))
        if type(self.state_binding) is not dict or set(self.state_binding) != {
            "revision",
            "stage_games",
            "phase",
            "deck_sha256",
            "policy_config_sha256",
            "meta_manifest_sha256",
            "meta_schedule_sha256",
            "native_baseline",
        }:
            raise NativeMetaOverfitAlternatingError("journal state binding is malformed")
        baseline = self.state_binding.get("native_baseline")
        if type(baseline) is not dict or set(baseline) != {
            "pair_id", "deck_sha256", "policy_sha256", "evaluator_sha256", "status"
        }:
            raise NativeMetaOverfitAlternatingError("journal state baseline binding is malformed")
        _nonnegative_int(self.state_binding["revision"], "journal state revision")
        if self.state_binding["stage_games"] not in EXACT_STAGE_GAMES_V1:
            raise NativeMetaOverfitAlternatingError("journal state stage is invalid")
        if self.state_binding["phase"] not in PHASES_V1:
            raise NativeMetaOverfitAlternatingError("journal state phase is invalid")
        for field_name in (
            "deck_sha256", "policy_config_sha256", "meta_manifest_sha256", "meta_schedule_sha256"
        ):
            _sha(self.state_binding[field_name], f"journal state {field_name}")
        for field_name in ("deck_sha256", "policy_sha256", "evaluator_sha256"):
            _sha(baseline[field_name], f"journal state baseline {field_name}")
        _text(baseline["pair_id"], "journal state baseline pair_id")
        if baseline["status"] != "PROVEN":
            raise NativeMetaOverfitAlternatingError("journal state baseline must be PROVEN")
        frozen_binding = dict(self.state_binding)
        frozen_binding["native_baseline"] = _ImmutableDict(baseline)
        object.__setattr__(self, "state_binding", _ImmutableDict(frozen_binding))
        object.__setattr__(self, "_seal_sha256", self._compute_content_sha256())

    def _compute_content_sha256(self) -> str:
        return _semantic_sha(
            "mage-ptcg:native-regression-journal:v1",
            self.to_dict(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": BRIDGE_SCHEMA_V1,
            "candidate_id": self.candidate_id,
            "state_sha256": self.state_sha256,
            "consecutive_native_regressions": self.consecutive_native_regressions,
            "last_summary_sha256": self.last_summary_sha256,
            "last_decision_sha256": self.last_decision_sha256,
            "authority": dict(self.authority),
            "state_binding": {
                **dict(self.state_binding),
                "native_baseline": dict(self.state_binding["native_baseline"]),
            },
        }

    @property
    def content_sha256(self) -> str:
        """Content address used when persisting and reloading the journal."""

        return self._seal_sha256

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "NativeRegressionJournalV1":
        if type(payload) is not dict:
            raise NativeMetaOverfitAlternatingError("regression journal must be an object")
        expected = {
            "schema_version",
            "candidate_id",
            "state_sha256",
            "consecutive_native_regressions",
            "last_summary_sha256",
            "last_decision_sha256",
            "authority",
            "state_binding",
        }
        if set(payload) != expected:
            raise NativeMetaOverfitAlternatingError("regression journal schema is invalid")
        if payload.get("schema_version") != BRIDGE_SCHEMA_V1:
            raise NativeMetaOverfitAlternatingError("regression journal schema mismatch")
        authority = payload.get("authority")
        if type(authority) is not dict:
            raise NativeMetaOverfitAlternatingError("regression journal authority is malformed")
        try:
            return cls(
                candidate_id=payload["candidate_id"],
                state_sha256=payload["state_sha256"],
                consecutive_native_regressions=payload["consecutive_native_regressions"],
                last_summary_sha256=payload["last_summary_sha256"],
                last_decision_sha256=payload["last_decision_sha256"],
                authority=authority,
                state_binding=payload["state_binding"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NativeMetaOverfitAlternatingError("regression journal is malformed") from exc

    def _validate_current_state(self, state: CandidateStateV1) -> None:
        if _state_sha256(state) != self.state_sha256:
            raise NativeMetaOverfitAlternatingError("journal state SHA mismatch")
        if _state_binding_payload(state) != dict(self.state_binding):
            raise NativeMetaOverfitAlternatingError("journal state binding differs from state")

    def _validate_next_state(self, state: CandidateStateV1) -> None:
        previous = dict(self.state_binding)
        if state.candidate_id != self.candidate_id:
            raise NativeMetaOverfitAlternatingError("journal candidate_id differs from next state")
        if state.revision != int(previous["revision"]) + 1:
            raise NativeMetaOverfitAlternatingError("journal next state revision is not consecutive")
        for name in ("meta_manifest_sha256", "meta_schedule_sha256"):
            if getattr(state, name) != previous[name]:
                raise NativeMetaOverfitAlternatingError(f"journal next state {name} changed unexpectedly")
        previous_phase = str(previous["phase"])
        if previous_phase == POLICY_FIXED_SHORT_V1 and state.phase == POLICY_FIXED_SHORT_V1:
            if state.policy_config_sha256 != previous["policy_config_sha256"]:
                raise NativeMetaOverfitAlternatingError("journal policy-fixed state changed policy")
        if previous_phase == DECK_FIXED_LONG_V1 and state.phase == DECK_FIXED_LONG_V1:
            if state.deck_sha256 != previous["deck_sha256"]:
                raise NativeMetaOverfitAlternatingError("journal deck-fixed state changed deck")
        baseline = previous["native_baseline"]
        if state.native_baseline.to_dict() != dict(baseline):
            raise NativeMetaOverfitAlternatingError("journal next state native baseline changed")
        previous_stage = int(previous["stage_games"])
        if state.stage_games not in (previous_stage, EXACT_STAGE_GAMES_V1[EXACT_STAGE_GAMES_V1.index(previous_stage) + 1] if previous_stage != EXACT_STAGE_GAMES_V1[-1] else previous_stage):
            raise NativeMetaOverfitAlternatingError("journal next state stage is not consecutive")

    def _record_state(self, state: CandidateStateV1) -> None:
        object.__setattr__(self, "state_binding", _ImmutableDict({
            **_state_binding_payload(state),
            "native_baseline": _ImmutableDict(state.native_baseline.to_dict()),
        }))

    def _bind(self, *, state: CandidateStateV1, summary: EvaluationSummaryV1, decision: Mapping[str, object]) -> None:
        if state.candidate_id != self.candidate_id:
            raise NativeMetaOverfitAlternatingError("journal candidate_id differs from state")
        summary = _coerce_summary(summary)
        _validate_summary_state_identity(summary, state)
        if _state_sha256(state) == self.state_sha256:
            self._validate_current_state(state)
        else:
            self._validate_next_state(state)
        if not isinstance(decision, Mapping):
            raise NativeMetaOverfitAlternatingError("journal decision must be a mapping")
        expected_count = (
            self.consecutive_native_regressions + 1
            if summary.candidate_score < summary.native_score
            else 0
        )
        supplied_count = decision.get("consecutive_native_regressions")
        if supplied_count != expected_count:
            raise NativeMetaOverfitAlternatingError(
                "journal decision regression count does not follow prior lineage"
            )
        expected_stop = expected_count >= NATIVE_REGRESSION_STOP_AFTER_V1
        if decision.get("stop_after_two") is not expected_stop:
            raise NativeMetaOverfitAlternatingError("journal decision stop flag is inconsistent")
        if decision.get("rollback_required") is not expected_stop:
            raise NativeMetaOverfitAlternatingError("journal decision rollback flag is inconsistent")
        if decision.get("candidate_id") != state.candidate_id:
            raise NativeMetaOverfitAlternatingError("journal decision candidate_id differs from state")
        if decision.get("native_pair_id") != state.native_baseline.pair_id:
            raise NativeMetaOverfitAlternatingError("journal decision native_pair_id differs from state")
        if decision.get("stage_games") != summary.stage_games:
            raise NativeMetaOverfitAlternatingError("journal decision stage differs from summary")
        if decision.get("candidate_score") != summary.candidate_score or decision.get("native_score") != summary.native_score:
            raise NativeMetaOverfitAlternatingError("journal decision score differs from summary")
        if decision.get("candidate_delta") != summary.candidate_delta:
            raise NativeMetaOverfitAlternatingError("journal decision delta differs from summary")
        if decision.get("protocol_sha256") != summary.protocol_sha256:
            raise NativeMetaOverfitAlternatingError("journal decision protocol differs from summary")
        if decision.get("evaluation_summary_sha256") != _evaluation_summary_sha_v1(summary):
            raise NativeMetaOverfitAlternatingError("journal decision summary SHA differs from summary")
        if decision.get("native_control_artifact_sha256") != summary.native_control_artifact_sha256:
            raise NativeMetaOverfitAlternatingError("journal decision native artifact differs from summary")
        if decision.get("native_control_block_sha256") != summary.native_control_block_sha256:
            raise NativeMetaOverfitAlternatingError("journal decision native block differs from summary")
        object.__setattr__(self, "state_sha256", _state_sha256(state))
        object.__setattr__(self, "consecutive_native_regressions", expected_count)
        object.__setattr__(self, "last_summary_sha256", _summary_sha(summary))
        object.__setattr__(self, "last_decision_sha256", _semantic_sha(
            "mage-ptcg:native-meta-overfit-decision:v1", dict(decision)
        ))
        self._record_state(state)
        object.__setattr__(self, "_seal_sha256", self._compute_content_sha256())

    def rebind_state(self, *, state: CandidateStateV1) -> None:
        """Carry the journal lineage across a non-evaluating state transition."""

        self._validate_next_state(state)
        object.__setattr__(self, "state_sha256", _state_sha256(state))
        self._record_state(state)
        object.__setattr__(self, "_seal_sha256", self._compute_content_sha256())


def build_native_regression_journal_v1(state: CandidateStateV1) -> NativeRegressionJournalV1:
    if type(state) is not CandidateStateV1:
        raise NativeMetaOverfitAlternatingError("journal state must be a CandidateStateV1")
    return NativeRegressionJournalV1(
        candidate_id=state.candidate_id,
        state_sha256=_state_sha256(state),
        state_binding=_state_binding_payload(state),
    )


def _validate_regression_journal(
    journal: NativeRegressionJournalV1, state: CandidateStateV1
) -> None:
    if type(journal) is not NativeRegressionJournalV1:
        raise NativeMetaOverfitAlternatingError("regression journal is required and must be bound")
    try:
        # Re-run constructor validation on the exact serialized content.  This
        # catches low-level mutation of nested authority mappings even though
        # normal attribute assignment is frozen.
        NativeRegressionJournalV1.from_dict(journal.to_dict())
    except NativeMetaOverfitAlternatingError as exc:
        raise NativeMetaOverfitAlternatingError("regression journal content is invalid") from exc
    if journal.content_sha256 != journal._compute_content_sha256():
        raise NativeMetaOverfitAlternatingError("regression journal content seal mismatch")
    if journal.candidate_id != state.candidate_id:
        raise NativeMetaOverfitAlternatingError("regression journal candidate_id differs from state")
    if journal.state_sha256 != _state_sha256(state):
        raise NativeMetaOverfitAlternatingError("regression journal state SHA mismatch")


def build_native_meta_overfit_state_v1(
    *,
    iteration_manifest_path: str | Path,
    meta_schedule_path: str | Path,
    candidate_id: str,
    deck_sha256: str,
    policy_config_sha256: str,
    native_baseline: NativeBaselineArmV1,
    phase: str = POLICY_FIXED_SHORT_V1,
    deck_mutation_candidate: DeckMutationCandidateV1 | None = None,
    public_advantage_table_path: str | Path | None = None,
) -> CandidateStateV1:
    """Construct one native-bound state without starting the optimizer runner."""

    if type(native_baseline) is not NativeBaselineArmV1:
        raise NativeMetaOverfitAlternatingError("native_baseline is mandatory")
    if phase not in PHASES_V1:
        raise NativeMetaOverfitAlternatingError(f"unsupported candidate phase: {phase}")
    _sha(deck_sha256, "deck_sha256")
    _sha(policy_config_sha256, "policy_config_sha256")
    binding = _iteration_binding(
        iteration_manifest_path,
        public_advantage_table_path=public_advantage_table_path,
    )
    _bind_baseline(binding["native_baseline"], native_baseline)
    schedule = Path(meta_schedule_path).resolve()
    if not schedule.is_file():
        raise NativeMetaOverfitAlternatingError(f"meta schedule is missing: {schedule}")
    if deck_mutation_candidate is not None:
        if type(deck_mutation_candidate) is not DeckMutationCandidateV1:
            raise NativeMetaOverfitAlternatingError("deck_mutation_candidate must be a DeckMutationCandidateV1")
        if deck_mutation_candidate.authority != DeckMutationAuthorityV1():
            raise NativeMetaOverfitAlternatingError("deck mutation candidate authority grants permission")
        if deck_mutation_candidate.deck_multiset_sha256 != deck_sha256:
            raise NativeMetaOverfitAlternatingError("deck mutation candidate deck SHA differs from state")
    try:
        return CandidateStateV1(
            schema_version=ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
            candidate_id=_text(candidate_id, "candidate_id"),
            parent_candidate_id=None,
            phase=phase,
            deck_sha256=deck_sha256,
            policy_config_sha256=policy_config_sha256,
            meta_manifest_sha256=binding["file_sha256"],
            meta_schedule_sha256=_sha_file(schedule),
            stage_games=EXACT_STAGE_GAMES_V1[0],
            native_baseline=native_baseline,
            authority=ResearchAuthorityV1(),
        )
    except AlternatingMetaOptimizerError as exc:
        raise NativeMetaOverfitAlternatingError(str(exc)) from exc


initialize_native_meta_overfit_state_v1 = build_native_meta_overfit_state_v1


def advance_native_meta_overfit_state_v1(
    state: CandidateStateV1,
    *,
    iteration_manifest_path: str | Path,
    meta_schedule_path: str | Path,
    phase: str | None = None,
    deck_sha256: str | None = None,
    policy_config_sha256: str | None = None,
    evaluation_summary: EvaluationSummaryV1 | Mapping[str, object] | None = None,
    next_stage_games: int | None = None,
    public_advantage_table_path: str | Path | None = None,
    regression_journal: NativeRegressionJournalV1 | None = None,
) -> CandidateStateV1:
    """Use the existing optimizer transition after strict bridge validation."""

    if regression_journal is None:
        raise NativeMetaOverfitAlternatingError("regression journal is required for every state transition")
    _validate_regression_journal(regression_journal, state)

    _validate_state_binding(
        state,
        iteration_manifest_path=iteration_manifest_path,
        meta_schedule_path=meta_schedule_path,
        public_advantage_table_path=public_advantage_table_path,
    )
    if evaluation_summary is not None:
        summary_item = _coerce_summary(evaluation_summary)
        previous_regressions = regression_journal.consecutive_native_regressions
        decision = evaluate_native_meta_overfit_stage_v1(
            summary_item,
            state,
            iteration_manifest_path=iteration_manifest_path,
            previous_native_regressions=previous_regressions,
            public_advantage_table_path=public_advantage_table_path,
        )
        if decision["stop_after_two"]:
            regression_journal._bind(state=state, summary=summary_item, decision=decision)
            raise NativeMetaOverfitAlternatingError("native regression stop-after-two requires rollback")
    try:
        result = advance_candidate_state_v1(
            state,
            phase=phase,
            deck_sha256=deck_sha256,
            policy_config_sha256=policy_config_sha256,
            next_stage_games=next_stage_games,
            candidate_score=(
                summary_item.candidate_score
                if evaluation_summary is not None and next_stage_games is not None
                else None
            ),
            native_score=(
                summary_item.native_score
                if evaluation_summary is not None and next_stage_games is not None
                else None
            ),
        )
    except AlternatingMetaOptimizerError as exc:
        raise NativeMetaOverfitAlternatingError(str(exc)) from exc
    if evaluation_summary is not None:
        regression_journal._bind(state=state, summary=summary_item, decision=decision)
        regression_journal.rebind_state(state=result)
    else:
        regression_journal.rebind_state(state=result)
    return result


def evaluate_native_meta_overfit_stage_v1(
    summary: EvaluationSummaryV1 | Mapping[str, object],
    state: CandidateStateV1,
    *,
    iteration_manifest_path: str | Path,
    previous_native_regressions: int = 0,
    max_seat_gap: float = DEFAULT_MAX_SEAT_GAP_V1,
    public_advantage_table_path: str | Path | None = None,
) -> dict[str, object]:
    """Validate one candidate/native stage and return a deterministic decision."""

    binding = _validate_state_binding(
        state,
        iteration_manifest_path=iteration_manifest_path,
        public_advantage_table_path=public_advantage_table_path,
    )
    item = _coerce_summary(summary)
    if item.candidate_id != state.candidate_id:
        raise NativeMetaOverfitAlternatingError("evaluation candidate_id differs from state")
    if item.native_pair_id != state.native_baseline.pair_id:
        raise NativeMetaOverfitAlternatingError("evaluation native_pair_id differs from native baseline")
    if item.candidate_policy_sha256 != state.policy_config_sha256:
        raise NativeMetaOverfitAlternatingError("evaluation candidate policy SHA differs from state")
    if item.candidate_deck_sha256 != state.deck_sha256:
        raise NativeMetaOverfitAlternatingError("evaluation candidate deck SHA differs from state")
    if item.candidate_evaluator_sha256 != state.native_baseline.evaluator_sha256:
        raise NativeMetaOverfitAlternatingError("evaluation candidate evaluator SHA differs from state")
    if item.native_policy_sha256 != state.native_baseline.policy_sha256:
        raise NativeMetaOverfitAlternatingError("evaluation native policy SHA differs from baseline")
    if item.native_deck_sha256 != state.native_baseline.deck_sha256:
        raise NativeMetaOverfitAlternatingError("evaluation native deck SHA differs from baseline")
    if item.native_evaluator_sha256 != state.native_baseline.evaluator_sha256:
        raise NativeMetaOverfitAlternatingError("evaluation native evaluator SHA differs from baseline")
    if item.stage_games != state.stage_games:
        raise NativeMetaOverfitAlternatingError("evaluation stage_games differs from state")
    if item.protocol_sha256 != binding["protocol_sha256"]:
        raise NativeMetaOverfitAlternatingError("evaluation protocol SHA differs from iteration")
    if type(previous_native_regressions) is not int or previous_native_regressions < 0:
        raise NativeMetaOverfitAlternatingError("previous_native_regressions must be nonnegative")
    if type(max_seat_gap) not in (int, float) or isinstance(max_seat_gap, bool) or not 0.0 <= float(max_seat_gap) <= 1.0:
        raise NativeMetaOverfitAlternatingError("max_seat_gap must be finite in [0,1]")
    if item.candidate_fault_count or item.native_fault_count:
        raise NativeMetaOverfitAlternatingError("fault gate failed: candidate/native fault count is nonzero")
    candidate_seat_ok = item.candidate_seat_gap <= float(max_seat_gap)
    native_seat_ok = item.native_seat_gap <= float(max_seat_gap)
    if not candidate_seat_ok or not native_seat_ok:
        raise NativeMetaOverfitAlternatingError("seat gate failed: candidate/native seat gap exceeds threshold")
    consecutive = previous_native_regressions + 1 if item.candidate_score < item.native_score else 0
    stop = consecutive >= NATIVE_REGRESSION_STOP_AFTER_V1
    summary_sha = hashlib.sha256(
        b"mage-ptcg:native-meta-overfit-evaluation-summary:v1\0" + _canonical_bytes(item.to_dict())
    ).hexdigest()
    next_stage = None
    if state.stage_games != EXACT_STAGE_GAMES_V1[-1]:
        next_stage = EXACT_STAGE_GAMES_V1[EXACT_STAGE_GAMES_V1.index(state.stage_games) + 1]
    return {
        "schema_version": BRIDGE_SCHEMA_V1,
        "candidate_id": state.candidate_id,
        "native_pair_id": state.native_baseline.pair_id,
        "stage_games": state.stage_games,
        "candidate_score": item.candidate_score,
        "native_score": item.native_score,
        "candidate_delta": item.candidate_delta,
        "candidate_seat_gap": item.candidate_seat_gap,
        "native_seat_gap": item.native_seat_gap,
        "fault_gate": True,
        "seat_gate": True,
        "candidate_native_pair_bound": True,
        "stage_gate_passed": True,
        "next_stage_games": next_stage,
        "consecutive_native_regressions": consecutive,
        "stop_after_two": stop,
        "rollback_required": stop,
        "evaluation_summary_sha256": summary_sha,
        "iteration_manifest_sha256": binding["file_sha256"],
        "public_advantage_table_sha256": binding["table_sha256"],
        "native_control_artifact_sha256": item.native_control_artifact_sha256,
        "native_control_block_sha256": item.native_control_block_sha256,
        "protocol_sha256": binding["protocol_sha256"],
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }


def promote_native_meta_overfit_successive_halving_v1(
    states: Sequence[CandidateStateV1],
    summaries: Mapping[str, EvaluationSummaryV1 | Mapping[str, object]],
    *,
    iteration_manifest_path: str | Path,
    next_stage_games: int,
    public_advantage_table_path: str | Path | None = None,
    regression_journals: Mapping[str, NativeRegressionJournalV1] | None = None,
) -> tuple[CandidateStateV1, ...]:
    """Gate every pair, then delegate ranking/advancement to the existing optimizer."""

    values = tuple(states)
    if not values:
        raise NativeMetaOverfitAlternatingError("successive-halving requires candidate/native pairs")
    if set(summaries) != {state.candidate_id for state in values}:
        raise NativeMetaOverfitAlternatingError("evaluation summary mapping must cover every candidate")
    if regression_journals is None:
        raise NativeMetaOverfitAlternatingError("regression journals are required for every promotion")
    if set(regression_journals) != {state.candidate_id for state in values}:
        raise NativeMetaOverfitAlternatingError("regression journal mapping must cover every candidate")
    scores: dict[str, float] = {}
    native_control_identity: tuple[object, ...] | None = None
    for state in values:
        summary = _coerce_summary(summaries[state.candidate_id])
        current_control_identity = _native_control_identity(summary)
        if native_control_identity is None:
            native_control_identity = current_control_identity
        elif current_control_identity != native_control_identity:
            raise NativeMetaOverfitAlternatingError(
                "native control block differs between successive-halving candidates"
            )
        journal = regression_journals[state.candidate_id]
        _validate_regression_journal(journal, state)
        previous_regressions = journal.consecutive_native_regressions
        decision = evaluate_native_meta_overfit_stage_v1(
            summary,
            state,
            iteration_manifest_path=iteration_manifest_path,
            previous_native_regressions=previous_regressions,
            public_advantage_table_path=public_advantage_table_path,
        )
        if decision["stop_after_two"]:
            journal._bind(state=state, summary=summary, decision=decision)
            raise NativeMetaOverfitAlternatingError("native regression stop-after-two requires rollback")
        journal._bind(state=state, summary=summary, decision=decision)
        scores[state.candidate_id] = summary.candidate_score
    if native_control_identity is None:
        raise NativeMetaOverfitAlternatingError("native control identity is missing")
    baseline_score = float(native_control_identity[7])
    try:
        return promote_successive_halving_v1(
            values,
            scores,
            native_baseline_score=baseline_score,
            next_stage_games=next_stage_games,
        )
    except AlternatingMetaOptimizerError as exc:
        raise NativeMetaOverfitAlternatingError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class RollbackDescriptorV1:
    """Content-addressed rollback target for a native-regression safety stop."""

    schema_version: str
    candidate_id: str
    iteration_manifest_sha256: str
    state_sha256: str
    checkpoint_path: str
    checkpoint_sha256: str
    reason: str
    consecutive_native_regressions: int
    rollback_required: bool
    authority: Mapping[str, bool]
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != BRIDGE_SCHEMA_V1:
            raise NativeMetaOverfitAlternatingError("rollback descriptor schema is invalid")
        _text(self.candidate_id, "rollback candidate_id")
        _sha(self.iteration_manifest_sha256, "rollback iteration manifest SHA")
        _sha(self.state_sha256, "rollback state SHA")
        _text(self.checkpoint_path, "rollback checkpoint_path")
        _sha(self.checkpoint_sha256, "rollback checkpoint SHA")
        if self.reason != "two native regressions":
            raise NativeMetaOverfitAlternatingError("rollback reason must be two native regressions")
        if type(self.consecutive_native_regressions) is not int or self.consecutive_native_regressions < NATIVE_REGRESSION_STOP_AFTER_V1:
            raise NativeMetaOverfitAlternatingError("rollback regression count must be at least two")
        if type(self.rollback_required) is not bool or self.rollback_required is not True:
            raise NativeMetaOverfitAlternatingError("rollback descriptor must require rollback")
        if dict(self.authority) != AUTHORITY_FALSE_V1 or self.research_only is not True:
            raise NativeMetaOverfitAlternatingError("rollback descriptor authority is invalid")
        object.__setattr__(self, "authority", _ImmutableDict(AUTHORITY_FALSE_V1))

    def to_dict(self) -> dict[str, object]:
        return asdict(self) | {"authority": dict(self.authority)}


def build_rollback_descriptor_v1(
    *,
    state: CandidateStateV1,
    checkpoint_path: str | Path,
    iteration_manifest_path: str | Path,
    reason: str,
    consecutive_native_regressions: int,
) -> dict[str, object]:
    """Build a rollback descriptor without changing optimizer journal state."""

    binding = _validate_state_binding(state, iteration_manifest_path=iteration_manifest_path)
    checkpoint = Path(checkpoint_path).resolve()
    if not checkpoint.is_file():
        raise NativeMetaOverfitAlternatingError(f"checkpoint is missing: {checkpoint}")
    descriptor = RollbackDescriptorV1(
        schema_version=BRIDGE_SCHEMA_V1,
        candidate_id=state.candidate_id,
        iteration_manifest_sha256=binding["file_sha256"],
        state_sha256=_state_sha256(state),
        checkpoint_path=str(checkpoint),
        checkpoint_sha256=_sha_file(checkpoint),
        reason=reason,
        consecutive_native_regressions=consecutive_native_regressions,
        rollback_required=True,
        authority=dict(AUTHORITY_FALSE_V1),
    )
    return descriptor.to_dict()


def verify_rollback_descriptor_v1(
    descriptor: RollbackDescriptorV1 | Mapping[str, object],
    *,
    state: CandidateStateV1,
    checkpoint_path: str | Path | None = None,
    iteration_manifest_path: str | Path,
) -> dict[str, object]:
    """Re-hash every rollback binding and reject any mismatch."""

    if isinstance(descriptor, RollbackDescriptorV1):
        try:
            # Reconstruct from serialized content to catch nested authority
            # mutation through a low-level dict operation.
            item = RollbackDescriptorV1(**descriptor.to_dict())
        except (TypeError, NativeMetaOverfitAlternatingError) as exc:
            if isinstance(exc, NativeMetaOverfitAlternatingError):
                raise
            raise NativeMetaOverfitAlternatingError("rollback descriptor is malformed") from exc
    elif isinstance(descriptor, Mapping):
        try:
            item = RollbackDescriptorV1(
                schema_version=str(descriptor.get("schema_version", "")),
                candidate_id=str(descriptor.get("candidate_id", "")),
                iteration_manifest_sha256=str(descriptor.get("iteration_manifest_sha256", "")),
                state_sha256=str(descriptor.get("state_sha256", "")),
                checkpoint_path=str(descriptor.get("checkpoint_path", "")),
                checkpoint_sha256=str(descriptor.get("checkpoint_sha256", "")),
                reason=str(descriptor.get("reason", "")),
                consecutive_native_regressions=descriptor.get("consecutive_native_regressions", -1),
                rollback_required=descriptor.get("rollback_required", False),
                authority=descriptor.get("authority", {}),
                research_only=descriptor.get("research_only", False),
            )
        except (TypeError, NativeMetaOverfitAlternatingError) as exc:
            if isinstance(exc, NativeMetaOverfitAlternatingError):
                raise
            raise NativeMetaOverfitAlternatingError("rollback descriptor is malformed") from exc
    else:
        raise NativeMetaOverfitAlternatingError("rollback descriptor has an unsupported type")
    binding = _validate_state_binding(state, iteration_manifest_path=iteration_manifest_path)
    if item.candidate_id != state.candidate_id:
        raise NativeMetaOverfitAlternatingError("rollback candidate_id differs from state")
    if item.iteration_manifest_sha256 != binding["file_sha256"]:
        raise NativeMetaOverfitAlternatingError("rollback iteration manifest SHA mismatch")
    if item.state_sha256 != _state_sha256(state):
        raise NativeMetaOverfitAlternatingError("rollback state SHA mismatch")
    checkpoint = Path(checkpoint_path).resolve() if checkpoint_path is not None else Path(item.checkpoint_path).resolve()
    if checkpoint != Path(item.checkpoint_path).resolve():
        raise NativeMetaOverfitAlternatingError("rollback checkpoint path mismatch")
    if not checkpoint.is_file() or _sha_file(checkpoint) != item.checkpoint_sha256:
        raise NativeMetaOverfitAlternatingError("rollback checkpoint SHA mismatch")
    return item.to_dict()


# Public deterministic helpers let a materializer construct the two required
# control hashes without copying their canonicalization rules.
derive_native_control_artifact_sha256 = _native_control_artifact_sha256
derive_native_control_block_sha256 = _native_control_block_sha256


__all__ = [
    "AUTHORITY_FALSE_V1",
    "BRIDGE_SCHEMA_V1",
    "DECK_FIXED_LONG_V1",
    "DEFAULT_MAX_SEAT_GAP_V1",
    "EXACT_STAGE_GAMES_V1",
    "EvaluationSummaryV1",
    "NATIVE_REGRESSION_STOP_AFTER_V1",
    "NativeBaselineArmV1",
    "NativeMetaOverfitAlternatingError",
    "NativeRegressionJournalV1",
    "POLICY_FIXED_SHORT_V1",
    "ResearchAuthorityV1",
    "RollbackDescriptorV1",
    "SUCCESSIVE_HALVING_GAMES_V1",
    "advance_native_meta_overfit_state_v1",
    "build_native_regression_journal_v1",
    "build_native_meta_overfit_state_v1",
    "build_rollback_descriptor_v1",
    "derive_native_control_artifact_sha256",
    "derive_native_control_block_sha256",
    "evaluate_native_meta_overfit_stage_v1",
    "initialize_native_meta_overfit_state_v1",
    "promote_native_meta_overfit_successive_halving_v1",
    "verify_rollback_descriptor_v1",
]
