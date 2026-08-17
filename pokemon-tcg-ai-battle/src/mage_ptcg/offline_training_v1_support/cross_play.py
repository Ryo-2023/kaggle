"""Cross-play evaluation matrix generator.

Aggregates outcomes into policy-vs-policy matrices and formats them.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError
from mage_ptcg.offline_training_v1_support.statistics import wilson_score_interval


def generate_cross_play_report(games: Iterable[dict[str, Any]], draw_weight: float = 0.5) -> dict[str, Any]:
    """Compile multi-dimensional matrices analyzing policy pairings."""
    game_list = list(games)

    policies = set()
    decks = set()

    # Nested mapping: candidate_policy -> opponent_policy -> metrics
    raw_cells = defaultdict(lambda: defaultdict(lambda: {
        "games": 0, "wins": 0, "losses": 0, "draws": 0,
        "invalid": 0, "crash": 0, "timeout": 0, "fallback": 0,
        "legal_sum": 0.0,
    }))

    # Seat-specific cells
    seat_cells = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {
        "games": 0, "wins": 0, "losses": 0, "draws": 0
    })))

    # Deck-specific cells
    deck_cells = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {
        "games": 0, "wins": 0, "losses": 0, "draws": 0
    })))

    for g in game_list:
        cp = g.get("candidate_policy_id")
        op = g.get("opponent_policy_id")
        if not cp or not op:
            continue
        policies.add(cp)
        policies.add(op)

        winner = g.get("winner")
        seat = int(g.get("candidate_seat", 0))
        c_deck = g.get("candidate_deck_id", "default_deck")
        o_deck = g.get("opponent_deck_id", "default_deck")

        decks.add(c_deck)
        decks.add(o_deck)

        is_invalid = bool(g.get("invalid", False))
        is_crash = bool(g.get("crash", False))
        is_timeout = bool(g.get("timeout", False))
        fallback = int(g.get("candidate_fallback_count", 0))
        legal_rate = float(g.get("candidate_legal_rate", 1.0))

        # Update base cells
        cell = raw_cells[cp][op]
        cell["games"] += 1
        if winner == "candidate":
            cell["wins"] += 1
        elif winner == "opponent":
            cell["losses"] += 1
        else:
            cell["draws"] += 1

        if is_invalid:
            cell["invalid"] += 1
        if is_crash:
            cell["crash"] += 1
        if is_timeout:
            cell["timeout"] += 1
        cell["fallback"] += fallback
        cell["legal_sum"] += legal_rate

        # Update seat cells
        scell = seat_cells[seat][cp][op]
        scell["games"] += 1
        if winner == "candidate":
            scell["wins"] += 1
        elif winner == "opponent":
            scell["losses"] += 1
        else:
            scell["draws"] += 1

        # Update deck cells
        dcell = deck_cells[c_deck][cp][op]
        dcell["games"] += 1
        if winner == "candidate":
            dcell["wins"] += 1
        elif winner == "opponent":
            dcell["losses"] += 1
        else:
            dcell["draws"] += 1

    sorted_policies = sorted(policies)
    sorted_decks = sorted(decks)

    # Helper to construct a matrix dict
    def make_matrix(cell_source: Any, key_val: str = "win_rate", seat: int | None = None, deck_id: str | None = None) -> dict[str, dict[str, Any]]:
        matrix = {}
        for cp in sorted_policies:
            matrix[cp] = {}
            for op in sorted_policies:
                if cp == op:
                    matrix[cp][op] = "NO_DATA"
                    continue

                if seat is not None:
                    src = cell_source[seat][cp][op]
                elif deck_id is not None:
                    src = cell_source[deck_id][cp][op]
                else:
                    src = cell_source[cp][op]

                n = src["games"]
                if n == 0:
                    matrix[cp][op] = "NO_DATA"
                    continue

                if key_val == "win_rate":
                    matrix[cp][op] = (src["wins"] + draw_weight * src["draws"]) / n
                elif key_val == "wilson":
                    matrix[cp][op] = list(wilson_score_interval(src["wins"], src["losses"], src["draws"], draw_weight))
                elif key_val == "legal_rate":
                    matrix[cp][op] = src["legal_sum"] / n
                elif key_val == "fallback_rate":
                    matrix[cp][op] = src["fallback"] / n
                else:
                    matrix[cp][op] = src.get(key_val, 0)
        return matrix

    report = {
        "schema_version": "support-cross-play-v1",
        "policies": sorted_policies,
        "matrices": {
            "game_count": make_matrix(raw_cells, "games"),
            "wins": make_matrix(raw_cells, "wins"),
            "draws": make_matrix(raw_cells, "draws"),
            "invalid": make_matrix(raw_cells, "invalid"),
            "crash": make_matrix(raw_cells, "crash"),
            "timeout": make_matrix(raw_cells, "timeout"),
            "fallback": make_matrix(raw_cells, "fallback"),
            "legal_rate": make_matrix(raw_cells, "legal_rate"),
            "fallback_rate": make_matrix(raw_cells, "fallback_rate"),
            "win_rate": make_matrix(raw_cells, "win_rate"),
            "wilson_interval": make_matrix(raw_cells, "wilson"),
            "seat_0_win_rate": make_matrix(seat_cells, "win_rate", seat=0),
            "seat_1_win_rate": make_matrix(seat_cells, "win_rate", seat=1),
        },
        "deck_matrices": {
            did: {
                "win_rate": make_matrix(deck_cells, "win_rate", deck_id=did),
                "game_count": make_matrix(deck_cells, "games", deck_id=did),
            } for did in sorted_decks
        }
    }

    return report


def format_cross_play_markdown(report: dict[str, Any]) -> str:
    """Format the cross-play report into a markdown summary."""
    policies = report.get("policies", [])
    matrices = report.get("matrices", {})
    wr_matrix = matrices.get("win_rate", {})
    count_matrix = matrices.get("game_count", {})

    lines = []
    lines.append("# Cross-Play Win Rate Matrix")
    lines.append("")
    # Table header
    header = "| Candidate \\ Opponent | " + " | ".join(policies) + " |"
    divider = "|---|" + "|---|".join("" for _ in policies) + "|"
    lines.append(header)
    lines.append(divider)

    for cp in policies:
        row = [cp]
        for op in policies:
            val = wr_matrix.get(cp, {}).get(op, "NO_DATA")
            cnt = count_matrix.get(cp, {}).get(op, 0)
            if val == "NO_DATA":
                row.append("-")
            else:
                row.append(f"{val:.2%} ({cnt} games)")
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def format_cross_play_csv(report: dict[str, Any]) -> str:
    """Export the win rate matrix as a CSV string."""
    policies = report.get("policies", [])
    wr_matrix = report.get("matrices", {}).get("win_rate", {})

    lines = []
    lines.append("Candidate/Opponent," + ",".join(policies))
    for cp in policies:
        row = [cp]
        for op in policies:
            val = wr_matrix.get(cp, {}).get(op, "NO_DATA")
            if val == "NO_DATA":
                row.append("")
            else:
                row.append(f"{val:.4f}")
        lines.append(",".join(row))

    return "\n".join(lines)
