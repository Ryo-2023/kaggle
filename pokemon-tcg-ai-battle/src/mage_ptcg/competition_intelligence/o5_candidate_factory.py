"""Fail-closed neural Candidate factory for O5 evaluation.

Two distinct failure modes are handled on purpose, not interchangeably:

* *Load-time* verification failure (missing file, corrupt export, model
  hash mismatch, feature schema hash mismatch) is a hard refusal --
  ``O5CandidateError`` is raised and no agent is built. A candidate that
  cannot be proven to be the artifact its identity claims must never
  silently become "whatever loaded."
* *Runtime* per-decision inference failure (the loaded, verified policy
  returns ``None`` from a specific ``choose()`` call) falls back to Rule
  Agent v0 deterministically, exactly like every other soft-prior agent in
  this repository, and is counted rather than hidden.

This reuses ``mage_ptcg.offline_training.neural_runtime.NeuralRuntimePolicy``
directly (the same loader ``evaluation.actual_agents.make_neural_student_agent``
uses) rather than re-parsing the export format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from agents.rule_agent import choose_rule_indices
from main import validate_deck

from mage_ptcg.offline_training.neural_runtime import NeuralRuntimeError, NeuralRuntimePolicy

from .o5_candidate_registry import CandidateArtifactIdentity


class O5CandidateError(RuntimeError):
    """Raised for a fail-closed candidate artifact load/verification failure."""


class NeuralCandidateAgent:
    """A verified ``NeuralRuntimePolicy`` wrapped with counted, decision-level fallback."""

    def __init__(self, policy: NeuralRuntimePolicy, deck: Sequence[int]) -> None:
        self._policy = policy
        self._supplied_deck = list(validate_deck(deck))
        self.inference_count = 0
        self.fallback_count = 0
        self.last_fallback_reason: str | None = None

    def __call__(self, obs_dict: dict, configuration: object = None) -> list[int]:
        # kaggle_environments' Agent.act() decides call arity by inspecting
        # ``agent.__code__.co_argcount`` and falls back to passing both
        # (observation, configuration) whenever that attribute is absent --
        # true for every plain function factory elsewhere in this repo, but
        # NOT for a class instance's bound __call__. A real cabt smoke test
        # caught this: every game raised
        # ``TypeError: NeuralCandidateAgent.__call__() takes 2 positional
        # arguments but 3 were given`` before this second parameter existed.
        if obs_dict.get("select") is None:
            return self._supplied_deck
        self.inference_count += 1
        selection = self._policy.choose(obs_dict)
        if selection is not None:
            return selection
        self.fallback_count += 1
        trace = self._policy.last_decision_trace or {}
        self.last_fallback_reason = str(trace.get("reason", "unknown"))
        return choose_rule_indices(obs_dict) or []


def build_neural_candidate(
    identity: CandidateArtifactIdentity, *, model_path: str | Path | None, deck: Sequence[int]
) -> NeuralCandidateAgent:
    """Load, hash-verify, and wrap ``identity``'s artifact from ``model_path``.

    ``model_path`` is always an explicit runtime argument -- never a
    hardcoded path in source -- so the same identity constants can be
    pointed at wherever the artifact bytes actually live.
    """
    if model_path is None:
        raise O5CandidateError(f"model_path is required to load candidate {identity.candidate_artifact_id!r}")
    try:
        policy = NeuralRuntimePolicy.load(
            model_path,
            expected_feature_hash=identity.feature_schema_hash,
            expected_model_hash=identity.model_hash,
        )
    except NeuralRuntimeError as exc:
        raise O5CandidateError(
            f"candidate {identity.candidate_artifact_id!r} failed load/verification: {exc}"
        ) from exc
    return NeuralCandidateAgent(policy, deck)


__all__ = ["NeuralCandidateAgent", "O5CandidateError", "build_neural_candidate"]
