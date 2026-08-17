"""Research-only common-pool evaluator for a Student v3 set candidate.

The candidate artifact closes the exact checkpoint, training summary, deck,
dataset, teacher catalog, and optional AWR target sidecar identities.  CABT is
run through the bounded parallel evaluator, with faults retained in the
requested-game denominator.  This module never changes ``main.py``, the
Champion, a package, or a submission.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.deck_io import read_deck_csv
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    OpponentInstanceV1,
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (
    EvaluationGameV1,
    _game_from_payload,
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.test_sim import run_match


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_V1 = "meta-specialist-student-v3-candidate-artifact-v2"
POLICY_IDENTITY_SCHEMA_V1 = "meta-specialist-student-v3-policy-identity-v1"
EVALUATION_SCHEMA_V1 = "meta-specialist-student-v3-set-candidate-pilot-v1"
RUNTIME_TELEMETRY_SCHEMA_V1 = "student-v3-set-runtime-telemetry-v1"
RUNNER_REF_V1 = (
    "scripts.run_student_v3_set_candidate_pilot_v1:run_student_v3_candidate_game_v1"
)
DEFAULT_RUNTIME_TELEMETRY_ROOT_V1 = (
    ROOT / "runs" / "student-v3-set-candidate-runtime-telemetry-v1"
)
_HEX = frozenset("0123456789abcdef")
_ARTIFACT_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "model_dir",
        "checkpoint_path",
        "checkpoint_sha256",
        "training_summary_path",
        "training_summary_sha256",
        "deck_path",
        "deck_sha256",
        "dataset_manifest_path",
        "dataset_manifest_sha256",
        "bridge_manifest_path",
        "bridge_manifest_sha256",
        "bridge_sha256",
        "teacher_catalog_sha256",
        "target_sidecar_sha256",
        "objective_kind",
        "purpose",
        "submission_deck_qualification_path",
        "submission_deck_qualification_file_sha256",
        "submission_deck_qualification_sha256",
        "qualified_deck_identity",
        "performance_evidence",
        "research_only",
        "training_authority",
        "promotion_authority",
        "submission_authority",
        "longrun_authority",
    }
)
_RUNTIME_TELEMETRY_FIELDS_V1 = frozenset(
    {
        "schema_version",
        "game_id",
        "candidate_id",
        "candidate_artifact_sha256",
        "policy_identity_sha256",
        "runtime_closure_sha256",
        "seat",
        "match_status",
        "selection_decision_count",
        "model_decision_count",
        "fallback_count",
        "fallback_reason_counts",
        "fallback_policy",
        "performance_evidence",
        "research_only",
        "training_authority",
        "promotion_authority",
        "submission_authority",
        "longrun_authority",
        "telemetry_sha256",
    }
)
_MODEL_CACHE: dict[str, tuple[Any, Mapping[str, object]]] = {}


class StudentV3CandidatePilotError(ValueError):
    """Raised when a Student v3 evaluation artifact is not closed."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry is not canonical JSON: {exc}"
        ) from exc


def _runtime_telemetry_sha256_v1(payload: Mapping[str, object]) -> str:
    body = dict(payload)
    body.pop("telemetry_sha256", None)
    return hashlib.sha256(
        RUNTIME_TELEMETRY_SCHEMA_V1.encode("ascii")
        + b"\0"
        + _canonical_json_bytes(body)
    ).hexdigest()


def _runtime_telemetry_path_v1(root: str | Path, game_id: str) -> Path:
    digest = hashlib.sha256(game_id.encode("utf-8")).hexdigest()
    return Path(root).resolve() / f"{digest}.json"


def _require_runtime_telemetry_path_v1(value: object, game_id: str) -> Path:
    raw = _text(value, "student_v3_runtime_telemetry_path")
    path = Path(raw)
    if not path.is_absolute() or str(path.resolve()) != raw:
        raise StudentV3CandidatePilotError(
            "student_v3_runtime_telemetry_path must be canonical and absolute"
        )
    expected_name = f"{hashlib.sha256(game_id.encode('utf-8')).hexdigest()}.json"
    if path.name != expected_name:
        raise StudentV3CandidatePilotError(
            "student_v3_runtime_telemetry_path is not bound to game_id"
        )
    return path


