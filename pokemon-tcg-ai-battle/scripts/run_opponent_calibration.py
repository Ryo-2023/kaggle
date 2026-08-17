"""プール全体を固定 reference panel との交差対戦で banding する (正典 §13)。

正典 §13 は `source_rank_band` と `local_strength_band` の分離を求める。band は
**実測でのみ**与えられ、Kaggle のメダルや出所からは継承されない (calibration_v1 の
契約)。この runner はその実測を回して `LocalStrengthManifest` 相当を出力する。

長時間実行を想定している。相手数 × panel 数 × 局数 の対戦を行うため、
既定の設定でも数時間規模になりうる。切り離して起動すること。
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

from mage_ptcg.meta_specialist.calibration_v1 import (
    CalibrationV1Error,
    MatchupResultV1,
    ReferencePanelV1,
    calibrate_opponent_v1,
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


def _play_matchup(subject, reference, *, games_per_seat: int, base_seed: int, out_dir: Path):
    """Seat-balanced head-to-head. Returns the six counters calibration needs."""
    counters = {"w0": 0, "d0": 0, "l0": 0, "w1": 0, "d1": 0, "l1": 0}
    sf = build_opponent_agent_factory_v1(subject)
    rf = build_opponent_agent_factory_v1(reference)
    for seat in (0, 1):
        for i in range(games_per_seat):
            first = seat == 0
            try:
                result = run_match(
                    deck_a_path=subject.deck_csv_path if first else reference.deck_csv_path,
                    deck_b_path=reference.deck_csv_path if first else subject.deck_csv_path,
                    agent_a_name="a", agent_b_name="b",
                    seed=base_seed + i, max_steps=2000,
                    output_dir=str(out_dir / f"{subject.opponent_id}-{reference.opponent_id}-{seat}-{i}"),
                    save_html=False, save_result=False,
                    agent_a_factory=sf if first else rf,
                    agent_b_factory=rf if first else sf,
                )
            except Exception:
                continue
            if result.get("status") != "DONE":
                continue
            winner = result.get("winner")
            key = "0" if seat == 0 else "1"
            if winner == 2:
                counters[f"d{key}"] += 1
            elif winner == seat:
                counters[f"w{key}"] += 1
            else:
                counters[f"l{key}"] += 1
    return counters


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", required=True,
                        help="reference panel を成す opponent id のカンマ区切り")
    parser.add_argument("--games-per-seat", type=int, default=8)
    parser.add_argument("--base-seed", type=int, default=7700000)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--max-decision-ms", type=float, default=1.0,
                        help="この latency 以下の相手だけを対象にする。探索相手は評価専用")
    parser.add_argument("--output-base", default="runs/meta-specialist-calibration")
    args = parser.parse_args()

    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    panel_ids = tuple(sorted({x.strip() for x in args.panel.split(",") if x.strip()}))
    if len(panel_ids) < 2:
        raise SystemExit("a reference panel needs at least two members")
    panel = ReferencePanelV1(reference_ids=panel_ids)
    references = {rid: resolve_opponent_v1(pool, rid, subject_deck_csv_path="x") for rid in panel_ids}

    subjects = [
        oid for oid in sorted(pool)
        if (pool[oid].mean_decision_ms or 99.0) <= args.max_decision_ms and oid not in panel_ids
    ]
    out = Path(args.output_base) / args.run_name
    out.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporterV1(total=len(subjects), desc=f"calibration {args.run_name}")
    reporter.note(f"[calibration] start subjects={len(subjects)} panel={len(panel_ids)} "
                  f"games_per_matchup={2 * args.games_per_seat}")

    started = time.time()
    calibrations: list[dict] = []
    for index, oid in enumerate(subjects, start=1):
        subject = resolve_opponent_v1(pool, oid, subject_deck_csv_path="x")
        matchups = []
        empty: list[str] = []
        for rid in panel_ids:
            c = _play_matchup(
                subject, references[rid], games_per_seat=args.games_per_seat,
                base_seed=args.base_seed, out_dir=out / "matches",
            )
            if sum(c.values()) == 0:
                # 全局が fault / 非 DONE。0-0 を「引き分け」として作らない:
                # 対戦していない事実を対戦結果として記録することになる。
                empty.append(rid)
                continue
            matchups.append(MatchupResultV1(
                reference_id=rid,
                wins_seat0=c["w0"], draws_seat0=c["d0"], losses_seat0=c["l0"],
                wins_seat1=c["w1"], draws_seat1=c["d1"], losses_seat1=c["l1"],
            ))
        if empty:
            reason = f"no completed games against {sorted(empty)}"
            calibrations.append({"opponent_id": oid, "band": None, "error": reason})
            reporter.update(1, last=oid, band="UNBANDED")
            continue
        try:
            calibration = calibrate_opponent_v1(opponent_id=oid, panel=panel, matchups=matchups)
        except CalibrationV1Error as exc:
            # 局が落ちて座席が崩れた場合など。捏造せず理由付きで残す。
            calibrations.append({"opponent_id": oid, "band": None, "error": str(exc)[:200]})
            reporter.update(1, last=oid, band="UNBANDED")
            continue
        calibrations.append({
            "opponent_id": oid, "band": calibration.band, "score": calibration.score,
            "interval_low": calibration.interval_low, "interval_high": calibration.interval_high,
            "games": calibration.games, "band_reason": calibration.band_reason,
        })
        banded = sum(1 for c in calibrations if c.get("band") in ("lower", "middle", "high"))
        reporter.update(1, last=oid, band=calibration.band,
                        score=calibration.score, banded=banded)
        _atomic_write_json(out / "local_strength_manifest.json", {
            "schema_version": "meta-specialist-local-strength-v1",
            "run_name": args.run_name, "panel": list(panel_ids),
            "games_per_seat": args.games_per_seat, "base_seed": args.base_seed,
            "completed": index, "total": len(subjects),
            "elapsed_seconds": round(time.time() - started, 1),
            "calibrations": calibrations,
        })

    reporter.close()
    counts: dict[str, int] = {}
    for item in calibrations:
        counts[str(item.get("band"))] = counts.get(str(item.get("band")), 0) + 1
    print(json.dumps({"subjects": len(subjects), "band_counts": counts,
                      "manifest": str(out / "local_strength_manifest.json")},
                     ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
