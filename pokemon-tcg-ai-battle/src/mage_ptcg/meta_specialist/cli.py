"""Local, network-free JSON CLI for the meta-specialist submission pipeline.

Every subcommand prints exactly one canonical JSON object plus one trailing
newline to stdout on success (exit 0), or one canonical JSON error object to
stderr on a classified failure (exit 2).  Nothing is ever written to both
streams for the same invocation.  ``collect-trajectories`` and
``train-from-trajectories`` are the exceptions to "nothing besides that JSON
object": both also write live progress (a TTY bar or periodic non-TTY
snapshot lines) and one human-readable summary line to stderr while they
run, and by default print a compact, aggregated human-readable summary (not
the raw run-summary JSON, whose ``faulted_jobs``/per-step arrays can span
hundreds of lines on a real run) to stdout on completion.  Pass ``--json`` to
either subcommand for the full machine-readable run-summary JSON on stdout
instead -- the exact same object that is always written, unabridged, to
``run_summary.json`` regardless of ``--json``.

This CLI only wraps existing, already-tested primitives:
``mage_ptcg.meta_specialist.decks`` for deck qualification and deck-lock
creation, ``mage_ptcg.meta_specialist.package`` for archive build/verify,
``mage_ptcg.meta_specialist.contracts``/``runtime`` for the frozen ladder and
runtime-constraint contracts, ``mage_ptcg.meta_specialist.actor_pool_v1``
(via ``collect_trajectories_v1``) for real trajectory collection, and
``mage_ptcg.meta_specialist.vtrace_bridge_v1``/``trajectory_target_v1``/
``neural_checkpoint_v1`` (via ``train_from_trajectories_v1``) for real,
resumable V-trace training over already-collected trajectories.
``qualify-deck`` requires a pre-measured, deck-bound CABT evidence file (see
``--cabt-evidence-json`` below) and fails closed if that evidence does not
exactly bind the deck being qualified. ``collect-trajectories`` is the one
subcommand that does perform real CABT measurement: it plays real games
through ``ActorPoolV1``, one archetype lane at a time, only ever against a
deck the seed qualification report already marks ``qualified``.
``train-from-trajectories`` is the one subcommand that takes real optimizer
steps, reading only trajectories a prior ``collect-trajectories`` run already
wrote to disk.

Hard boundary: this CLI can only BUILD and VERIFY a submission archive,
COLLECT trajectories, and TRAIN from already-collected trajectories, all
locally.  It has no subcommand, flag, or code path that uploads, submits, or
otherwise talks to the network, git remotes, or the Kaggle API -- submission
is, and must remain, a manual, human-executed action outside this tool.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Callable, NoReturn

from mage_ptcg.continuous_league.contracts import content_id
import mage_ptcg.meta_specialist.orchestrator_v1 as orchestrator_v1
from mage_ptcg.meta_specialist.actor_pool_v1 import (
    DEFAULT_MAX_STEPS_V1,
    DEFAULT_TIMEOUT_SECONDS_V1,
    ActorPoolV1Error,
)
from mage_ptcg.meta_specialist.collect_trajectories_v1 import (
    DEFAULT_MATERIALIZED_DECK_DIR_V1,
    DEFAULT_SEED_QUALIFICATION_REPORT_PATH_V1,
    CollectTrajectoriesError,
    run_collect_trajectories_v1,
)
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload
from mage_ptcg.meta_specialist.decks import (
    ArchetypeRegistryError,
    ArchetypeSpec,
    DeckAssetInput,
    DeckLineageError,
    DeckQualificationError,
    create_deck_lock,
    load_archetype_registry,
    qualify_deck_asset,
)
from mage_ptcg.meta_specialist.package import (
    BundleContractError,
    BundleSecurityError,
    build_specialist_archive,
    load_bundle_spec,
    verify_specialist_archive,
)
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest
from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
    ADVANTAGE_NORMALIZATION_MODES_V1,
    TrainFromTrajectoriesV1Error,
    run_train_from_trajectories_v1,
)

_CABT_EVIDENCE_SCHEMA = "meta-specialist-cabt-deck-evidence-v1"
_MAX_INPUT_FILE_BYTES = 4 * 1024 * 1024
_MAX_MESSAGE_LEN = 512


class CliError(Exception):
    """One classified, sanitized CLI failure; never a raw traceback."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _sanitize_message(value: str) -> str:
    single_line = " ".join(str(value).split())
    if len(single_line) > _MAX_MESSAGE_LEN:
        single_line = single_line[: _MAX_MESSAGE_LEN - 3] + "..."
    return single_line or "unspecified error"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _read_json_file(path: Path, *, label: str) -> object:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CliError("INPUT_ERROR", f"could not read {label} at {path}: {exc}") from exc
    if len(raw) > _MAX_INPUT_FILE_BYTES:
        raise CliError("INPUT_ERROR", f"{label} at {path} exceeds the bounded input size")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError("INPUT_ERROR", f"{label} at {path} is not valid UTF-8 JSON: {exc}") from exc


def _require_str_field(document: Mapping[str, object], field: str, *, label: str) -> str:
    value = document.get(field)
    if type(value) is not str or not value:
        raise CliError("INPUT_ERROR", f"{label} is missing required string field {field!r}")
    return value


