"""Run the C0/C1/C2 critic-conditioning ablation on a deterministic toy split.

The task intentionally makes the stable opponent family predictive while making
the per-game seed non-predictive.  It is a wiring/calibration experiment, not a
claim about CABT win rate or a substitute for the sealed teacher corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.critic_conditioning_v3 import OutcomeCriticConditionedV3  # noqa: E402
from mage_ptcg.meta_specialist.critic_v3 import calibration_metrics_v3  # noqa: E402
from mage_ptcg.meta_specialist.critic_warmup_v3 import warmup_critic_v3  # noqa: E402


def _episodes(*, seed: int, count: int, steps: int, hidden_dim: int) -> tuple[tuple[object, ...], ...]:
    if count < 2 or steps < 1 or hidden_dim < 1:
        raise ValueError("count/steps/hidden_dim are invalid")
    generator = torch.Generator().manual_seed(seed)
    episodes = []
    for index in range(count):
        family = "family-a" if index % 2 == 0 else "family-b"
        # The feature tensor has no family signal.  C1 can use stable
        # provenance; C0 must learn the marginal; C2 sees an unrelated seed.
        features = torch.randn(steps, hidden_dim, generator=generator) * 0.01
        label = 2 if family == "family-a" else 0
        labels = torch.full((steps,), label, dtype=torch.long)
        provenance = {
            "opponent_family": family,
            "deck_fingerprint": f"{family}-deck",
            "policy_family": "fixed-policy",
            "game_seed": seed * 100_000 + index * 17 + 3,
        }
        episodes.append((features, labels, provenance))
    return tuple(episodes)


def _metrics(critic: OutcomeCriticConditionedV3, episodes: tuple[tuple[object, ...], ...]) -> dict[str, float]:
    with torch.no_grad():
        probabilities = torch.cat([
            critic(features, provenance=provenance).probabilities
            for features, _labels, provenance in episodes
        ])
        labels = torch.cat([labels for _features, labels, _provenance in episodes]).to(torch.long)
    return calibration_metrics_v3(probabilities, labels)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--episodes", type=int, default=96)
    parser.add_argument("--validation-episodes", type=int, default=48)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    hidden_dim = 16
    train = _episodes(seed=args.seed, count=args.episodes, steps=args.steps, hidden_dim=hidden_dim)
    valid = _episodes(seed=args.seed + 1, count=args.validation_episodes, steps=args.steps, hidden_dim=hidden_dim)
    results: dict[str, object] = {}
    for mode in ("none", "stable", "game-seed"):
        critic = OutcomeCriticConditionedV3(hidden_dim=hidden_dim, mode=mode, seed=args.seed)
        initial = _metrics(critic, valid)
        warmup = warmup_critic_v3(critic, train, epochs=args.epochs, learning_rate=2e-3)
        final = _metrics(critic, valid)
        results[mode] = {"train_warmup": warmup, "validation_initial": initial, "validation_final": final}
    report = {
        "schema": "meta-specialist-critic-conditioning-ablation-v3",
        "seed": args.seed,
        "train_episodes": args.episodes,
        "validation_episodes": args.validation_episodes,
        "steps": args.steps,
        "modes": ["none", "stable", "game-seed"],
        "results": results,
        "interpretation": "stable family is the only intended predictive provenance; game_seed is a negative control",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
