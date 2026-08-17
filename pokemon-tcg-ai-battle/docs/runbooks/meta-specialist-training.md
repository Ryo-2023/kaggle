# Runbook: meta-specialist の軌跡収集と学習

収集済み軌跡から実際に optimizer step を回すまでの手順書。実測値はすべて 2026-08-04 に
worktree `meta-specialist-p0` で計測したもので、条件を併記する。数値は環境依存であり、
別マシンでは再計測する。

## 実行場所

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-p0
```

Python は main repo の `.venv` を使う（worktree 側に仮想環境は作らない）。

```bash
PY=/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python
```

## 1. 収集（済んでいれば飛ばす）

```bash
PYTHONPATH=.:src $PY scripts/collect_meta_specialist_trajectories.py \
  --num-games 2000 --base-seed 2000000 --run-name p0-rule-agent-2000
```

現状 `runs/meta-specialist-actor-pool/p0-rule-agent-2000/games` に **4,270局 /
87,258 transition** が蓄積済み。内訳と品質検証は
[collection-node-limit-and-resume-20260804.md](../evidence/collection-node-limit-and-resume-20260804.md)
を参照。

再収集すると既存局は再利用されない。`derive_actor_job_id_v1` が `source_commit` を
含むため、コミットが1つ入るだけで全ジョブIDが変わる。これは意図した provenance 固定で
あり、集計出力が「別 `source_commit` の既存 N 局は再利用しない」と明示する。

## 2. 学習

### 発散は解消済み（BC項の追加）

固定コーパスに対するオフライン学習では、`-(advantage × log π)` が下に非有界であるため、
何も錨が無いと収集行動の log π が際限なく下がり、ρ→0 で学習が停止する。設計が定める
**BC 損失**（`--bc-coefficient`、既定 0.1）がその錨である。実測と係数スイープは
[vtrace-degenerate-collapse-20260804.md](../evidence/vtrace-degenerate-collapse-20260804.md)
を参照。

実収集4,270局・80ステップで dlogp **+1.42（収集行動へ接近）**、dead_rho **0.000**、
critic も較正されることを確認済み。

**`--bc-coefficient 0` にしないこと。** 0.5 以上は rule agent の純粋模倣へ飽和する。

### 実行コマンド

```bash
PYTHONPATH=.:src $PY scripts/train_meta_specialist_from_trajectories.py \
  --collection-run-dir runs/meta-specialist-actor-pool/p0-rule-agent-2000 \
  --run-name p0-train-round1 \
  --trajectories-per-step 64 \
  --max-steps 1000 \
  --checkpoint-interval-steps 50 \
  --device cpu
