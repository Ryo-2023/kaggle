"""外部 teacher に subject デッキを操縦させ、その決定を教師データとして収集する。

正典 §9.3 (教師と ExIt) と §7 の ``TeacherDatasetManifest`` に対応する。

## これが何をするか

``collect_trajectories_v1`` は **subject 自身の behavior** を V-trace 軌跡として
集める。この module は別の目的を持つ。``opponents/`` に登録された強い agent に
**subject のデッキを操縦させ**、その committed action を BC / ExIt の policy
target として集める。θ0 を rule v0 の模倣ではなく、既知の強い方策から始めるための
入力になる。

## 正典に従って明示する規律

- **敗局を捨てない。** 正典 §9.3 は「勝局だけ BC、敗局は BC に使わない」を新系列の
  既定から外すと定める。outcome は ``value_target`` にだけ効き、policy target の
  weight は結果で 0 にしない。
- **表現できない決定を黙って捨てない。** 完全列挙が上限を超える等で class 列へ
  写せない決定は ``teacher.status="unavailable"`` と理由付きで残す。局ごと落とすと
  「複数選択を理由に game を黙って除外しない」(§9.3) に反する。
- **座席を均衡させる。** subject が先手/後手のどちらでも teacher の判断を集める。
- **利用許諾を record に載せる。** teacher の派生資格は
  ``make_source_permission_manifest_v1`` の ``allowed_usages`` として各 record の
  ``source.permission_manifest_id`` に紐づく。``training-local`` を持たない
  teacher からは 1 件も収集しない (fail-closed)。

## 非公開情報境界

teacher が見るのは engine が渡す自分の observation だけである。record の feature は
``build_actor_visible_decision_state_v2`` が抽出した actor-visible 情報に限られる
(正典 §9.2)。teacher / 相手の identity は ``source`` と manifest にだけ置き、
decision feature へは渡さない。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    FEATURE_SCHEMA_HASH_V1,
    extract_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    build_actor_visible_decision_state_v2,
    serialize_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    _candidate_rows_from_state,
    _decision_id,
    _record_content_hash,
    build_local_record_v2,
    canonical_json_bytes_v2,
    make_source_permission_manifest_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    OpponentInstanceV1,
    build_opponent_agent_factory_v1,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import RuntimeDecisionEnvelope
from mage_ptcg.meta_specialist.teacher_dataset_v1 import (
    TeacherActionNotEnumerableV1Error,
    hard_selection_teacher_payload_v1,
    invert_teacher_option_indices_v1,
    unavailable_teacher_payload_v1,
)


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEACHER_OUTPUT_BASE_V1 = _REPO_ROOT / "runs" / "meta-specialist-teacher-records"

TEACHER_SOURCE_KIND_V1 = "pooled_external_submission_agent"
INTERNAL_TEACHER_SOURCE_KIND_V1 = "team_internal_agent"
REQUIRED_TEACHER_USAGE_V1 = "training-local"
COLLECTION_CONTRACT_SCHEMA_V2 = "specialist-teacher-collection-contract-v2"
GAME_RESULT_SCHEMA_V2 = "specialist-teacher-collection-game-result-v2"

# 正典 §9.3: outcome は value/return target にだけ使う。
_VALUE_TARGET_BY_OUTCOME_V1 = {"win": 1.0, "draw": 0.0, "loss": -1.0}

# 正典 §9.3 の matchup cap。1 相手が dataset のこの割合を超えたら weight を下げる。
# 既定は「16 相手で均等なら 1/16 = 6.25%」に対し十分な余裕を持たせた値。
DEFAULT_MATCHUP_CAP_FRACTION_V1 = 0.25
_MIN_QUALITY_WEIGHT_V1 = 0.1


class CollectTeacherRecordsV1Error(ValueError):
    """Raised when teacher collection cannot proceed under the canon's rules."""


