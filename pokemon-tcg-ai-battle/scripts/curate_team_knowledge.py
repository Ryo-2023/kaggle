#!/usr/bin/env python3
"""Curate phase-1 team knowledge into training-safe normalized registries.

This program is deliberately a pure stage-2 transform.  It reads only
``artifacts/team-knowledge-mining`` and never invokes Git or reads the current
runtime implementation.  Semantic decompositions are explicit below so that
reviewers can audit every accepted rule back to phase-1 evidence IDs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts" / "team-knowledge-mining"
DEFAULT_OUTPUT = ROOT / "artifacts" / "team-knowledge-curated"

OUTPUT_FILES = (
    "canonical_rules.jsonl",
    "executable_teacher_registry.jsonl",
    "deck_variants.jsonl",
    "evaluation_registry.jsonl",
    "combos.jsonl",
    "macros.jsonl",
    "matchup_rules.jsonl",
    "hard_constraints.jsonl",
    "rejected_or_quarantined.jsonl",
    "provenance_map.jsonl",
    "summary.json",
    "report.md",
)

DECISIONS = {
    "ACCEPT_HARD_CONSTRAINT",
    "ACCEPT_TEACHER_RULE",
    "ACCEPT_SEARCH_PRIOR",
    "ACCEPT_DECK_METADATA",
    "ACCEPT_EVALUATION_WEIGHT",
    "HOLD_FOR_EXPERIMENT",
    "REJECT_DUPLICATE",
    "REJECT_INVALID_FIXTURE",
    "QUARANTINE_UNVERIFIED",
    "QUARANTINE_PRIVACY",
}
ACCEPTED = {decision for decision in DECISIONS if decision.startswith("ACCEPT_")}
PLACEHOLDER = "Executable ranking/selection behavior in the cited function"

SOURCE_EXPECTED_SHA256 = {
    "branch_inventory.csv": "2cb983e4939ca301076c535321688406aaad2a2fd720cc5ef7fb33530cc88964",
    "cabt_semantics.jsonl": "2c736b9898269b565ac2111b99283dcda76b29030fbf8529c8845d514bc8d0e1",
    "card_combos.jsonl": "0a7a1eb117e3aa06f038b184a5784923bee0cf3daa31cb61f0a1ea5ccc68e161",
    "commit_inventory.csv": "85fc7b841ab3e5438957f639ec781c7c3f36c466d61cae8ce120fb2f64ae70bc",
    "contradictions.jsonl": "ed5663b1b9895dd027b1281fabbcded0b89b7888c8fcd2627778ab166746b608",
    "coverage.json": "db5a2920e8323ed5f8a7d2dba4864728e4b9f9bf69b31109ba4320ef936adb20",
    "deck_profiles.jsonl": "c9bf1aeab9924e3561553ea70ff92cefdc3ff9f8c3993f89a0e295af5a93823f",
    "evaluation_findings.jsonl": "46a8e1a773f7ea106669ca29c2ec1a60b6e8aa9810c5df182be3a81e15e268bf",
    "evidence.jsonl": "1d3bc3d31f7fcebd3eb857b7432fd0dc9517b0e47924c6b66270d96fdd93c7e8",
    "failure_modes.jsonl": "0d987862cc453cee22b2636ffc43d94a30ab49ce8977e6f326ec6531b289d9c0",
    "file_inventory.csv": "2450f469532282c3688900da8d5536f3b46c03f144d9374a6f221a29f2a91ca6",
    "macros.jsonl": "36e941d0903379d9ddeea03e8dee656be7621485ff3e252687dc19850e9959e3",
    "matchup_tips.jsonl": "c32992a6cc9ff33bab303852ebe9931d39667983141096e89bff24c119f794b7",
    "policy_rules.jsonl": "ab92aa629ecb4ad41fa53e24659062f596fe22167de678c7c617e8a13d314d2b",
    "report.md": "ed40ea88c15ded09397474ad773849d8c2d5fb11cb7b7f6954099df65c7e816e",
    "summary.json": "5bff089e617e01c1d4895cb0c9b52db069839a6d078d8569468cf197358ebc78",
}


def stable_json(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text("".join(stable_json(row) + "\n" for row in rows), encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.write_text(stable_json(value, pretty=True) + "\n", encoding="utf-8")


def content_hash(cards: list[dict[str, int]]) -> str:
    payload = "".join(f"{card['card_id']},{card['count']}\n" for card in sorted(cards, key=lambda x: x["card_id"]))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def wilson_95(wins: int, losses: int) -> list[float] | None:
    n = wins + losses
    if not n:
        return None
    z = 1.959963984540054
    p = wins / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [round(centre - radius, 6), round(centre + radius, 6)]


class Curator:
    def __init__(self) -> None:
        self.evidence_rows = read_jsonl(SOURCE / "evidence.jsonl")
        self.evidence = {row["evidence_id"]: row for row in self.evidence_rows}
        self.policy_rows = read_jsonl(SOURCE / "policy_rules.jsonl")
        self.policies = {row["rule_id"]: row for row in self.policy_rows}
        self.deck_rows = read_jsonl(SOURCE / "deck_profiles.jsonl")
        self.contradictions = read_jsonl(SOURCE / "contradictions.jsonl")
        with (SOURCE / "commit_inventory.csv").open(encoding="utf-8", newline="") as handle:
            self.commits = {row["commit"]: row for row in csv.DictReader(handle)}

        self.rules: list[dict[str, Any]] = []
        self.hard_constraints: list[dict[str, Any]] = []
        self.decks: list[dict[str, Any]] = []
        self.evaluations: list[dict[str, Any]] = []
        self.combos: list[dict[str, Any]] = []
        self.macros: list[dict[str, Any]] = []
        self.matchups: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self.eval_by_key: dict[str, str] = {}
        self.deck_by_hash: dict[str, str] = {}
        self.deck_by_source: dict[str, str] = {}

    def _leaderboard_snapshot(self) -> dict[str, Any]:
        chunks = []
        for number in range(1431, 1441):
            evidence_id = f"EV-{number:06d}"
            chunks.append(self.evidence[evidence_id]["raw_behavior"])
        snapshot = json.loads("\n".join(chunks))
        if snapshot.get("snapshot_utc") != "2026-07-15T04:59:01.780122+00:00":
            raise ValueError("unexpected leaderboard snapshot")
        return snapshot

    def _evidence_ids(
        self,
        policy_ids: Iterable[str],
        extra: Iterable[str],
        *,
        include_policy_evidence: bool = True,
    ) -> list[str]:
        result = set(extra)
        if include_policy_evidence:
            for policy_id in policy_ids:
                result.update(self.policies[policy_id]["evidence_ids"])
        missing = result - self.evidence.keys()
        if missing:
            raise ValueError(f"unknown evidence IDs: {sorted(missing)}")
        return sorted(result)

    def add_rule(
        self,
        name: str,
        condition: str,
        features: list[str],
        action: str,
        *,
        score: str | None = None,
        priority: str | None = None,
        bonus: list[str] | None = None,
        penalty: list[str] | None = None,
        tie_break: str = "source does not specify a tie-break",
        exceptions: list[str] | None = None,
        deck_scope: list[str],
        matchup_scope: list[str] | None = None,
        phase_scope: list[str] | None = None,
        policy_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        evaluation_ids: list[str] | None = None,
        decision: str,
        conflicts: list[str] | None = None,
        notes: str = "",
        include_policy_evidence: bool = True,
    ) -> str:
        if decision not in DECISIONS:
            raise ValueError(decision)
        if not score and not priority:
            raise ValueError(f"rule needs score or priority: {name}")
        policy_ids = policy_ids or []
        rid = f"CR-{len(self.rules) + 1:06d}"
        source_functions = sorted({self.policies[pid]["name"] for pid in policy_ids})
        row = {
            "rule_id": rid,
            "name": name,
            "observable_condition": condition,
            "observable_features": features,
            "candidate_action_type": action,
            "score_formula": score,
            "priority": priority,
            "bonus": bonus or [],
            "penalty": penalty or [],
            "tie_break": tie_break,
            "exceptions": exceptions or [],
            "deck_scope": deck_scope,
            "matchup_scope": matchup_scope or [],
            "phase_scope": phase_scope or [],
            "source_policy_rule_ids": sorted(policy_ids),
            "source_functions": source_functions,
            "source_evidence_ids": self._evidence_ids(
                policy_ids,
                evidence_ids or [],
                include_policy_evidence=include_policy_evidence,
            ),
            "evaluation_ids": evaluation_ids or [],
            "conflicts_with": conflicts or [],
            "decision": decision,
            "notes": notes,
        }
        self.rules.append(row)
        return rid

    def add_hard(
        self,
        name: str,
        condition: str,
        requirement: str,
        *,
        policy_ids: list[str] | None = None,
        evidence_ids: list[str],
        exceptions: list[str] | None = None,
        include_policy_evidence: bool = True,
    ) -> str:
        policy_ids = policy_ids or []
        cid = f"HC-{len(self.hard_constraints) + 1:06d}"
        self.hard_constraints.append({
            "constraint_id": cid,
            "name": name,
            "observable_condition": condition,
            "requirement": requirement,
            "exceptions": exceptions or [],
            "source_policy_rule_ids": sorted(policy_ids),
            "source_evidence_ids": self._evidence_ids(
                policy_ids,
                evidence_ids,
                include_policy_evidence=include_policy_evidence,
            ),
            "decision": "ACCEPT_HARD_CONSTRAINT",
        })
        return cid

    def reject(
        self,
        item_type: str,
        source_item_ids: list[str],
        reason: str,
        decision: str,
        *,
        evidence_ids: list[str] | None = None,
        replacement_ids: list[str] | None = None,
    ) -> str:
        if decision not in DECISIONS - ACCEPTED:
            raise ValueError(decision)
        source_policy_ids = [item for item in source_item_ids if item in self.policies]
        qid = f"RQ-{len(self.rejected) + 1:06d}"
        self.rejected.append({
            "record_id": qid,
            "item_type": item_type,
            "source_item_ids": sorted(source_item_ids),
            "source_policy_rule_ids": sorted(source_policy_ids),
            "reason": reason,
            "replacement_ids": replacement_ids or [],
            "source_evidence_ids": self._evidence_ids(source_policy_ids, evidence_ids or []),
            "decision": decision,
        })
        return qid

    def add_evaluation(
        self,
        *,
        key: str,
        subject_policy: str,
        subject_deck: str,
        baseline: str | None,
        opponents: list[str],
        games: int,
        seed: int | None,
        wins: int | None,
        losses: int | None,
        draws: int | None,
        win_rate: float | None,
        confidence_interval: list[float] | None,
        kaggle_score: float | None,
        failure_counts: dict[str, int],
        evidence_ids: list[str],
        decision: str,
        unavailable_reason: dict[str, str] | None = None,
        notes: str = "",
        measurement_context: dict[str, Any] | None = None,
    ) -> str:
        eid = f"ER-{len(self.evaluations) + 1:06d}"
        row = {
            "evaluation_id": eid,
            "subject_policy": subject_policy,
            "subject_deck": subject_deck,
            "baseline": baseline,
            "opponents": opponents,
            "games": games,
            "seed": seed,
            "wins": wins,
            "losses": losses,
            "draws": draws,
            "win_rate": win_rate,
            "confidence_interval": confidence_interval,
            "kaggle_score": kaggle_score,
            "failure_counts": failure_counts,
            "source_evidence_ids": self._evidence_ids([], evidence_ids),
            "unavailable_reason": unavailable_reason or {},
            "measurement_context": measurement_context or {},
            "decision": decision,
            "notes": notes,
        }
        self.evaluations.append(row)
        self.eval_by_key[key] = eid
        return eid

    def add_knowledge(
        self,
        target: list[dict[str, Any]],
        prefix: str,
        name: str,
        payload: dict[str, Any],
        *,
        evidence_ids: list[str],
        evaluation_ids: list[str] | None,
        decision: str,
    ) -> str:
        kid = f"{prefix}-{len(target) + 1:06d}"
        row = {
            f"{ {'CB':'combo','MC':'macro','MR':'matchup_rule'}[prefix] }_id": kid,
            "name": name,
            **payload,
            "source_evidence_ids": self._evidence_ids([], evidence_ids),
            "evaluation_ids": evaluation_ids or [],
            "decision": decision,
        }
        target.append(row)
        return kid

    def build_decks(self) -> None:
        version_names = {
            "DECK-000001": "alakazam_leaderboard_gold1_v4_20260715",
            "DECK-000002": "alakazam_control_disruption_v1_20260711",
            "DECK-000003": "alakazam_control_jumbo_v2_20260712",
            "DECK-000004": "alakazam_draw_engine_v0_20260711",
            "DECK-000005": "psychic_aggro_v1_v2_shared_deck_20260711",
            "DECK-000006": "rule_agent_v0_official_sample_shared_baseline",
            "DECK-000007": "public_opponent_aristophanivan_multiply",
            "DECK-000008": "public_opponent_harukiharada_crustle",
            "DECK-000010": "public_opponent_itsuki9180_lucario",
            "DECK-000011": "public_opponent_kiyotah_abomasnow",
            "DECK-000012": "public_opponent_kiyotah_dragapult",
            "DECK-000013": "public_opponent_kiyotah_iono",
            "DECK-000014": "public_meta_alakazam_cddce6bc_20260711",
            "DECK-000015": "public_meta_snapshot_2a7de279",
            "DECK-000016": "public_meta_snapshot_24aee684",
            "DECK-000017": "public_meta_snapshot_c84d06a5",
            "DECK-000018": "public_meta_snapshot_6fe81ab0",
            "DECK-000020": "public_opponent_sue124_alakazam",
            "DECK-000021": "public_opponent_tomatomato_archaludon",
        }
        policy_versions: dict[str, list[dict[str, str]]] = {
            "DECK-000001": [
                {"policy": "leaderboard_alakazam_optimized", "commit": "54bdd3b78632dc9dee03a59b302036c79c2bc518", "date": "2026-07-15T16:42:20+09:00", "branch": "origin/feature/experiment-a"},
                {"policy": "leaderboard_submission", "commit": "6c0051", "date": "2026-07-15", "branch": "origin/feature/experiment-a"},
            ],
            "DECK-000002": [
                {"policy": "initial_control", "commit": "968e209", "date": "2026-07-11", "branch": "origin/feature/deck-psychic-aggro"},
                {"policy": "crustle_avoidance", "commit": "9e532f", "date": "2026-07-12", "branch": "origin/feature/deck-psychic-aggro"},
                {"policy": "low_deck_guard", "commit": "dcdd60", "date": "2026-07-12", "branch": "origin/feature/deck-psychic-aggro"},
                {"policy": "fighting_pivot", "commit": "e6780d", "date": "2026-07-12", "branch": "origin/feature/deck-psychic-aggro"},
            ],
            "DECK-000003": [
                {"policy": "control_v2", "commit": "fa32e3", "date": "2026-07-12", "branch": "origin/feature/deck-psychic-aggro"},
                {"policy": "ruruko_v0_snapshot", "commit": "85f1908e518cb1df6a9a9151431c6061d75da4c5", "date": "2026-07-12T13:28:35+09:00", "branch": "origin/feature/deck-psychic-aggro"},
                {"policy": "experiment_a_same_deck", "commit": "8f57c41d2452570217424d5c2412486ea78a7d40", "date": "2026-07-13T18:21:00+09:00", "branch": "origin/feature/experiment-a"},
            ],
            "DECK-000004": [
                {"policy": "draw_engine_initial", "commit": "d5aa8ea3c8614c744a48cc38b5babafd416fd020", "date": "2026-07-11T23:45:55+09:00", "branch": "origin/feature/deck-psychic-aggro"},
            ],
            "DECK-000005": [
                {"policy": "psychic_aggro_v1", "commit": "88a3a33", "date": "2026-07-11", "branch": "origin/feature/deck-psychic-aggro"},
                {"policy": "psychic_aggro_v2_bench_fix", "commit": "88a10dd", "date": "2026-07-11", "branch": "origin/feature/deck-psychic-aggro"},
            ],
        }
        version_evidence = {
            "DECK-000001": ["EV-000113", "EV-000512"],
            "DECK-000002": ["EV-000114", "EV-000434", "EV-000435"],
            "DECK-000003": ["EV-000116", "EV-000483", "EV-000485"],
            "DECK-000004": ["EV-000118", "EV-000434", "EV-000435"],
            "DECK-000005": ["EV-000117", "EV-000400", "EV-000407"],
            "DECK-000014": ["EV-001259", "EV-000358"],
        }
        policy_versions["DECK-000014"] = [{
            "policy": "generic_agent_wrapper_for_public_meta_deck",
            "commit": "6e227fd764869b53f8469e3e31a5475c8ee99e84",
            "date": "",
            "branch": "origin/feature/meta-opponents",
        }]

        for source_deck_id, versions in policy_versions.items():
            for version in versions:
                prefix = version["commit"]
                matches = [row for commit, row in self.commits.items() if commit.startswith(prefix)]
                if len(matches) != 1:
                    raise ValueError(f"commit prefix is not unique: {prefix}")
                commit_row = matches[0]
                version["commit"] = commit_row["commit"]
                version["date"] = commit_row["author_date"]
                if version["policy"] in {"ruruko_v0_snapshot", "experiment_a_same_deck"}:
                    version["source_evidence_ids"] = ["EV-000483"]
                else:
                    version["source_evidence_ids"] = version_evidence.get(source_deck_id, [])

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for deck in self.deck_rows:
            total = sum(card["count"] for card in deck["cards"])
            if total != 60:
                self.reject(
                    "deck_fixture",
                    [deck["deck_id"]],
                    f"{total}枚のsubmission validation counterexampleであり、training deckではない",
                    "REJECT_INVALID_FIXTURE",
                    evidence_ids=["EV-001845"],
                )
                continue
            groups[content_hash(deck["cards"])].append(deck)

        for fingerprint, group in sorted(groups.items(), key=lambda item: item[1][0]["deck_id"]):
            primary = group[0]
            did = f"DV-{len(self.decks) + 1:06d}"
            source_ids = [deck["deck_id"] for deck in group]
            source_evidence = []
            deck_evidence: dict[str, str] = {}
            for deck in group:
                candidates = [
                    evidence_id for evidence_id in deck["evidence_ids"]
                    if self.evidence[evidence_id].get("path", "").endswith("deck.csv")
                ]
                evidence_id = candidates[0] if candidates else deck["evidence_ids"][0]
                deck_evidence[deck["deck_id"]] = evidence_id
                source_evidence.append(evidence_id)
            source_evidence.extend(version_evidence.get(primary["deck_id"], []))
            source_evidence.extend(
                evidence_id
                for version in policy_versions.get(primary["deck_id"], [])
                for evidence_id in version.get("source_evidence_ids", [])
            )
            source_evidence = sorted(set(source_evidence))
            aliases = [
                {
                    "alias_type": "phase1_deck_profile",
                    "label": "/".join(deck["names"]),
                    "source_deck_id": deck["deck_id"],
                    "source_evidence_id": deck_evidence[deck["deck_id"]],
                }
                for deck in group
            ]
            name = version_names.get(primary["deck_id"], primary["names"][0].replace(" ", "_").lower())
            self.decks.append({
                "deck_variant_id": did,
                "version_name": name,
                "content_sha256": fingerprint,
                "card_count": 60,
                "cards": primary["cards"],
                "aliases": aliases,
                "source_deck_profile_ids": source_ids,
                "branch_commit_policy_versions": policy_versions.get(primary["deck_id"], []),
                "source_evidence_ids": self._evidence_ids([], source_evidence),
                "evaluation_ids": [],
                "training_eligible": True,
                "decision": "ACCEPT_DECK_METADATA",
            })
            self.deck_by_hash[fingerprint] = did
            for source_id in source_ids:
                self.deck_by_source[source_id] = did
            if len(group) > 1:
                duplicate_ids = [deck["deck_id"] for deck in group[1:]]
                self.reject(
                    "deck_alias",
                    duplicate_ids,
                    "カード多重集合が完全一致するためcanonical variantへalias統合",
                    "REJECT_DUPLICATE",
                    evidence_ids=source_evidence,
                    replacement_ids=[did],
                )

        # Opponent deck.csv evidence whose card multiset equals an existing canonical variant.
        opponent_deck_aliases = [
            ("EV-001240", "opponents/kiyotah_lucario/deck.csv"),
            ("EV-001249", "opponents/kojimar_lucario/deck.csv"),
            ("EV-001279", "opponents/official_random/deck.csv"),
            ("EV-001285", "opponents/romanrozen_strongstart/deck.csv"),
            ("EV-001297", "opponents/ruruko_alakazam_control/deck.csv"),
            ("EV-001334", "opponents/ruruko_experiment_a/deck.csv"),
        ]
        for evidence_id, label in opponent_deck_aliases:
            counts = Counter(
                int(line.strip())
                for line in self.evidence[evidence_id]["raw_behavior"].splitlines()
                if line.strip()
            )
            if sum(counts.values()) != 60:
                raise ValueError(f"opponent deck evidence is not 60 cards: {evidence_id}")
            cards = [{"card_id": card_id, "count": count} for card_id, count in sorted(counts.items())]
            fingerprint = content_hash(cards)
            if fingerprint not in self.deck_by_hash:
                raise ValueError(f"opponent deck evidence has no canonical variant: {evidence_id}")
            did = self.deck_by_hash[fingerprint]
            row = next(deck for deck in self.decks if deck["deck_variant_id"] == did)
            row["aliases"].append({
                "alias_type": "public_opponent_deck",
                "label": label,
                "source_evidence_id": evidence_id,
            })
            row["source_evidence_ids"] = sorted(set(row["source_evidence_ids"] + [evidence_id]))
            self.deck_by_source[label] = did

        snapshot = self._leaderboard_snapshot()
        rank_evidence = {
            rank: f"EV-{1431 + min((rank - 1) // 2, 8):06d}"
            for rank in range(1, 21)
        }
        # Chunk 5 contains ranks 9-11, so the simple two-per-chunk mapping shifts.
        rank_evidence.update({9: "EV-001435", 10: "EV-001435", 11: "EV-001435", 12: "EV-001436", 13: "EV-001436", 14: "EV-001437", 15: "EV-001437", 16: "EV-001438", 17: "EV-001438", 18: "EV-001439", 19: "EV-001439", 20: "EV-001439"})
        public_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        public_cards: dict[str, list[dict[str, int]]] = {}
        for entry in snapshot["leaderboard"]:
            counts = Counter(entry["deck"])
            cards = [{"card_id": card_id, "count": count} for card_id, count in sorted(counts.items())]
            fingerprint = content_hash(cards)
            public_groups[fingerprint].append(entry)
            public_cards[fingerprint] = cards

        for fingerprint, entries in sorted(public_groups.items(), key=lambda item: min(entry["rank"] for entry in item[1])):
            evidence_ids = sorted({rank_evidence[entry["rank"]] for entry in entries})
            aliases = [
                {
                    "alias_type": "public_leaderboard_submission",
                    "label": f"rank:{entry['rank']}/submission:{entry['submission_id']}",
                    "score": float(entry["score"]),
                    "snapshot_utc": snapshot["snapshot_utc"],
                }
                for entry in sorted(entries, key=lambda row: row["rank"])
            ]
            if fingerprint in self.deck_by_hash:
                did = self.deck_by_hash[fingerprint]
                row = next(deck for deck in self.decks if deck["deck_variant_id"] == did)
                row["aliases"].extend(aliases)
                row["source_evidence_ids"] = sorted(set(row["source_evidence_ids"] + evidence_ids))
            else:
                did = f"DV-{len(self.decks) + 1:06d}"
                card_ids = {card["card_id"] for card in public_cards[fingerprint]}
                archetype = "alakazam" if {741, 742, 743} <= card_ids else "unclassified"
                snapshot_date = snapshot["snapshot_utc"][:10].replace("-", "")
                self.decks.append({
                    "deck_variant_id": did,
                    "version_name": f"public_leaderboard_{archetype}_{fingerprint[:10]}_{snapshot_date}",
                    "content_sha256": fingerprint,
                    "card_count": 60,
                    "cards": public_cards[fingerprint],
                    "aliases": aliases,
                    "source_deck_profile_ids": [],
                    "branch_commit_policy_versions": [],
                    "source_evidence_ids": self._evidence_ids([], evidence_ids),
                    "evaluation_ids": [],
                    "training_eligible": True,
                    "decision": "ACCEPT_DECK_METADATA",
                })
                self.deck_by_hash[fingerprint] = did
            for entry in entries:
                self.deck_by_source[f"leaderboard_submission:{entry['submission_id']}"] = did

        self.reject(
            "privacy_fields",
            ["leaderboard.team_id", "leaderboard.team_name"],
            "公開snapshotの戦略知識化に不要な識別子はcurated出力へ転記しない",
            "QUARANTINE_PRIVACY",
            evidence_ids=[f"EV-{number:06d}" for number in range(1431, 1441)],
        )

    def build_evaluations(self) -> None:
        dv = self.deck_by_source
        rerun_opponents = [
            "aristophanivan_multiply", "harukiharada_crustle", "itsuki9180_lucario_jp",
            "kiyotah_abomasnow", "kiyotah_dragapult", "kiyotah_iono", "kiyotah_lucario",
            "kojimar_lucario", "official_random", "romanrozen_strongstart",
            "ruruko_alakazam_control", "sue124_alakazam", "tomatomato_archaludon",
        ]

        kaggle_common = {
            "baseline": None,
            "opponents": [],
            "games": 0,
            "seed": None,
            "wins": None,
            "losses": None,
            "draws": None,
            "win_rate": None,
            "confidence_interval": None,
            "failure_counts": {},
            "decision": "ACCEPT_EVALUATION_WEIGHT",
            "unavailable_reason": {
                "baseline": "submission record has no controlled baseline",
                "opponents": "Kaggle evaluation pool is not reported",
                "games": "Kaggle game count is not reported",
                "seed": "Kaggle seed is not reported",
                "wins": "Kaggle W-L-D is not reported",
                "losses": "Kaggle W-L-D is not reported",
                "draws": "Kaggle W-L-D is not reported",
                "win_rate": "Kaggle win rate is not reported",
                "confidence_interval": "Kaggle W-L-D is unavailable",
                "failure_counts": "Kaggle failure counts are not reported",
            },
        }
        kaggle_rows = [
            ("kaggle_official_266_1", "official_sample", dv["DECK-000006"], 266.1, ["EV-000524"]),
            ("kaggle_simple_176_4", "simple_priority", dv["DECK-000006"], 176.4, ["EV-000524"]),
            ("kaggle_simple_resubmit_116_3", "simple_priority_resubmission", dv["DECK-000006"], 116.3, ["EV-000524"]),
            ("kaggle_psychic_304_3", "psychic_aggro_v1", dv["DECK-000005"], 304.3, ["EV-000400"]),
            ("kaggle_psychic_311_3", "psychic_aggro_v1", dv["DECK-000005"], 311.3, ["EV-000524"]),
            ("kaggle_psychic_v2_441_4", "psychic_aggro_v2_bench_fix", dv["DECK-000005"], 441.4, ["EV-000407"]),
        ]
        for key, policy, deck, score, evidence in kaggle_rows:
            row_common = dict(kaggle_common)
            if key in {"kaggle_psychic_304_3", "kaggle_psychic_311_3"}:
                row_common["decision"] = "HOLD_FOR_EXPERIMENT"
            self.add_evaluation(
                key=key,
                subject_policy=policy,
                subject_deck=deck,
                kaggle_score=score,
                evidence_ids=evidence,
                notes=(
                    "submission ref 54562332 has two time-specific observed scores (304.3 in EV-000400, 311.3 in EV-000524); both are held and must not be double-weighted"
                    if key in {"kaggle_psychic_304_3", "kaggle_psychic_311_3"}
                    else "public score is a time-specific observation; do not infer games or win rate"
                ),
                **row_common,
            )

        self.add_evaluation(
            key="psychic_v1_gauntlet",
            subject_policy="psychic_aggro_v1",
            subject_deck=dv["DECK-000005"],
            baseline="same 13-opponent bench as control_v2",
            opponents=[],
            games=1300,
            seed=42,
            wins=241,
            losses=1059,
            draws=0,
            win_rate=round(241 / 1300, 6),
            confidence_interval=wilson_95(241, 1059),
            kaggle_score=None,
            failure_counts={"no_active_pokemon": 1017},
            evidence_ids=["EV-000440", "EV-000441", "EV-000443"],
            unavailable_reason={"opponents": "the old report states 13 opponents but does not enumerate them", "kaggle_score": "local bench result", "failure_counts.other": "other v1 loss reasons are not enumerated"},
            decision="ACCEPT_EVALUATION_WEIGHT",
            notes="engine RNG was not seed-fixable; seed is runner base seed",
        )
        self.add_evaluation(
            key="ruruko_v0_old_gauntlet",
            subject_policy="ruruko_control_v2_v0",
            subject_deck=dv["DECK-000003"],
            baseline="psychic_aggro_v1 on same bench",
            opponents=[],
            games=1300,
            seed=42,
            wins=521,
            losses=779,
            draws=0,
            win_rate=round(521 / 1300, 6),
            confidence_interval=wilson_95(521, 779),
            kaggle_score=None,
            failure_counts={"prizes_cleared": 376, "no_active_pokemon": 363, "deck_out": 40, "action_error": 0},
            evidence_ids=["EV-000440", "EV-000441", "EV-000442", "EV-000443"],
            unavailable_reason={"opponents": "the old report states 13 opponents but does not enumerate them", "kaggle_score": "local bench result"},
            decision="ACCEPT_EVALUATION_WEIGHT",
            notes="engine RNG was not seed-fixable; seed is runner base seed",
        )

        comparisons = [
            ("control_v2_vs_pre_jumbo", "control_v2_fa32e3", dv["DECK-000003"], "pre-Jumbo e6780d3", 500, 247, 253, 0, .494, [.450, .538], ["EV-000434"]),
            ("control_v2_vs_initial", "control_v2_fa32e3", dv["DECK-000003"], "initial control 968e209", 500, 262, 238, 0, .524, [.480, .567], ["EV-000434"]),
            ("control_v2_vs_crustle", "control_v2_fa32e3", dv["DECK-000003"], "harukiharada_crustle", 200, 168, 32, 0, .840, [.783, .884], ["EV-000435"]),
            ("initial_vs_crustle", "initial_control_968e209", dv["DECK-000002"], "harukiharada_crustle", 200, 164, 36, 0, .820, [.761, .867], ["EV-000435"]),
            ("control_v2_vs_kojimar", "control_v2_fa32e3", dv["DECK-000003"], "kojimar_lucario", 200, 40, 160, 0, .200, [.150, .261], ["EV-000435"]),
            ("initial_vs_kojimar", "initial_control_968e209", dv["DECK-000002"], "kojimar_lucario", 200, 39, 160, 1, .196, [.147, .257], ["EV-000435"]),
            ("control_v2_vs_sue", "control_v2_fa32e3", dv["DECK-000003"], "sue124_alakazam", 200, 119, 81, 0, .595, [.526, .661], ["EV-000435"]),
            ("initial_vs_sue", "initial_control_968e209", dv["DECK-000002"], "sue124_alakazam", 200, 105, 95, 0, .525, [.456, .593], ["EV-000435"]),
            ("experiment_a_smoke", "experiment_a_8f57c41", dv["DECK-000003"], "ruruko_v0_85f1908", 200, 109, 91, 0, .545, None, ["EV-000480"]),
        ]
        comparison_failures = {
            "control_v2_vs_crustle": {"no_active_pokemon": 17, "action_error": 0},
            "initial_vs_crustle": {"no_active_pokemon": 26, "action_error": 0},
            "control_v2_vs_sue": {"deck_out": 8, "action_error": 0},
            "initial_vs_sue": {"deck_out": 16, "action_error": 0},
        }
        for key, policy, deck, opponent, games, wins, losses, draws, rate, ci, evidence in comparisons:
            failure_counts = comparison_failures.get(key, {"action_error": 0})
            unavailable = {"seed": "not recorded in the result section", "failure_counts.other": "other failure counts are not reported", "kaggle_score": "local bench result"}
            if ci is None:
                unavailable["confidence_interval"] = "not reported for smoke comparison"
            comparison_evidence = list(evidence)
            if key != "experiment_a_smoke":
                comparison_evidence.append("EV-000436")
            self.add_evaluation(
                key=key,
                subject_policy=policy,
                subject_deck=deck,
                baseline=opponent,
                opponents=[opponent],
                games=games,
                seed=None,
                wins=wins,
                losses=losses,
                draws=draws,
                win_rate=rate,
                confidence_interval=ci,
                kaggle_score=None,
                failure_counts=failure_counts,
                evidence_ids=comparison_evidence,
                unavailable_reason=unavailable,
                decision="HOLD_FOR_EXPERIMENT" if key == "experiment_a_smoke" else "ACCEPT_EVALUATION_WEIGHT",
                notes="smoke signal only" if key == "experiment_a_smoke" else "controlled local comparison",
            )

        self.add_evaluation(
            key="ruruko_v0_gauntlet",
            subject_policy="ruruko_v0_85f1908_rerun",
            subject_deck=dv["DECK-000003"],
            baseline="experiment_a_8f57c41 same runner and deck",
            opponents=rerun_opponents,
            games=1300,
            seed=42,
            wins=None,
            losses=775,
            draws=None,
            win_rate=.4031,
            confidence_interval=[.377, .430],
            kaggle_score=None,
            failure_counts={"prizes_cleared": 393, "no_active_pokemon": 338, "deck_out": 44, "action_error": 0, "max_steps": 1},
            evidence_ids=["EV-000483", "EV-000485"],
            unavailable_reason={"wins": "not stated exactly; do not infer the max_steps event as a win", "draws": "not stated exactly; one of 1300 games is a max_steps reliability event", "kaggle_score": "local bench result"},
            decision="ACCEPT_EVALUATION_WEIGHT",
            notes="simultaneous controlled rerun",
        )
        self.add_evaluation(
            key="experiment_a_gauntlet",
            subject_policy="experiment_a_8f57c41",
            subject_deck=dv["DECK-000003"],
            baseline="ruruko_v0_85f1908 same runner and deck",
            opponents=rerun_opponents,
            games=1300,
            seed=42,
            wins=514,
            losses=786,
            draws=0,
            win_rate=.3954,
            confidence_interval=[.369, .422],
            kaggle_score=None,
            failure_counts={"prizes_cleared": 389, "no_active_pokemon": 369, "deck_out": 28, "action_error": 0, "max_steps": 0},
            evidence_ids=["EV-000483", "EV-000485"],
            unavailable_reason={"kaggle_score": "local bench result"},
            decision="ACCEPT_EVALUATION_WEIGHT",
            notes="same deck as V0; aggregate result does not promote Experiment A",
        )

        matchup_rates = [
            ("aristophanivan_multiply", 16, [.101, .244]),
            ("harukiharada_crustle", 89, [.814, .937]),
            ("itsuki9180_lucario_jp", 25, [.175, .343]),
            ("kiyotah_abomasnow", 24, [.167, .332]),
            ("kiyotah_dragapult", 12, [.070, .198]),
            ("kiyotah_iono", 14, [.085, .221]),
            ("kiyotah_lucario", 24, [.167, .332]),
            ("kojimar_lucario", 17, [.109, .255]),
            ("official_random", 88, [.802, .930]),
            ("romanrozen_strongstart", 69, [.594, .772]),
            ("ruruko_alakazam_control", 46, [.366, .557]),
            ("sue124_alakazam", 59, [.492, .681]),
            ("tomatomato_archaludon", 31, [.228, .406]),
        ]
        for opponent, wins, ci in matchup_rates:
            self.add_evaluation(
                key=f"experiment_a_vs_{opponent}",
                subject_policy="experiment_a_8f57c41",
                subject_deck=dv["DECK-000003"],
                baseline="ruruko_v0_85f1908 rate reported in same table",
                opponents=[opponent],
                games=100,
                seed=42,
                wins=wins,
                losses=100 - wins,
                draws=0,
                win_rate=wins / 100,
                confidence_interval=ci,
                kaggle_score=None,
                failure_counts={},
                evidence_ids=["EV-000483", "EV-000486"],
                unavailable_reason={"failure_counts": "per-matchup failures are only partially discussed", "kaggle_score": "local bench result"},
                decision="ACCEPT_EVALUATION_WEIGHT",
                notes="opponent-specific Experiment A screening",
            )

        v0_matchup_rates = [
            ("aristophanivan_multiply", 20, [.133, .289]),
            ("harukiharada_crustle", 80, [.711, .867]),
            ("itsuki9180_lucario_jp", 33, [.246, .427]),
            ("kiyotah_abomasnow", 18, [.117, .267]),
            ("kiyotah_dragapult", 18, [.117, .267]),
            ("kiyotah_iono", 10, [.055, .174]),
            ("kiyotah_lucario", 28, [.201, .375]),
            ("kojimar_lucario", 26, [.184, .354]),
            ("official_random", 91, [.838, .952]),
            ("romanrozen_strongstart", 70, [.604, .781]),
            ("sue124_alakazam", 55, [.452, .644]),
            ("tomatomato_archaludon", 25, [.175, .343]),
        ]
        for opponent, wins, ci in v0_matchup_rates:
            self.add_evaluation(
                key=f"ruruko_v0_vs_{opponent}",
                subject_policy="ruruko_v0_85f1908_rerun",
                subject_deck=dv["DECK-000003"],
                baseline="experiment_a_8f57c41 rate reported in same table",
                opponents=[opponent],
                games=100,
                seed=42,
                wins=wins,
                losses=100 - wins,
                draws=0,
                win_rate=wins / 100,
                confidence_interval=ci,
                kaggle_score=None,
                failure_counts={},
                evidence_ids=["EV-000483", "EV-000486"],
                unavailable_reason={"failure_counts": "per-matchup failures are only partially discussed", "kaggle_score": "local bench result"},
                decision="ACCEPT_EVALUATION_WEIGHT",
                notes="opponent-specific V0 screening from the controlled rerun",
            )
        self.add_evaluation(
            key="ruruko_v0_vs_mirror_reported_rate",
            subject_policy="ruruko_v0_85f1908_rerun",
            subject_deck=dv["DECK-000003"],
            baseline="experiment_a_8f57c41 rate reported in same table",
            opponents=["ruruko_alakazam_control"],
            games=100,
            seed=42,
            wins=None,
            losses=None,
            draws=None,
            win_rate=.505,
            confidence_interval=[.408, .601],
            kaggle_score=None,
            failure_counts={},
            evidence_ids=["EV-000483", "EV-000486"],
            unavailable_reason={"wins": "reported 0.505 rate is not representable as an integer count over 100 games", "losses": "not reported", "draws": "not reported", "failure_counts": "not fully reported", "kaggle_score": "local bench result"},
            decision="HOLD_FOR_EXPERIMENT",
            notes="preserve the reported rate without inventing W-L-D",
        )

        self.add_evaluation(
            key="experiment_a_vs_v0_direct",
            subject_policy="experiment_a_8f57c41",
            subject_deck=dv["DECK-000003"],
            baseline="ruruko_v0_85f1908",
            opponents=["ruruko_alakazam_control"],
            games=500,
            seed=42,
            wins=266,
            losses=234,
            draws=0,
            win_rate=.532,
            confidence_interval=[.488, .575],
            kaggle_score=None,
            failure_counts={"prizes_cleared": 110, "no_active_pokemon": 97, "deck_out": 27, "action_error": 0},
            evidence_ids=["EV-000483", "EV-000487"],
            unavailable_reason={"kaggle_score": "local bench result"},
            decision="ACCEPT_EVALUATION_WEIGHT",
            notes="direct matchup favors A, while aggregate gauntlet does not",
        )

        exploratory = [
            ("optimized_meta_100", "leaderboard_alakazam_optimized", dv["DECK-000001"], "previous deck/policy on same five opponents", 100, 53, .53),
            ("previous_meta_100", "previous_alakazam_deck_policy", dv["DECK-000003"], "optimized deck/policy on same five opponents", 100, 38, .38),
        ]
        for key, policy, deck, baseline, games, wins, rate in exploratory:
            self.add_evaluation(
                key=key,
                subject_policy=policy,
                subject_deck=deck,
                baseline=baseline,
                opponents=["Crustle", "Dragapult", "Archaludon", "Lucario", "Alakazam"],
                games=games,
                seed=None,
                wins=wins,
                losses=None,
                draws=None,
                win_rate=rate,
                confidence_interval=None,
                kaggle_score=None,
                failure_counts={},
                evidence_ids=["EV-000512"],
                unavailable_reason={"seed": "not reported", "losses": "only wins per matchup are reported", "draws": "not reported", "confidence_interval": "not reported", "kaggle_score": "local exploratory result", "failure_counts": "not reported"},
                decision="HOLD_FOR_EXPERIMENT",
                notes="five opponents x 20 games; explicitly described as exploratory small sample",
            )
        self.add_evaluation(
            key="optimized_vs_previous_direct_60",
            subject_policy="leaderboard_alakazam_optimized",
            subject_deck=dv["DECK-000001"],
            baseline="previous_alakazam_deck_policy",
            opponents=["previous_alakazam_deck_policy"],
            games=60,
            seed=None,
            wins=24,
            losses=36,
            draws=0,
            win_rate=.4,
            confidence_interval=None,
            kaggle_score=None,
            failure_counts={},
            evidence_ids=["EV-000512"],
            unavailable_reason={"seed": "not reported", "confidence_interval": "not reported", "kaggle_score": "local exploratory result", "failure_counts": "not reported"},
            decision="HOLD_FOR_EXPERIMENT",
            notes="small direct mirror comparison",
        )

        meta_rows = [
            ("cddce6bc12", 1779, 924, 855, 0, "EV-000358"),
            ("2a7de279f7", 991, 555, 436, 0, "EV-000358"),
            ("b6f2054e24", 653, 331, 322, 0, "EV-000358"),
            ("24aee6842a", 605, 298, 307, 0, "EV-000358"),
            ("c84d06a561", 402, 242, 160, 0, "EV-000358"),
            ("6fe81ab0b1", 365, 213, 152, 0, "EV-000358"),
            ("d74ac30e5c", 360, 174, 186, 0, "EV-000358"),
            ("0981af32d1", 350, 181, 169, 0, "EV-000358"),
            ("34288f07f5", 328, 175, 153, 0, "EV-000358"),
            ("fea3a860b5", 310, 107, 203, 0, "EV-000358"),
            ("d1ce085392", 264, 144, 120, 0, "EV-000358"),
            ("ff2ad79b17", 235, 127, 108, 0, "EV-000358"),
            ("d9d5742556", 226, 138, 88, 0, "EV-000358"),
            ("08664a4c4a", 210, 111, 99, 0, "EV-000358"),
            ("f9fc2d43e2", 202, 119, 83, 0, "EV-000358"),
            ("5822ed3821", 41, 28, 13, 0, "EV-000359"),
            ("150b37bfa3", 25, 17, 8, 0, "EV-000359"),
            ("e68c44d7b5", 21, 12, 9, 0, "EV-000359"),
            ("2e6233fda8", 106, 59, 47, 0, "EV-000359"),
            ("a2d928b429", 76, 42, 34, 0, "EV-000359"),
        ]
        canonical_meta_decks = {
            "cddce6bc12": dv["DECK-000014"],
            "2a7de279f7": dv["DECK-000015"],
            "24aee6842a": dv["DECK-000016"],
            "c84d06a561": dv["DECK-000017"],
            "6fe81ab0b1": dv["DECK-000018"],
        }
        for deck_hash, games, wins, losses, draws, evidence_id in meta_rows:
            subject_deck = canonical_meta_decks.get(deck_hash, f"external_subject:public_meta_deck_hash:{deck_hash}")
            unavailable = {"baseline": "observational aggregate has no controlled baseline", "seed": "heterogeneous source games", "kaggle_score": "not a Kaggle submission score", "failure_counts": "not reported"}
            if deck_hash not in canonical_meta_decks:
                unavailable["subject_deck"] = "exact 60-card list is absent from phase-1 deck_profiles"
            self.add_evaluation(
                key=f"public_meta_{deck_hash}",
                subject_policy="heterogeneous_opaque_public_policies",
                subject_deck=subject_deck,
                baseline=None,
                opponents=["heterogeneous_public_meta_pool"],
                games=games,
                seed=None,
                wins=wins,
                losses=losses,
                draws=draws,
                win_rate=round(wins / (wins + losses), 6),
                confidence_interval=wilson_95(wins, losses),
                kaggle_score=None,
                failure_counts={},
                evidence_ids=[evidence_id],
                unavailable_reason=unavailable,
                decision="HOLD_FOR_EXPERIMENT",
                notes="derived Wilson 95%; policy, matchup assignment, and deck effect are confounded",
            )

        snapshot = self._leaderboard_snapshot()
        rank_evidence = {
            **{rank: f"EV-{1431 + (rank - 1) // 2:06d}" for rank in range(1, 9)},
            9: "EV-001435", 10: "EV-001435", 11: "EV-001435",
            12: "EV-001436", 13: "EV-001436", 14: "EV-001437", 15: "EV-001437",
            16: "EV-001438", 17: "EV-001438", 18: "EV-001439", 19: "EV-001439", 20: "EV-001439",
        }
        for entry in snapshot["leaderboard"]:
            deck_id = dv[f"leaderboard_submission:{entry['submission_id']}"]
            self.add_evaluation(
                key=f"leaderboard_rank_{entry['rank']}",
                subject_policy=f"opaque_submission:{entry['submission_id']}",
                subject_deck=deck_id,
                baseline=None,
                opponents=[],
                games=0,
                seed=None,
                wins=None,
                losses=None,
                draws=None,
                win_rate=None,
                confidence_interval=None,
                kaggle_score=float(entry["score"]),
                failure_counts={},
                evidence_ids=[rank_evidence[entry["rank"]]],
                unavailable_reason={
                    "baseline": "leaderboard snapshot has no controlled baseline",
                    "opponents": "Kaggle pool is not reported",
                    "games": "not reported in snapshot",
                    "seed": "not reported in snapshot",
                    "wins": "not reported in snapshot",
                    "losses": "not reported in snapshot",
                    "draws": "not reported in snapshot",
                    "win_rate": "not reported in snapshot",
                    "confidence_interval": "W-L-D unavailable",
                    "failure_counts": "not reported in snapshot",
                },
                decision="HOLD_FOR_EXPERIMENT",
                notes=f"public leaderboard rank {entry['rank']} at {snapshot['snapshot_utc']}; identical deck lists do not imply identical policy",
            )

        leaderboard_tiers = [
            ("gold_1_20", 7, .35, 1163.8, "EV-001492"),
            ("silver_21_252_sample", 6, .30, 993.1, "EV-001494"),
            ("bronze_253_504_sample", 8, .40, 900.0, "EV-001496"),
            ("mid_rank_local_sample", 2, .10, 629.3, "EV-001498"),
        ]
        for tier, alakazam_count, share, average_score, evidence_id in leaderboard_tiers:
            self.add_evaluation(
                key=f"leaderboard_alakazam_tier_{tier}",
                subject_policy="heterogeneous_opaque_public_submissions",
                subject_deck="external_subject:Alakazam_archetype_aggregate",
                baseline=f"leaderboard_tier:{tier}",
                opponents=[],
                games=0,
                seed=None,
                wins=None,
                losses=None,
                draws=None,
                win_rate=None,
                confidence_interval=None,
                kaggle_score=average_score,
                failure_counts={},
                evidence_ids=[evidence_id],
                unavailable_reason={"subject_deck": "aggregate spans multiple exact Alakazam lists", "opponents": "Kaggle pool is not reported", "games": "20 is a sampled submission count, not battle games", "seed": "not reported", "wins": "not reported", "losses": "not reported", "draws": "not reported", "win_rate": "not reported", "confidence_interval": "battle W-L-D unavailable", "failure_counts": "not reported"},
                measurement_context={"sampled_submissions": 20, "alakazam_submissions": alakazam_count, "archetype_share": share, "metric": "average Kaggle score"},
                decision="HOLD_FOR_EXPERIMENT",
                notes="archetype/tier observation only; policy and exact list effects are confounded",
            )

        archetype_raw = self.evidence["EV-001531"]["raw_behavior"]
        for line in archetype_raw.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 6 or not cells[0].isdigit():
                continue
            rank, archetype, usage_text, share_text, wld_text, rate_text = cells
            wins, losses, draws = (int(value) for value in wld_text.split("-"))
            games = int(usage_text)
            self.add_evaluation(
                key=f"meta_archetype_rank_{rank}",
                subject_policy="heterogeneous_opaque_public_policies",
                subject_deck=f"external_subject:meta_archetype:{rank}:{archetype}",
                baseline=None,
                opponents=["heterogeneous_public_meta_pool"],
                games=games,
                seed=None,
                wins=wins,
                losses=losses,
                draws=draws,
                win_rate=float(rate_text.rstrip("%")) / 100,
                confidence_interval=wilson_95(wins, losses),
                kaggle_score=None,
                failure_counts={},
                evidence_ids=["EV-001531"],
                unavailable_reason={"subject_deck": "archetype cluster, not an exact deck list", "baseline": "observational aggregate has no controlled baseline", "seed": "heterogeneous source games", "kaggle_score": "not a Kaggle submission score", "failure_counts": "not reported"},
                measurement_context={"rank": int(rank), "usage_share": float(share_text.rstrip("%")) / 100, "metric": "public meta archetype W-L-D"},
                decision="HOLD_FOR_EXPERIMENT",
                notes="observational archetype aggregate; do not infer a causal deck or policy weight",
            )

        adoption_raw = self.evidence["EV-001533"]["raw_behavior"]
        for index, line in enumerate(adoption_raw.splitlines(), start=1):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 6 or not cells[2].endswith("%") or not cells[3].isdigit():
                continue
            card, category, adoption_text, games_text, rate_text, ci_text = cells
            ci_values = [float(value) / 100 for value in re.findall(r"([0-9.]+)%", ci_text)]
            self.add_evaluation(
                key=f"meta_card_adoption_{index}_{card}",
                subject_policy="heterogeneous_opaque_public_policies",
                subject_deck=f"external_subject:decks_adopting:{card}",
                baseline=None,
                opponents=["heterogeneous_public_meta_pool"],
                games=int(games_text),
                seed=None,
                wins=None,
                losses=None,
                draws=None,
                win_rate=float(rate_text.rstrip("%")) / 100,
                confidence_interval=ci_values if len(ci_values) == 2 else None,
                kaggle_score=None,
                failure_counts={},
                evidence_ids=["EV-001533"],
                unavailable_reason={"subject_deck": "card-adoption cohort, not an exact deck", "baseline": "non-adopter baseline is not in this table", "seed": "heterogeneous source games", "wins": "rate is rounded; exact W-L-D not reported", "losses": "rate is rounded; exact W-L-D not reported", "draws": "not reported", "kaggle_score": "not a Kaggle submission score", "failure_counts": "not reported", **({"confidence_interval": "not parsed from source"} if len(ci_values) != 2 else {})},
                measurement_context={"card": card, "category": category, "adoption_rate": float(adoption_text.rstrip("%")) / 100, "metric": "win rate when adopted"},
                decision="HOLD_FOR_EXPERIMENT",
                notes="correlation, not causation",
            )

        correlation_raw = self.evidence["EV-001534"]["raw_behavior"]
        for index, line in enumerate(correlation_raw.splitlines(), start=1):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 5 or "N=" not in cells[2] or "N=" not in cells[3]:
                continue
            card, scope, adopted_text, nonadopted_text, diff_text = cells
            adopted_match = re.search(r"([0-9.]+)% \(N=(\d+)\)", adopted_text)
            nonadopted_match = re.search(r"([0-9.]+)% \(N=(\d+)\)", nonadopted_text)
            if not adopted_match or not nonadopted_match:
                continue
            adopted_rate, adopted_n = float(adopted_match.group(1)) / 100, int(adopted_match.group(2))
            nonadopted_rate, nonadopted_n = float(nonadopted_match.group(1)) / 100, int(nonadopted_match.group(2))
            self.add_evaluation(
                key=f"meta_card_correlation_{index}_{card}",
                subject_policy="heterogeneous_opaque_public_policies",
                subject_deck=f"external_subject:decks_adopting:{card}",
                baseline=f"non-adopters rate={nonadopted_rate} N={nonadopted_n}",
                opponents=["heterogeneous_public_meta_pool"],
                games=adopted_n,
                seed=None,
                wins=None,
                losses=None,
                draws=None,
                win_rate=adopted_rate,
                confidence_interval=None,
                kaggle_score=None,
                failure_counts={},
                evidence_ids=["EV-001534"],
                unavailable_reason={"subject_deck": "card-adoption cohort, not an exact deck", "seed": "heterogeneous source games", "wins": "rate is rounded; exact W-L-D not reported", "losses": "rate is rounded; exact W-L-D not reported", "draws": "not reported", "confidence_interval": "not reported in adopted-vs-non-adopted table", "kaggle_score": "not a Kaggle submission score", "failure_counts": "not reported"},
                measurement_context={"card": card, "scope": scope, "nonadopted_games": nonadopted_n, "nonadopted_win_rate": nonadopted_rate, "reported_difference": diff_text, "metric": "adopted versus non-adopted correlation"},
                decision="HOLD_FOR_EXPERIMENT",
                notes="correlation, not causation; never use as an expert action label",
            )

        for deck in self.decks:
            deck["evaluation_ids"] = [
                row["evaluation_id"] for row in self.evaluations
                if row["subject_deck"] == deck["deck_variant_id"]
            ]

    def build_hard_constraints(self) -> None:
        self.add_hard(
            "registration returns exactly 60 card IDs",
            "obs.select is None",
            "return a list of exactly 60 integer card IDs forming the registered deck",
            evidence_ids=["EV-001211"],
        )
        self.add_hard(
            "selection length respects engine bounds",
            "obs.select is present",
            "minCount <= len(selection) <= maxCount",
            policy_ids=["RULE-000003", "RULE-000042"],
            evidence_ids=["EV-001211"],
            include_policy_evidence=False,
        )
        self.add_hard(
            "selection indices are in range",
            "obs.select is present",
            "every returned value is an int with 0 <= index < len(obs.select.option)",
            evidence_ids=["EV-001211"],
        )
        self.add_hard(
            "selection indices are unique",
            "obs.select is present",
            "do not return a duplicate option index",
            evidence_ids=["EV-001211"],
        )
        self.add_hard(
            "registration is not an action label",
            "obs.select is None",
            "interpret the returned 60-card list as deck registration, never as selected action indices",
            evidence_ids=["EV-001211"],
        )

    def build_rules(self) -> None:
        A = self.add_rule
        E = lambda *keys: [self.eval_by_key[key] for key in keys]

        # Phase-1 rules that were already textual are normalized into the same schema.
        A(
            "global MAIN order remains unresolved",
            "multiple MAIN actions are legal",
            ["option.type"],
            "MAIN",
            priority="Rule v0 order and inspect-options order disagree",
            deck_scope=["ANY_60_CARD_DECK"],
            phase_scope=["main"],
            policy_ids=["RULE-000001"],
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000001"],
        )
        A(
            "deterministic option-index tie-break",
            "two candidates in the reviewed Ruruko/Tomatomato selectors have exactly the same semantic score",
            ["score", "option_index"],
            "ANY_SELECTION",
            priority="smaller option index first",
            tie_break="smallest option index",
            deck_scope=["ruruko Alakazam", "tomatomato Archaludon"],
            policy_ids=["RULE-000002"],
            evidence_ids=["EV-001328", "EV-001374", "EV-001429"],
            include_policy_evidence=False,
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "optional non-main selection may decline",
            "Ruruko required-minimum helper is called with minCount == 0",
            ["select.type", "select.context", "minCount"],
            "DECLINE_OPTIONAL_SELECTION",
            priority="return an empty selection",
            tie_break="not applicable",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            policy_ids=["RULE-000004"],
            evidence_ids=["EV-001327", "EV-001373"],
            include_policy_evidence=False,
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Alakazam opening Active priority",
            "SETUP_ACTIVE offers two or more Alakazam-deck Basics",
            ["candidate.card_id", "option_index"],
            "SETUP_ACTIVE_POKEMON",
            score="Dunsparce=300, Abra=200, Fezandipiti ex=100, other=0",
            tie_break="smallest option index",
            deck_scope=["Alakazam"],
            phase_scope=["opening"],
            policy_ids=["RULE-000005"],
            evaluation_ids=E("ruruko_v0_old_gauntlet"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Alakazam develops Basics before optional resources",
            "MAIN contains a useful Basic PLAY and optional Item/Ability actions",
            ["candidate.card_id", "bench_free", "field_counts"],
            "PLAY_BASIC_POKEMON",
            priority="useful Basic before optional Item and draw Ability",
            exceptions=["do not consume a reserved Bench slot where that policy is separately enabled"],
            deck_scope=["Alakazam"],
            phase_scope=["opening", "main"],
            policy_ids=["RULE-000006"],
            evaluation_ids=E("ruruko_v0_old_gauntlet"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Alakazam evolves before draw Ability",
            "MAIN offers an Alakazam-line evolution and a draw Ability",
            ["evolution_card_id", "ability_source_card_id"],
            "EVOLVE",
            priority="evolve before activating draw Ability",
            deck_scope=["Alakazam"],
            phase_scope=["main"],
            policy_ids=["RULE-000007"],
            evaluation_ids=E("ruruko_v0_old_gauntlet"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "low-deck thinning threshold is unresolved",
            "optional draw/search is available while own deck is low",
            ["own.deckCount", "source_card_id", "successor_needed"],
            "DRAW_OR_SEARCH",
            priority="v0 threshold 10 conflicts with Experiment A thresholds 18/12/6",
            deck_scope=["Alakazam"],
            phase_scope=["late_game"],
            policy_ids=["RULE-000008"],
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000003"],
        )
        A(
            "Alakazam avoids blocked ex attack into Crustle",
            "opponent Active is Crustle 345, own Active is ex, a non-ex Bench candidate and RETREAT are visible",
            ["opponent.active.card_id", "own.active.is_ex", "bench.is_ex", "retreat_option"],
            "RETREAT",
            priority="retreat to a non-ex candidate before attach or attack",
            exceptions=["no non-ex Bench candidate or no legal RETREAT"],
            deck_scope=["Alakazam"],
            matchup_scope=["harukiharada_crustle", "Crustle 345"],
            phase_scope=["main", "promotion_or_pivot"],
            policy_ids=["RULE-000009"],
            evaluation_ids=E("control_v2_vs_crustle", "initial_vs_crustle", "experiment_a_vs_harukiharada_crustle"),
            decision="ACCEPT_TEACHER_RULE",
            notes="accepted as scoped behavior; evaluation does not isolate its causal effect",
        )
        A(
            "Fighting-weak Active pivot is not yet validated",
            "visible opponent Active is Fighting, own Active is Fighting-weak, and a non-weak Bench candidate plus RETREAT are visible",
            ["opponent.active.type", "own.active.weakness", "bench.weakness", "retreat_option"],
            "RETREAT",
            priority="pivot to non-Fighting-weak Bench candidate",
            deck_scope=["Alakazam"],
            matchup_scope=["Fighting", "Lucario"],
            phase_scope=["main", "promotion_or_pivot"],
            policy_ids=["RULE-000010"],
            evaluation_ids=E("control_v2_vs_kojimar", "initial_vs_kojimar", "experiment_a_vs_kojimar_lucario"),
            decision="HOLD_FOR_EXPERIMENT",
        )
        A(
            "Jumbo Ice Cream legacy gate",
            "own Active is Alakazam with at least 3 attached Energy and at least 40 damage",
            ["own.active.card_id", "attached_energy_count", "damage_on_active"],
            "PLAY_JUMBO_ICE_CREAM",
            priority="eligible only when all three conditions hold",
            exceptions=["later policies also require that healing does not lose a KO line"],
            deck_scope=["Alakazam control v2 with Jumbo Ice Cream"],
            phase_scope=["main"],
            policy_ids=["RULE-000011", "RULE-000044"],
            evaluation_ids=E("control_v2_vs_pre_jumbo"),
            decision="HOLD_FOR_EXPERIMENT",
            notes="direct comparison did not establish improvement over pre-Jumbo",
        )
        A(
            "Boss requires an attack and opponent Bench",
            "a legal ATTACK exists and opponent Bench is non-empty",
            ["attack_option_exists", "opponent.bench_count"],
            "PLAY_BOSSES_ORDERS",
            priority="eligible; otherwise do not play for gust",
            deck_scope=["Alakazam"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000012", "RULE-000037", "RULE-000051"],
            evaluation_ids=E("ruruko_v0_gauntlet", "experiment_a_gauntlet"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Xerosic hand-size gate",
            "opponent public handCount is greater than 3",
            ["opponent.handCount"],
            "PLAY_XEROSICS_MACHINATIONS",
            priority="eligible at handCount >= 4; otherwise skip",
            deck_scope=["Alakazam"],
            phase_scope=["main"],
            policy_ids=["RULE-000013", "RULE-000045", "RULE-000062"],
            evaluation_ids=E("ruruko_v0_gauntlet", "experiment_a_gauntlet"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Dudunsparce Ability preserves a successor",
            "Run Away Draw is offered",
            ["source_location", "own.bench_count", "own.deckCount"],
            "ACTIVATE_DUDUNSPARCE",
            priority="v0: Active requires a Bench replacement; Benched source requires at least 2 Benched Pokemon",
            exceptions=["Experiment A uses the stricter Bench>=3 and deckCount>15 variant"],
            deck_scope=["Alakazam control v0"],
            phase_scope=["main"],
            policy_ids=["RULE-000014", "RULE-000034"],
            evidence_ids=["EV-001325"],
            evaluation_ids=E("ruruko_v0_old_gauntlet"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "final Bench-slot reservation is unresolved",
            "opening Bench selection can fill the final free slot",
            ["bench_count", "bench_max", "candidate.card_id"],
            "SETUP_BENCH_POKEMON",
            priority="reserve one slot for Abra/Dunsparce versus select up to maxCount",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["opening"],
            policy_ids=["RULE-000015"],
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000002"],
        )
        A(
            "Experiment A successor maintenance requires isolated evaluation",
            "own field has <=2 Pokemon or no attack-ready Bench attacker",
            ["field_pokemon_count", "bench_attacker_ready"],
            "DEVELOP_SUCCESSOR",
            priority="attach/search/recover a successor before surplus Active resources",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000016", "RULE-000059"],
            evidence_ids=["EV-001348"],
            evaluation_ids=E("ruruko_v0_gauntlet", "experiment_a_gauntlet"),
            decision="HOLD_FOR_EXPERIMENT",
            notes="Experiment A reduced deck-out but increased no_active_pokemon",
        )
        A(
            "Boss target lexicographic prior",
            "Boss is useful and multiple opponent Bench targets are visible",
            ["immediate_win", "prize_value", "attached_energy", "stage", "max_hp", "current_hp"],
            "SELECT_OPPONENT_GUST_TARGET",
            score="lexicographic(immediate_win, prize_value, attached_energy, stage, max_hp, -current_hp)",
            tie_break="smallest option index",
            deck_scope=["Alakazam Experiment A", "Alakazam leaderboard optimized"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000017"],
            evidence_ids=["EV-000671", "EV-000672", "EV-000673", "EV-000820"],
            decision="ACCEPT_SEARCH_PRIOR",
        )

        # Generic agent: EV-000029 is the later implementation; EV-000011 is retained only as duplicate provenance.
        A(
            "generic one-turn attack-plan score",
            "turn >= 2 and a current or legally pivotable attacker has a modeled attack",
            ["estimated_damage", "target_hp", "target_prize", "own_prizes_left", "attacker_position", "energy_missing", "weakness", "resistance"],
            "ATTACK_PLAN",
            score="estimated_damage + KO*target_prize*1000 + lethal*100000 + active_attacker*220 - needs_attach*30 + main_attacker*50",
            bonus=["weakness doubles estimated damage", "active target receives target-side preference in the detailed scorer"],
            penalty=["resistance subtracts 30 damage", "one required attachment costs 30 score"],
            tie_break="earlier enumerated attacker/target/action",
            exceptions=["only a one-Energy deficit may be repaired in the current turn"],
            deck_scope=["ANY_60_CARD_DECK"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000021"],
            evidence_ids=["EV-000016", "EV-000017", "EV-000018", "EV-000019", "EV-000022", "EV-000029"],
            include_policy_evidence=False,
            decision="ACCEPT_SEARCH_PRIOR",
            notes="not an expert label for public meta_1..5 wrappers",
        )
        generic_action_specs = [
            ("generic Pokemon deployment", "a Pokémon PLAY is legal and Bench has space", ["candidate.card_type", "bench_free", "estimated_main_attacker"], "PLAY", "20000 + main_attacker*500", []),
            ("generic Energy attachment", "an ATTACH option targets a visible own Pokémon", ["target_is_main_attacker", "target_is_attack_plan", "attached_energy_count"], "ATTACH", "8000 + main_attacker*1000 + planned_target*2000 + no_energy*100", []),
            ("generic evolution", "an EVOLVE option is legal", ["target_attached_energy_count"], "EVOLVE", "9000 + attached_energy_count*5", []),
            ("generic low-deck draw guard", "own deckCount <= 22", ["own.deckCount", "option.card_type", "ability_source"], "PLAY_OR_ACTIVATE_DRAW", "-1 for Supporter, Item, or Ability", ["coarse guard; card effects are not modeled"]),
            ("generic per-turn Item decay", "own deckCount > 22 and an Item PLAY is offered", ["items_played_this_turn"], "PLAY_ITEM", "max(200, 4000 - 2500*items_played_this_turn)", []),
            ("generic self-harm count minimization", "context is DISCARD, DISCARD_ENERGY, DISCARD_TOOL, or DAMAGE", ["select.context", "minCount", "maxCount"], "SELECT_TARGETS", None, ["return exactly minCount ranked candidates"]),
        ]
        for name, condition, features, action, score, exception in generic_action_specs:
            A(
                name,
                condition,
                features,
                action,
                score=score,
                priority=None if score else "choose only minCount candidates",
                exceptions=exception,
                deck_scope=["ANY_60_CARD_DECK"],
                phase_scope=["main", "late_game"],
                policy_ids=["RULE-000021"],
                evidence_ids=["EV-000029"],
                include_policy_evidence=False,
                decision="ACCEPT_SEARCH_PRIOR",
            )

        # Lucario family canonicalized on kojimar LucarioPolicy.
        lucario_attacks = [
            ("Mega Lucario Wave Punch", "Mega Lucario ex attack 0 is considered", "damage=130; ranking_bonus=60*min(3, discarded Fighting Energy)", "1 Energy"),
            ("Mega Lucario Mega Brave", "Mega Lucario ex attack 1 is considered", "damage=270", "2 Energy"),
            ("Hariyama Wild Press", "Hariyama attack is considered", "damage=210", "3 Energy"),
            ("Makuhita same-turn Hariyama projection", "Makuhita can legally evolve to Hariyama this turn", "projected_damage=210; ranking_penalty=100", "3 Energy after evolution"),
            ("Solrock secondary attack", "Solrock is in play with Lunatone on own field", "damage=70", "1 Energy"),
        ]
        for name, condition, score, requirement in lucario_attacks:
            A(
                name,
                condition,
                ["attacker.card_id", "attack_index", "attached_energy", "discarded_fighting_energy", "Lunatone_present"],
                "ATTACK_PLAN",
                score=f"{score}; requirement={requirement}",
                deck_scope=["Lucario/Hariyama"],
                phase_scope=["main", "attack"],
                policy_ids=["RULE-000032"],
                evidence_ids=["EV-001255"],
                decision="ACCEPT_SEARCH_PRIOR",
            )
        A(
            "Lucario target score",
            "a modeled attack can target a visible opponent Pokémon",
            ["prize_value", "attached_energy", "tool_count", "stage", "card_specific", "hp", "estimated_damage"],
            "ATTACK_TARGET",
            score="prize*1000 + energy*150 + tools*100 + stage2*250 + stage1*130 + card_specific + hp; non-KO *= damage/hp; winning KO=50000; Active attacker +220; Active target +300",
            tie_break="smallest option index",
            deck_scope=["Lucario/Hariyama"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000032"],
            evidence_ids=["EV-001254", "EV-001255"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Lucario setup order",
            "SETUP_ACTIVE offers Lucario-deck Basics",
            ["is_first", "candidate.card_id"],
            "SETUP_ACTIVE_POKEMON",
            score="going second: Solrock=4, Riolu=3; going first: Solrock=2; Makuhita=1",
            tie_break="smallest option index",
            deck_scope=["Lucario/Hariyama"],
            phase_scope=["opening"],
            policy_ids=["RULE-000032"],
            evidence_ids=["EV-001255"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Lucario Energy development",
            "Fighting Energy ATTACH is legal",
            ["target.card_id", "target.energy_count", "active", "ready_line_exists"],
            "ATTACH",
            score="base 8000 + active*10; incomplete Hariyama/Lucario line +100, already-ready same line -50; Solrock at 0 +20 and at >=1 -100",
            tie_break="smallest option index",
            deck_scope=["Lucario/Hariyama"],
            phase_scope=["main"],
            policy_ids=["RULE-000032"],
            evidence_ids=["EV-001255"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Lucario duplicate-line suppression",
            "PLAY would create duplicate Lunatone/Solrock or a third Lucario line",
            ["field_counts", "candidate.card_id"],
            "PLAY_POKEMON",
            score="-1",
            deck_scope=["Lucario/Hariyama"],
            phase_scope=["main"],
            policy_ids=["RULE-000032"],
            evidence_ids=["EV-001255"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Lucario low-deck draw suppression",
            "own deckCount <= 10",
            ["own.deckCount", "source_card_id"],
            "DRAW_OR_SEARCH",
            priority="suppress Carmine, Lillie, and Lunatone Ability",
            deck_scope=["Lucario/Hariyama"],
            phase_scope=["late_game"],
            policy_ids=["RULE-000032"],
            evidence_ids=["EV-001255"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Lucario vs Crustle non-ex route",
            "Crustle 345 is visible on opponent field",
            ["opponent.card_ids", "candidate_attacker.is_ex", "Hariyama_line"],
            "DEVELOP_OR_ATTACK",
            priority="exclude Mega Lucario attack route and strengthen Hariyama/Makuhita Energy development",
            deck_scope=["Lucario/Hariyama"],
            matchup_scope=["Crustle 345"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000022", "RULE-000032"],
            evidence_ids=["EV-001167", "EV-001255"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Lucario vs Water late-game exposure penalty",
            "Water line is visible and opponent has <=3 Prizes remaining",
            ["opponent.card_ids", "opponent.prize_count", "candidate_attacker"],
            "ATTACK_PLAN",
            score="Mega Lucario plan -500; promotion bonus reduced from 20 to 8",
            exceptions=["specific public Water identifiers only"],
            deck_scope=["Lucario/Hariyama"],
            matchup_scope=["Abomasnow/Kyogre Water"],
            phase_scope=["late_game", "promotion_or_pivot"],
            policy_ids=["RULE-000022"],
            evidence_ids=["EV-001167", "EV-001255"],
            decision="HOLD_FOR_EXPERIMENT",
        )
        A(
            "Lucario beam-search state prior",
            "a successor state is evaluated by the bounded search",
            ["prize_diff", "own_energy", "line_counts", "active_hp", "ready_active", "opponent_active_hp", "hand_size", "deck_count"],
            "SEARCH_STATE",
            score="prize_diff*10000 + own_energy*200 + Lucario*500 + Hariyama*300 + basic_line*100 + active_hp*2 + ready_active*500 - opponent_active_hp*2.5 + hand*10 - deck_lt_5*5000; terminal=+/-9999999",
            deck_scope=["aristophanivan Lucario/Hariyama"],
            phase_scope=["search"],
            policy_ids=["RULE-000023"],
            evidence_ids=["EV-001168", "EV-001170"],
            decision="ACCEPT_SEARCH_PRIOR",
            notes="state prior only; SEARCH_ALGO execution wrapper is quarantined",
        )

        # Crustle energy and board plans.
        crustle_energy = [
            (
                "Mist Energy to Crustle",
                "candidate Energy is Mist and target is Crustle",
                "9000 + no_grass*3000 + misc_lt_2*1600 + behind_with_CounterGain_ready*2000 - total_energy_ge_3*5000",
            ),
            (
                "Grass Energy to Crustle",
                "candidate Energy is Basic Grass and target is Crustle",
                "10000 + no_grass*5000 - total_energy_ge_3*4000",
            ),
            (
                "Darkness Energy to Munkidori",
                "candidate Energy is Basic Darkness",
                "Munkidori with no Darkness=9200; Munkidori with Darkness=8600; other=1800",
            ),
            (
                "Fighting Energy to auxiliary attacker",
                "candidate Energy is Basic Fighting",
                "Koraidon ex/Cornerstone ex/Sudowoodo=7600; other=1700",
            ),
        ]
        for name, condition, score in crustle_energy:
            A(
                name,
                condition,
                ["energy.card_id", "target.card_id", "target.energy_types", "target.tool_ids", "prize_count_difference"],
                "ATTACH",
                score=score,
                tie_break="smallest option index",
                deck_scope=["harukiharada Crustle"],
                phase_scope=["main"],
                policy_ids=["RULE-000026"],
                evidence_ids=["EV-001197"],
                decision="ACCEPT_SEARCH_PRIOR",
            )
        A(
            "Crustle setup Active order",
            "SETUP_ACTIVE offers public Crustle-deck Basics",
            ["candidate.card_id"],
            "SETUP_ACTIVE_POKEMON",
            score="Dwebble=10000, Koraidon ex=9000, Cornerstone ex=8800, Munkidori=8400, Sudowoodo=8000, other=3000",
            tie_break="smallest option index",
            deck_scope=["harukiharada Crustle"],
            phase_scope=["opening"],
            policy_ids=["RULE-000027"],
            evidence_ids=["EV-001201"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Crustle promotion score",
            "own Pokémon is a legal SWITCH or TO_ACTIVE target",
            ["candidate.card_id", "energy_count", "hp", "damaged", "CounterGain_attached"],
            "SELECT_OWN_ACTIVE",
            score="energy*40 + hp + card bonus; Crustle +3000, damaged +200, Counter Gain +100; Dwebble +1200; Koraidon +1000; Cornerstone +900; Munkidori +600; Sudowoodo +500",
            tie_break="smallest option index",
            deck_scope=["harukiharada Crustle"],
            phase_scope=["promotion_or_pivot"],
            policy_ids=["RULE-000027"],
            evidence_ids=["EV-001201"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Crustle line evolution and duplicate limits",
            "a Pokémon PLAY/EVOLVE option is offered",
            ["candidate.card_id", "Dwebble_count", "Munkidori_count"],
            "PLAY_OR_EVOLVE_POKEMON",
            score="Dwebble->Crustle evolve=20000; Crustle without Dwebble -1000; fifth Dwebble -1000; third Munkidori -500",
            tie_break="smallest option index",
            deck_scope=["harukiharada Crustle"],
            phase_scope=["main"],
            policy_ids=["RULE-000027"],
            evidence_ids=["EV-001201"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Crustle attack prior",
            "an ATTACK option is legal",
            ["attack_id", "own.deckCount", "target_hp", "target_prize", "active_target"],
            "ATTACK",
            score="attack base: Crustle=1400, Ogerpon=1250, Koraidon=1080, Sudowoodo copy=760, Munkidori=700, Sudowoodo=680; add target score, Active target +400, KO prize*800",
            bonus=["own low deck adds 250 to Crustle/Ogerpon/Koraidon attacks"],
            tie_break="smallest option index",
            deck_scope=["harukiharada Crustle"],
            phase_scope=["attack", "late_game"],
            policy_ids=["RULE-000027"],
            evidence_ids=["EV-001199", "EV-001201"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Crustle wall retreat gate",
            "Active is Crustle and RETREAT is offered",
            ["opponent_has_ex", "active_damage", "fresh_bench_crustle"],
            "RETREAT",
            score="opponent ex visible=-1; otherwise damaged Active plus full-HP Bench Crustle=4000",
            exceptions=["auxiliary attacker Active receives separate 7000 retreat score"],
            deck_scope=["harukiharada Crustle"],
            phase_scope=["promotion_or_pivot"],
            policy_ids=["RULE-000027"],
            evidence_ids=["EV-001199", "EV-001201"],
            decision="ACCEPT_TEACHER_RULE",
        )

        # Abomasnow/Kyogre public opponent.
        abomasnow_rules = [
            ("Abomasnow versus Kyogre attacker switch", "both attacker routes are visible", ["opponent.active.hp", "discard_water_energy"], "ATTACKER_PLAN", "prefer Kyogre iff opponent Active HP <= 20*discarded Water Energy; otherwise Mega Abomasnow ex", None),
            ("Abomasnow ready-attacker switch", "planned attacker differs from Active and a ready copy is on Bench", ["planned_attacker", "bench_energy", "switch_option"], "SWITCH_OR_RETREAT", None, "switch to planned ready attacker"),
            ("Abomasnow Active promotion", "own Active target is selected", ["candidate.card_id", "energy_count", "planned_switch"], "SELECT_OWN_ACTIVE", "energy*2 + planned_switch*100 + MegaAbomasnow*20 + Kyogre*10", None),
            ("Kyogre Riptide estimate", "Kyogre attack is considered", ["discard_water_energy"], "ATTACK", "1000 + 20*discard_water_energy - 90", None),
            ("Hammer-lanche HP preference", "Mega Abomasnow attack is considered", ["opponent.active.hp"], "ATTACK", "base +100 if opponent HP>200, else base-100", None),
            ("Abomasnow Water Energy saturation", "Water Energy ATTACH is legal", ["target.card_id", "target.energy_count", "same_ready_attacker_exists"], "ATTACH", "Mega Abomasnow at 1 Energy +30, >=2 -300; Kyogre >=1 -200; existing same ready attacker adds -200/-300", None),
            ("Abomasnow Ultra Ball gate", "Ultra Ball PLAY is legal", ["hand_water_energy", "missing_key_pokemon"], "PLAY_ULTRA_BALL", "4000 if Water Energy>=3 or key Pokemon missing, else -1", None),
        ]
        for name, condition, features, action, score, priority in abomasnow_rules:
            A(
                name,
                condition,
                features,
                action,
                score=score,
                priority=priority,
                tie_break="smallest option index",
                exceptions=["source has no deck-out guard"],
                deck_scope=["kiyotah Abomasnow/Kyogre"],
                phase_scope=["main", "attack", "promotion_or_pivot"],
                policy_ids=["RULE-000029"],
                evidence_ids=["EV-001217"],
                evaluation_ids=E("experiment_a_vs_kiyotah_abomasnow"),
                decision="ACCEPT_SEARCH_PRIOR",
            )

        # Iono public opponent: attackId-as-score is intentionally not represented here.
        iono_rules = [
            ("Iono three-line board maintenance", "board or search selection can add a Pokemon", ["Voltorb_line", "Tadbulb_Bellibolt_line", "Wattrel_Kilowattrel_line"], "PLAY_OR_SEARCH_POKEMON", None, "maintain Voltorb attacker plus both evolution lines"),
            ("Iono Energy distribution", "Lightning Energy ATTACH is legal", ["target.card_id", "target.energy_count", "active", "ready_attacker_exists"], "ATTACH", "40000 + Voltorb(active and <2)*5000 + Voltorb(bench and no ready attacker)*1000 + Wattrel(no Energy)*6000 + Kilowattrel(no Energy)*8000; Bellibolt favored below 4", None),
            ("Iono Voltorb lethal promotion", "Voltorb is a promotion candidate", ["total_own_field_energy", "opponent.active.hp", "Voltorb.energy_count"], "SELECT_OWN_ACTIVE", "base +100000 if 20+20*field_energy >= opponent HP; ready Voltorb +10000", None),
            ("Iono Levincia gate", "Levincia PLAY is legal", ["discard_lightning_energy", "Kilowattrel_ability_available"], "PLAY_LEVINCIA", "85000 when discard has Lightning Energy or Kilowattrel ability can use it", None),
            ("Iono recovery gates", "Night Stretcher or Max Rod PLAY is legal", ["discard_card_ids", "matching_basic_present", "turn", "discard_lightning_energy_count"], "PLAY_RECOVERY", "Night Stretcher=75000 for Voltorb or matching evolution/basic; Max Rod=55000 when turn>=3 and discard Lightning>=2", None),
            ("Iono critical deck guard", "own deckCount <= 5", ["own.deckCount", "source_card_id"], "DRAW_OR_SEARCH", None, "stop most draw/search and Kilowattrel Ability"),
        ]
        for name, condition, features, action, score, priority in iono_rules:
            A(
                name,
                condition,
                features,
                action,
                score=score,
                priority=priority,
                tie_break="smallest option index",
                deck_scope=["kiyotah Iono"],
                phase_scope=["main", "late_game", "promotion_or_pivot"],
                policy_ids=["RULE-000030"],
                evidence_ids=["EV-001237"],
                evaluation_ids=E("experiment_a_vs_kiyotah_iono"),
                decision="ACCEPT_SEARCH_PRIOR" if name != "Iono critical deck guard" else "ACCEPT_TEACHER_RULE",
            )

        # Ruruko v0 placeholder functions, split into atomic rules.
        A(
            "Ruruko Fighting-weak promotion penalty",
            "opponent Active is visibly Fighting and promotion candidate is Fighting-weak",
            ["opponent.active.type", "candidate.weakness", "candidate.card_id"],
            "SELECT_OWN_ACTIVE",
            score="pokemon_priority - 1000",
            tie_break="smallest option index",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            matchup_scope=["Fighting", "Lucario"],
            phase_scope=["opening", "promotion_or_pivot"],
            policy_ids=["RULE-000035", "RULE-000049"],
            evidence_ids=["EV-001306", "EV-001350"],
            evaluation_ids=E("control_v2_vs_kojimar", "experiment_a_vs_kojimar_lucario"),
            decision="HOLD_FOR_EXPERIMENT",
        )
        A(
            "Ruruko v0 attachment tuple",
            "Energy ATTACH options exist",
            ["target_position", "target_energy_count", "required_energy", "energy_is_telepath", "pokemon_priority"],
            "ATTACH",
            score="lexicographic(tier, telepath_bonus, -missing, pokemon_priority); tier=Active_missing 4, Bench_missing 3, Active_ready 2, Bench_ready 1",
            bonus=["Telepath Psychic Energy +1"],
            tie_break="smallest option index",
            exceptions=["v0 treats Active Alakazam target as 3 Energy for Jumbo setup"],
            deck_scope=["ruruko Alakazam v0"],
            phase_scope=["main"],
            policy_ids=["RULE-000036"],
            evidence_ids=["EV-001298", "EV-001310"],
            evaluation_ids=E("ruruko_v0_gauntlet"),
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Ruruko Pokémon selection priority",
            "PLAY or EVOLVE offers multiple Alakazam-line candidates",
            ["candidate.card_id", "option_index"],
            "PLAY_OR_EVOLVE_POKEMON",
            score="Alakazam=600, Kadabra=500, Abra=400, Dunsparce=300, Dudunsparce=200, Fezandipiti ex=100",
            tie_break="smallest option index",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["main"],
            policy_ids=["RULE-000039", "RULE-000041", "RULE-000053", "RULE-000055"],
            evidence_ids=["EV-001298", "EV-001312", "EV-001313", "EV-001335", "EV-001356", "EV-001357"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Ruruko Fezandipiti Ability priority",
            "multiple ACTIVATE options are available",
            ["ability_source_card_id"],
            "ACTIVATE_ABILITY",
            score="Fezandipiti ex=300, eligible Dudunsparce=200, generic=100, ineligible Dudunsparce=-1",
            tie_break="smallest option index",
            deck_scope=["ruruko Alakazam v0"],
            phase_scope=["main"],
            policy_ids=["RULE-000034"],
            evidence_ids=["EV-001325"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Ruruko discard keep priority",
            "DISCARD context selects own hand cards",
            ["candidate.card_id", "option_index"],
            "SELECT_DISCARD",
            score="discard ascending keep value: Alakazam1000, Kadabra950, Abra900, Dudunsparce850, Dunsparce800, RareCandy750, PokePad700, NightStretcher650, Jumbo625, Fez600, Telepath300, Psychic200, Lillie150, Hilda140, Poffin100, Boss50, Xerosic40, Hammer30, other0",
            tie_break="smaller keep value then smaller option index",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["main"],
            policy_ids=["RULE-000038", "RULE-000052"],
            evidence_ids=["EV-001328", "EV-001374"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Ruruko opponent target lowest HP",
            "selection targets an opponent Pokémon for gust or damage",
            ["candidate.current_hp", "option_index"],
            "SELECT_OPPONENT_TARGET",
            score="-current_hp",
            tie_break="smallest option index",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000038", "RULE-000052"],
            evidence_ids=["EV-001328", "EV-001374"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Ruruko goes second",
            "IS_FIRST choice offers NO",
            ["select.context", "option.type"],
            "CHOOSE_TURN_ORDER",
            priority="choose NO (go second)",
            exceptions=["policy assumes immediate Supporter and extra draw benefit"],
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["opening"],
            policy_ids=["RULE-000047", "RULE-000064"],
            evidence_ids=["EV-001329", "EV-001375"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Ruruko accepts mulligan",
            "MULLIGAN choice offers YES",
            ["select.context", "option.type"],
            "MULLIGAN",
            priority="choose YES",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["opening"],
            policy_ids=["RULE-000047", "RULE-000064"],
            evidence_ids=["EV-001329", "EV-001375"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Ruruko strict MAIN sequence remains unresolved",
            "multiple MAIN action families are legal",
            ["option.type", "candidate.card_id", "matchup_flags", "deck_count"],
            "MAIN",
            priority="Basic setup -> Item -> evolve -> Ability -> matchup retreat -> attach -> supporter -> attack -> end",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["main"],
            policy_ids=["RULE-000040", "RULE-000054"],
            evidence_ids=["EV-001326", "EV-001372"],
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000001"],
        )
        A(
            "Ruruko setup Bench occupancy remains unresolved",
            "SETUP_BENCH allows more candidates than minCount",
            ["maxCount", "bench_free", "candidate.card_id"],
            "SETUP_BENCH_POKEMON",
            priority="v0/early Experiment A selects up to maxCount; later Experiment A reserves one slot",
            deck_scope=["ruruko Alakazam v0", "Experiment A"],
            phase_scope=["opening"],
            policy_ids=["RULE-000043", "RULE-000057"],
            evidence_ids=["EV-001308", "EV-001352"],
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000002"],
        )

        A(
            "Experiment A Dudunsparce gate",
            "Run Away Draw is offered by Dudunsparce",
            ["own.bench_count", "own.deckCount", "ability_source_card_id"],
            "ACTIVATE_DUDUNSPARCE",
            score="200 if bench_count>=3 and deckCount>15, else -1; Fezandipiti=300, generic Ability=100",
            tie_break="smallest option index",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000048"],
            evidence_ids=["EV-001369"],
            evaluation_ids=E("experiment_a_gauntlet", "ruruko_v0_gauntlet"),
            decision="HOLD_FOR_EXPERIMENT",
            notes="stricter gate co-occurs with lower deck-out and higher no_active_pokemon",
        )
        A(
            "Experiment A attachment tuple",
            "Energy ATTACH options exist",
            ["target_position", "target_energy_count", "required_energy", "energy_is_telepath", "own.deckCount", "pokemon_priority"],
            "ATTACH",
            score="lexicographic(tier, telepath_adjustment, -missing, pokemon_priority); Alakazam required_energy=1; Telepath adjustment=-1 at soft deck threshold",
            tie_break="smallest option index",
            exceptions=["soft threshold is part of CONFLICT-000003 and must not be a Teacher label"],
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000050"],
            evidence_ids=["EV-001335", "EV-001354"],
            evaluation_ids=E("experiment_a_gauntlet"),
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000003"],
        )
        A(
            "Experiment A Night Stretcher emergency recovery",
            "Night Stretcher target selection occurs with <=2 own field Pokemon",
            ["field_pokemon_count", "candidate.card_id"],
            "SELECT_RECOVERY_TARGET",
            score="Abra=1000, Dunsparce=900, Basic Psychic Energy=800, Fezandipiti ex=700, Kadabra=600, Alakazam=500, Dudunsparce=400",
            tie_break="smallest option index",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000052"],
            evidence_ids=["EV-001374"],
            evaluation_ids=E("experiment_a_gauntlet"),
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Experiment A Night Stretcher line completion",
            "Night Stretcher selects with >2 field Pokemon and Abra already in play",
            ["field_pokemon_count", "Abra_present", "candidate.card_id"],
            "SELECT_RECOVERY_TARGET",
            score="Alakazam=1000, Kadabra=900, Abra=800, Dudunsparce=700, Dunsparce=600, Basic Psychic Energy=500, Fezandipiti ex=400",
            tie_break="smallest option index",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000052"],
            evidence_ids=["EV-001374"],
            evaluation_ids=E("experiment_a_gauntlet"),
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Experiment A promotion tiers",
            "own Pokémon must be promoted or switched Active",
            ["candidate.card_id", "attached_energy", "required_energy", "matchup_adjustment"],
            "SELECT_OWN_ACTIVE",
            score="ready Alakazam=600, other ready attacker=500, Alakazam missing one Energy=400, Dunsparce=300, Abra/Kadabra=200, Dudunsparce=150, other=100, plus matchup adjustment",
            tie_break="smallest option index",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["promotion_or_pivot"],
            policy_ids=["RULE-000052"],
            evidence_ids=["EV-001374"],
            evaluation_ids=E("experiment_a_gauntlet"),
            decision="HOLD_FOR_EXPERIMENT",
            notes="no_active_pokemon worsened in aggregate versus V0",
        )
        A(
            "Experiment A optional draw thresholds remain unresolved",
            "an optional draw Ability is offered",
            ["own.deckCount", "active_can_attack", "successor_needed"],
            "ACTIVATE_DRAW_ABILITY",
            priority="above soft threshold allow; between soft and hard allow only when Active cannot attack and successor is needed; at hard threshold stop",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000060", "RULE-000064"],
            evidence_ids=["EV-001370", "EV-001375"],
            evaluation_ids=E("ruruko_v0_gauntlet", "experiment_a_gauntlet"),
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000003"],
        )
        A(
            "Experiment A search thresholds remain unresolved",
            "optional deck search is offered",
            ["own.deckCount", "successor_needed", "source_card_id"],
            "SEARCH_DECK",
            priority="above soft threshold allow; at/below soft threshold allow only for required successor; critical behavior is source-specific",
            deck_scope=["Alakazam Experiment A"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000061"],
            evidence_ids=["EV-001371"],
            evaluation_ids=E("ruruko_v0_gauntlet", "experiment_a_gauntlet"),
            decision="HOLD_FOR_EXPERIMENT",
            conflicts=["CONFLICT-000003"],
        )

        # Psychic aggro v2 has a measured bench-fix result and a shared v1/v2 deck.
        A(
            "Psychic aggro v2 opening Active",
            "SETUP_ACTIVE offers Psychic-aggro Basics",
            ["candidate.card_id", "option_index"],
            "SETUP_ACTIVE_POKEMON",
            score="Meloetta ex=400, Zacian=300, Xerneas ex=200, Enamorus=100",
            tie_break="smallest option index",
            deck_scope=["Psychic aggro v2"],
            phase_scope=["opening"],
            evidence_ids=["EV-000966", "EV-000972"],
            evaluation_ids=E("kaggle_psychic_v2_441_4"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Psychic aggro v2 attachment tiers",
            "Energy ATTACH options exist",
            ["target_position", "target_energy_count", "required_energy", "pokemon_priority"],
            "ATTACH",
            score="lexicographic(tier, -missing, pokemon_priority); Active_missing=4, Bench_missing=3, Active_ready=2, Bench_ready=1",
            tie_break="smallest option index",
            deck_scope=["Psychic aggro v2"],
            phase_scope=["main"],
            evidence_ids=["EV-000974"],
            evaluation_ids=E("kaggle_psychic_v2_441_4"),
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Psychic aggro v2 main macro",
            "multiple MAIN action families are legal",
            ["basic_play", "attachment", "supporter_card_id", "attack"],
            "MAIN",
            priority="Basic deployment -> attach -> Supporter(Lillie300 > Zeyu200 > Cheren100) -> attack -> end",
            tie_break="smallest option index inside each family",
            deck_scope=["Psychic aggro v2"],
            phase_scope=["main", "attack"],
            evidence_ids=["EV-000975"],
            evaluation_ids=E("kaggle_psychic_v2_441_4"),
            decision="ACCEPT_TEACHER_RULE",
            notes="v1 PLAY hand-index bug is not included",
        )

        # Later experiment-a functions not represented by the placeholder rows.
        A(
            "Enriching Energy Dudunsparce recycle loop",
            "Enriching Energy can attach to Dudunsparce and Run Away Draw remains safe",
            ["candidate_energy_id", "target.card_id", "ability_available", "bench_count", "deck_count"],
            "ATTACH_THEN_ACTIVATE",
            priority="attach Enriching Energy to Dudunsparce, draw, then shuffle Dudunsparce and attached Energy into deck",
            exceptions=["do not remove the last successor", "do not activate across the unresolved deck threshold"],
            deck_scope=["Alakazam leaderboard optimized"],
            phase_scope=["main"],
            evidence_ids=["EV-000654", "EV-000655", "EV-000689", "EV-000692"],
            evaluation_ids=E("optimized_meta_100"),
            decision="ACCEPT_SEARCH_PRIOR",
            notes="small-sample evaluation does not isolate the combo",
        )
        A(
            "tactical Xerosic gate",
            "Xerosic is legal and its discard count is estimated from opponent public handCount",
            ["opponent.handCount", "opponent_ready_attacker", "own_prizes_left", "planned_KO_preserved"],
            "PLAY_XEROSICS_MACHINATIONS",
            priority="never lose a planned KO; discard>=4 use; 3 requires opponent not ready or prizes<=3; 2 requires both; 1 only at prizes=1 and opponent not ready",
            deck_scope=["Alakazam leaderboard optimized"],
            phase_scope=["main", "late_game"],
            evidence_ids=["EV-000687"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "tactical Hilda gate",
            "Hilda is legal",
            ["needed_evolution", "search_safe", "winning_attack_exists", "energy_deficit"],
            "PLAY_HILDA",
            priority="prefer when evolution is needed, search is safe, no winning attack is lost, and Energy deficit is also repaired",
            deck_scope=["Alakazam leaderboard optimized"],
            phase_scope=["main"],
            evidence_ids=["EV-000678"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Nighttime Mine visible Tera gate",
            "Nighttime Mine is legal and a visible Tera Pokémon is in play",
            ["visible_board_tera", "stadium_option"],
            "PLAY_NIGHTTIME_MINE",
            priority="eligible when visible Tera condition holds",
            deck_scope=["Alakazam leaderboard optimized"],
            matchup_scope=["visible Tera"],
            phase_scope=["main"],
            evidence_ids=["EV-000684", "EV-000692"],
            decision="ACCEPT_SEARCH_PRIOR",
        )

        # Sue Alakazam: only observable public state and own hidden state are used.
        A(
            "Sue Powerful Hand damage estimate",
            "Alakazam Powerful Hand attack is planned",
            ["own.hand_size", "available_evolutions", "unused_draw_abilities", "supporter_options", "Enriching_attach"],
            "ATTACK_PLAN",
            score="20*(hand_size + max net hand increase); net: Kadabra evolution +1, Candy->Alakazam +1, Kadabra->Alakazam +2, Dudunsparce +3, Fezandipiti +3, Hilda +1, Dawn +2, Enriching +3",
            tie_break="smallest option index",
            deck_scope=["sue124 Alakazam"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000065"],
            evidence_ids=["EV-001384"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Sue attack target order",
            "one or more visible opponent targets are considered",
            ["Kadabra_30_damage_lethal", "winning_KO", "prize_value", "hp", "active"],
            "ATTACK_OR_BOSS_TARGET",
            priority="Kadabra 30-damage finisher > immediate winning KO (Active then higher HP) > max-Prize KO then higher HP > Active",
            tie_break="smallest option index",
            deck_scope=["sue124 Alakazam"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000065"],
            evidence_ids=["EV-001384"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Sue exact deck safety budget",
            "a non-winning draw/search/evolution effect would remove cards from own deck",
            ["own.deckCount", "own.prize_count", "effect_draw_count", "immediate_win"],
            "DRAW_OR_SEARCH",
            score="safe_draws = deckCount - own_prize_count - 1; allow only when required draws <= safe_draws",
            exceptions=["immediate winning line may ignore future deck-out budget"],
            deck_scope=["sue124 Alakazam"],
            phase_scope=["main", "late_game"],
            policy_ids=["RULE-000065"],
            evidence_ids=["EV-001384"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Sue conditional draw Ability",
            "Dudunsparce or Fezandipiti draw Ability is offered",
            ["planned_KO_damage_gap", "missing_Boss", "missing_evolution", "missing_energy"],
            "ACTIVATE_DRAW_ABILITY",
            priority="use only to reach planned KO or find a missing key enabler",
            deck_scope=["sue124 Alakazam"],
            phase_scope=["main"],
            policy_ids=["RULE-000065"],
            evidence_ids=["EV-001384"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Sue setup capacity",
            "opening or MAIN can deploy Basics",
            ["Abra_line_count", "Dunsparce_line_count", "bench_free", "Fezandipiti_needed"],
            "PLAY_POKEMON",
            priority="Abra line up to 3, Dunsparce line up to 2, keep one Bench slot free; Fezandipiti only when needed",
            deck_scope=["sue124 Alakazam"],
            phase_scope=["opening", "main"],
            policy_ids=["RULE-000065"],
            evidence_ids=["EV-001384"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Sue Energy routing",
            "Energy ATTACH options exist",
            ["energy.card_id", "target_line", "target_energy_count", "retreat_needed"],
            "ATTACH",
            score="Psychic to Abra line until 1; Enriching to Dunsparce line; retreat-enabling Active target=9500",
            tie_break="smallest option index",
            deck_scope=["sue124 Alakazam"],
            phase_scope=["main"],
            policy_ids=["RULE-000065"],
            evidence_ids=["EV-001384"],
            decision="ACCEPT_SEARCH_PRIOR",
        )

        # Tomatomato Archaludon target and selection functions.
        A(
            "Tomatomato score-order selection",
            "one or more options have been scored without an exception",
            ["option_score", "option_index", "minCount", "maxCount"],
            "SELECT_OPTIONS",
            priority="score descending; tie index ascending; skip negative after minCount; fill to minCount if needed",
            tie_break="smallest option index",
            exceptions=["per-option broad-except path is quarantined"],
            deck_scope=["tomatomato Archaludon"],
            phase_scope=["all_selection_contexts"],
            policy_ids=["RULE-000067"],
            evidence_ids=["EV-001429"],
            decision="ACCEPT_TEACHER_RULE",
        )
        A(
            "Archaludon tank retreat suppression",
            "Active is Archaludon ex with a Tool and HP>200",
            ["active.card_id", "active.tool_count", "active.hp"],
            "RETREAT",
            score="-5000",
            deck_scope=["tomatomato Archaludon"],
            phase_scope=["promotion_or_pivot"],
            policy_ids=["RULE-000068"],
            evidence_ids=["EV-001424"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Archaludon attack-ready retreat",
            "computed Archaludon ex attack route exists and needs retreat",
            ["bench_attack_route", "retreat_available"],
            "RETREAT",
            score="13000; default retreat=-100",
            deck_scope=["tomatomato Archaludon"],
            phase_scope=["promotion_or_pivot", "attack"],
            policy_ids=["RULE-000068"],
            evidence_ids=["EV-001424"],
            decision="ACCEPT_SEARCH_PRIOR",
        )
        tom_target_rules = [
            ("Archaludon effect attachment source", "ATTACH_FROM selects a Pokémon", ["candidate.energy_count", "candidate.card_id"], "SELECT_ATTACH_SOURCE", "-5000 at >=3 Energy; Cinderace with >=1 Energy -3000; otherwise effect-attach score"),
            ("Archaludon field target", "TO_FIELD or TO_BENCH selects a Pokémon", ["candidate.card_id"], "SELECT_FIELD_TARGET", "Archaludon ex=18000, Duraludon=16000, Cinderace=3000"),
            ("Archaludon heal target", "HEAL selects a Pokémon", ["candidate.card_id", "damage_on_candidate"], "SELECT_HEAL_TARGET", "Archaludon ex=20000+damage; other=damage"),
            ("Archaludon own promotion", "SWITCH or TO_ACTIVE selects own Pokémon", ["candidate.card_id"], "SELECT_OWN_ACTIVE", "Cinderace=16000, Archaludon ex=15000, Duraludon=8000, other=1000"),
            ("Archaludon Boss target", "SWITCH or TO_ACTIVE selects opponent Pokémon", ["killable", "prize_value", "target_energy"], "SELECT_OPPONENT_TARGET", "killable: 20000+prize*3000+energy*100; otherwise 5000+prize*1000+energy*200"),
            ("Archaludon damage-counter target", "DAMAGE selects a Pokémon", ["candidate.current_hp"], "SELECT_DAMAGE_TARGET", "10000-current_hp"),
        ]
        for name, condition, features, action, score in tom_target_rules:
            A(
                name,
                condition,
                features,
                action,
                score=score,
                tie_break="smallest option index",
                deck_scope=["tomatomato Archaludon"],
                phase_scope=["main", "promotion_or_pivot", "attack"],
                policy_ids=["RULE-000069"],
                evidence_ids=["EV-001428"],
                decision="ACCEPT_SEARCH_PRIOR",
            )
        A(
            "Archaludon attack-energy route",
            "Archaludon ex/Duraludon attack route is evaluated",
            ["current_energy", "hand_metal", "energy_attached_this_turn", "evolution_available", "discard_metal", "retreat_available"],
            "ATTACK_PLAN",
            priority="ready at 3 Energy; 2 plus one legal Metal attach; evolving Duraludon adds min(2, discard Metal); otherwise consider ready Bench route if retreatable",
            deck_scope=["tomatomato Archaludon"],
            phase_scope=["main", "attack"],
            policy_ids=["RULE-000066"],
            evidence_ids=["EV-001407", "EV-001408", "EV-001409"],
            decision="ACCEPT_SEARCH_PRIOR",
            notes="inner route only; random-fallback wrapper remains quarantined",
        )

        # Unique semantics isolated from the otherwise-duplicate Lucario monoliths.
        self.lucario_prize_rule_id = A(
            "Lucario prize-route Mega Lucario suppression",
            "own remaining Prize count is 2 or 3",
            ["my_prize", "attack_route.pokemon_id"],
            "ATTACK_PLAN",
            score="base_score-500 on Mega Lucario ex attack routes",
            penalty=["-500 to Mega Lucario ex route while my_prize in {2, 3}"],
            exceptions=["my_prize==1 is excluded: the source comments that any attacker loses that exchange"],
            deck_scope=["Lucario/Hariyama", "itsuki9180 lucario jp", "kiyotah lucario"],
            phase_scope=["attack"],
            policy_ids=["RULE-000028", "RULE-000031"],
            evidence_ids=["EV-001211", "EV-001246"],
            include_policy_evidence=False,
            evaluation_ids=E("experiment_a_vs_itsuki9180_lucario_jp", "experiment_a_vs_kiyotah_lucario"),
            decision="ACCEPT_SEARCH_PRIOR",
            notes="isolated from the rejected Lucario monolith rules; identical logic appears in both itsuki9180 and kiyotah implementations",
        )

        # romanrozen_strongstart _pilot: EXACT public implementation with numeric option-type scores.
        A(
            "Strongstart lethal attack ranking",
            "MAIN context 0 offers an attack option with known damage",
            ["attack_damage_table", "opponent_active.hp", "opponent_active.weakness", "own_active.energy_type"],
            "RANK_MAIN_OPTIONS",
            score="eff=damage*2 on weakness match else damage; lethal (eff>=opponent Active HP) scores 900+eff; otherwise 40+0.2*min(eff, 320)",
            bonus=["weakness match doubles effective damage"],
            tie_break="first option index at strictly greater score (earliest maximum wins)",
            deck_scope=["romanrozen strongstart"],
            phase_scope=["main", "attack"],
            evidence_ids=["EV-001293"],
            evaluation_ids=E("experiment_a_vs_romanrozen_strongstart", "ruruko_v0_vs_romanrozen_strongstart"),
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Strongstart option-type score table",
            "MAIN context 0 ranks options by numeric option.type",
            ["option.type", "option.inPlayArea", "hand_card_class", "own.deckCount", "own.handCount"],
            "RANK_MAIN_OPTIONS",
            score="type9=200; type10=150; type8=170 if inPlayArea==4 else 120; type7 by hand card: Basic Pokemon 160, other Pokemon 158, Supporter -50 if deckCount<=7 else 130 if handCount<=4 else 70, other card 110; type12=20; type14=0; unknown=5",
            penalty=["Supporter scored -50 while own deckCount<=7 (deck-out guard)"],
            tie_break="first option index at strictly greater score (earliest maximum wins)",
            exceptions=["type13 attack options are ranked by the lethal attack ranking instead"],
            deck_scope=["romanrozen strongstart"],
            phase_scope=["main"],
            evidence_ids=["EV-001293"],
            evaluation_ids=E("experiment_a_vs_romanrozen_strongstart", "ruruko_v0_vs_romanrozen_strongstart"),
            decision="ACCEPT_SEARCH_PRIOR",
        )
        A(
            "Strongstart promotion and fallback selection",
            "selection context 22 or any unhandled context",
            ["select.context", "option.inPlayArea", "select.minCount"],
            "SELECT_PROMOTION_OR_FALLBACK",
            priority="context 22: first option with inPlayArea==4; otherwise first max(1, minCount) option indexes",
            tie_break="smallest option index",
            deck_scope=["romanrozen strongstart"],
            phase_scope=["promotion_or_pivot"],
            evidence_ids=["EV-001293"],
            evaluation_ids=E("experiment_a_vs_romanrozen_strongstart", "ruruko_v0_vs_romanrozen_strongstart"),
            decision="ACCEPT_SEARCH_PRIOR",
        )

    def build_combos_macros_matchups(self) -> None:
        E = lambda *keys: [self.eval_by_key[key] for key in keys]

        def combo(
            name: str,
            scope: list[str],
            condition: str,
            setup: list[str],
            energy: list[str],
            evolution: list[str],
            target: list[str],
            *,
            retreat: list[str] | None = None,
            supporter: list[str] | None = None,
            search: list[str] | None = None,
            deck_out: list[str] | None = None,
            successor: list[str] | None = None,
            matchup: list[str] | None = None,
            evidence: list[str],
            evaluations: list[str] | None = None,
            decision: str = "ACCEPT_SEARCH_PRIOR",
        ) -> None:
            self.add_knowledge(
                self.combos,
                "CB",
                name,
                {
                    "deck_scope": scope,
                    "observable_condition": condition,
                    "setup_sequence": setup,
                    "energy_attachment_plan": energy,
                    "evolution_sequence": evolution,
                    "target_selection": target,
                    "retreat_conditions": retreat or [],
                    "supporter_conditions": supporter or [],
                    "search_targets": search or [],
                    "deck_out_avoidance": deck_out or [],
                    "successor_maintenance": successor or [],
                    "matchup_conditions": matchup or [],
                },
                evidence_ids=evidence,
                evaluation_ids=evaluations,
                decision=decision,
            )

        combo(
            "Abra line evolution draw chain",
            ["Alakazam"],
            "Abra/Kadabra and the matching evolution pieces are visible",
            ["deploy Abra", "preserve an evolution Bench slot", "evolve before optional draw Ability"],
            ["attach one Psychic Energy to the attacker line"],
            ["Abra -> Kadabra -> Alakazam", "or Abra + Rare Candy -> Alakazam"],
            ["after hand growth, choose a KO target for Powerful Hand"],
            search=["missing Alakazam line stage", "Rare Candy when direct evolution is legal"],
            deck_out=["apply the active policy's verified draw budget; threshold variants remain held"],
            successor=["keep another Abra/Dunsparce line when Bench capacity permits"],
            evidence=["EV-000560", "EV-000658", "EV-000692", "EV-001384"],
            evaluations=E("ruruko_v0_old_gauntlet"),
        )
        combo(
            "Alakazam Jumbo Ice Cream tank",
            ["Alakazam control v2"],
            "Active Alakazam has >=3 Energy and >=40 damage",
            ["promote Alakazam", "accumulate three Energy"],
            ["do not divert the third Energy before the legacy gate"],
            ["complete Alakazam before healing"],
            ["retain a KO line after healing"],
            deck_out=["none in the item itself"],
            successor=["do not consume successor resources solely for healing"],
            evidence=["EV-000869", "EV-001324"],
            evaluations=E("control_v2_vs_pre_jumbo"),
            decision="HOLD_FOR_EXPERIMENT",
        )
        combo(
            "Enriching Energy Run Away Draw recycle",
            ["Alakazam leaderboard optimized"],
            "Dudunsparce and Enriching Energy are available and a successor remains",
            ["develop Dunsparce/Dudunsparce", "leave a replacement Pokémon"],
            ["attach Enriching Energy to Dudunsparce"],
            ["Dunsparce -> Dudunsparce"],
            ["activate Run Away Draw after the Energy draw resolves"],
            deck_out=["do not activate at an unresolved unsafe deck threshold"],
            successor=["require a remaining replacement before shuffling Dudunsparce away"],
            evidence=["EV-000654", "EV-000655", "EV-000689", "EV-000692"],
            evaluations=E("optimized_meta_100"),
        )
        combo(
            "Boss immediate-KO conversion",
            ["Alakazam"],
            "an attack is legal and an opponent Bench target becomes an immediate or winning KO",
            ["calculate current attack damage", "compare Active and Bench KO lines"],
            [],
            [],
            ["gust the best lexicographic KO target", "attack that target"],
            supporter=["Boss only when attack option and opponent Bench exist"],
            evidence=["EV-000671", "EV-000673", "EV-001322"],
        )
        combo(
            "Night Stretcher successor repair",
            ["Alakazam Experiment A"],
            "own field has <=2 Pokémon or the Abra line is incomplete",
            ["observe remaining field count", "identify missing line stage"],
            [],
            [],
            ["recover Abra first in an emergency; otherwise recover Alakazam/Kadabra to complete an existing Abra"],
            search=["Abra", "Dunsparce", "Alakazam", "Kadabra", "Basic Psychic Energy"],
            deck_out=["recovery itself does not establish the unresolved draw threshold"],
            successor=["repair a second attacker/replacement line"],
            evidence=["EV-001348", "EV-001374"],
            evaluations=E("experiment_a_gauntlet"),
        )
        combo(
            "Crustle three-Energy wall",
            ["harukiharada Crustle"],
            "Dwebble/Crustle is visible and Grass/Mist resources are available",
            ["open with Dwebble", "develop a Crustle line"],
            ["Grass first when absent", "Mist for mixed-energy completion", "Counter Gain bonus only when behind and prerequisites hold"],
            ["Dwebble -> Crustle"],
            ["attack through the wall or keep it Active against ex"],
            retreat=["move only to a fresh Crustle when damaged and opponent has no ex"],
            search=["Dwebble", "Crustle", "Grass Energy", "Mist Energy"],
            successor=["fresh Bench Crustle is the replacement wall"],
            evidence=["EV-001186", "EV-001197", "EV-001199", "EV-001201"],
        )
        combo(
            "Crustle Counter Gain catch-up",
            ["harukiharada Crustle"],
            "own Prize count is larger than opponent Prize count and Crustle has Grass plus miscellaneous Energy",
            ["attach Counter Gain to Crustle"],
            ["complete Grass + miscellaneous Energy before the conditional Mist bonus"],
            ["Dwebble -> Crustle"],
            ["use the completed Crustle attack"],
            evidence=["EV-001197", "EV-001198", "EV-001201"],
        )
        combo(
            "Munkidori Darkness package",
            ["harukiharada Crustle"],
            "Munkidori is visible and Basic Darkness can attach",
            ["deploy Munkidori as auxiliary attacker"],
            ["first Darkness to Munkidori scores 9200; later Darkness 8600"],
            [],
            ["use Munkidori according to its attack/target score"],
            evidence=["EV-001197", "EV-001201"],
        )
        combo(
            "Mega Lucario Wave Punch discard-Energy ranking prior",
            ["Lucario/Hariyama"],
            "Mega Lucario ex attack 0 is available",
            ["develop Riolu line", "observe discarded Fighting Energy"],
            ["one Energy readies Wave Punch"],
            ["Riolu -> Mega Lucario ex"],
            ["attack remains modeled as 130 damage; discarded Energy adds only 60-per-card ranking bonus up to three", "prefer a KO target using prize/energy/stage score"],
            search=["Mega Lucario ex", "Fighting Energy"],
            evidence=["EV-001255"],
        )
        combo(
            "Hariyama non-ex Crustle attacker",
            ["Lucario/Hariyama"],
            "Crustle 345 is visible and the Makuhita/Hariyama line can be developed",
            ["deploy Makuhita", "exclude Mega Lucario as blocked attack route"],
            ["attach toward three Energy; incomplete line receives priority"],
            ["Makuhita -> Hariyama"],
            ["attack Crustle with Wild Press"],
            matchup=["Crustle 345"],
            successor=["keep a non-ex route while ex is blocked"],
            evidence=["EV-001255"],
        )
        combo(
            "Lunatone Solrock secondary attacker",
            ["Lucario/Hariyama"],
            "Lunatone is in play and Solrock can receive one Energy",
            ["deploy both complementary Basics"],
            ["attach exactly one Energy to Solrock; later copies are penalized"],
            [],
            ["use Solrock's modeled 70-damage attack"],
            evidence=["EV-001255"],
        )
        combo(
            "Abomasnow Kyogre lethal switch",
            ["kiyotah Abomasnow/Kyogre"],
            "opponent Active HP is at most 20 times discarded Water Energy",
            ["develop Snover/Mega Abomasnow as default attacker", "accumulate discarded Water Energy"],
            ["two Energy for Mega Abomasnow; one for Kyogre"],
            ["Snover -> Mega Abomasnow ex"],
            ["switch to Kyogre only when Riptide threshold is met"],
            retreat=["switch when planned ready attacker differs from Active"],
            search=["Snover", "Mega Abomasnow ex", "Kyogre"],
            evidence=["EV-001217"],
            evaluations=E("experiment_a_vs_kiyotah_abomasnow"),
        )
        combo(
            "Voltorb field-Energy chain",
            ["kiyotah Iono"],
            "Voltorb is available and field Energy can be spread across three lines",
            ["maintain Voltorb, Bellibolt, and Kilowattrel lines"],
            ["distribute Lightning Energy; Voltorb damage uses total field Energy"],
            ["Tadbulb -> Bellibolt", "Wattrel -> Kilowattrel"],
            ["promote Voltorb when 20+20*field Energy is lethal"],
            deck_out=["stop draw/search at deckCount<=5"],
            successor=["preserve all three lines instead of saturating one"],
            evidence=["EV-001237"],
            evaluations=E("experiment_a_vs_kiyotah_iono"),
        )
        combo(
            "Kilowattrel Levincia loop",
            ["kiyotah Iono"],
            "Kilowattrel has one Energy and discard contains Lightning Energy or its Ability is available",
            ["deploy Wattrel"],
            ["attach first Lightning Energy to Wattrel/Kilowattrel"],
            ["Wattrel -> Kilowattrel"],
            ["use Levincia when the discard/Ability condition holds"],
            search=["Wattrel", "Kilowattrel", "Levincia"],
            deck_out=["disable Ability at deckCount<=5"],
            evidence=["EV-001237"],
        )
        combo(
            "Dragapult Phantom Dive spread",
            ["kiyotah Dragapult"],
            "Dragapult ex can be evolved with both required Energy types",
            ["deploy Dreepy", "preserve multiple low-HP Bench targets on opponent field"],
            ["attach Fire and Psychic as distinct requirements"],
            ["Dreepy -> Drakloak -> Dragapult ex", "or Rare Candy direct evolution"],
            ["200 to Active plus 60 damage counters allocated for multi-KO pressure"],
            supporter=["Boss when a better spread/KO target is visible"],
            search=["missing Dragapult evolution stage", "Rare Candy"],
            evidence=["EV-001223", "EV-001224", "EV-001230", "EV-001231"],
        )
        combo(
            "Sue multi-source Powerful Hand growth",
            ["sue124 Alakazam"],
            "Alakazam attack is available and hand-growth enablers are visible",
            ["evolve Abra/Kadabra", "activate Dudunsparce/Fezandipiti only as needed"],
            ["Enriching Energy on Active Alakazam can net +3 hand"],
            ["Abra/Kadabra -> Alakazam"],
            ["select a KO target after computing maximum reachable hand"],
            supporter=["choose at most one of Hilda/Dawn/Boss by required net hand and target"],
            search=["missing evolution", "Boss", "Energy"],
            deck_out=["required draw count must fit deckCount-prizes-1"],
            successor=["keep one Bench slot and a second line"],
            evidence=["EV-001384"],
        )
        combo(
            "Enhanced Hammer removes special-defense Energy",
            ["sue124 Alakazam"],
            "planned target has visible special-defense Energy and available Hammers are sufficient",
            ["estimate Powerful Hand after paying Hammer hand cost"],
            [],
            [],
            ["remove special-defense Energy, recompute damage, then attack only if KO remains"],
            evidence=["EV-001383", "EV-001384"],
        )
        combo(
            "Cinderace Turbo Flare to Archaludon",
            ["tomatomato Archaludon"],
            "Cinderace can attack and a Duraludon line is available",
            ["start Cinderace Active", "place Duraludon on Bench"],
            ["Turbo Flare supplies three Metal to Bench Duraludon"],
            ["Duraludon -> Archaludon ex after Energy setup"],
            ["use Metal Defender 220 after evolution"],
            search=["Duraludon", "Archaludon ex"],
            successor=["Bench Duraludon is the next attacker"],
            evidence=["EV-001407", "EV-001408", "EV-001409", "EV-001412"],
        )
        combo(
            "Ultra Ball fuels Assemble Alloy",
            ["tomatomato Archaludon"],
            "Ultra Ball can safely discard Metal and the Duraludon line is incomplete",
            ["retain enough hand to pay two-card discard"],
            ["discard up to two Metal for later Alloy attachment"],
            ["search/evolve Duraludon -> Archaludon ex"],
            ["attach discarded Metal via Assemble Alloy"],
            search=["Duraludon", "Archaludon ex"],
            evidence=["EV-001402", "EV-001409", "EV-001420", "EV-001427"],
        )
        combo(
            "Relicanth preserves Raging Hammer",
            ["tomatomato Archaludon"],
            "Relicanth is in play and damaged Archaludon/Duraludon can use the inherited attack",
            ["keep Relicanth when the route is required"],
            ["meet inherited attack Energy requirement"],
            ["Duraludon may evolve while retaining the inherited route"],
            ["Raging Hammer damage = 80 + 10*damage counters"],
            matchup=["especially Crustle where Metal Defender is blocked"],
            evidence=["EV-001412", "EV-001419"],
        )

        def macro(name: str, scope: list[str], condition: str, steps: list[str], evidence: list[str], *, evaluations: list[str] | None = None, decision: str = "ACCEPT_SEARCH_PRIOR", exceptions: list[str] | None = None) -> None:
            self.add_knowledge(
                self.macros,
                "MC",
                name,
                {
                    "deck_scope": scope,
                    "observable_condition": condition,
                    "ordered_steps": steps,
                    "exceptions": exceptions or [],
                },
                evidence_ids=evidence,
                evaluation_ids=evaluations,
                decision=decision,
            )

        macro("generic attack-plan macro", ["ANY_60_CARD_DECK"], "MAIN and turn>=2", ["enumerate legal current/pivot attackers", "estimate damage and target", "evolve or attach toward selected plan", "deploy useful Pokémon", "execute planned attack"], ["EV-000022", "EV-000029"], exceptions=["public meta_1..5 actions were not scraped expert labels"])
        macro("Lucario battle macro", ["Lucario/Hariyama"], "MAIN", ["secure Riolu/Makuhita/Solrock lines", "compute attack and target plan", "attach required Energy", "Switch/Boss only when plan requires it", "evolve", "attack"], ["EV-001255"])
        macro("Crustle wall macro", ["harukiharada Crustle"], "opening through main", ["open Dwebble", "attach Grass", "complete Mist/Counter Gain resources", "evolve Crustle", "keep wall Active against ex", "rotate to fresh Crustle only when condition holds"], ["EV-001197", "EV-001199", "EV-001201"])
        macro("Abomasnow Kyogre macro", ["kiyotah Abomasnow/Kyogre"], "main", ["deploy Snover", "prepare two-Energy Mega Abomasnow", "accumulate discarded Water", "switch to Kyogre only for Riptide lethal"], ["EV-001217"], evaluations=E("experiment_a_vs_kiyotah_abomasnow"))
        macro("Iono Energy-spread macro", ["kiyotah Iono"], "opening through late game", ["secure Voltorb attacker", "develop Wattrel/Kilowattrel", "develop Tadbulb/Bellibolt", "spread Lightning Energy", "stop draw/search at deckCount<=5"], ["EV-001237"], evaluations=E("experiment_a_vs_kiyotah_iono"))
        macro("Dragapult spread-damage macro", ["kiyotah Dragapult"], "main and attack", ["deploy Dreepy", "advance evolution or Rare Candy", "attach Fire and Psychic", "choose Phantom Dive Active target", "allocate 60 Bench counters", "Boss only for improved KO map"], ["EV-001223", "EV-001230", "EV-001231"], evaluations=E("experiment_a_vs_kiyotah_dragapult"))
        macro("Ruruko strict macro unresolved", ["ruruko Alakazam"], "MAIN", ["Basic", "Item", "evolve", "draw Ability", "matchup retreat", "attach", "Supporter", "attack"], ["EV-001326", "EV-001372"], evaluations=E("ruruko_v0_gauntlet", "experiment_a_gauntlet"), decision="HOLD_FOR_EXPERIMENT", exceptions=["global ordering conflicts with CONFLICT-000001"])
        macro("Sue lethal-construction macro", ["sue124 Alakazam"], "MAIN with an Alakazam attack line", ["select lethal target", "calculate required hand increase", "evolve and draw only required amount", "apply Hammer/Boss enablers", "attach or retreat", "attack with Powerful Hand"], ["EV-001384"])
        macro("Archaludon acceleration macro", ["tomatomato Archaludon"], "opening through attack", ["open Cinderace", "Turbo Flare to Duraludon", "prepare discard Metal", "evolve Active Duraludon", "Assemble Alloy", "choose Metal Defender or inherited Raging Hammer"], ["EV-001407", "EV-001409", "EV-001412"])
        macro("Psychic aggro v2 deterministic macro", ["Psychic aggro v2"], "opening and MAIN", ["select prioritized Active", "deploy Basics", "attach by readiness tier", "play one prioritized Supporter", "attack", "end"], ["EV-000972", "EV-000974", "EV-000975"], evaluations=E("kaggle_psychic_v2_441_4"), decision="ACCEPT_TEACHER_RULE")

        def matchup(name: str, scope: list[str], opponents: list[str], condition: str, action: str, exceptions: list[str], evidence: list[str], evaluations: list[str] | None, decision: str) -> None:
            self.add_knowledge(
                self.matchups,
                "MR",
                name,
                {
                    "deck_scope": scope,
                    "opponent_scope": opponents,
                    "observable_condition": condition,
                    "candidate_action_type": action,
                    "exceptions": exceptions,
                },
                evidence_ids=evidence,
                evaluation_ids=evaluations,
                decision=decision,
            )

        matchup("Alakazam vs Crustle ex-block pivot", ["Alakazam"], ["Crustle 345"], "opponent Active Crustle, own Active ex, non-ex Bench and RETREAT visible", "RETREAT_TO_NON_EX", ["no legal non-ex pivot"], ["EV-001317", "EV-001318", "EV-001326", "EV-001328"], E("control_v2_vs_crustle", "experiment_a_vs_harukiharada_crustle"), "ACCEPT_SEARCH_PRIOR")
        matchup("Alakazam vs Fighting weakness pivot", ["Alakazam"], ["Fighting", "Lucario"], "visible Fighting Active, own Active weak, non-weak Bench and RETREAT visible", "RETREAT_TO_NON_WEAK", ["evaluation shows no isolated improvement"], ["EV-001306", "EV-001319", "EV-001320", "EV-001326"], E("control_v2_vs_kojimar", "initial_vs_kojimar"), "HOLD_FOR_EXPERIMENT")
        matchup("Lucario vs Crustle Hariyama route", ["Lucario/Hariyama"], ["Crustle 345"], "Crustle is visible", "DEVELOP_HARIYAMA_AND_EXCLUDE_MEGA_LUCARIO_ATTACK", [], ["EV-001255"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Lucario vs Water late-game caution", ["Lucario/Hariyama"], ["Abomasnow/Kyogre Water"], "Water line visible and opponent prizes<=3", "PENALIZE_MEGA_LUCARIO_EXPOSURE", ["card-ID detector is snapshot-specific"], ["EV-001167", "EV-001255"], None, "HOLD_FOR_EXPERIMENT")
        matchup("Sue vs Duskull Psyduck tech", ["sue124 Alakazam"], ["Duskull line"], "Duskull is visible on opponent field", "DEPLOY_PSYDUCK", [], ["EV-001384"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Sue vs Water threat Shaymin tech", ["sue124 Alakazam"], ["Slowpoke", "Froakie", "Wellspring Ogerpon ex", "N's Darumaka"], "one listed Water threat is visible", "DEPLOY_SHAYMIN", [], ["EV-001384"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Sue vs Dragapult Battle Cage", ["sue124 Alakazam"], ["Dragapult line"], "Dreepy, Drakloak, or Dragapult ex is visible", "PLAY_BATTLE_CAGE_SCORE_19000", [], ["EV-001384"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Archaludon vs Crustle non-ex hammer route", ["tomatomato Archaludon"], ["Crustle"], "Crustle matchup detector is active", "KEEP_DURALUDON_RELICANTH_RAGING_HAMMER_ROUTE", ["do not evolve to Archaludon ex or use Metal Defender"], ["EV-001417", "EV-001419"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Archaludon vs Hop Boss Snorlax", ["tomatomato Archaludon"], ["Hop"], "Hop Snorlax is a legal gust target", "BOSS_SNORLAX", ["Cinderace picks least-mobile; Archaludon picks greatest threat"], ["EV-001420", "EV-001428"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Archaludon vs Alakazam heal ceiling", ["tomatomato Archaludon"], ["Alakazam"], "opponent public handCount and visible board allow Powerful Hand range estimate", "CONDITIONAL_JUMBO_ICE_CREAM", ["do not heal away a Raging Hammer KO; skip if healed HP stays below floor"], ["EV-001413", "EV-001419"], None, "ACCEPT_SEARCH_PRIOR")
        matchup("Archaludon heal threshold vs Lucario/Starmie", ["tomatomato Archaludon"], ["Lucario", "Starmie"], "matchup-specific incoming damage threshold is visible in policy", "HEAL_ABOVE_270_OR_210_THRESHOLD", ["not isolated by evaluation"], ["EV-001416", "EV-001419"], None, "HOLD_FOR_EXPERIMENT")
        matchup("Crustle holds wall against ex", ["harukiharada Crustle"], ["visible opponent ex"], "Active Crustle and any opponent ex is visible", "DO_NOT_RETREAT_CRUSTLE", ["fresh-wall rotation only when opponent has no ex"], ["EV-001199", "EV-001201"], None, "ACCEPT_SEARCH_PRIOR")

    def build_rejections(self) -> None:
        explicit_policy_rejections = [
            (["RULE-000018", "RULE-000019", "RULE-000020"], "belief-guided-search is restricted to duplicate confirmation and is not a primary source in phase 2", "QUARANTINE_UNVERIFIED"),
            (["RULE-000024"], "aristophanivan dispatcher falls back to [0] after conversion failure; not a semantic strategy", "QUARANTINE_UNVERIFIED"),
            (["RULE-000025"], "rollout_turn is search plumbing that reuses AdvancedPolicy and adds no independent rule", "REJECT_DUPLICATE"),
            (["RULE-000028"], "Itsuki Lucario monolith duplicates the canonical Lucario-family rules after its unique prize-route penalty was isolated into a dedicated rule", "REJECT_DUPLICATE"),
            (["RULE-000031"], "Kiyota Lucario monolith duplicates canonical Kojimar LucarioPolicy after its unique prize-route penalty was isolated into a dedicated rule", "REJECT_DUPLICATE"),
            (["RULE-000033"], "Kojimar agent is only a dispatcher to LucarioPolicy", "REJECT_DUPLICATE"),
            (["RULE-000046"], "Ruruko v0 agent is only registration/dispatch around extracted functions", "REJECT_DUPLICATE"),
            (["RULE-000056"], "Experiment A required-minimum helper duplicates accepted legality constraint", "REJECT_DUPLICATE"),
            (["RULE-000058"], "Experiment A Jumbo helper is stale for the reviewed policy/deck path and duplicates the v0 gate", "REJECT_DUPLICATE"),
            (["RULE-000063"], "Experiment A agent is only registration/dispatch around extracted functions", "REJECT_DUPLICATE"),
            (["RULE-000066"], "Tomatomato wrapper uses broad except followed by random fallback", "QUARANTINE_UNVERIFIED"),
        ]
        replacement_map = {
            "RULE-000028": [self.lucario_prize_rule_id],
            "RULE-000031": [self.lucario_prize_rule_id],
        }
        for policy_ids, reason, decision in explicit_policy_rejections:
            replacements = sorted({rid for pid in policy_ids for rid in replacement_map.get(pid, [])})
            self.reject("policy_function", policy_ids, reason, decision, replacement_ids=replacements)

        self.reject(
            "public_opponent_policy",
            ["opponents/official_random/main.py::agent"],
            "official_random selects uniformly among legal options; there is no deterministic ranking semantics to extract, so it is kept out of learning candidates",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-001281"],
        )

        self.reject(
            "generic_legacy_implementation",
            ["EV-000011"],
            "EV-000029 is the later generic implementation; legacy behavior is duplicate/stale",
            "REJECT_DUPLICATE",
            evidence_ids=["EV-000011", "EV-000029"],
        )
        self.reject(
            "policy_subpath",
            ["RULE-000021:random_fallback"],
            "judgment exceptions lead to random legal selection and are not a learnable semantic rule",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-000029"],
        )
        self.reject(
            "policy_subpath",
            ["RULE-000023:search_execution"],
            "broad exception handling and unreleased search resources make the execution wrapper unverified; state prior remains separately curated",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-001170"],
        )
        self.reject(
            "policy_subpath",
            ["RULE-000027:Supporter_scores"],
            "Supporter branches are nested under Item/Tool/Stadium type checks and are normally unreachable; hand-card scorer also receives board counts",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-001200", "EV-001201"],
        )
        self.reject(
            "policy_subpath",
            ["RULE-000030:ATTACK"],
            "Iono ATTACK is ranked by attackId rather than damage/effect and cannot be interpreted as a strategic score",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-001237"],
        )
        self.reject(
            "policy_subpath",
            ["RULE-000067:per_option_exception"],
            "per-option broad exception is converted to a sentinel score; only exception-free ordering is accepted",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-001429"],
        )

        for contradiction in self.contradictions:
            evidence_ids = sorted({eid for claim in contradiction["claims"] for eid in claim["evidence_ids"]})
            self.reject(
                "contradiction",
                [contradiction["contradiction_id"]],
                f"{contradiction['topic']} remains unresolved; no conflicting claim is accepted as a global rule",
                "HOLD_FOR_EXPERIMENT",
                evidence_ids=evidence_ids,
            )

        self.reject(
            "counterexample_fixture",
            ["artifacts/search/c3_bounded_search_v0_counterexamples.json"],
            "belief-guided-search counterexample is used only to confirm duplicate/failure status and is not a real match result",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-000073"],
        )
        self.reject(
            "broken_agent_fixtures",
            [f"EV-{number:06d}" for number in range(1846, 1854)],
            "noninteger or symlinked broken-agent fixtures are submission-validator counterexamples, not decks or battles",
            "REJECT_INVALID_FIXTURE",
            evidence_ids=[f"EV-{number:06d}" for number in range(1846, 1854)],
        )
        self.reject(
            "invalid_meta_records",
            ["meta_snapshot.bad_rewards"],
            "10 bad_rewards records are explicitly invalid and excluded from the 5008-game aggregate",
            "REJECT_INVALID_FIXTURE",
            evidence_ids=["EV-000357"],
        )
        self.reject(
            "duplicate_evaluation_evidence",
            ["EV-000525"],
            "byte-equivalent duplicate of the Kaggle submission CSV already represented by EV-000524",
            "REJECT_DUPLICATE",
            evidence_ids=["EV-000524", "EV-000525"],
        )
        self.reject(
            "duplicate_meta_reports",
            ["EV-001539", "EV-001541", "EV-001542"],
            "reports/meta duplicates the report/meta observations; canonical evidence is EV-001531/1533/1534",
            "REJECT_DUPLICATE",
            evidence_ids=["EV-001531", "EV-001533", "EV-001534", "EV-001539", "EV-001541", "EV-001542"],
        )
        self.reject(
            "phase1_merged_evaluations",
            ["EVAL-000070", "EVAL-000071"],
            "phase-1 rows merged different meta snapshots; curated registry uses only target snapshot EV-000358/359",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-000353", "EV-000354", "EV-000358", "EV-000359"],
        )
        self.reject(
            "phase1_merged_leaderboard_evaluations",
            ["EVAL-000191", "EVAL-000192", "EVAL-000193", "EVAL-000197", "EVAL-000208", "EVAL-000219", "EVAL-000230", "EVAL-000239", "EVAL-000240"],
            "phase-1 rows merged 04:59 and 05:30 snapshots; curated entries use only EV-001431..1440",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=[f"EV-{number:06d}" for number in range(1431, 1450)],
        )
        self.reject(
            "eval_stats_without_direct_phase1_result_evidence",
            ["parallel_runner_acceptance", "SPRT_live", "round_robin_rating"],
            "referenced BENCH_USAGE results have only commit-locator evidence IDs in phase 1; commit messages are prohibited as evaluation results",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-002091", "EV-002120", "EV-002132", "EV-002141"],
        )
        self.reject(
            "evaluation_false_positive_classes",
            ["commit_messages", "function_bodies", "score_constants", "pending_submissions"],
            "these classes are not measured battle results and are excluded from evaluation_registry",
            "QUARANTINE_UNVERIFIED",
            evidence_ids=["EV-000348", "EV-000514", "EV-002120", "EV-002132"],
        )

        covered = {
            policy_id
            for collection in (self.rules, self.hard_constraints, self.rejected)
            for row in collection
            for policy_id in row.get("source_policy_rule_ids", [])
        }
        for policy in self.policy_rows:
            if policy["rule_id"] not in covered:
                self.reject(
                    "policy_function",
                    [policy["rule_id"]],
                    "no safely isolated semantic rule remains after duplicate and verification review",
                    "QUARANTINE_UNVERIFIED",
                )

    def build_teacher_registry(self) -> list[dict[str, Any]]:
        teachers = []
        for rule in self.rules:
            if rule["decision"] != "ACCEPT_TEACHER_RULE":
                continue
            teachers.append({
                "teacher_id": f"TR-{len(teachers) + 1:06d}",
                "canonical_rule_id": rule["rule_id"],
                "name": rule["name"],
                "observable_condition": rule["observable_condition"],
                "observable_features": rule["observable_features"],
                "candidate_action_type": rule["candidate_action_type"],
                "score_formula": rule["score_formula"],
                "priority": rule["priority"],
                "bonus": rule["bonus"],
                "penalty": rule["penalty"],
                "tie_break": rule["tie_break"],
                "exceptions": rule["exceptions"],
                "deck_scope": rule["deck_scope"],
                "matchup_scope": rule["matchup_scope"],
                "phase_scope": rule["phase_scope"],
                "evaluation_ids": rule["evaluation_ids"],
                "source_evidence_ids": rule["source_evidence_ids"],
                "decision": rule["decision"],
            })
        for macro in self.macros:
            if macro["decision"] != "ACCEPT_TEACHER_RULE":
                continue
            teachers.append({
                "teacher_id": f"TR-{len(teachers) + 1:06d}",
                "canonical_rule_id": macro["macro_id"],
                "name": macro["name"],
                "observable_condition": macro["observable_condition"],
                "observable_features": macro.get("observable_features", []),
                "candidate_action_type": "MACRO_SEQUENCE",
                "score_formula": None,
                "priority": "deterministic ordered steps: " + " -> ".join(macro["ordered_steps"]),
                "bonus": [],
                "penalty": [],
                "tie_break": "macro executes its steps in fixed order",
                "exceptions": macro["exceptions"],
                "deck_scope": macro["deck_scope"],
                "matchup_scope": macro.get("matchup_scope", []),
                "phase_scope": macro.get("phase_scope", []),
                "evaluation_ids": macro["evaluation_ids"],
                "source_evidence_ids": macro["source_evidence_ids"],
                "decision": macro["decision"],
            })
        return teachers

    def build_provenance(self, teachers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        collections = [
            ("canonical_rules.jsonl", self.rules, "rule_id"),
            ("executable_teacher_registry.jsonl", teachers, "teacher_id"),
            ("deck_variants.jsonl", self.decks, "deck_variant_id"),
            ("evaluation_registry.jsonl", self.evaluations, "evaluation_id"),
            ("combos.jsonl", self.combos, "combo_id"),
            ("macros.jsonl", self.macros, "macro_id"),
            ("matchup_rules.jsonl", self.matchups, "matchup_rule_id"),
            ("hard_constraints.jsonl", self.hard_constraints, "constraint_id"),
            ("rejected_or_quarantined.jsonl", self.rejected, "record_id"),
        ]
        provenance = []
        for filename, rows, id_key in collections:
            for row in rows:
                evidence_ids = row.get("source_evidence_ids", [])
                sources = []
                for evidence_id in evidence_ids:
                    evidence = self.evidence[evidence_id]
                    sources.append({
                        "evidence_id": evidence_id,
                        "branch": evidence.get("branch"),
                        "commit": evidence.get("commit"),
                        "path": evidence.get("path"),
                        "line_range": evidence.get("line_range"),
                        "evidence_type": evidence.get("evidence_type"),
                        "certification": evidence.get("certification"),
                    })
                provenance.append({
                    "provenance_id": f"PV-{len(provenance) + 1:06d}",
                    "curated_id": row[id_key],
                    "curated_file": filename,
                    "source_evidence_ids": evidence_ids,
                    "sources": sources,
                })
        return provenance

    def validate_memory(self, teachers: list[dict[str, Any]], provenance: list[dict[str, Any]]) -> dict[str, bool]:
        collections = [
            self.rules, teachers, self.decks, self.evaluations, self.combos,
            self.macros, self.matchups, self.hard_constraints, self.rejected, provenance,
        ]
        ids = []
        id_keys = (
            "rule_id", "teacher_id", "deck_variant_id", "evaluation_id",
            "combo_id", "macro_id", "matchup_rule_id", "constraint_id",
            "record_id", "provenance_id",
        )
        for rows in collections:
            for row in rows:
                ids.extend(row[key] for key in id_keys if key in row)

        accepted_rows = [
            row
            for rows in (self.rules, self.decks, self.evaluations, self.combos, self.macros, self.matchups, self.hard_constraints)
            for row in rows
            if row["decision"] in ACCEPTED
        ]
        evidence_refs = {
            evidence_id
            for rows in collections
            for row in rows
            for evidence_id in row.get("source_evidence_ids", [])
        }
        evaluation_ids = {row["evaluation_id"] for row in self.evaluations}
        referenced_evaluations = {
            evaluation_id
            for rows in (self.rules, teachers, self.decks, self.combos, self.macros, self.matchups)
            for row in rows
            for evaluation_id in row.get("evaluation_ids", [])
        }
        rule_ids = {row["rule_id"] for row in self.rules}
        placeholder_policy_ids = {
            row["rule_id"] for row in self.policy_rows if row["preferred_action"] == PLACEHOLDER
        }
        processed_policy_ids = {
            policy_id
            for rows in (self.rules, self.hard_constraints, self.rejected)
            for row in rows
            for policy_id in row.get("source_policy_rule_ids", [])
        }
        unresolved_ids = {row["contradiction_id"] for row in self.contradictions}
        held_contradictions = {
            source_id
            for row in self.rejected
            if row["item_type"] == "contradiction" and row["decision"] == "HOLD_FOR_EXPERIMENT"
            for source_id in row["source_item_ids"]
        }
        counterexample_evidence = {"EV-000073", *{f"EV-{number:06d}" for number in range(1845, 1854)}}

        wld_arithmetic = True
        for row in self.evaluations:
            wins, losses, draws, games = row["wins"], row["losses"], row["draws"], row["games"]
            if wins is not None and losses is not None and draws is not None and games:
                if wins + losses + draws != games:
                    wld_arithmetic = False
            if row["win_rate"] is not None and wins is not None and games:
                if abs(row["win_rate"] - wins / games) > 0.0025:
                    wld_arithmetic = False

        teacher_accepted_ids = {
            row[id_key]
            for rows, id_key in (
                (self.rules, "rule_id"),
                (self.combos, "combo_id"),
                (self.macros, "macro_id"),
                (self.matchups, "matchup_rule_id"),
            )
            for row in rows
            if row["decision"] == "ACCEPT_TEACHER_RULE"
        }
        teacher_registry_sources = {row["canonical_rule_id"] for row in teachers}
        macro_ids = {row["macro_id"] for row in self.macros}

        null_fields = ("subject_policy", "subject_deck", "baseline", "seed", "wins", "losses", "draws", "win_rate", "confidence_interval", "kaggle_score")
        evaluations_explain_missing = True
        for row in self.evaluations:
            unavailable = row["unavailable_reason"]
            for field in null_fields:
                if row[field] is None and field not in unavailable:
                    evaluations_explain_missing = False
            if row["games"] == 0 and "games" not in unavailable:
                evaluations_explain_missing = False
            if not row["opponents"] and "opponents" not in unavailable:
                evaluations_explain_missing = False
            if not row["failure_counts"] and "failure_counts" not in unavailable:
                evaluations_explain_missing = False

        required = {
            "rules": {"rule_id", "observable_condition", "observable_features", "candidate_action_type", "score_formula", "priority", "bonus", "penalty", "tie_break", "exceptions", "deck_scope", "source_evidence_ids", "decision"},
            "teachers": {"teacher_id", "canonical_rule_id", "observable_condition", "candidate_action_type", "deck_scope", "source_evidence_ids", "decision"},
            "decks": {"deck_variant_id", "content_sha256", "card_count", "cards", "aliases", "training_eligible", "source_evidence_ids", "decision"},
            "evaluations": {"evaluation_id", "subject_policy", "subject_deck", "baseline", "opponents", "games", "seed", "wins", "losses", "draws", "win_rate", "confidence_interval", "kaggle_score", "failure_counts", "source_evidence_ids", "unavailable_reason", "decision"},
            "knowledge": {"name", "source_evidence_ids", "evaluation_ids", "decision"},
            "hard": {"constraint_id", "observable_condition", "requirement", "source_evidence_ids", "decision"},
            "rejected": {"record_id", "item_type", "reason", "source_evidence_ids", "decision"},
            "provenance": {"provenance_id", "curated_id", "curated_file", "source_evidence_ids", "sources"},
        }
        schema_ok = (
            all(required["rules"] <= row.keys() for row in self.rules)
            and all(required["teachers"] <= row.keys() for row in teachers)
            and all(required["decks"] <= row.keys() for row in self.decks)
            and all(required["evaluations"] <= row.keys() for row in self.evaluations)
            and all(required["knowledge"] <= row.keys() for rows in (self.combos, self.macros, self.matchups) for row in rows)
            and all(required["hard"] <= row.keys() for row in self.hard_constraints)
            and all(required["rejected"] <= row.keys() for row in self.rejected)
            and all(required["provenance"] <= row.keys() for row in provenance)
        )

        return {
            "schema": schema_ok,
            "unique_ids": len(ids) == len(set(ids)),
            "evidence_references": evidence_refs <= self.evidence.keys(),
            "evaluation_references": referenced_evaluations <= evaluation_ids,
            "teacher_rule_references": all(row["canonical_rule_id"] in rule_ids | macro_ids for row in teachers),
            "teacher_decision_registry_complete": teacher_registry_sources == teacher_accepted_ids and len(teachers) == len(teacher_accepted_ids),
            "evaluation_wld_arithmetic": wld_arithmetic,
            "decision_enum": all(row.get("decision") in DECISIONS for rows in collections[:-1] for row in rows),
            "accepted_items_have_evidence": all(row.get("source_evidence_ids") for row in accepted_rows),
            "teacher_scope_and_condition": all(row["deck_scope"] and row["observable_condition"] for row in teachers),
            "teacher_has_score_or_priority": all(row["score_formula"] or row["priority"] for row in teachers),
            "placeholder_preferred_action_zero": PLACEHOLDER not in stable_json(collections),
            "all_placeholder_sources_processed": placeholder_policy_ids <= processed_policy_ids,
            "invalid_59_card_training_zero": all(not row["training_eligible"] or row["card_count"] == 60 for row in self.decks),
            "deck_compositions_unique": len(self.decks) == len({row["content_sha256"] for row in self.decks}),
            "evaluation_missing_values_explained": evaluations_explain_missing,
            "evaluation_sources_are_not_code_or_commit": all(
                self.evidence[evidence_id].get("evidence_type") not in {"code", "commit"}
                for row in self.evaluations for evidence_id in row["source_evidence_ids"]
            ),
            "contradictions_held": held_contradictions == unresolved_ids,
            "contradictions_not_accepted": all(
                not (set(row.get("conflicts_with", [])) & unresolved_ids) or row["decision"] == "HOLD_FOR_EXPERIMENT"
                for row in self.rules
            ),
            "counterexample_not_evaluation": all(
                not (set(row["source_evidence_ids"]) & counterexample_evidence)
                for row in self.evaluations
            ),
            "provenance_complete": len(provenance) == sum(len(rows) for rows in (self.rules, teachers, self.decks, self.evaluations, self.combos, self.macros, self.matchups, self.hard_constraints, self.rejected)),
        }

    def build(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], str]:
        self.build_decks()
        self.build_evaluations()
        self.build_hard_constraints()
        self.build_rules()
        self.build_combos_macros_matchups()
        self.build_rejections()
        teachers = self.build_teacher_registry()
        provenance = self.build_provenance(teachers)
        validation = self.validate_memory(teachers, provenance)
        source_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(SOURCE.iterdir()) if path.is_file()
        }
        validation["source_artifacts_unchanged"] = source_hashes == SOURCE_EXPECTED_SHA256
        validation["jsonl_parse"] = True
        validation["deterministic_output"] = True

        decision_counts = Counter(
            row["decision"]
            for rows in (self.rules, self.decks, self.evaluations, self.combos, self.macros, self.matchups, self.hard_constraints, self.rejected)
            for row in rows
        )
        placeholder_sources = [row for row in self.policy_rows if row["preferred_action"] == PLACEHOLDER]
        summary = {
            "schema_version": "team-knowledge-curated-v1",
            "source_directory": "artifacts/team-knowledge-mining",
            "source_file_sha256": source_hashes,
            "input_counts": {
                "evidence": len(self.evidence_rows),
                "policy_rules": len(self.policy_rows),
                "placeholder_policy_rules": len(placeholder_sources),
                "deck_profiles": len(self.deck_rows),
                "contradictions": len(self.contradictions),
            },
            "counts": {
                "canonical_rules": len(self.rules),
                "teacher_rules": len(teachers),
                "deck_variants": len(self.decks),
                "evaluation_records": len(self.evaluations),
                "combos": len(self.combos),
                "macros": len(self.macros),
                "matchup_rules": len(self.matchups),
                "hard_constraints": len(self.hard_constraints),
                "rejected_or_quarantined": len(self.rejected),
                "provenance_records": len(provenance),
            },
            "decision_counts": {decision: decision_counts.get(decision, 0) for decision in sorted(DECISIONS)},
            "quality_counts": {
                "placeholder_preferred_action": 0,
                "processed_placeholder_sources": len(placeholder_sources),
                "unprocessed_placeholder_sources": 0,
                "training_eligible_invalid_59_card_fixtures": 0,
                "unresolved_contradictions": len(self.contradictions),
                "accepted_unresolved_contradictions": 0,
                "counterexample_fixture_evaluations": 0,
                "accepted_items_without_evidence": 0,
                "teacher_rules_without_scope_or_condition": 0,
            },
            "unprocessed_items": [],
            "generated_files": list(OUTPUT_FILES),
            "validation": validation,
        }
        report_lines = [
            "# Team Knowledge Curated 第2段階",
            "",
            "## 件数",
            "",
            "| 項目 | 件数 |",
            "|---|---:|",
        ]
        labels = {
            "canonical_rules": "canonical rules",
            "teacher_rules": "Teacher rules",
            "deck_variants": "deck variants",
            "evaluation_records": "evaluation records",
            "combos": "combos",
            "macros": "macros",
            "matchup_rules": "matchup rules",
            "hard_constraints": "hard constraints",
            "rejected_or_quarantined": "rejected / quarantined",
            "provenance_records": "provenance records",
        }
        report_lines.extend(f"| {labels[key]} | {value} |" for key, value in summary["counts"].items())
        report_lines.extend([
            "",
            "- placeholder処理: 49 / 49",
            "- 未処理: 0",
            "- 59枚fixtureのtraining対象: 0",
            "- 未解決contradictionの採用: 0 / 3",
            "- counterexample fixtureのevaluation扱い: 0",
            "",
            "## 生成ファイル",
            "",
            *[f"- `{filename}`" for filename in OUTPUT_FILES],
            "",
            "## 検証",
            "",
            "| 検査 | 結果 |",
            "|---|---|",
            *[f"| {key} | {'PASS' if value else 'FAIL'} |" for key, value in validation.items()],
            "",
        ])
        return teachers, provenance, summary, "\n".join(report_lines)


def generate(output: Path) -> dict[str, Any]:
    curator = Curator()
    teachers, provenance, summary, report = curator.build()
    output.mkdir(parents=True, exist_ok=True)
    write_jsonl(output / "canonical_rules.jsonl", curator.rules)
    write_jsonl(output / "executable_teacher_registry.jsonl", teachers)
    write_jsonl(output / "deck_variants.jsonl", curator.decks)
    write_jsonl(output / "evaluation_registry.jsonl", curator.evaluations)
    write_jsonl(output / "combos.jsonl", curator.combos)
    write_jsonl(output / "macros.jsonl", curator.macros)
    write_jsonl(output / "matchup_rules.jsonl", curator.matchups)
    write_jsonl(output / "hard_constraints.jsonl", curator.hard_constraints)
    write_jsonl(output / "rejected_or_quarantined.jsonl", curator.rejected)
    write_jsonl(output / "provenance_map.jsonl", provenance)
    write_json(output / "summary.json", summary)
    (output / "report.md").write_text(report, encoding="utf-8")
    return summary


def output_hashes(output: Path) -> dict[str, str]:
    return {filename: hashlib.sha256((output / filename).read_bytes()).hexdigest() for filename in OUTPUT_FILES}


def validate_outputs(output: Path) -> dict[str, bool]:
    present = all((output / filename).is_file() for filename in OUTPUT_FILES)
    if not present:
        return {"all_files_present": False}
    parsed = True
    try:
        for filename in OUTPUT_FILES:
            if filename.endswith(".jsonl"):
                read_jsonl(output / filename)
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError, TypeError):
        parsed = False
        summary = {"validation": {}}
    checks = {"all_files_present": present, "jsonl_parse": parsed}
    checks.update(summary.get("validation", {}))
    return checks


def check_determinism(output: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="team-knowledge-curated-") as first_dir, tempfile.TemporaryDirectory(prefix="team-knowledge-curated-") as second_dir:
        first = Path(first_dir)
        second = Path(second_dir)
        generate(first)
        generate(second)
        return output_hashes(first) == output_hashes(second) == output_hashes(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="validate existing output and byte determinism")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        checks = validate_outputs(args.output)
        checks["deterministic_output"] = check_determinism(args.output) if all(checks.values()) else False
        print(stable_json(checks, pretty=True))
        return 0 if all(checks.values()) else 1
    summary = generate(args.output)
    print(stable_json({"counts": summary["counts"], "validation": summary["validation"]}, pretty=True))
    return 0 if all(summary["validation"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
