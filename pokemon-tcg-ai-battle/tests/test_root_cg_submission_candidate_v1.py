"""Focused contracts for the self-owned cg candidate bridge."""

from __future__ import annotations

from mage_ptcg.meta_specialist.root_cg_submission_agent_v1 import ROOT_DECK, agent
from scripts.build_root_cg_submission_candidate_v1 import CG_RUNTIME_FILES, _stage_source
from scripts.run_root_cg_candidate_arena_v1 import ArenaArm, _aggregate, _arm_subject_deck


def test_self_owned_candidate_registers_the_bound_root_deck() -> None:
    assert len(ROOT_DECK) == 60
    assert agent({"select": None}) == list(ROOT_DECK)


def test_arena_aggregate_seat_summary_is_finite() -> None:
    rows = [
        {"outcome": "win", "seat": 0},
        {"outcome": "loss", "seat": 0},
        {"outcome": "win", "seat": 1},
        {"outcome": "fault", "seat": 1},
    ]
    summary = _aggregate(rows)
    assert summary["requested_games"] == 4
    assert summary["wins"] == 2
    assert summary["faults"] == 1
    assert summary["seat"]["0"]["requested_games"] == 2
    assert summary["seat"]["1"]["faults"] == 1


def test_candidate_package_runtime_shape_is_explicit() -> None:
    assert CG_RUNTIME_FILES == ("__init__.py", "api.py", "sim.py", "utils.py", "libcg.so")


def test_variant_builder_stages_the_requested_deck(tmp_path) -> None:
    source = tmp_path / "candidate-deck.csv"
    values = list(ROOT_DECK)
    values[0], values[1] = values[0], 135
    source.write_text("\n".join(str(value) for value in values) + "\n", encoding="utf-8")
    stage = tmp_path / "package"
    _stage_source(stage, source_deck=source)
    assert (stage / "deck.csv").read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_variant_builder_stages_the_requested_policy_and_deck(tmp_path) -> None:
    """Policy-fixed deck phases must bind both source identities explicitly."""
    source = tmp_path / "candidate-deck.csv"
    source.write_text("\n".join(map(str, ROOT_DECK)) + "\n", encoding="utf-8")
    policy = tmp_path / "candidate-policy.py"
    policy.write_text("# policy interaction fixture\nagent = None\n", encoding="utf-8")
    stage = tmp_path / "package"
    _stage_source(stage, source_deck=source, source_agent=policy)
    assert (stage / "deck.csv").read_bytes() == source.read_bytes()
    assert (stage / "main.py").read_bytes() == policy.read_bytes()


def test_candidate_arm_binds_package_deck_path(tmp_path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    deck = package / "deck.csv"
    deck.write_text("\n".join(str(value) for value in ROOT_DECK) + "\n", encoding="utf-8")
    arm = ArenaArm("candidate", "candidate", "a" * 64, "root_cg", package)
    assert _arm_subject_deck(arm) == deck.resolve()
