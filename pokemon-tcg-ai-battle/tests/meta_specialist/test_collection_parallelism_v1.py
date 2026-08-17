"""1 レーン内の並列収集が、逐次と同じ契約を保つことを固定する。

局は `game_index` だけから seed / seat / 相手が決まるため互いに独立で、記録も局
ごとに別ファイルへ書かれる。したがってプロセス並列にできる。

**再現性は元から無い。** `--base-seed` を揃えても、逐次同士で 12/12 局が異なる
(native `cg` engine 側の非決定性)。並列化はこれを悪化させないが、改善もしない。
この事実を固定しておかないと、後から「並列化したから結果が変わった」と誤って
帰属される。
"""

from __future__ import annotations

import inspect

from mage_ptcg.meta_specialist import collect_teacher_records_v1 as module


def test_games_are_independent_functions_of_their_index() -> None:
    """並列化の前提: 局の条件が game_index だけから決まること。"""
    source = inspect.getsource(module._play_one_game_v1)

    assert 'seed = payload["base_seed"] + game_index' in source
    # 座席は相手巡回とエイリアスしないよう周回番号から決める
    # (test_sharded_snapshot_v1 の座席テスト参照)。
    assert "seat_for_game_v1(game_index, len(opponent_ids))" in source
    assert "opponent_ids[game_index % len(opponent_ids)]" in source


def test_the_worker_writes_one_file_per_game() -> None:
    """局ごとに別ファイルなので、worker 間で書き込みが競合しない。"""
    source = inspect.getsource(module._play_one_game_v1)

    assert 'f"game-{game_index:06d}.jsonl"' in source


def test_the_worker_does_not_apply_the_matchup_weight_itself() -> None:
    """cap は corpus 全体の構成に依存し、単独の worker には分からない。"""
    source = inspect.getsource(module._play_one_game_v1)

    assert "quality_weight=1.0" in source
    assert "quality_weight_for_v1" not in source


def test_the_matchup_weight_is_computed_from_the_whole_corpus() -> None:
    """最終構成で重み付けする。前置きの局数に依存する順序依存を持たない。

    以前ここは ``"for row in per_game" in source`` を検査していた。その文字列は
    **実装が壊れている間もずっと存在した**。``per_game`` はその実行で回した局だけを
    指しており、resume すると分母が corpus の一部になって cap が誤発動し、249,299
    record のうち 242 件が不要に減点されたうえ content_hash が不整合になった。
    字面の一致を不変条件の証拠として扱うと、このように誤った保証を与える。

    実際の不変条件は tests/meta_specialist/test_matchup_weight_integrity_v1.py が
    出力に対して検査する。ここは分母が disk 上の全局であることだけ見る。
    """
    source = inspect.getsource(module._apply_matchup_weights_v1)

    assert "quality_weight_for_v1" in source
    assert "_scan_completed_games_v1(records_dir)" in source, (
        "分母が disk 上の全局ではなく、この実行で回した局になっている"
    )


def test_parallel_collection_uses_processes_not_threads() -> None:
    """相手 policy の読み込みは sys.modules を書き換えるため thread では危険。"""
    source = inspect.getsource(module.run_collect_teacher_records_v1)

    assert "ProcessPoolExecutor" in source
    assert 'get_context("spawn")' in source


def test_the_seeding_helper_does_not_claim_reproducibility() -> None:
    """実測で否定された主張を docstring へ書き戻させない。"""
    docstring = inspect.getdoc(module.seed_agent_randomness_v1) or ""

    assert "Does not make runs" in docstring
    assert "no additional" in docstring, (
        "並列化が非決定性を増やさないという実測の記録が消えている"
    )
    assert "never on" in docstring or "sample size" in docstring, (
        "seed 一致を根拠に差分を帰属してはならない旨の記述が消えている"
    )
