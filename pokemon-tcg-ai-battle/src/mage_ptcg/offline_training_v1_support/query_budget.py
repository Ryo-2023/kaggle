"""Query budget allocation module for teacher calls.

Distributes queries across teachers based on hard-state priority,
context rarity, teacher costs, and maximum round budgets.
"""

from __future__ import annotations

from typing import Any, Sequence

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError

class QueryBudgetAllocator:
    """Allocates teacher query budgets to decisions to optimize sample value."""

    def allocate(
        self,
        records: Sequence[dict[str, Any]],
        teachers: dict[str, dict[str, Any]], # teacher_id -> { 'cost': float, 'confidence': float, 'cap': int }
        round_budget: float,
    ) -> dict[str, Any]:
        """Compute query assignments for decision records within round budget limit."""
        if round_budget < 0.0:
            raise SupportContractError(f"Round budget cannot be negative: {round_budget}")

        for t_id, info in teachers.items():
            cost = info.get("cost", 0.0)
            if cost < 0.0:
                raise SupportContractError(f"Teacher cost cannot be negative: {t_id}")

        # Compute priority score for each record without accessing private hand/observations
        # Sort records based on priority (hard_state_priority, context_rarity)
        sorted_records = []
        for r in records:
            # Shield raw private state from query plan
            # Extract safe references
            rec_ref = {
                "decision_id": r.get("decision_id"),
                "episode_id": r.get("episode_id"),
            }
            if not rec_ref["decision_id"]:
                continue

            # Calculate priority
            hard_score = r.get("priority_score", 0.0)
            rarity = 1.5 if r.get("selection_type") in ("rare_select", "special_select") else 1.0
            total_priority = hard_score * rarity
            sorted_records.append((total_priority, r["decision_id"], rec_ref))

        # Sort descending by priority
        sorted_records.sort(key=lambda x: x[0], reverse=True)

        query_plan = []
        assigned_decisions = {}
        teacher_caps_remaining = {t_id: info.get("cap", 999999) for t_id, info in teachers.items()}
        remaining_budget = round_budget
        estimated_cost = 0.0

        reason_decomposition = {
            "hard_state": 0,
            "rare_context": 0,
            "uniform": 0,
        }

        # Greedy allocation
        for priority, dec_id, ref in sorted_records:
            # Find the best available teacher for this record
            # Best = highest confidence that fits budget and cap constraints
            best_teacher = None
            best_conf = -1.0
            best_cost = 0.0

            for t_id, info in teachers.items():
                cost = info.get("cost", 0.0)
                conf = info.get("confidence", 0.0)
                cap = teacher_caps_remaining[t_id]

                if cap > 0 and cost <= remaining_budget:
                    if conf > best_conf:
                        best_teacher = t_id
                        best_conf = conf
                        best_cost = cost

            if best_teacher:
                remaining_budget -= best_cost
                estimated_cost += best_cost
                teacher_caps_remaining[best_teacher] -= 1

                assigned_decisions[dec_id] = best_teacher
                query_plan.append({
                    "decision_id": dec_id,
                    "episode_id": ref["episode_id"],
                    "assigned_teacher": best_teacher,
                    "cost": best_cost,
                })

                # Decompose reasons
                if priority > 0.0:
                    reason_decomposition["hard_state"] += 1
                else:
                    reason_decomposition["uniform"] += 1
            else:
                # No budget or cap left for this record
                pass

        unassigned = [dec_id for _, dec_id, _ in sorted_records if dec_id not in assigned_decisions]

        return {
            "query_plan": query_plan,
            "selected_decision_ids": sorted(list(assigned_decisions.keys())),
            "teacher_assignments": assigned_decisions,
            "estimated_cost": estimated_cost,
            "reason_decomposition": reason_decomposition,
            "unassigned_records": sorted(unassigned),
            "budget_utilization": estimated_cost / round_budget if round_budget > 0.0 else 0.0,
        }