def _load_known_card_ids(path: Path) -> tuple[set[int], str]:
    """Load recognized card IDs from the official competition card-data CSV.

    Matches the ``Card ID`` column convention already used by
    ``scripts/test_sim.py``'s ``load_known_card_ids``.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CliError("INPUT_ERROR", f"could not read known-card-ids file at {path}: {exc}") from exc
    file_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CliError("INPUT_ERROR", f"known-card-ids file at {path} is not valid UTF-8: {exc}") from exc
    reader = csv.DictReader(io.StringIO(text))
    if "Card ID" not in (reader.fieldnames or []):
        raise CliError("INPUT_ERROR", f"known-card-ids file at {path} has no 'Card ID' column")
    try:
        ids = {int(row["Card ID"]) for row in reader if row.get("Card ID")}
    except ValueError as exc:
        raise CliError("INPUT_ERROR", f"known-card-ids file at {path} has a non-integer Card ID: {exc}") from exc
    if not ids:
        raise CliError("INPUT_ERROR", f"known-card-ids file at {path} contains no card IDs")
    return ids, file_sha256


def _resolve_archetype(registry, archetype_id: str) -> ArchetypeSpec:
    spec = registry.archetypes.get(archetype_id)
    if spec is not None:
        return spec
    for candidate in registry.archetypes.values():
        if archetype_id in candidate.aliases:
            return candidate
    raise CliError("CONTRACT_ERROR", f"archetype_id {archetype_id!r} is not registered")


def _qualified_asset_payload(asset) -> dict[str, object]:
    return {
        "schema_version": "meta-specialist-qualified-deck-asset-v1",
        "asset_id": asset.asset_id,
        "archetype_id": asset.archetype_id,
        "card_ids": list(asset.card_ids),
        "deck_identity": asset.deck_identity,
        "deck_file_sha256": asset.deck_file_sha256,
        "source_ref": asset.source_ref,
        "source_commit": asset.source_commit,
        "asset_class": asset.asset_class,
        "usage_boundary": asset.usage_boundary,
        "policy_compatibility": asset.policy_compatibility,
        "card_database_version": asset.card_database_version,
        "card_count": asset.card_count,
        "cabt_legality_status": asset.cabt_legality_status,
        "cabt_legality_evidence": asset.cabt_legality_evidence,
    }


def _cmd_show_runtime_constraints(_args: argparse.Namespace) -> dict[str, object]:
    return RuntimeConstraintManifest.frozen_v1().to_payload()


_REPO_ROOT_FOR_CLEANUP = Path(__file__).resolve().parents[3]


def _cmd_cleanup_plan(args: argparse.Namespace) -> dict[str, object]:
    """正典 §18 CLI の `cleanup-plan`: 削除前の参照・容量監査。

    何も削除しない。正典 §20 が要求する「path、size、content hash、参照元、
    再生成方法、保持理由、復元可能性」を列挙した manifest を出すだけである。
    dirty worktree では計画自体を拒否する (§20: dirty worktree では untracked
    cleanup を行わない)。
    """
    from mage_ptcg.meta_specialist.cleanup_manifest_v1 import (
        CleanupManifestV1,
        CleanupManifestV1Error,
        plan_cleanup_target_v1,
    )
    from mage_ptcg.meta_specialist.worktree_guard_v1 import (
        WorktreeGuardV1Error,
        inspect_worktree_status_v1,
    )

    repo_root = Path(args.repo_root).resolve()
    try:
        protection = inspect_worktree_status_v1(str(repo_root))
    except WorktreeGuardV1Error as exc:
        raise CliError("CONTRACT_ERROR", f"could not inspect the worktree: {exc}") from exc

    if protection.is_dirty and not args.allow_dirty_worktree:
        raise CliError(
            "CONTRACT_ERROR",
            f"{repo_root} has {protection.modified_count} modified and "
            f"{protection.untracked_count} untracked entries. 正典 §20 は dirty "
            "worktree での untracked cleanup を禁じる。--allow-dirty-worktree を "
            "明示した場合だけ計画だけを作る (削除はこの CLI では行わない)。",
        )

    targets = []
    for relative_path in args.path or []:
        try:
            targets.append(
                plan_cleanup_target_v1(
                    repo_root, relative_path,
                    referenced_by=(), regenerable_by=args.regenerable_by,
                    retention_reason=args.retention_reason, restorable=args.restorable,
                )
            )
        except (CleanupManifestV1Error, WorktreeGuardV1Error) as exc:
            raise CliError("CONTRACT_ERROR", f"{relative_path}: {exc}") from exc

    manifest = CleanupManifestV1(manifest_id=protection.manifest_id(), targets=tuple(targets))
    return {
        "status": "PLANNED",
        "deleted": [],
        "worktree_protection": protection.to_dict(),
        "cleanup_manifest": manifest.to_dict(),
        "cleanup_manifest_content_hash": manifest.content_hash(),
    }


def _cmd_show_ladder_contract(args: argparse.Namespace) -> dict[str, object]:
    try:
        payload = ladder_mechanics_payload(checked_at_utc=args.checked_at_utc)
    except ValueError as exc:
        raise CliError("ARGUMENT_ERROR", f"--checked-at-utc is invalid: {exc}") from exc
    payload["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", payload)
    return payload


def _cmd_qualify_deck(args: argparse.Namespace) -> dict[str, object]:
    asset_document = _read_json_file(Path(args.asset_json), label="asset-json")
    if type(asset_document) is not dict:
        raise CliError("INPUT_ERROR", "asset-json must be a JSON object")
    fields = {
        name: _require_str_field(asset_document, name, label="asset-json")
        for name in (
            "asset_id", "archetype_id", "deck_path", "source_ref", "source_commit",
            "asset_class", "usage_boundary", "policy_compatibility", "card_database_version",
        )
    }

    try:
        registry = load_archetype_registry(Path(args.registry))
    except ArchetypeRegistryError as exc:
        raise CliError("CONTRACT_ERROR", f"could not load archetype registry: {exc}") from exc
    archetype = _resolve_archetype(registry, fields["archetype_id"])

    known_card_ids, known_card_ids_file_sha256 = _load_known_card_ids(Path(args.known_card_ids))

    evidence_document = _read_json_file(Path(args.cabt_evidence_json), label="cabt-evidence-json")
    if type(evidence_document) is not dict:
        raise CliError("INPUT_ERROR", "cabt-evidence-json must be a JSON object")
    if evidence_document.get("schema_version") != _CABT_EVIDENCE_SCHEMA:
        raise CliError("CONTRACT_ERROR", "cabt-evidence-json schema_version is not supported")
    if evidence_document.get("passed") is not True:
        raise CliError("CONTRACT_ERROR", "cabt-evidence-json does not record passed=true")
    evidence_text = evidence_document.get("evidence")
    if type(evidence_text) is not str or not evidence_text.strip():
        raise CliError("CONTRACT_ERROR", "cabt-evidence-json evidence must be a nonempty string")
    expected_runtime_version = RuntimeConstraintManifest.frozen_v1().verifier_dependency
    if evidence_document.get("cabt_runtime_version") != expected_runtime_version:
        raise CliError(
            "CONTRACT_ERROR",
            f"cabt-evidence-json cabt_runtime_version must be {expected_runtime_version!r}",
        )

    try:
        deck_asset_input = DeckAssetInput.from_path(
            asset_id=fields["asset_id"],
            archetype_id=archetype.runtime_id,
            path=Path(fields["deck_path"]),
            source_ref=fields["source_ref"],
            source_commit=fields["source_commit"],
            asset_class=fields["asset_class"],
            usage_boundary=fields["usage_boundary"],
            policy_compatibility=fields["policy_compatibility"],
            card_database_version=fields["card_database_version"],
        )
    except DeckQualificationError as exc:
        raise CliError("CONTRACT_ERROR", f"could not read deck at {fields['deck_path']}: {exc}") from exc

    if evidence_document.get("deck_identity") != deck_asset_input.deck_identity:
        raise CliError("CONTRACT_ERROR", "cabt-evidence-json deck_identity does not match deck.csv")
    if evidence_document.get("deck_file_sha256") != deck_asset_input.deck_file_sha256:
        raise CliError("CONTRACT_ERROR", "cabt-evidence-json deck_file_sha256 does not match deck.csv")
    if evidence_document.get("card_database_version") != fields["card_database_version"]:
        raise CliError("CONTRACT_ERROR", "cabt-evidence-json card_database_version does not match --asset-json")

    try:
        qualified = qualify_deck_asset(
            deck_asset_input,
            archetype,
            known_card_ids=known_card_ids,
            cabt_legality=lambda _cards, _evidence=evidence_text: (True, _evidence),
        )
    except DeckQualificationError as exc:
        raise CliError("CONTRACT_ERROR", f"deck qualification failed: {exc}") from exc

    payload = _qualified_asset_payload(qualified)
    payload["status"] = "QUALIFIED"
    payload["known_card_ids_file_sha256"] = known_card_ids_file_sha256
    payload["cabt_evidence_sha256"] = hashlib.sha256(
        json.dumps(evidence_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _cmd_lock_deck(args: argparse.Namespace) -> dict[str, object]:
    compared = [item for item in args.compared_deck_identities.split(",") if item]
    try:
        lock = create_deck_lock(
            archetype_id=args.archetype_id,
            selected_deck_identity=args.selected_deck_identity,
            compared_deck_identities=compared,
            foundation_init_id=args.foundation_init_id,
            joint_race_schedule_id=args.joint_race_schedule_id,
            equal_transition_budget=args.equal_transition_budget,
        )
    except DeckLineageError as exc:
        raise CliError("CONTRACT_ERROR", f"could not create deck lock: {exc}") from exc
    return {
        "schema_version": "meta-specialist-lock-deck-cli-v1",
        "status": "LOCKED",
        "archetype_id": lock.archetype_id,
        "selected_deck_identity": lock.selected_deck_identity,
        "compared_deck_identities": list(lock.compared_deck_identities),
        "foundation_init_id": lock.foundation_init_id,
        "joint_race_schedule_id": lock.joint_race_schedule_id,
        "equal_transition_budget": lock.equal_transition_budget,
        "deck_lock_id": lock.deck_lock_id,
        "policy_lineage_id": lock.policy_lineage_id,
    }


def _cmd_build_submission(args: argparse.Namespace) -> dict[str, object]:
    try:
        spec = load_bundle_spec(Path(args.spec))
    except BundleSecurityError as exc:
        raise CliError("SECURITY_ERROR", f"could not load bundle spec: {exc}") from exc
    except BundleContractError as exc:
        raise CliError("CONTRACT_ERROR", f"could not load bundle spec: {exc}") from exc
    try:
        report = build_specialist_archive(spec, Path(args.output))
    except BundleSecurityError as exc:
        raise CliError("SECURITY_ERROR", f"could not build submission archive: {exc}") from exc
    except BundleContractError as exc:
        raise CliError("CONTRACT_ERROR", f"could not build submission archive: {exc}") from exc
    return report.to_payload()


_TOP_FAULT_REASONS_SHOWN_V1 = 5


def _seat_balance_summary_v1(per_lane: Mapping[str, Mapping[str, object]]) -> str:
    """Aggregate every lane's seat/0 and seat/1 collected-vs-attempted counts."""
    totals = {"0": [0, 0], "1": [0, 0]}  # [collected, attempted]
    for lane in per_lane.values():
        for seat, stats in lane["seats"].items():
            totals[seat][0] += stats["collected"]
            totals[seat][1] += stats["attempted"]
    return " ".join(f"seat{seat}={collected}/{attempted}" for seat, (collected, attempted) in sorted(totals.items()))


