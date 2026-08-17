#!/usr/bin/env python3
"""Run the bounded V4 GPU pilot, then verify every best checkpoint on held-out CABT.

The command deliberately has one fixed research contract: two lanes, two seeds,
positive STOP coverage in both partitions, and 24 fixed held-out games per
checkpoint.  It is resumable only from artifacts that still satisfy that
contract and whose checkpoint provenance is intact; an invalid artifact is a
fail-closed error rather than a reason to silently retrain or re-evaluate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
TRAINING_SCRIPT = ROOT / "scripts" / "run_meta_specialist_v4_bc.py"
EVALUATION_SCRIPT = ROOT / "scripts" / "measure_v4_checkpoint_strength.py"
CAMPAIGN_SCHEMA = "meta-specialist-v4-gpu-medium-campaign-v1"
TRAINING_SCHEMA = "meta-specialist-recurrent-bc-v4-research-report"
EVALUATION_SCHEMA = "meta-specialist-v4-heldout-checkpoint-strength-v1"
SEEDS = (0, 1)
MAX_RECORDS = 8192
EPISODES_PER_PARTITION = 32
COMPONENTS_PER_PARTITION = 32
EPOCHS = 3
PATIENCE = 1
HIDDEN_DIM = 128
EMBEDDING_DIM = 64
TBPTT_STEPS = 8
GAMES_PER_SEAT = 2
OPPONENT_COUNT = 6
BASE_SEED = 9_400_000
MAX_STEPS = 2_000

DEFAULT_LANES = (
    {
        "lane": "alakazam",
        "selection_manifest": ROOT / "runs/meta-specialist-two-lane-readiness/recurrent-selection/alakazam.json",
        "selection_manifest_sha256": "8093116b9071847cc17ed0f742bf6000697646386dbcc410d924e145d021bc7e",
        "subject_deck_csv": ROOT / "opponents/nihei_alakazam/deck.csv",
        "subject_archetype_id": "alakazam",
    },
    {
        "lane": "archaludon",
        "selection_manifest": ROOT / "runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json",
        "selection_manifest_sha256": "b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc",
        "subject_deck_csv": ROOT / "opponents/public_archaludon_cinderace_r7/deck.csv",
        "subject_archetype_id": "archaludon",
    },
)


class CampaignError(RuntimeError):
    """A sealed artifact cannot be safely reused or compared."""


def _json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise CampaignError(f"{label} does not exist or is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"{label} is not a readable JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise CampaignError(f"{label} must be a JSON object: {path}")
    return value


def _require_hex64(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise CampaignError(f"{label} must be a 64-character lowercase SHA-256")
    return value


def _checkpoint_from_training_result(result: Mapping[str, object], *, lane: str, seed: int) -> dict[str, str]:
    try:
        checkpoint = Path(str(result["best_checkpoint_path"])).resolve(strict=True)
    except (KeyError, OSError, RuntimeError) as exc:
        raise CampaignError(f"{lane} seed {seed} training result has no readable best checkpoint") from exc
    if not checkpoint.is_file():
        raise CampaignError(f"{lane} seed {seed} best checkpoint is not a regular file: {checkpoint}")
    expected_file_sha = _require_hex64(result.get("best_checkpoint_file_sha256"), "training checkpoint file SHA-256")
    actual_file_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if actual_file_sha != expected_file_sha:
        raise CampaignError(
            f"{lane} seed {seed} checkpoint file SHA-256 changed since training report: "
            f"expected {expected_file_sha}, got {actual_file_sha}",
        )
    return {
        "path": str(checkpoint),
        "file_sha256": expected_file_sha,
        "tensor_state_sha256": _require_hex64(
            result.get("best_checkpoint_tensor_state_sha256"), "training checkpoint tensor-state SHA-256",
        ),
    }


def _validate_training_report(path: Path, lane_config: Mapping[str, object]) -> dict[int, dict[str, str]]:
    """Validate the reusable portion of a V4 training report and checkpoint files."""
    report = _json_object(path, "training report")
    lane = str(lane_config["lane"])
    if report.get("schema") != TRAINING_SCHEMA or report.get("lane") != lane:
        raise CampaignError(f"training report is not the sealed V4 report for lane {lane}: {path}")
    if report.get("device") != "cuda:0":
        raise CampaignError(f"training report was not produced on cuda:0: {path}")
    if report.get("selection_manifest_file_sha256") != lane_config["selection_manifest_sha256"]:
        raise CampaignError(f"training report selection manifest SHA-256 differs for lane {lane}: {path}")
    coverage = report.get("coverage_target")
    if not isinstance(coverage, dict) or coverage != {
        "episodes_per_partition": EPISODES_PER_PARTITION,
        "components_per_partition": COMPONENTS_PER_PARTITION,
        "require_positive_stop": True,
    }:
        raise CampaignError(f"training report lacks the required medium positive-STOP coverage: {path}")
    decoder_coverage = report.get("decoder_coverage_by_partition")
    if not isinstance(decoder_coverage, dict):
        raise CampaignError(f"training report has no decoder coverage: {path}")
    for partition in ("train", "validation"):
        rows = decoder_coverage.get(partition)
        if not isinstance(rows, dict) or not isinstance(rows.get("positive_stop_target_rows"), int) or rows["positive_stop_target_rows"] <= 0:
            raise CampaignError(f"training report has no positive STOP target in {partition}: {path}")
    seed_results = report.get("seed_results")
    if not isinstance(seed_results, dict) or set(seed_results) != {str(seed) for seed in SEEDS}:
        raise CampaignError(f"training report must contain exactly seeds {SEEDS}: {path}")
    validated: dict[int, dict[str, str]] = {}
    for seed in SEEDS:
        item = seed_results[str(seed)]
        if not isinstance(item, dict):
            raise CampaignError(f"training result is not an object for lane {lane} seed {seed}: {path}")
        validated[seed] = _checkpoint_from_training_result(item, lane=lane, seed=seed)
    return validated


def _validate_evaluation_report(
    path: Path, checkpoint: Mapping[str, str], *, games_per_seat: int, opponent_count: int,
) -> None:
    """Require an exact fault-free 24-game evaluation for this sealed checkpoint."""
    report = _json_object(path, "held-out evaluation report")
    if report.get("schema_version") != EVALUATION_SCHEMA:
        raise CampaignError(f"held-out evaluation has unexpected schema: {path}")
    recorded = report.get("checkpoint")
    if not isinstance(recorded, dict):
        raise CampaignError(f"held-out evaluation has no checkpoint provenance: {path}")
    if recorded.get("file_sha256") != checkpoint["file_sha256"]:
        raise CampaignError(f"held-out evaluation file SHA-256 does not match its training report: {path}")
    if recorded.get("tensor_state_sha256") != checkpoint["tensor_state_sha256"]:
        raise CampaignError(f"held-out evaluation tensor-state SHA-256 does not match its training report: {path}")
    if Path(str(recorded.get("path", ""))).resolve() != Path(checkpoint["path"]).resolve():
        raise CampaignError(f"held-out evaluation checkpoint path does not match its training report: {path}")
    requested_games = opponent_count * 2 * games_per_seat
    if report.get("games_per_seat") != games_per_seat or report.get("requested_games") != requested_games:
        raise CampaignError(f"held-out evaluation is not the required {requested_games}-game protocol: {path}")
    opponent_ids = report.get("opponent_ids")
    if not isinstance(opponent_ids, list) or len(opponent_ids) != opponent_count:
        raise CampaignError(f"held-out evaluation does not use the fixed {opponent_count}-opponent pool: {path}")
    if report.get("faults") != 0 or report.get("comparison_status") != "valid":
        raise CampaignError(f"held-out evaluation contains a fault or invalid comparison status: {path}")


def _training_command(lane: Mapping[str, object], output: Path, python: str) -> list[str]:
    return [
        python, str(TRAINING_SCRIPT),
        "--selection-manifest", str(lane["selection_manifest"]),
        "--selection-manifest-sha256", str(lane["selection_manifest_sha256"]),
        "--fast-research-subset", "--require-positive-stop", "--device", "cuda:0",
        "--max-records", str(MAX_RECORDS),
        "--episodes-per-partition", str(EPISODES_PER_PARTITION),
        "--components-per-partition", str(COMPONENTS_PER_PARTITION),
        "--epochs", str(EPOCHS), "--patience", str(PATIENCE),
        "--seeds", ",".join(str(seed) for seed in SEEDS),
        "--tbptt-steps", str(TBPTT_STEPS), "--hidden-dim", str(HIDDEN_DIM),
        "--embedding-dim", str(EMBEDDING_DIM), "--output", str(output),
    ]


def _evaluation_command(lane: Mapping[str, object], checkpoint: Mapping[str, str], output: Path, python: str) -> list[str]:
    return [
        python, str(EVALUATION_SCRIPT), "--checkpoint", checkpoint["path"],
        "--subject-deck-csv", str(lane["subject_deck_csv"]),
        "--subject-archetype-id", str(lane["subject_archetype_id"]),
        "--opponent-count", str(OPPONENT_COUNT), "--games-per-seat", str(GAMES_PER_SEAT),
        "--base-seed", str(BASE_SEED), "--max-steps", str(MAX_STEPS), "--output", str(output),
    ]


def _config() -> dict[str, object]:
    return {
        "seeds": list(SEEDS), "max_records": MAX_RECORDS,
        "episodes_per_partition": EPISODES_PER_PARTITION,
        "components_per_partition": COMPONENTS_PER_PARTITION,
        "require_positive_stop": True, "epochs": EPOCHS, "patience": PATIENCE,
        "hidden_dim": HIDDEN_DIM, "embedding_dim": EMBEDDING_DIM,
        "tbptt_steps": TBPTT_STEPS, "device": "cuda:0",
        "opponent_count": OPPONENT_COUNT, "games_per_seat": GAMES_PER_SEAT,
        "base_seed": BASE_SEED, "max_steps": MAX_STEPS,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/meta-specialist-v4-gpu-campaign")
    parser.add_argument("--python", default=sys.executable, help="Python executable with CUDA-enabled PyTorch")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    lane_summary: dict[str, Any] = {}
    for lane in DEFAULT_LANES:
        lane_name = str(lane["lane"])
        training_output = output_root / f"{lane_name}-training.json"
        if training_output.exists():
            checkpoints = _validate_training_report(training_output, lane)
            print(f"[campaign] reusing verified GPU training: {training_output}", flush=True)
        else:
            print(f"[campaign] training {lane_name} on cuda:0", flush=True)
            subprocess.run(_training_command(lane, training_output, args.python), check=True)
            checkpoints = _validate_training_report(training_output, lane)
        evaluation_paths: dict[str, str] = {}
        for seed, checkpoint in checkpoints.items():
            evaluation_output = output_root / f"{lane_name}-seed-{seed}-heldout-24.json"
            if evaluation_output.exists():
                _validate_evaluation_report(
                    evaluation_output, checkpoint, games_per_seat=GAMES_PER_SEAT, opponent_count=OPPONENT_COUNT,
                )
                print(f"[campaign] reusing verified held-out result: {evaluation_output}", flush=True)
            else:
                print(f"[campaign] evaluating {lane_name} seed {seed}: 24 held-out games", flush=True)
                subprocess.run(_evaluation_command(lane, checkpoint, evaluation_output, args.python), check=True)
                _validate_evaluation_report(
                    evaluation_output, checkpoint, games_per_seat=GAMES_PER_SEAT, opponent_count=OPPONENT_COUNT,
                )
            evaluation_paths[str(seed)] = str(evaluation_output)
        lane_summary[lane_name] = {
            "training_report": str(training_output),
            "checkpoints": {str(seed): checkpoint for seed, checkpoint in checkpoints.items()},
            "heldout_evaluations": evaluation_paths,
        }
    summary = {"schema": CAMPAIGN_SCHEMA, "status": "complete", "config": _config(), "lanes": lane_summary}
    summary_path = output_root / "campaign-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
