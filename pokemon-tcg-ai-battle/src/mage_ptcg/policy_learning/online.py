"""Online PPO and V-trace learner updates over actor-recorded legal trajectories."""
from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Sequence

from .algorithms import generalized_advantage_estimate, ppo_clipped_loss, vtrace_targets
from .data import PolicyLearningExample
from .training import collate


class OnlineLearningError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OnlineStep:
    """One actor decision; no raw observation or hidden opponent state.

    ``ppo_eligible`` marks whether this transition carries a categorical
    behavior probability the policy loss may consume.  A multi-select Top-k
    ranking or an optional decline does not, but it still advances the
    episode, so it stays in the sequence with its policy loss masked instead
    of discarding the whole episode's credit assignment.

    ``environment_step_id`` is CABT's public step counter and ``reward_boundary``
    is False for a follow-up prompt inside one environment step.  Such a
    follow-up must not be discounted as if the environment had advanced.
    """

    example: PolicyLearningExample
    behavior_log_probability: float
    reward: float
    discount: float
    terminal: bool
    actor_policy_version: str
    vocabulary_hash: str
    deck_fingerprint: str
    ppo_eligible: bool = True
    value_eligible: bool = True
    environment_step_id: int | None = None
    decision_substep: int = 0
    reward_boundary: bool = True


def _validate_step(step: OnlineStep) -> None:
    import math

    if (not isinstance(step.actor_policy_version, str) or not step.actor_policy_version
            or not isinstance(step.vocabulary_hash, str) or len(step.vocabulary_hash) != 64
            or not isinstance(step.deck_fingerprint, str) or len(step.deck_fingerprint) != 64):
        raise OnlineLearningError("actor trajectory identity is malformed")
    if step.deck_fingerprint != step.example.deck_fingerprint:
        raise OnlineLearningError("actor trajectory deck does not match the decision")
    if not all(math.isfinite(float(value)) for value in (step.behavior_log_probability, step.reward, step.discount)):
        raise OnlineLearningError("actor trajectory contains a non-finite value")
    if not 0.0 <= step.discount <= 1.0 or (step.terminal and step.discount != 0.0):
        raise OnlineLearningError("terminal mask and discount are inconsistent")


def _validate_steps(steps: Sequence[OnlineStep], *, require_same_policy: bool) -> None:
    if not steps:
        raise OnlineLearningError("online trajectory is empty")
    for step in steps:
        _validate_step(step)
    if require_same_policy and len({step.actor_policy_version for step in steps}) != 1:
        raise OnlineLearningError("PPO batch mixes actor policy versions")
    if len({step.vocabulary_hash for step in steps}) != 1:
        raise OnlineLearningError("online trajectory mixes vocabularies")


def _torch() -> tuple[Any, Any]:
    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as exc:  # pragma: no cover - environment guard
        raise OnlineLearningError("PyTorch is required for online policy learning") from exc
    return torch, functional


def _collate_device(values: Sequence[PolicyLearningExample], families: dict[str, int], device: Any) -> dict[str, Any]:
    # The family auxiliary target is not consumed by PPO/V-trace.  Online
    # CABT records can legitimately lack an opponent family label, so give
    # that unused collate column a valid sentinel index without changing the
    # policy/value input or fabricating a persisted family identity.
    batch = collate(list(values), {"UNKNOWN": 0, **families})
    return {key: value.to(device) for key, value in batch.items() if hasattr(value, "to")}


def _apply(model: Any, tensors: dict[str, Any]) -> dict[str, Any]:
    return model(tensors["state"], tensors["history"], tensors["history_lengths"], tensors["actions"], tensors["action_mask"], tensors["rule_proposal_mask"])


