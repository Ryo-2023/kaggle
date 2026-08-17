# Runbook: 強 teacher からの θ0 蒸留

## 結論

正典 §1 の Foundation θ0 を、乱数初期化ではなく**既知の強い teacher の複製**として作る
経路が動く状態になった。3 段階で、いずれも再実行可能である。

```
① teacher 収集   run_teacher_collection.py   →  records/*.jsonl + teacher_dataset_manifest.json
② 封印           seal_teacher_dataset.py     →  snapshot.json (train/development/test)
③ BC 蒸留        run_bc_distillation.py      →  checkpoints/*.pt (FoundationInit provenance 付き)
```

以降 `PY` と `PYTHONPATH` は次とする。

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical
export PYTHONPATH=.:src
PY=/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python
```

## ① teacher 収集

teacher に subject デッキを操縦させ、その決定を BC target として記録する。

```bash
$PY scripts/run_teacher_collection.py \
  --archetype-id grimmsnarl_froslass_munkidori \
  --teacher-id ozawa_grimmsnarl_v2 \
  --num-games 300 --base-seed 5100000 \
  --run-name grimmsnarl-teacher-300
```

- 相手はプールの**高速 (<=1ms) な相手**から決定的に抽出する。探索相手 (410-487ms) は
  評価専用であり、学習ループへ入れると収集が桁で遅くなる。
- 座席は自動で均衡する (subject_first / subject_second が同数)。
- **敗局を捨てない** (正典 §9.3)。outcome は `value_target` にだけ効く。
- 表現できない決定は `teacher.status="unavailable"` として数え、局ごと落とさない。

実測 (2026-08-05、300局): grimmsnarl 24,087 records / rocket 17,860 records、
いずれも fault 0・未ラベル 0・座席 150/150。所要は 1 レーンあたり十数分程度。

## ② 封印

records を 1 つの dataset snapshot にまとめ、manifest と permission を検証した上で
train/development/test へ分割する。split は episode / near-duplicate の連結成分単位で
割り当てられるので、同一局が split を跨がない。

```bash
$PY scripts/seal_teacher_dataset.py \
  --collection-run-dir runs/meta-specialist-teacher-records/grimmsnarl-teacher-300 \
  --archetype-id grimmsnarl_froslass_munkidori \
  --output runs/meta-specialist-teacher-records/grimmsnarl-teacher-300/snapshot.json
```

**注意: これは重い。** 24,087 records で 10 分以上、出力は約 250 MB。全 example を 1 つの
JSON 文書へ入れて canonical hash を取る設計のため、records 数に対して線形以上に伸びる。
長時間実行する場合は切り離して起動すること。

```bash
setsid nohup env PYTHONPATH=.:src $PY scripts/seal_teacher_dataset.py ... \
  > seal.log 2>&1 < /dev/null &
```

## ③ BC 蒸留 (θ0 生成)

```bash
$PY scripts/run_bc_distillation.py \
  --snapshot runs/meta-specialist-teacher-records/grimmsnarl-teacher-300/snapshot.json \
  --archetype-id grimmsnarl_froslass_munkidori \
  --deck-csv opponents/ozawa_grimmsnarl_v2/deck.csv \
  --teacher-id ozawa_grimmsnarl_v2 \
  --decision-ref docs/decisions/2026-08-05-archaludon-teacher-derivation.md \
  --run-name grimmsnarl-theta0 \
  --max-steps 2000 --examples-per-step 64 --microbatch-examples 16 \
  --checkpoint-interval-steps 100
