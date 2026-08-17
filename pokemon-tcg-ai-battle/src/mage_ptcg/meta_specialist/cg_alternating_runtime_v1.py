"""Bounded deck/policy alternating runtime for packaged self-owned cg agents.

The older outcome-only alternating runtime is bound to the native candidate
factory.  This adapter keeps the same fixed-dimension contract for the
submission-shaped ``cg`` package: a policy-fixed short phase may change only
the deck, while a deck-fixed long phase may change only the policy.  It is
research-only, uses terminal WDL rows, and never grants training, promotion,
submission, or unbounded-longrun authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from scripts import run_root_cg_candidate_arena_v1 as arena
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1, run_parallel_cabt_evaluation


CG_ALTERNATING_SCHEMA_V1 = "meta-specialist-cg-alternating-runtime-v1"
CG_POLICY_FIXED_SHORT_V1 = "POLICY_FIXED_SHORT"
CG_DECK_FIXED_LONG_V1 = "DECK_FIXED_LONG"
CG_PHASES_V1 = frozenset({CG_POLICY_FIXED_SHORT_V1, CG_DECK_FIXED_LONG_V1})
CG_STAGE_GAMES_V1 = (96, 384, 768, 1536)
DEFAULT_WORKERS_V1 = 12
DEFAULT_WORKER_RECYCLE_GAMES_V1 = 16
AUTHORITY_FALSE_V1 = dict(arena.AUTHORITY_FALSE)


class CgAlternatingRuntimeError(ValueError):
    """Raised when a packaged cg stage cannot prove its identity contract."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgAlternatingRuntimeError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CgAlternatingRuntimeError("value is not canonical JSON") from exc


def _semantic_sha(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical(value)).hexdigest()


