"""Fail-closed CABT runtime for a trained legal-action actor-critic."""
from __future__ import annotations

import math
import hashlib
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.decision_state import DecisionStateError, build_decision_state
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.features import action_features, state_features
# Canonical MAIN-selection predicate.  Rule v0 and Student v0 both decline
# optional auxiliary prompts through it; reuse keeps one convention so that
# swapping the candidate policy does not silently change play.
from mage_ptcg.student.runtime import _is_main_selection

from .data import _history_features, vocabulary_hash
from .training import load_model


class PolicyRuntimeError(RuntimeError):
    pass


class RecurrentLegalActorCriticPolicy:
    """Scores CABT legal candidates and never emits a free-form action."""

    def __init__(self, *, model: Any, device: str, deck: list[int], actor_policy_version: str, vocabulary_digest: str,
                 use_rule_proposal: bool = False, rule_proposal_agent: Any | None = None,
                 stochastic_actions: bool = False) -> None:
        if len(deck) != 60 or any(type(card) is not int for card in deck):
            raise PolicyRuntimeError("exact 60-card deck is required")
        if not isinstance(actor_policy_version, str) or len(actor_policy_version) != 64:
            raise PolicyRuntimeError("actor policy version is malformed")
        if vocabulary_digest != vocabulary_hash():
            raise PolicyRuntimeError("runtime vocabulary does not match the checkpoint")
        self.model, self.device, self.deck = model, device, list(deck)
        self.actor_policy_version, self.vocabulary_hash = actor_policy_version, vocabulary_digest
        self.last_decision_trace: dict[str, Any] | None = None
        self._visible_history: tuple[str, ...] = ()
        self._deck_delivered = False
        self.use_rule_proposal = use_rule_proposal
        self._rule_proposal_agent = rule_proposal_agent
        self.stochastic_actions = stochastic_actions
        self._sampling_rng: Any | None = None
        if stochastic_actions:
            try:
                import torch
                self._sampling_rng = torch.Generator(device=device)
                # A checkpoint-specific seed makes retries/resume replay the
                # same actor policy while still sampling from its legal
                # categorical distribution.
                self._sampling_rng.manual_seed(int(actor_policy_version[:16], 16))
            except Exception as exc:
                raise PolicyRuntimeError("PPO actor sampling generator failed to initialize") from exc

    def set_rule_proposal_agent(self, agent: Any) -> None:
        self._rule_proposal_agent = agent

    def reset_episode(self) -> None:
        """Erase state before a new CABT game; adapters call this per seat."""
        self._visible_history = ()
        self._deck_delivered = False
        self.last_decision_trace = {"status": "episode_reset"}

    def set_episode_seed(self, *, game_id: str, candidate_side: int) -> None:
        """Bind PPO sampling to a recorded episode without sharing RNG state.

        The adapter calls this immediately after CABT's deck request, before
        the first selectable prompt.  A retry therefore replays the same
        sampled trajectory while distinct games explore independently.
        """
        if not self.stochastic_actions:
            return
        if not isinstance(game_id, str) or not game_id or candidate_side not in (0, 1):
            raise PolicyRuntimeError("PPO episode sampling identity is malformed")
        import hashlib
        digest = hashlib.sha256(f"{self.actor_policy_version}\0{game_id}\0{candidate_side}".encode("utf-8")).digest()
        self._sampling_rng.manual_seed(int.from_bytes(digest[:8], "big"))

    def set_visible_history(self, history: tuple[str, ...]) -> None:
        if len(history) > 64 or any(type(item) is not str or len(item) != 64 for item in history):
            raise PolicyRuntimeError("visible history contract is malformed")
        self._visible_history = history

    def choose(self, observation: object) -> list[int]:
        if not isinstance(observation, Mapping):
            raise PolicyRuntimeError("observation is not a mapping")
        if observation.get("select") is None:
            # CABT's initial deck request and a later terminal/no-decision
            # callback both have no ``select`` payload.  A 60-card deck is
            # valid only once; returning it at a terminal callback marks this
            # seat ERROR despite the policy never selecting an illegal action.
            if self._deck_delivered:
                self.last_decision_trace = {"status": "terminal_no_select"}
                return []
            self.reset_episode()
            self._deck_delivered = True
            self.last_decision_trace = {"status": "deck_request"}
            return list(self.deck)
        select = observation.get("select")
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list):
            raise PolicyRuntimeError("select contract is malformed")
        minimum, maximum = select.get("minCount"), select.get("maxCount")
        if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum <= len(select["option"]):
            raise PolicyRuntimeError("selection cardinality is malformed")
        try:
            if is_ordered_selection(select.get("type"), select.get("context")):
                raise PolicyRuntimeError(
                    "candidate-wise actor-critic cannot decode ordered Skill labels"
                )
        except ValueError as exc:
            raise PolicyRuntimeError("selection has an unknown CABT schema") from exc
        if maximum == 0:
            # An empty optional prompt is legal, but it is not a categorical
            # action and consequently cannot be used as a PPO transition.
            self.last_decision_trace = {"status": "selected", "selected_count": 0,
                                        "actor_action_mode": "optional_empty", "ppo_eligible": False,
                                        "behavior_log_probability": None,
                                        "behavior_log_probability_kind": "NOT_PPO_OPTIONAL_DECLINE"}
            return []
        if minimum == 0 and not _is_main_selection(select.get("type")):
            # ``minCount == 0`` with ``maxCount >= 1`` is a legal auxiliary
            # prompt that may be answered with nothing.  Declining matches
            # Rule v0 and Student v0; it is not a runtime failure and must
            # never be recorded as a Rule-v0 fallback.  It is also not a
            # categorical action, so it carries no PPO log-probability.
            self.last_decision_trace = {"status": "selected", "selected_count": 0,
                                        "actor_action_mode": "optional_declined", "ppo_eligible": False,
                                        "behavior_log_probability": None,
                                        "behavior_log_probability_kind": "NOT_PPO_OPTIONAL_DECLINE",
                                        "actor_policy_version": self.actor_policy_version,
                                        "vocabulary_hash": self.vocabulary_hash}
            return []
        try:
            state = build_decision_state(observation, visible_history=self._visible_history)
        except DecisionStateError as exc:
            raise PolicyRuntimeError("decision state construction failed") from exc
        if not state.legal_actions:
            raise PolicyRuntimeError("decision has no legal actions")
        try:
            import torch
            state_t = torch.tensor([state_features(state.actor_view)], dtype=torch.float32, device=self.device)
            history = _history_features(tuple(state.actor_view.visible_history))
            history_t = torch.tensor([history], dtype=torch.float32, device=self.device)
            lengths_t = torch.tensor([len(history)], dtype=torch.long, device=self.device)
            action_t = torch.tensor([[action_features(action.action_key) for action in state.legal_actions]], dtype=torch.float32, device=self.device)
            mask_t = torch.ones((1, len(state.legal_actions)), dtype=torch.bool, device=self.device)
            proposal_t = torch.zeros((1, len(state.legal_actions)), dtype=torch.bool, device=self.device)
            proposal_digest = None
            # Rule proposals are trained as a single legal-action feature.
            # A multi-select proposal has no equivalent representation, so
            # leave it absent rather than invoking Rule v0 as a fallback.
            if self.use_rule_proposal and minimum == maximum == 1:
                if self._rule_proposal_agent is None:
                    raise PolicyRuntimeError("Rule v0 proposal agent is unavailable")
                proposed = self._rule_proposal_agent(observation)
                if not isinstance(proposed, list) or len(proposed) != 1 or type(proposed[0]) is not int:
                    raise PolicyRuntimeError("Rule v0 proposal is not a single action")
                matched = [index for index, action in enumerate(state.legal_actions) if action.option_index == proposed[0]]
                if len(matched) != 1:
                    raise PolicyRuntimeError("Rule v0 proposal is outside legal actions")
                proposal_t[0, matched[0]] = True
                proposal_digest = state.legal_actions[matched[0]].action_key.digest
            with torch.no_grad():
                output = self.model(state_t, history_t, lengths_t, action_t, mask_t, proposal_t)
                logits = output["policy_logits"][0]
                scores = logits.tolist(); value = float(output["value"][0])
        except Exception as exc:
            raise PolicyRuntimeError("actor-critic inference failed") from exc
        if not scores or any(not math.isfinite(score) for score in scores):
            raise PolicyRuntimeError("actor-critic emitted non-finite scores")
        ppo_eligible = minimum == maximum == 1
        # A MAIN prompt with ``minCount == 0`` still requires the actor to
        # keep playing: Rule v0 and Student v0 answer it with exactly one
        # action.  It stays out of PPO because the model has no probability
        # mass for the "select nothing" alternative, so its categorical is
        # not the full behavior distribution.
        required = minimum if minimum else 1
        if self.stochastic_actions and ppo_eligible:
            probabilities = torch.softmax(logits, dim=0)
            selected = int(torch.multinomial(probabilities, 1, generator=self._sampling_rng).item())
        else:
            selected = min(range(len(scores)), key=lambda index: (-scores[index], state.legal_actions[index].action_key.digest, state.legal_actions[index].option_index))
        if ppo_eligible:
            selected_indices = [selected]
            action_mode = "single_categorical" if self.stochastic_actions else "single_argmax"
        else:
            # CABT represents a multi-select prompt as independent legal
            # options.  A deterministic top-k ranking satisfies minCount,
            # keeps every returned index legal and unique, and removes the
            # previous Rule-v0 fallback.  This is intentionally excluded from
            # PPO: the model only defines a categorical probability over one
            # option, not an action-set probability.
            selected_indices = sorted(
                range(len(scores)),
                key=lambda index: (-scores[index], state.legal_actions[index].action_key.digest,
                                   state.legal_actions[index].option_index),
            )[:required]
            action_mode = "main_optional_single" if minimum == 0 else "multi_topk_ranking"
        option_indices = [state.legal_actions[index].option_index for index in selected_indices]
        if (len(option_indices) != required or len(set(option_indices)) != len(option_indices)
                or not required <= maximum
                or any(not 0 <= option_index < len(select["option"]) for option_index in option_indices)):
            raise PolicyRuntimeError("actor-critic selected invalid legal options")
        if not math.isfinite(value):
            raise PolicyRuntimeError("actor-critic emitted non-finite value")
        behavior_log_probability = float(torch.log_softmax(logits, dim=0)[selected_indices[0]])
        if not math.isfinite(behavior_log_probability):
            raise PolicyRuntimeError("actor-critic emitted non-finite behavior log-probability")
        probabilities = torch.softmax(logits, dim=0)
        policy_confidence = float(probabilities.max())
        if not math.isfinite(policy_confidence):
            raise PolicyRuntimeError("actor-critic emitted non-finite policy confidence")
        self.last_decision_trace = {"status": "selected", "selected_count": len(option_indices), "value": value,
                                    "behavior_log_probability": behavior_log_probability,
                                    "actor_policy_version": self.actor_policy_version,
                                    "vocabulary_hash": self.vocabulary_hash,
                                    "behavior_log_probability_kind": "CATEGORICAL_SINGLE_ACTION" if ppo_eligible else "NOT_PPO_ACTION_SET",
                                    "actor_action_mode": action_mode, "ppo_eligible": ppo_eligible,
                                    "policy_confidence": policy_confidence,
                                    "rule_proposal_digests": [proposal_digest] if proposal_digest is not None else None}
        return option_indices

    def as_agent(self):
        def agent(observation: object, configuration: object = None) -> list[int]:
            del configuration
            return self.choose(observation)
        return agent


ACTION_MODES = ("argmax", "sample")


def load_runtime_policy(model_dir: Path, *, device: str, deck: list[int],
                        action_mode: str = "argmax") -> tuple[RecurrentLegalActorCriticPolicy, dict[str, Any]]:
    """Load a checkpoint under an explicitly requested action-selection mode.

    The mode is never inferred from the checkpoint schema.  Inferring it made
    a BC checkpoint act greedily while a PPO checkpoint sampled, so any
    head-to-head evaluation of the two confounded the parameter update with
    the action-selection rule.  Callers must state which mode they mean.
    """
    if action_mode not in ACTION_MODES:
        raise PolicyRuntimeError(f"action mode must be one of {ACTION_MODES}")
    model, summary, _families = load_model(model_dir, device_name=device)
    digest = hashlib.sha256((model_dir / "best.pt").read_bytes()).hexdigest()
    return RecurrentLegalActorCriticPolicy(model=model, device=device, deck=deck, actor_policy_version=digest,
                                           vocabulary_digest=str(summary.get("vocabulary_hash", "")),
                                           use_rule_proposal=bool(summary.get("config", {}).get("use_rule_proposal", False)),
                                           stochastic_actions=action_mode == "sample"), summary
