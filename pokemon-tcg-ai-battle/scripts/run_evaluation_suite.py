"""正典 §14.0 の ascent_suite / top_band_suite を実測で回す。

band は `run_opponent_calibration.py` が出した `local_strength_manifest.json` から
だけ読む。出所やメダルから band を推定しない (正典 §13、`calibration_v1`)。

subject は次のどちらかを指定する。

- `--subject-opponent-id`: 登録済み相手を subject として回す。checkpoint が無い
  段階で suite 経路そのものを実測できる。
- `--subject-checkpoint` + `--subject-deck-csv`: 学習済み policy を評価する。

両 suite とも座席を均等に振る。going first の価値が大きいため、座席を揃えない
score は policy ではなく座席を測ってしまう (`calibration_v1` の契約と同じ理由)。

Kaggle への提出は一切行わない。この runner は測定と報告だけを行う。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.curriculum_opponents_v1 import band_map_from_manifest_v1
from mage_ptcg.meta_specialist.evaluation_suites_v1 import (
    INFRASTRUCTURE_FAULT_V1,
    SuiteGameResultV1,
    build_ascent_suite_v1,
    build_top_band_suite_v1,
    run_evaluation_suite_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1
from scripts.test_sim import run_match


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _subject_binding(args, pool):
    """Return ``(deck_csv_path, agent_factory, subject_identity)``."""
    if args.subject_opponent_id:
        subject = resolve_opponent_v1(pool, args.subject_opponent_id, subject_deck_csv_path="x")
        return (
            subject.deck_csv_path,
            build_opponent_agent_factory_v1(subject),
            f"opponent:{args.subject_opponent_id}",
        )
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        neural_checkpoint_behavior_identity_v1,
    )
    from mage_ptcg.meta_specialist.neural_policy_v1 import (
        load_specialist_neural_policy_from_checkpoint_v1,
    )

    identity = neural_checkpoint_behavior_identity_v1(args.subject_checkpoint)
    policy = load_specialist_neural_policy_from_checkpoint_v1(
        args.subject_checkpoint, expected_content_hash=identity,
        checkpoint_lineage_id=args.subject_lineage_id,
    )

    def factory(_deck, _seed):
        return policy.build_agent()

    return args.subject_deck_csv, factory, f"checkpoint:{identity[:16]}"


def _play_block(*, subject, opponents, pool, games_per_opponent_seat, base_seed, out_dir, reporter):
    """Seat-balanced games against every opponent in one block."""
    deck_path, subject_factory, _identity = subject
    results: list[SuiteGameResultV1] = []
    for opponent_id in opponents:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for index in range(games_per_opponent_seat):
                subject_first = seat == 0
                seed = base_seed + index
                started = time.time()
                fault = ""
                score = 0.0
                try:
                    result = run_match(
                        deck_a_path=deck_path if subject_first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if subject_first else deck_path,
                        agent_a_name="a", agent_b_name="b",
                        seed=seed, max_steps=2000,
                        output_dir=str(out_dir / f"{opponent_id}-{seat}-{index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if subject_first else opponent_factory,
                        agent_b_factory=opponent_factory if subject_first else subject_factory,
                    )
                except Exception:
                    # Engine / runner level failure: §14.3 classifies this apart
                    # from a logical fault and re-runs the block for both sides.
                    fault = INFRASTRUCTURE_FAULT_V1
                    result = {}
                if not fault and result.get("status") != "DONE":
                    fault = INFRASTRUCTURE_FAULT_V1
                if not fault:
                    winner = result.get("winner")
                    if winner == 2:
                        score = 0.5
                    elif winner == seat:
                        score = 1.0
                    else:
                        score = 0.0
                results.append(SuiteGameResultV1(
                    opponent_id=opponent_id, opponent_version=opponent.policy_hash[:16],
                    seat=seat, scenario_seed=seed, score=score, fault=fault,
                    decision_latencies_ms=((time.time() - started) * 1000.0,),
                ))
                reporter.update(1, opponent=opponent_id, faults=sum(
                    1 for item in results if item.fault
                ))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strength-manifest", required=True,
                        help="run_opponent_calibration.py が出した local_strength_manifest.json")
    parser.add_argument("--suite", choices=("ascent_suite", "top_band_suite"), required=True)
    parser.add_argument("--subject-opponent-id", default="")
    parser.add_argument("--subject-checkpoint", default="")
    parser.add_argument("--subject-deck-csv", default="")
    parser.add_argument("--subject-lineage-id", default="evaluation-suite-v1")
    parser.add_argument("--historical-champions", default="",
                        help="top_band_suite 用。カンマ区切りの opponent id")
    parser.add_argument("--unused-exploiters", default="")
    parser.add_argument("--games-per-opponent-seat", type=int, default=8)
    parser.add_argument("--base-seed", type=int, default=8800000)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--output-base", default="runs/meta-specialist-evaluation")
    args = parser.parse_args()

    if bool(args.subject_opponent_id) == bool(args.subject_checkpoint):
        raise SystemExit("give exactly one of --subject-opponent-id or --subject-checkpoint")
    if args.subject_checkpoint and not args.subject_deck_csv:
        raise SystemExit("--subject-checkpoint requires --subject-deck-csv")

    manifest = json.loads(Path(args.strength_manifest).read_text(encoding="utf-8"))
    band_map = band_map_from_manifest_v1(manifest)
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    available = tuple(sorted(pool))

    def split(value: str) -> tuple[str, ...]:
        return tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))

    if args.suite == "ascent_suite":
        suite = build_ascent_suite_v1(band_map=band_map, available=available)
    else:
        suite = build_top_band_suite_v1(
            band_map=band_map, available=available,
            historical_champions=split(args.historical_champions),
            unused_exploiters=split(args.unused_exploiters),
        )

    out = Path(args.output_base) / args.run_name
    out.mkdir(parents=True, exist_ok=True)
    subject = _subject_binding(args, pool)
    total = sum(
        len(block.opponent_ids) * 2 * args.games_per_opponent_seat for block in suite.blocks
    )
    reporter = ProgressReporterV1(total=total, desc=f"{args.suite} {args.run_name}")
    reporter.note(
        f"[evaluation] suite={args.suite} schedule={suite.schedule_id()[:16]} "
        f"blocks={[(b.band, len(b.opponent_ids)) for b in suite.blocks]} games={total}"
    )

    def play_block(band: str, opponents: tuple[str, ...]):
        reporter.note(f"[evaluation] block band={band} opponents={len(opponents)}")
        return _play_block(
            subject=subject, opponents=opponents, pool=pool,
            games_per_opponent_seat=args.games_per_opponent_seat,
            base_seed=args.base_seed, out_dir=out / "matches", reporter=reporter,
        )

    report = run_evaluation_suite_v1(suite, play_block=play_block)
    reporter.close()

    payload = {
        "run_name": args.run_name,
        "subject": subject[2],
        "strength_manifest": str(Path(args.strength_manifest).resolve()),
        "suite": suite.to_dict(),
        "report": report.to_dict(),
    }
    _atomic_write_json(out / "suite_report.json", payload)
    print(json.dumps(payload["report"], ensure_ascii=False, indent=2), flush=True)
    if report.requires_rerun:
        print(
            f"[evaluation] {report.infrastructure_failures} infrastructure failures: "
            "§14.3 requires re-running the affected block for both sides before this "
            "report is used for promotion.",
            file=sys.stderr, flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
