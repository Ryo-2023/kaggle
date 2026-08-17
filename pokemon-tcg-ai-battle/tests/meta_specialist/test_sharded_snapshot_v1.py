"""shard 分割しても corpus 全体の性質が保たれることを固定する。

## なぜ切り詰めではなく分割か

上限に収まる先頭 N 局だけを封印すると、`opponent = index % n` / `seat` の巡回に
より、残った範囲の構成が「たまたま切れた位置」に依存する。捨てた分は二度と使えず、
何を捨てたのかも dataset から追えない。全例を保持し、束ねる単位だけを分ける。

## 何が壊れうるか

corpus は 2 通りに分かれる。学習成果物としての **shard** と、exact file identity の
単位である **chunk** である。どちらの境界も、同じ episode の決定を train と test へ
散らしてはならない。split は chunk ごとでも shard ごとでもなく corpus 全体で決める。

本ファイルはその不変条件を、実装の字面ではなく**出力**に対して固定する。以前は
`inspect.getsource` を grep していたが、実装を関数へ切り出しただけでテストが落ち、
壊れていない不変条件を壊れたと報告した。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seat_for_game_v1
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    atomic_write_local_dataset_v2,
    build_local_dataset_manifest_v2,
)
from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
    reissue_sealed_envelope_v2,
)
from mage_ptcg.meta_specialist.training_snapshot_v1 import (
    TRAINING_SNAPSHOT_SCHEMA_V1,
    TrainingSnapshotV1Error,
    build_sharded_training_snapshots_from_chunks_v1,
    corpus_dataset_sha256_v1,
    partition_record_files_v1,
    read_sharded_split_examples_v1,
    seal_sharded_corpora_v1,
    read_training_snapshot_v1,
)

from tests.meta_specialist.test_training_example_envelope_v2 import (
    _qualified_two_record_dataset,
)

QUALIFIED_AT = "2026-08-02T00:00:00Z"


def _two_chunk_corpus(tmp_path):
    """同じ near-duplicate を共有する 2 record を、別々の chunk へ置く。

    chunk ごとに split を決める実装なら、この 2 件は別 split へ分かれうる。
    corpus 全体で決めるなら必ず同じ split になる。
    """
    _path, records, _manifest, _permission, trusted, vocabulary = (
        _qualified_two_record_dataset(tmp_path)
    )
    chunks = []
    for index, record in enumerate(records):
        manifest = build_local_dataset_manifest_v2(
            records=(record,), environment_version="fixture", deck_fingerprint="d" * 64,
            trusted_permissions=trusted,
        )
        chunk_path = tmp_path / f"chunk-{index}.local.jsonl"
        atomic_write_local_dataset_v2(chunk_path, records=(record,), manifest=manifest)
        chunks.append((chunk_path, manifest))
    return chunks, trusted, vocabulary


def _capped_corpus(tmp_path, *, copies=10, chunks_wanted=2):
    """同じ位置を cap (8) より多く含む corpus を、複数 chunk へ分けて作る。

    Regression: shard は corpus の断片なので、自分の例から重複 cap を再導出すると
    corpus 全体の重みと食い違う。2 shard 以上 **かつ** cap が実際に発動する corpus
    でしか再現しないため、cap が効かない小さな fixture では検出できなかった。
    """
    from mage_ptcg.meta_specialist.local_dataset_v2 import _record_content_hash, _record_id

    _path, records, _manifest, _permission, trusted, vocabulary = (
        _qualified_two_record_dataset(tmp_path)
    )
    base = records[0]
    made = []
    for index in range(copies):
        item = copy.deepcopy(base)
        item["episode_id_hash"] = f"{index:064x}"
        item["decision_index"] = 1
        item["record_id"] = _record_id(
            decision_id=item["decision_id"], episode_id_hash=item["episode_id_hash"],
            decision_index=item["decision_index"],
        )
        item["content_hash"] = _record_content_hash(item)
        made.append(item)

    per_chunk = max(1, len(made) // chunks_wanted)
    chunks = []
    for position in range(0, len(made), per_chunk):
        group = tuple(made[position:position + per_chunk])
        manifest = build_local_dataset_manifest_v2(
            records=group, environment_version="fixture", deck_fingerprint="d" * 64,
            trusted_permissions=trusted,
        )
        chunk_path = tmp_path / f"capped-{len(chunks)}.local.jsonl"
        atomic_write_local_dataset_v2(chunk_path, records=group, manifest=manifest)
        chunks.append((chunk_path, manifest))
    return chunks, trusted, vocabulary


def _seal(tmp_path, chunks, trusted, vocabulary, *, shard_max_examples=1, out="out"):
    return build_sharded_training_snapshots_from_chunks_v1(
        chunks, vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc=QUALIFIED_AT, output_dir=tmp_path / out,
        shard_max_examples=shard_max_examples,
    )


def _all_examples(tmp_path, index, out="out"):
    examples = []
    for shard in index["shards"]:
        payload = read_training_snapshot_v1(tmp_path / out / shard["path"])
        examples.extend(payload["examples"])
    return examples


# ---------------------------------------------------------------------------
# split は corpus 全体で決まる
# ---------------------------------------------------------------------------


def test_one_near_duplicate_group_stays_in_one_split_across_chunks(tmp_path) -> None:
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)

    examples = _all_examples(tmp_path, index)
    assert len(examples) == 2
    assert len({item["near_duplicate_id"] for item in examples}) == 1, (
        "前提が崩れている: 2 件が同じ near-duplicate 群でないとこのテストは無意味"
    )
    assert len({item["split"] for item in examples}) == 1, (
        "同じ近似重複群が chunk 境界で train/test に分かれた。split が chunk ごとに "
        "決まっている"
    )


def test_the_shards_really_are_separate_files_holding_every_example(tmp_path) -> None:
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)

    assert len(index["shards"]) == 2, "shard_max_examples=1 なのに分割されていない"
    assert index["examples_total"] == 2
    record_ids = [item["record_id"] for item in _all_examples(tmp_path, index)]
    assert len(record_ids) == len(set(record_ids)) == 2, "example が重複または欠落した"


def test_every_shard_carries_the_same_corpus_wide_duplicate_cap(tmp_path) -> None:
    """cap は corpus の性質であり、shard は自分の断片から再計算できない。"""
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)

    caps = [
        read_training_snapshot_v1(tmp_path / "out" / shard["path"])["duplicate_cap"]
        for shard in index["shards"]
    ]
    assert caps[0] == caps[1] == index["duplicate_cap"]


def test_the_duplicate_cap_survives_being_split_across_shards(tmp_path) -> None:
    """cap が実際に発動する corpus を複数 shard へ割っても封印でき、読み戻せる。

    Regression: shard 単体の検証が corpus 全体の cap を自分の断片から再導出して
    いたため、`example_quality_weight does not match the duplicate cap` で封印が
    落ちた。cap が効かない小さな corpus では再現しない。
    """
    chunks, trusted, vocabulary = _capped_corpus(tmp_path, copies=10, chunks_wanted=2)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=3)

    assert len(index["shards"]) >= 2, "shard が 1 個では回帰を再現しない"
    assert index["duplicate_cap"]["groups_capped"] == 1, (
        "cap が発動していない corpus ではこのテストは無意味"
    )
    assert index["duplicate_cap"]["records_capped"] == 10

    weights = {
        item["example_quality_weight"] for item in _all_examples(tmp_path, index)
    }
    assert weights and all(weight < 1.0 for weight in weights), (
        "cap 対象なのに重みが下がっていない"
    )
    # corpus 全体の検証を通ること (ここが shard 単体では検証できない部分)。
    total = sum(
        len(read_sharded_split_examples_v1(tmp_path / "out" / "snapshot_index.json", name))
        for name in index["split_names"]
    )
    assert total == 10


def test_a_shard_is_not_readable_as_a_standalone_snapshot(tmp_path) -> None:
    """shard は corpus 全体の検査を省くので、単体 snapshot と混同されてはならない。"""
    chunks, trusted, vocabulary = _capped_corpus(tmp_path, copies=10, chunks_wanted=2)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=3)

    shard = read_training_snapshot_v1(tmp_path / "out" / index["shards"][0]["path"])
    assert shard["schema_version"] == "specialist-training-snapshot-shard-v1"
    assert shard["schema_version"] != TRAINING_SNAPSHOT_SCHEMA_V1


def test_the_corpus_check_catches_a_weight_that_ignores_the_cap(tmp_path) -> None:
    """cap を無視した重みが shard 単体検証を通っても、corpus 検証で落ちる。"""
    chunks, trusted, vocabulary = _capped_corpus(tmp_path, copies=10, chunks_wanted=2)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=3)
    index_path = tmp_path / "out" / "snapshot_index.json"

    victim = tmp_path / "out" / index["shards"][0]["path"]
    payload = json.loads(victim.read_text(encoding="utf-8"))
    payload["examples"][0]["example_quality_weight"] = payload["examples"][0][
        "pre_cap_quality_weight"
    ]
    victim.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((TrainingSnapshotV1Error, LocalDatasetV2Error)):
        read_sharded_split_examples_v1(index_path, "train")


# ---------------------------------------------------------------------------
# 並列化は結果を変えない
# ---------------------------------------------------------------------------


def test_reading_in_parallel_returns_exactly_the_serial_result(tmp_path) -> None:
    """worker 数は所要時間だけを変え、内容も順序も変えてはならない。

    shard は完了順ではなく shard 順に並べ直す必要がある。並べ直さないと、同じ
    index から読んだのに run ごとに example の順が変わり、学習が再現しなくなる。
    """
    chunks, trusted, vocabulary = _capped_corpus(tmp_path, copies=12, chunks_wanted=4)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=2)
    index_path = tmp_path / "out" / "snapshot_index.json"
    assert len(index["shards"]) >= 4, "shard が少ないと並列の順序ずれを検出できない"

    serial = read_sharded_split_examples_v1(index_path, "train")
    parallel = read_sharded_split_examples_v1(index_path, "train", workers=4)
    assert parallel == serial


def test_reading_rejects_a_nonpositive_worker_count(tmp_path) -> None:
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)
    with pytest.raises(TrainingSnapshotV1Error, match="positive int"):
        read_sharded_split_examples_v1(
            tmp_path / "out" / "snapshot_index.json", "train", workers=0
        )


def test_each_chunk_owns_its_shards_so_the_pass_is_parallelisable(tmp_path) -> None:
    """shard は chunk 境界で区切られ、番号が chunk 順に連続すること。

    区切られていないと 1 つの shard が複数 chunk の例を持ち、chunk ごとの job に
    分けられない (= 第 2 パスが直列に戻る)。
    """
    chunks, trusted, vocabulary = _capped_corpus(tmp_path, copies=12, chunks_wanted=4)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=100)

    assert len(index["shards"]) == len(chunks), (
        "chunk 1 個につき shard 1 個になっていない"
    )
    assert [row["path"] for row in index["shards"]] == [
        f"snapshot-{position:04d}.json" for position in range(len(chunks))
    ]


# ---------------------------------------------------------------------------
# chunk の切り方
# ---------------------------------------------------------------------------


def test_chunks_group_whole_record_files_under_the_byte_budget(tmp_path) -> None:
    """chunk 境界は file 単位。1 file が複数 chunk に割れると chunk を並列に書けない。"""
    files = []
    for index, size in enumerate((30, 30, 30, 90, 10)):
        path = tmp_path / f"game-{index:03d}.jsonl"
        path.write_bytes(b"x" * size)
        files.append(path)

    groups = partition_record_files_v1(files, chunk_max_bytes=70)

    assert [len(group) for group in groups] == [2, 1, 1, 1], groups
    assert [Path(name).name for group in groups for name in group] == [
        path.name for path in files
    ], "入力順が保たれていない。chunk 順は corpus identity に効く"


def test_a_single_oversized_file_still_becomes_its_own_chunk(tmp_path) -> None:
    """予算より大きい 1 file を無限ループや空 chunk にしない。"""
    path = tmp_path / "game-000.jsonl"
    path.write_bytes(b"x" * 500)

    assert partition_record_files_v1([path], chunk_max_bytes=10) == [[str(path)]]


def test_chunk_partitioning_is_deterministic(tmp_path) -> None:
    files = []
    for index in range(12):
        path = tmp_path / f"game-{index:03d}.jsonl"
        path.write_bytes(b"x" * (7 * index + 3))
        files.append(path)

    first = partition_record_files_v1(files, chunk_max_bytes=64)
    second = partition_record_files_v1(list(files), chunk_max_bytes=64)
    assert first == second


def test_chunk_budget_must_be_positive(tmp_path) -> None:
    path = tmp_path / "game-000.jsonl"
    path.write_bytes(b"x")
    with pytest.raises(TrainingSnapshotV1Error, match="positive int"):
        partition_record_files_v1([path], chunk_max_bytes=0)


def test_sealing_several_corpora_needs_at_least_one(tmp_path) -> None:
    with pytest.raises(TrainingSnapshotV1Error, match="at least one corpus"):
        seal_sharded_corpora_v1([], workers=2)


# ---------------------------------------------------------------------------
# corpus の identity
# ---------------------------------------------------------------------------


def test_the_index_names_every_chunk_and_the_digest_recomputes_from_them(tmp_path) -> None:
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)

    listed = index["dataset_chunks"]
    assert [item["path"] for item in listed] == [str(path) for path, _m in chunks], (
        "chunk が与えた順で記録されていない。digest は順序に依存する"
    )
    assert index["dataset_snapshot_sha256"] == corpus_dataset_sha256_v1(
        [item["dataset_snapshot_sha256"] for item in listed]
    ), "corpus identity が、index が挙げた chunk から再計算できない"


def test_the_corpus_digest_is_not_a_single_chunks_file_hash(tmp_path) -> None:
    """1 chunk でも digest。読み手が file hash か digest かを推測せずに済む。"""
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks[:1], trusted, vocabulary, shard_max_examples=8)

    only = index["dataset_chunks"][0]
    assert index["dataset_snapshot_sha256"] != only["dataset_snapshot_sha256"]
    assert index["dataset_snapshot_sha256"] == corpus_dataset_sha256_v1(
        [only["dataset_snapshot_sha256"]]
    )


def test_the_index_records_the_source_artifacts_theta0_is_attributed_to(tmp_path) -> None:
    """index が source_artifacts を持たないと θ0 が teacher を名乗れない。"""
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)

    assert index["source_artifacts"], "θ0 の provenance 元が index から消えている"
    assert all(
        set(item) == {"kind", "artifact_sha256"} for item in index["source_artifacts"]
    )


# ---------------------------------------------------------------------------
# fail-closed
# ---------------------------------------------------------------------------


def test_reading_a_sharded_split_refuses_a_missing_shard(tmp_path) -> None:
    """shard が欠けたまま学習が縮んだ train set で走ることを防ぐ。"""
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)
    index_path = tmp_path / "out" / "snapshot_index.json"

    (tmp_path / "out" / index["shards"][0]["path"]).unlink()
    with pytest.raises((TrainingSnapshotV1Error, LocalDatasetV2Error, OSError)):
        read_sharded_split_examples_v1(index_path, "train")


def test_reading_a_sharded_split_refuses_an_edited_shard(tmp_path) -> None:
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    index = _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=1)
    index_path = tmp_path / "out" / "snapshot_index.json"

    victim = tmp_path / "out" / index["shards"][0]["path"]
    payload = json.loads(victim.read_text(encoding="utf-8"))
    payload["examples"] = []
    victim.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((TrainingSnapshotV1Error, LocalDatasetV2Error)):
        read_sharded_split_examples_v1(index_path, "train")


def test_shard_size_must_be_positive(tmp_path) -> None:
    chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    with pytest.raises(TrainingSnapshotV1Error, match="positive int"):
        _seal(tmp_path, chunks, trusted, vocabulary, shard_max_examples=0)


def test_a_corpus_needs_at_least_one_chunk(tmp_path) -> None:
    _chunks, trusted, vocabulary = _two_chunk_corpus(tmp_path)
    with pytest.raises(TrainingSnapshotV1Error, match="at least one dataset chunk"):
        _seal(tmp_path, [], trusted, vocabulary, shard_max_examples=1)


def test_reissuing_a_sealed_envelope_refuses_edited_bytes() -> None:
    """pass 間で spool した bytes は、再 issue 時に完全検証される。"""
    with pytest.raises(LocalDatasetV2Error):
        reissue_sealed_envelope_v2(b'{"model_input":{}}')
    with pytest.raises(LocalDatasetV2Error, match="exact bytes"):
        reissue_sealed_envelope_v2("not bytes")


# ---------------------------------------------------------------------------
# 座席と相手巡回のエイリアス
# ---------------------------------------------------------------------------


def test_no_opponent_is_locked_to_a_single_seat() -> None:
    """Regression: `seat = index % 2` は偶数個の相手巡回と完全にエイリアスした。

    16 相手では index と index+16 の偶奇が一致するため、相手 16 体すべてが常に
    同じ座席でしか当たらなかった。manifest の seat_counts は 150/150 と均等に
    見えるが、matchup ごとには 8 体が常に先手・8 体が常に後手であり、先手の価値が
    大きいこのゲームでは matchup ごとの成績が座席と交絡する。
    """
    for opponent_count in (2, 4, 8, 12, 16, 32):
        seats: dict[int, list[int]] = {}
        for index in range(opponent_count * 2 * 5):
            seats.setdefault(index % opponent_count, []).append(
                seat_for_game_v1(index, opponent_count)
            )
        locked = [key for key, values in seats.items() if len(set(values)) == 1]
        assert not locked, f"{opponent_count} 相手で座席固定が発生: {locked}"


def test_each_opponent_gets_both_seats_equally() -> None:
    for opponent_count in (8, 16):
        seats: dict[int, list[int]] = {}
        for index in range(opponent_count * 2 * 4):
            seats.setdefault(index % opponent_count, []).append(
                seat_for_game_v1(index, opponent_count)
            )
        for key, values in seats.items():
            assert values.count(0) == values.count(1), (
                f"相手 {key} の先手/後手が {values.count(0)}/{values.count(1)} で偏った"
            )


def test_seat_is_independent_of_which_opponent_is_faced() -> None:
    """同じ周回内では全相手が同じ座席、次の周回で入れ替わる。"""
    count = 16

    first_cycle = {seat_for_game_v1(i, count) for i in range(count)}
    second_cycle = {seat_for_game_v1(i, count) for i in range(count, 2 * count)}

    assert first_cycle == {0}
    assert second_cycle == {1}
