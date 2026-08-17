"""Strict common24 reconciliation to dynamic META_TRAIN outcome adapter.

The compact JSONL is intentionally limited to the four fields consumed by the
dynamic curriculum.  Its companion manifest retains the complete game,
identity, seed, split, status, and execution-closure provenance.  This module
never grants training, promotion, external execution, or submission authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionError,
    load_meta_distribution_manifest_v1,
)
from mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 import (
    CANDIDATE_RUNNER_REF_V1,
    Common24ReconciliationError,
    NATIVE_RUNNER_REF_V1,
    OUTPUT_SCHEMA_V1,
    reconcile_student_v3_native_common24_v1,
)


ADAPTER_SCHEMA_V1 = "meta-specialist-common24-curriculum-outcome-adapter-v1"
PURPOSE_V1 = "META_TRAIN_DYNAMIC_CURRICULUM_OUTCOME_RESEARCH_ONLY"
AUTHORITY_FALSE_V1 = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "source_reconciliation",
        "source_meta_distribution",
        "execution_closure",
        "arms",
        "blocks",
        "records",
        "excluded_heldout",
        "output",
        "summary",
        "consumer_contract",
        "authority",
        "adapter_sha256",
    }
)
_RUNNER_PATHS = {
    CANDIDATE_RUNNER_REF_V1: "scripts/run_student_v3_set_candidate_pilot_v1.py",
    NATIVE_RUNNER_REF_V1: "scripts/run_native_policy_candidate_pilot_v1.py",
}


class Common24CurriculumOutcomeAdapterError(ValueError):
    """Raised when common24 evidence cannot safely become a curriculum ledger."""


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
        raise Common24CurriculumOutcomeAdapterError(
            f"value is not canonical JSON: {exc}"
        ) from exc


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Common24CurriculumOutcomeAdapterError(f"cannot hash file: {path}") from exc
    return digest.hexdigest()


def _semantic_sha(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Common24CurriculumOutcomeAdapterError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(path: Path, *, canonical: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                Common24CurriculumOutcomeAdapterError(f"non-finite JSON: {token}")
            ),
        )
    except Common24CurriculumOutcomeAdapterError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Common24CurriculumOutcomeAdapterError(f"invalid JSON: {path}") from exc
    if type(value) is not dict:
        raise Common24CurriculumOutcomeAdapterError(f"JSON root is not an object: {path}")
    if canonical and raw != _canonical_bytes(value):
        raise Common24CurriculumOutcomeAdapterError(f"JSON is not canonical: {path}")
    return value


def _inside(root: Path, value: str | Path, label: str, *, must_exist: bool = True) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Common24CurriculumOutcomeAdapterError(f"{label} escapes repo_root") from exc
    if must_exist and not path.is_file():
        raise Common24CurriculumOutcomeAdapterError(f"{label} is not a file: {path}")
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError as exc:
        raise Common24CurriculumOutcomeAdapterError("bound source escapes repo_root") from exc


def _atomic_write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        with path.open("rb") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.endswith(b"\n") or raw == b"\n":
                    raise Common24CurriculumOutcomeAdapterError(
                        f"source ledger framing is invalid at line {number}"
                    )
                body = raw[:-1]
                value = json.loads(
                    body.decode("utf-8"),
                    object_pairs_hook=_reject_pairs,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        Common24CurriculumOutcomeAdapterError(
                            f"source ledger has non-finite {token}"
                        )
                    ),
                )
                if type(value) is not dict or body != _canonical_bytes(value):
                    raise Common24CurriculumOutcomeAdapterError(
                        f"source ledger row is not canonical at line {number}"
                    )
                rows.append(value)
    except Common24CurriculumOutcomeAdapterError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Common24CurriculumOutcomeAdapterError(f"invalid source ledger: {path}") from exc
    if not rows:
        raise Common24CurriculumOutcomeAdapterError("source ledger must not be empty")
    return rows


def _identity(root: Path, identity: Mapping[str, object], arm: str) -> dict[str, object]:
    if arm == "candidate":
        return {
            "candidate_id": identity["candidate_id"],
            "artifact_path": _relative(root, _inside(root, str(identity["artifact_path"]), "candidate artifact")),
            "artifact_sha256": identity["artifact_sha256"],
            "policy_sha256": identity["policy_sha256"],
            "deck_sha256": identity["deck_sha256"],
            "runner_ref": identity["runner_ref"],
        }
    return {
        "candidate_id": identity["candidate_id"],
        "policy_path": _relative(root, _inside(root, str(identity["policy_path"]), "native policy")),
        "policy_sha256": identity["policy_sha256"],
        "deck_path": _relative(root, _inside(root, str(identity["deck_path"]), "native deck")),
        "deck_sha256": identity["deck_sha256"],
        "runner_ref": identity["runner_ref"],
    }


def _runner_binding(root: Path, runner_ref: object, label: str) -> dict[str, object]:
    if type(runner_ref) is not str or runner_ref not in _RUNNER_PATHS:
        raise Common24CurriculumOutcomeAdapterError(f"unsupported {label} runner_ref")
    path = _inside(root, _RUNNER_PATHS[runner_ref], f"{label} runner source")
    return {
        "runner_ref": runner_ref,
        "source_path": _relative(root, path),
        "source_sha256": _sha_file(path),
    }


def _score(row: Mapping[str, object]) -> float:
    outcome = row.get("outcome")
    if outcome == "win":
        return 1.0
    if outcome == "draw":
        return 0.5
    if outcome in {"loss", "fault"}:
        return 0.0
    raise Common24CurriculumOutcomeAdapterError("unsupported source outcome")


def _file_binding(root: Path, raw: Mapping[str, object], label: str) -> dict[str, str]:
    path = _inside(root, str(raw.get("path")), label)
    expected = raw.get("sha256")
    actual = _sha_file(path)
    if expected != actual:
        raise Common24CurriculumOutcomeAdapterError(f"{label} SHA mismatch")
    return {"path": _relative(root, path), "file_sha256": actual}


def _derive(
    *, root: Path, reconciliation_path: Path, meta_manifest_path: Path, output_dir: Path
) -> tuple[dict[str, object], bytes]:
    reconciliation = _strict_json(reconciliation_path, canonical=True)
    if reconciliation.get("schema_version") != OUTPUT_SCHEMA_V1:
        raise Common24CurriculumOutcomeAdapterError("unsupported reconciliation schema")
    supplied_reconciliation_sha = reconciliation.get("reconciliation_sha256")
    expected_reconciliation_sha = _semantic_sha(
        OUTPUT_SCHEMA_V1,
        {key: value for key, value in reconciliation.items() if key != "reconciliation_sha256"},
    )
    if supplied_reconciliation_sha != expected_reconciliation_sha:
        raise Common24CurriculumOutcomeAdapterError("source reconciliation semantic SHA mismatch")
    request_binding = reconciliation.get("request")
    if type(request_binding) is not dict or set(request_binding) != {"path", "sha256"}:
        raise Common24CurriculumOutcomeAdapterError("reconciliation request binding is invalid")
    request_path = _inside(root, str(request_binding["path"]), "reconciliation request")
    if _sha_file(request_path) != request_binding["sha256"]:
        raise Common24CurriculumOutcomeAdapterError("reconciliation request SHA mismatch")
    try:
        reproduced = reconcile_student_v3_native_common24_v1(request_path)
    except (Common24ReconciliationError, OSError, ValueError) as exc:
        raise Common24CurriculumOutcomeAdapterError(
            f"formal reconciliation verification failed: {exc}"
        ) from exc
    if reproduced != reconciliation:
        raise Common24CurriculumOutcomeAdapterError("source reconciliation does not reproduce")
    request = _strict_json(request_path)
    try:
        meta = load_meta_distribution_manifest_v1(meta_manifest_path, verify_sources=True)
    except (MetaDistributionError, OSError, ValueError) as exc:
        raise Common24CurriculumOutcomeAdapterError(
            f"formal meta distribution verification failed: {exc}"
        ) from exc
    for source in meta.sources:
        _inside(root, source.path, "meta distribution source")
    by_id = {row.opponent_id: row for row in meta.rows}
    protocol = request.get("protocol")
    if type(protocol) is not dict or type(protocol.get("opponent_ids")) is not list:
        raise Common24CurriculumOutcomeAdapterError("request protocol is invalid")
    opponent_ids = list(protocol["opponent_ids"])
    if any(opponent_id not in by_id for opponent_id in opponent_ids):
        raise Common24CurriculumOutcomeAdapterError("protocol opponent is absent from meta distribution")

    candidate_identity = _identity(root, reconciliation["candidate_identity"], "candidate")
    native_identity = _identity(root, reconciliation["native_identity"], "native")
    candidate_runner = _runner_binding(root, candidate_identity["runner_ref"], "candidate")
    native_runner = _runner_binding(root, native_identity["runner_ref"], "native")
    protocol_sha = _semantic_sha("meta-specialist-common24-curriculum-protocol-v1", protocol)
    closure_body = {
        "protocol": protocol,
        "protocol_sha256": protocol_sha,
        "evaluator_implementation_sha256": protocol["evaluator_implementation_sha256"],
        "candidate_runner": candidate_runner,
        "native_runner": native_runner,
    }
    execution_closure = {
        **closure_body,
        "execution_closure_sha256": _semantic_sha(
            "meta-specialist-common24-curriculum-execution-closure-v1", closure_body
        ),
    }

    raw_blocks = request.get("blocks")
    if type(raw_blocks) is not list or not raw_blocks:
        raise Common24CurriculumOutcomeAdapterError("request blocks are invalid")
    records: list[dict[str, object]] = []
    output_rows: list[dict[str, object]] = []
    heldout_ids: dict[str, set[str]] = {"META_DEV": set(), "META_FINAL": set()}
    heldout_rows = {"META_DEV": 0, "META_FINAL": 0}
    all_game_ids: set[str] = set()
    block_bindings: list[dict[str, object]] = []
    candidate_source_rows = 0
    fault_rows = 0
    for block_index, raw_block in enumerate(raw_blocks):
        if type(raw_block) is not dict:
            raise Common24CurriculumOutcomeAdapterError("request block is invalid")
        comparison_id = raw_block.get("comparison_block_id")
        repetitions = raw_block.get("repetitions_per_opponent_seat")
        if type(comparison_id) is not str or type(repetitions) is not int or repetitions <= 0:
            raise Common24CurriculumOutcomeAdapterError("request block schedule is invalid")
        arm_binding: dict[str, object] = {"comparison_block_id": comparison_id}
        for arm in ("candidate", "native"):
            arm_raw = raw_block.get(arm)
            if type(arm_raw) is not dict:
                raise Common24CurriculumOutcomeAdapterError(f"{arm} block is invalid")
            ledger_path = _inside(root, str(arm_raw.get("ledger_path")), f"{arm} ledger")
            rows = _read_jsonl(ledger_path)
            ids = [row.get("game_id") for row in rows]
            if any(type(game_id) is not str or not game_id for game_id in ids):
                raise Common24CurriculumOutcomeAdapterError("game_id must be a non-empty string")
            overlap = all_game_ids.intersection(ids)
            if overlap:
                raise Common24CurriculumOutcomeAdapterError(
                    f"duplicate game_id across source arms: {sorted(overlap)[0]}"
                )
            all_game_ids.update(str(game_id) for game_id in ids)
            base_seed = arm_raw.get("base_seed")
            if type(base_seed) is not int or base_seed < 0:
                raise Common24CurriculumOutcomeAdapterError("base_seed must be nonnegative")
            for ordinal, row in enumerate(rows):
                if row.get("seed") != base_seed + ordinal:
                    raise Common24CurriculumOutcomeAdapterError("seed/base_seed schedule mismatch")
            arm_binding[arm] = {
                "block_id": arm_raw.get("block_id"),
                "base_seed": base_seed,
                "runner_ref": arm_raw.get("runner_ref"),
                "rows": len(rows),
                "ledger": _file_binding(
                    root,
                    {"path": arm_raw.get("ledger_path"), "sha256": arm_raw.get("ledger_sha256")},
                    f"{arm} ledger",
                ),
                "manifest": _file_binding(
                    root,
                    {"path": arm_raw.get("manifest_path"), "sha256": arm_raw.get("manifest_sha256")},
                    f"{arm} evaluator manifest",
                ),
                "summary": _file_binding(
                    root,
                    {"path": arm_raw.get("summary_path"), "sha256": arm_raw.get("summary_sha256")},
                    f"{arm} evaluator summary",
                ),
                "game_id_set_sha256": _semantic_sha(
                    "meta-specialist-common24-game-id-set-v1", sorted(ids)
                ),
            }
            if arm != "candidate":
                continue
            candidate_source_rows += len(rows)
            for ordinal, row in enumerate(rows):
                opponent_id = row.get("opponent_id")
                if type(opponent_id) is not str or opponent_id not in by_id:
                    raise Common24CurriculumOutcomeAdapterError("source opponent is not in meta distribution")
                meta_row = by_id[opponent_id]
                opponent_identity = row.get("opponent_identity")
                if (
                    type(opponent_identity) is not dict
                    or opponent_identity.get("policy_sha256") != meta_row.policy_sha256
                    or opponent_identity.get("deck_sha256") != meta_row.deck_sha256
                ):
                    raise Common24CurriculumOutcomeAdapterError(
                        "source opponent identity disagrees with meta distribution"
                    )
                split = meta_row.split
                if split != "META_TRAIN":
                    if split not in heldout_ids:
                        raise Common24CurriculumOutcomeAdapterError("unsupported meta split")
                    heldout_ids[split].add(opponent_id)
                    heldout_rows[split] += 1
                    continue
                status = row.get("status")
                outcome = row.get("outcome")
                fault = outcome == "fault"
                if fault != (status == "FAULT"):
                    raise Common24CurriculumOutcomeAdapterError("fault/status mismatch")
                score = _score(row)
                seat = row.get("seat")
                seed = row.get("seed")
                game_id = row.get("game_id")
                if seat not in (0, 1) or type(seed) is not int or type(game_id) is not str:
                    raise Common24CurriculumOutcomeAdapterError("game seat/seed/id is invalid")
                record = {
                    "record_index": len(records),
                    "game_id": game_id,
                    "comparison_block_id": comparison_id,
                    "block_id": row.get("block_id"),
                    "ordinal": ordinal,
                    "opponent_id": opponent_id,
                    "opponent_pair_id": meta_row.pair_id,
                    "opponent_policy_sha256": meta_row.policy_sha256,
                    "opponent_deck_sha256": meta_row.deck_sha256,
                    "split": split,
                    "candidate_score": score,
                    "fault": fault,
                    "seat": seat,
                    "seed": seed,
                    "base_seed": base_seed,
                    "status": status,
                    "raw_status": row.get("raw_status"),
                }
                records.append(record)
                output_rows.append(
                    {
                        "opponent_id": opponent_id,
                        "candidate_score": score,
                        "fault": fault,
                        "seat": seat,
                    }
                )
                fault_rows += int(fault)
        block_bindings.append(arm_binding)
    if len(all_game_ids) != 2 * int(reconciliation["target_games_per_arm"]):
        raise Common24CurriculumOutcomeAdapterError("source game_id universe is incomplete")
    if not output_rows:
        raise Common24CurriculumOutcomeAdapterError("no META_TRAIN rows remain after heldout rejection")
    ledger_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in output_rows)
    output_ledger_path = output_dir / "outcome-ledger.jsonl"
    source_reconciliation = {
        "path": _relative(root, reconciliation_path),
        "file_sha256": _sha_file(reconciliation_path),
        "reconciliation_sha256": supplied_reconciliation_sha,
        "request_path": _relative(root, request_path),
        "request_file_sha256": _sha_file(request_path),
    }
    source_meta = {
        "path": _relative(root, meta_manifest_path),
        "file_sha256": _sha_file(meta_manifest_path),
        "schema_version": meta.schema_version,
        "candidate_id": meta.candidate_id,
    }
    manifest: dict[str, object] = {
        "schema_version": ADAPTER_SCHEMA_V1,
        "purpose": PURPOSE_V1,
        "source_reconciliation": source_reconciliation,
        "source_meta_distribution": source_meta,
        "execution_closure": execution_closure,
        "arms": {"candidate": candidate_identity, "native": native_identity},
        "blocks": block_bindings,
        "records": records,
        "excluded_heldout": {
            split: {
                "opponent_ids": sorted(heldout_ids[split]),
                "rows": heldout_rows[split],
                "reason": "HELDOUT_SPLIT_REJECTED_FROM_CURRICULUM_OUTCOME",
            }
            for split in ("META_DEV", "META_FINAL")
        },
        "output": {
            "path": _relative(root, output_ledger_path),
            "file_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
            "rows": len(output_rows),
            "row_schema": ["candidate_score", "fault", "opponent_id", "seat"],
            "game_id_set_sha256": _semantic_sha(
                "meta-specialist-common24-curriculum-output-game-id-set-v1",
                sorted(record["game_id"] for record in records),
            ),
        },
        "summary": {
            "candidate_source_rows": candidate_source_rows,
            "emitted_meta_train_rows": len(records),
            "excluded_meta_dev_rows": heldout_rows["META_DEV"],
            "excluded_meta_final_rows": heldout_rows["META_FINAL"],
            "fault_rows": fault_rows,
            "unique_emitted_game_ids": len({record["game_id"] for record in records}),
        },
        "consumer_contract": {
            "consumer": "dynamic_meta_train_curriculum_v1",
            "emitted_split": "META_TRAIN",
            "meta_dev_rows_allowed": 0,
            "meta_final_rows_allowed": 0,
            "candidate_arm_only": True,
            "native_arm_role": "identity_bound_comparator_only",
        },
        "authority": dict(AUTHORITY_FALSE_V1),
        "adapter_sha256": None,
    }
    manifest["adapter_sha256"] = _semantic_sha(
        ADAPTER_SCHEMA_V1,
        {key: value for key, value in manifest.items() if key != "adapter_sha256"},
    )
    return manifest, ledger_bytes


def build_common24_curriculum_outcome_adapter_v1(
    *,
    repo_root: str | Path,
    reconciliation_path: str | Path,
    meta_manifest_path: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    """Build a candidate-arm-only META_TRAIN ledger plus strict sidecar manifest."""

    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise Common24CurriculumOutcomeAdapterError("repo_root must be a directory")
    reconciliation = _inside(root, reconciliation_path, "source reconciliation")
    meta = _inside(root, meta_manifest_path, "meta distribution manifest")
    output = Path(output_dir).resolve()
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise Common24CurriculumOutcomeAdapterError("output_dir escapes repo_root") from exc
    ledger_path = output / "outcome-ledger.jsonl"
    manifest_path = output / "adapter-manifest.json"
    if ledger_path.exists() or manifest_path.exists():
        raise FileExistsError(output)
    manifest, ledger_bytes = _derive(
        root=root,
        reconciliation_path=reconciliation,
        meta_manifest_path=meta,
        output_dir=output,
    )
    _atomic_write_new(ledger_path, ledger_bytes)
    try:
        _atomic_write_new(manifest_path, _canonical_bytes(manifest))
    except BaseException:
        ledger_path.unlink(missing_ok=True)
        raise
    verified = verify_common24_curriculum_outcome_adapter_v1(manifest_path, root)
    if verified != manifest:
        raise Common24CurriculumOutcomeAdapterError("post-write verification drift")
    return manifest


def verify_common24_curriculum_outcome_adapter_v1(
    manifest_path: str | Path, repo_root: str | Path
) -> dict[str, object]:
    """Reload all sources and reproduce the exact adapter manifest and JSONL."""

    root = Path(repo_root).resolve()
    path = _inside(root, manifest_path, "adapter manifest")
    manifest = _strict_json(path, canonical=True)
    if set(manifest) != _MANIFEST_KEYS or manifest.get("schema_version") != ADAPTER_SCHEMA_V1:
        raise Common24CurriculumOutcomeAdapterError("adapter manifest schema is invalid")
    if manifest.get("purpose") != PURPOSE_V1 or manifest.get("authority") != AUTHORITY_FALSE_V1:
        raise Common24CurriculumOutcomeAdapterError("adapter purpose/authority is invalid")
    supplied = manifest.get("adapter_sha256")
    expected = _semantic_sha(
        ADAPTER_SCHEMA_V1,
        {key: value for key, value in manifest.items() if key != "adapter_sha256"},
    )
    if supplied != expected:
        raise Common24CurriculumOutcomeAdapterError("adapter semantic SHA mismatch")
    source_reconciliation = manifest.get("source_reconciliation")
    source_meta = manifest.get("source_meta_distribution")
    output = manifest.get("output")
    if not all(type(value) is dict for value in (source_reconciliation, source_meta, output)):
        raise Common24CurriculumOutcomeAdapterError("adapter source/output binding is invalid")
    reconciliation_path = _inside(root, str(source_reconciliation["path"]), "source reconciliation")
    meta_path = _inside(root, str(source_meta["path"]), "meta distribution manifest")
    ledger_path = _inside(root, str(output["path"]), "outcome ledger")
    if _sha_file(reconciliation_path) != source_reconciliation.get("file_sha256"):
        raise Common24CurriculumOutcomeAdapterError("source reconciliation file SHA mismatch")
    if _sha_file(meta_path) != source_meta.get("file_sha256"):
        raise Common24CurriculumOutcomeAdapterError("meta distribution file SHA mismatch")
    if _sha_file(ledger_path) != output.get("file_sha256"):
        raise Common24CurriculumOutcomeAdapterError("outcome ledger SHA mismatch")
    reproduced, ledger_bytes = _derive(
        root=root,
        reconciliation_path=reconciliation_path,
        meta_manifest_path=meta_path,
        output_dir=ledger_path.parent,
    )
    if ledger_path.read_bytes() != ledger_bytes:
        raise Common24CurriculumOutcomeAdapterError("outcome ledger bytes do not reproduce")
    if reproduced != manifest:
        raise Common24CurriculumOutcomeAdapterError("adapter manifest does not reproduce")
    return manifest


__all__ = [
    "ADAPTER_SCHEMA_V1",
    "AUTHORITY_FALSE_V1",
    "Common24CurriculumOutcomeAdapterError",
    "build_common24_curriculum_outcome_adapter_v1",
    "verify_common24_curriculum_outcome_adapter_v1",
]
