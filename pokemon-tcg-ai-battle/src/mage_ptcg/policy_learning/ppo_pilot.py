"""Resumable, candidate-only BC-initialized PPO pilot state machine."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import random
import re
from typing import Any

from .data import PolicyDataError, from_record, load_examples, vocabulary_hash
from .model import ActorCriticConfig, build_actor_critic
from .online import OnlineLearningError, OnlineStep, ppo_update_episodes
from mage_ptcg.offline_scaleup.progress import ProgressReporter
from .training import _device, _torch, collate, family_vocabulary, load_model


SCHEMA = "policy-learning-ppo-pilot-v1"


class PilotError(ValueError):
    pass


def rollout_resume_base(summary: dict[str, Any], rollout_root: Path) -> int:
    """Return the round number immediately before the next runner iteration.

    A completed collection is not consumed until its successful PPO metric is
    persisted.  Returning one less for an unconsumed newest directory makes
    the shell loop revisit that immutable rollout after a failed update.
    """
    try:
        ppo = summary["ppo"]
        counter = int(ppo["rollouts"]) if "rollouts" in ppo else int(ppo.get("updates", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise PilotError("PPO training summary has invalid rollout counters") from exc
    persisted = [
        int(match.group(1))
        for path in rollout_root.glob("round-*")
        if path.is_dir() and (match := re.fullmatch(r"round-(\d+)", path.name))
    ]
    processed: set[int] = set()
    for metric in ppo.get("metrics", []):
        match = re.fullmatch(r"round-(\d+)", Path(str(metric.get("run_dir", ""))).name)
        if match:
            processed.add(int(match.group(1)))
    latest = max(persisted, default=0)
    return latest - 1 if latest and latest not in processed else max(counter, latest)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _torch_save(torch: Any, path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp"); torch.save(value, temporary); os.replace(temporary, path)


def _rng_state(torch: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"python": random.getstate(), "torch": torch.get_rng_state()}
    if torch.cuda.is_available():
        value["cuda"] = torch.cuda.get_rng_state_all()
    return value


def _restore_rng_state(torch: Any, value: dict[str, Any]) -> None:
    try:
        random.setstate(value["python"])
        torch.set_rng_state(value["torch"])
        if torch.cuda.is_available() and "cuda" in value:
            torch.cuda.set_rng_state_all(value["cuda"])
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise PilotError("PPO pilot RNG state is malformed") from exc


def _runtime_checkpoint(model: Any, summary: dict[str, Any]) -> dict[str, Any]:
    return {"schema": summary["schema"], "model": model.state_dict(), "config": summary["config"], "families": summary["families"],
            "vocabulary_hash": summary["vocabulary_hash"], "dataset_sha256": summary["dataset_sha256"]}


def _load_examples_with_progress(dataset: Path, *, phase: str, progress: bool | None,
                                 progress_interval_seconds: float | None) -> list[Any]:
    total = dataset.stat().st_size
    reporter = ProgressReporter(phase=phase, total=total, unit="byte", progress=progress,
                                interval_seconds=progress_interval_seconds)
    last = 0

    def report(done: int, _total: int) -> None:
        nonlocal last
        delta = max(0, done - last)
        if delta:
            reporter.update(delta)
            last += delta

    try:
        return load_examples(dataset, splits=("train",), on_progress=report)
    finally:
        reporter.close()


def initialize(*, bc_model_dir: Path, output_dir: Path, device_name: str = "cpu", learning_rate: float = 1e-5,
               seed: int = 91000, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PilotError("PPO pilot output directory already exists")
    model, source_summary, families = load_model(bc_model_dir, device_name=device_name)
    if not source_summary.get("config", {}).get("use_recurrence", False):
        raise PilotError("PPO pilot requires a recurrent BC initialization")
    source_dataset = Path(str(source_summary.get("dataset", "")))
    if not source_dataset.is_file():
        raise PilotError("BC initialization dataset is unavailable for exact-deck verification")
    deck_fingerprints = {example.deck_fingerprint for example in _load_examples_with_progress(
        source_dataset, phase="gate5a-verify-dataset", progress=progress, progress_interval_seconds=progress_interval_seconds)}
    if len(deck_fingerprints) != 1:
        raise PilotError("PPO pilot requires one exact BC training deck")
    torch, _functional, _loader, _dataset = _torch(); device = _device(device_name)
    random.seed(seed); torch.manual_seed(seed)
    if device.type == "cuda": torch.cuda.manual_seed_all(seed)
    reference = build_actor_critic(ActorCriticConfig(**source_summary["config"])).to(device)
    reference.load_state_dict(model.state_dict()); reference.eval()
    for parameter in reference.parameters(): parameter.requires_grad_(False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {**source_summary, "schema": SCHEMA, "objective": "ppo-pilot", "device": str(device),
               "bc_initialization": {"model_dir": str(bc_model_dir), "checkpoint_sha256": hashlib.sha256((bc_model_dir / "best.pt").read_bytes()).hexdigest()},
               "exact_deck_fingerprint": next(iter(deck_fingerprints)),
               "ppo": {"learning_rate": learning_rate, "seed": seed, "updates": 0, "decisions": 0, "value_warmup_epochs": 0,
                       "actor_sampling": "categorical-legal-actions-checkpoint-seeded-v1", "scheduler": None}}
    _atomic_json(output_dir / "training_summary.json", summary)
    _torch_save(torch, output_dir / "best.pt", _runtime_checkpoint(model, summary))
    _torch_save(torch, output_dir / "pilot_state.pt", {"schema": SCHEMA, "model": model.state_dict(), "reference": reference.state_dict(),
                                                         "optimizer": optimizer.state_dict(), "config": summary["config"], "families": families,
                                                         "summary": summary, "rng_state": _rng_state(torch)})
    return summary


def _load_state(output_dir: Path, device_name: str) -> tuple[Any, Any, Any, dict[str, Any], dict[str, int], Any]:
    torch, _functional, _loader, _dataset = _torch(); device = _device(device_name)
    # ``map_location=device`` would also move the CPU and CUDA RNG byte
    # tensors to the learner GPU.  ``torch.set_rng_state`` requires a CPU
    # byte tensor, which made the first CUDA resume fail.  Load metadata and
    # checkpoint tensors on CPU, then let ``load_state_dict`` copy model and
    # optimizer tensors to the already-device-bound learner objects.
    state = torch.load(output_dir / "pilot_state.pt", map_location="cpu", weights_only=False)
    if state.get("schema") != SCHEMA:
        raise PilotError("PPO pilot state schema is unsupported")
    _restore_rng_state(torch, state.get("rng_state", {}))
    summary = state["summary"]; config = ActorCriticConfig(**state["config"]); families = state["families"]
    model = build_actor_critic(config).to(device); reference = build_actor_critic(config).to(device)
    model.load_state_dict(state["model"]); reference.load_state_dict(state["reference"]); reference.eval()
    for parameter in reference.parameters(): parameter.requires_grad_(False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=float(summary["ppo"]["learning_rate"])); optimizer.load_state_dict(state["optimizer"])
    return model, reference, optimizer, summary, families, state


def value_warmup(*, output_dir: Path, dataset: Path, device_name: str = "cpu", epochs: int = 5, batch_size: int = 256,
                 progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    if epochs < 1: raise PilotError("value warm-up epochs must be positive")
    model, reference, optimizer, summary, families, state = _load_state(output_dir, device_name)
    del reference, optimizer
    torch, functional, _loader, _dataset = _torch(); device = _device(device_name)
    values = _load_examples_with_progress(dataset, phase="gate5a-load-warmup-data", progress=progress,
                                          progress_interval_seconds=progress_interval_seconds)
    if family_vocabulary(values) != families: raise PilotError("value warm-up dataset family vocabulary differs from BC initialization")
    for parameter in model.parameters(): parameter.requires_grad_(False)
    for parameter in model.value_head.parameters(): parameter.requires_grad_(True)
    optimizer = torch.optim.AdamW(model.value_head.parameters(), lr=float(summary["ppo"]["learning_rate"]))
    losses = []; steps_per_epoch = (len(values) + batch_size - 1) // batch_size
    reporter = ProgressReporter(phase="gate5a-value-warmup", total=epochs * steps_per_epoch, unit="batch", progress=progress,
                                interval_seconds=progress_interval_seconds)
    try:
        for epoch in range(epochs):
            for start in range(0, len(values), batch_size):
                batch = collate(values[start:start + batch_size], families); tensors = {key: value.to(device) for key, value in batch.items() if hasattr(value, "to")}
                output = model(tensors["state"], tensors["history"], tensors["history_lengths"], tensors["actions"], tensors["action_mask"], tensors["rule_proposal_mask"])
                loss = functional.smooth_l1_loss(output["value"], tensors["returns"])
                if not bool(torch.isfinite(loss).item()): raise PilotError("value warm-up loss is non-finite")
                optimizer.zero_grad(set_to_none=True); loss.backward(); torch.nn.utils.clip_grad_norm_(model.value_head.parameters(), 1.0); optimizer.step(); losses.append(float(loss.detach()))
                reporter.update(1, epoch=epoch + 1, loss=round(float(loss.detach()), 6))
    finally:
        reporter.close()
    for parameter in model.parameters(): parameter.requires_grad_(True)
    summary["ppo"]["value_warmup_epochs"] = int(summary["ppo"].get("value_warmup_epochs", 0)) + epochs
    summary["ppo"]["value_warmup_loss"] = sum(losses) / len(losses)
    state.update({"model": model.state_dict(), "optimizer": torch.optim.AdamW(model.parameters(), lr=float(summary["ppo"]["learning_rate"])).state_dict(), "summary": summary,
                  "rng_state": _rng_state(torch)})
    _atomic_json(output_dir / "training_summary.json", summary); _torch_save(torch, output_dir / "best.pt", _runtime_checkpoint(model, summary)); _torch_save(torch, output_dir / "pilot_state.pt", state)
    return {"value_warmup_loss": summary["ppo"]["value_warmup_loss"], "epochs": epochs}


def _outcome(game: dict[str, Any]) -> float:
    winner, side = game.get("winner"), int(game["candidate_side"])
    if winner == side:
        return 1.0
    if winner in (0, 1):
        return -1.0
    if winner == -1:
        return 0.0
    raise PilotError("online game winner is undocumented")


def _environment_step_id(sample: dict[str, Any]) -> int | None:
    """CABT's public step counter for this decision, if it was recorded.

    It is an actor-visible public field.  Empirically each candidate decision
    occupies its own step, but the counter is read rather than assumed so a
    follow-up prompt inside one environment step stays detectable.
    """
    state = sample.get("public_state")
    value = state.get("step") if isinstance(state, dict) else None
    return int(value) if isinstance(value, int) else None


def trajectories_from_run(run_dir: Path) -> list[list[OnlineStep]]:
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    if summary.get("gate") != "PASS": raise PilotError("PPO rollout run must PASS")
    trajectories: list[list[OnlineStep]] = []
    for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines():
        game = json.loads(line)
        # A missing ``fallback_count`` is an unmeasured run, not a clean one.
        # Defaulting it to 0 would silently admit pre-contract rollouts.
        if "fallback_count" not in game:
            raise PilotError("PPO rollout game is missing fallback_count")
        if game.get("status") != "DONE" or game["fallback_count"] != 0: continue
        counters = game.get("decision_counters")
        if isinstance(counters, dict) and int(counters.get("uncaptured_fallback_count", 0)) != 0:
            # A Rule-v0 delegation on the optional-prompt path leaves no
            # decision row, so the episode is mixed-behavior even though every
            # captured row looks clean.
            continue
        # CABT can emit a completed result with an undocumented winner code
        # (for example ``2``).  It is neither a draw nor a loss by contract,
        # so excluding the whole recurrent episode is safer than inventing a
        # terminal reward for an otherwise legal rollout.
        if game.get("winner") not in (0, 1, -1):
            continue
        samples = game.get("teacher_samples", [])
        # A Rule-v0 fallback is mixed-behavior data and still invalidates the
        # episode.  A legal multi-select Top-k ranking or an optional decline
        # does not: the model simply defines no categorical probability for
        # that action, so the transition keeps the episode's value/GAE chain
        # with its policy loss masked.  (Decisions are encoded independently;
        # there is no cross-decision hidden state to contaminate.  The reason
        # to keep the episode intact is credit assignment, not recurrence.)
        if not isinstance(samples, list) or any(not isinstance(sample, dict) or sample.get("fallback_used") for sample in samples):
            continue
        if not any(sample.get("ppo_eligible") is not False for sample in samples):
            continue
        steps: list[OnlineStep] = []
        previous_step_id: int | None = None
        substep = 0
        for index, sample in enumerate(samples):
            eligible = sample.get("ppo_eligible") is not False
            step_id = _environment_step_id(sample)
            if step_id is not None and step_id == previous_step_id:
                substep += 1
            else:
                substep = 0
            row = {"episode_id": game["game_id"], "split": "train", "candidate_outcome": "UNKNOWN", "teacher_trust": "LIMITED", "rule_bc_example": sample,
                   "behavior_log_probability": sample.get("behavior_log_probability"), "decision_index": index}
            try:
                example = from_record(row, default_decision_index=index)
            except PolicyDataError as exc:
                if not eligible:
                    # Only categorical transitions have to satisfy the
                    # single-action dataset contract.
                    previous_step_id = step_id
                    continue
                raise PilotError("PPO-eligible rollout contains an invalid categorical transition") from exc
            version, vocab = sample.get("actor_policy_version"), sample.get("vocabulary_hash")
            behavior = sample.get("behavior_log_probability")
            if not isinstance(version, str) or not isinstance(vocab, str):
                raise PilotError("PPO rollout lacks actor version/vocabulary")
            if eligible and not isinstance(behavior, (float, int)):
                raise PilotError("PPO rollout lacks actor log-probability")
            steps.append(OnlineStep(example=example, behavior_log_probability=float(behavior) if isinstance(behavior, (float, int)) else 0.0,
                                    reward=0.0, discount=.99, terminal=False,
                                    actor_policy_version=version, vocabulary_hash=vocab, deck_fingerprint=example.deck_fingerprint,
                                    ppo_eligible=eligible, value_eligible=True,
                                    environment_step_id=step_id, decision_substep=substep, reward_boundary=substep == 0))
            previous_step_id = step_id
        if steps and any(step.ppo_eligible for step in steps):
            # A follow-up prompt inside one environment step must not be
            # discounted as if the environment had advanced.
            for index in range(len(steps) - 1):
                if steps[index + 1].decision_substep > 0:
                    steps[index] = replace(steps[index], discount=1.0, reward_boundary=False)
            steps[-1] = replace(steps[-1], reward=_outcome(game), discount=0.0, terminal=True, reward_boundary=True)
            trajectories.append(steps)
    if not trajectories: raise PilotError("PPO rollout has no usable non-fallback single-action episodes")
    return trajectories


def trajectory_eligibility_report(run_dir: Path) -> dict[str, Any]:
    """Classify every rollout episode before PPO filtering without guessing rewards."""
    reasons: Counter[str] = Counter(); total_decisions = 0; eligible_decisions = 0; fallback_decisions = 0
    for line in (run_dir / "game_results.jsonl").read_text(encoding="utf-8").splitlines():
        game = json.loads(line); samples = game.get("teacher_samples", [])
        if not isinstance(samples, list):
            reasons["MALFORMED_SAMPLES"] += 1; continue
        total_decisions += len(samples)
        fallback_decisions += sum(bool(sample.get("fallback_used")) for sample in samples if isinstance(sample, dict))
        if game.get("status") != "DONE": reasons["NON_TERMINAL_OR_INVALID_GAME"] += 1; continue
        if "fallback_count" not in game: reasons["FALLBACK_COUNT_NOT_RECORDED"] += 1; continue
        if game["fallback_count"] != 0 or any(isinstance(sample, dict) and sample.get("fallback_used") for sample in samples):
            reasons["FALLBACK_MIXED_EPISODE"] += 1; continue
        counters = game.get("decision_counters")
        if isinstance(counters, dict) and int(counters.get("uncaptured_fallback_count", 0)) != 0:
            reasons["UNCAPTURED_FALLBACK_EPISODE"] += 1; continue
        if game.get("winner") not in (0, 1, -1): reasons["UNDOCUMENTED_TERMINAL_WINNER"] += 1; continue
        if not samples: reasons["NO_TRAINABLE_DECISION"] += 1; continue
        if any(not isinstance(sample, dict) or sample.get("ppo_eligible") is False for sample in samples):
            reasons["NON_CATEGORICAL_ACTION_SET"] += 1; continue
        if any(not isinstance(sample.get("behavior_log_probability"), (int, float)) for sample in samples):
            reasons["MISSING_BEHAVIOR_LOG_PROB"] += 1; continue
        reasons["PPO_ELIGIBLE"] += 1; eligible_decisions += len(samples)
    return {"schema":"policy-learning-ppo-eligibility-v1", "total_episodes":sum(reasons.values()),
            "episodes_by_reason":dict(sorted(reasons.items())), "total_decisions":total_decisions,
            "ppo_eligible_decisions":eligible_decisions, "fallback_decisions":fallback_decisions,
            "ppo_episode_utilization":(reasons["PPO_ELIGIBLE"] / sum(reasons.values())) if reasons else 0.0}


def update(*, output_dir: Path, run_dir: Path, device_name: str = "cpu", clip_ratio: float = .2, value_weight: float = .5,
           entropy_weight: float = .001, kl_weight: float = .05, gae_lambda: float = .95,
           epochs: int = 4, minibatch_episodes: int = 64,
           max_behavior_kl: float | None = None, min_entropy: float | None = None,
           learning_rate: float | None = None) -> dict[str, Any]:
    model, reference, optimizer, summary, families, state = _load_state(output_dir, device_name)
    eligibility = trajectory_eligibility_report(run_dir)
    _atomic_json(run_dir / "ppo_eligibility.json", eligibility)
    trajectories = trajectories_from_run(run_dir); expected = hashlib.sha256((output_dir / "best.pt").read_bytes()).hexdigest()
    versions = {step.actor_policy_version for episode in trajectories for step in episode}
    if versions != {expected}: raise PilotError("rollout actor version does not match current PPO checkpoint")
    vocabularies = {step.vocabulary_hash for episode in trajectories for step in episode}
    if vocabularies != {summary["vocabulary_hash"]}:
        raise PilotError("rollout vocabulary does not match PPO initialization")
    decks = {step.deck_fingerprint for episode in trajectories for step in episode}
    if decks != {summary["exact_deck_fingerprint"]}:
        raise PilotError("rollout deck does not match PPO initialization")
    if learning_rate is not None:
        # A rollout that used to buy one gradient step now buys tens of them,
        # so the learning rate is no longer implied by the artifact and must
        # be re-stated (and re-tuned) explicitly.
        if learning_rate <= 0:
            raise PilotError("PPO learning rate must be positive")
        for group in optimizer.param_groups:
            group["lr"] = learning_rate
        summary["ppo"]["learning_rate"] = learning_rate
    metrics = ppo_update_episodes(model, reference, optimizer, trajectories, families=families, device=_device(device_name), clip_ratio=clip_ratio,
                                  value_weight=value_weight, entropy_weight=entropy_weight, kl_weight=kl_weight, gae_lambda=gae_lambda,
                                  epochs=epochs, minibatch_episodes=minibatch_episodes,
                                  seed=int(summary["ppo"].get("seed", 0)) + int(summary["ppo"].get("updates", 0)),
                                  max_behavior_kl=max_behavior_kl, min_entropy=min_entropy)
    torch, _functional, _loader, _dataset = _torch()
    summary["ppo"]["updates"] += int(metrics["gradient_steps"]); summary["ppo"]["rollouts"] = int(summary["ppo"].get("rollouts", 0)) + 1
    summary["ppo"]["decisions"] += int(metrics["steps"])
    summary["ppo"].setdefault("metrics", []).append({"run_dir": str(run_dir), **metrics})
    state.update({"model": model.state_dict(), "optimizer": optimizer.state_dict(), "summary": summary, "rng_state": _rng_state(torch)})
    _atomic_json(output_dir / "training_summary.json", summary); _torch_save(torch, output_dir / "best.pt", _runtime_checkpoint(model, summary)); _torch_save(torch, output_dir / "pilot_state.pt", state)
    return {"episodes": len(trajectories), "excluded_episodes":eligibility["total_episodes"] - len(trajectories),
            "ppo_episode_utilization":eligibility["ppo_episode_utilization"], **metrics, "decisions_total": summary["ppo"]["decisions"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mage-policy-ppo-pilot"); sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("initialize"); init.add_argument("--bc-model-dir", type=Path, required=True); init.add_argument("--output-dir", type=Path, required=True); init.add_argument("--device", default="cpu"); init.add_argument("--learning-rate", type=float, default=1e-5); init.add_argument("--seed", type=int, default=91000); init.add_argument("--progress", action="store_true"); init.add_argument("--progress-interval-seconds", type=float, default=None)
    warm = sub.add_parser("value-warmup"); warm.add_argument("--output-dir", type=Path, required=True); warm.add_argument("--dataset", type=Path, required=True); warm.add_argument("--device", default="cpu"); warm.add_argument("--epochs", type=int, default=5); warm.add_argument("--batch-size", type=int, default=256); warm.add_argument("--progress", action="store_true"); warm.add_argument("--progress-interval-seconds", type=float, default=None)
    upd = sub.add_parser("update"); upd.add_argument("--output-dir", type=Path, required=True); upd.add_argument("--run-dir", type=Path, required=True); upd.add_argument("--device", default="cpu"); upd.add_argument("--clip-ratio", type=float, default=.2); upd.add_argument("--value-weight", type=float, default=.5); upd.add_argument("--entropy-weight", type=float, default=.001); upd.add_argument("--kl-weight", type=float, default=.05); upd.add_argument("--gae-lambda", type=float, default=.95)
    upd.add_argument("--ppo-epochs", type=int, default=4); upd.add_argument("--minibatch-episodes", type=int, default=64)
    upd.add_argument("--max-behavior-kl", type=float, default=None, help="stop the update as soon as the policy leaves this trust region")
    upd.add_argument("--min-entropy", type=float, default=None, help="stop the update if the policy collapses below this entropy")
    upd.add_argument("--learning-rate", type=float, default=None, help="override the stored PPO learning rate for this update")
    args = parser.parse_args(argv)
    try:
        if args.command == "initialize":
            summary = initialize(bc_model_dir=args.bc_model_dir, output_dir=args.output_dir, device_name=args.device, learning_rate=args.learning_rate, seed=args.seed,
                                 progress=True if args.progress else None, progress_interval_seconds=args.progress_interval_seconds)
            result = {"event": "PPO_PILOT_INITIALIZED", "output_dir": str(args.output_dir), "device": summary["device"],
                      "bc_checkpoint_sha256": summary["bc_initialization"]["checkpoint_sha256"],
                      "exact_deck_fingerprint": summary["exact_deck_fingerprint"]}
        elif args.command == "value-warmup": result = value_warmup(output_dir=args.output_dir, dataset=args.dataset, device_name=args.device, epochs=args.epochs, batch_size=args.batch_size,
                                                                      progress=True if args.progress else None, progress_interval_seconds=args.progress_interval_seconds)
        else: result = update(output_dir=args.output_dir, run_dir=args.run_dir, device_name=args.device, clip_ratio=args.clip_ratio, value_weight=args.value_weight, entropy_weight=args.entropy_weight, kl_weight=args.kl_weight, gae_lambda=args.gae_lambda,
                              epochs=args.ppo_epochs, minibatch_episodes=args.minibatch_episodes,
                              max_behavior_kl=args.max_behavior_kl, min_entropy=args.min_entropy, learning_rate=args.learning_rate)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0
    except (PilotError, OnlineLearningError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False)); return 2


if __name__ == "__main__": raise SystemExit(main())