def _sha256(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise StudentV3CandidatePilotError(f"{name} must be a lowercase SHA-256")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise StudentV3CandidatePilotError(f"{name} must be a non-empty string")
    return value


def _false(value: object, name: str) -> bool:
    if value is not False:
        raise StudentV3CandidatePilotError(f"{name} must be false")
    return False


def _true(value: object, name: str) -> bool:
    if value is not True:
        raise StudentV3CandidatePilotError(f"{name} must be true")
    return True


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise StudentV3CandidatePilotError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _resolved_file(value: object, name: str, *, parent: Path) -> Path:
    raw = Path(_text(value, name))
    path = (raw if raw.is_absolute() else parent / raw).resolve()
    if not path.is_file():
        raise StudentV3CandidatePilotError(f"{name} is not a file: {path}")
    return path


def _resolved_repo_file(value: object, name: str) -> tuple[Path, str]:
    relative = _text(value, name)
    if Path(relative).is_absolute():
        raise StudentV3CandidatePilotError(
            f"{name} must be a canonical repository-relative path"
        )
    path = (ROOT / relative).resolve()
    try:
        canonical = str(path.relative_to(ROOT))
    except ValueError as exc:
        raise StudentV3CandidatePilotError(f"{name} escapes repository root") from exc
    if canonical != relative or not path.is_file():
        raise StudentV3CandidatePilotError(
            f"{name} is not a canonical repository file: {relative}"
        )
    return path, relative


@dataclass(frozen=True, slots=True)
class StudentV3CandidateArtifactV1:
    candidate_id: str
    model_dir: Path
    checkpoint_path: Path
    checkpoint_sha256: str
    training_summary_path: Path
    training_summary_sha256: str
    deck_path: Path
    deck_sha256: str
    dataset_manifest_path: Path
    dataset_manifest_sha256: str
    bridge_manifest_path: Path
    bridge_manifest_sha256: str
    bridge_sha256: str
    teacher_catalog_sha256: str
    target_sidecar_sha256: str | None
    objective_kind: str
    purpose: str
    submission_deck_qualification_path: Path
    submission_deck_qualification_repo_path: str
    submission_deck_qualification_file_sha256: str
    submission_deck_qualification_sha256: str
    qualified_deck_identity: str
    runtime_closure: Mapping[str, object]
    performance_evidence: bool
    research_only: bool
    training_authority: bool
    promotion_authority: bool
    submission_authority: bool
    longrun_authority: bool
    artifact_path: Path
    artifact_sha256: str

    @property
    def policy_identity_sha256(self) -> str:
        payload = {
            "checkpoint_sha256": self.checkpoint_sha256,
            "training_summary_sha256": self.training_summary_sha256,
            "runtime_closure": self.runtime_closure,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "bridge_manifest_sha256": self.bridge_manifest_sha256,
            "bridge_sha256": self.bridge_sha256,
            "teacher_catalog_sha256": self.teacher_catalog_sha256,
            "target_sidecar_sha256": self.target_sidecar_sha256,
            "objective_kind": self.objective_kind,
            "purpose": self.purpose,
            "submission_deck_qualification_file_sha256": (
                self.submission_deck_qualification_file_sha256
            ),
            "submission_deck_qualification_path": (
                self.submission_deck_qualification_repo_path
            ),
            "submission_deck_qualification_sha256": (
                self.submission_deck_qualification_sha256
            ),
            "qualified_deck_identity": self.qualified_deck_identity,
            "deck_sha256": self.deck_sha256,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(
            POLICY_IDENTITY_SCHEMA_V1.encode("ascii") + b"\0" + canonical
        ).hexdigest()


def _load_training_summary_v1(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                StudentV3CandidatePilotError(
                    f"non-finite training summary value: {value}"
                )
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudentV3CandidatePilotError(
            f"could not read training summary: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise StudentV3CandidatePilotError("training summary is not a mapping")
    return payload


def _objective_kind(value: object) -> str:
    if value not in {"THETA0_PRETRAIN", "AWR_FINE_TUNE"}:
        raise StudentV3CandidatePilotError(
            "objective_kind must be THETA0_PRETRAIN or AWR_FINE_TUNE"
        )
    return str(value)


def _require_summary_authority_false(summary: Mapping[str, object]) -> None:
    authority = summary.get("authority")
    expected = {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    if authority != expected:
        raise StudentV3CandidatePilotError(
            "training summary authority must remain exactly false"
        )


def _summary_bindings(
    candidate: StudentV3CandidateArtifactV1,
) -> dict[str, object]:
    return {
        "dataset_manifest_sha256": candidate.dataset_manifest_sha256,
        "catalog_sha256": candidate.teacher_catalog_sha256,
        "weight_sidecar_sha256": candidate.target_sidecar_sha256,
        "objective_kind": candidate.objective_kind,
        "purpose": candidate.purpose,
        "best_checkpoint_sha256": candidate.checkpoint_sha256,
    }


def _require_runtime_summary_bindings_v1(
    candidate: StudentV3CandidateArtifactV1,
    summary: Mapping[str, object],
) -> int:
    for field, expected in _summary_bindings(candidate).items():
        if summary.get(field) != expected:
            raise StudentV3CandidatePilotError(
                f"runtime summary {field} does not match candidate artifact"
            )
    _require_summary_authority_false(summary)
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetRuntimeError,
        required_max_count_from_summary,
    )

    try:
        return required_max_count_from_summary(summary)
    except StudentV3SetRuntimeError as exc:
        raise StudentV3CandidatePilotError(str(exc)) from exc


def _load_mapping_json_v1(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(
                StudentV3CandidatePilotError(
                    f"non-finite {label} value: {item}"
                )
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudentV3CandidatePilotError(f"{label} is unreadable") from exc
    if not isinstance(value, Mapping):
        raise StudentV3CandidatePilotError(f"{label} is not a mapping")
    return value


def _atomic_candidate_new_v1(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_student_v3_candidate_artifact_v1(
    *,
    candidate_id: str,
    model_dir: str | Path,
    dataset_manifest_path: str | Path,
    submission_deck_qualification_path: str | Path,
    output_path: str | Path,
) -> StudentV3CandidateArtifactV1:
    """Build one immutable candidate identity from formally verified inputs."""
    candidate_name = _text(candidate_id, "candidate_id")
    directory = Path(model_dir).resolve()
    if not directory.is_dir():
        raise StudentV3CandidatePilotError("model_dir is not a directory")
    checkpoint = directory / "best.pt"
    summary_path = directory / "training_summary.json"
    if not checkpoint.is_file() or not summary_path.is_file():
        raise StudentV3CandidatePilotError(
            "model_dir must contain best.pt and training_summary.json"
        )
    dataset_path = Path(dataset_manifest_path).resolve()
    if not dataset_path.is_file():
        raise StudentV3CandidatePilotError("dataset_manifest_path is not a file")
    dataset = _load_mapping_json_v1(dataset_path, label="dataset manifest")
    from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
        GPUStudentV3SetError,
        _verify_dataset_manifest,
    )

    try:
        _verify_dataset_manifest(dataset_path.parent, dict(dataset))
    except (GPUStudentV3SetError, OSError, ValueError) as exc:
        raise StudentV3CandidatePilotError(
            f"formal GPU dataset verification failed: {exc}"
        ) from exc
    if dataset.get("synthetic_test_only") is not False:
        raise StudentV3CandidatePilotError(
            "candidate artifact cannot use a synthetic-only dataset"
        )
    bridge_value = dataset.get("bridge_manifest_path")
    if type(bridge_value) is not str or not bridge_value:
        raise StudentV3CandidatePilotError(
            "dataset manifest has no formal bridge path"
        )
    bridge_path = Path(bridge_value).resolve()
    if not bridge_path.is_file():
        raise StudentV3CandidatePilotError("formal bridge manifest is missing")
    qualification_path = Path(submission_deck_qualification_path).resolve()
    try:
        qualification_relative = str(qualification_path.relative_to(ROOT))
    except ValueError as exc:
        raise StudentV3CandidatePilotError(
            "deck qualification must remain inside the repository"
        ) from exc
    from mage_ptcg.meta_specialist.submission_deck_qualification_v1 import (
        SubmissionDeckQualificationV1Error,
        verify_submission_deck_qualification_v1,
    )

    try:
        qualification, qualified_deck = verify_submission_deck_qualification_v1(
            qualification_path, ROOT
        )
    except (SubmissionDeckQualificationV1Error, OSError, ValueError) as exc:
        raise StudentV3CandidatePilotError(
            f"formal deck qualification verification failed: {exc}"
        ) from exc
    summary = _load_training_summary_v1(summary_path)
    sidecar = summary.get("weight_sidecar_sha256")
    if sidecar is not None:
        _sha256(sidecar, "weight_sidecar_sha256")
    payload: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "candidate_id": candidate_name,
        "model_dir": str(directory),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _sha256_file(checkpoint),
        "training_summary_path": str(summary_path),
        "training_summary_sha256": _sha256_file(summary_path),
        "deck_path": str((ROOT / str(qualification["deck_path"])).resolve()),
        "deck_sha256": qualified_deck.deck_file_sha256,
        "dataset_manifest_path": str(dataset_path),
        "dataset_manifest_sha256": _sha256_file(dataset_path),
        "bridge_manifest_path": str(bridge_path),
        "bridge_manifest_sha256": _sha256_file(bridge_path),
        "bridge_sha256": dataset.get("bridge_sha256"),
        "teacher_catalog_sha256": dataset.get("catalog_sha256"),
        "target_sidecar_sha256": sidecar,
        "objective_kind": summary.get("objective_kind"),
        "purpose": summary.get("purpose"),
        "submission_deck_qualification_path": qualification_relative,
        "submission_deck_qualification_file_sha256": _sha256_file(
            qualification_path
        ),
        "submission_deck_qualification_sha256": qualification.get(
            "qualification_sha256"
        ),
        "qualified_deck_identity": qualified_deck.deck_identity,
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    output = Path(output_path).resolve()
    try:
        _atomic_candidate_new_v1(output, payload)
        return load_student_v3_candidate_artifact_v1(output)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def load_student_v3_candidate_artifact_v1(
    path: str | Path,
) -> StudentV3CandidateArtifactV1:
    artifact_path = Path(path).resolve()
    if not artifact_path.is_file():
        raise StudentV3CandidatePilotError(f"candidate artifact is not a file: {artifact_path}")
    try:
        payload = json.loads(
            artifact_path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudentV3CandidatePilotError(f"could not read candidate artifact: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _ARTIFACT_FIELDS:
        keys = set(payload) if isinstance(payload, dict) else set()
        raise StudentV3CandidatePilotError(
            f"candidate artifact keys are not closed: missing={sorted(_ARTIFACT_FIELDS - keys)}, "
            f"extra={sorted(keys - _ARTIFACT_FIELDS)}"
        )
    if payload["schema_version"] != SCHEMA_V1:
        raise StudentV3CandidatePilotError("unsupported candidate artifact schema_version")
    parent = artifact_path.parent
    model_dir_raw = Path(_text(payload["model_dir"], "model_dir"))
    model_dir = (model_dir_raw if model_dir_raw.is_absolute() else parent / model_dir_raw).resolve()
    if not model_dir.is_dir():
        raise StudentV3CandidatePilotError(f"model_dir is not a directory: {model_dir}")
    checkpoint = _resolved_file(payload["checkpoint_path"], "checkpoint_path", parent=parent)
    summary = _resolved_file(payload["training_summary_path"], "training_summary_path", parent=parent)
    deck = _resolved_file(payload["deck_path"], "deck_path", parent=parent)
    dataset_manifest = _resolved_file(
        payload["dataset_manifest_path"], "dataset_manifest_path", parent=parent
    )
    bridge_manifest = _resolved_file(
        payload["bridge_manifest_path"], "bridge_manifest_path", parent=parent
    )
    qualification_path, qualification_repo_path = _resolved_repo_file(
        payload["submission_deck_qualification_path"],
        "submission_deck_qualification_path",
    )
    if checkpoint != (model_dir / "best.pt").resolve():
        raise StudentV3CandidatePilotError(
            "checkpoint_path must be exactly model_dir/best.pt"
        )
    if summary != (model_dir / "training_summary.json").resolve():
        raise StudentV3CandidatePilotError(
            "training_summary_path must be exactly model_dir/training_summary.json"
        )
    checkpoint_sha = _sha256(payload["checkpoint_sha256"], "checkpoint_sha256")
    summary_sha = _sha256(payload["training_summary_sha256"], "training_summary_sha256")
    deck_sha = _sha256(payload["deck_sha256"], "deck_sha256")
    if _sha256_file(checkpoint) != checkpoint_sha:
        raise StudentV3CandidatePilotError("checkpoint SHA mismatch")
    if _sha256_file(summary) != summary_sha:
        raise StudentV3CandidatePilotError("training summary SHA mismatch")
    if _sha256_file(deck) != deck_sha:
        raise StudentV3CandidatePilotError("deck SHA mismatch")
    # Deck parsing is part of the artifact gate, not deferred to a worker.
    read_deck_csv(deck)
    sidecar_raw = payload["target_sidecar_sha256"]
    sidecar_sha = None if sidecar_raw is None else _sha256(sidecar_raw, "target_sidecar_sha256")
    objective_kind = _objective_kind(payload["objective_kind"])
    if (objective_kind == "THETA0_PRETRAIN") != (sidecar_sha is None):
        raise StudentV3CandidatePilotError(
            "objective_kind and target_sidecar_sha256 are inconsistent"
        )
    purpose = _text(payload["purpose"], "purpose")
    dataset_sha = _sha256(payload["dataset_manifest_sha256"], "dataset_manifest_sha256")
    if _sha256_file(dataset_manifest) != dataset_sha:
        raise StudentV3CandidatePilotError("dataset manifest SHA mismatch")
    bridge_manifest_sha = _sha256(
        payload["bridge_manifest_sha256"], "bridge_manifest_sha256"
    )
    if _sha256_file(bridge_manifest) != bridge_manifest_sha:
        raise StudentV3CandidatePilotError("bridge manifest SHA mismatch")
    bridge_sha = _sha256(payload["bridge_sha256"], "bridge_sha256")
    catalog_sha = _sha256(payload["teacher_catalog_sha256"], "teacher_catalog_sha256")
    try:
        dataset_payload = json.loads(
            dataset_manifest.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudentV3CandidatePilotError("dataset manifest is unreadable") from exc
    if not isinstance(dataset_payload, Mapping):
        raise StudentV3CandidatePilotError("dataset manifest is not a mapping")
    dataset_expected = {
        "purpose": purpose,
        "catalog_sha256": catalog_sha,
        "bridge_manifest_path": str(bridge_manifest),
        "bridge_manifest_sha256": bridge_manifest_sha,
        "bridge_sha256": bridge_sha,
        "synthetic_test_only": False,
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }
    for field, expected in dataset_expected.items():
        if dataset_payload.get(field) != expected:
            candidate_field = (
                "teacher_catalog_sha256"
                if field == "catalog_sha256"
                else field
            )
            raise StudentV3CandidatePilotError(
                f"dataset manifest {candidate_field} does not match candidate artifact"
            )
    qualification_file_sha = _sha256(
        payload["submission_deck_qualification_file_sha256"],
        "submission_deck_qualification_file_sha256",
    )
    if _sha256_file(qualification_path) != qualification_file_sha:
        raise StudentV3CandidatePilotError("deck qualification file SHA mismatch")
    from mage_ptcg.meta_specialist.submission_deck_qualification_v1 import (
        SubmissionDeckQualificationV1Error,
        verify_submission_deck_qualification_v1,
    )

    try:
        qualification, qualified_deck = verify_submission_deck_qualification_v1(
            qualification_path, ROOT
        )
    except (SubmissionDeckQualificationV1Error, OSError, ValueError) as exc:
        raise StudentV3CandidatePilotError(
            f"formal deck qualification verification failed: {exc}"
        ) from exc
    qualification_sha = _sha256(
        payload["submission_deck_qualification_sha256"],
        "submission_deck_qualification_sha256",
    )
    qualified_deck_identity = _text(
        payload["qualified_deck_identity"], "qualified_deck_identity"
    )
    if (
        qualification.get("qualification_sha256") != qualification_sha
        or qualified_deck.deck_file_sha256 != deck_sha
        or qualified_deck.deck_identity != qualified_deck_identity
        or qualified_deck.usage_boundary != "bundle_allowed"
        or qualified_deck.policy_compatibility != "student-v3-set"
        or (ROOT / str(qualification["deck_path"])).resolve() != deck.resolve()
    ):
        raise StudentV3CandidatePilotError(
            "qualified deck does not match candidate deck identity"
        )
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        student_v3_set_runtime_closure_v1,
    )

    runtime_closure = student_v3_set_runtime_closure_v1()
    training_summary = _load_training_summary_v1(summary)
    summary_expected = {
        "dataset_manifest_sha256": dataset_sha,
        "catalog_sha256": catalog_sha,
        "weight_sidecar_sha256": sidecar_sha,
        "objective_kind": objective_kind,
        "purpose": purpose,
        "best_checkpoint_sha256": checkpoint_sha,
    }
    for field, expected in summary_expected.items():
        if training_summary.get(field) != expected:
            artifact_field = {
                "catalog_sha256": "teacher_catalog_sha256",
                "weight_sidecar_sha256": "target_sidecar_sha256",
            }.get(field, field)
            raise StudentV3CandidatePilotError(
                f"training summary {artifact_field} mismatch"
            )
    _require_summary_authority_false(training_summary)
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetRuntimeError,
        required_max_count_from_summary,
    )

    try:
        required_max_count_from_summary(training_summary)
    except StudentV3SetRuntimeError as exc:
        raise StudentV3CandidatePilotError(str(exc)) from exc
    return StudentV3CandidateArtifactV1(
        candidate_id=_text(payload["candidate_id"], "candidate_id"),
        model_dir=model_dir,
        checkpoint_path=checkpoint,
        checkpoint_sha256=checkpoint_sha,
        training_summary_path=summary,
        training_summary_sha256=summary_sha,
        deck_path=deck,
        deck_sha256=deck_sha,
        dataset_manifest_path=dataset_manifest,
        dataset_manifest_sha256=dataset_sha,
        bridge_manifest_path=bridge_manifest,
        bridge_manifest_sha256=bridge_manifest_sha,
        bridge_sha256=bridge_sha,
        teacher_catalog_sha256=catalog_sha,
        target_sidecar_sha256=sidecar_sha,
        objective_kind=objective_kind,
        purpose=purpose,
        submission_deck_qualification_path=qualification_path,
        submission_deck_qualification_repo_path=qualification_repo_path,
        submission_deck_qualification_file_sha256=qualification_file_sha,
        submission_deck_qualification_sha256=qualification_sha,
        qualified_deck_identity=qualified_deck_identity,
        runtime_closure=runtime_closure,
        performance_evidence=_false(payload["performance_evidence"], "performance_evidence"),
        research_only=_true(payload["research_only"], "research_only"),
        training_authority=_false(payload["training_authority"], "training_authority"),
        promotion_authority=_false(payload["promotion_authority"], "promotion_authority"),
        submission_authority=_false(payload["submission_authority"], "submission_authority"),
        longrun_authority=_false(payload["longrun_authority"], "longrun_authority"),
        artifact_path=artifact_path,
        artifact_sha256=_sha256_file(artifact_path),
    )


def _opponent_identity(instance: OpponentInstanceV1) -> dict[str, object]:
    return {
        "policy_sha256": _sha256_file(Path(instance.policy_path)),
        "deck_sha256": _sha256_file(Path(instance.deck_csv_path)),
        "usage_boundary": instance.usage_boundary,
        "source": instance.source,
    }


def build_student_v3_candidate_games_v1(
    *,
    candidate: StudentV3CandidateArtifactV1,
    pool: Mapping[str, OpponentInstanceV1],
    reference_ids: Sequence[str],
    games_per_opponent_seat: int = 2,
    base_seed: int = 13_000_000,
    block_id: str = "student-v3-set-candidate-pilot-v1",
    max_steps: int = 2_000,
    timeout_seconds: float = 600.0,
    pool_root: str | Path = ROOT / "opponents",
    telemetry_root: str | Path = DEFAULT_RUNTIME_TELEMETRY_ROOT_V1,
) -> tuple[EvaluationGameV1, ...]:
    if type(candidate) is not StudentV3CandidateArtifactV1:
        raise StudentV3CandidatePilotError("candidate must be an exact StudentV3CandidateArtifactV1")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise StudentV3CandidatePilotError("games_per_opponent_seat must be positive")
    if not reference_ids or len(reference_ids) != len(set(reference_ids)):
        raise StudentV3CandidatePilotError("reference_ids must be non-empty and unique")
    missing = sorted(set(reference_ids) - set(pool))
    if missing:
        raise StudentV3CandidatePilotError(f"unknown reference IDs: {missing}")
    pool_root_path = Path(pool_root).resolve()
    if not pool_root_path.is_dir():
        raise StudentV3CandidatePilotError(f"pool_root is not a directory: {pool_root_path}")
    telemetry_root_path = Path(telemetry_root).resolve()
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for opponent_id in reference_ids:
        opponent = pool[opponent_id]
        identity = _opponent_identity(opponent)
        for seat in (0, 1):
            for repetition in range(games_per_opponent_seat):
                game_id = (
                    f"{block_id}-{candidate.candidate_id}-{candidate.policy_identity_sha256[:12]}-"
                    f"{opponent_id}-seat{seat}-g{repetition:04d}"
                )
                games.append(
                    EvaluationGameV1(
                        game_id=game_id,
                        block_id=block_id,
                        policy_id=f"student-v3-set:{candidate.candidate_id}",
                        policy_sha256=candidate.policy_identity_sha256,
                        deck_id=candidate.candidate_id,
                        deck_sha256=candidate.deck_sha256,
                        opponent_id=opponent_id,
                        opponent_identity=identity,
                        opponent_deck_sha256=str(identity["deck_sha256"]),
                        seat=seat,
                        seed=base_seed + ordinal,
                        max_steps=max_steps,
                        timeout_seconds=timeout_seconds,
                        subject_deck_path=str(candidate.deck_path),
                        opponent_deck_path=opponent.deck_csv_path,
                        policy_agent_name=f"student-v3-set:{candidate.candidate_id}",
                        opponent_agent_name=opponent_id,
                        runner_ref=RUNNER_REF_V1,
                        metadata={
                            "schema_version": EVALUATION_SCHEMA_V1,
                            "candidate_id": candidate.candidate_id,
                            "candidate_artifact_path": str(candidate.artifact_path),
                            "candidate_artifact_sha256": candidate.artifact_sha256,
                            "checkpoint_path": str(candidate.checkpoint_path),
                            "checkpoint_sha256": candidate.checkpoint_sha256,
                            "policy_identity_sha256": candidate.policy_identity_sha256,
                            "training_summary_sha256": candidate.training_summary_sha256,
                            "runtime_closure": candidate.runtime_closure,
                            "qualified_deck_identity": candidate.qualified_deck_identity,
                            "submission_deck_qualification_file_sha256": (
                                candidate.submission_deck_qualification_file_sha256
                            ),
                            "submission_deck_qualification_path": (
                                candidate.submission_deck_qualification_repo_path
                            ),
                            "submission_deck_qualification_sha256": (
                                candidate.submission_deck_qualification_sha256
                            ),
                            "dataset_manifest_sha256": candidate.dataset_manifest_sha256,
                            "teacher_catalog_sha256": candidate.teacher_catalog_sha256,
                            "target_sidecar_sha256": candidate.target_sidecar_sha256,
                            "objective_kind": candidate.objective_kind,
                            "purpose": candidate.purpose,
                            "repetition": repetition,
                            "pool_root": str(pool_root_path),
                            "student_v3_runtime_telemetry_path": str(
                                _runtime_telemetry_path_v1(
                                    telemetry_root_path, game_id
                                )
                            ),
                            "inference_device": "cpu",
                            "engine_seed_supported": False,
                            "pairing": "independent_stratified",
                            "performance_evidence": False,
                            "research_only": True,
                            "training_authority": False,
                            "promotion_authority": False,
                            "submission_authority": False,
                            "longrun_authority": False,
                        },
                    )
                )
                ordinal += 1
    return tuple(games)


def _require_schedule_metadata_bindings_v1(
    candidate: StudentV3CandidateArtifactV1,
    metadata: Mapping[str, object],
) -> None:
    expected = {
        "candidate_id": candidate.candidate_id,
        "candidate_artifact_sha256": candidate.artifact_sha256,
        "checkpoint_sha256": candidate.checkpoint_sha256,
        "policy_identity_sha256": candidate.policy_identity_sha256,
        "training_summary_sha256": candidate.training_summary_sha256,
        "runtime_closure": candidate.runtime_closure,
        "qualified_deck_identity": candidate.qualified_deck_identity,
        "submission_deck_qualification_file_sha256": (
            candidate.submission_deck_qualification_file_sha256
        ),
        "submission_deck_qualification_path": (
            candidate.submission_deck_qualification_repo_path
        ),
        "submission_deck_qualification_sha256": (
            candidate.submission_deck_qualification_sha256
        ),
        "dataset_manifest_sha256": candidate.dataset_manifest_sha256,
        "teacher_catalog_sha256": candidate.teacher_catalog_sha256,
        "target_sidecar_sha256": candidate.target_sidecar_sha256,
        "objective_kind": candidate.objective_kind,
        "purpose": candidate.purpose,
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    for field, value in expected.items():
        if metadata.get(field) != value:
            raise StudentV3CandidatePilotError(
                f"scheduled {field} does not match candidate artifact"
            )
    artifact_path = Path(str(metadata.get("candidate_artifact_path", ""))).resolve()
    checkpoint_path = Path(str(metadata.get("checkpoint_path", ""))).resolve()
    if artifact_path != candidate.artifact_path:
        raise StudentV3CandidatePilotError(
            "scheduled candidate_artifact_path does not match candidate artifact"
        )
    if checkpoint_path != candidate.checkpoint_path:
        raise StudentV3CandidatePilotError(
            "scheduled checkpoint_path does not match candidate artifact"
        )


def _runtime_counter_v1(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry {name} must be a non-negative int"
        )
    return value


def _validate_runtime_snapshot_v1(
    snapshot: Mapping[str, object],
) -> dict[str, object]:
    expected = {
        "selection_decision_count",
        "model_decision_count",
        "fallback_count",
        "fallback_reason_counts",
    }
    if not isinstance(snapshot, Mapping) or set(snapshot) != expected:
        raise StudentV3CandidatePilotError(
            "runtime telemetry snapshot has an invalid schema"
        )
    selection_count = _runtime_counter_v1(
        snapshot["selection_decision_count"], "selection_decision_count"
    )
    model_count = _runtime_counter_v1(
        snapshot["model_decision_count"], "model_decision_count"
    )
    fallback_count = _runtime_counter_v1(
        snapshot["fallback_count"], "fallback_count"
    )
    reasons = snapshot["fallback_reason_counts"]
    if not isinstance(reasons, Mapping):
        raise StudentV3CandidatePilotError(
            "runtime telemetry fallback_reason_counts must be a mapping"
        )
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        RULE_V0_FALLBACK_REASONS_V1,
    )

    normalized_reasons: dict[str, int] = {}
    for reason, count in reasons.items():
        if type(reason) is not str or reason not in RULE_V0_FALLBACK_REASONS_V1:
            raise StudentV3CandidatePilotError(
                "runtime telemetry contains a non-allowlisted fallback reason"
            )
        normalized_reasons[reason] = _runtime_counter_v1(
            count, f"fallback_reason_counts.{reason}"
        )
        if normalized_reasons[reason] == 0:
            raise StudentV3CandidatePilotError(
                "runtime telemetry must omit zero-count fallback reasons"
            )
    if (
        sum(normalized_reasons.values()) != fallback_count
        or model_count > selection_count
        or fallback_count > selection_count
        or model_count + fallback_count > selection_count
    ):
        raise StudentV3CandidatePilotError(
            "runtime telemetry counters are internally inconsistent"
        )
    return {
        "selection_decision_count": selection_count,
        "model_decision_count": model_count,
        "fallback_count": fallback_count,
        "fallback_reason_counts": dict(sorted(normalized_reasons.items())),
    }


def _build_runtime_telemetry_v1(
    *,
    game: EvaluationGameV1,
    candidate: StudentV3CandidateArtifactV1,
    snapshot: Mapping[str, object],
    match_status: str,
) -> dict[str, object]:
    counters = _validate_runtime_snapshot_v1(snapshot)
    closure_sha = _sha256(
        candidate.runtime_closure.get("closure_sha256"),
        "runtime_closure.closure_sha256",
    )
    payload: dict[str, object] = {
        "schema_version": RUNTIME_TELEMETRY_SCHEMA_V1,
        "game_id": game.game_id,
        "candidate_id": candidate.candidate_id,
        "candidate_artifact_sha256": candidate.artifact_sha256,
        "policy_identity_sha256": candidate.policy_identity_sha256,
        "runtime_closure_sha256": closure_sha,
        "seat": game.seat,
        "match_status": _text(match_status, "match_status"),
        **counters,
        "fallback_policy": "agents.rule_agent:choose_rule_indices",
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    payload["telemetry_sha256"] = _runtime_telemetry_sha256_v1(payload)
    return payload


def _load_runtime_telemetry_v1(
    row: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    game_id = _text(row.get("game_id"), "ledger game_id")
    metadata = row.get("metadata")
    if not isinstance(metadata, Mapping):
        raise StudentV3CandidatePilotError(
            f"runtime telemetry row {game_id} has no metadata mapping"
        )
    path = _require_runtime_telemetry_path_v1(
        metadata.get("student_v3_runtime_telemetry_path"), game_id
    )
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                StudentV3CandidatePilotError(
                    f"non-finite runtime telemetry value: {value}"
                )
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry artifact is unreadable for {game_id}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping) or set(payload) != _RUNTIME_TELEMETRY_FIELDS_V1:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry artifact schema mismatch for {game_id}"
        )
    telemetry_sha = _sha256(payload.get("telemetry_sha256"), "telemetry_sha256")
    if payload.get("schema_version") != RUNTIME_TELEMETRY_SCHEMA_V1:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry schema_version mismatch for {game_id}"
        )
    if _runtime_telemetry_sha256_v1(payload) != telemetry_sha:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry SHA mismatch for {game_id}"
        )
    runtime_closure = metadata.get("runtime_closure")
    if not isinstance(runtime_closure, Mapping):
        raise StudentV3CandidatePilotError(
            f"scheduled runtime closure is missing for {game_id}"
        )
    if metadata.get("policy_identity_sha256") != row.get("policy_sha256"):
        raise StudentV3CandidatePilotError(
            f"scheduled policy identity does not match ledger for {game_id}"
        )
    expected = {
        "game_id": game_id,
        "candidate_id": metadata.get("candidate_id"),
        "candidate_artifact_sha256": metadata.get("candidate_artifact_sha256"),
        "policy_identity_sha256": row.get("policy_sha256"),
        "runtime_closure_sha256": runtime_closure.get("closure_sha256"),
        "seat": row.get("seat"),
        "fallback_policy": "agents.rule_agent:choose_rule_indices",
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise StudentV3CandidatePilotError(
                f"runtime telemetry {field} mismatch for {game_id}"
            )
    raw_status = row.get("raw_status")
    if type(raw_status) is str and payload.get("match_status") != raw_status:
        raise StudentV3CandidatePilotError(
            f"runtime telemetry match_status mismatch for {game_id}"
        )
    counters = _validate_runtime_snapshot_v1(
        {field: payload[field] for field in (
            "selection_decision_count",
            "model_decision_count",
            "fallback_count",
            "fallback_reason_counts",
        )}
    )
    normalized = dict(payload)
    normalized.update(counters)
    return normalized, _sha256_file(path)


def _load_student_v3_policy_factory_v1(
    candidate: StudentV3CandidateArtifactV1,
    *,
    schedule_metadata: Mapping[str, object],
):
    _require_schedule_metadata_bindings_v1(candidate, schedule_metadata)
    if _sha256_file(candidate.checkpoint_path) != candidate.checkpoint_sha256:
        raise StudentV3CandidatePilotError("checkpoint changed after artifact load")
    if _sha256_file(candidate.training_summary_path) != candidate.training_summary_sha256:
        raise StudentV3CandidatePilotError("training summary changed after artifact load")
    if _sha256_file(candidate.deck_path) != candidate.deck_sha256:
        raise StudentV3CandidatePilotError("deck changed after artifact load")
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        student_v3_set_runtime_closure_v1,
    )

    if student_v3_set_runtime_closure_v1() != candidate.runtime_closure:
        raise StudentV3CandidatePilotError(
            "runtime closure changed after candidate artifact load"
        )
    key = candidate.policy_identity_sha256
    cached = _MODEL_CACHE.get(key)
    if cached is None:
        from mage_ptcg.offline_scaleup.student_v3_set_runtime import load_set_candidate_ranker

        model, summary = load_set_candidate_ranker(candidate.model_dir, "cpu")
        if not isinstance(summary, Mapping):
            raise StudentV3CandidatePilotError("Student v3 runtime summary is not a mapping")
        cached = (model, dict(summary))
        _MODEL_CACHE[key] = cached
    model, summary = cached
    max_count = _require_runtime_summary_bindings_v1(candidate, summary)
    deck = read_deck_csv(candidate.deck_path)
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetRuntimeTelemetry,
    )

    telemetry = StudentV3SetRuntimeTelemetry()

    def factory(_deck: object, _seed: int):
        from mage_ptcg.offline_scaleup.student_v3_set_runtime import StudentV3SetCandidatePolicy

        return StudentV3SetCandidatePolicy(
            model=model,
            device="cpu",
            deck=deck,
            max_count=max_count,
            telemetry=telemetry,
        ).as_agent()

    factory.student_v3_runtime_telemetry = telemetry  # type: ignore[attr-defined]
    return factory


def _factory_runtime_snapshot_v1(factory: object) -> dict[str, object]:
    from mage_ptcg.offline_scaleup.student_v3_set_runtime import (
        StudentV3SetRuntimeTelemetry,
    )

    telemetry = getattr(factory, "student_v3_runtime_telemetry", None)
    if type(telemetry) is not StudentV3SetRuntimeTelemetry:
        raise StudentV3CandidatePilotError(
            "Student v3 policy factory has no exact runtime telemetry"
        )
    return _validate_runtime_snapshot_v1(telemetry.snapshot())


def run_student_v3_candidate_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    game = _game_from_payload(payload)
    metadata = game.metadata
    telemetry_path = _require_runtime_telemetry_path_v1(
        metadata.get("student_v3_runtime_telemetry_path"), game.game_id
    )
    artifact_path = Path(str(metadata.get("candidate_artifact_path", ""))).resolve()
    candidate = load_student_v3_candidate_artifact_v1(artifact_path)
    _require_schedule_metadata_bindings_v1(candidate, metadata)
    if candidate.artifact_sha256 != metadata.get("candidate_artifact_sha256"):
        raise StudentV3CandidatePilotError("candidate artifact SHA changed after scheduling")
    if candidate.policy_identity_sha256 != game.policy_sha256:
        raise StudentV3CandidatePilotError(
            "scheduled policy identity does not match candidate closure"
        )
    if candidate.deck_sha256 != game.deck_sha256:
        raise StudentV3CandidatePilotError("scheduled deck identity does not match candidate")
    pool_root = Path(str(metadata.get("pool_root", ROOT / "opponents"))).resolve()
    pool = load_opponent_pool_v1(pool_root)
    opponent = resolve_opponent_v1(
        pool, game.opponent_id, subject_deck_csv_path=str(candidate.deck_path)
    )
    subject_factory = _load_student_v3_policy_factory_v1(
        candidate,
        schedule_metadata=metadata,
    )
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    first = game.seat == 0

    def persist_runtime_telemetry_v1(
        match_status: str,
    ) -> tuple[dict[str, object], str]:
        snapshot = _factory_runtime_snapshot_v1(subject_factory)
        artifact = _build_runtime_telemetry_v1(
            game=game,
            candidate=candidate,
            snapshot=snapshot,
            match_status=match_status,
        )
        _atomic_json(telemetry_path, artifact)
        verification_row = {
            "game_id": game.game_id,
            "policy_sha256": game.policy_sha256,
            "seat": game.seat,
            "raw_status": None if match_status == "RUNNER_EXCEPTION" else match_status,
            "metadata": metadata,
        }
        reloaded, file_sha = _load_runtime_telemetry_v1(verification_row)
        if reloaded != artifact:
            raise StudentV3CandidatePilotError(
                "runtime telemetry changed during atomic verification"
            )
        return snapshot, file_sha

    try:
        raw_result = run_match(
            deck_a_path=str(candidate.deck_path) if first else opponent.deck_csv_path,
            deck_b_path=opponent.deck_csv_path if first else str(candidate.deck_path),
            agent_a_name=game.policy_agent_name if first else game.opponent_agent_name,
            agent_b_name=game.opponent_agent_name if first else game.policy_agent_name,
            seed=game.seed,
            max_steps=game.max_steps,
            output_dir=str(ROOT / "runs" / "student-v3-set-candidate-worker" / game.game_id),
            save_html=False,
            save_result=False,
            agent_a_factory=subject_factory if first else opponent_factory,
            agent_b_factory=opponent_factory if first else subject_factory,
        )
    except BaseException as exc:
        try:
            persist_runtime_telemetry_v1("RUNNER_EXCEPTION")
        except BaseException as telemetry_exc:
            exc.add_note(
                "runtime telemetry persistence also failed: "
                f"{type(telemetry_exc).__name__}: {telemetry_exc}"
            )
        raise
    if not isinstance(raw_result, Mapping):
        persist_runtime_telemetry_v1("RUNNER_EXCEPTION")
        raise StudentV3CandidatePilotError("CABT runner result is not a mapping")
    try:
        status = _text(raw_result.get("status"), "CABT runner status")
    except StudentV3CandidatePilotError:
        persist_runtime_telemetry_v1("RUNNER_EXCEPTION")
        raise
    runtime_snapshot, telemetry_file_sha = persist_runtime_telemetry_v1(status)
    result = dict(raw_result)
    result["student_v3_runtime_telemetry"] = runtime_snapshot
    result["student_v3_runtime_telemetry_path"] = str(telemetry_path)
    result["student_v3_runtime_telemetry_file_sha256"] = telemetry_file_sha
    return result


def _require_runtime_telemetry_candidate_root_v1(
    rows: Sequence[Mapping[str, object]],
) -> StudentV3CandidateArtifactV1:
    first_metadata = rows[0].get("metadata")
    if not isinstance(first_metadata, Mapping):
        raise StudentV3CandidatePilotError(
            "runtime telemetry ledger has no candidate metadata root"
        )
    artifact_path = Path(
        _text(first_metadata.get("candidate_artifact_path"), "candidate_artifact_path")
    ).resolve()
    candidate = load_student_v3_candidate_artifact_v1(artifact_path)
    expected_metadata = {
        "schema_version": EVALUATION_SCHEMA_V1,
        "candidate_id": candidate.candidate_id,
        "candidate_artifact_path": str(candidate.artifact_path),
        "candidate_artifact_sha256": candidate.artifact_sha256,
        "policy_identity_sha256": candidate.policy_identity_sha256,
        "runtime_closure": candidate.runtime_closure,
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    for row in rows:
        game_id = _text(row.get("game_id"), "ledger game_id")
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            raise StudentV3CandidatePilotError(
                f"runtime telemetry row {game_id} has no metadata mapping"
            )
        for field, value in expected_metadata.items():
            if metadata.get(field) != value:
                raise StudentV3CandidatePilotError(
                    f"runtime telemetry candidate root {field} mismatch for {game_id}"
                )
        if row.get("policy_sha256") != candidate.policy_identity_sha256:
            raise StudentV3CandidatePilotError(
                f"runtime telemetry ledger policy identity mismatch for {game_id}"
            )
    return candidate


def _summarize_runtime_telemetry_v1(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    declarations = [
        isinstance(row.get("metadata"), Mapping)
        and "student_v3_runtime_telemetry_path" in row["metadata"]
        for row in rows
    ]
    if not any(declarations):
        return {
            "status": "UNAVAILABLE",
            "observed_games": 0,
            "selection_decision_count": None,
            "model_decision_count": None,
            "fallback_count": None,
            "fallback_reason_counts": None,
            "artifact_file_sha256s": {},
        }
    if not all(declarations):
        raise StudentV3CandidatePilotError(
            "runtime telemetry path is missing from part of the ledger"
        )
    _require_runtime_telemetry_candidate_root_v1(rows)
    game_ids = [_text(row.get("game_id"), "ledger game_id") for row in rows]
    if len(game_ids) != len(set(game_ids)):
        raise StudentV3CandidatePilotError(
            "runtime telemetry ledger contains duplicate game_id"
        )
    selection_count = 0
    model_count = 0
    fallback_count = 0
    reasons: Counter[str] = Counter()
    file_sha256s: dict[str, str] = {}
    for row in rows:
        artifact, file_sha = _load_runtime_telemetry_v1(row)
        game_id = str(row["game_id"])
        selection_count += int(artifact["selection_decision_count"])
        model_count += int(artifact["model_decision_count"])
        fallback_count += int(artifact["fallback_count"])
        reasons.update(artifact["fallback_reason_counts"])
        file_sha256s[game_id] = file_sha
    return {
        "status": "COMPLETE",
        "observed_games": len(rows),
        "selection_decision_count": selection_count,
        "model_decision_count": model_count,
        "fallback_count": fallback_count,
        "fallback_reason_counts": dict(sorted(reasons.items())),
        "artifact_file_sha256s": dict(sorted(file_sha256s.items())),
    }


def summarize_student_v3_candidate_rows_v1(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    aggregate = aggregate_ledger_v1(rows)
    metadata = rows[0].get("metadata", {}) if rows else {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    by_seat = {
        str(seat): aggregate_ledger_v1([row for row in rows if row.get("seat") == seat])
        for seat in (0, 1)
    }
    return {
        "schema_version": EVALUATION_SCHEMA_V1,
        "candidate_id": metadata.get("candidate_id"),
        "purpose": metadata.get("purpose"),
        **aggregate,
        "by_seat": by_seat,
        "student_v3_runtime_telemetry": _summarize_runtime_telemetry_v1(rows),
        "engine_seed_supported": False,
        "pairing": "independent_stratified",
        "performance_evidence": False,
        "research_only": True,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }


def _load_reference_ids(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("opponent_ids") if isinstance(payload, Mapping) else None
    if (
        not isinstance(values, list)
        or len(values) != 24
        or len(set(values)) != 24
        or any(type(value) is not str or not value for value in values)
    ):
        raise StudentV3CandidatePilotError("reference config requires exactly 24 unique IDs")
    if payload.get("promotion_authority") is not False:
        raise StudentV3CandidatePilotError("reference config must have no promotion authority")
    return tuple(values)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-candidate-artifact", action="store_true",
        help="build and verify a closed candidate artifact without running CABT",
    )
    parser.add_argument("--candidate-id")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--dataset-manifest", type=Path)
    parser.add_argument("--deck-qualification", type=Path)
    parser.add_argument("--candidate-output", type=Path)
    parser.add_argument("--candidate-artifact", type=Path)
    parser.add_argument(
        "--reference-config",
        type=Path,
        default=ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json",
    )
    parser.add_argument("--pool-root", type=Path, default=ROOT / "opponents")
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=13_000_000)
    parser.add_argument("--block-id", default="student-v3-set-candidate-pilot-v1")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--worker-recycle-games", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.build_candidate_artifact:
        if (
            args.candidate_id is None
            or args.model_dir is None
            or args.dataset_manifest is None
            or args.deck_qualification is None
            or args.candidate_output is None
            or args.candidate_artifact is not None
        ):
            parser.error(
                "candidate build requires --candidate-id, --model-dir, "
                "--dataset-manifest, --deck-qualification, and "
                "--candidate-output; do not pass --candidate-artifact"
            )
        candidate = build_student_v3_candidate_artifact_v1(
            candidate_id=args.candidate_id,
            model_dir=args.model_dir,
            dataset_manifest_path=args.dataset_manifest,
            submission_deck_qualification_path=args.deck_qualification,
            output_path=args.candidate_output,
        )
        print(
            json.dumps(
                {
                    "candidate_artifact_path": str(candidate.artifact_path),
                    "candidate_artifact_sha256": candidate.artifact_sha256,
                    "checkpoint_sha256": candidate.checkpoint_sha256,
                    "policy_identity_sha256": candidate.policy_identity_sha256,
                    "qualified_deck_identity": candidate.qualified_deck_identity,
                    "runtime_closure_sha256": candidate.runtime_closure[
                        "closure_sha256"
                    ],
                    "performance_evidence": False,
                    "promotion_authority": False,
                    "submission_authority": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.candidate_artifact is None:
        parser.error(
            "evaluation requires --candidate-artifact, or use "
            "--build-candidate-artifact"
        )
    if args.output is None:
        parser.error("evaluation requires --output")
    candidate = load_student_v3_candidate_artifact_v1(args.candidate_artifact)
    pool = load_opponent_pool_v1(args.pool_root.resolve())
    references = _load_reference_ids(args.reference_config.resolve())
    games = build_student_v3_candidate_games_v1(
        candidate=candidate,
        pool=pool,
        reference_ids=references,
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        block_id=args.block_id,
        max_steps=args.max_steps,
        timeout_seconds=args.timeout_seconds,
        pool_root=args.pool_root.resolve(),
    )
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=args.output,
        max_workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
        overwrite=args.overwrite,
    )
    summary = summarize_student_v3_candidate_rows_v1(result["rows"])
    summary.update(
        {
            "candidate_artifact_sha256": candidate.artifact_sha256,
            "checkpoint_sha256": candidate.checkpoint_sha256,
            "policy_identity_sha256": candidate.policy_identity_sha256,
            "deck_sha256": candidate.deck_sha256,
            "qualified_deck_identity": candidate.qualified_deck_identity,
            "submission_deck_qualification_path": (
                candidate.submission_deck_qualification_repo_path
            ),
            "submission_deck_qualification_file_sha256": (
                candidate.submission_deck_qualification_file_sha256
            ),
            "submission_deck_qualification_sha256": (
                candidate.submission_deck_qualification_sha256
            ),
            "runtime_closure": candidate.runtime_closure,
            "bridge_manifest_sha256": candidate.bridge_manifest_sha256,
            "bridge_sha256": candidate.bridge_sha256,
            "teacher_catalog_sha256": candidate.teacher_catalog_sha256,
            "dataset_manifest_sha256": candidate.dataset_manifest_sha256,
            "target_sidecar_sha256": candidate.target_sidecar_sha256,
            "reference_config_sha256": _sha256_file(args.reference_config.resolve()),
            "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
            "arena_summary": result["summary"],
        }
    )
    output = args.output / "student_v3_candidate_summary.json"
    _atomic_json(output, summary)
    print(
        json.dumps(
            {**summary, "summary_sha256": _sha256_file(output)},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCHEMA_V1",
    "EVALUATION_SCHEMA_V1",
    "StudentV3CandidateArtifactV1",
    "StudentV3CandidatePilotError",
    "build_student_v3_candidate_artifact_v1",
    "load_student_v3_candidate_artifact_v1",
    "build_student_v3_candidate_games_v1",
    "run_student_v3_candidate_game_v1",
    "summarize_student_v3_candidate_rows_v1",
]
