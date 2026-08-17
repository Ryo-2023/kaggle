# BC 学習の速度低下は torch のスレッド過剰割当だった

`bc-alakazam` が他レーンより 12.35 倍遅かった原因は、torch の intra-op スレッドを
28 本渡していたことである。このモデルのテンソルは小さく、スレッドを増やすほど遅く
なる。学習の数値結果はスレッド数に依存しないことを別途確認したので、修正は速度だけを
変える。

- 確認日: 2026-08-07
- commit: `a4e64752` (branch `feature/meta-specialist-canonical`)
- 環境: 28 コア、torch 2.11.0+cu128、CPU 実行
- 再現スクリプト: `scripts/run_bc_distillation.py`、`scripts/run_parallel_lanes.py`

## 観測

4 レーンの BC を同一コード・同一ハイパーパラメータで走らせたときの実績。`threads/lane`
は `run_parallel_lanes.py` が `total_threads // len(lanes)` で決めた値である。

| lane | threads/lane | 同時実行 | steps/s |
|---|---:|---|---:|
| archaludon | 14 | rocket と 2 本 | 0.263 |
| rocket | 14 | archaludon と 2 本 | 0.244 |
| grimmsnarl | 14 | 単独（alakazam は step 2 で異常終了） | 0.600 |
| alakazam | **28** | 単独 | **0.0486** |

alakazam は単独で全コアを与えられながら、14 スレッドの grimmsnarl より 12.35 倍遅い。

checkpoint の mtime から 200 step ごとのレートを再構成すると、両者とも時間経過で
劣化していない。grimmsnarl は 20 窓すべて 0.59〜0.61 steps/s、alakazam は 0.056
steps/s で平坦である。したがって累積的な劣化ではなく定常的な差である。

## スレッド数だけを振った測定

BC 本体を `SIGSTOP` で止め、競合のない状態で各レーンの実データを使って
forward + backward を測った。1 マイクロバッチは 16 例。

| threads | alakazam 1 パス | grimmsnarl 1 パス | 最速比 |
|---:|---:|---:|---:|
| 1 | 117.1 ms | 80.9 ms | 1.00x |
| 2 | **114.6 ms** | **79.3 ms** | **1.00x** |
| 4 | 119.1 ms | 82.0 ms | 1.04x |
| 7 | 225.3 ms | 159.7 ms | 1.97x |
| 14 | 498.1 ms | 343.5 ms | 4.33x |
| 28 | 4247.3 ms | 2873.9 ms | **37x** |

4 スレッドを超えると単調に悪化する。両レーンで曲線がほぼ一致するので、データ内容では
なくスレッド数の効果である。

原因は演算量に対する同期コストである。1 マイクロバッチは 16 例 x 最大 11〜21 トークン
x hidden 128、モデルは 406,851 パラメータしかない。この規模では OpenMP のバリアが
演算より高くつく。実行中プロセスの CPU 時間は utime がほぼ全部で stime はゼロに近く、
ユーザ空間のスピン待ちがコアを焼いていた形と一致する。

## 検証: この仮説は観測値を再現するか

ベンチから予測した比と、実パイプラインの実績比を突き合わせた。

| 量 | 値 |
|---|---:|
| ベンチ予測比 (alakazam@28 / grimmsnarl@14) | 0.0808 |
| 実パイプライン比 (0.0486 / 0.600) | 0.0810 |
| 一致度 | 0.997 |

内訳も一致する。

| 要因 | 倍率 |
|---|---:|
| スレッド過剰 (14 → 28) | 8.56x |
| データ形状 (token 幅 11 → 21) | 1.45x |
| 積 | **12.39x** |
| 実測差 | **12.35x** |

## 否定した原因

| 候補 | 判定根拠 |
|---|---|
| メモリ不足・swap | `VmSwap: 0 kB`、空き 23 GiB |
| モデルが大きい | 全レーン checkpoint 4.8 MB、406,851 パラメータで同一 |
| 例数・rows が多い | rows/例 は alakazam 1.08、grimmsnarl 1.08 |
| 時間経過で劣化 | checkpoint 間隔が両レーンとも平坦 |

## 学習結果はスレッド数に依存しない

