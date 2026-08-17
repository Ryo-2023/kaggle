"""登録済みの相手 1 体を、指定した相手集合に対して座席均等で測る。

正典 §13 の `local_strength_band` と同じ考え方で、**実測でのみ**強さを与える。
Kaggle の medal や μ は時期の異なる提出間で公平な絶対値ではないため、採用判断へ
そのまま持ち込まない。

座席は必ず均等に振る。先手の価値が大きいため、座席を揃えない勝率は方策ではなく
座席を測ってしまう (`calibration_v1` の契約と同じ理由)。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import (
    seed_agent_randomness_v1,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1
from scripts.test_sim import run_match


def _checkpoint_subject(args):
    """Bind a trained checkpoint as the subject.

    Imported lazily: torch must not be pulled in for the far more common case of
    measuring a rule-based pool member.
    """
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        _NeuralAgentPolicyFactoryV1,
        _build_actor_pool_deck_binding_v1,
        neural_checkpoint_behavior_identity_v1,
    )
    from mage_ptcg.meta_specialist.neural_policy_v1 import (
        load_specialist_neural_policy_from_checkpoint_v1,
    )
    from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent

    identity = neural_checkpoint_behavior_identity_v1(args.subject_checkpoint)
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=args.subject_archetype_id,
        deck_csv_path=Path(args.subject_deck_csv),
        source_commit="0" * 40,
    )
    policy = load_specialist_neural_policy_from_checkpoint_v1(
        args.subject_checkpoint, expected_content_hash=identity,
        # Must be a SHA-256 digest: `PolicyTelemetrySnapshot` validates it.
        # A descriptive string here made every game fail the runtime's telemetry
        # contract, which the old bare `except` reported only as a fault count.
        checkpoint_lineage_id=deck_lock.policy_lineage_id,
    )
    constraints = RuntimeConstraintManifest.frozen_v1()

    def factory(_deck, seed):
        # The same construction the actor pool uses.  An earlier version called a
        # `policy.build_agent()` that does not exist on SpecialistNeuralPolicyV1;
        # every game raised AttributeError and was counted as a fault, so every
        # checkpoint measured 0 games played and scored 0.000 with a [0,1]
        # interval -- a number that looked like a result and was not one.
        binding = make_agent(
            deck_asset=qualified, deck_lock=deck_lock, vocabulary=vocabulary,
            policy_factory=_NeuralAgentPolicyFactoryV1(
                policy=policy, decoding_mode="greedy", sampling_seed=seed,
            ),
            expected_policy_identity=identity, constraints=constraints,
        )
        return binding.agent

    return args.subject_deck_csv, factory, f"checkpoint-{identity[:12]}"


def _wilson(wins: float, games: int, z: float = 1.959964) -> tuple[float, float]:
    if games == 0:
        return 0.0, 1.0
    p = wins / games
    d = 1 + z * z / games
    centre = (p + z * z / (2 * games)) / d
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject-id", default="",
                        help="登録済み相手を subject にする場合の ID")
    parser.add_argument("--subject-checkpoint", default="",
                        help="学習済み checkpoint を subject にする場合のパス")
    parser.add_argument("--subject-deck-csv", default="",
                        help="--subject-checkpoint と併用")
    parser.add_argument("--subject-archetype-id", default="",
                        help="--subject-checkpoint と併用")
    parser.add_argument("--opponent-ids", default="",
                        help="カンマ区切り。省略時は --opponents-from を使う")
    parser.add_argument("--opponents-from", default="",
                        help="teacher_dataset_manifest.json のパス。opponent_ids を読む")
    parser.add_argument("--games-per-opponent-seat", type=int, default=4)
    parser.add_argument("--base-seed", type=int, default=9100000)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    if args.opponents_from:
        manifest = json.loads(Path(args.opponents_from).read_text(encoding="utf-8"))
        opponent_ids = tuple(manifest["opponent_ids"])
    else:
        opponent_ids = tuple(sorted(
            {x.strip() for x in args.opponent_ids.split(",") if x.strip()}
        ))
    if not opponent_ids:
        raise SystemExit("give --opponent-ids or --opponents-from")

    if bool(args.subject_id) == bool(args.subject_checkpoint):
        raise SystemExit("give exactly one of --subject-id or --subject-checkpoint")

    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    if args.subject_id:
        subject_instance = resolve_opponent_v1(pool, args.subject_id, subject_deck_csv_path="x")
        subject_deck_path = subject_instance.deck_csv_path
        subject_factory = build_opponent_agent_factory_v1(subject_instance)
        subject_label = args.subject_id
    else:
        if not (args.subject_deck_csv and args.subject_archetype_id):
            raise SystemExit(
                "--subject-checkpoint requires --subject-deck-csv and --subject-archetype-id"
            )
        subject_deck_path, subject_factory, subject_label = _checkpoint_subject(args)

    total = len(opponent_ids) * 2 * args.games_per_opponent_seat
    reporter = ProgressReporterV1(total=total, desc=f"measure {subject_label}")
    reporter.note(f"[measure] subject={subject_label} opponents={len(opponent_ids)} games={total}")

    per_opponent: dict[str, dict[str, int]] = {}
    fault_reasons: dict[str, int] = {}
    wins = draws = losses = faults = 0
    seat_wins = {0: 0, 1: 0}
    seat_games = {0: 0, 1: 0}
    started = time.time()
    out_root = Path("runs/meta-specialist-strength") / re.sub(r"[^A-Za-z0-9._-]", "_", subject_label)

    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        row = per_opponent.setdefault(opponent_id, {"w": 0, "d": 0, "l": 0, "f": 0})
        for seat in (0, 1):
            for index in range(args.games_per_opponent_seat):
                first = seat == 0
                seed_agent_randomness_v1(args.base_seed + index)
                try:
                    result = run_match(
                        deck_a_path=subject_deck_path if first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if first else subject_deck_path,
                        agent_a_name="a", agent_b_name="b",
                        seed=args.base_seed + index, max_steps=2000,
                        output_dir=str(out_root / f"{opponent_id}-{seat}-{index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if first else opponent_factory,
                        agent_b_factory=opponent_factory if first else subject_factory,
                    )
                except Exception as exc:
                    # Record why.  Swallowing the reason hid an AttributeError in
                    # the subject factory for an entire run: 36/36 games faulted
                    # and the report said `score_rate: 0.0` with no indication
                    # that nothing had been played.
                    faults += 1; row["f"] += 1
                    reason = f"{type(exc).__name__}: {exc}"
                    fault_reasons[reason] = fault_reasons.get(reason, 0) + 1
                    reporter.update(1, faults=faults); continue
                if result.get("status") != "DONE":
                    faults += 1; row["f"] += 1
                    reporter.update(1, faults=faults); continue
                winner = result.get("winner")
                seat_games[seat] += 1
                if winner == 2:
                    draws += 1; row["d"] += 1
                elif winner == seat:
                    wins += 1; row["w"] += 1; seat_wins[seat] += 1
                else:
                    losses += 1; row["l"] += 1
                played = wins + draws + losses
                reporter.update(1, win=wins, loss=losses, draw=draws, faults=faults,
                                rate=(wins + 0.5 * draws) / played if played else 0.0)
    reporter.close()

    played = wins + draws + losses
    score = (wins + 0.5 * draws) / played if played else 0.0
    low, high = _wilson(wins + 0.5 * draws, played)
    payload = {
        "subject_id": subject_label,
        "opponent_ids": list(opponent_ids),
        "games_played": played,
        "faults": faults,
        "fault_reasons": dict(sorted(fault_reasons.items(), key=lambda kv: -kv[1])),
        "wins": wins, "draws": draws, "losses": losses,
        "score_rate": score,
        "score_ci95": [low, high],
        "seat_score": {
            str(seat): (seat_wins[seat] / seat_games[seat]) if seat_games[seat] else None
            for seat in (0, 1)
        },
        "elapsed_seconds": round(time.time() - started, 1),
        "per_opponent": per_opponent,
    }
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "per_opponent"},
                     ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