```

出力は `runs/meta-specialist-bc-distill/<run-name>/` の下。

- `progress_summary.json` を 10 step ごとに atomic 更新する。端末へは checkpoint 時だけ出力。
- checkpoint の `metadata.foundation_init` に teacher の `policy_hash`、
  `usage_boundary`、`derivation_boundary`、`decision_ref` が入る。
- **`derivation_boundary` が `derivation_qualified` でない teacher からは θ0 を作れない。**
  未決の licence 判断は拒否される (正典 §5)。判断は
  `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` に記録する。
- Rule Agent v0 を teacher に指定すると拒否される。

smoke 実測 (264 examples、20 step): loss 1.6226 → 0.8491、skip 0、9.8 秒。

### 見るべき指標

`loss` の低下だけで判断しない。以前の失敗 (2026-08-04) は loss が下がりながら方策が
「rule agent の鋭くした複製」へ収束したものだった。**teacher の複製が目的である本工程では
それが正しい挙動**だが、後段の RL でも同じ形に留まっていないかは別途確認する。

## 次段: RL

θ0 を初期値として `train_from_trajectories_v1` (V-trace) へ渡す。**実装済み**。
下の「④ θ0 → RL 接続」を参照。

## 現在の対象レーン

| レーン | teacher | 対プール実測 (n=72) |
|---|---|---|
| `grimmsnarl_froslass_munkidori` | `ozawa_grimmsnarl_v2` | 76.4% [0.65, 0.85] |
| `rocket_mewtwo_spidops` | `ozawa_rocket_v2` | 72.2% [0.61, 0.81] |
| `archaludon` | 上記 θ0 からの転移 (未実施) | 公開 3 体は 4.2 / 8.3 / 5.6% (対照: `ozawa_grimmsnarl_v2` 76.4%) |

archaludon には強い teacher が存在しないため、他レーンの θ0 を転移させる方針である
(正典 §8.4 の deck 非依存性が根拠)。**転移の実効は未測定**なので、小規模で先に確かめること。

---

# 追記 (2026-08-05): 残り 4 経路の実装完了

## ④ θ0 → RL 接続

`train-from-trajectories` に `--bootstrap-checkpoint` を追加した。

```bash
$PY -m mage_ptcg.meta_specialist train-from-trajectories \
  --collection-run-dir runs/meta-specialist-actor-pool/<collection> \
  --run-name grimmsnarl-rl-from-theta0 \
  --max-steps 2000 \
  --bootstrap-checkpoint runs/meta-specialist-bc-distill/grimmsnarl-theta0-300/checkpoints/checkpoint-<sha>.pt
```

**重みだけを読む。** optimizer / scheduler / RNG / step / sampler cursor は新規にする。
θ0 は教師あり (BC) で当てた重みであり、この run は V-trace である。supervised な目的で
推定した Adam の moment を持ち込む理由がない。

- topology 不一致は**失敗する**。合う層だけ読んで残りを乱数のまま残すと、「θ0 から
  始めた」と記録しながら実質ほぼ乱数初期化になる。
- checkpoint 名 (`checkpoint-<sha256>.pt`) と中身の不一致も失敗する。
- Rule Agent v0 由来の θ0 は拒否される。
- この run の checkpoint は `init_kind=warm_start`、親に θ0 の sha256、teacher を保持。
  **teacher → θ0 → RL run の系譜が checkpoint だけから読める。**

回帰テスト: `tests/meta_specialist/test_theta0_bootstrap.py` (5 件)。

## ⑤ curriculum の配線

正典 §13 の `local_strength_band` を実際の相手選択へ繋いだ。2 段階である。

### 5-1. プールの banding (長時間)

```bash
$PY scripts/run_opponent_calibration.py \
  --panel kiyotah_lucario,ozawa_grimmsnarl_v2,ozawa_rocket_v2 \
  --games-per-seat 32 --base-seed 7700000 \
  --run-name pool-calibration-v1
