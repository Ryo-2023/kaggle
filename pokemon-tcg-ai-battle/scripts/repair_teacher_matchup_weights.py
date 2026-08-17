"""収集済み teacher records の matchup weight と content_hash を corpus 全体で直す。

## 何を直すか

`_apply_matchup_weights_v1` に 2 つの欠陥があり、resume を挟んだ収集で record が
壊れた。実測 (t1-alakazam, 3,000 局 249,299 record) では 3 局 242 record が該当した。

- weight の分母がその実行で回した局だけだった。3 局だけ回した resume では 1 相手の
  シェアが 25% cap を超えて見え、weight が下げられた。corpus 全体ではその相手は
  8.4% であり、cap は本来かからない。
- weight を書き換えても record 自身の `content_hash` を再計算していなかった。
  結果、hash が編集前の内容を指したまま残り、封印が
  `record content_hash does not verify` で corpus 全体を拒否した。

本スクリプトは records ディレクトリを corpus として読み直し、**corpus 全体の**
matchup シェアから正しい weight を決め、weight を直し、`content_hash` を再計算する。
再収集は不要である。

## 何を直さないか

weight と content_hash 以外のフィールドには触れない。決定内容、value_target、
episode、seat、相手はそのままである。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import (
    DEFAULT_MATCHUP_CAP_FRACTION_V1,
    _apply_matchup_weights_v1,
    _scan_completed_games_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import _record_content_hash


def _audit(records_dir: Path) -> tuple[int, int, list[str]]:
    """壊れている record 数と、その局を数える。"""
    bad_records = 0
    bad_games: list[str] = []
    total = 0
    for path in sorted(records_dir.glob("*.jsonl")):
        broken_here = 0
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                total += 1
                record = json.loads(line)
                if _record_content_hash(record) != record.get("content_hash"):
                    broken_here += 1
        if broken_here:
            bad_records += broken_here
            bad_games.append(path.name)
    return total, bad_records, bad_games


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection-run-dir", required=True)
    parser.add_argument(
        "--apply", action="store_true",
        help="既定は監査のみ。指定したときだけ書き換える",
    )
    parser.add_argument(
        "--matchup-cap-fraction", type=float, default=DEFAULT_MATCHUP_CAP_FRACTION_V1,
    )
    args = parser.parse_args()

    run_dir = Path(args.collection_run_dir)
    records_dir = run_dir / "records"
    manifest_path = run_dir / "teacher_dataset_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    opponent_ids = manifest["opponent_ids"]

    total, bad_records, bad_games = _audit(records_dir)
    print(f"[repair] {run_dir.name}: records={total:,} 局={len(_scan_completed_games_v1(records_dir)):,}")
    print(f"[repair] content_hash 不一致: {bad_records:,} record / {len(bad_games)} 局")
    if bad_games[:10]:
        print(f"[repair] 該当局: {bad_games[:10]}{' ...' if len(bad_games) > 10 else ''}")
    if not bad_records:
        print("[repair] 修復は不要です")
        return 0
    if not args.apply:
        print("[repair] 監査のみ。書き換えるには --apply を付けてください")
        return 0

    rewritten = _apply_matchup_weights_v1(
        records_dir, [], opponent_ids=opponent_ids,
        matchup_cap_fraction=args.matchup_cap_fraction,
    )
    print(f"[repair] {rewritten} 局を書き換えました")

    total_after, bad_after, _games = _audit(records_dir)
    print(f"[repair] 再監査: records={total_after:,} content_hash 不一致={bad_after:,}")
    if bad_after:
        print("[repair] 不一致が残っています", file=sys.stderr)
        return 1
    print("[repair] 全 record の content_hash が verify します")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