def _top_fault_reason_counts_v1(faulted_jobs: Sequence[Mapping[str, object]], *, limit: int) -> list[str]:
    """Group listed faulted-job reasons into '<count>x <reason>' lines, most common first."""
    counts: dict[str, int] = {}
    for job in faulted_jobs:
        reason = str(job.get("fault_reason") or "(no reason recorded)")
        counts[reason] = counts.get(reason, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [f"{count}x {reason}" for reason, count in ranked[:limit]]


def _format_collect_trajectories_summary_v1(payload: Mapping[str, object]) -> str:
    """Render ``run_collect_trajectories_v1``'s payload as a short, human-readable report.

    Never prints the raw ``faulted_jobs`` array (one entry per faulted job) --
    only aggregated per-lane totals and fault-reason counts. The full payload
    (identical to what was already written to ``run_summary.json``) remains
    available verbatim via ``--json``.
    """
    per_lane: Mapping[str, Mapping[str, object]] = payload["per_lane"]
    wall_time_seconds = float(payload["wall_time_seconds"])
    games_completed = int(payload["games_completed"])
    throughput = games_completed / wall_time_seconds if wall_time_seconds > 0 else 0.0

    lines = [
        f"collect-trajectories: run={payload['run_name']} lanes={','.join(payload['lanes'])}",
        (
            f"  totals: requested={payload['num_games_requested']} attempted={payload['games_attempted']} "
            f"completed={games_completed} resumed_skipped={payload['games_resumed_skipped']} "
            f"faulted={payload['games_faulted']} timeout={payload['games_timeout']} "
            f"transitions={payload['transitions_collected']}"
        ),
        f"  seat balance (collected/attempted): {_seat_balance_summary_v1(per_lane)}",
        f"  wall_time={wall_time_seconds:.1f}s throughput={throughput:.2f} games/s (completed/wall_time)",
        "  per_lane (completed/attempted, faulted, timeout, transitions):",
    ]
    name_width = max((len(name) for name in per_lane), default=0)
    for name in sorted(per_lane):
        lane = per_lane[name]
        lines.append(
            f"    {name:<{name_width}}  completed={lane['completed']}/{lane['attempted']}  "
            f"faulted={lane['faulted']}  timeout={lane['timeout']}  transitions={lane['transitions']}"
        )

    faulted_jobs: Sequence[Mapping[str, object]] = payload["faulted_jobs"]
    total_faulted_or_timeout = int(payload["games_faulted"]) + int(payload["games_timeout"])
    if total_faulted_or_timeout:
        sample_note = (
            f" (from a sample of {len(faulted_jobs)} of {total_faulted_or_timeout} faulted/timeout jobs)"
            if payload["faulted_jobs_truncated"] else ""
        )
        lines.append(f"  top fault reasons{sample_note}:")
        lines.extend(f"    {line}" for line in _top_fault_reason_counts_v1(faulted_jobs, limit=_TOP_FAULT_REASONS_SHOWN_V1))
    else:
        lines.append("  top fault reasons: none")

    # A commit to this repo changes every job id, so a re-collection legitimately
    # reuses nothing. Say that plainly instead of leaving `resumed_skipped=0` to
    # be read as broken resume.
    existing: Mapping[str, object] = payload.get("existing_games_outside_this_plan") or {}
    if int(existing.get("count", 0) or 0):
        versions: Sequence[str] = existing.get("behavior_versions") or []
        shown = ", ".join(version[:12] for version in versions[:3])
        if len(versions) > 3:
            shown += f", +{len(versions) - 3} more"
        lines.append(
            f"  existing games outside this plan: {existing['count']} "
            f"({existing['transitions']} transitions) already in games/ but not claimed by this "
            "run's job ids -- collected under a different source_commit, deliberately not reused"
        )
        lines.append(f"    subject_behavior_version(s) present: {shown or 'unknown'}")
        if int(existing.get("unreadable", 0) or 0):
            lines.append(f"    unreadable existing records: {existing['unreadable']}")
        lines.append(
            "    train-from-trajectories reads every record under games/, so these still "
            "contribute to training."
        )

    lines.append(f"  run_summary:      {payload['run_summary_path']}")
    lines.append(f"  progress_summary: {payload['progress_summary_path']}")
    lines.append("  (pass --json for the full machine-readable payload)")
    return "\n".join(lines)


def _format_learning_health_lines_v1(payload: Mapping[str, object]) -> list[str]:
    """Say whether the policy actually moved, not just that steps ran.

    ``loss`` and ``grad_norm`` alone cannot distinguish a policy that is
    learning from one collapsing onto a degenerate action, so the aggregated
    summary reports the direction the policy moved relative to the behavior
    that produced the data, and how much of the gradient V-trace truncated.
    Absent fields are omitted rather than printed as zero.
    """
    health = payload.get("learning_health_last")
    if not isinstance(health, Mapping):
        return []
    shift = health.get("mean_log_probability_shift")
    if shift is None:
        return []
    shift = float(shift)
    direction = (
        "toward the collected behavior" if shift > 0.0
        else "away from the collected behavior" if shift < 0.0
        else "unchanged relative to the collected behavior"
    )
    lines = [f"  learning: mean log-prob shift={shift:+.4f} ({direction})"]
    target, behavior = health.get("mean_target_log_probability"), health.get("mean_behavior_log_probability")
    if target is not None and behavior is not None:
        lines.append(
            f"            mean log-prob: target={float(target):.4f} behavior={float(behavior):.4f}"
        )
    clipped = health.get("clipped_importance_fraction")
    if clipped is not None:
        clipped = float(clipped)
        note = "  <- nearly every step's gradient is truncated" if clipped > 0.9 else ""
        lines.append(f"            V-trace importance clipped high={clipped:.3f}{note}")
    vanished = health.get("vanishing_importance_fraction")
    if vanished is not None:
        vanished = float(vanished)
        lines.append(f"            V-trace importance vanished (rho<0.01)={vanished:.3f}")
        if vanished > 0.5:
            lines.append(
                "            WARNING: the policy has moved far enough from the collected "
                "behavior that V-trace scales most of its gradient to zero. Further steps "
                "will change little; this run is no longer learning."
            )
    ratio = health.get("mean_importance_ratio")
    continuation = health.get("mean_continuation_c")
    if ratio is not None and continuation is not None:
        lines.append(
            f"            mean importance ratio={float(ratio):.4f} "
            f"mean continuation c={float(continuation):.4f}"
        )
    opponent_values = health.get("opponent_state_value_means")
    if isinstance(opponent_values, list) and len(opponent_values) > 1:
        lines.append(f"            critic opponent strata={len(opponent_values)}")
    return lines


def _format_train_from_trajectories_summary_v1(payload: Mapping[str, object]) -> str:
    """Render ``run_train_from_trajectories_v1``'s payload as a short, human-readable report.

    Never prints the raw per-step ``loss_trajectory``/``gradient_norms`` arrays
    -- only their last values and totals. The full payload (identical to what
    was already written to ``run_summary.json``) remains available verbatim
    via ``--json``.
    """
    wall_time_seconds = float(payload["wall_time_seconds"])
    steps_taken = int(payload["steps_taken_this_run"])
    throughput = steps_taken / wall_time_seconds if wall_time_seconds > 0 and steps_taken > 0 else 0.0
    loss_trajectory: Sequence[object] = payload["loss_trajectory"]
    gradient_norms: Sequence[object] = payload["gradient_norms"]
    scoring_failures: Sequence[object] = payload["scoring_failures_this_run"]

    lines = [
        f"train-from-trajectories: run={payload['run_name']} device={payload['device']}",
        (
            f"  steps: {payload['step_before']}->{payload['step_after']} (budget={payload['max_steps']}) "
            f"taken={steps_taken} skipped={payload['steps_skipped_this_run']} resumed={payload['resumed']}"
        ),
        (
            f"  games: found={payload['games_found']} unreadable={payload['games_unreadable']} "
            f"admitted={payload['games_admitted']} dropped_stale={payload['games_dropped_stale']}"
        ),
        (
            f"  transitions: admitted_total={payload['transitions_admitted_total']} "
            f"consumed_this_run={payload['transitions_consumed_this_run']}"
        ),
        f"  last: loss={loss_trajectory[-1] if loss_trajectory else None} grad_norm={gradient_norms[-1] if gradient_norms else None}",
        (
            f"  scoring_failures_this_run={len(scoring_failures)}"
            + (" (truncated)" if payload["scoring_failures_this_run_truncated"] else "")
        ),
        f"  wall_time={wall_time_seconds:.1f}s throughput={throughput:.2f} steps/s",
        *_format_learning_health_lines_v1(payload),
        f"  checkpoint: {payload['checkpoint_sha256']} at {payload['checkpoint_path']}",
        f"  run_summary:      {payload['run_summary_path']}",
        f"  progress_summary: {payload['progress_summary_path']}",
        "  (pass --json for the full machine-readable payload)",
    ]
    return "\n".join(lines)


_AGGREGATED_SUMMARY_FORMATTERS_V1: dict[str, Callable[[Mapping[str, object]], str]] = {
    "collect-trajectories": _format_collect_trajectories_summary_v1,
    "train-from-trajectories": _format_train_from_trajectories_summary_v1,
}


def _cmd_collect_trajectories(args: argparse.Namespace) -> dict[str, object]:
    try:
        return run_collect_trajectories_v1(
            lanes_arg=args.lanes,
            num_games=args.num_games,
            base_seed=args.base_seed,
            workers=args.workers,
            run_name=args.run_name,
            persistent_worker=args.persistent_worker,
            behavior_kind=args.behavior_kind,
            neural_checkpoint_path=args.neural_checkpoint_path,
            decoding_mode=args.decoding_mode,
            sampling_seed=args.sampling_seed,
            timeout_seconds=args.timeout_seconds,
            max_steps=args.max_steps,
            opponent_kind=args.opponent_kind,
            opponent_kinds=args.opponent_kinds,
            opponent_schedule=args.opponent_schedule,
            pool_epoch=args.pool_epoch,
            policy_lag=args.policy_lag,
            non_terminal_discount=args.non_terminal_discount,
            source_commit=args.source_commit,
            seed_qualification_report_path=Path(args.seed_qualification_report),
            materialized_deck_dir=Path(args.materialized_deck_dir),
        )
    except CollectTrajectoriesError as exc:
        raise CliError("CONTRACT_ERROR", f"could not plan trajectory collection: {exc}") from exc
    except ActorPoolV1Error as exc:
        raise CliError("CONTRACT_ERROR", f"could not run trajectory collection: {exc}") from exc


def _cmd_train_from_trajectories(args: argparse.Namespace) -> dict[str, object]:
    try:
        return run_train_from_trajectories_v1(
            collection_run_dir=Path(args.collection_run_dir),
            run_name=args.run_name,
            max_steps=args.max_steps,
            current_pool_epoch=args.current_pool_epoch,
            recipe_max_age=args.recipe_max_age,
            trajectories_per_step=args.trajectories_per_step,
            microbatch_trajectories=args.microbatch_trajectories,
            optimizer_kind=args.optimizer,
            learning_rate=args.learning_rate,
            rho_bar=args.rho_bar,
            c_bar=args.c_bar,
            value_coefficient=args.value_coefficient,
            advantage_normalization=args.advantage_normalization,
            max_gradient_norm=args.max_gradient_norm,
            hidden_dim=args.hidden_dim,
            card_dim=args.card_dim,
            symbol_dim=args.symbol_dim,
            seed=args.seed,
            device=args.device,
            checkpoint_interval_steps=args.checkpoint_interval_steps,
            source_commit=args.source_commit,
            progress=args.progress,
            torch_threads=args.torch_threads,
            **(
                {} if args.entropy_coefficient is None
                else {"entropy_coefficient": args.entropy_coefficient}
            ),
            **(
                {} if args.bc_coefficient is None
                else {"bc_coefficient": args.bc_coefficient}
            ),
            bootstrap_checkpoint_path=args.bootstrap_checkpoint or "",
        )
    except TrainFromTrajectoriesV1Error as exc:
        raise CliError("CONTRACT_ERROR", f"could not run trajectory-based training: {exc}") from exc


def _cmd_verify_submission(args: argparse.Namespace) -> dict[str, object]:
    try:
        report = verify_specialist_archive(Path(args.archive))
    except BundleSecurityError as exc:
        raise CliError("SECURITY_ERROR", f"could not verify submission archive: {exc}") from exc
    except BundleContractError as exc:
        raise CliError("CONTRACT_ERROR", f"could not verify submission archive: {exc}") from exc
    return report.to_payload()


class _ArgumentParser(argparse.ArgumentParser):
    """Raises :class:`CliError` instead of printing usage text and calling exit."""

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        raise CliError("ARGUMENT_ERROR", message)


def _build_parser() -> _ArgumentParser:
    parser = _ArgumentParser(
        prog="meta-specialist",
        description="Local, network-free build/verify CLI for the meta-specialist submission bundle.",
    )
    subparsers = parser.add_subparsers(dest="command")

    show_constraints = subparsers.add_parser(
        "show-runtime-constraints", help="Print the frozen v1 runtime constraint manifest.",
    )
    show_constraints.set_defaults(handler=_cmd_show_runtime_constraints)

    cleanup = subparsers.add_parser(
        "cleanup-plan",
        help="Audit deletion candidates and emit a cleanup manifest. Deletes nothing.",
    )
    cleanup.add_argument("--repo-root", default=str(_REPO_ROOT_FOR_CLEANUP))
    cleanup.add_argument(
        "--path", action="append", default=[],
        help="Repository-relative file to audit. Repeatable. Globs are refused.",
    )
    cleanup.add_argument("--regenerable-by", default="",
                         help="Command that can recreate this artifact, if any.")
    cleanup.add_argument("--retention-reason", default="superseded",
                         help="Why this artifact may go.")
    cleanup.add_argument("--restorable", action="store_true",
                         help="Set when the artifact is recoverable from Git or a backup.")
    cleanup.add_argument(
        "--allow-dirty-worktree", action="store_true",
        help="Plan even on a dirty worktree. Still deletes nothing.",
    )
    cleanup.set_defaults(handler=_cmd_cleanup_plan)

    show_ladder = subparsers.add_parser(
        "show-ladder-contract", help="Print the versioned official ladder mechanics contract.",
    )
    show_ladder.add_argument(
        "--checked-at-utc", required=True,
        help="Explicit RFC 3339 UTC timestamp (ending in Z) recording when this was manually checked.",
    )
    show_ladder.set_defaults(handler=_cmd_show_ladder_contract)

    qualify = subparsers.add_parser(
        "qualify-deck", help="Qualify one deck asset against a registry, known card IDs, and pre-measured CABT evidence.",
    )
    qualify.add_argument("--asset-json", required=True, help="Path to a JSON object describing the deck asset provenance.")
    qualify.add_argument("--registry", required=True, help="Path to a meta-specialist-archetypes-v1 registry JSON file.")
    qualify.add_argument("--known-card-ids", required=True, help="Path to the official card-data CSV (a 'Card ID' column).")
    qualify.add_argument(
        "--cabt-evidence-json", required=True,
        help="Path to a meta-specialist-cabt-deck-evidence-v1 JSON file recording a real, already-measured CABT result.",
    )
    qualify.set_defaults(handler=_cmd_qualify_deck)

    lock = subparsers.add_parser("lock-deck", help="Create a content-addressed DeckLockDecision.")
    lock.add_argument("--archetype-id", required=True)
    lock.add_argument("--selected-deck-identity", required=True)
    lock.add_argument(
        "--compared-deck-identities", required=True,
        help="Comma-separated list of deck identities compared in the race (must include the selected one).",
    )
    lock.add_argument("--foundation-init-id", required=True)
    lock.add_argument("--joint-race-schedule-id", required=True)
    lock.add_argument("--equal-transition-budget", required=True, type=int)
    lock.set_defaults(handler=_cmd_lock_deck)

    build = subparsers.add_parser(
        "build-submission", help="Build and structurally verify a submission archive from a local bundle spec.",
    )
    build.add_argument("--spec", required=True, help="Path to a meta-specialist-bundle-spec-v1 JSON file.")
    build.add_argument("--output", required=True, help="Output .tar.gz path for the built archive.")
    build.set_defaults(handler=_cmd_build_submission)

    verify = subparsers.add_parser(
        "verify-submission", help="Structurally verify one archive without importing or executing it.",
    )
    verify.add_argument("--archive", required=True, help="Path to a previously built submission archive.")
    verify.set_defaults(handler=_cmd_verify_submission)

    collect = subparsers.add_parser(
        "collect-trajectories",
        help=(
            "Drive the real ActorPoolV1 to collect seat-balanced trajectories for one or more "
            "qualified archetype lanes. Resumable: a game already completed under the same "
            "--run-name is skipped, never re-collected."
        ),
    )
    collect.add_argument(
        "--lanes", default="all",
        help="'all' (every archetype the seed qualification report marks 'qualified') or a "
             "comma-separated list of archetype ids, e.g. 'alakazam,rocket_mewtwo_spidops'.",
    )
    collect.add_argument(
        "--num-games", required=True, type=int,
        help="Total games to plan across the selected lanes (distributed as evenly as possible, "
             "then split evenly across seats within each lane).",
    )
    collect.add_argument(
        "--base-seed", required=True, type=int,
        help="First env_seed of the run; every planned game gets a distinct, deterministic seed "
             "starting here, so re-running the identical command reproduces the identical plan.",
    )
    collect.add_argument("--workers", type=int, default=2, help="Concurrent worker processes (ActorPoolV1 num_workers).")
    collect.add_argument(
        "--persistent-worker", action="store_true",
        help="Reuse one process across many games instead of spawning one per game. "
             "Off by default because it gives up the per-game OS-level hard kill: a hung "
             "game can then only be bounded by the runtime's own cooperative deadline. "
             "Use it only under an external wall-clock timeout. Measured on 28 cores, the "
             "default path spends about half of a serial game's wall time on process start "
             "and torch import, and worker utilisation falls to 23%% at 28 workers.",
    )
    collect.add_argument(
        "--run-name", required=True,
        help="Single path-component name; artifacts are written under "
             "runs/meta-specialist-actor-pool/<run-name>/ only.",
    )
    collect.add_argument("--behavior-kind", choices=("rule_agent", "neural_specialist"), default="rule_agent")
    collect.add_argument(
        "--neural-checkpoint-path", default="",
        help="Required when --behavior-kind neural_specialist; path to a checkpoint file.",
    )
    collect.add_argument("--decoding-mode", choices=("greedy", "sample"), default="greedy")
    collect.add_argument("--sampling-seed", type=int, default=0, help="Used only when --decoding-mode sample.")
    collect.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS_V1, help="Per-game hard timeout.")
    collect.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS_V1, help="Per-game engine step cap.")
    collect.add_argument("--opponent-kind", default="cabt_rule_agent_v0")
    collect.add_argument(
        "--opponent-schedule", default="",
        help="JSON file mapping opponent id -> games per cycle. Overrides both "
             "--opponent-kind and --opponent-kinds. Full coverage of the observed "
             "meta is not the same as matching it: a uniform cycle over the widened "
             "pool still gives the archetype that is 37.1%% of the medal zone only "
             "4.8%% of the games. Generate one with scripts/make_opponent_schedule.py.",
    )
    collect.add_argument(
        "--opponent-kinds", default="",
        help="Comma-separated opponent ids to cycle across the planned games, "
             "overriding --opponent-kind. Seats come from the cycle number, so a "
             "rotation stays seat-balanced per matchup. Use this to train against "
             "the same distribution you measure against: a run collected only "
             "against cabt_rule_agent_v0 beat that opponent and lost 0.448 -> 0.281 "
             "to the evaluation pool (docs/evidence/vtrace-rl-degrades-against-eval-pool-20260807.md).",
    )
    collect.add_argument("--pool-epoch", type=int, default=0)
    collect.add_argument("--policy-lag", type=int, default=0)
    collect.add_argument("--non-terminal-discount", type=float, default=1.0)
    collect.add_argument(
        "--source-commit", default=None,
        help="Defaults to the exact checked-out commit of this worktree (git rev-parse HEAD).",
    )
    collect.add_argument(
        "--seed-qualification-report", default=str(DEFAULT_SEED_QUALIFICATION_REPORT_PATH_V1),
        help="Path to a meta-specialist-seed-qualification-report-v1 JSON file.",
    )
    collect.add_argument(
        "--materialized-deck-dir", default=str(DEFAULT_MATERIALIZED_DECK_DIR_V1),
        help="Directory containing the materialized qualified deck CSVs the report refers to.",
    )
    collect.add_argument(
        "--json", action="store_true",
        help="Print the full machine-readable run-summary JSON to stdout instead of the "
             "default compact, aggregated human-readable summary.",
    )
    collect.set_defaults(handler=_cmd_collect_trajectories)

    train = subparsers.add_parser(
        "train-from-trajectories",
        help=(
            "Take real V-trace optimizer steps over trajectories already collected by "
            "collect-trajectories. Resumable: re-running the same command against an "
            "existing --run-name continues from its stored step, never restarts from zero."
        ),
    )
    train.add_argument(
        "--collection-run-dir", required=True,
        help="A collect-trajectories output root, e.g. "
             "runs/meta-specialist-actor-pool/<run-name> (must contain games/*/record.json).",
    )
    train.add_argument(
        "--run-name", required=True,
        help="Single path-component name; artifacts are written under "
             "runs/meta-specialist-training/<run-name>/ only.",
    )
    train.add_argument(
        "--max-steps", required=True, type=int,
        help="Target cumulative optimizer-step count for this --run-name. Resuming an "
             "existing run at or beyond this step takes zero further steps.",
    )
    train.add_argument("--current-pool-epoch", type=int, default=0)
    train.add_argument("--recipe-max-age", type=int, default=0)
    train.add_argument(
        "--trajectories-per-step", type=int, default=None,
        help="Trajectories (game records) per optimizer step. Default: every admitted "
             "trajectory, every step. Must not exceed the number admitted.",
    )
    train.add_argument(
        "--microbatch-trajectories", type=int, default=None,
        help="Initial OOM-defense chunk size within one minibatch; shrinks on retry. "
             "Default: the whole minibatch at once.",
    )
    train.add_argument("--optimizer", choices=("adamw", "sgd"), default="adamw")
    train.add_argument("--learning-rate", type=float, default=1.0e-3)
    train.add_argument("--rho-bar", type=float, default=1.0)
    train.add_argument("--c-bar", type=float, default=1.0)
    train.add_argument(
        "--value-coefficient", type=float, default=0.5,
        help="Weight of the value-regression term. This is NOT inert: "
             "`SpecialistPolicyModelV1` has a value head, this loop passes "
             "`state_value` into `evaluate_trajectory_loss_v1`, and V-trace uses the "
             "current learner's V(x) as its baseline, so the term carries real "
             "gradient. Setting it to 0 leaves the policy gradient without a fitted "
             "baseline. See the `train_from_trajectories_v1` module docstring, which "
             "records why the old wording here ('no value head exists yet') was wrong.",
    )
    train.add_argument(
        "--advantage-normalization",
        choices=ADVANTAGE_NORMALIZATION_MODES_V1, default="none",
        help="Rescale the V-trace advantage before it weights the policy gradient, "
             "using the previous step's minibatch moments. 'none' (default) keeps "
             "the historical update. 'center' subtracts the mean, removing the "
             "common downward push a losing-heavy corpus puts on every collected "
             "action. 'standardize' also divides by the standard deviation, so the "
             "step size stops shrinking as the advantage spread does (measured: "
             "0.333 -> 0.229 over 6 rounds). Dividing makes the advantage roughly "
             "4x larger, so --bc-coefficient anchors correspondingly more weakly -- "
             "watch dlogp.",
    )
    train.add_argument("--max-gradient-norm", type=float, default=1.0)
    train.add_argument("--hidden-dim", type=int, default=128)
    train.add_argument("--card-dim", type=int, default=64)
    train.add_argument("--symbol-dim", type=int, default=16)
    train.add_argument("--seed", type=int, default=0)
    train.add_argument("--device", default="cpu", help="e.g. 'cpu' or 'cuda'. Never required to be CUDA.")
    train.add_argument("--checkpoint-interval-steps", type=int, default=10)
    train.add_argument(
        "--source-commit", default=None,
        help="Defaults to the exact checked-out commit of this worktree (git rev-parse HEAD).",
    )
    train.add_argument(
        "--bootstrap-checkpoint", default="",
        help="θ0 checkpoint from BC distillation to start from. Weights only: the "
             "optimizer, scheduler, RNG, step, and sampler cursor all start fresh, "
             "because θ0 was fitted under a supervised objective and this run is "
             "V-trace. A topology mismatch fails rather than partially loading. The "
             "run's checkpoints record init_kind=warm_start with θ0 as the parent, so "
             "the teacher -> θ0 -> run lineage stays readable.",
    )
    train.add_argument(
        "--bc-coefficient", type=float, default=None,
        help="Weight on the behavior-cloning anchor (default 0.1). Without it, offline "
             "training on a fixed corpus drives the collected actions' log-probabilities "
             "down without bound until the importance ratios collapse and learning stops. "
             "0.0 disables the anchor; 0.5+ was measured to saturate into pure cloning.",
    )
    train.add_argument(
        "--entropy-coefficient", type=float, default=None,
        help="Weight on the policy-entropy bonus (default 0.01). Keeps the policy from "
             "collapsing onto a single candidate. 0.0 disables it entirely.",
    )
    train.add_argument(
        "--torch-threads", type=int, default=None,
        help="Intra-op threads for scoring (default 1). This model's ops are small enough "
             "that torch's per-core default costs more in synchronization than it saves: "
             "measured 4.0x slower at 14 threads than at 1 on a real 64-game minibatch.",
    )
    train.add_argument(
        "--progress", dest="progress", action="store_true", default=None,
        help="Force the live single-line progress bar even when stderr is redirected "
             "(e.g. through tee). Without either flag, a TTY gets the bar and a "
             "redirected stream gets periodic aggregated snapshot lines instead.",
    )
    train.add_argument(
        "--no-progress", dest="progress", action="store_false",
        help="Suppress all live progress output. The run-summary and "
             "progress_summary.json artifacts are still written.",
    )
    train.add_argument(
        "--json", action="store_true",
        help="Print the full machine-readable run-summary JSON to stdout instead of the "
             "default compact, aggregated human-readable summary.",
    )
    train.set_defaults(handler=_cmd_train_from_trajectories)

    # Subcommand: calibrate
    calibrate = subparsers.add_parser("calibrate", help="Calibrate proxy opponent strength and confidence intervals.")
    calibrate.add_argument("--samples", type=int, default=100)
    calibrate.add_argument("--wins", type=int, default=50)
    calibrate.set_defaults(handler=lambda args: orchestrator_v1.dispatch_stage_handler_v1("calibrate", {"samples": args.samples, "wins": args.wins}))

    # Subcommand: joint-opt
    joint_opt = subparsers.add_parser("joint-opt", help="Verify foundation init decision for joint deck-policy race.")
    joint_opt.add_argument("--commit", required=True)
    joint_opt.set_defaults(handler=lambda args: orchestrator_v1.dispatch_stage_handler_v1("joint_opt", {"commit": args.commit}))

    # Subcommand: race
    race = subparsers.add_parser("race", help="Execute cross-archetype global race and select primary/backup submission.")
    race.add_argument("--primary", required=True)
    race.add_argument("--backup", required=True)
    race.set_defaults(handler=lambda args: orchestrator_v1.dispatch_stage_handler_v1("race", {"primary_id": args.primary, "backup_id": args.backup}))

    # Subcommand: orchestrate
    orchestrate = subparsers.add_parser("orchestrate", help="Run durable orchestrator task pipeline across stages.")
    orchestrate.add_argument("--stage", choices=("collect", "train", "evaluate", "promote", "curriculum"), default="evaluate")
    orchestrate.set_defaults(handler=lambda args: orchestrator_v1.dispatch_stage_handler_v1(args.stage, {}))

    return parser


def _emit_error(error_type: str, message: str) -> None:
    document = {"status": "ERROR", "error_type": error_type, "message": _sanitize_message(message)}
    sys.stderr.write(_canonical_json(document) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        handler = getattr(args, "handler", None)
        if handler is None:
            raise CliError("ARGUMENT_ERROR", "a subcommand is required")
        payload = handler(args)
    except CliError as exc:
        _emit_error(exc.error_type, exc.message)
        return 2
    formatter = _AGGREGATED_SUMMARY_FORMATTERS_V1.get(getattr(args, "command", None))
    if formatter is not None and not getattr(args, "json", False):
        sys.stdout.write(formatter(payload) + "\n")
    else:
        sys.stdout.write(_canonical_json(payload) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["CliError", "main"]
