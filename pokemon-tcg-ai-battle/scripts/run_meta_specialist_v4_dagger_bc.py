#!/usr/bin/env python3
"""Research-only V4 DAgger fine-tuning from a sealed actor screen.

The screen is collected with the current V4 policy, then every visited public
state is relabelled by the sealed rule teacher.  The base sealed selection is
mixed with those complete game episodes and fine-tuned from an existing V4
checkpoint.  This script never promotes a checkpoint and refuses faults,
missing hashes, split overlap, or malformed transition payloads.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import build_rule_agent_policy_factory_v1  # noqa: E402
from mage_ptcg.meta_specialist.dagger_v4 import (  # noqa: E402
    dagger_record_sha256_v4,
    merge_dagger_episode_sequences_v4,
    mix_dagger_sequences_v4,
    parse_transition_payload_v4,
    prioritized_dagger_component_ids_v4,
    relabel_transition_v4,
    strict_disagreement_metadata_v4,
)
from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    ACTION_BALANCED_WEIGHTS_V1,
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    materialize_fast_research_uniform_subset_v4,
    positive_stop_target_metrics_v4,
    selected_objective_sha256_v4,
    train_recurrent_bc_v4,
    trainer_implementation_sha256_v4,
)
from mage_ptcg.meta_specialist.v4_imitation_metrics import (  # noqa: E402
    evaluate_recurrent_imitation_v4,
)


SCREEN_SCHEMA_V4 = "meta-specialist-v4-dagger-screen-v2"
TRANSITION_SCHEMA_V4 = "meta-specialist-v4-dagger-transition-v1"
REPORT_SCHEMA_V4 = "meta-specialist-v4-dagger-bc-report-v1"
PAIRED_SEED_MANIFEST_SCHEMA_V4 = "meta-specialist-v4-dagger-paired-seed-manifest-v1"
_HEX = frozenset("0123456789abcdef")
DEFAULT_FOCUS_OPPONENTS_V4 = ("ozawa_crustle_v2",)
DEFAULT_FOCUS_SEATS_V4 = (1,)
DEFAULT_FOCUS_ACTION_TYPES_V4 = (9, 13, 14)


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _parse_focus_names(value: str) -> tuple[str, ...]:
    if type(value) is not str:
        raise argparse.ArgumentTypeError("focus names must be comma-separated strings")
    values = tuple(item.strip() for item in value.split(",") if item.strip())
    if len(set(values)) != len(values):
        raise argparse.ArgumentTypeError("focus names must not contain duplicates")
    return values


def _parse_focus_ints(value: str, *, field: str, minimum: int, maximum: int) -> tuple[int, ...]:
    if type(value) is not str:
        raise argparse.ArgumentTypeError(f"{field} must be comma-separated integers")
    try:
        values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{field} must be comma-separated integers") from exc
    if any(item < minimum or item > maximum for item in values) or len(set(values)) != len(values):
        raise argparse.ArgumentTypeError(f"{field} contains an out-of-range or duplicate value")
    return values


def _parse_action_type_weights(value: str) -> dict[str, float] | None:
    """Parse an explicit research arm without silently changing the default objective."""
    if type(value) is not str:
        raise argparse.ArgumentTypeError("action-type weights must be a mapping or none")
    if value in {"none", "uniform"}:
        return None
    if value == "balanced_v1":
        return dict(ACTION_BALANCED_WEIGHTS_V1)
    result: dict[str, float] = {}
    for item in value.split(","):
        pair = item.strip()
        if not pair or "=" not in pair:
            raise argparse.ArgumentTypeError("action-type weights must use type=value pairs")
        key, raw = (part.strip() for part in pair.split("=", 1))
        if key != "STOP":
            try:
                numeric_key = int(key)
            except ValueError as exc:
                raise argparse.ArgumentTypeError("action-type weight key is invalid") from exc
            if not 0 <= numeric_key <= 16 or str(numeric_key) != key:
                raise argparse.ArgumentTypeError("action-type weight key is invalid")
        if key in result:
            raise argparse.ArgumentTypeError("action-type weights contain duplicates")
        try:
            numeric = float(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("action-type weight value is invalid") from exc
        if not math.isfinite(numeric) or numeric <= 0.0:
            raise argparse.ArgumentTypeError("action-type weight value must be finite and positive")
        result[key] = numeric
    if not result:
        raise argparse.ArgumentTypeError("action-type weights cannot be empty")
    return result


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
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


def _write_progress(path: Path | None, payload: Mapping[str, object]) -> None:
    if path is not None:
        _atomic_json(path, {
            "schema": "meta-specialist-v4-dagger-bc-progress-v1",
            "updated_unix": time.time(),
            **dict(payload),
        })


def _make_training_progress_callback(
    path: Path | None, *, seed: int, epochs: int, started: float,
    write_interval_seconds: float = 10.0,
) -> object:
    """Return a throttled in-epoch heartbeat writer with a mandatory final flush."""
    last_write = 0.0

    def callback(payload: Mapping[str, object]) -> None:
        nonlocal last_write
        completed = int(payload.get("sequences_completed", 0))
        total = int(payload.get("sequences_total", 0))
        now = time.monotonic()
        if completed < total and now - last_write < write_interval_seconds:
            return
        last_write = now
        _write_progress(path, {
            "status": "running", "stage": "training", "seed": seed,
            "epochs_requested": epochs, "elapsed_seconds": now - started,
            **dict(payload),
        })

    return callback


def _validation_imitation_metrics_v4(
    model: SpecialistModelV4, validation: Sequence[object],
) -> dict[str, object]:
    """Return the sealed carry-state action metrics for one trained seed.

    The DAgger report must carry the same action diagnostics used by the
    promotion gate.  Keeping this call next to checkpoint training prevents a
    completed BC report from being mistaken for a performance-qualified arm
    when the separate offline metrics pass was forgotten.
    """
    return evaluate_recurrent_imitation_v4(
        model, validation, partition="validation", recurrence="carry",
    )


def _summarize_dagger_mixture(
    *, base: Sequence[object], dagger: Sequence[object], mixed: Sequence[object],
) -> dict[str, int | float]:
    """Report the selected overlay ratio, not the available overlay size."""
    base_ids = tuple(getattr(row, "component_id", None) for row in base)
    dagger_ids = tuple(getattr(row, "component_id", None) for row in dagger)
    mixed_ids = tuple(getattr(row, "component_id", None) for row in mixed)
    if any(type(value) is not str for value in (*base_ids, *dagger_ids, *mixed_ids)):
        raise ValueError("DAgger mixture component identity is missing")
    if len(set(base_ids)) != len(base_ids) or len(set(dagger_ids)) != len(dagger_ids):
        raise ValueError("DAgger mixture contains duplicate available components")
    if set(base_ids) & set(dagger_ids):
        raise ValueError("DAgger mixture base and overlay components overlap")
    available = set(base_ids) | set(dagger_ids)
    if len(set(mixed_ids)) != len(mixed_ids) or not set(mixed_ids) <= available:
        raise ValueError("DAgger mixture contains an unknown or duplicate selected component")
    base_selected = sum(value in set(base_ids) for value in mixed_ids)
    dagger_selected = sum(value in set(dagger_ids) for value in mixed_ids)
    return {
        "base_available": len(base_ids),
        "dagger_available": len(dagger_ids),
        "base_selected": base_selected,
        "dagger_selected": dagger_selected,
        "dagger_fraction_actual": dagger_selected / len(mixed_ids) if mixed_ids else 0.0,
    }


def _training_material_v4(material: Mapping[str, object]) -> dict[str, object]:
    """Resolve one seed's sealed inputs without falling back to another seed.

    Paired-screen mode deliberately binds a different screen/checkpoint to each
    RNG seed.  Keeping this normalization in one helper prevents the training
    loop from accidentally reusing the last materialized seed (the old code did
    so via undefined ``args.init_checkpoint``/``init_file_sha`` names).
    """
    if not isinstance(material, Mapping):
        raise ValueError("seed training material must be a mapping")
    binding = material.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError("seed training material has no binding")
    required_sha = (
        "init_checkpoint_file_sha256", "init_checkpoint_tensor_state_sha256",
        "screen_file_sha256", "transitions_file_sha256",
    )
    for field in required_sha:
        _sha(binding.get(field), field=field)
    path_fields = ("init_checkpoint_path", "screen_path", "transitions_path")
    resolved: dict[str, object] = {
        field: Path(str(binding.get(field))) for field in path_fields
    }
    resolved.update({field: str(binding[field]) for field in required_sha})
    for field in (
        "dagger", "mixed", "train", "validation", "focus_component_ids",
    ):
        value = material.get(field)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError(f"seed training material {field} is not a sequence")
        resolved[field] = tuple(value)
    mixture_summary = material.get("mixture_summary")
    if not isinstance(mixture_summary, Mapping):
        raise ValueError("seed training material mixture summary is invalid")
    selected_sha = _sha(material.get("selected_sha"), field="selected_sequence_sha256")
    dagger_sha = _sha(material.get("dagger_sha"), field="dagger_sequence_sha256")
    resolved["mixture_summary"] = dict(mixture_summary)
    resolved["selected_sha"] = selected_sha
    resolved["dagger_sha"] = dagger_sha
    resolved["strict_target_report"] = material.get("strict_target_report")
    resolved["strict_disagreement_report"] = material.get("strict_disagreement_report")
    return resolved


def _read_hashed_json(path: Path, expected_sha: str, *, field: str) -> dict[str, object]:
    expected = _sha(expected_sha, field=f"{field}_sha256")
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError(f"{field} bytes do not match the external SHA anchor")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not valid JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{field} must be a JSON object")
    return value


def _manifest_member_path_v4(value: object, *, field: str, manifest_path: Path) -> str:
    """Resolve one explicit paired-manifest artifact path without guessing roots."""
    if type(value) is not str or not value:
        raise ValueError(f"paired seed {field} path is invalid")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return str(candidate.resolve())


def _resolve_paired_seed_provenance_v4(
    *,
    seeds: Sequence[int],
    lane: str,
    manifest_path: Path,
    manifest_file_sha256: str,
) -> tuple[tuple[dict[str, object], ...], dict[str, str]]:
    """Load a hash-anchored, exact seed-to-screen/checkpoint mapping.

    This is deliberately an opt-in input form.  A repeated training seed may
    never silently inherit another seed's actor screen or warm-start identity.
    The referenced artifacts are opened and cryptographically checked later,
    before any checkpoint directory is created.
    """
    payload = _read_hashed_json(manifest_path, manifest_file_sha256, field="paired_seed_manifest")
    if set(payload) != {"schema", "lane", "seed_provenance"}:
        raise ValueError("paired seed manifest has an open schema")
    if payload.get("schema") != PAIRED_SEED_MANIFEST_SCHEMA_V4:
        raise ValueError("paired seed manifest schema is invalid")
    if payload.get("lane") != lane:
        raise ValueError("paired seed manifest lane differs from the requested lane")
    raw_bindings = payload.get("seed_provenance")
    if type(raw_bindings) is not list:
        raise ValueError("paired seed manifest provenance must be a list")
    expected_seeds = tuple(seeds)
    if len(expected_seeds) != len(set(expected_seeds)):
        raise ValueError("paired seed manifest requires distinct requested seeds")

    bindings_by_seed: dict[int, dict[str, object]] = {}
    for raw_binding in raw_bindings:
        if type(raw_binding) is not dict or set(raw_binding) != {
            "seed", "screen", "transitions", "init_checkpoint",
        }:
            raise ValueError("paired seed manifest binding has an open schema")
        seed = raw_binding.get("seed")
        if type(seed) is not int:
            raise ValueError("paired seed manifest seed is invalid")
        if seed in bindings_by_seed:
            raise ValueError("paired seed manifest contains duplicate seed provenance")

        screen = raw_binding.get("screen")
        transitions = raw_binding.get("transitions")
        checkpoint = raw_binding.get("init_checkpoint")
        if type(screen) is not dict or set(screen) != {"path", "file_sha256"}:
            raise ValueError("paired seed screen binding has an open schema")
        if type(transitions) is not dict or set(transitions) != {"path", "file_sha256"}:
            raise ValueError("paired seed transitions binding has an open schema")
        if type(checkpoint) is not dict or set(checkpoint) != {
            "path", "file_sha256", "tensor_state_sha256",
        }:
            raise ValueError("paired seed init checkpoint binding has an open schema")
        bindings_by_seed[seed] = {
            "seed": seed,
            "screen_path": _manifest_member_path_v4(
                screen.get("path"), field="screen", manifest_path=manifest_path,
            ),
            "screen_file_sha256": _sha(screen.get("file_sha256"), field="paired_seed.screen_file_sha256"),
            "transitions_path": _manifest_member_path_v4(
                transitions.get("path"), field="transitions", manifest_path=manifest_path,
            ),
            "transitions_file_sha256": _sha(
                transitions.get("file_sha256"), field="paired_seed.transitions_file_sha256",
            ),
            "init_checkpoint_path": _manifest_member_path_v4(
                checkpoint.get("path"), field="init_checkpoint", manifest_path=manifest_path,
            ),
            "init_checkpoint_file_sha256": _sha(
                checkpoint.get("file_sha256"), field="paired_seed.init_checkpoint_file_sha256",
            ),
            "init_checkpoint_tensor_state_sha256": _sha(
                checkpoint.get("tensor_state_sha256"),
                field="paired_seed.init_checkpoint_tensor_state_sha256",
            ),
        }
    if set(bindings_by_seed) != set(expected_seeds):
        raise ValueError("paired seed manifest seed coverage differs from the requested seeds")
    return (
        tuple(bindings_by_seed[seed] for seed in expected_seeds),
        {
            "mode": "paired_seed_manifest",
            "path": str(manifest_path.resolve()),
            "file_sha256": _sha(manifest_file_sha256, field="paired_seed_manifest_sha256"),
        },
    )


def _validate_dagger_seed_checkpoint_binding_v4(
    screen: Mapping[str, object], *, binding: Mapping[str, object],
) -> None:
    """Reject a screen whose sealed actor checkpoint differs from its seed init."""
    checkpoint_binding = screen.get("checkpoint")
    if type(checkpoint_binding) is not dict:
        raise ValueError("DAgger screen has no closed checkpoint binding")
    expected_file_sha = _sha(
        binding.get("init_checkpoint_file_sha256"), field="init_checkpoint_file_sha256",
    )
    expected_tensor_sha = _sha(
        binding.get("init_checkpoint_tensor_state_sha256"),
        field="init_checkpoint_tensor_state_sha256",
    )
    if (
        checkpoint_binding.get("file_sha256") != expected_file_sha
        or checkpoint_binding.get("tensor_state_sha256") != expected_tensor_sha
    ):
        raise ValueError("DAgger screen checkpoint identity differs from the warm-start checkpoint")


def _paired_selected_sequence_identity_v4(
    seed_records: Sequence[Mapping[str, object]], *, paired_manifest_identity: Mapping[str, object],
) -> str:
    """Hash all per-seed selected data identities and their sealed input provenance."""
    manifest = dict(paired_manifest_identity)
    if set(manifest) != {"mode", "path", "file_sha256"} or manifest.get("mode") != "paired_seed_manifest":
        raise ValueError("paired selected sequence identity requires a closed manifest identity")
    _sha(manifest.get("file_sha256"), field="paired_seed_manifest_sha256")
    normalized: list[dict[str, object]] = []
    required = {
        "seed", "screen_path", "screen_file_sha256", "transitions_path", "transitions_file_sha256",
        "init_checkpoint_path", "init_checkpoint_file_sha256", "init_checkpoint_tensor_state_sha256",
        "selected_sequence_sha256", "dagger_sequence_sha256",
    }
    for raw_record in seed_records:
        if set(raw_record) != required:
            raise ValueError("paired selected sequence identity record has an open schema")
        seed = raw_record.get("seed")
        if type(seed) is not int:
            raise ValueError("paired selected sequence identity seed is invalid")
        record = dict(raw_record)
        for field in (
            "screen_file_sha256", "transitions_file_sha256", "init_checkpoint_file_sha256",
            "init_checkpoint_tensor_state_sha256", "selected_sequence_sha256", "dagger_sequence_sha256",
        ):
            record[field] = _sha(record.get(field), field=f"paired_selected.{field}")
        for field in ("screen_path", "transitions_path", "init_checkpoint_path"):
            if type(record.get(field)) is not str or not record[field]:
                raise ValueError(f"paired selected {field} is invalid")
        normalized.append(record)
    if not normalized or len({int(record["seed"]) for record in normalized}) != len(normalized):
        raise ValueError("paired selected sequence identity requires distinct seed records")
    payload = {
        "schema": "meta-specialist-v4-dagger-paired-selected-sequence-v1",
        "paired_manifest": manifest,
        "seed_records": sorted(normalized, key=lambda record: int(record["seed"])),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _validate_screen_subject_identity(
    screen: Mapping[str, object], *, lane: str,
) -> dict[str, str]:
    """Validate the subject deck/archetype sealed by the DAgger screen."""
    if screen.get("schema") != SCREEN_SCHEMA_V4:
        raise ValueError("DAgger screen schema is invalid")
    archetype = screen.get("subject_archetype_id")
    if type(archetype) is not str or not archetype:
        raise ValueError("DAgger screen subject archetype is missing")
    if archetype != lane:
        raise ValueError("DAgger screen subject archetype differs from the requested lane")
    raw_path = screen.get("subject_deck_csv_path")
    if type(raw_path) is not str or not raw_path:
        raise ValueError("DAgger screen subject deck path is missing")
    path = Path(raw_path)
    if path.is_symlink():
        raise ValueError("DAgger screen subject deck must be a regular non-symlink file")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("DAgger screen subject deck is missing") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError("DAgger screen subject deck must be a regular non-symlink file")
    expected = _sha(screen.get("subject_deck_file_sha256"), field="subject_deck_file_sha256")
    actual = _file_sha(resolved)
    if actual != expected:
        raise ValueError("DAgger screen subject deck SHA does not match the sealed identity")
    return {
        "archetype_id": archetype,
        "deck_csv_path": str(resolved),
        "deck_file_sha256": expected,
    }


def _read_transition_rows(
    path: Path, *, expected_sha: str, expected_screen: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    expected = _sha(expected_sha, field="transitions_file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("DAgger transition sidecar bytes do not match the external SHA anchor")
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"DAgger transition sidecar line {line_number} is invalid JSON") from exc
        if type(value) is not dict or set(value) != {
            "schema", "game_id", "episode_group", "component_id", "partition",
            "opponent_id", "seat", "env_seed", "transition_index", "transition",
        }:
            raise ValueError(f"DAgger transition sidecar line {line_number} has an open schema")
        if value["schema"] != TRANSITION_SCHEMA_V4:
            raise ValueError("DAgger transition sidecar schema is invalid")
        for field in ("game_id", "episode_group", "component_id"):
            _sha(value[field], field=f"transition.{field}")
        if value["partition"] not in {"train", "validation"}:
            raise ValueError("DAgger transition partition is invalid")
        if type(value["seat"]) is not int or value["seat"] not in {0, 1}:
            raise ValueError("DAgger transition seat is invalid")
        if type(value["env_seed"]) is not int or value["env_seed"] < 0:
            raise ValueError("DAgger transition env_seed is invalid")
        if type(value["transition_index"]) is not int or value["transition_index"] < 0:
            raise ValueError("DAgger transition index is invalid")
        transition = parse_transition_payload_v4(value["transition"])
        # Re-serializing the typed object must retain the captured canonical
        # content hash.  The row metadata remains the game-level authority.
        if transition.to_dict() != value["transition"]:
            raise ValueError("DAgger transition payload is not canonical")
        rows.append(value)
    expected_count = expected_screen.get("transition_records")
    if type(expected_count) is not int or expected_count != len(rows) or not rows:
        raise ValueError("DAgger transition count disagrees with the sealed screen")
    return tuple(rows)


def _select_dagger_transition_rows_v4(
    rows: Sequence[Mapping[str, object]], *,
    screen: Mapping[str, object],
    strict_focus_targets: bool,
    focus_opponents: Sequence[str],
    focus_seats: Sequence[int],
) -> tuple[tuple[Mapping[str, object], ...], tuple[dict[str, object], ...] | None]:
    """Select whole screen games for an opt-in opponent/seat target set.

    The default path deliberately returns every row without imposing a new
    screen contract.  Strict mode instead binds every sidecar group back to
    the sealed screen game inventory, proves one-to-one game/episode/component
    ownership, and only then filters at the complete-game boundary.
    """
    all_rows = tuple(rows)
    if type(strict_focus_targets) is not bool:
        raise ValueError("strict_focus_targets must be a boolean")
    if not strict_focus_targets:
        return all_rows, None

    opponents = tuple(focus_opponents)
    seats = tuple(focus_seats)
    if (
        not opponents
        or any(type(value) is not str or not value for value in opponents)
        or len(set(opponents)) != len(opponents)
    ):
        raise ValueError("strict focus targets require distinct non-empty opponents")
    if (
        not seats
        or any(type(value) is not int or value not in {0, 1} for value in seats)
        or len(set(seats)) != len(seats)
    ):
        raise ValueError("strict focus targets require distinct seats 0 or 1")
    if (
        screen.get("status") != "VALID"
        or type(screen.get("faults")) is not int
        or screen.get("faults") != 0
    ):
        raise ValueError("strict focus targets require a fault-free VALID screen")
    games = screen.get("games")
    if type(games) is not list or not games:
        raise ValueError("strict focus targets require the sealed screen game inventory")
    if (
        type(screen.get("games_requested")) is not int
        or screen.get("games_requested") != len(games)
        or type(screen.get("games_completed")) is not int
        or screen.get("games_completed") != len(games)
    ):
        raise ValueError("strict focus screen game counts are inconsistent")
    if type(screen.get("transition_records")) is not int or screen.get("transition_records") != len(all_rows):
        raise ValueError("strict focus screen transition count is inconsistent")

    expected_by_game: dict[str, dict[str, object]] = {}
    target_game_ids: set[str] = set()
    available_pairs: set[tuple[str, int]] = set()
    available_metadata: list[dict[str, object]] = []
    for game in games:
        if type(game) is not dict:
            raise ValueError("strict focus screen game must be an object")
        job_id = game.get("job_id")
        opponent_id = game.get("opponent_id")
        seat = game.get("seat")
        env_seed = game.get("env_seed")
        transition_records = game.get("transitions")
        if type(job_id) is not str or not job_id or type(opponent_id) is not str or not opponent_id:
            raise ValueError("strict focus screen game identity is invalid")
        if type(seat) is not int or seat not in {0, 1} or type(env_seed) is not int or env_seed < 0:
            raise ValueError("strict focus screen game seat/seed is invalid")
        if type(transition_records) is not int or transition_records < 0:
            raise ValueError("strict focus screen game transition count is invalid")
        if game.get("status") != "completed" or game.get("fault") is not None:
            raise ValueError("strict focus screen contains an incomplete or faulted game")
        game_id = hashlib.sha256(
            f"meta-specialist-v4-dagger-game:{job_id}".encode("utf-8")
        ).hexdigest()
        if game_id in expected_by_game:
            raise ValueError("strict focus screen contains duplicate game/component identity")
        partition = "validation" if int(game_id[:2], 16) < 64 else "train"
        metadata = {
            "game_id": game_id,
            "episode_group": game_id,
            "component_id": game_id,
            "partition": partition,
            "opponent_id": opponent_id,
            "seat": seat,
            "env_seed": env_seed,
            "transition_records": transition_records,
        }
        expected_by_game[game_id] = metadata
        if opponent_id in opponents and seat in seats and transition_records > 0:
            target_game_ids.add(game_id)
            available_pairs.add((opponent_id, seat))
            available_metadata.append(metadata)

    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    component_owner: dict[str, str] = {}
    episode_owner: dict[str, str] = {}
    for row in all_rows:
        if not isinstance(row, Mapping):
            raise ValueError("strict focus transition row must be an object")
        game_id = _sha(row.get("game_id"), field="strict_focus.game_id")
        episode_group = _sha(row.get("episode_group"), field="strict_focus.episode_group")
        component_id = _sha(row.get("component_id"), field="strict_focus.component_id")
        if game_id not in expected_by_game:
            raise ValueError("strict focus transition references a game absent from the screen")
        previous_component_owner = component_owner.setdefault(component_id, game_id)
        if previous_component_owner != game_id:
            raise ValueError("strict focus component overlap spans multiple games")
        previous_episode_owner = episode_owner.setdefault(episode_group, game_id)
        if previous_episode_owner != game_id:
            raise ValueError("strict focus episode overlap spans multiple games")
        grouped[game_id].append(row)

    bound_fields = (
        "game_id", "episode_group", "component_id", "partition",
        "opponent_id", "seat", "env_seed",
    )
    for game_id, expected in expected_by_game.items():
        game_rows = grouped.get(game_id, [])
        if len(game_rows) != expected["transition_records"]:
            raise ValueError(f"strict focus episode transition count differs from screen for game {game_id}")
        indices = [row.get("transition_index") for row in game_rows]
        if any(type(index) is not int or index < 0 for index in indices):
            raise ValueError(f"strict focus transition indices are invalid for game {game_id}")
        indices.sort()
        if indices != list(range(len(game_rows))):
            raise ValueError(f"strict focus transition indices are not contiguous for game {game_id}")
        for row in game_rows:
            if any(row.get(field) != expected[field] for field in bound_fields):
                raise ValueError(f"strict focus episode metadata differs from screen for game {game_id}")

    required_pairs = {(opponent, seat) for opponent in opponents for seat in seats}
    missing_pairs = sorted(required_pairs - available_pairs)
    if missing_pairs:
        rendered = ", ".join(f"{opponent}/seat{seat}" for opponent, seat in missing_pairs)
        raise ValueError(f"strict focus target availability is missing: {rendered}")
    if {str(row["partition"]) for row in available_metadata} != {"train", "validation"}:
        raise ValueError("strict focus target availability must include train and validation episodes")

    selected = tuple(row for row in all_rows if row["game_id"] in target_game_ids)
    if len(selected) != sum(int(row["transition_records"]) for row in available_metadata):
        raise ValueError("strict focus target selection is not episode-complete")
    return selected, tuple(available_metadata)


def _strict_target_counts_v4(
    metadata: Sequence[Mapping[str, object]], *,
    focus_opponents: Sequence[str],
    focus_seats: Sequence[int],
) -> dict[str, object]:
    rows = tuple(metadata)
    by_partition = {"train": 0, "validation": 0}
    by_opponent = {opponent: 0 for opponent in focus_opponents}
    by_seat = {str(seat): 0 for seat in focus_seats}
    by_opponent_seat = {
        opponent: {str(seat): 0 for seat in focus_seats}
        for opponent in focus_opponents
    }
    for row in rows:
        partition = row["partition"]
        opponent = row["opponent_id"]
        seat = row["seat"]
        by_partition[str(partition)] += 1
        by_opponent[str(opponent)] += 1
        by_seat[str(seat)] += 1
        by_opponent_seat[str(opponent)][str(seat)] += 1
    return {
        "sequences": len(rows),
        "episodes": len({row["episode_group"] for row in rows}),
        "components": len({row["component_id"] for row in rows}),
        "transitions": sum(int(row["transition_records"]) for row in rows),
        "by_partition": by_partition,
        "by_opponent": by_opponent,
        "by_seat": by_seat,
        "by_opponent_seat": by_opponent_seat,
    }


def _strict_target_sequence_report_v4(
    *,
    focus_opponents: Sequence[str],
    focus_seats: Sequence[int],
    available_metadata: Sequence[Mapping[str, object]],
    base: Sequence[object],
    dagger: Sequence[object],
    mixed: Sequence[object],
) -> dict[str, object]:
    """Bind strict target counts to the sequences actually selected for BC."""
    opponents = tuple(focus_opponents)
    seats = tuple(focus_seats)
    metadata_rows = tuple(dict(row) for row in available_metadata)
    metadata_by_component: dict[str, dict[str, object]] = {}
    episode_groups: set[str] = set()
    for row in metadata_rows:
        component_id = _sha(row.get("component_id"), field="strict_target.component_id")
        episode_group = _sha(row.get("episode_group"), field="strict_target.episode_group")
        game_id = _sha(row.get("game_id"), field="strict_target.game_id")
        if component_id in metadata_by_component:
            raise ValueError("strict target metadata contains component overlap")
        if episode_group in episode_groups:
            raise ValueError("strict target metadata contains episode overlap")
        if row.get("opponent_id") not in opponents or row.get("seat") not in seats:
            raise ValueError("strict target metadata contains a non-target sequence")
        if row.get("partition") not in {"train", "validation"} or type(row.get("transition_records")) is not int:
            raise ValueError("strict target metadata is invalid")
        if type(row.get("transition_records")) is not int or int(row["transition_records"]) < 1:
            raise ValueError("strict target metadata transition count is invalid")
        metadata_by_component[component_id] = row
        episode_groups.add(episode_group)
        if game_id != episode_group or game_id != component_id:
            raise ValueError("strict target metadata game/episode/component identity is invalid")

    base_ids = tuple(getattr(row, "component_id", None) for row in base)
    dagger_ids = tuple(getattr(row, "component_id", None) for row in dagger)
    mixed_ids = tuple(getattr(row, "component_id", None) for row in mixed)
    if any(type(value) is not str for value in (*base_ids, *dagger_ids, *mixed_ids)):
        raise ValueError("strict target sequence component identity is missing")
    if len(set(base_ids)) != len(base_ids) or len(set(dagger_ids)) != len(dagger_ids):
        raise ValueError("strict target sequence component overlap is present")
    if set(base_ids) & set(dagger_ids):
        raise ValueError("strict target base and overlay component overlap")
    target_ids = set(metadata_by_component)
    if set(dagger_ids) != target_ids:
        raise ValueError("strict target DAgger overlay contains a non-target or missing target component")
    if len(set(mixed_ids)) != len(mixed_ids) or not set(mixed_ids) <= set(base_ids) | target_ids:
        raise ValueError("strict target mixed selection contains an unknown or duplicate component")
    for sequence in dagger:
        metadata = metadata_by_component[sequence.component_id]
        if (
            getattr(sequence, "episode_group", None) != metadata["episode_group"]
            or getattr(sequence, "partition", None) != metadata["partition"]
        ):
            raise ValueError("strict target episode integrity differs after relabeling")
    selected_metadata = tuple(
        metadata_by_component[component_id]
        for component_id in mixed_ids
        if component_id in target_ids
    )
    if not selected_metadata:
        raise ValueError("strict focus selected no target DAgger sequence")
    return {
        "enabled": True,
        "requested_opponents": list(opponents),
        "requested_seats": list(seats),
        "available_counts": _strict_target_counts_v4(
            metadata_rows, focus_opponents=opponents, focus_seats=seats,
        ),
        "selected_counts": _strict_target_counts_v4(
            selected_metadata, focus_opponents=opponents, focus_seats=seats,
        ),
        "available_sequence_metadata": [dict(row) for row in metadata_rows],
        "selected_sequence_metadata": [dict(row) for row in selected_metadata],
    }


def build_dagger_sequences_v4(
    rows: Sequence[Mapping[str, object]], *, lane: str,
) -> tuple[Any, ...]:
    """Relabel and merge all transitions from each complete actor game."""
    teacher_factory, teacher_identity = build_rule_agent_policy_factory_v1()
    grouped: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    metadata: dict[str, Mapping[str, object]] = {}
    for row in rows:
        game_id = row["game_id"]
        assert isinstance(game_id, str)
        if game_id in metadata:
            previous = metadata[game_id]
            for field in ("episode_group", "component_id", "partition"):
                if row[field] != previous[field]:
                    raise ValueError("DAgger game metadata changes within an episode")
        else:
            metadata[game_id] = row
        transition = parse_transition_payload_v4(row["transition"])
        sequence = relabel_transition_v4(
            transition, teacher_factory=teacher_factory, policy_version=teacher_identity,
            lane=lane, episode_group=row["episode_group"], component_id=row["component_id"],
            partition=row["partition"],
        )
        grouped[game_id].append((row["transition_index"], sequence))
    merged: list[Any] = []
    for game_id in sorted(grouped):
        ordered = tuple(sequence for _index, sequence in sorted(grouped[game_id], key=lambda item: item[0]))
        if [index for index, _sequence in sorted(grouped[game_id], key=lambda item: item[0])] != list(range(len(ordered))):
            raise ValueError(f"DAgger transition indices are not contiguous for game {game_id}")
        merged.append(merge_dagger_episode_sequences_v4(ordered))
    if not merged or {item.partition for item in merged} != {"train", "validation"}:
        raise ValueError("DAgger screen must provide both train and validation games")
    return tuple(merged)


def build_dagger_sequences_with_strict_disagreement_v4(
    rows: Sequence[Mapping[str, object]], *, lane: str,
    focus_opponents: Sequence[str] = (), focus_seats: Sequence[int] = (),
    focus_action_types: Sequence[int] = (),
    max_mean_behavior_log_probability: float | None = None,
) -> tuple[tuple[Any, ...], dict[str, object]]:
    """Build complete-game DAgger episodes from strict public disagreements.

    A transition is eligible only when the recorded student prefix action and
    the sealed teacher target differ on the same public prefix chain.  Once a
    transition in a game is eligible, the complete game is retained; no
    counterfactual state is generated after the first mismatch.  This keeps
    recurrent boundaries and provenance intact while making the selection
    criterion auditable.
    """
    opponents = tuple(focus_opponents)
    seats = tuple(focus_seats)
    if any(type(value) is not str or not value for value in opponents):
        raise ValueError("strict disagreement focus opponents must be non-empty strings")
    if any(type(value) is not int or value not in {0, 1} for value in seats):
        raise ValueError("strict disagreement focus seats must be 0 or 1")
    if len(set(opponents)) != len(opponents) or len(set(seats)) != len(seats):
        raise ValueError("strict disagreement focus metadata contains duplicates")

    teacher_factory, teacher_identity = build_rule_agent_policy_factory_v1()
    grouped: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    game_metadata: dict[str, Mapping[str, object]] = {}
    game_rows: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    game_metadata_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("strict disagreement row must be an object")
        game_id = row.get("game_id")
        if type(game_id) is not str or not game_id:
            raise ValueError("strict disagreement game_id is invalid")
        if game_id in game_metadata:
            previous = game_metadata[game_id]
            for field in ("episode_group", "component_id", "partition", "opponent_id", "seat"):
                if row.get(field) != previous.get(field):
                    raise ValueError("strict disagreement game metadata changes within an episode")
        else:
            game_metadata[game_id] = row
        transition = parse_transition_payload_v4(row.get("transition"))
        sequence = relabel_transition_v4(
            transition, teacher_factory=teacher_factory, policy_version=teacher_identity,
            lane=lane, episode_group=row["episode_group"], component_id=row["component_id"],
            partition=row["partition"],
        )
        metadata = strict_disagreement_metadata_v4(
            transition, sequence, focus_action_types=focus_action_types,
            max_mean_behavior_log_probability=max_mean_behavior_log_probability,
        )
        supervised_prefixes = frozenset(
            int(index) for index in metadata["disagreement_prefix_indices"]
        ) if metadata["eligible"] else frozenset()
        sequence = type(sequence)(
            lane=sequence.lane,
            episode_group=sequence.episode_group,
            component_id=sequence.component_id,
            partition=sequence.partition,
            steps=tuple(
                replace(
                    step,
                    supervision_weight=1.0 if index in supervised_prefixes else 0.0,
                )
                for index, step in enumerate(sequence.steps)
            ),
            burn_in=sequence.burn_in,
            research_only=sequence.research_only,
        )
        row_metadata = {
            "game_id": game_id,
            "episode_group": row["episode_group"],
            "component_id": row["component_id"],
            "partition": row["partition"],
            "opponent_id": row["opponent_id"],
            "seat": row["seat"],
            "transition_index": row["transition_index"],
            **metadata,
        }
        grouped[game_id].append((row["transition_index"], sequence))
        game_rows[game_id].append(row)
        game_metadata_rows[game_id].append(row_metadata)

    merged: list[Any] = []
    selected_components: list[str] = []
    selected_metadata: list[dict[str, object]] = []
    disagreement_transition_count = 0
    eligible_transition_count = 0
    effective_loss_mass = 0.0
    non_forced_effective_loss_mass = 0.0
    supervised_prefix_count = 0
    by_partition: dict[str, int] = {"train": 0, "validation": 0}
    by_opponent: dict[str, int] = defaultdict(int)
    by_seat: dict[str, int] = defaultdict(int)
    for game_id in sorted(grouped):
        ordered_pairs = sorted(grouped[game_id], key=lambda item: item[0])
        indices = [index for index, _sequence in ordered_pairs]
        if indices != list(range(len(indices))):
            raise ValueError(f"strict disagreement transition indices are not contiguous for game {game_id}")
        metadata_rows = game_metadata_rows[game_id]
        if any(row["disagreement"] for row in metadata_rows):
            disagreement_transition_count += sum(bool(row["disagreement"]) for row in metadata_rows)
        metadata = game_metadata[game_id]
        metadata_matches = (
            (not opponents or metadata.get("opponent_id") in opponents)
            and (not seats or metadata.get("seat") in seats)
        )
        eligible_rows = [row for row in metadata_rows if row["eligible"] and metadata_matches]
        if not eligible_rows:
            continue
        selected = merge_dagger_episode_sequences_v4(tuple(sequence for _index, sequence in ordered_pairs))
        merged.append(selected)
        component_id = selected.component_id
        selected_components.append(component_id)
        selected_metadata.extend(metadata_rows)
        eligible_transition_count += len(eligible_rows)
        effective_loss_mass += math.fsum(float(row["effective_loss_mass"]) for row in eligible_rows)
        non_forced_effective_loss_mass += math.fsum(
            float(row["non_forced_effective_loss_mass"]) for row in eligible_rows
        )
        supervised_prefix_count += sum(
            int(step.supervision_weight > 0.0)
            for _index, sequence in ordered_pairs
            for step in sequence.steps
        )
        partition = str(selected.partition)
        by_partition[partition] = by_partition.get(partition, 0) + 1
        by_opponent[str(metadata["opponent_id"])] += 1
        by_seat[str(metadata["seat"])] += 1

    all_metadata = tuple(
        row
        for game_id in sorted(game_metadata_rows)
        for row in game_metadata_rows[game_id]
    )
    selected_game_ids = {str(row["game_id"]) for row in selected_metadata}
    selected_all_metadata = tuple(
        row
        for row in selected_metadata
        if str(row["game_id"]) in selected_game_ids
    )

    report = {
        "schema": "meta-specialist-v4-strict-disagreement-report-v1",
        "teacher_policy_version": teacher_identity,
        "selection_semantics": "same_recorded_public_prefix_chain_complete_game",
        "focus_opponents": list(opponents),
        "focus_seats": list(seats),
        "focus_action_types": list(focus_action_types),
        "max_mean_behavior_log_probability": max_mean_behavior_log_probability,
        "available_episode_count": len(grouped),
        "selected_episode_count": len(merged),
        "selected_components": selected_components,
        "available_transition_count": sum(len(rows_for_game) for rows_for_game in game_rows.values()),
        "disagreement_transition_count": disagreement_transition_count,
        "eligible_transition_count": eligible_transition_count,
        "effective_loss_mass": effective_loss_mass,
        "non_forced_effective_loss_mass": non_forced_effective_loss_mass,
        "supervised_prefix_count": supervised_prefix_count,
        "available_total_effective_loss_mass": math.fsum(
            float(row["total_effective_loss_mass"]) for row in all_metadata
        ),
        "available_total_non_forced_effective_loss_mass": math.fsum(
            float(row["total_non_forced_effective_loss_mass"]) for row in all_metadata
        ),
        "selected_total_effective_loss_mass": math.fsum(
            float(row["total_effective_loss_mass"]) for row in selected_all_metadata
        ),
        "selected_total_non_forced_effective_loss_mass": math.fsum(
            float(row["total_non_forced_effective_loss_mass"]) for row in selected_all_metadata
        ),
        "selected_by_partition": by_partition,
        "selected_by_opponent": dict(sorted(by_opponent.items())),
        "selected_by_seat": dict(sorted(by_seat.items())),
        "selected_transition_metadata": selected_metadata,
    }
    return tuple(merged), report


def _resolve_device(value: str) -> torch.device:
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError(f"requested CUDA device is unavailable: {value}")
    return device


def _seeds(value: str) -> tuple[int, ...]:
    try:
        result = tuple(int(item) for item in value.split(",") if item)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if len(result) != 2 or len(set(result)) != 2:
        raise argparse.ArgumentTypeError("exactly two distinct seeds are required")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--selection-manifest-sha256", required=True)
    parser.add_argument("--screen", type=Path)
    parser.add_argument("--screen-sha256")
    parser.add_argument("--transitions", type=Path)
    parser.add_argument("--transitions-sha256")
    parser.add_argument("--lane", choices=("alakazam", "archaludon"), required=True)
    parser.add_argument("--init-checkpoint", type=Path)
    parser.add_argument("--init-checkpoint-file-sha256")
    parser.add_argument("--init-checkpoint-tensor-state-sha256")
    parser.add_argument(
        "--paired-seed-manifest", type=Path,
        help=(
            "hash-anchored JSON mapping each requested seed to its sealed "
            "screen, transitions, and matching warm-start checkpoint"
        ),
    )
    parser.add_argument("--paired-seed-manifest-sha256")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument("--seeds", type=_seeds, default=(0, 1))
    parser.add_argument("--max-records", type=int, default=65536)
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--episodes-per-partition", type=int, default=512)
    parser.add_argument("--components-per-partition", type=int, default=512)
    parser.add_argument("--train-episodes-per-partition", type=int, default=512)
    parser.add_argument("--validation-episodes-per-partition", type=int, default=128)
    parser.add_argument("--train-components-per-partition", type=int, default=512)
    parser.add_argument("--validation-components-per-partition", type=int, default=128)
    parser.add_argument("--require-positive-stop", action="store_true")
    parser.add_argument("--dagger-fraction", type=float, default=0.2)
    parser.add_argument(
        "--focus-opponents", type=_parse_focus_names,
        default=DEFAULT_FOCUS_OPPONENTS_V4,
        help="comma-separated opponent IDs to prioritize in the DAgger overlay",
    )
    parser.add_argument(
        "--focus-seats", type=lambda value: _parse_focus_ints(value, field="focus_seats", minimum=0, maximum=1),
        default=DEFAULT_FOCUS_SEATS_V4,
        help="comma-separated seat IDs to prioritize in the DAgger overlay",
    )
    parser.add_argument(
        "--strict-focus-targets", action="store_true",
        help=(
            "filter the DAgger overlay to complete games matching both "
            "--focus-opponents and --focus-seats, with fail-closed screen integrity checks"
        ),
    )
    parser.add_argument(
        "--strict-disagreement-targets", action="store_true",
        help=(
            "select complete games only when the recorded student prefix and "
            "teacher target disagree on the same public prefix chain"
        ),
    )
    parser.add_argument(
        "--strict-disagreement-action-types",
        type=lambda value: _parse_focus_ints(
            value, field="strict_disagreement_action_types", minimum=0, maximum=16,
        ),
        default=None,
        help="optional semantic target types used by strict disagreement eligibility; defaults to --focus-action-types",
    )
    parser.add_argument(
        "--strict-max-mean-behavior-log-probability", type=float, default=None,
        help="optional upper bound for mean recorded behavior log-probability in strict disagreement selection",
    )
    parser.add_argument(
        "--focus-action-types", type=lambda value: _parse_focus_ints(value, field="focus_action_types", minimum=0, maximum=16),
        default=DEFAULT_FOCUS_ACTION_TYPES_V4,
        help="comma-separated semantic option types to prioritize (9=EVOLVE, 13=ATTACK, 14=END)",
    )
    parser.add_argument(
        "--action-type-weights", type=_parse_action_type_weights, default=None,
        help="optional loss-weight arm: none, balanced_v1, or comma-separated type=value pairs",
    )
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--embedding-dim", type=int, default=64)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--patience", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--gradient-clip-norm", type=float, default=1.0)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    args = parser.parse_args()

    try:
        device = _resolve_device(args.device)
        legacy_values = (
            args.screen, args.screen_sha256, args.transitions, args.transitions_sha256,
            args.init_checkpoint, args.init_checkpoint_file_sha256,
            args.init_checkpoint_tensor_state_sha256,
        )
        paired_requested = (
            args.paired_seed_manifest is not None
            or args.paired_seed_manifest_sha256 is not None
        )
        if paired_requested:
            if args.paired_seed_manifest is None or args.paired_seed_manifest_sha256 is None:
                raise ValueError("paired seed manifest path and SHA256 must be supplied together")
            if any(value is not None for value in legacy_values):
                raise ValueError("paired seed manifest cannot be mixed with single-screen inputs")
            seed_bindings, paired_manifest_identity = _resolve_paired_seed_provenance_v4(
                seeds=args.seeds, lane=args.lane, manifest_path=args.paired_seed_manifest,
                manifest_file_sha256=args.paired_seed_manifest_sha256,
            )
        else:
            if any(value is None for value in legacy_values):
                raise ValueError(
                    "single-screen mode requires screen, transitions, and init checkpoint paths with all SHA256 values"
                )
            assert args.screen is not None and args.transitions is not None and args.init_checkpoint is not None
            assert args.screen_sha256 is not None and args.transitions_sha256 is not None
            assert args.init_checkpoint_file_sha256 is not None
            assert args.init_checkpoint_tensor_state_sha256 is not None
            shared_binding = {
                "screen_path": str(args.screen.resolve()),
                "screen_file_sha256": _sha(args.screen_sha256, field="screen_sha256"),
                "transitions_path": str(args.transitions.resolve()),
                "transitions_file_sha256": _sha(args.transitions_sha256, field="transitions_sha256"),
                "init_checkpoint_path": str(args.init_checkpoint.resolve()),
                "init_checkpoint_file_sha256": _sha(
                    args.init_checkpoint_file_sha256, field="init_checkpoint_file_sha256",
                ),
                "init_checkpoint_tensor_state_sha256": _sha(
                    args.init_checkpoint_tensor_state_sha256,
                    field="init_checkpoint_tensor_state_sha256",
                ),
            }
            seed_bindings = tuple({"seed": seed, **shared_binding} for seed in args.seeds)
            paired_manifest_identity = None

        _write_progress(args.progress_path, {
            "status": "running", "stage": "screen_read", "device": str(device),
            "seeds_total": len(args.seeds), "paired_seed_manifest": paired_requested,
        })
        subject_identity: dict[str, str] | None = None
        seed_materials: dict[int, dict[str, object]] = {}
        for binding in seed_bindings:
            seed = int(binding["seed"])
            screen_path = Path(str(binding["screen_path"]))
            transitions_path = Path(str(binding["transitions_path"]))
            screen = _read_hashed_json(
                screen_path, str(binding["screen_file_sha256"]), field=f"screen.seed{seed}",
            )
            current_subject_identity = _validate_screen_subject_identity(screen, lane=args.lane)
            if subject_identity is None:
                subject_identity = current_subject_identity
            elif subject_identity != current_subject_identity:
                raise ValueError("paired seed screens have different subject deck identities")
            if screen.get("status") != "VALID" or screen.get("faults") != 0:
                raise ValueError("DAgger screen is not a fault-free VALID artifact")
            _validate_dagger_seed_checkpoint_binding_v4(screen, binding=binding)
            transitions_path_from_screen = screen.get("transitions_path")
            if type(transitions_path_from_screen) is not str:
                raise ValueError("DAgger screen has no transitions path")
            if Path(transitions_path_from_screen).resolve() != transitions_path.resolve():
                raise ValueError("DAgger transitions path differs from the sealed screen")
            screen_rows = _read_transition_rows(
                transitions_path,
                expected_sha=str(binding["transitions_file_sha256"]), expected_screen=screen,
            )
            rows, strict_target_metadata = _select_dagger_transition_rows_v4(
                screen_rows,
                screen=screen,
                strict_focus_targets=args.strict_focus_targets,
                focus_opponents=args.focus_opponents,
                focus_seats=args.focus_seats,
            )
            if screen.get("lane") is not None and screen.get("lane") != args.lane:
                raise ValueError("DAgger screen lane differs from the requested lane")
            focus_component_ids = prioritized_dagger_component_ids_v4(
                rows,
                focus_opponents=args.focus_opponents,
                focus_seats=args.focus_seats,
                focus_action_types=args.focus_action_types,
            )
            strict_disagreement_report = None
            if args.strict_disagreement_targets:
                strict_action_types = (
                    args.strict_disagreement_action_types
                    if args.strict_disagreement_action_types is not None
                    else args.focus_action_types
                )
                strict_opponents = args.focus_opponents if args.strict_focus_targets else ()
                strict_seats = args.focus_seats if args.strict_focus_targets else ()
                dagger, strict_disagreement_report = build_dagger_sequences_with_strict_disagreement_v4(
                    rows,
                    lane=args.lane,
                    focus_opponents=strict_opponents,
                    focus_seats=strict_seats,
                    focus_action_types=strict_action_types,
                    max_mean_behavior_log_probability=args.strict_max_mean_behavior_log_probability,
                )
                focus_component_ids = tuple(strict_disagreement_report["selected_components"])
                if not dagger:
                    raise ValueError("strict disagreement criteria matched no complete-game component")
                if {item.partition for item in dagger} != {"train", "validation"}:
                    raise ValueError("strict disagreement overlay must include train and validation games")
            else:
                dagger = build_dagger_sequences_v4(rows, lane=args.lane)
            if not focus_component_ids and (args.focus_opponents or args.focus_seats or args.focus_action_types):
                raise ValueError("DAgger focus criteria matched no complete-game component")
            _write_progress(args.progress_path, {
                "status": "running", "stage": "dagger_relabel", "device": str(device),
                "seed": seed, "screen_games": screen.get("games_completed"),
                "transition_records": len(rows),
                "strict_disagreement_targets": args.strict_disagreement_targets,
            })
            seed_materials[seed] = {
                "binding": dict(binding), "screen": screen, "rows": rows,
                "strict_target_metadata": strict_target_metadata,
                "focus_component_ids": focus_component_ids,
                "dagger": dagger,
                "strict_disagreement_report": strict_disagreement_report,
            }

        subset = materialize_fast_research_uniform_subset_v4(
            args.selection_manifest,
            expected_selection_manifest_file_sha256=args.selection_manifest_sha256,
            max_records=args.max_records, subset_fraction=args.subset_fraction,
            burn_in=args.burn_in, episodes_per_partition=args.episodes_per_partition,
            components_per_partition=args.components_per_partition,
            train_episodes_per_partition=args.train_episodes_per_partition,
            validation_episodes_per_partition=args.validation_episodes_per_partition,
            train_components_per_partition=args.train_components_per_partition,
            validation_components_per_partition=args.validation_components_per_partition,
            require_positive_stop=args.require_positive_stop,
            mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        )
        if subset.lane != args.lane:
            raise ValueError("DAgger lane does not match the sealed selection lane")
        base = tuple(subset.sequences)
        dagger_fraction = float(args.dagger_fraction)
        if not 0.0 <= dagger_fraction < 1.0:
            raise ValueError("dagger_fraction must be in [0, 1)")
        for seed in args.seeds:
            material = seed_materials[seed]
            dagger = tuple(material["dagger"])
            mixed = mix_dagger_sequences_v4(
                base, dagger, dagger_fraction=dagger_fraction,
                seed=seed if paired_requested else args.seeds[0],
                priority_component_ids=tuple(material["focus_component_ids"]),
            )
            mixture_summary = _summarize_dagger_mixture(base=base, dagger=dagger, mixed=mixed)
            strict_target_report = None
            if material["strict_target_metadata"] is not None and not args.strict_disagreement_targets:
                strict_target_report = _strict_target_sequence_report_v4(
                    focus_opponents=args.focus_opponents,
                    focus_seats=args.focus_seats,
                    available_metadata=tuple(material["strict_target_metadata"]),
                    base=base, dagger=dagger, mixed=mixed,
                )
            material.update({
                "mixed": mixed,
                "mixture_summary": mixture_summary,
                "strict_target_report": strict_target_report,
                "selected_sha": selected_objective_sha256_v4(mixed),
                "dagger_sha": hashlib.sha256(
                    "".join(dagger_record_sha256_v4(row) for row in dagger).encode("ascii")
                ).hexdigest(),
                "train": tuple(item for item in mixed if item.partition == "train"),
                "validation": tuple(item for item in mixed if item.partition == "validation"),
            })
        trainer_sha = trainer_implementation_sha256_v4()
        if subject_identity is None:
            raise ValueError("DAgger seed provenance materialized no subject identity")
        if paired_requested:
            assert paired_manifest_identity is not None
            paired_selected_sequence_identity = _paired_selected_sequence_identity_v4(
                tuple({
                    **dict(seed_materials[seed]["binding"]),
                    "selected_sequence_sha256": str(seed_materials[seed]["selected_sha"]),
                    "dagger_sequence_sha256": str(seed_materials[seed]["dagger_sha"]),
                } for seed in args.seeds),
                paired_manifest_identity=paired_manifest_identity,
            )
        else:
            paired_selected_sequence_identity = None
        # Verify every distinct warm-start before any output is created.  The
        # strict loader also checks the live V4 implementation closure.
        checked_checkpoints: set[tuple[str, str, str]] = set()
        for binding in seed_bindings:
            checkpoint_key = (
                str(binding["init_checkpoint_path"]),
                str(binding["init_checkpoint_file_sha256"]),
                str(binding["init_checkpoint_tensor_state_sha256"]),
            )
            if checkpoint_key in checked_checkpoints:
                continue
            probe = SpecialistModelV4(
                card_vocabulary_size=subset.card_vocabulary_size,
                hidden_dim=args.hidden_dim, embedding_dim=args.embedding_dim, seed=0,
            )
            load_specialist_checkpoint_v4(
                Path(checkpoint_key[0]), probe,
                expected_file_sha256=checkpoint_key[1], expected_tensor_state_sha256=checkpoint_key[2],
            )
            checked_checkpoints.add(checkpoint_key)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))

    _write_progress(args.progress_path, {
        "status": "running", "stage": "materialize_and_relabel",
        "base_sequences": len(base), "dagger_sequences": len(dagger),
        "mixed_sequences": len(mixed), "device": str(device),
    })
    checkpoint_root = args.output.parent / f"{args.output.stem}-checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    seed_results: dict[str, dict[str, object]] = {}
    started = time.monotonic()
    try:
        from tqdm import tqdm
        seed_iterator = tqdm(
            args.seeds, total=len(args.seeds), desc="v4-dagger-bc", unit="seed",
            dynamic_ncols=True, file=sys.stdout, disable=not sys.stdout.isatty(),
        )
    except ImportError:  # pragma: no cover
        seed_iterator = args.seeds
    for seed_index, seed in enumerate(seed_iterator, start=1):
        training_material = _training_material_v4(seed_materials[seed])
        init_checkpoint_path = training_material["init_checkpoint_path"]
        init_file_sha = str(training_material["init_checkpoint_file_sha256"])
        init_tensor_sha = str(training_material["init_checkpoint_tensor_state_sha256"])
        screen_path = training_material["screen_path"]
        transitions_path = training_material["transitions_path"]
        dagger = tuple(training_material["dagger"])
        mixed = tuple(training_material["mixed"])
        train = tuple(training_material["train"])
        validation = tuple(training_material["validation"])
        mixture_summary = dict(training_material["mixture_summary"])
        selected_sha = str(training_material["selected_sha"])
        dagger_sha = str(training_material["dagger_sha"])
        focus_component_ids = tuple(training_material["focus_component_ids"])
        strict_target_report = training_material["strict_target_report"]
        strict_disagreement_report = training_material["strict_disagreement_report"]
        _write_progress(args.progress_path, {
            "status": "running", "stage": "training", "seed": seed,
            "seed_index": seed_index, "seeds_total": len(args.seeds),
            "epochs_completed": 0, "epochs_requested": args.epochs,
            "optimizer_updates_completed": 0,
        })
        model = SpecialistModelV4(
            card_vocabulary_size=subset.card_vocabulary_size,
            hidden_dim=args.hidden_dim, embedding_dim=args.embedding_dim, seed=seed,
        ).to(device)
        load_specialist_checkpoint_v4(
            Path(str(init_checkpoint_path)), model,
            expected_file_sha256=init_file_sha, expected_tensor_state_sha256=init_tensor_sha,
        )
        run_config = {
            "lane": subset.lane,
            "subject_archetype_id": subject_identity["archetype_id"],
            "subject_deck_csv_path": subject_identity["deck_csv_path"],
            "subject_deck_file_sha256": subject_identity["deck_file_sha256"],
            "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
            "screen_file_sha256": str(training_material["screen_file_sha256"]),
            "transitions_file_sha256": str(training_material["transitions_file_sha256"]),
            "init_checkpoint_file_sha256": init_file_sha,
            "init_checkpoint_tensor_state_sha256": init_tensor_sha,
            "base_selected_sequence_sha256": selected_objective_sha256_v4(base),
            "dagger_sequence_sha256": dagger_sha,
            "selected_sequence_sha256": selected_sha,
            "dagger_fraction": dagger_fraction,
            "dagger_mixture": dict(mixture_summary),
            "focus_opponents": list(args.focus_opponents),
            "focus_seats": list(args.focus_seats),
            "focus_action_types": list(args.focus_action_types),
            "focus_component_ids": list(focus_component_ids),
            "action_type_weights": args.action_type_weights,
            "burn_in": args.burn_in,
            "max_records": args.max_records,
            "coverage_target": {
                "train_episodes_per_partition": args.train_episodes_per_partition,
                "validation_episodes_per_partition": args.validation_episodes_per_partition,
                "train_components_per_partition": args.train_components_per_partition,
                "validation_components_per_partition": args.validation_components_per_partition,
                "require_positive_stop": args.require_positive_stop,
            },
            "model": {"card_vocabulary_size": subset.card_vocabulary_size, "hidden_dim": args.hidden_dim, "embedding_dim": args.embedding_dim},
            "trainer": {"epochs": args.epochs, "patience": args.patience, "learning_rate": args.learning_rate, "tbptt_steps": args.tbptt_steps, "gradient_clip_norm": args.gradient_clip_norm},
            "trainer_implementation_sha256": trainer_sha,
        }
        if strict_target_report is not None:
            run_config["strict_target_selection"] = dict(strict_target_report)
        if isinstance(strict_disagreement_report, Mapping):
            run_config["strict_disagreement_selection"] = dict(strict_disagreement_report)

        def epoch_callback(payload: dict[str, object], *, current_seed: int = seed) -> None:
            _write_progress(args.progress_path, {
                "status": "running", "stage": "training", "seed": current_seed, **payload,
            })

        train_progress_callback = _make_training_progress_callback(
            args.progress_path, seed=seed, epochs=args.epochs, started=started,
        )

        result = train_recurrent_bc_v4(
            model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
            output_dir=checkpoint_root / f"seed-{seed}", sequence_order_seed=seed,
            epochs=args.epochs, patience=args.patience, learning_rate=args.learning_rate,
            tbptt_steps=args.tbptt_steps, gradient_clip_norm=args.gradient_clip_norm,
            action_type_weights=args.action_type_weights,
            run_config=run_config, resume=False, epoch_callback=epoch_callback,
            train_progress_callback=train_progress_callback,
        )
        stop_metrics = positive_stop_target_metrics_v4(model, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
        imitation_metrics = _validation_imitation_metrics_v4(model, validation)
        seed_results[str(seed)] = {
            "sequence_order_seed": seed,
            "best_epoch": result.best_epoch, "epochs_completed": result.epochs_completed,
            "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
            "best_validation_complete_action_nll": result.best_validation_complete_action_nll,
            "validation_delta_nll": result.validation_delta_nll, "improved": result.improved,
            "validation_by_component": dict(result.validation_by_component),
            "history": [dict(row) for row in result.history],
            "optimizer_updates_completed": result.optimizer_updates_completed,
            "elapsed_seconds": result.elapsed_seconds,
            "best_checkpoint_path": str(result.best_checkpoint_path),
            "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
            "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
            "screen_path": str(screen_path),
            "screen_file_sha256": str(training_material["screen_file_sha256"]),
            "transitions_path": str(transitions_path),
            "transitions_file_sha256": str(training_material["transitions_file_sha256"]),
            "init_checkpoint_path": str(init_checkpoint_path),
            "init_checkpoint_file_sha256": init_file_sha,
            "init_checkpoint_tensor_state_sha256": init_tensor_sha,
            "selected_sequence_sha256": selected_sha,
            "dagger_sequence_sha256": dagger_sha,
            "dagger_mixture": dict(mixture_summary),
            "strict_target_selection": (
                dict(strict_target_report) if isinstance(strict_target_report, Mapping) else None
            ),
            "strict_disagreement_selection": (
                dict(strict_disagreement_report)
                if isinstance(strict_disagreement_report, Mapping) else None
            ),
            "validation_positive_stop_target_metrics": dict(stop_metrics),
            "validation_imitation_metrics": imitation_metrics,
        }
        if hasattr(seed_iterator, "set_postfix"):
            seed_iterator.set_postfix(seed=seed, nll=f"{result.best_validation_complete_action_nll:.4f}", refresh=False)

    resolved_seed_materials = {
        seed: _training_material_v4(seed_materials[seed]) for seed in args.seeds
    }
    primary_material = resolved_seed_materials[args.seeds[0]]
    seed_provenance = {
        str(seed): {
            "screen_path": str(material["screen_path"]),
            "screen_file_sha256": str(material["screen_file_sha256"]),
            "transitions_path": str(material["transitions_path"]),
            "transitions_file_sha256": str(material["transitions_file_sha256"]),
            "init_checkpoint_path": str(material["init_checkpoint_path"]),
            "init_checkpoint_file_sha256": str(material["init_checkpoint_file_sha256"]),
            "init_checkpoint_tensor_state_sha256": str(material["init_checkpoint_tensor_state_sha256"]),
            "selected_sequence_sha256": str(material["selected_sha"]),
            "dagger_sequence_sha256": str(material["dagger_sha"]),
            "dagger_mixture": dict(material["mixture_summary"]),
            "strict_target_selection": (
                dict(material["strict_target_report"])
                if isinstance(material["strict_target_report"], Mapping) else None
            ),
            "strict_disagreement_selection": (
                dict(material["strict_disagreement_report"])
                if isinstance(material["strict_disagreement_report"], Mapping) else None
            ),
            "dagger_sequences_by_partition": {
                partition: sum(row.partition == partition for row in material["dagger"])
                for partition in ("train", "validation")
            },
            "mixed_sequences_by_partition": {
                partition: sum(row.partition == partition for row in material["mixed"])
                for partition in ("train", "validation")
            },
        }
        for seed, material in resolved_seed_materials.items()
    }
    report = {
        "schema": REPORT_SCHEMA_V4,
        "mode": RESEARCH_ONLY_UNIFORM_WEIGHT,
        "promotion_authority": False,
        "status": "RESEARCH_ONLY_COMPLETE",
        "device": str(device),
        "elapsed_seconds": time.monotonic() - started,
        "selection_manifest": str(subset.selection_manifest_path),
        "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
        "lane": subset.lane,
        "subject_archetype_id": subject_identity["archetype_id"],
        "subject_deck_csv_path": subject_identity["deck_csv_path"],
        "subject_deck_file_sha256": subject_identity["deck_file_sha256"],
        "base_records_by_partition": dict(subset.records_by_partition),
        "base_sequences_by_partition": {part: sum(row.partition == part for row in base) for part in ("train", "validation")},
        "dagger_sequences_by_partition": dict(seed_provenance[str(args.seeds[0])]["dagger_sequences_by_partition"]),
        "mixed_sequences_by_partition": dict(seed_provenance[str(args.seeds[0])]["mixed_sequences_by_partition"]),
        "dagger_fraction_requested": dagger_fraction,
        "dagger_fraction_actual": mixture_summary["dagger_fraction_actual"],
        "dagger_mixture": dict(mixture_summary),
        "focus": {
            "opponents": list(args.focus_opponents), "seats": list(args.focus_seats),
            "action_types": list(args.focus_action_types),
            "component_ids": list(primary_material["focus_component_ids"]),
        },
        "strict_disagreement_targets": args.strict_disagreement_targets,
        "action_type_weights": args.action_type_weights,
        "screen_file_sha256": str(primary_material["screen_file_sha256"]),
        "transitions_file_sha256": str(primary_material["transitions_file_sha256"]),
        "init_checkpoint_file_sha256": str(primary_material["init_checkpoint_file_sha256"]),
        "init_checkpoint_tensor_state_sha256": str(primary_material["init_checkpoint_tensor_state_sha256"]),
        "selected_sequence_sha256": str(primary_material["selected_sha"]),
        "trainer_implementation_sha256": trainer_sha,
        "seed_provenance": seed_provenance,
        "training_config": {
            "max_records": args.max_records, "subset_fraction": args.subset_fraction,
            "burn_in": args.burn_in, "dagger_fraction": dagger_fraction,
            "epochs": args.epochs, "patience": args.patience, "learning_rate": args.learning_rate,
            "tbptt_steps": args.tbptt_steps, "gradient_clip_norm": args.gradient_clip_norm,
            "action_type_weights": args.action_type_weights,
            "strict_disagreement_action_types": list(
                args.strict_disagreement_action_types
                if args.strict_disagreement_action_types is not None
                else args.focus_action_types
            ),
            "strict_max_mean_behavior_log_probability": args.strict_max_mean_behavior_log_probability,
            "hidden_dim": args.hidden_dim, "embedding_dim": args.embedding_dim,
            "seeds": list(args.seeds), "device": str(device),
        },
        "seed_results": seed_results,
    }
    if isinstance(primary_material["strict_target_report"], Mapping):
        report["focus"]["strict_targets"] = True
        report["strict_target_selection"] = dict(primary_material["strict_target_report"])
    if isinstance(primary_material["strict_disagreement_report"], Mapping):
        report["strict_disagreement_selection"] = dict(primary_material["strict_disagreement_report"])
    _atomic_json(args.output, report)
    _write_progress(args.progress_path, {
        "status": "complete", "stage": "complete", "output": str(args.output),
        "seeds_completed": len(seed_results), "elapsed_seconds": report["elapsed_seconds"],
    })
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
