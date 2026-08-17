#!/usr/bin/env python3
"""Replay V4 checkpoints on sealed actor-visible transitions.

This is a research diagnostic.  It recomputes confidence/OOD scores from the
public model/step payload and a hash-bound checkpoint.  Envelope identities
are used only to reset recurrent state at game boundaries and never appear in
the output or feature calculation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import time

import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, load_specialist_checkpoint_v4
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
    PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
    PublicBucketReferenceV1,
    PublicEligibilityPolicyV1,
    score_public_step_v1,
)
from scripts.run_meta_specialist_v4_public_confidence_ood_bc import (
    load_public_ood_reference_bundle_v1,
)
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1


REPLAY_SCHEMA_V1 = "meta-specialist-public-confidence-ood-replay-v1"
_HEX64 = frozenset("0123456789abcdef")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hex64(value: str) -> str:
    if len(value) != 64 or any(character not in _HEX64 for character in value):
        raise argparse.ArgumentTypeError("must be a lowercase SHA-256 digest")
    return value


def _load_reference(
    path: Path,
    *,
    expected_source_sha256: str,
    expected_artifact_sha256: str | None = None,
) -> tuple[PublicBucketReferenceV1, str]:
    if not path.is_file():
        raise ValueError("reference must be a regular file")
    artifact_sha256 = _sha256_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("reference JSON is invalid") from exc
    if type(payload) is not dict:
        raise ValueError("reference schema is invalid")
    if payload.get("schema_version") == "meta-specialist-public-bucket-reference-bundle-v1":
        try:
            reference = load_public_ood_reference_bundle_v1(
                path,
                expected_artifact_sha256=expected_artifact_sha256,
                expected_source_list_sha256=expected_source_sha256,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("reference bundle is invalid or hash-mismatched") from exc
        return reference, artifact_sha256
    if payload.get("schema_version") != "meta-specialist-public-bucket-reference-v1":
        raise ValueError("reference schema is invalid")
    if expected_artifact_sha256 is not None and artifact_sha256 != expected_artifact_sha256:
        raise ValueError("reference artifact SHA does not match the expected frozen artifact")
    if payload.get("bucket_schema_version") != PUBLIC_CONFIDENCE_OOD_SCHEMA_V1:
        raise ValueError("reference bucket schema is invalid")
    if payload.get("source_sha256") != expected_source_sha256:
        raise ValueError("reference source SHA does not match the expected frozen reference")
    privacy = payload.get("privacy")
    expected_privacy = {
        "uses_hidden_fields": False,
        "uses_opponent_id": False,
        "uses_policy_identity": False,
        "uses_seat": False,
    }
    if privacy != expected_privacy:
        raise ValueError("reference privacy contract is not fail-closed")
    reference = PublicBucketReferenceV1(
        source_sha256=expected_source_sha256,
        bucket_counts=payload.get("bucket_counts", {}),
        rare_count_threshold=payload.get("rare_count_threshold", 2),
    )
    if sum(reference.bucket_counts.values()) != payload.get("prefix_count"):
        raise ValueError("reference prefix count does not match bucket histogram")
    return reference, artifact_sha256


def replay_public_confidence_ood(
    transitions_path: str | os.PathLike[str],
    checkpoint_path: str | os.PathLike[str],
    reference_path: str | os.PathLike[str],
    *,
    partition: str,
    checkpoint_file_sha256: str,
    checkpoint_tensor_state_sha256: str,
    reference_source_sha256: str,
    reference_artifact_sha256: str | None = None,
    card_vocabulary_size: int,
    hidden_dim: int,
    embedding_dim: int,
    device: str = "cpu",
    torch_threads: int = 2,
    policy: PublicEligibilityPolicyV1 = PublicEligibilityPolicyV1(),
) -> dict[str, object]:
    transitions = Path(transitions_path)
    checkpoint = Path(checkpoint_path)
    reference_file = Path(reference_path)
    if partition not in {"train", "validation"}:
        raise ValueError("partition must be train or validation")
    if not transitions.is_file() or not checkpoint.is_file():
        raise ValueError("transitions and checkpoint must be regular files")
    if type(policy) is not PublicEligibilityPolicyV1:
        raise ValueError("policy must be an exact PublicEligibilityPolicyV1")
    if type(torch_threads) is not int or torch_threads < 1:
        raise ValueError("torch_threads must be a positive int")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError(f"requested CUDA device is unavailable: {device}")
    source_sha256 = _sha256_file(transitions)
    torch.set_num_threads(torch_threads)
    reference, reference_artifact_sha256 = _load_reference(
        reference_file,
        expected_source_sha256=reference_source_sha256,
        expected_artifact_sha256=reference_artifact_sha256,
    )
    model = SpecialistModelV4(
        card_vocabulary_size=card_vocabulary_size,
        hidden_dim=hidden_dim,
        embedding_dim=embedding_dim,
    ).to(device)
    descriptor = load_specialist_checkpoint_v4(
        checkpoint,
        model,
        expected_file_sha256=checkpoint_file_sha256,
        expected_tensor_state_sha256=checkpoint_tensor_state_sha256,
    )
    model.eval()
    counter: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    transition_count = 0
    skipped_transition_count = 0
    prefix_count = 0
    forced_prefix_count = 0
    eligible_prefix_count = 0
    eligible_transition_count = 0
    target_missing_count = 0
    normalized_surprisal_sum = 0.0
    normalized_surprisal_count = 0
    previous_game_id: object = None
    hidden_state: torch.Tensor | None = None
    started = time.monotonic()

    with transitions.open("r", encoding="utf-8") as handle, torch.inference_mode():
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid transition JSON at line {line_number}") from exc
            if type(row) is not dict:
                raise ValueError(f"transition row must be an object at line {line_number}")
            if row.get("partition") != partition:
                skipped_transition_count += 1
                continue
            if row.get("schema") != "meta-specialist-v4-dagger-transition-v1":
                raise ValueError(f"unexpected transition schema at line {line_number}")
            try:
                transition = parse_transition_payload_v4(row.get("transition"))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError(f"invalid canonical transition at line {line_number}") from exc
            states = tuple(
                representation_v4_from_step_input_v1(
                    transition.model_input,
                    prefix.step_input,
                    allow_unbound_selected=True,
                )
                for prefix in transition.prefix_steps
            )
            if not states:
                raise ValueError(f"transition prefix_steps is empty at line {line_number}")
            game_id = row.get("game_id")
            episode_start = previous_game_id != game_id
            outputs = model.forward_record_group_v4(
                states,
                hidden_state=hidden_state,
                episode_start=episode_start,
            )
            hidden_state = outputs[-1].hidden_state.detach() if outputs[-1].hidden_state is not None else None
            previous_game_id = game_id
            transition_count += 1
            transition_has_eligible_prefix = False
            for prefix, output in zip(transition.prefix_steps, outputs):
                semantic_logits = tuple(float(value) for value in output.logits.detach().cpu().tolist())
                stop_logit = None
                if prefix.step_input.stop_available:
                    stop_logit = float((model.stop_vector @ output.global_token + model.stop_bias).detach().cpu().item())
                logits = SpecialistStepLogitsV1(semantic_logits=semantic_logits, stop_logit=stop_logit)
                if prefix.chosen_semantic_action is None and not prefix.chosen_is_stop:
                    target_missing_count += 1
                score = score_public_step_v1(
                    transition.model_input,
                    prefix.step_input,
                    logits,
                    chosen_semantic_action=prefix.chosen_semantic_action,
                    chosen_is_stop=prefix.chosen_is_stop,
                    reference=reference,
                    policy=policy,
                )
                prefix_count += 1
                forced_prefix_count += int(score.forced)
                eligible_prefix_count += int(score.eligible)
                transition_has_eligible_prefix = transition_has_eligible_prefix or score.eligible
                reasons[score.reason] += 1
                if score.ood_unseen:
                    counter["ood_unseen_prefix_count"] += 1
                if score.ood_rare:
                    counter["ood_rare_prefix_count"] += 1
                if score.normalized_surprisal is not None and not score.forced:
                    normalized_surprisal_sum += score.normalized_surprisal
                    normalized_surprisal_count += 1
                    if score.normalized_surprisal >= policy.min_normalized_surprisal:
                        counter["high_surprisal_prefix_count"] += 1
            eligible_transition_count += int(transition_has_eligible_prefix)

    nonforced_prefix_count = prefix_count - forced_prefix_count
    if transition_count == 0 or prefix_count == 0:
        raise ValueError("selected partition contains no canonical transitions")
    return {
        "schema_version": REPLAY_SCHEMA_V1,
        "partition": partition,
        "transition_source_sha256": source_sha256,
        "reference": {
            "artifact_sha256": reference_artifact_sha256,
            "source_sha256": reference.source_sha256,
            "bucket_count": len(reference.bucket_counts),
            "rare_count_threshold": reference.rare_count_threshold,
        },
        "checkpoint": {
            "path": str(checkpoint.resolve()),
            "file_sha256": checkpoint_file_sha256,
            "tensor_state_sha256": checkpoint_tensor_state_sha256,
            "descriptor_sha256": _canonical_sha256(descriptor),
        },
        "transition_count": transition_count,
        "skipped_transition_count": skipped_transition_count,
        "prefix_count": prefix_count,
        "forced_prefix_count": forced_prefix_count,
        "nonforced_prefix_count": nonforced_prefix_count,
        "eligible_prefix_count": eligible_prefix_count,
        "eligible_transition_count": eligible_transition_count,
        "eligible_rate_all": eligible_prefix_count / prefix_count,
        "eligible_rate_nonforced": eligible_prefix_count / nonforced_prefix_count if nonforced_prefix_count else None,
        "target_missing_count": target_missing_count,
        "reason_counts": dict(sorted(reasons.items())),
        "diagnostic_counts": dict(sorted(counter.items())),
        "mean_normalized_surprisal_nonforced": (
            normalized_surprisal_sum / normalized_surprisal_count if normalized_surprisal_count else None
        ),
        "policy": {
            "min_normalized_surprisal": policy.min_normalized_surprisal,
            "max_top1_top2_margin": policy.max_top1_top2_margin,
            "focus_on_ood": policy.focus_on_ood,
        },
        "torch_threads": torch_threads,
        "privacy": {
            "uses_external_identity": False,
            "uses_seat": False,
            "uses_policy_identity": False,
            "uses_hidden_fields": False,
            "uses_game_identity_for_features": False,
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transitions", required=True, type=Path)
    parser.add_argument("--partition", choices=("train", "validation"), default="train")
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--checkpoint-file-sha256", required=True, type=_hex64)
    parser.add_argument("--checkpoint-tensor-state-sha256", required=True, type=_hex64)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--reference-source-sha256", required=True, type=_hex64)
    parser.add_argument("--reference-artifact-sha256", type=_hex64)
    parser.add_argument("--card-vocabulary-size", required=True, type=int)
    parser.add_argument("--hidden-dim", required=True, type=int)
    parser.add_argument("--embedding-dim", required=True, type=int)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--min-normalized-surprisal", type=float, default=0.5)
    parser.add_argument("--max-top1-top2-margin", type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    policy = PublicEligibilityPolicyV1(
        min_normalized_surprisal=args.min_normalized_surprisal,
        max_top1_top2_margin=args.max_top1_top2_margin,
    )
    payload = replay_public_confidence_ood(
        args.transitions,
        args.checkpoint,
        args.reference,
        partition=args.partition,
        checkpoint_file_sha256=args.checkpoint_file_sha256,
        checkpoint_tensor_state_sha256=args.checkpoint_tensor_state_sha256,
        reference_source_sha256=args.reference_source_sha256,
        reference_artifact_sha256=args.reference_artifact_sha256,
        card_vocabulary_size=args.card_vocabulary_size,
        hidden_dim=args.hidden_dim,
        embedding_dim=args.embedding_dim,
        device=args.device,
        torch_threads=args.torch_threads,
        policy=policy,
    )
    _write_json_atomic(args.output, payload)
    print(json.dumps({key: payload[key] for key in ("partition", "transition_count", "prefix_count", "eligible_prefix_count", "eligible_rate_nonforced")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