def _file_sha256_v1(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def teacher_source_kind_v1(teacher: OpponentInstanceV1) -> str:
    """Map the registered source class to the record/permission source kind."""
    if teacher.source == "internal":
        return INTERNAL_TEACHER_SOURCE_KIND_V1
    if teacher.source == "public":
        return TEACHER_SOURCE_KIND_V1
    raise CollectTeacherRecordsV1Error(
        f"{teacher.opponent_id}: unsupported teacher source class {teacher.source!r}"
    )


@dataclass(frozen=True, slots=True)
class TeacherCollectionGameResultV1:
    game_index: int
    seat: int
    opponent_id: str
    status: str
    outcome: str | None
    records: tuple[dict[str, Any], ...]
    unlabelled: int
    detail: str = ""


def _atomic_write_json_v1(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_write_jsonl_v1(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def build_teacher_permission_manifest_v1(
    teacher: OpponentInstanceV1,
    *,
    allowed_usages: Sequence[str],
    decision_ref: str,
) -> dict[str, Any]:
    """Seal the derivation decision for one teacher into a permission manifest.

    ``allowed_usages`` comes from a recorded human decision, never from this
    module's own inference: whether a pooled agent's behaviour may be distilled
    is a licence and competition-rules judgement (正典 §5).  Collection refuses
    to run without ``training-local``, so an undecided teacher cannot quietly
    contribute training data.
    """
    usages = tuple(sorted(set(allowed_usages)))
    if REQUIRED_TEACHER_USAGE_V1 not in usages:
        raise CollectTeacherRecordsV1Error(
            f"{teacher.opponent_id}: allowed_usages {usages!r} does not include "
            f"{REQUIRED_TEACHER_USAGE_V1!r}; refusing to collect training records "
            "from a teacher whose derivation has not been qualified"
        )
    if not decision_ref:
        raise CollectTeacherRecordsV1Error(
            f"{teacher.opponent_id}: a decision_ref is required so the permission "
            "manifest points at where the derivation decision is recorded"
        )
    return make_source_permission_manifest_v1(
        artifact_sha256=teacher.policy_hash,
        source_kind=teacher_source_kind_v1(teacher),
        allowed_usages=usages,
        revision=teacher.policy_hash[:16],
        issuer=decision_ref,
        valid_from_utc=None,
        expires_at_utc=None,
    )



def quality_weight_for_v1(
    *,
    opponent_id: str,
    matchup_counts: Mapping[str, int],
    total_records: int,
    matchup_cap_fraction: float = DEFAULT_MATCHUP_CAP_FRACTION_V1,
) -> float:
    """正典 §9.3 の matchup cap を quality weight として実現する。

    > 同一 matchup、同一 teacher、同一 exact deck が dataset を占有しないよう cap を
    > 設ける。

    ある相手からの record が dataset の ``matchup_cap_fraction`` を超えたら、その相手の
    以降の record の weight を線形に下げる。捨てるのではなく下げるのは、正典 §9.3 が
    「leak、fault、illegal、schema 不明がない全ての有効 teacher decision を policy
    target 候補とする」と定めるためである。占有だけを抑え、決定そのものは残す。

    ``local_dataset_v2`` は ``quality_weight`` を ``(0, 1]`` に制約するので、下限は
    0 に到達しない。
    """
    if total_records <= 0:
        return 1.0
    share = matchup_counts.get(opponent_id, 0) / total_records
    if share <= matchup_cap_fraction:
        return 1.0
    # share == 1.0 (単一相手が占有) でも 0 にはしない。
    excess = (share - matchup_cap_fraction) / max(1e-9, 1.0 - matchup_cap_fraction)
    return max(_MIN_QUALITY_WEIGHT_V1, 1.0 - excess * (1.0 - _MIN_QUALITY_WEIGHT_V1))


class _TeacherRecordingAgentV1:
    """Runs the teacher and turns each committed decision into one record.

    The teacher's own return value is passed through untouched: recording is a
    read-only side channel and must never change which indices reach the
    engine, or the collected data would describe a policy that never played.
    """

    def __init__(
        self,
        *,
        teacher_agent: Any,
        teacher: OpponentInstanceV1,
        vocabulary: Any,
        episode_id_hash: str,
        permission_manifest_id: str,
        source_kind: str,
    ) -> None:
        self._agent = teacher_agent
        self._teacher = teacher
        self._vocabulary = vocabulary
        self._episode_id_hash = episode_id_hash
        self._permission_manifest_id = permission_manifest_id
        if source_kind not in (TEACHER_SOURCE_KIND_V1, INTERNAL_TEACHER_SOURCE_KIND_V1):
            raise CollectTeacherRecordsV1Error("unsupported teacher record source_kind")
        self._source_kind = source_kind
        self.pending: list[dict[str, Any]] = []
        self.omissions: list[dict[str, Any]] = []
        self.unlabelled = 0

    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        indices = self._agent(observation)
        if isinstance(observation, Mapping) and observation.get("select") is not None:
            try:
                self._capture(observation, indices)
            except TeacherActionNotEnumerableV1Error as exc:
                self.unlabelled += 1
                self.pending.append({"__unavailable__": unavailable_teacher_payload_v1(str(exc)[:200])})
        return indices

    def _capture(self, observation: Mapping[str, Any], indices: Sequence[int]) -> None:
        state = build_actor_visible_decision_state_v2(observation)
        envelope = RuntimeDecisionEnvelope.from_actor_visible_state(
            state, vocabulary=self._vocabulary
        )
        selection = invert_teacher_option_indices_v1(envelope, indices)
        information_state = serialize_actor_visible_decision_state_v2(state)["information_view"]
        extracted = extract_specialist_model_input_v1(state, self._vocabulary)
        decision_id = _decision_id(
            information_state,
            [row["local_action_id"] for row in _candidate_rows_from_state(state, extracted)],
        )
        # `value_target` is filled in once the episode outcome is known; the
        # record is finalized in `_finalize_records_v1`.
        self.pending.append(
            {
                "state": state,
                "selection": tuple(selection),
                "information_state": information_state,
                "model_input_id": extracted.model_input_id,
                "decision_id": decision_id,
            }
        )

    def finalize(
        self,
        *,
        outcome: str | None,
        quality_weight: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Attach the episode's value target and build the validated records.

        ``quality_weight`` carries the matchup cap computed by the caller from
        the dataset so far (正典 §9.3)。値は record 単位ではなく局単位で決まる:
        同じ局の決定はすべて同じ相手に対するものだからである。
        """
        value_target = _VALUE_TARGET_BY_OUTCOME_V1.get(outcome or "", None)
        records: list[dict[str, Any]] = []
        index = 0
        for item in self.pending:
            if "__unavailable__" in item:
                # 正典 §9.3:「複数選択を理由に game または episode を黙って除外しない」。
                # カウントだけ残して payload を捨てると、どの決定がなぜ落ちたかを後から
                # 追えない。理由付きで別ファイルへ残す (training_eligible ではないので
                # dataset には入らない)。
                self.omissions.append({
                    "decision_index": index,
                    "episode_id_hash": self._episode_id_hash,
                    "teacher": item["__unavailable__"],
                })
                index += 1
                continue
            teacher_payload = hard_selection_teacher_payload_v1(
                teacher_id=self._teacher.opponent_id,
                teacher_revision=self._teacher.policy_hash[:16],
                model_input_id=item["model_input_id"],
                decision_id=item["decision_id"],
                information_state=item["information_state"],
                selection=item["selection"],
                value_target=value_target,
                quality_weight=quality_weight,
            )
            records.append(
                build_local_record_v2(
                    state=item["state"],
                    vocabulary=self._vocabulary,
                    episode_id_hash=self._episode_id_hash,
                    decision_index=index,
                    selection=item["selection"],
                    behavior={
                        "status": "unavailable",
                        "reason": "external teacher exposes no policy distribution",
                    },
                    teacher=teacher_payload,
                    student={
                        "status": "fallback",
                        "selection": [],
                        "scores": [],
                        "reason": "teacher collection does not score a student",
                    },
                    source={
                        "kind": self._source_kind,
                        "artifact_sha256": self._teacher.policy_hash,
                        "synthetic": False,
                        "synthetic_fields": [],
                        "training_eligible": True,
                        "usage_class": "qualified_training",
                        "permission_manifest_id": self._permission_manifest_id,
                    },
                    provenance={"source_record_ordinal": index},
                )
            )
            index += 1
        return records


def episode_id_hash_v1(*, run_name: str, game_index: int, seed: int) -> str:
    return hashlib.sha256(
        f"mage_ptcg:teacher-episode:v1\0{run_name}\0{game_index}\0{seed}".encode("utf-8")
    ).hexdigest()


def outcome_from_match_result_v1(result: Mapping[str, Any], *, subject_seat: int) -> str | None:
    """Read the subject's outcome from one engine match result.

    ``scripts.test_sim`` classifies a finished episode as ``DONE`` only when
    ``winner in (0, 1, 2)``, where ``2`` is the draw code.  Anything else
    (``STEP_LIMIT``, ``AGENT_*``, ``INCOMPLETE``) returns ``None``.

    ``None`` is kept distinct from a draw on purpose: an episode whose result
    is unknown must not be written as ``value_target=0.0``, which would teach
    the critic that an unfinished game was an even one.
    """
    if result.get("status") != "DONE":
        return None
    winner = result.get("winner")
    if type(winner) is not int or winner not in (0, 1, 2):
        return None
    if winner == 2:
        return "draw"
    return "win" if winner == subject_seat else "loss"


__all__ = [
    "DEFAULT_TEACHER_OUTPUT_BASE_V1",
    "REQUIRED_TEACHER_USAGE_V1",
    "TEACHER_SOURCE_KIND_V1",
    "CollectTeacherRecordsV1Error",
    "TeacherCollectionGameResultV1",
    "_TeacherRecordingAgentV1",
    "_atomic_write_json_v1",
    "_initialize_or_validate_collection_contract_v1",
    "_restore_omissions_v1",
    "_write_game_result_sidecar_v1",
    "_restore_game_sidecars_v1",
    "_scan_completed_games_v1",
    "_restore_game_stats_v1",
    "_finalize_collection_corpus_v1",
    "build_teacher_permission_manifest_v1",
    "teacher_source_kind_v1",
    "episode_id_hash_v1",
    "outcome_from_match_result_v1",
]

_log = logging.getLogger(__name__)

_GAME_FILE_RE = re.compile(r"^game-(\d+)\.jsonl$")


def _scan_completed_games_v1(records_dir: Path) -> set[int]:
    """Return only record files that prove a terminal labelled episode.

    A nonempty JSONL is not itself completion evidence: older collectors could
    write decisions for ``STEP_LIMIT``/other non-DONE games with
    ``value_target=None``.  Such a file must be rerun, never promoted to DONE
    merely because it exists.  Corrupt self-hashes fail closed instead of
    being overwritten by a resume.
    """
    completed: set[int] = set()
    if not records_dir.is_dir():
        return completed
    contract_v2 = (records_dir.parent / "collection_contract.json").is_file()
    sidecars: dict[int, dict[str, Any]] = {}
    if contract_v2:
        restored = _restore_game_sidecars_v1(records_dir)
        sidecars = {int(row["game_index"]): row for row in restored["rows"]}
        for game_index, sidecar in sidecars.items():
            if sidecar["status"] == "DONE":
                if sidecar["outcome"] not in ("win", "draw", "loss"):
                    raise CollectTeacherRecordsV1Error(
                        f"game-{game_index:06d} DONE sidecar has no terminal outcome"
                    )
                completed.add(game_index)
    for entry in records_dir.iterdir():
        match = _GAME_FILE_RE.match(entry.name)
        if not match or entry.stat().st_size <= 0:
            continue
        targets: set[float | None] = set()
        rows = 0
        try:
            with entry.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    rows += 1
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise CollectTeacherRecordsV1Error(
                            f"{entry.name}:{line_number} is not a record object"
                        )
                    if record.get("content_hash") != _record_content_hash(record):
                        raise CollectTeacherRecordsV1Error(
                            f"{entry.name}:{line_number} content_hash does not verify"
                        )
                    teacher = record.get("teacher")
                    if not isinstance(teacher, Mapping):
                        raise CollectTeacherRecordsV1Error(
                            f"{entry.name}:{line_number} has no teacher payload"
                        )
                    value = teacher.get("value_target")
                    if value is None:
                        targets.add(None)
                    elif (
                        type(value) in (int, float)
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                    ):
                        targets.add(float(value))
                    else:
                        raise CollectTeacherRecordsV1Error(
                            f"{entry.name}:{line_number} has invalid value_target"
                        )
        except json.JSONDecodeError as exc:
            raise CollectTeacherRecordsV1Error(
                f"{entry.name} is not valid JSONL: {exc}"
            ) from exc
        except OSError as exc:
            raise CollectTeacherRecordsV1Error(f"could not scan {entry}: {exc}") from exc
        if rows == 0:
            continue
        if None in targets:
            if len(targets) != 1:
                raise CollectTeacherRecordsV1Error(
                    f"{entry.name} mixes terminal and nonterminal value targets"
                )
            continue
        if len(targets) != 1:
            raise CollectTeacherRecordsV1Error(
                f"{entry.name} contains inconsistent terminal outcomes"
            )
        game_index = int(match.group(1))
        if contract_v2:
            if game_index not in completed:
                raise CollectTeacherRecordsV1Error(
                    f"{entry.name} exists without a DONE v2 sidecar"
                )
            expected_target = _VALUE_TARGET_BY_OUTCOME_V1[str(sidecars[game_index]["outcome"])]
            if targets != {expected_target}:
                raise CollectTeacherRecordsV1Error(
                    f"{entry.name} value_target does not match its DONE v2 sidecar"
                )
        else:
            completed.add(game_index)
    return completed


def _initialize_or_validate_collection_contract_v1(
    path: Path, contract: Mapping[str, object]
) -> dict[str, object]:
    """Atomically initialize one run identity, or require exact resume parity."""
    expected = dict(contract)
    if expected.get("schema_version") != COLLECTION_CONTRACT_SCHEMA_V2:
        raise CollectTeacherRecordsV1Error("unsupported collection contract schema")
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CollectTeacherRecordsV1Error(
                f"could not read existing collection contract: {exc}"
            ) from exc
        if existing != expected:
            raise CollectTeacherRecordsV1Error(
                "collection contract mismatch; run_name cannot mix collection inputs"
            )
        return expected
    if path.parent.exists() and any(path.parent.iterdir()):
        raise CollectTeacherRecordsV1Error(
            "collection output already contains artifacts but has no v2 contract"
        )
    _atomic_write_json_v1(path, expected)
    return expected


def _initialize_or_validate_collector_source_snapshot_v1(
    path: Path, *, expected_sha256: str
) -> None:
    """Preserve the exact dirty-worktree collector bytes used by this run."""
    current_source = Path(__file__).read_bytes()
    current_sha = hashlib.sha256(current_source).hexdigest()
    if current_sha != expected_sha256:
        raise CollectTeacherRecordsV1Error(
            "running collector source does not match its collection contract"
        )
    if path.exists():
        if not path.is_file() or _file_sha256_v1(path) != expected_sha256:
            raise CollectTeacherRecordsV1Error(
                "collector source snapshot does not match its collection contract"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp.", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(current_source)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _restore_omissions_v1(path: Path) -> list[dict[str, Any]]:
    """Load the prior omission ledger so resume cannot erase unsupported rows."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise CollectTeacherRecordsV1Error(
                        f"omissions.jsonl:{line_number} is not an object"
                    )
                rows.append(row)
    except json.JSONDecodeError as exc:
        raise CollectTeacherRecordsV1Error(f"omissions ledger is invalid: {exc}") from exc
    except OSError as exc:
        raise CollectTeacherRecordsV1Error(f"could not read omissions ledger: {exc}") from exc
    return rows


def _sidecar_path_v1(records_dir: Path, game_index: int) -> Path:
    return records_dir.parent / "game-results" / f"game-{game_index:06d}.result.json"


def _write_game_result_sidecar_v1(
    *,
    records_dir: Path,
    game_index: int,
    seed: int,
    seat: int,
    opponent_id: str,
    episode_id_hash: str,
    status: str,
    outcome: str | None,
    record_path: Path,
    record_count: int,
    unlabelled: int,
    omissions: Sequence[Mapping[str, object]],
    detail: str,
    subject_deck_sha256: str,
    teacher_policy_sha256: str,
    permission_manifest_id: str,
) -> Path:
    """Atomically publish the authoritative per-game resume descriptor."""
    if type(record_count) is not int or record_count < 0:
        raise CollectTeacherRecordsV1Error("record_count must be nonnegative")
    if status == "DONE" and outcome not in ("win", "draw", "loss"):
        raise CollectTeacherRecordsV1Error("DONE sidecar requires a terminal outcome")
    if status == "DONE" and record_count <= 0:
        raise CollectTeacherRecordsV1Error("DONE sidecar requires labelled records")
    if status != "DONE" and outcome is not None:
        raise CollectTeacherRecordsV1Error("non-DONE sidecar cannot carry an outcome")
    if type(unlabelled) is not int or unlabelled < 0 or unlabelled != len(omissions):
        raise CollectTeacherRecordsV1Error("unlabelled must equal omission row count")
    if record_count and not record_path.is_file():
        raise CollectTeacherRecordsV1Error("record file is missing for sidecar")
    record_sha = _file_sha256_v1(record_path) if record_count else None
    payload = {
        "schema_version": GAME_RESULT_SCHEMA_V2,
        "game_index": game_index,
        "seed": seed,
        "seat": seat,
        "opponent_id": opponent_id,
        "episode_id_hash": episode_id_hash,
        "status": status,
        "outcome": outcome,
        "record_path": str(record_path.resolve()),
        "record_sha256": record_sha,
        "record_count": record_count,
        "unlabelled": unlabelled,
        "omissions": [dict(row) for row in omissions],
        "detail": detail,
        "subject_deck_sha256": subject_deck_sha256,
        "teacher_policy_sha256": teacher_policy_sha256,
        "permission_manifest_id": permission_manifest_id,
    }
    attempt_dir = records_dir.parent / "game-attempts"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    existing_attempts = sorted(attempt_dir.glob(f"game-{game_index:06d}-attempt-*.json"))
    attempt_payload = {**payload, "attempt_ordinal": len(existing_attempts) + 1}
    _atomic_write_json_v1(
        attempt_dir
        / f"game-{game_index:06d}-attempt-{len(existing_attempts) + 1:04d}.json",
        attempt_payload,
    )
    path = _sidecar_path_v1(records_dir, game_index)
    _atomic_write_json_v1(path, payload)
    return path


def _read_game_sidecar_v1(path: Path, records_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CollectTeacherRecordsV1Error(f"could not read game sidecar {path.name}: {exc}") from exc
    required = {
        "schema_version", "game_index", "seed", "seat", "opponent_id",
        "episode_id_hash", "status", "outcome", "record_path", "record_sha256",
        "record_count", "unlabelled", "omissions", "detail",
        "subject_deck_sha256", "teacher_policy_sha256", "permission_manifest_id",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise CollectTeacherRecordsV1Error(f"game sidecar {path.name} schema is not closed")
    if payload["schema_version"] != GAME_RESULT_SCHEMA_V2:
        raise CollectTeacherRecordsV1Error(f"unsupported game sidecar schema: {path.name}")
    game_index = payload["game_index"]
    if type(game_index) is not int or _sidecar_path_v1(records_dir, game_index).resolve() != path.resolve():
        raise CollectTeacherRecordsV1Error(f"game sidecar path/index mismatch: {path.name}")
    omissions = payload["omissions"]
    if not isinstance(omissions, list) or any(not isinstance(row, dict) for row in omissions):
        raise CollectTeacherRecordsV1Error(f"game sidecar omissions are invalid: {path.name}")
    if type(payload["unlabelled"]) is not int or payload["unlabelled"] != len(omissions):
        raise CollectTeacherRecordsV1Error(f"game sidecar omission count mismatch: {path.name}")
    record_path = (records_dir / f"game-{game_index:06d}.jsonl").resolve()
    if Path(str(payload["record_path"])).resolve() != record_path:
        raise CollectTeacherRecordsV1Error(f"game sidecar record path mismatch: {path.name}")
    count = payload["record_count"]
    if type(count) is not int or count < 0:
        raise CollectTeacherRecordsV1Error(f"game sidecar record count is invalid: {path.name}")
    if count:
        if not record_path.is_file() or _file_sha256_v1(record_path) != payload["record_sha256"]:
            raise CollectTeacherRecordsV1Error(f"game sidecar record SHA mismatch: {path.name}")
        actual = sum(1 for line in record_path.open(encoding="utf-8") if line.strip())
        if actual != count:
            raise CollectTeacherRecordsV1Error(f"game sidecar record count mismatch: {path.name}")
    elif payload["record_sha256"] is not None:
        raise CollectTeacherRecordsV1Error(f"empty game sidecar has a record SHA: {path.name}")
    elif record_path.exists():
        raise CollectTeacherRecordsV1Error(
            f"empty/non-DONE game sidecar retains a record file: {path.name}"
        )
    return payload


def _restore_game_sidecars_v1(records_dir: Path) -> dict[str, object]:
    """Validate and aggregate all persisted game result descriptors."""
    sidecar_dir = records_dir.parent / "game-results"
    rows: list[dict[str, Any]] = []
    if sidecar_dir.is_dir():
        for path in sorted(sidecar_dir.glob("game-*.result.json")):
            rows.append(_read_game_sidecar_v1(path, records_dir))
    return {
        "rows": rows,
        "unlabelled": sum(int(row["unlabelled"]) for row in rows),
        "omissions": [omission for row in rows for omission in row["omissions"]],
        "faulted_attempts": sum(1 for row in rows if row["status"] != "DONE"),
    }


def _restore_game_stats_v1(
    records_dir: Path,
    completed_indices: set[int],
    opponent_ids: Sequence[str],
) -> tuple[dict[str, int], dict[str, int], int, int]:
    """完了済みゲームの record ファイルから matchup_counts, outcome_counts,
    total_records, total_unlabelled を復元する。

    各ゲームの opponent_id は game_index % len(opponent_ids) で決定論的に決まるので
    ファイル内容を parse せずに特定できる。record 数はファイル内の行数で数える。
    outcome は record の teacher.value_target から逆引きする。
    """
    matchup_counts: dict[str, int] = {}
    outcome_counts: dict[str, int] = {}
    total_records = 0
    contract_v2 = (records_dir.parent / "collection_contract.json").is_file()
    sidecar_state = _restore_game_sidecars_v1(records_dir) if contract_v2 else None
    total_unlabelled = (
        int(sidecar_state["unlabelled"])
        if sidecar_state is not None
        else len(_restore_omissions_v1(records_dir.parent / "omissions.jsonl"))
    )
    sidecars = (
        {int(row["game_index"]): row for row in sidecar_state["rows"]}
        if sidecar_state is not None
        else {}
    )

    for game_index in sorted(completed_indices):
        path = records_dir / f"game-{game_index:06d}.jsonl"
        opponent_id = opponent_ids[game_index % len(opponent_ids)]
        n_records = 0
        outcome_key: str | None = (
            str(sidecars[game_index]["outcome"])
            if game_index in sidecars
            else None
        )
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    n_records += 1
                    # outcome は全 record 共通なので最初の 1 件から取れば十分
                    if outcome_key is None:
                        try:
                            record = json.loads(line)
                            vt = record.get("teacher", {}).get("value_target")
                            if vt == 1.0:
                                outcome_key = "win"
                            elif vt == -1.0:
                                outcome_key = "loss"
                            elif vt == 0.0:
                                outcome_key = "draw"
                            else:
                                outcome_key = "unknown"
                        except (json.JSONDecodeError, AttributeError):
                            outcome_key = "unknown"
        except OSError:
            continue

        matchup_counts[opponent_id] = matchup_counts.get(opponent_id, 0) + n_records
        total_records += n_records
        if outcome_key:
            outcome_counts[outcome_key] = outcome_counts.get(outcome_key, 0) + 1

    return matchup_counts, outcome_counts, total_records, total_unlabelled


def _merge_worker_matchup_counts_v1(
    restored_counts: Mapping[str, int], worker_rows: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    """Merge resumed record counts with rows produced by parallel workers."""
    counts = dict(restored_counts)
    for row in worker_rows:
        opponent_id = row.get("opponent_id")
        n_records = row.get("n_records")
        if not isinstance(opponent_id, str) or not opponent_id:
            raise CollectTeacherRecordsV1Error("worker row has invalid opponent_id")
        if type(n_records) is not int or n_records < 0:
            raise CollectTeacherRecordsV1Error("worker row has invalid n_records")
        counts[opponent_id] = counts.get(opponent_id, 0) + n_records
    return counts


def _collection_manifest_stats_v1(
    *,
    already_done: set[int],
    results: Sequence[TeacherCollectionGameResultV1],
    opponent_count: int,
) -> dict[str, object]:
    """Build completion counts without dropping records restored on resume."""
    result_indices = {row.game_index for row in results}
    if already_done.intersection(result_indices):
        raise CollectTeacherRecordsV1Error(
            "resumed game indices must not also appear in new results"
        )
    completed_indices = set(already_done)
    completed_indices.update(row.game_index for row in results if row.status == "DONE")
    attempted_indices = set(already_done).union(result_indices)
    return {
        "games_completed": len(completed_indices),
        "games_faulted": sum(1 for row in results if row.status == "faulted"),
        "games_other_status": sorted({
            row.status for row in results if row.status not in ("DONE", "faulted")
        }),
        "seat_counts": {
            "subject_first": sum(
                1 for game_index in attempted_indices
                if seat_for_game_v1(game_index, opponent_count) == 0
            ),
            "subject_second": sum(
                1 for game_index in attempted_indices
                if seat_for_game_v1(game_index, opponent_count) == 1
            ),
        },
    }


def seat_for_game_v1(game_index: int, opponent_count: int) -> int:
    """先手/後手を、相手の巡回と独立に決める。

    従来は ``seat = game_index % 2`` かつ ``opponent = ids[game_index % n]`` で、
    ``n`` が偶数だと両者が完全にエイリアスした。16 相手では ``game_index`` と
    ``game_index + 16`` の偶奇が一致するため、**相手 16 体すべてが常に同じ座席
    でしか当たらない**。manifest の ``seat_counts`` は 150/150 と均等に見えるが、
    matchup ごとには 8 体が常に先手・8 体が常に後手であり、先手の価値が大きい
    このゲームでは matchup ごとの成績が座席と交絡する。

    巡回の周回番号で座席を決めれば、相手の巡回とは独立になり、各相手が両座席を
    同数ずつ受け持つ (局数が ``2 * opponent_count`` の倍数のとき厳密に均等)。
    """
    if opponent_count < 1:
        raise CollectTeacherRecordsV1Error("opponent_count must be positive")
    return (game_index // opponent_count) % 2


def seed_agent_randomness_v1(seed: int) -> None:
    """Remove the *agent-side* source of run-to-run variance.  Does not make runs
    reproducible -- see below.

    22 of the 66 pooled agents import ``random`` and call it directly (R7's
    ``_legal_fallback`` uses ``random.sample``), and nothing seeded it.  Seeding
    here rather than editing each agent keeps the pooled policies exactly as
    published.

    **This is not sufficient for reproducibility, and `--base-seed` does not
    provide it.**  Measured on 12 games of the same command:

    - two sequential runs:      12/12 games differed
    - sequential vs 8 workers:  12/12 games differed
    - with this seeding, and with ``PYTHONHASHSEED=0``: still 12/12

    The remaining source is below Python -- the native ``cg`` engine's own
    randomness is not fully determined by the ``seed`` argument.  Two
    consequences worth stating plainly:

    1. Parallel collection introduces **no additional** non-determinism.  It
       differs from a sequential run exactly as much as two sequential runs
       differ from each other, so parallelising costs nothing in reproducibility
       that was not already lost.
    2. Comparisons between runs must rest on sample size and intervals, never on
       "same seed, so the difference must be the change".  Any such reasoning
       against these runs is unsound.
    """
    import random as _random

    _random.seed(seed)
    try:
        import numpy as _numpy
    except ImportError:
        return
    _numpy.random.seed(seed % (2 ** 32))


def _play_one_game_v1(payload: dict) -> dict:
    """Play exactly one game and write its records.  Runs in a worker process.

    Everything the game needs is derived from ``game_index`` alone (seed, seat,
    opponent), so games are independent and can run in any order or in parallel.

    The matchup weight is deliberately *not* applied here.  It depends on the
    whole dataset's composition, which no single worker can know; the parent
    applies it once every game is in (see ``_apply_matchup_weights_v1``).  That
    also removes an order dependence the sequential version had: a game's weight
    used to depend on how many records happened to precede it.
    """
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        _build_actor_pool_deck_binding_v1,
        engine_identity_v1,
    )
    from mage_ptcg.meta_specialist.decks import DeckQualificationError
    from mage_ptcg.meta_specialist.opponent_pool_v1 import (
        build_opponent_agent_factory_v1, default_pool_root_v1,
        load_opponent_pool_v1, resolve_opponent_v1,
    )
    from scripts.test_sim import run_match

    game_index = payload["game_index"]
    deck_path = Path(payload["deck_path"])
    expected_deck_sha = payload.get("expected_deck_sha256")
    actual_deck_sha = _file_sha256_v1(deck_path)
    if expected_deck_sha is not None and actual_deck_sha != expected_deck_sha:
        raise CollectTeacherRecordsV1Error(
            "worker subject deck bytes changed after the collection contract was sealed"
        )
    subject_deck_sha = str(expected_deck_sha or actual_deck_sha)
    if (
        payload.get("collector_source_sha256") is not None
        and _file_sha256_v1(Path(__file__))
        != payload["collector_source_sha256"]
    ):
        raise CollectTeacherRecordsV1Error(
            "worker collector source changed after the collection contract was sealed"
        )
    pool = load_opponent_pool_v1(Path(payload["pool_root"]))
    teacher = resolve_opponent_v1(pool, payload["teacher_id"], subject_deck_csv_path=str(deck_path))
    try:
        _q, _l, vocabulary = _build_actor_pool_deck_binding_v1(
            archetype_id=payload["archetype_id"], deck_csv_path=deck_path,
            source_commit=payload["source_commit"],
        )
    except DeckQualificationError as exc:
        if str(exc) != "CABT legality must return (True, nonempty evidence)":
            raise
        row = {
            "game_index": game_index,
            "seat": seat_for_game_v1(game_index, len(payload["opponent_ids"])),
            "opponent_id": payload["opponent_ids"][
                game_index % len(payload["opponent_ids"])
            ],
            "status": "faulted",
            "outcome": None,
            "n_records": 0,
            "unlabelled": 0,
            "omissions": [],
            "detail": f"{type(exc).__name__}: {exc}",
        }
        record_path = Path(payload["records_dir"]) / f"game-{game_index:06d}.jsonl"
        record_path.unlink(missing_ok=True)
        _write_game_result_sidecar_v1(
            records_dir=Path(payload["records_dir"]), game_index=game_index,
            seed=payload["base_seed"] + game_index,
            seat=row["seat"], opponent_id=row["opponent_id"],
            episode_id_hash=episode_id_hash_v1(
                run_name=payload["run_name"], game_index=game_index,
                seed=payload["base_seed"] + game_index,
            ),
            status="faulted", outcome=None,
            record_path=record_path,
            record_count=0, unlabelled=0, omissions=(), detail=row["detail"],
            subject_deck_sha256=subject_deck_sha,
            teacher_policy_sha256=teacher.policy_hash,
            permission_manifest_id=str(payload["permission_manifest_id"]),
        )
        return row
    _engine, worker_engine_path, worker_engine_sha = engine_identity_v1()
    if (
        payload.get("engine_entry_point") != worker_engine_path
        or payload.get("engine_source_sha256") != worker_engine_sha
        or payload.get("vocabulary_manifest") != vocabulary.to_manifest_dict()
    ):
        raise CollectTeacherRecordsV1Error(
            "worker engine or vocabulary does not match the collection contract"
        )

    seed = payload["base_seed"] + game_index
    opponent_ids = payload["opponent_ids"]
    seat = seat_for_game_v1(game_index, len(opponent_ids))
    opponent_id = opponent_ids[game_index % len(opponent_ids)]
    opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(deck_path))
    episode = episode_id_hash_v1(
        run_name=payload["run_name"], game_index=game_index, seed=seed
    )
    recorder = _TeacherRecordingAgentV1(
        teacher_agent=build_opponent_agent_factory_v1(teacher)(None, seed),
        teacher=teacher, vocabulary=vocabulary, episode_id_hash=episode,
        permission_manifest_id=payload["permission_manifest_id"],
        source_kind=payload.get(
            "teacher_source_kind", teacher_source_kind_v1(teacher)
        ),
    )
    opponent_factory = (
        None if opponent.is_mirror else build_opponent_agent_factory_v1(opponent)
    )
    subject_first = seat == 0
    opponent_name = "rule" if opponent.is_mirror else opponent.opponent_id
    seed_agent_randomness_v1(seed)
    try:
        result = run_match(
            deck_a_path=str(deck_path) if subject_first else opponent.deck_csv_path,
            deck_b_path=opponent.deck_csv_path if subject_first else str(deck_path),
            agent_a_name="teacher" if subject_first else opponent_name,
            agent_b_name=opponent_name if subject_first else "teacher",
            seed=seed, max_steps=payload["max_steps"],
            output_dir=str(Path(payload["output_root"]) / "matches" / f"game-{game_index:06d}"),
            save_html=False, save_result=False,
            agent_a_factory=(lambda d, s: recorder) if subject_first else opponent_factory,
            agent_b_factory=opponent_factory if subject_first else (lambda d, s: recorder),
        )
    except Exception as exc:  # engine faults are per-game, not per-run
        recorder.finalize(outcome=None, quality_weight=1.0)
        record_path = Path(payload["records_dir"]) / f"game-{game_index:06d}.jsonl"
        record_path.unlink(missing_ok=True)
        row = {
            "game_index": game_index, "seat": seat, "opponent_id": opponent_id,
            "status": "faulted", "outcome": None, "n_records": 0,
            "unlabelled": recorder.unlabelled, "omissions": list(recorder.omissions),
            "detail": f"{type(exc).__name__}: {exc}",
        }
        _write_game_result_sidecar_v1(
            records_dir=Path(payload["records_dir"]), game_index=game_index,
            seed=seed, seat=seat, opponent_id=opponent_id,
            episode_id_hash=episode, status="faulted", outcome=None,
            record_path=record_path,
            record_count=0, unlabelled=recorder.unlabelled,
            omissions=tuple(recorder.omissions), detail=row["detail"],
            subject_deck_sha256=subject_deck_sha,
            teacher_policy_sha256=teacher.policy_hash,
            permission_manifest_id=str(payload["permission_manifest_id"]),
        )
        return row

    outcome = outcome_from_match_result_v1(result, subject_seat=seat)
    records = recorder.finalize(outcome=outcome, quality_weight=1.0)
    path = Path(payload["records_dir"]) / f"game-{game_index:06d}.jsonl"
    completed_with_labels = (
        outcome is not None and result.get("status") == "DONE" and bool(records)
    )
    if completed_with_labels:
        _atomic_write_jsonl_v1(path, records)
    else:
        path.unlink(missing_ok=True)
    sidecar_status = (
        "DONE"
        if completed_with_labels
        else "NO_LABELLED_RECORDS"
        if outcome is not None and result.get("status") == "DONE"
        else str(result.get("status"))
    )
    _write_game_result_sidecar_v1(
        records_dir=Path(payload["records_dir"]), game_index=game_index,
        seed=seed, seat=seat, opponent_id=opponent_id,
        episode_id_hash=episode, status=sidecar_status,
        outcome=outcome if sidecar_status == "DONE" else None,
        record_path=path,
        record_count=len(records) if sidecar_status == "DONE" else 0,
        unlabelled=recorder.unlabelled, omissions=tuple(recorder.omissions),
        detail="", subject_deck_sha256=subject_deck_sha,
        teacher_policy_sha256=teacher.policy_hash,
        permission_manifest_id=str(payload["permission_manifest_id"]),
    )
    return {
        "game_index": game_index, "seat": seat, "opponent_id": opponent_id,
        "status": sidecar_status,
        "outcome": outcome if sidecar_status == "DONE" else None,
        "n_records": len(records) if sidecar_status == "DONE" else 0,
        "unlabelled": recorder.unlabelled,
        "omissions": list(recorder.omissions), "detail": "",
    }


def _apply_matchup_weights_v1(
    records_dir: Path, per_game: list[dict], *,
    opponent_ids: Sequence[str],
    matchup_cap_fraction: float = DEFAULT_MATCHUP_CAP_FRACTION_V1,
) -> int:
    """Rewrite every game's records with the weight its matchup finally earned.

    The cap is a property of the corpus, so both the counts and the games it is
    applied to must cover **every** game on disk, not the ones this invocation
    happened to play.

    Two defects this fixes, both found by a sealed corpus refusing to load:

    - Counting only this run's games made a resumed run measure shares against a
      fraction of the corpus.  A resume that played 3 games saw one opponent hold
      far more than the 25% cap and wrote a reduced weight, while the full corpus
      gave that opponent 8.4%.  242 records of a 249,299-record corpus were
      down-weighted for no reason.
    - Rewriting ``quality_weight`` without recomputing ``content_hash`` left the
      record's own integrity hash describing the pre-edit content, so
      ``record content_hash does not verify`` and the corpus could not be sealed
      at all.
    """
    completed = sorted(_scan_completed_games_v1(records_dir))
    counts: dict[str, int] = {}
    total = 0
    for game_index in completed:
        opponent_id = opponent_ids[game_index % len(opponent_ids)]
        try:
            with open(records_dir / f"game-{game_index:06d}.jsonl", encoding="utf-8") as handle:
                written = sum(1 for line in handle if line.strip())
        except OSError:
            continue
        counts[opponent_id] = counts.get(opponent_id, 0) + written
        total += written

    rewritten = 0
    for game_index in completed:
        opponent_id = opponent_ids[game_index % len(opponent_ids)]
        weight = quality_weight_for_v1(
            opponent_id=opponent_id, matchup_counts=counts,
            total_records=total, matchup_cap_fraction=matchup_cap_fraction,
        )
        path = records_dir / f"game-{game_index:06d}.jsonl"
        sidecar_payload: dict[str, Any] | None = None
        if (records_dir.parent / "collection_contract.json").is_file():
            sidecar_path = _sidecar_path_v1(records_dir, game_index)
            sidecar_payload = _read_game_sidecar_v1(sidecar_path, records_dir)
            if sidecar_payload["status"] != "DONE":
                raise CollectTeacherRecordsV1Error(
                    f"game-{game_index:06d} finalization requires a DONE sidecar"
                )
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out: list[str] = []
        changed = False
        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            teacher = record.get("teacher")
            if isinstance(teacher, dict) and teacher.get("quality_weight") != weight:
                teacher["quality_weight"] = weight
                # The record carries its own integrity hash over its content.
                # Editing the weight without recomputing it leaves a record that
                # describes content it no longer has, and every reader that
                # verifies the hash refuses the whole corpus.
                record["content_hash"] = _record_content_hash(record)
                changed = True
            out.append(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")))
        if not changed:
            continue
        _atomic_write_jsonl_v1(path, tuple(json.loads(line) for line in out))
        if sidecar_payload is not None:
            # Keep the immutable attempt ledger aligned with the canonicalized
            # corpus bytes.  Rewriting only the current sidecar would make the
            # latest immutable attempt describe the pre-cap record SHA.
            _write_game_result_sidecar_v1(
                records_dir=records_dir,
                game_index=game_index,
                seed=int(sidecar_payload["seed"]),
                seat=int(sidecar_payload["seat"]),
                opponent_id=str(sidecar_payload["opponent_id"]),
                episode_id_hash=str(sidecar_payload["episode_id_hash"]),
                status="DONE",
                outcome=str(sidecar_payload["outcome"]),
                record_path=path,
                record_count=len(out),
                unlabelled=int(sidecar_payload["unlabelled"]),
                omissions=tuple(sidecar_payload["omissions"]),
                detail="corpus-global matchup weight finalization",
                subject_deck_sha256=str(sidecar_payload["subject_deck_sha256"]),
                teacher_policy_sha256=str(sidecar_payload["teacher_policy_sha256"]),
                permission_manifest_id=str(sidecar_payload["permission_manifest_id"]),
            )
        rewritten += 1
    return rewritten


def _finalize_collection_corpus_v1(
    records_dir: Path,
    *,
    opponent_ids: Sequence[str],
) -> dict[str, object]:
    """Apply the corpus-global cap and reconstruct canonical final statistics.

    This is deliberately one shared post-pass for both serial and parallel
    collection.  Online order-dependent weights are never allowed to become a
    final sealed corpus merely because ``workers=1`` was selected.
    """
    if not opponent_ids:
        raise CollectTeacherRecordsV1Error("opponent_ids must be non-empty")
    rewritten = _apply_matchup_weights_v1(
        records_dir, [], opponent_ids=opponent_ids
    )
    completed = _scan_completed_games_v1(records_dir)
    matchup_counts, outcome_counts, total_records, total_unlabelled = (
        _restore_game_stats_v1(records_dir, completed, opponent_ids)
    )
    return {
        "completed_indices": completed,
        "matchup_counts": matchup_counts,
        "outcome_counts": outcome_counts,
        "records": total_records,
        "unlabelled": total_unlabelled,
        "rewritten_games": rewritten,
    }


def _collection_contract_payload_v1(
    *,
    run_name: str,
    archetype_id: str,
    deck_path: Path,
    teacher: OpponentInstanceV1,
    opponents: Sequence[OpponentInstanceV1],
    opponent_ids: Sequence[str],
    num_games: int,
    base_seed: int,
    max_steps: int,
    source_commit: str,
    decision_ref: str,
    permission_manifest_id: str,
    pool_root: Path,
    allowed_usages: Sequence[str],
    engine_entry_point: str,
    engine_source_sha256: str,
    vocabulary_manifest: Mapping[str, object],
    collector_source_snapshot_path: Path,
    permission_manifest: Mapping[str, object],
) -> dict[str, object]:
    def asset(instance: OpponentInstanceV1) -> dict[str, object]:
        policy_sha = (
            _file_sha256_v1(instance.policy_path) if instance.policy_path else None
        )
        return {
            "opponent_id": instance.opponent_id,
            "policy_sha256": policy_sha,
            "deck_file_sha256": _file_sha256_v1(instance.deck_csv_path),
            "canonical_deck_hash": instance.canonical_deck_hash,
            "source": instance.source,
            "usage_boundary": instance.usage_boundary,
        }

    return {
        "schema_version": COLLECTION_CONTRACT_SCHEMA_V2,
        "run_name": run_name,
        "archetype_id": archetype_id,
        "subject_deck_csv_path": str(deck_path.resolve()),
        "subject_deck_file_sha256": _file_sha256_v1(deck_path),
        "teacher": asset(teacher),
        "teacher_source_kind": teacher_source_kind_v1(teacher),
        "opponent_ids": list(opponent_ids),
        "opponents": [asset(instance) for instance in opponents],
        "games_requested": num_games,
        "base_seed": base_seed,
        "max_steps": max_steps,
        "source_commit": source_commit,
        "decision_ref": decision_ref,
        "permission_manifest_id": permission_manifest_id,
        "permission_content_hash": permission_manifest.get("content_hash"),
        "permission_trusted_bytes_sha256": hashlib.sha256(
            canonical_json_bytes_v2(dict(permission_manifest))
        ).hexdigest(),
        "allowed_usages": sorted(set(allowed_usages)),
        "pool_root": str(pool_root.resolve()),
        "pool_manifest_sha256": _file_sha256_v1(pool_root / "pool_manifest.json"),
        "engine_entry_point": engine_entry_point,
        "engine_source_sha256": engine_source_sha256,
        "feature_schema_hash": FEATURE_SCHEMA_HASH_V1,
        "vocabulary_manifest": dict(vocabulary_manifest),
        "collector_source_sha256": _file_sha256_v1(Path(__file__)),
        "collector_source_snapshot_path": str(
            collector_source_snapshot_path.resolve()
        ),
        "seat_schedule": "seat=(game_index//opponent_count)%2",
        "opponent_schedule": "opponent_ids[game_index%opponent_count]",
        "matchup_cap_fraction": DEFAULT_MATCHUP_CAP_FRACTION_V1,
    }


def run_collect_teacher_records_v1(
    *,
    progress_path: str = "",
    workers: int = 1,
    archetype_id: str,
    subject_deck_csv_path: str | Path,
    teacher_id: str,
    opponent_ids: Sequence[str],
    num_games: int,
    base_seed: int,
    run_name: str,
    allowed_usages: Sequence[str],
    decision_ref: str,
    source_commit: str,
    max_steps: int = 2000,
    pool_root: Path | None = None,
    output_base_dir: Path = DEFAULT_TEACHER_OUTPUT_BASE_V1,
) -> dict[str, Any]:
    """Play ``num_games`` with the teacher piloting the subject deck, and record them.

    Seats alternate so both the subject-first and subject-second orders are
    represented; opponents cycle through ``opponent_ids`` so one strong or weak
    matchup cannot dominate the dataset (正典 §9.3 の matchup cap の下地).
    """
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        _build_actor_pool_deck_binding_v1,
        engine_identity_v1,
    )
    from mage_ptcg.meta_specialist.opponent_pool_v1 import (
        default_pool_root_v1,
        load_opponent_pool_v1,
        resolve_opponent_v1,
    )
    from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1
    from scripts.test_sim import run_match

    if num_games <= 0:
        raise CollectTeacherRecordsV1Error("num_games must be positive")
    if not opponent_ids:
        raise CollectTeacherRecordsV1Error("at least one opponent id is required")

    deck_path = Path(subject_deck_csv_path)
    root = Path(pool_root) if pool_root is not None else default_pool_root_v1(_REPO_ROOT)
    pool = load_opponent_pool_v1(root)
    teacher = resolve_opponent_v1(pool, teacher_id, subject_deck_csv_path=str(deck_path))
    if teacher.is_mirror:
        raise CollectTeacherRecordsV1Error(
            "the mirror instance is the engine's built-in rule agent, not a teacher "
            "to distil from"
        )
    permission = build_teacher_permission_manifest_v1(
        teacher, allowed_usages=allowed_usages, decision_ref=decision_ref
    )
    source_kind = teacher_source_kind_v1(teacher)
    _qualified, _lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=archetype_id, deck_csv_path=deck_path, source_commit=source_commit
    )
    _engine, engine_entry_point, engine_source_sha256 = engine_identity_v1()

    output_root = Path(output_base_dir) / run_name
    resolved_opponents = tuple(
        resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(deck_path))
        for opponent_id in opponent_ids
    )
    contract = _collection_contract_payload_v1(
        run_name=run_name,
        archetype_id=archetype_id,
        deck_path=deck_path,
        teacher=teacher,
        opponents=resolved_opponents,
        opponent_ids=opponent_ids,
        num_games=num_games,
        base_seed=base_seed,
        max_steps=max_steps,
        source_commit=source_commit,
        decision_ref=decision_ref,
        permission_manifest_id=permission["permission_manifest_id"],
        pool_root=root,
        allowed_usages=allowed_usages,
        engine_entry_point=engine_entry_point,
        engine_source_sha256=engine_source_sha256,
        vocabulary_manifest=vocabulary.to_manifest_dict(),
        collector_source_snapshot_path=output_root / "collector_source_snapshot.py",
        permission_manifest=permission,
    )
    contract_path = output_root / "collection_contract.json"
    _initialize_or_validate_collection_contract_v1(contract_path, contract)
    _initialize_or_validate_collector_source_snapshot_v1(
        output_root / "collector_source_snapshot.py",
        expected_sha256=str(contract["collector_source_sha256"]),
    )
    records_dir = output_root / "records"
    records_dir.mkdir(parents=True, exist_ok=True)

    # --- resume: 既に完了済みのゲームをスキャンして復元 ---
    already_done = _scan_completed_games_v1(records_dir)
    # matches/ 側も確認: record が無くても match ディレクトリだけ残っているケースは
    # 未完了とみなし再実行する (records が正)

    results: list[TeacherCollectionGameResultV1] = []
    restored_sidecars = _restore_game_sidecars_v1(records_dir)
    omissions: list[dict[str, Any]] = list(restored_sidecars["omissions"])

    if already_done:
        matchup_counts, outcome_counts, total_records, total_unlabelled = (
            _restore_game_stats_v1(records_dir, already_done, opponent_ids)
        )
        _log.info(
            "[teacher-collect] resume: %d / %d ゲーム完了済み、スキップします "
            "(records=%d, outcomes=%s)",
            len(already_done), num_games, total_records, outcome_counts,
        )
    else:
        matchup_counts = {}
        outcome_counts = {}
        total_records = 0
        total_unlabelled = 0

    games_to_run = num_games - len(already_done)
    reporter = ProgressReporterV1(
        total=num_games, desc=f"teacher-collect {run_name}",
        progress_path=progress_path or None,
    )
    # 既に完了した分を進捗に反映
    if already_done:
        reporter.update(
            len(already_done),
            records=total_records, unlabelled=total_unlabelled,
            faults=0,
            win=outcome_counts.get("win", 0), loss=outcome_counts.get("loss", 0),
        )
        reporter.note(
            f"[teacher-collect] resume: {len(already_done)}/{num_games} 完了済み、"
            f"残り {games_to_run} ゲームを収集します"
        )
    else:
        reporter.note(
            f"[teacher-collect] start lane={archetype_id} teacher={teacher_id} "
            f"games={num_games} opponents={len(opponent_ids)}"
        )
    pending_indices = [i for i in range(num_games) if i not in already_done]
    if workers > 1 and pending_indices:
        payload_base = {
            "deck_path": str(deck_path), "pool_root": str(root),
            "expected_deck_sha256": contract["subject_deck_file_sha256"],
            "engine_entry_point": contract["engine_entry_point"],
            "engine_source_sha256": contract["engine_source_sha256"],
            "vocabulary_manifest": contract["vocabulary_manifest"],
            "collector_source_sha256": contract["collector_source_sha256"],
            "teacher_id": teacher.opponent_id, "archetype_id": archetype_id,
            "teacher_source_kind": source_kind,
            "source_commit": source_commit, "base_seed": base_seed,
            "opponent_ids": list(opponent_ids), "run_name": run_name,
            "permission_manifest_id": permission["permission_manifest_id"],
            "max_steps": max_steps, "output_root": str(output_root),
            "records_dir": str(records_dir),
        }
        # Processes, not threads: loading an opponent's policy rebinds entries in
        # `sys.modules`, which is process-global and not thread-safe.  "spawn"
        # rather than fork because the parent may already hold torch's threads.
        context = multiprocessing.get_context("spawn")
        rows: list[dict] = []
        with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool_executor:
            futures = {
                pool_executor.submit(_play_one_game_v1, dict(payload_base, game_index=index)): index
                for index in pending_indices
            }
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                omissions.extend(row.pop("omissions", []))
                total_records += row["n_records"]
                total_unlabelled += row["unlabelled"]
                key = row["outcome"] or ("faulted" if row["status"] == "faulted" else "unknown")
                if row["status"] == "DONE":
                    outcome_counts[key] = outcome_counts.get(key, 0) + 1
                reporter.update(
                    1, records=total_records, unlabelled=total_unlabelled,
                    faults=sum(1 for r in rows if r["status"] == "faulted"),
                    win=outcome_counts.get("win", 0), loss=outcome_counts.get("loss", 0),
                )
        rows.sort(key=lambda r: r["game_index"])
        matchup_counts = _merge_worker_matchup_counts_v1(matchup_counts, rows)
        for row in rows:
            results.append(TeacherCollectionGameResultV1(
                game_index=row["game_index"], seat=row["seat"],
                opponent_id=row["opponent_id"], status=row["status"],
                outcome=row["outcome"], records=(), unlabelled=row["unlabelled"],
                detail=row.get("detail", ""),
            ))
        pending_indices = []

    for game_index in pending_indices:
        seed = base_seed + game_index
        seat = seat_for_game_v1(game_index, len(opponent_ids))
        opponent_id = opponent_ids[game_index % len(opponent_ids)]
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(deck_path))
        episode = episode_id_hash_v1(run_name=run_name, game_index=game_index, seed=seed)

        recorder = _TeacherRecordingAgentV1(
            teacher_agent=build_opponent_agent_factory_v1(teacher)(None, seed),
            teacher=teacher,
            vocabulary=vocabulary,
            episode_id_hash=episode,
            permission_manifest_id=permission["permission_manifest_id"],
            source_kind=source_kind,
        )
        opponent_factory = (
            None if opponent.is_mirror else build_opponent_agent_factory_v1(opponent)
        )
        subject_first = seat == 0
        opponent_name = "rule" if opponent.is_mirror else opponent.opponent_id
        seed_agent_randomness_v1(seed)
        try:
            result = run_match(
                deck_a_path=str(deck_path) if subject_first else opponent.deck_csv_path,
                deck_b_path=opponent.deck_csv_path if subject_first else str(deck_path),
                agent_a_name="teacher" if subject_first else opponent_name,
                agent_b_name=opponent_name if subject_first else "teacher",
                seed=seed,
                max_steps=max_steps,
                output_dir=str(output_root / "matches" / f"game-{game_index:06d}"),
                save_html=False,
                save_result=False,
                agent_a_factory=(lambda d, s: recorder) if subject_first else opponent_factory,
                agent_b_factory=opponent_factory if subject_first else (lambda d, s: recorder),
            )
        except Exception as exc:  # engine faults are per-game, not per-run
            recorder.finalize(outcome=None, quality_weight=1.0)
            record_path = records_dir / f"game-{game_index:06d}.jsonl"
            record_path.unlink(missing_ok=True)
            _write_game_result_sidecar_v1(
                records_dir=records_dir, game_index=game_index, seed=seed,
                seat=seat, opponent_id=opponent_id, episode_id_hash=episode,
                status="faulted", outcome=None, record_path=record_path,
                record_count=0, unlabelled=recorder.unlabelled,
                omissions=tuple(recorder.omissions),
                detail=f"{type(exc).__name__}: {exc}",
                subject_deck_sha256=str(contract["subject_deck_file_sha256"]),
                teacher_policy_sha256=teacher.policy_hash,
                permission_manifest_id=permission["permission_manifest_id"],
            )
            results.append(
                TeacherCollectionGameResultV1(
                    game_index=game_index, seat=seat, opponent_id=opponent_id,
                    status="faulted", outcome=None, records=(), unlabelled=recorder.unlabelled,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        outcome = outcome_from_match_result_v1(result, subject_seat=seat)
        weight = quality_weight_for_v1(
            opponent_id=opponent_id,
            matchup_counts=matchup_counts,
            total_records=total_records,
        )
        records = recorder.finalize(outcome=outcome, quality_weight=weight)
        matchup_counts[opponent_id] = matchup_counts.get(opponent_id, 0) + len(records)
        omissions.extend(recorder.omissions)
        outcome_counts[outcome or "unknown"] = outcome_counts.get(outcome or "unknown", 0) + 1
        total_records += len(records)
        total_unlabelled += recorder.unlabelled
        path = records_dir / f"game-{game_index:06d}.jsonl"
        completed_with_labels = (
            outcome is not None and result.get("status") == "DONE" and bool(records)
        )
        if completed_with_labels:
            _atomic_write_jsonl_v1(path, records)
        else:
            path.unlink(missing_ok=True)
        sidecar_status = (
            "DONE" if completed_with_labels
            else "NO_LABELLED_RECORDS"
            if outcome is not None and result.get("status") == "DONE"
            else str(result.get("status"))
        )
        _write_game_result_sidecar_v1(
            records_dir=records_dir, game_index=game_index, seed=seed,
            seat=seat, opponent_id=opponent_id, episode_id_hash=episode,
            status=sidecar_status,
            outcome=outcome if sidecar_status == "DONE" else None,
            record_path=path,
            record_count=len(records) if sidecar_status == "DONE" else 0,
            unlabelled=recorder.unlabelled, omissions=tuple(recorder.omissions),
            detail="", subject_deck_sha256=str(contract["subject_deck_file_sha256"]),
            teacher_policy_sha256=teacher.policy_hash,
            permission_manifest_id=permission["permission_manifest_id"],
        )
        results.append(
            TeacherCollectionGameResultV1(
                game_index=game_index, seat=seat, opponent_id=opponent_id,
                status=sidecar_status,
                outcome=outcome if sidecar_status == "DONE" else None,
                records=tuple(records) if sidecar_status == "DONE" else (),
                unlabelled=recorder.unlabelled,
            )
        )
        reporter.update(
            1, records=total_records, unlabelled=total_unlabelled,
            faults=sum(1 for r in results if r.status == "faulted"),
            win=outcome_counts.get("win", 0), loss=outcome_counts.get("loss", 0),
        )

    finalized = _finalize_collection_corpus_v1(records_dir, opponent_ids=opponent_ids)
    matchup_counts = dict(finalized["matchup_counts"])
    outcome_counts = dict(finalized["outcome_counts"])
    total_records = int(finalized["records"])
    rewritten = int(finalized["rewritten_games"])
    if rewritten:
        reporter.note(f"[teacher-collect] matchup cap を {rewritten} 局へ適用")
    unique_omissions: list[dict[str, Any]] = []
    omission_keys: set[str] = set()
    for omission in omissions:
        key = json.dumps(omission, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in omission_keys:
            omission_keys.add(key)
            unique_omissions.append(omission)
    omissions = unique_omissions
    total_unlabelled = len(omissions)
    reporter.close()
    completion_stats = _collection_manifest_stats_v1(
        already_done=already_done,
        results=results,
        opponent_count=len(opponent_ids),
    )
    manifest = {
        "schema_version": "specialist-teacher-dataset-manifest-v2",
        "run_name": run_name,
        "archetype_id": archetype_id,
        "subject_deck_csv_path": str(deck_path),
        "subject_deck_file_sha256": contract["subject_deck_file_sha256"],
        "base_seed": base_seed,
        "max_steps": max_steps,
        "source_commit": source_commit,
        "teacher_id": teacher.opponent_id,
        "teacher_policy_hash": teacher.policy_hash,
        "teacher_deck_file_sha256": contract["teacher"]["deck_file_sha256"],
        "teacher_source_kind": source_kind,
        "teacher_usage_boundary": teacher.usage_boundary,
        "permission_manifest": permission,
        "derivation_decision_ref": decision_ref,
        "opponent_ids": list(opponent_ids),
        "games_requested": num_games,
        "games_completed": completion_stats["games_completed"],
        "games_faulted": completion_stats["games_faulted"],
        "games_other_status": completion_stats["games_other_status"],
        "records_written": total_records,
        "decisions_unlabelled": total_unlabelled,
        "outcome_counts": outcome_counts,
        "seat_counts": completion_stats["seat_counts"],
        "records_dir": str(records_dir),
        "matchup_record_counts": dict(sorted(matchup_counts.items())),
        "matchup_cap_fraction": DEFAULT_MATCHUP_CAP_FRACTION_V1,
        "omissions_path": str(output_root / "omissions.jsonl"),
        "collection_contract_path": str(contract_path),
        "collection_contract_sha256": _file_sha256_v1(contract_path),
        "collector_source_snapshot_path": contract["collector_source_snapshot_path"],
        "collector_source_sha256": contract["collector_source_sha256"],
        "permission_trusted_bytes_sha256": contract[
            "permission_trusted_bytes_sha256"
        ],
        "permission_content_hash": contract["permission_content_hash"],
    }
    # 表現できなかった決定は捨てず、理由付きで残す (正典 §9.3)。
    omissions_path = output_root / "omissions.jsonl"
    _atomic_write_jsonl_v1(omissions_path, omissions)
    manifest["omissions_sha256"] = _file_sha256_v1(omissions_path)
    manifest["game_result_sidecars"] = len(
        tuple((output_root / "game-results").glob("game-*.result.json"))
    )
    attempt_paths = tuple((output_root / "game-attempts").glob("game-*-attempt-*.json"))
    manifest["game_attempts_total"] = len(attempt_paths)
    manifest["game_attempts_non_done"] = sum(
        1
        for path in attempt_paths
        if json.loads(path.read_text(encoding="utf-8")).get("status") != "DONE"
    )
    _atomic_write_json_v1(output_root / "teacher_dataset_manifest.json", manifest)
    return manifest
