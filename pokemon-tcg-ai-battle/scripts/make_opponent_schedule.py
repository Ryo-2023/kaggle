#!/usr/bin/env python3
"""学習相手の出現回数を、観測メタ分布へ寄せた schedule として書き出す。

## なぜ一様ではないか

プールを広げて medal 圏デッキを 100% カバーしても、一様に回すだけでは分布は合わない。
実測 (2026-08-04 snapshot) では、medal 圏の 37.1% を占める Marnie's Grimmsnarl ex が
84 体の一様巡回では 4.8% にしかならず、medal 圏 1.6% の Mega Lucario ex が 11.9% を
占める。カバー率と分布は別の問題である。

## なぜ完全比例でもないか

- snapshot は 62 件・3 日前であり、37.1% の 95% 区間は概ね ±12pt ある
- メタは実際に動く (2026-07-15 時点の上位とは別の分布だった)
- 稀なアーキタイプを 0 回にすると、そこだけ致命的に弱い方策が残る

そこで `weight = mix * メタ比率 + (1 - mix) * 一様` とし、下限を残す。正典 L7 の
「past bands retain a nonzero rehearsal floor」と同じ考え方である。

## 帯の選び方

既定は medal 圏 (金銀銅) と中位ライバル層の両方を数える。中位層は今すぐ当たる相手、
medal 圏は上がった先で当たる相手であり、どちらも必要である。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

EVAL_HELD_OUT_V1 = (
    "kiyotah_lucario", "sue124_alakazam", "skarin_dragapult",
    "ozawa_crustle_v2", "nihei_megalopunny", "yaroslav_crustleaware_lucario",
)


def _deck_key(cards) -> tuple[int, ...]:
    return tuple(sorted(int(card) for card in cards))


def _pool_decks(opponents_dir: Path) -> dict[str, tuple[int, ...]]:
    out = {}
    for entry in sorted(opponents_dir.iterdir()):
        path = entry / "deck.csv"
        if not entry.is_dir() or not path.is_file():
            continue
        ids = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        if len(ids) == 60:
            out[entry.name] = _deck_key(ids)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--opponents-dir", default=str(_ROOT / "opponents"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--cycle-games", type=int, default=200,
                        help="1 周あたりの総局数。weight はこれを配分した整数になる")
    parser.add_argument("--mix", type=float, default=0.7,
                        help="メタ比率の重み。残りが一様。0 で一様、1 で完全比例")
    parser.add_argument("--tiers", default="gold,silver,bronze,rivals")
    args = parser.parse_args()

    if not 0.0 <= args.mix <= 1.0:
        raise SystemExit("--mix must be within [0, 1]")

    analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    pool = _pool_decks(Path(args.opponents_dir))
    train = {name: key for name, key in pool.items() if name not in EVAL_HELD_OUT_V1}
    if not train:
        raise SystemExit("training pool is empty")

    # 観測されたデッキごとの出現数
    observed: Counter[tuple[int, ...]] = Counter()
    for tier in [t.strip() for t in args.tiers.split(",") if t.strip()]:
        rows = analysis["rivals"] if tier == "rivals" else analysis["medal_tiers"][tier]
        for row in rows:
            observed[_deck_key(row["deck"])] += 1
    total_observed = sum(observed.values())

    # 同じデッキを複数エージェントが持つ場合、そのデッキ分の重みを等分する
    holders: dict[tuple[int, ...], list[str]] = {}
    for name, key in train.items():
        holders.setdefault(key, []).append(name)

    share: dict[str, float] = {}
    uniform = 1.0 / len(train)
    for name, key in train.items():
        meta = observed[key] / total_observed / len(holders[key]) if observed[key] else 0.0
        share[name] = args.mix * meta + (1.0 - args.mix) * uniform

    scale = sum(share.values())
    # 全員に最低 1 局。下限を先に確保してから残りを比例配分する。
    weights = {name: 1 for name in train}
    remaining = args.cycle_games - len(train)
    if remaining < 0:
        raise SystemExit(
            f"--cycle-games={args.cycle_games} is below the pool size {len(train)}; "
            "every opponent needs at least one game per cycle"
        )
    for name, value in sorted(share.items(), key=lambda kv: -kv[1]):
        weights[name] += int(remaining * value / scale)
    Path(args.output).write_text(
        json.dumps(dict(sorted(weights.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    cycle = sum(weights.values())
    print(f"学習相手 {len(train)} 体 / 1 周 {cycle} 局 / mix={args.mix} "
          f"(メタ {args.mix:.0%} + 一様 {1-args.mix:.0%})")
    print(f"\n{'相手':<34}{'weight':>7}{'周内比率':>9}{'観測比率':>9}")
    for name, weight in sorted(weights.items(), key=lambda kv: -kv[1])[:12]:
        key = train[name]
        obs = observed[key] / total_observed * 100 if observed[key] else 0.0
        print(f"{name[:32]:<34}{weight:>7}{weight/cycle*100:>8.1f}%{obs:>8.1f}%")
    print(f"\n-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
