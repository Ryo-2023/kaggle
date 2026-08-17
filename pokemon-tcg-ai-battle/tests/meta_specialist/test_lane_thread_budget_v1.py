"""レーンへ渡すスレッド予算が、process 並列と torch の intra-op 並列で分かれること。

この 2 つを 1 つの数で配ると、レーンを 1 本だけ指定した run が全コアを torch へ渡す。
このモデルのテンソルは 1 マイクロバッチ 16 例 x 最大 30 トークン x hidden 128 と小さく、
OpenMP のバリア同期が演算量を上回るため、それが**最も遅い設定**になる。28 コア機での
実測は 2 スレッドが最速で、4 で 1.04 倍、7 で 1.97 倍、14 で 4.33 倍、28 で 37 倍遅い。

実際に `bc-alakazam` が 28 スレッドを受け取り、14 スレッドで走った grimmsnarl より
12.35 倍遅くなった。ベンチの予測比 0.0808 と実測比 0.0810 が一致してこれが原因と確定
している (docs/evidence/bc-thread-oversubscription-20260807.md)。

**argv を実際に組み立てて検査する。** 「その行がソースにあるか」を grep する形式では、
値の配線が入れ替わっても通ってしまう。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "run_parallel_lanes_under_test", _ROOT / "scripts" / "run_parallel_lanes.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_module()


def _args(tmp_path: Path, stage: str, **overrides) -> argparse.Namespace:
    """`_lane_command` が読む属性だけを持つ最小の args。"""
    snapshot = tmp_path / "snapshot_index.json"
    snapshot.write_text("{}", encoding="utf-8")
    values = dict(
        stage=stage, run_prefix="t1", max_torch_threads=4,
        num_games=10, base_seed=1, collect_workers=0, rl_games=10,
        max_steps=100, examples_per_step=64, microbatch_examples=16,
        checkpoint_interval_steps=200,
        output_base=str(tmp_path / "out"),
        snapshot_template=str(snapshot),
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _flag(argv: list[str], name: str) -> str:
    assert name in argv, f"{name} が argv にない: {argv}"
    return argv[argv.index(name) + 1]


def test_a_single_lane_run_does_not_hand_every_core_to_torch(tmp_path: Path) -> None:
    """回帰: レーン 1 本の run が 28 スレッドを受け取っていた。"""
    args = _args(tmp_path, "bc")
    argv = _lane_argv(args, threads=28, tmp_path=tmp_path)

    assert _flag(argv, "--torch-threads") == "4"


def test_shard_reading_keeps_the_full_core_budget(tmp_path: Path) -> None:
    """読み込みはプロセス並列でコア数まで速くなるので、cap を掛けてはいけない。

    torch のスレッドを下げるために読み込みまで下げると、起動時の shard 読み込みが
    28 ワーカーから 4 ワーカーへ落ちる。計算の修正で起動を遅くしては意味がない。
    """
    args = _args(tmp_path, "bc")
    argv = _lane_argv(args, threads=28, tmp_path=tmp_path)

    assert _flag(argv, "--read-workers") == "28"


def test_the_cap_never_raises_a_smaller_budget(tmp_path: Path) -> None:
    """コア予算のほうが小さいときは、そちらに従う。"""
    args = _args(tmp_path, "bc")
    argv = _lane_argv(args, threads=2, tmp_path=tmp_path)

    assert _flag(argv, "--torch-threads") == "2"
    assert _flag(argv, "--read-workers") == "2"


def test_rl_train_is_capped_too(tmp_path: Path) -> None:
    """rl-train は --torch-threads を渡しておらず、既定の全コアで走っていた。"""
    checkpoints = tmp_path / "out" / "t1-alakazam" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "checkpoint-a.pt").write_bytes(b"x")

    args = _args(tmp_path, "rl-train")
    argv = _lane_argv(args, threads=28, tmp_path=tmp_path)

    assert _flag(argv, "--torch-threads") == "4"


def test_collection_workers_are_not_capped(tmp_path: Path) -> None:
    """収集はプロセス並列。ここへ cap を掛けると純粋な性能低下になる。"""
    args = _args(tmp_path, "collect")
    argv = _lane_argv(args, threads=28, tmp_path=tmp_path)

    assert _flag(argv, "--workers") == "28"


@pytest.mark.parametrize("threads", [1, 2, 4, 7, 14, 28, 64])
def test_the_torch_thread_count_is_always_positive_and_capped(
    tmp_path: Path, threads: int
) -> None:
    args = _args(tmp_path, "bc")
    resolved = module._torch_threads_v1(args, threads)

    assert 1 <= resolved <= args.max_torch_threads
    assert resolved <= threads


def _lane_argv(args, *, threads: int, tmp_path: Path) -> list[str]:
    return module._lane_command(
        "alakazam", module.LANE_PRESETS_V1["alakazam"], args, threads,
        tmp_path / "progress.json",
    )