```

- band は**実測でのみ**与えられる。Kaggle のメダルや出所からは継承されない。
- **`--games-per-seat` を十分大きくすること。** 2 (n=12) では 56 体すべてが
  `ambiguous` になった。これは欠陥ではなく、CI が広すぎて banding を拒否する
  正しい fail-closed である。実 band を得るには 32 以上を推奨する。
- 全局が fault した matchup は 0-0 を「引き分け」にせず、その相手を未 banding として残す。
- `local_strength_manifest.json` を 1 体ごとに atomic 更新するので、中断しても
  そこまでの結果は残る。

### 5-2. phase → 相手の展開

`curriculum_opponents_v1.select_phase_opponents_v1(phase, band_map=..., available=..., games=N)`
が phase の mixture を局ごとの相手列へ展開する。

- `ambiguous` と未 banding の相手は**使わない**。
- quota を満たせない band があれば**失敗する**。他 band で埋めると「top_focus を
  走らせた」と記録しながら実際は lower 中心、というずれになる。

回帰テスト: `tests/meta_specialist/test_curriculum_opponents_v1.py` (5 件)。

## ⑥ census (正典 §16)

`census_fetch_v1` に取得の状態機械・pacing・resume を実装した。

- **8 状態の SQLite 状態機械**: `pending` / `submission_fixed` / `episode_fixed` /
  `replay_fetched` / `deck_extracted` / `qualified` / `retry_wait` / `terminal_failure`
- **pacing**: 初期 2 秒、100 成功かつ 429 なしで 10% 短縮、floor 0.5 秒
- **circuit breaker**: 最初の 429 で開く。`Retry-After` を厳守、無ければ 60 秒から
  指数的に。解除時は probe 1 件だけ
- **resume**: `census_id` を封印し、resume 中に別 snapshot へ切り替えられない。
  `retry_wait` は `not_before_utc` を過ぎるまで再取得しない
- **決定的な選択**: submission は public score 最大 → submitted-at 新しい順 →
  submission ID 小さい順。episode は両 deck 完備のうち ID 最小

**transport は注入する。** `requests` も Kaggle CLI も直接呼ばない。取得規律の
テストが credential もネットワークも要求しないのは設計である。

回帰テスト: `tests/meta_specialist/test_census_fetch_v1.py` (24 件)。

**未接続**: 実際の Kaggle 取得 (`competition_intelligence/live_payloads.py` が
leaderboard / submissions / episodes を扱う) をこの状態機械へ繋ぐ配線は残っている。
credential が要るため、ユーザー側で実行する前提の作業である。

## ⑦ 封印のスケール

実測 (24,087 records / 251 MB snapshot):

| 工程 | 変更前 | 変更後 |
|---|---:|---:|
| 封印 (`seal_teacher_dataset.py`) | 10 分超 | 変更なし |
| `read_training_snapshot_v1` | 71.8s | 変更なし |
| `snapshot_examples_for_split_v1` | 61.9s | **36.0s** |

split 抽出は example ごとに canonical JSON の直列化 → 再パースの往復をしていた。
`validate_training_snapshot_v1` が 1 行上で検証済みの値から作ったバイト列を、もう一度
全検証していたことになる。deep copy へ変えて分離は維持したまま短縮した。

封印そのものは依然として重い。全 example を 1 つの JSON 文書へ入れて canonical hash を
取る設計に由来するため、短縮には snapshot 形式の変更が要る。**未対応**。
長時間実行では切り離して起動すること。

---

# 本番学習の起動手順 (2026-08-05 時点)

## 4 レーンの teacher と実測強度

同一 12 相手・座席均衡での実測。対照として計測系の健全性も確認済み。

| レーン | teacher | 対プール勝率 | RL 段 (`collect-trajectories`) |
|---|---|---|---|
| `alakazam` | `nihei_alakazam` | **89.6%** [0.78, 0.95] n=48 | 可 (qualified) |
| `grimmsnarl_froslass_munkidori` | `ozawa_grimmsnarl_v2` | 76.4% [0.65, 0.85] n=72 | 可 (qualified) |
| `rocket_mewtwo_spidops` | `ozawa_rocket_v2` | 72.2% [0.61, 0.81] n=72 | 可 (qualified) |
| `archaludon` | なし → 他レーン θ0 からの転移 | 公開 3 体は 4.2 / 5.6 / 8.3% | **不可** (未 qualified) |

crustle は外している。`ozawa_crustle_v2` が `crustle_mega_kangaskhan` の core
(756 = Mega Kangaskhan ex) を持たないためである。詳細は
`docs/decisions/2026-08-05-crustle-deck-core-mismatch.md`。

## 端末表示

TTY では **単一の更新式 progress bar** が出る。postfix に判断に必要な集計値
(loss / grad / records / faults / win-loss / band など) が載り、局や step ごとの
行ログは出ない。

**出力を `tee` や pipe へ通さないこと。** carriage return が解釈されず bar の断片が
大量に出る。pipe を検出すると自動で 10 秒ごとの集約スナップショットへ落ちるので事故には
ならないが、bar は見えなくなる。バックグラウンド実行でログファイルへ落とす場合は
その集約モードになる。

## 起動

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical
export PYTHONPATH=.:src
PY=/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python
```

### Step 1: teacher 収集 (3 レーン、各 15〜20 分)

grimmsnarl と rocket は 300 局分が収集済みなので、残り 2 レーンだけでよい。
**別々の端末で並列に走らせると bar が混ざらない。**

```bash
# 端末 A
$PY scripts/run_teacher_collection.py \
  --archetype-id alakazam --teacher-id nihei_alakazam \
  --num-games 300 --base-seed 5300000 --run-name alakazam-teacher-300

# 端末 B
$PY scripts/run_teacher_collection.py \
  --archetype-id archaludon --teacher-id tomatomato_archaludon \
  --num-games 300 --base-seed 5400000 --run-name archaludon-teacher-300
```

### Step 2: 封印 (1 レーンあたり 10 分超、出力 約 250 MB)

**重い。切り離して起動すること。**

```bash
for lane in alakazam:alakazam-teacher-300 archaludon:archaludon-teacher-300; do
  a="${lane%%:*}"; r="${lane##*:}"
  setsid nohup env PYTHONPATH=.:src $PY scripts/seal_teacher_dataset.py \
    --collection-run-dir "runs/meta-specialist-teacher-records/$r" \
    --archetype-id "$a" \
    --output "runs/meta-specialist-teacher-records/$r/snapshot.json" \
    > "/tmp/seal-$a.log" 2>&1 < /dev/null &
done
```

### Step 3: θ0 蒸留 (16,000 examples / 300 step で約 8 分)