```

### `--trajectories-per-step` は必ず指定する

省略すると **1 step が admitted 全件**（4,270局・87,258 transition）を1つの autograd
graph に載せる。実測で **RSS 27 GB を超えてなお増加中**（47 GB マシン）となり、1 step も
完了しない。省略時は実行前に拒否され、推奨値が提示される。

| `--trajectories-per-step` | transition/step | graph 増分 |
|---|---|---|
| 32 | 554 | +0.14 GB |
| 64 | 1,325 | +0.17 GB |
| 128 | 2,495 | +0.24 GB |
| 256 | 5,298 | +0.59 GB |

`--trajectories-per-step 64` の実測は **2.83 s/step**（実収集4,270局、12ステップ完走、
peak RSS 9.9GB）。

### 高速化の内訳

| 施策 | 効果 |
|---|---|
| step毎の再検証を廃止（起動時1回へ） | 5.50 → 0.84 ms/transition（scoring部分 6.5倍） |
| 候補encodeをminibatch全体で共有 | 1.21倍（8,794回の出現 → 473個の実encode） |
| `torch.set_num_threads(1)` | **4.0倍** |

スレッド数の実測（実64局minibatch、forward+backward）:

| threads | 1 | 2 | 4 | 8 | 14（torch既定） |
|---|---|---|---|---|---|
| 実測 | **2.42s** | 2.55s | 2.74s | 5.48s | 9.62s |

モデルが小さくopが細かいため、スレッド同期のコストが演算量を上回る。既定を 1 とし、
`--torch-threads` で変更できる。モデルを大きくした場合は再計測する。

合計で **13.9s → 2.83s（約4.9倍）**。

### `--device cuda` は使わない

実測（NVIDIA RTX PRO 5000 Blackwell、実 transition 200件）:

| device | 実測 |
|---|---|
| cpu | 3.01 ms/transition |
| cuda | 8.71 ms/transition（**約 2.9 倍遅い**） |

モデルが約39万パラメータと小さく、1決定が細かい逐次カーネルの連鎖になるため、
kernel launch と転送のオーバーヘッドが演算量を上回る。設定の問題ではない。
モデルを大幅に大きくするか transition 間の真のバッチ化を入れた場合は再計測する。

## 3. 進捗の見え方

3フェーズがそれぞれ**単一の更新式バー**で表示される。同時に出るバーは常に1本。

```
load-trajectories:    100%|██████████| 4270/4270 [07:26<00:00, 9.56game/s, faults=0, transitions=87258, valid=4270]
prepare-transitions:  100%|██████████| 87258/87258 [04:46<00:00, 305transition/s, games=4270]
train-from-trajectories: 42%|████▏ | 5/12 [00:14<00:19, 2.83s/step, clip_hi=0.051, dead_rho=0.22, dlogp=-5.26, grad=0.0141, loss=-0.0001, loss_avg=-0.034, trend=down, skipped=0]
```

### 学習が進んでいるかの判断材料

loss と grad だけでは、学習しているのか退化しているのか区別できない。bar の postfix と
集計出力に次を出す。

| 表示 | 意味 | 見方 |
|---|---|---|
| `dlogp` | `mean(log π_target − log π_behavior)` | 単調に負へ進むなら発散。0付近で動かないなら学習していない |
| `dead_rho` | `rho < 0.01` の割合 | 上昇するほど勾配が消えている。0.5超でWARNINGを出す |
| `clip_hi` | `rho > rho_bar` の割合 | 1.0近くなら勾配がほぼ切り捨てられている |
| `loss_avg` / `trend` | 直近20ステップの平均と方向 | 単一stepのノイズを均した傾向 |

`clip_hi` が小さくても `dead_rho` が上がっていれば発散である。上側だけを見ない。

全step分は `run_summary.json` の `learning_health_per_step` に残る。

- TTY では tqdm のバー。リダイレクト時は 10 秒ごとの集約スナップショット行になる。
- `--progress` で `tee` 経由でもバーを強制、`--no-progress` で抑制。
- 機械可読な現在値は `runs/meta-specialist-training/<run-name>/progress_summary.json`
  に atomic に更新される。別端末から `cat` すれば進捗が取れる。

`load-trajectories` と `prepare-transitions` は合計で**約12分の固定起動コスト**。
以前はここが無表示で停止と区別できなかった。

### 準備フェーズは何をしているか

各 transition の payload 検証と step input の再構築は**モデルのパラメータに依存しない**。
step ごとに再実行すると `--max-steps` 回だけ同一結果を再導出することになり、実測で
モデルの forward より高くついていた。これを起動時1回に移した結果、step あたりの
scoring は **5.50 ms → 0.84 ms/transition（6.5倍）**。

同一性は `tests/meta_specialist/test_trajectory_target_equivalence_v1.py` が、実収集
データに対する **log-probability と勾配の一致**で担保している。速くなっても値が変われば
意味がないため、許容差は float32 の丸め相当に固定している。

メモリは prepared transition 1件あたり実測 **約52 KB**。87,258件で約 4.3 GB。

## 4. 中断と再開

同じ `--run-name` で同じコマンドを再実行すると、`latest_checkpoint.json` から
step / sampler cursor / optimizer / RNG を復元して続きから進む。

`training_identity`（snapshot・topology・recipe・seed）が1つでも違うと**拒否**される。
`--trajectories-per-step` や `--learning-rate` は recipe に含まれるため、途中で変えると
再開できない。変える場合は新しい `--run-name` を使う。

## 5. 既知の制約

- **value head が無い**。全 transition の `value` は固定の 0.0 で、V-trace の value 項は
  勾配を持たない。実際の勾配はすべて policy-gradient 項から来る。`--value-coefficient` は
  報告される loss の大きさだけを変える。
- **entropy 項が無い**。係数は 0.0 に固定されており、entropy bonus を捏造しない。
- **収集データの勝率が 12.6%**（seat0 11.5% / seat1 13.8%）。subject の `rule_agent` が
  対戦相手 `cabt_rule_agent_v0` に大きく負け越している。この偏りは学習信号にそのまま
  乗る。(要検証) 原因未特定。
- `alakazam` レーンに `worker exited with code 1` の fault が 19/667 残る。原因未解明。

## 6. 提出はしない

このリポジトリのどの経路からも Kaggle へ送信しない。bundle の build とローカル検証は
行ってよいが、外部送信はユーザーが対象と実行を明示した場合だけ扱う。
