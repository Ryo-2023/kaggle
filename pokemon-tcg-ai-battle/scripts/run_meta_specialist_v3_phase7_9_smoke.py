"""Integration smoke for learner, opponent-schedule, and DAgger phases.

This intentionally exercises the Phase 7–9 contracts with deterministic toy
trajectories.  It is useful for catching wiring/schema regressions, but it is
never treated as CABT screening or promotion evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.dagger_dataset_v1 import DAggerDatasetV1, DAggerRecordV1  # noqa: E402
from mage_ptcg.meta_specialist.evaluation_protocol_v2 import paired_summary_v2  # noqa: E402
from mage_ptcg.meta_specialist.experiment_manifest_v1 import promotion_gate_v1  # noqa: E402
from mage_ptcg.meta_specialist.learner_awr_crr_v1 import awr_weights_v1, crr_weights_v1  # noqa: E402
from mage_ptcg.meta_specialist.learner_ppo_recurrent_v1 import ppo_recurrent_loss_v1  # noqa: E402
from mage_ptcg.meta_specialist.learner_vtrace_online_v1 import (  # noqa: E402
    ConsumeOnceVTraceQueueV1,
    vtrace_targets_v1,
)
from mage_ptcg.meta_specialist.opponent_schedule_v2 import sample_opponent_v2, schedule_probabilities_v2  # noqa: E402
from mage_ptcg.meta_specialist.search_teacher_v1 import soft_search_target_v1  # noqa: E402


def _learner_contract_smoke(seed: int) -> dict[str, object]:
    generator = torch.Generator().manual_seed(seed)
    old = torch.log_softmax(torch.randn(8, generator=generator), dim=0)
    new = old + 0.01 * torch.randn(8, generator=generator)
    advantages = torch.randn(8, generator=generator)
    ppo = ppo_recurrent_loss_v1(
        new_log_probs=new, old_log_probs=old, advantages=advantages,
        entropy=torch.full_like(old, 0.8), reference_log_probs=old,
    )
    queue = ConsumeOnceVTraceQueueV1(max_actor_lag=1)
    queue.publish("episode-0", version=4, payload={"seed": seed})
    item = queue.consume("episode-0", learner_version=4)
    rewards = torch.randn(4, generator=generator)
    values = torch.randn(5, generator=generator)
    logp = torch.zeros(4)
    targets = vtrace_targets_v1(
        rewards=rewards, values=values, behavior_log_probs=logp,
        target_log_probs=logp, discounts=torch.ones(4),
    )
    return {
        "ppo_exact_kl": ppo.exact_kl,
        "vtrace_target_count": int(targets.numel()),
        "consume_once_payload": item.payload,
        "awr_max": float(awr_weights_v1(advantages).max().item()),
        "crr_positive_fraction": float(crr_weights_v1(advantages).mean().item()),
    }


def _schedule_smoke(seed: int) -> dict[str, object]:
    meta = {"alakazam": 0.35, "archaludon": 0.30, "grimmsnarl": 0.20, "rocket": 0.15}
    fixed = {key: 1.0 for key in meta}
    hard = {"alakazam": 0.8, "archaludon": 0.4, "grimmsnarl": 0.6, "rocket": 0.2}
    uncertainty = {"alakazam": 0.2, "archaludon": 0.8, "grimmsnarl": 0.5, "rocket": 0.9}
    modes = {
        "O0-fixed-meta": schedule_probabilities_v2(meta, fixed, floor=0.05),
        "O1-adaptive-hard-negative": schedule_probabilities_v2(meta, hard, uncertainty=uncertainty, floor=0.05),
        "O2-mirror-negative-control": schedule_probabilities_v2({key: 1.0 for key in meta}, fixed, floor=0.05),
    }
    counts = {}
    for name, probabilities in modes.items():
        values = [0, 0, 0, 0]
        keys = tuple(sorted(probabilities))
        for index in range(256):
            selected = keys.index(sample_opponent_v2(probabilities, seed=seed + index))
            values[selected] += 1
        counts[name] = {key: values[pos] for pos, key in enumerate(keys)}
    return {"probabilities": modes, "sample_counts_256": counts}


def _dagger_smoke() -> dict[str, object]:
    dataset = DAggerDatasetV1()
    for index in range(8):
        state_hash = hashlib.sha256(f"state-{index // 2}".encode()).hexdigest()
        target = soft_search_target_v1(
            {"play": 1.0 + index / 10, "pass": 0.2},
            standard_errors={"play": 0.1, "pass": 0.2},
            current_policy={"play": 0.5, "pass": 0.5},
        )
        dataset.add(DAggerRecordV1(
            state_hash, "theta0", target.probabilities, target.confidence,
            "high_entropy", "public-opponent-v1",
        ))
    return {"attempted_records": 8, "deduplicated_records": len(dataset), "teacher_confidence_mean": sum(record.teacher_confidence for record in dataset.records()) / len(dataset)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--games", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    learner = {name: {str(seed): _learner_contract_smoke(args.seed + seed) for seed in range(3)} for name in ("L1-PPO", "L2-consume-once-V-trace", "L3-AWR-CRR")}
    candidate = [1 if (index * 17 + args.seed) % 5 < 3 else 0 for index in range(args.games)]
    baseline = [1 if (index * 19 + args.seed) % 5 < 2 else 0 for index in range(args.games)]
    paired = paired_summary_v2(candidate, baseline, seed=args.seed)
    report = {
        "schema": "meta-specialist-v3-phase7-9-smoke",
        "seed": args.seed,
        "tier": "synthetic_contract_smoke",
        "learner_contracts": learner,
        "paired_synthetic_screen": {
            **paired,
            "promotion_gate": promotion_gate_v1(
                paired_delta=float(paired["paired_delta"]), ci_lower=float(paired["bootstrap_ci_low"]),
                fault_rate=0.0, seat_delta=0.0, training_seed_consistency=True,
            ),
            "eligible_for_promotion": False,
        },
        "opponent_schedule": _schedule_smoke(args.seed),
        "dagger": _dagger_smoke(),
        "interpretation": "contract/wiring smoke only; no real CABT games or promotion evidence",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
