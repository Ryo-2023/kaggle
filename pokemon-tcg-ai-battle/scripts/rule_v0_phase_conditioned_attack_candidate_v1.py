"""Materialized research-only policy source for the phase-conditioned screen."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from mage_ptcg.meta_specialist.rule_v0_phase_conditioned_overlay_v1 import (
    choose_phase_conditioned_indices,
)


_ROOT = Path(__file__).resolve().parents[1]
_ROOT_MAIN_SPEC = importlib.util.spec_from_file_location(
    "_rule_v0_phase_overlay_root_main",
    _ROOT / "main.py",
)
if _ROOT_MAIN_SPEC is None or _ROOT_MAIN_SPEC.loader is None:
    raise RuntimeError("root main.py cannot be loaded")
_ROOT_MAIN = importlib.util.module_from_spec(_ROOT_MAIN_SPEC)
_ROOT_MAIN_SPEC.loader.exec_module(_ROOT_MAIN)
_BASE_AGENT = _ROOT_MAIN.make_rule_agent(deck_path=_ROOT / "deck.csv")


def agent(obs_dict: dict) -> list[int]:
    fallback = _BASE_AGENT(obs_dict)
    return choose_phase_conditioned_indices(obs_dict, fallback)


agent.__name__ = "rule_v0_phase_conditioned_attack_candidate"