def _select_rows(tensors: dict[str, Any], rows: Any) -> dict[str, Any]:
    """Row subset of an already-collated, already-uploaded batch.

    Every collated column is row-aligned with one decision, so a subset of
    decisions is an index_select.  Rows keep the padding width of the batch
    they were collated in; padded legal-action columns carry ``-inf`` logits
    and contribute nothing to a softmax, so the surviving rows score exactly
    as they would in a narrower batch.
    """
    return {key: value.index_select(0, rows) for key, value in tensors.items()}


def _forward(model: Any, values: Sequence[PolicyLearningExample], families: dict[str, int], device: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    tensors = _collate_device(values, families, device)
    return _apply(model, tensors), tensors


def _masked_policy_distribution(torch: Any, logits: Any, reference_logits: Any) -> tuple[Any, Any, Any]:
    """Return padding-safe (probabilities, log-probs, reference log-probs).

    Padding is represented by ``-inf`` logits.  The values are replaced before
    any multiplication: applying ``where`` only after ``0 * -inf`` has already
    formed a NaN can still leak a NaN gradient through CUDA backward.
    """
    log_probs = torch.log_softmax(logits, dim=1); probabilities = torch.softmax(logits, dim=1)
    reference_log_probs = torch.log_softmax(reference_logits, dim=1)
    legal = torch.isfinite(log_probs) & torch.isfinite(reference_log_probs)
    return (torch.where(legal, probabilities, torch.zeros_like(probabilities)),
            torch.where(legal, log_probs, torch.zeros_like(log_probs)),
            torch.where(legal, reference_log_probs, torch.zeros_like(reference_log_probs)))


def _divergence_from_logits(torch: Any, policy_logits: Any, anchor_logits: Any, behavior_logits: Any,
                            segments: Sequence[tuple[int, int]]) -> dict[str, float]:
    """Per-episode entropy and KLs from one batched set of logits.

    ``segments`` are the (start, stop) row ranges of each episode, so the
    reported values stay the mean over episodes of a per-episode mean rather
    than a flat mean over decisions.
    """
    entropies = []; behavior_kls = []; anchor_kls = []
    with torch.no_grad():
        for start, stop in segments:
            probabilities, log_probs, anchor_log_probs = _masked_policy_distribution(
                torch, policy_logits[start:stop], anchor_logits[start:stop])
            _p, _lp, behavior_log_probs = _masked_policy_distribution(
                torch, policy_logits[start:stop], behavior_logits[start:stop])
            entropies.append(float(-(probabilities * log_probs).sum(dim=1).mean()))
            anchor_kls.append(float((probabilities * (log_probs - anchor_log_probs)).sum(dim=1).mean()))
            behavior_kls.append(float((probabilities * (log_probs - behavior_log_probs)).sum(dim=1).mean()))
    return {"entropy": sum(entropies) / len(entropies),
            "kl_to_behavior": sum(behavior_kls) / len(behavior_kls),
            "kl_to_bc_anchor": sum(anchor_kls) / len(anchor_kls)}


def _policy_divergence(torch: Any, model: Any, reference: Any, behavior_reference: Any,
                       trajectories: Sequence[Sequence[OnlineStep]], families: dict[str, int], device: Any) -> dict[str, float]:
    """Measure the *current* policy against both the behavior and BC anchors.

    ``kl_to_behavior`` is the trust-region quantity: how far the updated policy
    moved from the policy that actually collected this rollout.  Rollback must
    use it.  ``kl_to_bc_anchor`` measures cumulative drift from the initial BC
    checkpoint and is a monitoring signal, not the same constraint.
    """
    segments = []; start = 0
    for episode in trajectories:
        segments.append((start, start + len(episode))); start += len(episode)
    tensors = _collate_device([step.example for episode in trajectories for step in episode], families, device)
    with torch.no_grad():
        return _divergence_from_logits(torch, _apply(model, tensors)["policy_logits"], _apply(reference, tensors)["policy_logits"],
                                       _apply(behavior_reference, tensors)["policy_logits"], segments)


def ppo_update_episodes(
    model: Any, reference_model: Any, optimizer: Any, trajectories: Sequence[Sequence[OnlineStep]], *, families: dict[str, int], device: Any,
    clip_ratio: float = .2, value_weight: float = .5, entropy_weight: float = .001, kl_weight: float = .05,
    gae_lambda: float = .95, epochs: int = 4, minibatch_episodes: int = 64, seed: int = 0,
    max_behavior_kl: float | None = None, min_entropy: float | None = None,
    behavior_model: Any | None = None,
) -> dict[str, float]:
    """KL-anchored PPO over complete on-policy episodes, multi-epoch.

    ``reference_model`` stays frozen at the BC initialization and supplies the
    anchor KL penalty.  Actor log-probabilities remain the PPO behavior policy
    and are never reconstructed from offline rows.

    A single full-batch gradient step per rollout makes the objective
    degenerate: the ratio is exactly 1 at the only point the loss is
    evaluated, so clipping never engages and thousands of collected games buy
    one parameter update.  Recomputing the current log-probabilities per
    minibatch over several epochs is what makes the clipped objective mean
    anything.  ``behavior_log_probability`` stays fixed at its collected value
    throughout, and advantages are normalized once over the whole rollout.
    """
    torch, functional = _torch()
    if (not trajectories or any(not episode for episode in trajectories) or value_weight < 0 or entropy_weight < 0
            or kl_weight < 0 or epochs < 1 or minibatch_episodes < 1):
        raise OnlineLearningError("PPO episode batch is invalid")
    versions: set[str] = set()
    model.train()
    # Actor collection runs inference mode, so retain deterministic actor
    # logits by disabling dropout during this update.  The recurrent learner
    # itself must still be in training mode: cuDNN does not permit GRU/LSTM
    # backward after an eval-mode forward on CUDA.
    for module in model.modules():
        if isinstance(module, torch.nn.Dropout):
            module.eval()
    reference_model.eval()

    # The behavior policy is the parameter state that collected this rollout.
    # Snapshot it before the first step so the trust-region KL below compares
    # against it rather than against the BC anchor.
    if behavior_model is None:
        import copy
        behavior_model = copy.deepcopy(model)
    behavior_model.eval()
    for parameter in behavior_model.parameters():
        parameter.requires_grad_(False)

    prepared = []
    for episode in trajectories:
        _validate_steps(episode, require_same_policy=True); versions.add(episode[0].actor_policy_version)
        if not episode[-1].terminal or any(step.terminal for step in episode[:-1]):
            raise OnlineLearningError("PPO episode terminal mask must end the episode")
        if not any(step.ppo_eligible for step in episode):
            raise OnlineLearningError("PPO episode has no eligible categorical transition")
        prepared.append(episode)
    if len(versions) != 1:
        raise OnlineLearningError("PPO pilot batch mixes actor policy versions")

    # Advantages and value targets come from the collecting policy's value
    # estimates, computed once.  Recomputing them per epoch would make the
    # target chase the parameters being optimized.
    episode_advantages: list[Any] = []; episode_targets: list[Any] = []; eligibility: list[Any] = []
    with torch.no_grad():
        for episode in prepared:
            output, _ = _forward(behavior_model, [step.example for step in episode], families, device)
            rewards = torch.tensor([step.reward for step in episode], dtype=output["value"].dtype, device=device)
            discounts = torch.tensor([step.discount for step in episode], dtype=output["value"].dtype, device=device)
            advantages, targets = generalized_advantage_estimate(rewards, discounts, output["value"], gae_lambda=gae_lambda)
            episode_advantages.append(advantages); episode_targets.append(targets)
            eligibility.append(torch.tensor([step.ppo_eligible for step in episode], dtype=torch.bool, device=device))
    eligible_all = torch.cat([advantage[mask] for advantage, mask in zip(episode_advantages, eligibility)])
    if eligible_all.numel() == 0:
        raise OnlineLearningError("PPO batch has no eligible transitions")
    mean, deviation = eligible_all.mean(), eligible_all.std(unbiased=False).clamp_min(torch.finfo(eligible_all.dtype).eps)
    episode_advantages = [(advantage - mean) / deviation for advantage in episode_advantages]

    # Collate and upload the rollout once.  Re-collating each episode inside
    # every epoch, for the policy, the anchor and the divergence probe, cost
    # hundreds of single-episode forwards per gradient step and dominated the
    # update; the anchor and behavior policies are frozen for the whole update
    # so their logits are computed once here.
    episode_lengths = [len(episode) for episode in prepared]
    row_bounds: list[tuple[int, int]] = []; cursor = 0
    for length in episode_lengths:
        row_bounds.append((cursor, cursor + length)); cursor += length
    all_tensors = _collate_device([step.example for episode in prepared for step in episode], families, device)
    episode_rows = [torch.arange(start, stop, device=device) for start, stop in row_bounds]
    behavior_log_probabilities = torch.tensor([step.behavior_log_probability for episode in prepared for step in episode],
                                              dtype=all_tensors["state"].dtype, device=device)
    value_eligible_all = torch.tensor([step.value_eligible for episode in prepared for step in episode], dtype=torch.bool, device=device)
    with torch.no_grad():
        anchor_logits_all = _apply(reference_model, all_tensors)["policy_logits"]
        behavior_logits_all = _apply(behavior_model, all_tensors)["policy_logits"]

    generator = random.Random(seed)
    order = list(range(len(prepared)))
    gradient_steps = 0; clip_fractions = []; ratios = []
    last = {"total": 0.0, "policy": 0.0, "value": 0.0, "entropy": 0.0, "kl_to_bc_anchor": 0.0}
    stopped_early: str | None = None
    for epoch in range(epochs):
        generator.shuffle(order)
        for start in range(0, len(order), minibatch_episodes):
            indices = order[start:start + minibatch_episodes]
            policy_losses = []; value_losses = []; entropies = []; kls = []
            rows = torch.cat([episode_rows[index] for index in indices])
            tensors = _select_rows(all_tensors, rows)
            output = _apply(model, tensors)
            anchor_logits = anchor_logits_all.index_select(0, rows)
            behavior = behavior_log_probabilities.index_select(0, rows)
            value_eligible = value_eligible_all.index_select(0, rows)
            all_current = torch.log_softmax(output["policy_logits"], dim=1).gather(1, tensors["target"].unsqueeze(1)).squeeze(1)
            offset = 0
            for index in indices:
                length = episode_lengths[index]; span = slice(offset, offset + length); offset += length
                mask = eligibility[index]
                current = all_current[span]; episode_behavior = behavior[span]
                # Ineligible transitions (multi-select Top-k, optional decline)
                # keep the episode's value/GAE chain but contribute no policy
                # gradient: the model defines no probability for their action.
                ratio = torch.exp(current[mask] - episode_behavior[mask])
                policy_losses.append(ppo_clipped_loss(current[mask], episode_behavior[mask], episode_advantages[index][mask], clip_ratio=clip_ratio))
                value_target_mask = value_eligible[span]
                if bool(value_target_mask.any()):
                    value_losses.append(functional.smooth_l1_loss(output["value"][span][value_target_mask], episode_targets[index][value_target_mask]))
                probabilities, log_probs, anchor_log_probs = _masked_policy_distribution(
                    torch, output["policy_logits"][span], anchor_logits[span])
                entropies.append(-(probabilities * log_probs).sum(dim=1).mean())
                kls.append((probabilities * (log_probs - anchor_log_probs)).sum(dim=1).mean())
                with torch.no_grad():
                    ratios.append(float(ratio.mean()))
                    clip_fractions.append(float(((ratio - 1.0).abs() > clip_ratio).to(ratio.dtype).mean()))
            policy = torch.stack(policy_losses).mean()
            value = torch.stack(value_losses).mean() if value_losses else torch.zeros((), device=device)
            entropy = torch.stack(entropies).mean(); kl = torch.stack(kls).mean()
            total = policy + value_weight * value + kl_weight * kl - entropy_weight * entropy
            if not bool(torch.isfinite(total).item()):
                raise OnlineLearningError("PPO pilot loss is non-finite")
            optimizer.zero_grad(set_to_none=True); total.backward()
            gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            if (not bool(torch.isfinite(gradient_norm).item())
                    or any(parameter.grad is not None and not bool(torch.isfinite(parameter.grad).all().item())
                           for parameter in model.parameters())):
                optimizer.zero_grad(set_to_none=True)
                raise OnlineLearningError("PPO pilot gradient is non-finite")
            optimizer.step(); gradient_steps += 1
            if any(not bool(torch.isfinite(parameter).all().item()) for parameter in model.parameters()):
                raise OnlineLearningError("PPO pilot optimizer step produced non-finite parameters")
            last = {"total": float(total.detach()), "policy": float(policy.detach()), "value": float(value.detach()),
                    "entropy": float(entropy.detach()), "kl_to_bc_anchor": float(kl.detach()),
                    "gradient_norm": float(gradient_norm.detach())}
            # Checking the trust region only at the end of the rollout would
            # let an intermediate epoch walk far past it and walk part-way
            # back.  Stop as soon as the bound is crossed.
            if max_behavior_kl is not None or min_entropy is not None:
                # Only the updated policy has to be re-evaluated: the anchor and
                # the behavior policy are frozen, so their logits come from the
                # single pass taken before the epoch loop.
                minibatch_segments = []; cursor = 0
                for index in indices:
                    minibatch_segments.append((cursor, cursor + episode_lengths[index])); cursor += episode_lengths[index]
                with torch.no_grad():
                    probe = _divergence_from_logits(torch, _apply(model, tensors)["policy_logits"], anchor_logits,
                                                    behavior_logits_all.index_select(0, rows), minibatch_segments)
                if max_behavior_kl is not None and probe["kl_to_behavior"] > max_behavior_kl:
                    stopped_early = f"KL_TO_BEHAVIOR_EXCEEDED_AT_EPOCH_{epoch}"
                if min_entropy is not None and probe["entropy"] < min_entropy:
                    stopped_early = f"ENTROPY_BELOW_MINIMUM_AT_EPOCH_{epoch}"
            if stopped_early:
                break
        if stopped_early:
            break

    with torch.no_grad():
        posterior = _divergence_from_logits(torch, _apply(model, all_tensors)["policy_logits"], anchor_logits_all, behavior_logits_all, row_bounds)
    return {**last,
            # Measured after the final optimizer step, so the runner's gate
            # sees the policy it is about to deploy rather than the one that
            # collected the rollout.
            "kl_to_behavior_post": posterior["kl_to_behavior"],
            "kl_to_bc_anchor_post": posterior["kl_to_bc_anchor"],
            "entropy_post": posterior["entropy"],
            "mean_importance_ratio": sum(ratios) / len(ratios) if ratios else 1.0,
            "clip_fraction": sum(clip_fractions) / len(clip_fractions) if clip_fractions else 0.0,
            "gradient_steps": float(gradient_steps), "epochs_requested": float(epochs),
            "early_stop_reason": stopped_early or "NONE",
            "steps": float(int(eligible_all.numel())),
            "episode_decisions": float(sum(len(episode) for episode in prepared)),
            "actor_policy_versions": float(len(versions))}


def ppo_update(
    model: Any, optimizer: Any, steps: Sequence[OnlineStep], *, families: dict[str, int], device: Any,
    clip_ratio: float = .2, value_weight: float = .5, burn_in: int = 0,
) -> dict[str, float]:
    """One PPO update from same-policy actor records with recorded log-probs."""
    torch, functional = _torch()
    if not steps or value_weight < 0 or burn_in < 0 or burn_in >= len(steps):
        raise OnlineLearningError("PPO update input is invalid")
    _validate_steps(steps, require_same_policy=True)
    steps = steps[burn_in:]
    output, tensors = _forward(model, [item.example for item in steps], families, device)
    target = tensors["target"]
    current = torch.log_softmax(output["policy_logits"], dim=1).gather(1, target.unsqueeze(1)).squeeze(1)
    behavior = torch.tensor([item.behavior_log_probability for item in steps], dtype=current.dtype, device=device)
    rewards = torch.tensor([item.reward for item in steps], dtype=current.dtype, device=device)
    # ``terminal`` is validated above and is represented in the recorded
    # discount for bootstrap-capable updates; this one-step PPO pilot uses
    # the actor's immediate terminal reward as its return.
    advantages = (rewards - output["value"]).detach()
    policy = ppo_clipped_loss(current, behavior, advantages, clip_ratio=clip_ratio)
    value = functional.smooth_l1_loss(output["value"], rewards)
    total = policy + value_weight * value
    if not bool(torch.isfinite(total).item()):
        raise OnlineLearningError("PPO loss is non-finite")
    optimizer.zero_grad(set_to_none=True); total.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
    return {"total": float(total.detach()), "policy": float(policy.detach()), "value": float(value.detach()), "steps": float(len(steps))}


def vtrace_update(
    model: Any, optimizer: Any, trajectories: Sequence[Sequence[OnlineStep]], *, families: dict[str, int], device: Any,
    rho_clip: float = 1.0, c_clip: float = 1.0, value_weight: float = .5, burn_in: int = 0,
) -> dict[str, float]:
    """One V-trace update; each inner sequence is one complete actor episode."""
    torch, functional = _torch()
    if (not trajectories or any(not trajectory for trajectory in trajectories) or value_weight < 0
            or burn_in < 0 or any(burn_in >= len(trajectory) for trajectory in trajectories)):
        raise OnlineLearningError("V-trace trajectories are invalid")
    losses = []; policy_losses = []; value_losses = []; count = 0
    optimizer.zero_grad(set_to_none=True)
    for trajectory in trajectories:
        _validate_steps(trajectory, require_same_policy=False)
        if not trajectory[-1].terminal or any(item.terminal for item in trajectory[:-1]):
            raise OnlineLearningError("V-trace terminal mask must end each complete episode")
        # Every example retains its prior public history.  Burn-in decisions
        # reconstruct that context but are excluded from the policy/value loss.
        trajectory = trajectory[burn_in:]
        output, tensors = _forward(model, [item.example for item in trajectory], families, device)
        target = tensors["target"]
        current = torch.log_softmax(output["policy_logits"], dim=1).gather(1, target.unsqueeze(1)).squeeze(1)
        behavior = torch.tensor([item.behavior_log_probability for item in trajectory], dtype=current.dtype, device=device)
        rewards = torch.tensor([item.reward for item in trajectory], dtype=current.dtype, device=device)
        discounts = torch.tensor([item.discount for item in trajectory], dtype=current.dtype, device=device)
        targets, advantages = vtrace_targets(rewards, discounts, output["value"], torch.zeros((), dtype=current.dtype, device=device), current, behavior, rho_clip=rho_clip, c_clip=c_clip)
        policy = -(advantages * current).mean(); value = functional.smooth_l1_loss(output["value"], targets)
        losses.append(policy + value_weight * value); policy_losses.append(policy); value_losses.append(value); count += len(trajectory)
    total = torch.stack(losses).mean()
    if not bool(torch.isfinite(total).item()):
        raise OnlineLearningError("V-trace loss is non-finite")
    total.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step()
    return {"total": float(total.detach()), "policy": float(torch.stack(policy_losses).mean().detach()), "value": float(torch.stack(value_losses).mean().detach()), "steps": float(count)}