修正が速度だけを変えることの根拠。本番と同じ設定 (seed 0、lr 1e-3、examples/step 64、
microbatch 16、max_gradient_norm 1.0、value_coefficient 0.5) で AdamW ステップを
12 回まわし、スレッド数を変えて比較した。

| threads | param SHA-256 (先頭 16) | loss 差 最大 | 勾配ノルム差 最大 |
|---:|---|---:|---:|
| 28 | `0adb95e2c6d07814` | (基準) | — |
| 14 | `0adb95e2c6d07814` | 0.000e+00 | 0.000e+00 |
| 4 | `0adb95e2c6d07814` | 0.000e+00 | 0.000e+00 |
| 2 | `0adb95e2c6d07814` | 0.000e+00 | 0.000e+00 |

更新後パラメータがビット単位で一致する。スレッド数は速度だけを変える。

### 実データ・実行規模での確認

上は 12 step の合成的な確認なので、本番の run 同士でも突き合わせた。alakazam の
step 200 checkpoint を、28 スレッドで走った旧 run と 4 スレッドで走った新 run で比較
した (同 seed、同 corpus)。

| 比較対象 | 結果 |
|---|---|
| model パラメータ | 41 テンソル中 不一致 **0** |
| optimizer 状態 | 123 テンソル中 不一致 **0** |
| metadata | 差なし |
| `cpu_rng_state` | **不一致** |

checkpoint の content hash が run 間で変わるのは `cpu_rng_state` だけが理由である。
学習は専用の `torch.Generator().manual_seed(seed)` を使うため、global RNG は軌跡へ
影響しない。

`neural_checkpoint_behavior_identity_v1` はファイルの実バイトを hash する。したがって
同一の重みでも run が変われば identity は変わる。これは「読んだ成果物そのものを同定
する」という同関数の設計どおりであり、動作の同値類を表すものではない。

## 修正

`run_parallel_lanes.py` が 1 つの数で配っていた 2 種類の並列度を分けた。

| 用途 | 並列の種類 | 配る値 |
|---|---|---|
| 教師収集 (`--workers`) | プロセス | コア予算そのまま |
| shard 読み込み (`--read-workers`) | プロセス | コア予算そのまま |
| BC / RL 学習 (`--torch-threads`) | torch intra-op | `min(--max-torch-threads, コア予算)`、既定 4 |

`--read-workers` は新設した。従来 `run_bc_distillation.py` は読み込みワーカー数を
`--torch-threads` に連動させていたため、スレッドを下げると起動時の shard 読み込みまで
28 ワーカーから 4 ワーカーへ落ちてしまう。

`rl-train` (`train-from-trajectories`) は `--torch-threads` を渡しておらず、既定の
全コアで走っていた。同じ上限を適用した。

回帰テストは `tests/meta_specialist/test_lane_thread_budget_v1.py`。実際に argv を
組み立てて検査する。修正前のコードに対しては 12 件中 11 件が失敗する (残る 1 件は
収集ワーカーへ cap を掛けないことを守る側で、修正前でも通るのが正しい)。

## 修正後の実測

alakazam を同じ設定で再実行した結果。

| | 修正前 | 修正後 |
|---|---:|---:|
| torch スレッド | 28 | 4 |
| steps/s | 0.0486 | **1.588** |
| 4000 step の所要 | 約 22 時間 | **約 42 分** |

**32.7 倍**の高速化。ベンチからの予測 35.6 倍とほぼ一致する (差は同時に走る評価が
コアを使うため)。

既に完走している archaludon / grimmsnarl / rocket の θ0 は再実行していない。14
スレッドで出た成果物は 4 スレッドで出るものと同一であることが上の通り確認できて
いるため、再実行しても同じ重みになる。

## 未確認事項

- 最速は 2 スレッドだが既定は 4 とした。実測差は 1.04 倍で、レーン数が増えたときの
  余裕を取っている。2 へ下げる価値があるかは (要検証)。
- GPU 実行は未測定。CPU 側でこの 37 倍を取り戻せるため、GPU 化の優先度は下がった。
  `train-from-trajectories` には `--device` があるが、`run_bc_distillation.py` には
  無く、`build_ragged_step_batch_v1` が入力を CPU 上に作るため現状は動かない。
