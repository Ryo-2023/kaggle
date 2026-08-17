"""Teacher ensemble aggregation offline utility.

Combines actions from multiple teachers using majority, confidence-weighted,
or fallback chain voting with stable tie-breaking and abstention support.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError

class TeacherEnsemble:
    """Combines predictions from multiple teacher agents offline."""

    def aggregate_votes(
        self,
        teacher_outputs: dict[str, dict[str, dict[str, Any]]], # decision_id -> { teacher_id -> { 'action': str, 'confidence': float, 'rank': int } }
        method: str = "majority",
        abstention_threshold: float = 0.0,
    ) -> dict[str, Any]:
        """Aggregate votes for each decision_id using specified method."""
        valid_methods = {"majority", "confidence_weighted", "fallback_chain"}
        if method not in valid_methods:
            raise SupportContractError(f"Unsupported ensemble method: {method}")

        results = {}
        conflicts = {}

        for dec_id, votes in teacher_outputs.items():
            if not votes:
                results[dec_id] = {
                    "action": None,
                    "status": "ABSTAINED",
                    "reason": "no_votes",
                    "teachers_participated": [],
                }
                continue

            # Gather valid votes
            valid_votes = {}
            participating_teachers = []
            for t_id, vote_data in votes.items():
                if not vote_data or "action" not in vote_data:
                    continue
                action = vote_data["action"]
                if action is None:
                    continue
                # Reject fabricated or unsupported score structures
                conf = vote_data.get("confidence", 1.0)
                if not isinstance(conf, (int, float)) or conf < 0.0 or conf > 1.0:
                    continue # Ignore invalid scores
                valid_votes[t_id] = {
                    "action": action,
                    "confidence": conf,
                    "rank": vote_data.get("rank", 1),
                }
                participating_teachers.append(t_id)

            if not valid_votes:
                results[dec_id] = {
                    "action": None,
                    "status": "ABSTAINED",
                    "reason": "no_valid_votes",
                    "teachers_participated": [],
                }
                continue

            # Apply aggregation method
            selected_action = None
            status = "AGREED"

            if method == "majority":
                action_counts = defaultdict(int)
                for v in valid_votes.values():
                    action_counts[v["action"]] += 1

                # Find max votes
                max_votes = max(action_counts.values())
                candidates = [act for act, cnt in action_counts.items() if cnt == max_votes]

                # Stable ActionKey tie-break: Sort alphabetically and choose the first
                candidates.sort()
                selected_action = candidates[0]

                # Conflict detection: if multiple candidates had max votes or there was disagreement
                if len(action_counts) > 1:
                    status = "CONFLICT_RESOLVED"
                    conflicts[dec_id] = list(action_counts.keys())

                # Abstention check
                total_votes = sum(action_counts.values())
                if (max_votes / total_votes) < abstention_threshold:
                    selected_action = None
                    status = "ABSTAINED"

            elif method == "confidence_weighted":
                action_scores = defaultdict(float)
                for v in valid_votes.values():
                    action_scores[v["action"]] += v["confidence"]

                max_score = max(action_scores.values())
                candidates = [act for act, score in action_scores.items() if abs(score - max_score) < 1e-9]
                candidates.sort()
                selected_action = candidates[0]

                if len(action_scores) > 1:
                    status = "CONFLICT_RESOLVED"
                    conflicts[dec_id] = list(action_scores.keys())

                # Abstention check using weight proportion
                total_score = sum(action_scores.values())
                if total_score > 0.0 and (max_score / total_score) < abstention_threshold:
                    selected_action = None
                    status = "ABSTAINED"

            elif method == "fallback_chain":
                # Order teachers and pick the first available. Sort by teacher ID to make it deterministic.
                sorted_teachers = sorted(list(valid_votes.keys()))
                selected_action = valid_votes[sorted_teachers[0]]["action"]
                status = "FALLBACK"

            results[dec_id] = {
                "action": selected_action,
                "status": status,
                "teachers_participated": sorted(participating_teachers),
            }

        return {
            "results": results,
            "conflicts": conflicts,
        }
