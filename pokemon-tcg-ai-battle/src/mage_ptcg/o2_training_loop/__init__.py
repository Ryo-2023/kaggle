"""Evaluation-only O2 minimum viable training-loop sidecar."""

from .core import (
    O2ContractError, build_match_matrix, deck_content_hash, execute_match_plan,
    ingest_rule_bc_replay, load_deck_pool, load_opponent_pool, paired_evaluation, promotion_report,
)
from .cabt import cabt_backend, resolve_real_agent, resolve_real_deck

__all__ = ["O2ContractError", "build_match_matrix", "cabt_backend", "deck_content_hash", "execute_match_plan", "ingest_rule_bc_replay", "load_deck_pool", "load_opponent_pool", "paired_evaluation", "promotion_report", "resolve_real_agent", "resolve_real_deck"]