grimmsnarl は完了済み (loss 1.347 → 0.665、skip 0)。残り 3 レーン:

```bash
# rocket (snapshot は封印済み)
$PY scripts/run_bc_distillation.py \
  --snapshot runs/meta-specialist-teacher-records/rocket-teacher-300/snapshot.json \
  --archetype-id rocket_mewtwo_spidops --deck-csv opponents/ozawa_rocket_v2/deck.csv \
  --teacher-id ozawa_rocket_v2 \
  --decision-ref docs/decisions/2026-08-05-archaludon-teacher-derivation.md \
  --run-name rocket-theta0 --max-steps 2000 \
  --examples-per-step 64 --microbatch-examples 16 --checkpoint-interval-steps 200

# alakazam / archaludon は Step 2 完了後に同じ形で
```

### Step 4: RL (θ0 を初期値に、3 レーン)

先に `collect-trajectories` で軌跡を集め、`--bootstrap-checkpoint` に θ0 を渡す。

```bash
$PY -m mage_ptcg.meta_specialist collect-trajectories \
  --lanes alakazam --num-games 2000 --base-seed 6100000 \
  --workers 10 --run-name alakazam-rl-collect

$PY -m mage_ptcg.meta_specialist train-from-trajectories \
  --collection-run-dir runs/meta-specialist-actor-pool/alakazam-rl-collect \
  --run-name alakazam-rl --max-steps 5000 \
  --bootstrap-checkpoint runs/meta-specialist-bc-distill/alakazam-theta0/checkpoints/checkpoint-<sha>.pt
```

`<sha>` は Step 3 の `run_summary.json` の `final_checkpoint` から取る。

## 進捗の確認

bar とは別に、各 run の artifact に atomic 更新される JSON がある。
別端末から読める。

```bash
cat runs/meta-specialist-bc-distill/<run>/progress_summary.json
cat runs/meta-specialist-teacher-records/<run>/teacher_dataset_manifest.json
cat runs/meta-specialist-calibration/<run>/local_strength_manifest.json
```

---

# 実測: θ0 の到達度と archaludon への転移 (2026-08-05)

同一 10 相手 (プールの高速相手からランダム抽出、seed 11)、各 4 局、座席均衡。
subject は `grimmsnarl-theta0-300` の最終 checkpoint (300 step / 16,333 examples)。

| 条件 | n | win% | CI95 (Wilson) |
|---|---:|---:|---|
| θ0 + grimmsnarl デッキ (対照) | 40 | 40.0% | [0.26, 0.55] |
| θ0 + archaludon デッキ (転移) | 40 | 30.0% | [0.18, 0.45] |
| (参考) teacher `ozawa_grimmsnarl_v2` | 72 | 76.4% | [0.65, 0.85] |
| (参考) 公開 archaludon 3 体 | 各 72 | 4.2 / 5.6 / 8.3% | — |

## 読み取れること

**1. 転移は有効である。** grimmsnarl の teacher から蒸留した θ0 が、一度も見ていない
archaludon デッキで 30.0% [0.18, 0.45] を出した。公開 archaludon エージェント 3 体
(4.2 / 5.6 / 8.3%、いずれも上限が 0.17 以下) を上回る。正典 §8.4 の deck 非依存性
(「seed deck 専用の固定 action ID に依存しない」) が設計上の性質にとどまらず実効を
持つことの、最初の実測である。fault は 0/6 で、合法手を返せることも確認済み。

**2. θ0 はまだ teacher に届いていない。** 40.0% に対し teacher は 76.4% である。
300 step は 16,333 examples に対して約 1.2 epoch でしかない。**`--max-steps` を
大きくすべきである。** 蒸留の目的は teacher の複製なので、teacher との差が縮まらない
うちに RL へ進めても、正典 §1 の「既知の強い方策を起点にする」意図を満たさない。

## 限界

- n=40 (相手ごと n=4)。相手別の値は出せない。
- プールは Kaggle のメタではない (正典 §13 の `local_strength_band` であって
  `source_rank_band` ではない)。ラダー成績を意味しない。
- 転移は grimmsnarl θ0 の 1 本でしか測っていない。rocket / alakazam θ0 でも同じか、
  どの teacher からの転移が最良かは未測定。

## 帰結

- 本番の θ0 蒸留は **`--max-steps` を 300 ではなく 2000 以上**にする (runbook の
  起動手順はそうしてある)。teacher との差を縮めてから RL へ渡す。
- archaludon は「強い teacher が無いレーン」ではあるが、**他レーンからの転移で
  公開エージェントを大きく上回る**ことが分かった。どの θ0 から転移させるのが最良かは
  3 レーンの θ0 が揃ってから比較する。