def _write_json_no_clobber(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(payload) + b"\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_name, path)
        os.unlink(temporary_name)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class CgPackageSpecV1:
    """Hash-bound package identity used by one alternating arm."""

    candidate_id: str
    package_root: Path
    policy_sha256: str
    deck_sha256: str
    archive_sha256: str
    manifest_sha256: str
    policy_source_sha256: str

    def __post_init__(self) -> None:
        if type(self.candidate_id) is not str or not self.candidate_id.strip():
            raise CgAlternatingRuntimeError("candidate_id must be non-empty")
        object.__setattr__(self, "package_root", Path(self.package_root).resolve())
        for name in ("policy_sha256", "deck_sha256", "archive_sha256", "manifest_sha256", "policy_source_sha256"):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise CgAlternatingRuntimeError(f"{name} must be a lowercase SHA-256")

    @classmethod
    def from_package(cls, package_root: Path | str) -> "CgPackageSpecV1":
        root = Path(package_root).resolve()
        manifest_path = root.parent / "candidate_manifest.json"
        if not root.is_dir() or not manifest_path.is_file():
            raise CgAlternatingRuntimeError(f"package/manifest missing: {root}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            archive_info = manifest["archive"]
            archive_rel = Path(str(archive_info["path"]))
            if archive_rel.is_absolute() or ".." in archive_rel.parts:
                raise CgAlternatingRuntimeError("archive path escapes package root")
            archive = (root.parent / archive_rel).resolve()
            if archive.parent != root.parent:
                raise CgAlternatingRuntimeError("archive must be beside package/")
            policy_source = str(manifest.get("policy_source_sha256", ""))
            candidate_id = str(manifest.get("candidate_id", ""))
            deck_sha = str(manifest.get("deck_sha256", ""))
            archive_sha = str(archive_info.get("sha256", ""))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CgAlternatingRuntimeError(f"invalid candidate manifest: {manifest_path}") from exc
        spec = cls(
            candidate_id=candidate_id,
            package_root=root,
            policy_sha256=_sha256(root / "main.py"),
            deck_sha256=deck_sha,
            archive_sha256=archive_sha,
            manifest_sha256=_sha256(manifest_path),
            policy_source_sha256=policy_source,
        )
        spec.verify_sources()
        return spec

    @property
    def archive_path(self) -> Path:
        return self.package_root.parent / "submission.tar.gz"

    @property
    def manifest_path(self) -> Path:
        return self.package_root.parent / "candidate_manifest.json"

    def verify_sources(self) -> None:
        if _sha256(self.package_root / "main.py") != self.policy_sha256:
            raise CgAlternatingRuntimeError(f"policy SHA changed: {self.candidate_id}")
        if _sha256(self.package_root / "deck.csv") != self.deck_sha256:
            raise CgAlternatingRuntimeError(f"deck SHA changed: {self.candidate_id}")
        if _sha256(self.archive_path) != self.archive_sha256:
            raise CgAlternatingRuntimeError(f"archive SHA changed: {self.candidate_id}")
        if _sha256(self.manifest_path) != self.manifest_sha256:
            raise CgAlternatingRuntimeError(f"candidate manifest changed: {self.candidate_id}")

    def to_dict(self) -> dict[str, object]:
        self.verify_sources()
        return {
            "candidate_id": self.candidate_id,
            "package_root": str(self.package_root),
            "policy_sha256": self.policy_sha256,
            "deck_sha256": self.deck_sha256,
            "archive_sha256": self.archive_sha256,
            "manifest_sha256": self.manifest_sha256,
            "policy_source_sha256": self.policy_source_sha256,
            "research_only": True,
        }


@dataclass(frozen=True, slots=True)
class CgAlternatingPairV1:
    phase: str
    candidate: CgPackageSpecV1
    control: CgPackageSpecV1
    stage_games: int

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "stage_games": self.stage_games,
            "candidate": self.candidate.to_dict(),
            "control": self.control.to_dict(),
            "authority": dict(AUTHORITY_FALSE_V1),
            "research_only": True,
        }


def validate_cg_pair_v1(
    *,
    phase: str,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    stage_games: int,
) -> CgAlternatingPairV1:
    """Allow only the identity dimension declared by ``phase`` to change."""

    if type(candidate) is not CgPackageSpecV1 or type(control) is not CgPackageSpecV1:
        raise CgAlternatingRuntimeError("candidate and control must be CgPackageSpecV1")
    if phase not in CG_PHASES_V1:
        raise CgAlternatingRuntimeError(f"unsupported phase: {phase}")
    if stage_games not in CG_STAGE_GAMES_V1:
        raise CgAlternatingRuntimeError("stage_games is outside the successive-halving sequence")
    if candidate.candidate_id == control.candidate_id:
        raise CgAlternatingRuntimeError("candidate and control IDs must differ")
    candidate.verify_sources()
    control.verify_sources()
    if phase == CG_POLICY_FIXED_SHORT_V1:
        if candidate.policy_sha256 != control.policy_sha256:
            raise CgAlternatingRuntimeError("policy-fixed phase cannot change policy identity")
        if candidate.deck_sha256 == control.deck_sha256:
            raise CgAlternatingRuntimeError("policy-fixed phase requires a deck change")
    else:
        if candidate.deck_sha256 != control.deck_sha256:
            raise CgAlternatingRuntimeError("deck-fixed phase requires the frozen deck")
        if candidate.policy_sha256 == control.policy_sha256:
            raise CgAlternatingRuntimeError("deck-fixed phase requires a policy identity change")
    return CgAlternatingPairV1(phase=phase, candidate=candidate, control=control, stage_games=stage_games)


def next_cg_stage_games_v1(stage_games: int, *, positive: bool) -> int | None:
    if stage_games not in CG_STAGE_GAMES_V1:
        raise CgAlternatingRuntimeError("stage_games is outside the successive-halving sequence")
    if not positive or stage_games == CG_STAGE_GAMES_V1[-1]:
        return None
    return CG_STAGE_GAMES_V1[CG_STAGE_GAMES_V1.index(stage_games) + 1]


def _stage_repetitions(stage_games: int, reference_ids: Sequence[str]) -> int:
    if not reference_ids or len(set(reference_ids)) != len(reference_ids):
        raise CgAlternatingRuntimeError("reference_ids must be unique and non-empty")
    denominator = 2 * len(reference_ids)
    if stage_games % denominator:
        raise CgAlternatingRuntimeError("stage_games is not divisible by opponents*seats")
    return stage_games // denominator


def build_cg_pair_games_v1(
    *,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    phase: str,
    reference_ids: Sequence[str],
    pool_root: Path | str,
    stage_games: int,
    base_seed: int,
    block_id: str,
) -> tuple[arena.EvaluationGameV1, ...]:
    """Build identical opponent/seat/repetition/seed strata for both arms."""

    validate_cg_pair_v1(phase=phase, candidate=candidate, control=control, stage_games=stage_games)
    repetitions = _stage_repetitions(stage_games, reference_ids)
    pool_root = Path(pool_root).resolve()
    arms = (
        ("candidate", candidate),
        ("control", control),
    )
    built: list[arena.EvaluationGameV1] = []
    for arm_name, spec in arms:
        arm = arena.ArenaArm(
            arm_id=f"cg-alternating-{arm_name}",
            policy_id=spec.candidate_id,
            policy_sha256=spec.policy_sha256,
            arm_kind="root_cg",
            candidate_package_root=spec.package_root,
        )
        raw = arena._build_games(
            arm=arm,
            refs=tuple(reference_ids),
            pool_root=pool_root,
            base_seed=base_seed,
            games_per_opponent_seat=repetitions,
            block_id=f"{block_id}:{arm_name}",
        )
        for game in raw:
            metadata = {
                **dict(game.metadata),
                "cg_alternating_phase": phase,
                "cg_alternating_arm": arm_name,
                "pair_key": f"{game.opponent_id}|seat{game.seat}|rep{game.metadata.get('repetition')}",
                "research_only": True,
                "authority": dict(AUTHORITY_FALSE_V1),
            }
            built.append(replace(game, metadata=metadata))
    candidate_keys = {
        (str(game.metadata["pair_key"]), game.seed)
        for game in built
        if game.metadata.get("cg_alternating_arm") == "candidate"
    }
    control_keys = {
        (str(game.metadata["pair_key"]), game.seed)
        for game in built
        if game.metadata.get("cg_alternating_arm") == "control"
    }
    if candidate_keys != control_keys:
        raise CgAlternatingRuntimeError("candidate/control paired strata differ")
    if len(candidate_keys) != stage_games:
        raise CgAlternatingRuntimeError("paired strata count does not equal stage_games")
    return tuple(built)


def _aggregate(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return arena._aggregate(rows)


def summarize_cg_pair_rows_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    phase: str,
    stage_games: int,
    protocol_sha256: str,
) -> dict[str, object]:
    """Create a fault-inclusive paired WDL summary from evaluator rows."""

    if phase not in CG_PHASES_V1 or stage_games not in CG_STAGE_GAMES_V1:
        raise CgAlternatingRuntimeError("invalid phase/stage in summary")
    by_arm = {
        arm: [row for row in rows if isinstance(row.get("metadata"), Mapping) and row["metadata"].get("cg_alternating_arm") == arm]
        for arm in ("candidate", "control")
    }
    if len(by_arm["candidate"]) != stage_games or len(by_arm["control"]) != stage_games:
        raise CgAlternatingRuntimeError("both cg arms must cover the exact stage games")
    def key(row: Mapping[str, object]) -> tuple[str, object]:
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping) or not isinstance(metadata.get("pair_key"), str):
            raise CgAlternatingRuntimeError("pair key missing from evaluator row")
        return str(metadata["pair_key"]), row.get("seed")
    candidate_keys = {key(row) for row in by_arm["candidate"]}
    control_keys = {key(row) for row in by_arm["control"]}
    if candidate_keys != control_keys:
        raise CgAlternatingRuntimeError("candidate/control summary strata differ")
    candidate_summary = _aggregate(by_arm["candidate"])
    control_summary = _aggregate(by_arm["control"])
    candidate_score = float(candidate_summary["score_rate"] or 0.0)
    control_score = float(control_summary["score_rate"] or 0.0)
    candidate_seat = candidate_summary.get("seat", {})
    control_seat = control_summary.get("seat", {})
    candidate_gap = abs(float(candidate_seat["0"]["score_rate"]) - float(candidate_seat["1"]["score_rate"]))
    control_gap = abs(float(control_seat["0"]["score_rate"]) - float(control_seat["1"]["score_rate"]))
    faults = int(candidate_summary["faults"]) + int(control_summary["faults"])
    delta = candidate_score - control_score
    positive = faults == 0 and delta > 0.0 and candidate_gap <= 0.05 and control_gap <= 0.05
    decision = "INVALID_FAULT" if faults else "POSITIVE_CONTINUE" if positive else "NOT_PROMOTABLE"
    payload: dict[str, object] = {
        "schema_version": CG_ALTERNATING_SCHEMA_V1,
        "phase": phase,
        "stage_games": stage_games,
        "protocol_sha256": protocol_sha256,
        "candidate": candidate_summary,
        "control": control_summary,
        "candidate_delta": delta,
        "candidate_delta_points": delta * 100.0,
        "candidate_seat_gap": candidate_gap,
        "control_seat_gap": control_gap,
        "decision": decision,
        "next_stage_games": next_cg_stage_games_v1(stage_games, positive=positive),
        "candidate_identity": candidate.to_dict(),
        "control_identity": control.to_dict(),
        "paired_strata_sha256": _semantic_sha(
            "mage-ptcg:cg-alternating-paired-strata:v1",
            {"keys": sorted((str(pair), str(seed)) for pair, seed in candidate_keys)},
        ),
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    payload["summary_sha256"] = _semantic_sha(
        "mage-ptcg:cg-alternating-summary:v1",
        {key: value for key, value in payload.items() if key != "summary_sha256"},
    )
    return payload


def _protocol_sha256(*, pool_root: Path, reference_ids: Sequence[str], phase: str, stage_games: int, base_seed: int, block_id: str) -> str:
    manifest = pool_root / "pool_manifest.json"
    if not manifest.is_file():
        raise CgAlternatingRuntimeError(f"opponent pool manifest missing: {manifest}")
    return _semantic_sha(
        "mage-ptcg:cg-alternating-protocol:v1",
        {
            "pool_manifest_sha256": _sha256(manifest),
            "reference_ids": list(reference_ids),
            "phase": phase,
            "stage_games": stage_games,
            "base_seed": base_seed,
            "block_id": block_id,
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        },
    )


def run_cg_alternating_stage_v1(
    *,
    candidate: CgPackageSpecV1,
    control: CgPackageSpecV1,
    phase: str,
    reference_ids: Sequence[str],
    pool_root: Path | str,
    stage_games: int,
    base_seed: int,
    block_id: str,
    output_root: Path | str,
    execute: bool = False,
    workers: int = DEFAULT_WORKERS_V1,
    worker_recycle_games: int | None = None,
) -> dict[str, object]:
    """Materialize or execute one bounded cg candidate/control stage."""

    expected_recycle = DEFAULT_WORKER_RECYCLE_GAMES_V1 if stage_games == 96 else 64
    if workers != DEFAULT_WORKERS_V1:
        raise CgAlternatingRuntimeError("cg alternating runtime is sealed to workers=12")
    if worker_recycle_games is None:
        worker_recycle_games = expected_recycle
    if worker_recycle_games != expected_recycle:
        raise CgAlternatingRuntimeError(f"stage_games={stage_games} requires recycle={expected_recycle}")
    pair = validate_cg_pair_v1(phase=phase, candidate=candidate, control=control, stage_games=stage_games)
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"cg alternating output root exists: {root}")
    pool_root = Path(pool_root).resolve()
    games = build_cg_pair_games_v1(
        candidate=candidate,
        control=control,
        phase=phase,
        reference_ids=tuple(reference_ids),
        pool_root=pool_root,
        stage_games=stage_games,
        base_seed=base_seed,
        block_id=block_id,
    )
    protocol_sha = _protocol_sha256(
        pool_root=pool_root,
        reference_ids=tuple(reference_ids),
        phase=phase,
        stage_games=stage_games,
        base_seed=base_seed,
        block_id=block_id,
    )
    root.mkdir(parents=True, exist_ok=False)
    spec = {
        "schema_version": CG_ALTERNATING_SCHEMA_V1,
        "phase": pair.phase,
        "stage_games": stage_games,
        "base_seed": base_seed,
        "block_id": block_id,
        "reference_ids": list(reference_ids),
        "protocol_sha256": protocol_sha,
        "candidate": candidate.to_dict(),
        "control": control.to_dict(),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    spec_sha = _write_json_no_clobber(root / "stage-spec.json", spec)
    manifest: dict[str, object] = {
        "schema_version": CG_ALTERNATING_SCHEMA_V1,
        "status": "EXECUTING" if execute else "DRY_RUN",
        "phase": phase,
        "stage_games": stage_games,
        "requested_games": len(games),
        "base_seed": base_seed,
        "block_id": block_id,
        "reference_ids": list(reference_ids),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "protocol_sha256": protocol_sha,
        "stage_spec_sha256": spec_sha,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "candidate_id": candidate.candidate_id,
        "control_id": control.candidate_id,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    manifest_sha = _write_json_no_clobber(root / "manifest.json", manifest)
    if not execute:
        return {"status": "DRY_RUN", "output_root": str(root), "manifest_sha256": manifest_sha, "authority": dict(AUTHORITY_FALSE_V1), "research_only": True}
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=root / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    summary = summarize_cg_pair_rows_v1(
        evaluation["rows"],
        candidate=candidate,
        control=control,
        phase=phase,
        stage_games=stage_games,
        protocol_sha256=protocol_sha,
    )
    summary["evaluator_summary"] = evaluation["summary"]
    summary_sha = _write_json_no_clobber(root / "summary.json", summary)
    complete = {
        **manifest,
        "status": "COMPLETE",
        "completed_games": evaluation["summary"].get("completed_games"),
        "faults": evaluation["summary"].get("faults"),
        "decision": summary["decision"],
        "next_stage_games": summary["next_stage_games"],
        "summary_sha256": summary_sha,
    }
    complete_sha = _write_json_no_clobber(root / "manifest-complete.json", complete)
    return {
        "status": "COMPLETE",
        "output_root": str(root),
        "manifest_sha256": complete_sha,
        "summary_sha256": summary_sha,
        "summary": summary,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }


def load_cg_alternating_stage_v1(run_root: Path | str) -> dict[str, object]:
    """Reload a dry-run or completed stage and verify its immutable sidecars."""

    root = Path(run_root).resolve()
    manifest_path = root / "manifest-complete.json"
    if not manifest_path.is_file():
        manifest_path = root / "manifest.json"
    spec_path = root / "stage-spec.json"
    if not manifest_path.is_file() or not spec_path.is_file():
        raise CgAlternatingRuntimeError(f"cg alternating stage sidecars missing: {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CgAlternatingRuntimeError("cg alternating stage JSON is unreadable") from exc
    if not isinstance(manifest, dict) or not isinstance(spec, dict):
        raise CgAlternatingRuntimeError("cg alternating stage JSON must be objects")
    if manifest.get("schema_version") != CG_ALTERNATING_SCHEMA_V1 or spec.get("schema_version") != CG_ALTERNATING_SCHEMA_V1:
        raise CgAlternatingRuntimeError("cg alternating stage schema mismatch")
    if manifest.get("authority") != AUTHORITY_FALSE_V1 or manifest.get("research_only") is not True:
        raise CgAlternatingRuntimeError("cg alternating stage grants authority")
    if spec.get("authority") != AUTHORITY_FALSE_V1 or spec.get("research_only") is not True:
        raise CgAlternatingRuntimeError("cg alternating spec grants authority")
    expected_spec_sha = manifest.get("stage_spec_sha256")
    if expected_spec_sha != _sha256(spec_path):
        raise CgAlternatingRuntimeError("stage spec SHA changed")
    result: dict[str, object] = {**manifest, "stage_spec": spec}
    if manifest.get("status") == "COMPLETE":
        summary_path = root / "summary.json"
        if not summary_path.is_file() or manifest.get("summary_sha256") != _sha256(summary_path):
            raise CgAlternatingRuntimeError("completed cg alternating summary SHA changed or is missing")
        try:
            result["summary"] = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CgAlternatingRuntimeError("completed cg alternating summary is unreadable") from exc
    return result


def run_cg_alternating_iteration_v1(
    *,
    deck_candidate: CgPackageSpecV1,
    deck_control: CgPackageSpecV1,
    policy_candidate: CgPackageSpecV1,
    policy_control: CgPackageSpecV1,
    reference_ids: Sequence[str],
    pool_root: Path | str,
    stage_games: int,
    base_seed: int,
    output_root: Path | str,
    execute: bool = False,
    workers: int = DEFAULT_WORKERS_V1,
    worker_recycle_games: int | None = None,
) -> dict[str, object]:
    """Run at most one deck-fixed/policy-fixed iteration; never loop forever."""

    validate_cg_pair_v1(
        phase=CG_POLICY_FIXED_SHORT_V1,
        candidate=deck_candidate,
        control=deck_control,
        stage_games=stage_games,
    )
    if policy_candidate.deck_sha256 != deck_candidate.deck_sha256 or policy_control.deck_sha256 != deck_candidate.deck_sha256:
        raise CgAlternatingRuntimeError("policy phase must reuse the frozen deck candidate")
    validate_cg_pair_v1(
        phase=CG_DECK_FIXED_LONG_V1,
        candidate=policy_candidate,
        control=policy_control,
        stage_games=stage_games,
    )
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"cg alternating iteration root exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    deck_result = run_cg_alternating_stage_v1(
        candidate=deck_candidate,
        control=deck_control,
        phase=CG_POLICY_FIXED_SHORT_V1,
        reference_ids=reference_ids,
        pool_root=pool_root,
        stage_games=stage_games,
        base_seed=base_seed,
        block_id="cg-alternating-deck",
        output_root=root / "policy-fixed-short",
        execute=execute,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    policy_result: dict[str, object] | None = None
    if execute and deck_result.get("summary", {}).get("decision") == "POSITIVE_CONTINUE":
        policy_result = run_cg_alternating_stage_v1(
            candidate=policy_candidate,
            control=policy_control,
            phase=CG_DECK_FIXED_LONG_V1,
            reference_ids=reference_ids,
            pool_root=pool_root,
            stage_games=stage_games,
            base_seed=base_seed + (stage_games * 2),
            block_id="cg-alternating-policy",
            output_root=root / "deck-fixed-long",
            execute=True,
            workers=workers,
            worker_recycle_games=worker_recycle_games,
        )
    payload: dict[str, object] = {
        "schema_version": CG_ALTERNATING_SCHEMA_V1,
        "status": "COMPLETE" if execute else "DRY_RUN",
        "execute": bool(execute),
        "stage_games": stage_games,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games or (16 if stage_games == 96 else 64),
        "deck_phase": deck_result,
        "policy_phase": policy_result,
        "policy_phase_started": policy_result is not None,
        "next_action": "manual_successive_halving_required" if policy_result and policy_result.get("summary", {}).get("decision") == "POSITIVE_CONTINUE" else "stop_or_review",
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    payload["iteration_sha256"] = _semantic_sha(
        "mage-ptcg:cg-alternating-iteration:v1",
        {key: value for key, value in payload.items() if key != "iteration_sha256"},
    )
    _write_json_no_clobber(root / "iteration.json", payload)
    return payload


__all__ = [
    "AUTHORITY_FALSE_V1",
    "CG_ALTERNATING_SCHEMA_V1",
    "CG_DECK_FIXED_LONG_V1",
    "CG_PHASES_V1",
    "CG_POLICY_FIXED_SHORT_V1",
    "CG_STAGE_GAMES_V1",
    "CgAlternatingPairV1",
    "CgAlternatingRuntimeError",
    "CgPackageSpecV1",
    "DEFAULT_WORKER_RECYCLE_GAMES_V1",
    "DEFAULT_WORKERS_V1",
    "build_cg_pair_games_v1",
    "load_cg_alternating_stage_v1",
    "next_cg_stage_games_v1",
    "run_cg_alternating_iteration_v1",
    "run_cg_alternating_stage_v1",
    "summarize_cg_pair_rows_v1",
    "validate_cg_pair_v1",
]
