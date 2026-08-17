"""収集後の matchup weight 適用が、record の整合性を壊さないことを固定する。

## 何が起きたか

3,000 局の corpus (t1-alakazam, 249,299 record) が封印できなかった。原因は
`_apply_matchup_weights_v1` の 2 つの欠陥で、どちらも resume を挟むと顕在化した。

1. weight の分母がその実行で回した局だけだった。3 局しか回さなかった resume では
   1 相手のシェアが 25% cap を超えて見え、weight が下げられた。corpus 全体では
   その相手は 8.4% であり、cap は本来かからない。
2. weight を書き換えても `content_hash` を再計算していなかった。record が自分の
   内容と一致しない hash を持ったまま残り、封印が
   `record content_hash does not verify` で corpus 全体を拒否した。

実測 242 record / 3 局が壊れ、他の 3 レーンは無傷だった。resume の有無で決まるため、
1 回で回し切った収集では再現しない。
"""

from __future__ import annotations

import json

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import (
    _apply_matchup_weights_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import _record_content_hash

from tests.meta_specialist.test_training_example_envelope_v2 import (
    _qualified_two_record_dataset,
)


def _write_games(tmp_path, records, *, games):
    """1 局 1 ファイルで records ディレクトリを作る。"""
    records_dir = tmp_path / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    for game_index in range(games):
        path = records_dir / f"game-{game_index:06d}.jsonl"
        with open(path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")) + "\n")
    return records_dir


def _all_records(records_dir):
    for path in sorted(records_dir.glob("*.jsonl")):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield path.name, json.loads(line)


def test_rewriting_the_weight_keeps_every_content_hash_verifiable(tmp_path) -> None:
    """Regression: weight を書き換えて hash を放置すると corpus 全体が封印不能になる。"""
    _path, records, _m, _p, _t, _v = _qualified_two_record_dataset(tmp_path)
    records_dir = _write_games(tmp_path, records, games=4)

    # cap を極端に低くして、必ず weight が下がる状況を作る。
    rewritten = _apply_matchup_weights_v1(
        records_dir, [], opponent_ids=["only_opponent"], matchup_cap_fraction=0.01,
    )

    assert rewritten > 0, "weight が下がる条件なのに 1 局も書き換えられていない"
    changed = 0
    for name, record in _all_records(records_dir):
        assert _record_content_hash(record) == record["content_hash"], (
            f"{name} の content_hash が内容と一致しない。封印がこの corpus を拒否する"
        )
        if record["teacher"]["quality_weight"] != 1.0:
            changed += 1
    assert changed > 0, "重みが下がった record が 1 件も無い"


def test_the_share_is_measured_over_every_game_on_disk(tmp_path) -> None:
    """Regression: resume した実行が、その回の局だけを分母にして cap を誤発動させた。

    2 相手を均等に巡回した 20 局は、どちらも 50% で 25% cap を超えるが、cap を
    十分に高くすれば重みは 1.0 のままでなければならない。ここで per_game に
    「今回の実行で回した 1 局」だけを渡しても、分母は disk 上の全局になる。
    """
    _path, records, _m, _p, _t, _v = _qualified_two_record_dataset(tmp_path)
    records_dir = _write_games(tmp_path, records, games=20)

    rewritten = _apply_matchup_weights_v1(
        records_dir,
        [{"game_index": 0, "opponent_id": "a", "status": "DONE", "n_records": 2}],
        opponent_ids=["a", "b"], matchup_cap_fraction=0.60,
    )

    assert rewritten == 0, (
        "全局で見れば各相手 50% で cap 0.60 を下回るのに、重みが下げられた。"
        "分母がこの実行の局だけになっている"
    )
    for _name, record in _all_records(records_dir):
        assert record["teacher"]["quality_weight"] == 1.0


def test_an_untouched_corpus_is_not_rewritten(tmp_path) -> None:
    """cap がかからない corpus では 1 ファイルも書き換えない（mtime を汚さない）。"""
    _path, records, _m, _p, _t, _v = _qualified_two_record_dataset(tmp_path)
    records_dir = _write_games(tmp_path, records, games=8)
    before = {p.name: p.stat().st_mtime_ns for p in records_dir.glob("*.jsonl")}

    rewritten = _apply_matchup_weights_v1(
        records_dir, [], opponent_ids=["a", "b"], matchup_cap_fraction=0.90,
    )

    assert rewritten == 0
    assert {p.name: p.stat().st_mtime_ns for p in records_dir.glob("*.jsonl")} == before
