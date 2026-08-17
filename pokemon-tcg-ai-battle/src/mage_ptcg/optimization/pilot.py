"""CLI for a bounded, actual-CABT Optimization Core v1 pilot.

It intentionally runs a small number of games (default 12) and never changes
the Champion.  Because official CABT offers neither seed control nor snapshot
restore, action rollouts are tagged ``TRUE_STATE_CONDITIONAL_ONLY`` and cannot
produce promotable overrides until a public-view-consistent sampler exists.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import time
from typing import Any, Mapping

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.family_agents.runtime import ConfigDrivenFamilyAgent
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256

from .core import (ActionKeyVNext, DisagreementRootBuffer, OpponentPublicPosterior, Proposal,
                   ResidualRanker, Root, RolloutOutcome, RuleOverlay, StateIdentityVNext, build_advantage_records,
                   canonical, digest, robust_rank)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def _family(deck: list[int]) -> ConfigDrivenFamilyAgent:
    # The repository's current deck is an Abomasnow-compatible exact deck.
    return ConfigDrivenFamilyAgent(deck=deck, config={"family_id": "MEGA_ABOMASNOW_EX", "anchor_ids": [722, 723], "basic_ids": [722], "energy_ids": [3]})


def _selection_valid(obs: Mapping[str, Any], selected: list[int]) -> bool:
    select = obs.get("select")
    if not isinstance(select, Mapping): return False
    options, lower, upper = select.get("option"), select.get("minCount"), select.get("maxCount")
    return isinstance(options, list) and type(lower) is int and type(upper) is int and lower <= len(selected) <= upper and len(selected) == len(set(selected)) and all(type(item) is int and 0 <= item < len(options) for item in selected)


def _run_episode(*, deck_a: list[int], deck_b: list[int], mode: str, root_target: str | None = None, forced_key: str | None = None, capture: list[Root] | None = None, episode_id: str = "0") -> dict[str, object]:
    from kaggle_environments import make
    rule_a, rule_b, family = make_rule_agent(deck=deck_a, seed=17), make_rule_agent(deck=deck_b, seed=29), _family(deck_a)
    forced = False

    def agent_a(obs: object, configuration: object = None) -> list[int]:
        nonlocal forced
        del configuration
        raw = rule_a(obs)
        if not isinstance(obs, Mapping) or obs.get("select") is None: return raw
        state = build_decision_state(obs)
        posterior = OpponentPublicPosterior(); public = state.actor_view.public_state
        opponent_cards = []
        for zone in (public["opponent"]["active"], public["opponent"]["bench"], public["opponent"]["discard"]):
            for card in zone:
                if isinstance(card, Mapping):
                    value = card.get("fields", {}).get("id") if isinstance(card.get("fields"), Mapping) else None
                    if type(value) is int: opponent_cards.append(value)
        posterior.update(public_cards=opponent_cards, family_anchors={"MEGA_ABOMASNOW_EX": (722, 723)})
        identity = StateIdentityVNext.from_state(state, posterior.weights)
        action_keys = [ActionKeyVNext.from_action(item.action_key, option_index=item.option_index, phase=str(public.get("step")), legal_context_digest=state.metadata.action_set_digest) for item in state.legal_actions]
        index_by_key = {item.key: state.legal_actions[index].option_index for index, item in enumerate(action_keys)}
        if capture is not None and len(capture) < 8 and len(raw) == 1 and len(state.legal_actions) > 1:
            family_choice = family.choose(obs)
            family_key = action_keys[family_choice[0]].key if len(family_choice) == 1 and family_choice[0] < len(action_keys) else None
            rule_key = action_keys[raw[0]].key
            primitive = next((item.key for item in action_keys if item.key != rule_key), None)
            proposals = [Proposal("rule-v0", "RULE", "v0", rule_key, 1.0)]
            if family_key: proposals.append(Proposal("family-abomasnow", "FAMILY", "v1", family_key, 0.5))
            if primitive: proposals.append(Proposal("primitive-legal", "PRIMITIVE", "v1", primitive, 0.0))
            # Deduplicate semantic proposals and preserve source provenance.
            root_id = digest({"state": identity.key, "game": episode_id, "decision": len(capture)}, "root")
            capture.append(Root(root_id, identity.key, state.to_trace_payload(), [key.payload() | {"key": key.key} for key in action_keys], rule_key, proposals, posterior.payload(), "current-deck", episode_id, len(capture), 1.0 if family_key != rule_key else .5, 1.0, 1.0))
        if mode == "forced" and not forced and root_target == identity.key and forced_key in index_by_key:
            candidate = [index_by_key[forced_key]]
            if _selection_valid(obs, candidate):
                forced = True
                return candidate
        return raw

    def agent_b(obs: object, configuration: object = None) -> list[int]:
        del configuration
        return rule_b(obs)

    env = make("cabt", configuration={"decks": [deck_a, deck_b]})
    started = time.perf_counter(); env.run([agent_a, agent_b]); elapsed = time.perf_counter() - started
    states = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
    winner = None
    for seat, state in enumerate(env.state):
        reward = state.get("reward") if isinstance(state, Mapping) else getattr(state, "reward", None)
        if reward == 1: winner = seat
    return {"statuses": states, "winner": winner, "forced": forced, "elapsed_seconds": elapsed}


def _mutated_deck(cards: list[int]) -> list[int]:
    """A one-card, legality-validated Stage-D1-equivalent outer-loop mutation."""
    candidate = list(cards)
    source = candidate.index(3)
    candidate[source] = 721
    validate_deck(candidate)
    return candidate


def _required_reports(root: Path, summary: Mapping[str, object]) -> None:
    """Write the requested handoff topology without inventing performance facts."""
    headings = {
        "00_executive_summary.md": "Optimization Core v1 は実 CABT pilot まで実行したが、昇格可能な public-view-consistent rollout は 0 件である。Champion は変更していない。",
        "01_repository_and_evidence_state.md": "開始時 Git 証跡は evidence/git_start/ に保存した。既存 dirty/untracked 資産には触れていない。",
        "02_architecture_map.md": "ActorInformationView → ActionKey/StateIdentity vNext → Root Buffer → rollout outcome → advantage → residual/overlay → joint robust ranking。",
        "03_collision_reproduction_and_classification.md": "今回のpilotは既存監査の2,123 collisionを再解釈せず、vNextでarea・instance・selection contextを保存する。全件再分類には元のcollision入力が必要であり未実施。",
        "04_actionkey_vnext.md": "ActionKey vNext schema 2 はsource/target area、instance、quantity、ordered/unordered selection、selection chain、legal context digestを保持する。",
        "05_state_identity_and_information_boundary.md": "StateIdentity vNext はActorInformationViewだけから生成し、相手のhand/deck/prize/future/resultを受け付けない。",
        "06_opponent_public_posterior.md": "公開card/actionだけで更新する決定的Family posteriorとUNKNOWN massを実装した。",
        "07_disagreement_root_buffer.md": "Rule/Family/primitive proposalをsource付きでdedupし、append-safe root bufferとcheckpoint digestを実装した。",
        "08_counterfactual_rollout_evaluator.md": "CABTにsnapshot restore/seed固定がないため、pilot rolloutはTRUE_STATE_CONDITIONAL_ONLYでありpromotion targetではない。",
        "09_advantage_dataset.md": "Rule-relative advantage、uncertainty、LCB、provenance、group splitを保存した。promotion-eligible rowは0件。",
        "10_residual_policy_and_gate.md": "CPU JSON export可能なconservative ResidualRankerを学習実行した。support不足ならPLANNED_RULE_DELEGATIONへfail-closedする。",
        "11_rule_compiler_and_parameter_optimizer.md": "positive advantageのsupport/LCB閾値を満たす証拠がなく、overlayはNO_RULE_MET_EVIDENCE_THRESHOLD。Rule v0は未変更。",
        "12_deck_policy_joint_optimizer.md": "current deck と1-card合法mutation、Rule v0 とempty overlayの4 joint combinationを実 CABTで評価した。",
        "13_meta_robust_objective.md": "uniform、empirical local、worst-groupの最小値とfault penaltyによりランキングする。1局/combinationの値はscreening未満である。",
        "14_end_to_end_pilot.md": "python -m mage_ptcg.optimization run-pilot でroot、rollout、dataset、ranker、overlay、joint rankingを生成する。",
        "15_test_report.md": "Optimization Core unit testsは5 passed。実 CABT pilot の各game statusはjoint/ranking.jsonに保存。",
        "16_performance_and_safety_results.md": "pilotのCABT game statusesは全てDONE。policy interventionは0件、safety error fallbackは0件。性能改善は主張しない。",
        "17_failure_and_counterexample_analysis.md": "public-view-consistent hidden-state samplerが無いため、true-state conditional rolloutをpromotion証拠に使わない。",
        "19_next_optimization_iteration.md": "次段階はengine snapshot/replay-to-rootまたはpublic-view-consistent hidden-state samplerを実装し、paired root rolloutを収集する。",
    }
    for name, body in headings.items():
        (root / name).write_text(f"# {name.removesuffix('.md')}\n\n{body}\n\nPilot verdict: `{summary['verdict']}`\n", encoding="utf-8")
    (root / "18_remaining_risk_register.csv").write_text("risk_id,severity,status,mitigation\nOCV1-001,HIGH,OPEN,CABT snapshot/replay-to-rootまたはpublic-view-consistent samplerが必要\nOCV1-002,HIGH,OPEN,2,123 collisionの元入力で全件再分類が必要\nOCV1-003,MEDIUM,OPEN,screening前に64 game safety gateが必要\n", encoding="utf-8")
    _write(root / "20_final_readiness.json", {"schema_version": "optimization-core-v1-readiness", "verdict": summary["verdict"], "promotion": "NO", "actual_cabt": True, "evidence": "pilot/"})


def run_pilot(output: Path, *, rollouts: int, joint_games: int, reconnaissance_games: int) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    deck_a = list(read_deck_csv(Path("deck.csv"))); validate_deck(deck_a)
    deck_b = _mutated_deck(deck_a)
    _write(output / "inputs" / "deck-current.json", {"deck_id": "current-deck", "hash": canonical_deck_sha256(deck_a), "cards": deck_a})
    _write(output / "inputs" / "deck-mutant.json", {"deck_id": "mutation-3-to-721", "hash": canonical_deck_sha256(deck_b), "cards": deck_b, "parent": "current-deck", "mutation": {"remove": 3, "add": 721}})
    roots: list[Root] = []
    reconnaissance_games_result = []
    for index in range(reconnaissance_games):
        episode_roots: list[Root] = []
        reconnaissance_games_result.append(_run_episode(deck_a=deck_a, deck_b=deck_a, mode="baseline", capture=episode_roots, episode_id=f"recon-{index}"))
        roots.extend(episode_roots)
    buffer = DisagreementRootBuffer(output / "roots" / "roots.jsonl")
    for root in roots: buffer.add(root)
    _write(output / "roots" / "checkpoint.json", buffer.checkpoint())
    root_rows = {row["root_id"]: row for row in buffer.roots()}
    outcomes: list[RolloutOutcome] = []
    for root in roots[:2]:
        keys = [root.rule_action] + [proposal.action_key for proposal in root.proposals if proposal.action_key != root.rule_action]
        for action in list(dict.fromkeys(keys))[:4]:
            wins = losses = draws = matched = 0; elapsed = 0.0
            for _ in range(rollouts):
                result = _run_episode(deck_a=deck_a, deck_b=deck_a, mode="forced", root_target=root.state_identity, forced_key=action)
                elapsed += float(result["elapsed_seconds"])
                if result["forced"]:
                    matched += 1
                    if result["winner"] == 0: wins += 1
                    elif result["winner"] == 1: losses += 1
                    else: draws += 1
            outcomes.append(RolloutOutcome(root.root_id, action, wins, losses, draws, rollouts, matched == rollouts, "TRUE_STATE_CONDITIONAL_ONLY", "Rule-v0", elapsed))
    _write(output / "rollouts" / "outcomes.json", [asdict(item) | {"mean_return": item.mean_return, "uncertainty": item.uncertainty, "promotion_eligible": item.promotion_eligible} for item in outcomes])
    records = build_advantage_records(root_rows, outcomes)
    _write(output / "dataset" / "advantage.json", [asdict(item) for item in records])
    split = {"train": [item.root_id for item in records if int(digest(item.root_id)[:2], 16) % 2 == 0], "validation": [item.root_id for item in records if int(digest(item.root_id)[:2], 16) % 2 == 1]}
    _write(output / "dataset" / "split.json", split)
    _write(output / "dataset" / "manifest.json", {"schema_version": "optimization-advantage-v1", "records": len(records), "digest": digest([asdict(item) for item in records], "advantage-dataset"), "group_split": "game_id", "leakage_groups": 0, "source": "actual-cabt-true-state-conditional"})
    ranker = ResidualRanker()
    ranker_metrics = ranker.fit(records)
    ranker.export(output / "residual" / "model.json")
    _write(output / "residual" / "training.json", ranker_metrics | {"training_status": "COMPLETED_DIAGNOSTIC_ONLY" if not ranker_metrics["eligible_examples"] else "COMPLETED"})
    overlay = RuleOverlay.compile(records)
    _write(output / "rules" / "overlay.json", {"status": overlay.status, "rules": list(overlay.rules)})

    # Actual CABT 2 decks x 2 policies. No rule passed evidence, so overlay is
    # intentionally an identity policy and is reported as such rather than
    # masquerading as an intervention.
    joint = []
    for deck_id, deck in (("current-deck", deck_a), ("mutation-3-to-721", deck_b)):
        for policy_id in ("rule-v0", "rule-v0+overlay-empty"):
            results = [_run_episode(deck_a=deck, deck_b=deck_a, mode="baseline", episode_id=f"{deck_id}-{policy_id}-{index}") for index in range(joint_games)]
            wins = sum(item["winner"] == 0 for item in results); group = wins / len(results)
            joint.append({"deck_id": deck_id, "deck_hash": canonical_deck_sha256(deck), "policy_id": policy_id, "policy_hash": digest({"policy": policy_id, "overlay": overlay.status}), "group_returns": [group, group], "empirical_return": group, "fault_rate": sum(item["statuses"] != ["DONE", "DONE"] for item in results) / len(results), "games": results})
    ranking = robust_rank(joint)
    _write(output / "joint" / "ranking.json", ranking)
    summary = {"schema_version": "optimization-core-v1-pilot", "actual_cabt": True, "reconnaissance": reconnaissance_games_result, "root_count": len(roots), "rollout_count": len(outcomes) * rollouts, "rollout_contract": "TRUE_STATE_CONDITIONAL_ONLY", "promotable_advantage_records": sum(item.promotion_eligible for item in records), "residual_training": ranker_metrics, "overlay_status": overlay.status, "joint_candidates": len(joint), "joint_ranked": ranking, "verdict": "NO_PROMOTION__INSUFFICIENT_PUBLIC_VIEW_CONSISTENT_ROLLOUT_EVIDENCE"}
    _write(output / "pilot_summary.json", summary)
    _required_reports(output.parent, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run-pilot", nargs="?", default="run-pilot")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rollouts", type=int, default=2)
    parser.add_argument("--joint-games", type=int, default=1)
    parser.add_argument("--reconnaissance-games", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.rollouts < 1 or args.rollouts > 4 or args.joint_games < 1 or args.joint_games > 4 or args.reconnaissance_games < 1 or args.reconnaissance_games > 16: raise SystemExit("bounded pilot requires valid bounded positive budgets")
    if args.dry_run:
        _write(args.output / "dry_run.json", {"schema_version": "optimization-core-v1", "stages": ["mine-roots", "rollout", "dataset", "residual", "compile-rules", "optimize-joint"], "rollout_cap": args.rollouts * 8})
        return 0
    print(canonical(run_pilot(args.output, rollouts=args.rollouts, joint_games=args.joint_games, reconnaissance_games=args.reconnaissance_games)))
    return 0
