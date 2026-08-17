# meta-specialist RL の停滞、原因切り分け、対策と検証（2026-08-07〜08 全記録／引き継ぎ）

このファイルは 2026-08-07〜08 の作業全体を 1 枚にまとめた引き継ぎ資料である。後続の担当が
これだけ読めば、何が分かっていて、何が未解決で、次に何を打てばよいかが分かることを
目的とする。

- 対象ブランチ: `feature/meta-specialist-canonical`
- worktree: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`
- 起点 commit: `a4e64752`
- 記録時点: 2026-08-08 13:40 JST（t3 完走・384 局検証まで反映）
- **未コミット。** 変更一覧は §9 にある。

---

## 0. 結論（先に読む）

1. **対策 4 件は機構としては成功した。** t2 で観測した「エントロピーだけ膨張して着手の質が
   変わらない」現象は止まり、収集スコアの傾きが -0.0120/round から +0.0031/round へ反転した
   （挙動の差は 4.8σ）。詳細は §7。
2. **勝率への転移は 1 レーンだけである。** held-out 6 体・greedy・384 局で
   **alakazam が θ0 0.423 → 0.665（+0.241、p<0.001）**。
   残り 3 レーンは合算 0.374 → 0.364（-0.011、p=0.603）で有意な変化なし。
3. **本命の archaludon はまだ θ0 が最良である。** 0.381 → 0.367（-0.014、p=0.671）。
   t2 で 0.297 まで落ちたところから θ0 水準へ戻したが、上回ってはいない。
4. **4 レーン合算の +0.054（p=0.003）は alakazam 単独が作った数字である。**
   合算だけを見て「RL が効いた」と要約してはならない。
5. **評価が再現しない問題は未解決のまま残っている。** 同一 checkpoint・同一 seed・greedy で
   0.302〜0.448 に散る（§4.1）。A/B を繰り返すなら先に直したほうが総コストは下がる。
6. **採用状況**: alakazam のみ RL checkpoint が θ0 を上回った。他 3 レーンは θ0 が最良。
   ただし Promotion Gate は通していないので、Champion 差し替えは別途判断すること。

---

## 1. このセッションで扱った範囲

時系列で 6 つの塊があった。1〜3 は個別の証拠文書があるので、ここでは要点と参照のみ置く。

| # | 内容 | 詳細 |
|---|---|---|
| 1 | BC の 12 倍遅延（torch スレッド過剰割り当て） | [bc-thread-oversubscription-20260807.md](bc-thread-oversubscription-20260807.md) |
| 2 | RL 1 ラウンドの費用分解と actor fault の原因 | [rl-round-cost-and-actor-faults-20260807.md](rl-round-cost-and-actor-faults-20260807.md) |
| 3 | t1 の失敗（ミラー相手のみで学習）と相手プール是正 | [vtrace-rl-degrades-against-eval-pool-20260807.md](vtrace-rl-degrades-against-eval-pool-20260807.md) |
| 4 | 評価の再現性が壊れている発見 | 本書 §4 |
| 5 | t2 の停滞と原因切り分け、対策実装 | 本書 §5〜§6 |
| 6 | t3 の実行と 384 局検証 | 本書 §7 |

3 回の RL run の位置づけ:

| run | ラウンド | 学習相手 | 学習信号の設定 | held-out 結果 |
|---|---:|---|---|---|
| t1 | 14 | ミラー 1 体 | 既定 | 4 レーンとも θ0 未満（§3） |
| t2 | 6 | 加重 96 体 | 既定 | 4 レーン合算 横ばい（§5.2） |
| t3 | 8 | 加重 96 体 | 対策 4 件 | alakazam のみ大幅改善（§7） |

---

## 2. 用語と実行の形

### 2.1 レーンとデッキ

4 レーン並列で回す。ユーザーの本命は **archaludon（ブリジュラス）**。

| lane | archetype_id | subject deck |
|---|---|---|
| archaludon | `archaludon` | `opponents/public_archaludon_cinderace_r7/deck.csv` |
| grimmsnarl | `grimmsnarl_froslass_munkidori` | `opponents/ozawa_grimmsnarl_v2/deck.csv` |
| alakazam | `alakazam` | `opponents/nihei_alakazam/deck.csv` |
| rocket | `rocket_mewtwo_spidops` | `opponents/ozawa_rocket_v2/deck.csv` |

### 2.2 1 ラウンドの構成

```
collect (500局 x 4レーン, sample デコード, 学習プール96体)
  -> train (V-trace, N step x 4レーン)
  -> eval  (24局 x 4レーン, greedy, held-out 6体)
```

`runs/pipeline-logs/run_overnight.sh` が締切まで反復する。RUN_TAG が成果物の名前空間で、
**t1 / t2 / t3 は使用済み。次は t4。**同じタグで再実行すると checkpoint ポインタと収集
ディレクトリを上書きする。

各 run の起点は毎回 θ0 であり、前の run を引き継がない（`run_overnight.sh` の
「起点を θ0 に置く」段）。t3 の結果は t2 の続きではなく、**θ0 からの独立な 8 ラウンド**である。

### 2.3 相手プール（102 体）

- **学習 96 体**: 加重 schedule `configs/meta_specialist/opponent_schedule_v1.json`
  （1 周 538 局、`0.7 x メタ比率 + 0.3 x 一様`）。生成は `scripts/make_opponent_schedule.py`。
- **評価 6 体（held-out、絶対に学習へ使わない）**:
  `kiyotah_lucario` / `sue124_alakazam` / `skarin_dragapult` / `ozawa_crustle_v2` /
  `nihei_megalopunny` / `yaroslav_crustleaware_lucario`
- 全 102 体が `usage_boundary: local_eval_only`。**提出 bundle へ入れてはならない。**

---

## 3. t1 の失敗と是正（済）

詳細は [vtrace-rl-degrades-against-eval-pool-20260807.md](vtrace-rl-degrades-against-eval-pool-20260807.md)。
要点のみ。

- t1 は `cabt_rule_agent_v0` の**ミラー戦のみ**で 14 ラウンド学習し、held-out 6 体で評価
  していた。収集リターンは単調上昇（archaludon `ret` -0.062 → +0.438）したが転移せず。
- 相手プールに 3 つの別々の欠陥があった。
  1. `meta_*` 7 体が 1 局も完走できなかった（`kaggle_environments` は `__code__` を
     持たない callable object に全引数を渡すため `TypeError`）。素の関数で包んで解決、
     0/28 → 28/28。**自分の提出 `main.py` の `agent` は素の関数なので提出側に影響なし。**
  2. medal 圏 62 件のうちカバーできるのが 33 件（53%）だった。36 デッキを追加して 100%。
  3. カバー率と分布は別問題。一様巡回では medal 圏 37.1% の Grimmsnarl が 4.8% に
     しかならない。加重 schedule で最大乖離 23.3pt → 7.0pt。
- 加重実装で見つけた罠 2 件（巡回長 > 実行長で座席固定、素朴な round-robin では短い run
  に重みが出ない）も同文書にある。

---

## 4. 評価が再現しない（**未解決**）

### 4.1 観測

`scripts/measure_opponent_strength.py` は `decoding_mode="greedy"`、
`seed_agent_randomness_v1(base_seed + index)`、`run_match(seed=base_seed + index)` を使う。
**本来は決定的なはずだが、そうなっていない。**

同一 checkpoint（archaludon θ0 = `checkpoint-c17f9222...`）を、同一相手 6 体・
同一 `--base-seed 9400000`・各 96 局で 4 回測った結果:

| 測定 | 勝ち | 局数 | スコア |
|---|---:|---:|---:|
| t1 パイプライン | 43 | 96 | 0.448 |
| t2 パイプライン | 29 | 96 | 0.302 |
| 再現1 | 33 | 96 | 0.344 |
| 再現2 | 39 | 96 | 0.406 |
| **プール** | **144** | **384** | **0.375 [0.328, 0.424]** |

観測レンジ 0.302〜0.448（幅 0.146）。二項分布の標準偏差 0.049 とおおむね整合するので、
**1 回の 96 局測定は「決定的な再測定」ではなく「独立な標本 1 つ」**として扱うほかない。

### 4.2 影響

- **[vtrace-rl-degrades-against-eval-pool-20260807.md](vtrace-rl-degrades-against-eval-pool-20260807.md)
  の「archaludon が p=0.016 で有意に悪化」は言い過ぎである。** 基準の 0.448 は高めに
  出た 1 回で、プール値 0.375 と比べ直すと **p=0.086** で有意ではない。同文書の
  4 レーン符号一致（1/16）と機構の特定は有効なままだが、単一レーンの有意性は撤回する。
- 96 局では 0.137 未満の差が検出できない。384 局で 0.068、4 レーン合算で約 0.04。

### 4.3 未確認の原因候補

- 相手エージェントは `build_opponent_agent_factory_v1(opponent)` が**相手 1 体につき
  1 回**しか呼ばれず、同じ `agent` オブジェクトが 16 局（2 座席 x 8 局）で共有される。
  相手が内部状態を持てば局間で漏れる。これは t1/t2 とも同じ挙動で、変更していない。
- engine 側に seed されない乱数がある可能性。未調査。

### 4.4 当座の回避策（実装済）

`runs/pipeline-logs/compare_theta0.py` が `theta0-baseline-*.json` を**全部合算**して
基準にする。run を回すたびに θ0 の 96 局測定が 1 本増えるので、基準は自動で厚くなる。

t3 完走後（t1 / t2 / t3 の各 96 局 + archaludon のみ再現測定 2 回）:

| lane | θ0 | 局数 |
|---|---|---:|
| archaludon | 0.381 [0.34, 0.43] | 480 |
| grimmsnarl | 0.302 [0.25, 0.36] | 288 |
| alakazam | 0.423 [0.37, 0.48] | 281 |
| rocket | 0.436 [0.38, 0.49] | 287 |

（archaludon の再現測定 2 回は
`runs/meta-specialist-bc-distill/t1-archaludon/theta0-baseline-repro{1,2}.json`。
プロトコルは同一。）

**本書の中で θ0 の数値が節ごとに違うのはこのためである。**§5 は t2 時点（384/192/188/191局）、
§7 は t3 時点（480/288/281/287局）の基準を使っている。比較するときは同じ節の値を使うこと。

---

## 5. t2 の結果と原因切り分け

### 5.1 実行条件

```
RUN_TAG=t2  DEADLINE_HOURS=11  開始 2026-08-07 15:24
RL_GAMES=500  RL_STEPS=80  TRAJ_PER_STEP=64  MICRO_TRAJ=8
adv_norm=none  rho_bar=1.0  c_bar=1.0  entropy=0.01  bc=0.1
value_coefficient=0.5  lr=1e-3  optimizer=adamw  max_grad_norm=1.0
学習相手: 加重96体  評価相手: held-out 6体
```

round 6 完了（20:07）時点でユーザーが停止。1 ラウンド約 48 分。

### 5.2 held-out での結果（384 局 x 4 レーン = 1,526 局）

round 6 の方策を、プール済み θ0 と比較した。

| lane | θ0（プール） | round 6 | 差 | 両側 p |
|---|---|---|---:|---:|
| archaludon | 0.375 [0.33,0.42] 384局 | 0.297 [0.25,0.34] 384局 | -0.078 | 0.022 |
| grimmsnarl | 0.302 [0.24,0.37] 192局 | 0.299 [0.26,0.35] 384局 | -0.003 | 0.949 |
| alakazam | 0.410 [0.34,0.48] 188局 | 0.497 [0.45,0.55] 376局 | +0.088 | 0.049 |
| rocket | 0.445 [0.38,0.52] 191局 | 0.432 [0.38,0.48] 382局 | -0.013 | 0.766 |
| **4レーン計** | **0.381 955局** | **0.381 1526局** | **-0.000** | **0.983** |

**合計で完全に横ばい。**レーン単位の p=0.022 と p=0.049 は 4 回比較のうちの 2 つで、
Bonferroni 補正すると 0.088 / 0.196 となり、どちらも有意ではない。

### 5.3 収集側との食い違いと、その説明

収集時スコア（毎ラウンド約 2,000 局、sample デコード、学習プール 96 体）は下降した。

```
round   archaludon  grimmsnarl    alakazam      rocket        4レーン計       n
    1        0.274       0.425       0.327       0.273   0.325 [0.304,0.345]  1989
    2        0.225       0.412       0.380       0.287   0.325 [0.305,0.346]  1977
    3        0.226       0.399       0.329       0.272   0.306 [0.286,0.327]  1987
    4        0.200       0.396       0.335       0.289   0.304 [0.285,0.325]  1979
    5        0.216       0.382       0.300       0.254   0.288 [0.268,0.308]  1990
    6        0.221       0.355       0.282       0.196   0.263 [0.245,0.283]  1985
```

傾き -0.0120/round、傾きの標準誤差 0.0025 なので **4.8σ**。

**しかしこれは方策が弱くなったのではない。**方策エントロピーが単調に上昇していた
（サンプルした行動の平均 `-log π(a)` は方策エントロピーの不偏推定）。

| lane | round 1 | round 6 | 増加 |
|---|---:|---:|---:|
| archaludon | 0.212 | 0.262 | +24% |
| grimmsnarl | 0.218 | 0.257 | +18% |
| alakazam | 0.192 | 0.266 | +39% |
| rocket | 0.225 | 0.354 | +58% |

収集は `sample`、評価は `greedy`。エントロピーが上がると sample 時の着手はばらけて
勝率が落ちるが、argmax は変わらないので greedy は影響を受けない。
**収集スコアの下降と held-out 横ばいは、これで整合する。**

正しい要約は「悪化した」ではなく
**「6 ラウンド回して、方策の分布は広がったが、着手の質は何も変わらなかった」**。

### 5.4 検証した仮説と判定

| # | 仮説 | 判定 | 根拠 |
|---|---|---|---|
| 1 | 相手プールの偏り（t1 の原因） | 除外 | 加重 96 体で是正済み |
| 2 | critic が系統的に楽観的 | **棄却** | §5.5 |
| 3 | value head が未学習（2026-08-04 の崩壊） | **棄却** | `value_coefficient` の CLI 既定は 0.5 で、実際に 0.5 が渡っている |
| 4 | BC anchor 不在による発散 | **棄却** | `bc_coefficient` の既定 `_DEFAULT_BC_COEFFICIENT_V1 = 0.1` が有効。2026-08-04 は dlogp が -8.0 まで行ったが t2 は -0.48 止まり |
| 5 | 学習率・最適化の破綻 | 棄却 | grad_norm 0.04〜0.17（上限 1.0 に未到達）、loss は 0.06〜0.07 で平坦、発散なし |
| 6 | 方策が収集行動から離れ続ける | **確認** | `dlogp` 全 24 点が負、-0.23 → -0.48 へ単調 |
| 7 | エントロピーボーナスが方策を均す | **一部** | 係数 0.01。方策は 0.19〜0.22 nats と極端に尖っており勾配が効きやすいが、1 ラウンド +0.008 で主因ではない |
| 8 | **報酬信号が薄い** | **確認・本質** | 11,151 transition 中、報酬が非零なのは **192 = 1.7%**。残り 98.3% の advantage は critic のブートストラップのみ |
| 9 | **`rho_bar=1.0` の非対称クリップ** | **確認** | `clip_hi` 19〜30%。ρ=min(1, π_t/π_b) なので「確率を上げたい」行動ほど頭打ち、下げる方向は勾配が消える |
| 10 | **データ再利用が過剰** | **確認** | 1 transition あたり **10.2 回**（consumed 310,794 / admitted 30,361） |
| 11 | 勝率が低く正例が足りない | **確認** | 学習プールに対し 0.22〜0.35。advantage の 59% が負 |
| 12 | **advantage を正規化していない** | **確認** | `advantage = result.pg_advantage.detach()` を素通し。中心化も標準化もなし |

### 5.5 critic 楽観説の棄却（重要な訂正）

セッション中に一度「critic が系統的に楽観的で、それが原因」と述べたが、**これは無効な
比較に基づく誤りだった**。学習ログの `V`（全 transition の平均状態価値）と
`ret`（終端リターン）は別の量であり、両者の差を「楽観度」と呼べない。

本番と同じコード経路を monkeypatch し（`vtrace_bridge_v1.evaluate_vtrace_v1_torch` を
包んで `pg_advantage` を記録）、archaludon の実データで直接測った結果:

| | round 1（起点 θ0） | round 6（起点 r5） |
|---|---:|---:|
| `value` 平均 | +0.043 | **-0.380** |
| V-trace 目標 `vs` 平均 | -0.025 | -0.403 |
| 残差（value - vs） | +0.068 | **+0.023** |
| 真の終端リターン平均 | -0.469 | -0.539 |
| advantage 平均 | -0.0674 | -0.0227 |
| advantage 標準偏差 | **0.333** | **0.229** |
| advantage が負の割合 | 59.9% | 58.9% |
| 非零報酬 / transition | 192 / 11,151 | 191 / 11,427 |

**critic はきちんと学習しており（+0.043 → -0.380 と真値 -0.54 を追随）、round 6 の誤差は
0.023 しかない。**ここは原因ではない。

一方 **advantage の標準偏差が 0.333 → 0.229 と 31% 縮小**している。ラウンドが進むほど
学習信号が弱くなる。

### 5.6 根本原因

単一のバグではなく積み重ね。

```
報酬は終端のみ (1.7%)            → advantage の98%が critic 由来
  + advantage 正規化なし          → adv_sd が 0.333→0.229、実効ステップが勝手に縮む
  + rho_bar=1.0 の片側クリップ     → 「上げたい」信号が19〜30%で頭打ち
  + 10.2回のデータ再利用           → off-policy 度が累積
  + エントロピーボーナス 0.01      → 尖った方策を毎ラウンド少しずつ均す
──────────────────────────────
= 毎ラウンド 0.3 nats 動くが、方向が選べていない
  → エントロピーだけ上がり、argmax の質は不変 → held-out 横ばい
```

**対照実験になっているのが t1 である。**ミラー相手（信号が強く単純）では収集リターンが
-0.062 → +0.438 と大きく上がった。**学習機構自体は動く。**多様で強い 96 体に対して
信号が足りない。

---

## 6. 実装した対策（コード変更は 1 件のみ）

### 6.1 advantage 正規化（新規）

| ファイル | 変更 |
|---|---|
| `src/mage_ptcg/meta_specialist/vtrace_bridge_v1.py` | `evaluate_trajectory_loss_v1` に `advantage_shift` / `advantage_scale`（既定 0.0 / 1.0 = 恒等）。`VTraceLossV1` に `advantage_sum` / `advantage_square_sum` を追加し、`accumulate_trajectory_losses_v1` で合算 |
| `src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py` | `ADVANTAGE_NORMALIZATION_MODES_V1 = ("none","center","standardize")`、`AdvantageNormalizationV1`、`run_train_from_trajectories_v1(advantage_normalization=...)`、recipe への記録 |
| `src/mage_ptcg/meta_specialist/cli.py` | `--advantage-normalization` |

設計上の判断を 2 つ記録しておく。

- **統計は「前 step の minibatch」から取る。**現 step の統計を使うには、minibatch 全体の
  advantage を保持するか forward を 2 回するかになり、OOM 対策の microbatch ループの
  目的を壊す。corpus はラウンド内で固定なので前 step の推定で足りる。run の 1 step 目
  だけ恒等（80 step 中 1 step）。
- **記録するモーメントは正規化前の生値。**正規化後を記録すると次 step の shift/scale が
  二重にかかり、update が縮み続ける。この契約はテストで固定した。

既定は `none` で、従来の更新をそのまま再現する。

### 6.2 引数だけの 3 件

`runs/pipeline-logs/run_overnight.sh` に環境変数を追加した。

| 変数 | 既定 | t3 で使う値 | 意図 |
|---|---|---|---|
| `ADV_NORM` | `none` | `standardize` | 対策 1 |
| `BC_COEF` | 空（CLI 既定 0.1） | **`0.4`** | 対策 1 に伴う必須調整（§6.3） |
| `RHO_BAR` | `1.0` | `2.0` | 対策 3 |
| `ENTROPY_COEF` | `0.01` | `0.001` | 対策 4 |
| `RL_STEPS` | `80` | `24` | 対策 2（再利用 10.2 → 3.07 回） |

### 6.3 `standardize` を素で入れると崩壊する（実測）

archaludon の r6 実データ・r5 checkpoint・30 step で測った。

| 設定 | dlogp | clip_hi | dead_rho | grad |
|---|---:|---:|---:|---:|
| base（none, ρ=1.0, ent=0.01, bc=0.1） | -0.360 | 0.228 | 0.001 | 0.086 |
| standardize, bc=0.1 | **-0.605** | 0.009 | **0.019** | 0.067 |
| **standardize, bc=0.4** | **-0.362** | 0.002 | 0.001 | **0.124** |
| standardize, bc=1.0 | -0.340 | 0.004 | 0.001 | 0.293 |
| center, bc=0.1 | -0.363 | 0.003 | 0.001 | 0.095 |

- `bc=0.1` のままだと `dead_rho` が base の 19 倍、loss が 7 分の 1（0.064 → 0.0086）に
  なる。これは収束ではなく
  [vtrace-degenerate-collapse-20260804.md](vtrace-degenerate-collapse-20260804.md) が
  記録した**信号消失の初期徴候**である。
- 原因は advantage の標準偏差が約 0.25 で、割ると advantage が約 4 倍になり、
  BC anchor が相対的に 4 分の 1 になること。**`bc` も 4 倍（0.1 → 0.4）にすると
  drift と `dead_rho` が base と同水準に戻り、勾配だけが 1.4 倍になる。**
- `rho_bar` 2.0 は単独で `clip_hi` を 0.228 → 0.002〜0.009 へ落とす（30 倍以上の改善）。

---

## 7. t3 の結果（2026-08-08 完走）

### 7.1 実行条件

```
RUN_TAG=t3  DEADLINE_HOURS=4   2026-08-07 22:21 -> 08-08 02:49  8 ラウンド完走
RL_GAMES=500  RL_STEPS=24  TRAJ_PER_STEP=64  MICRO_TRAJ=8   再利用 3.1 回/transition
adv_norm=standardize  bc=0.4  rho_bar=2.0  entropy=0.001
value_coefficient=0.5  lr=1e-3  optimizer=adamw  c_bar=1.0  max_grad_norm=1.0
学習相手: 加重96体  評価相手: held-out 6体（t1/t2 と同一、変えていない）
```

1 ラウンド約 33 分（t2 は 48 分）。学習が 26 分 → 約 8 分に縮んだ分。`WARN` / `FAIL` はゼロ。

### 7.2 学習の健全性（対策は効いた）

8 ラウンド全体の 4 レーン最悪値。

| round | dlogp | clip_hi | dead_rho |
|---:|---:|---:|---:|
| r1 | -0.293 | 0.005 | 0.003 |
| r4 | -0.334 | 0.005 | 0.004 |
| r8 | -0.354 | 0.005 | 0.002 |

t2 との比較:

| 指標 | t2（6 ラウンド） | t3（8 ラウンド） | 危険域 |
|---|---|---|---|
| `clip_hi` | 0.19〜0.30 | **0.003〜0.005** | — |
| `dlogp` | -0.23 → **-0.48**（単調悪化） | -0.29 → -0.35（微増で安定） | -1.0 |
| `dead_rho` | 0.001（上昇傾向） | 0.002〜0.004 | 0.05 |

`rho_bar=2.0` が `clip_hi` を約 60 倍下げ、`dlogp` の累積ドリフトが止まった。
`standardize + bc=0.4` は 8 ラウンドを通じて崩壊していない。

### 7.3 エントロピー膨張が止まった（t2 の失敗様式の解消）

平均 `-log π(a)`（各ラウンド先頭 120 局）。

| lane | t2 r1→r6（6 ラウンド） | t3 r1→r8（8 ラウンド） |
|---|---:|---:|
| archaludon | 0.2097 → 0.2617 **(+24%)** | 0.2097 → 0.2255 (+7.5%) |
| grimmsnarl | 0.2183 → 0.2569 **(+18%)** | 0.2190 → 0.2133 **(-2.6%)** |
| alakazam | 0.1916 → 0.2659 **(+39%)** | 0.1883 → 0.1971 (+4.7%) |
| rocket | 0.2245 → 0.3538 **(+58%)** | 0.2217 → 0.2527 (+14%) |

**より多いラウンド数で、より小さい膨張。**t2 の「分布が広がるだけ」は解消した。

### 7.4 収集スコアの傾きが反転

```
round   archaludon  grimmsnarl    alakazam      rocket        4レーン計       n
    1        0.258       0.388       0.351       0.293   0.322 [0.302,0.343]  1985
    2        0.238       0.407       0.349       0.284   0.319 [0.299,0.340]  1984
    3        0.251       0.486       0.394       0.281   0.353 [0.332,0.374]  1978
    4        0.280       0.467       0.381       0.266   0.348 [0.328,0.370]  1985
    5        0.291       0.443       0.422       0.292   0.362 [0.341,0.383]  1986
    6        0.212       0.445       0.431       0.290   0.344 [0.323,0.365]  1980
    7        0.224       0.418       0.444       0.259   0.335 [0.315,0.356]  1979
    8        0.240       0.438       0.441       0.283   0.350 [0.329,0.371]  1988
```

| run | 傾き | 標準誤差 |
|---|---:|---:|
| t2 | **-0.0120/round** | 0.0025（4.8σ 下降） |
| t3 | **+0.0031/round** | 0.0019（1.6σ 上昇） |
| 差 | **+0.0151** | 0.0031 → **4.8σ** |

t3 単体では 1.6σ で「上昇した」と断言できる水準ではない。**主張できるのは
「t2 とは挙動が明確に違う（4.8σ）」までである。**

### 7.5 held-out での確定値（384 局 x 4 レーン）

`transfer_check.sh 32 t3`。round 8 の方策を、プール済み θ0 と比較した。

| lane | θ0（プール） | t2 r6 | **t3 r8** | t3 - θ0 | 両側 p |
|---|---|---:|---:|---:|---:|
| archaludon | 0.381 [0.34,0.43] 480局 | 0.297 | **0.367** [0.32,0.42] 384局 | -0.014 | 0.671 |
| grimmsnarl | 0.302 [0.25,0.36] 288局 | 0.299 | **0.339** [0.29,0.39] 384局 | +0.036 | 0.317 |
| **alakazam** | **0.423 [0.37,0.48] 281局** | **0.497** | **0.665 [0.62,0.71] 382局** | **+0.241** | **<0.001** |
| rocket | 0.436 [0.38,0.49] 287局 | 0.432 | **0.385** [0.34,0.44] 384局 | -0.050 | 0.191 |

集計:

| 集計単位 | θ0 | t3 r8 | 差 | 両側 p |
|---|---|---|---:|---:|
| 4 レーン計 | 0.385（1,336局） | 0.439（1,534局） | +0.054 | 0.003 |
| **alakazam を除く 3 レーン** | **0.374（1,055局）** | **0.364（1,152局）** | **-0.011** | **0.603** |

**この局数で有意になる差は 0.036、80% の検出力で見える差は 0.051。**

### 7.6 読み方（重要）

- **効果は alakazam 1 レーンに集中している。**4 レーン合算の +0.054（p=0.003）は
  alakazam 単独が作った数字であり、**合算だけを引用して「RL が効いた」と要約しては
  ならない。**alakazam を除くと -0.011（p=0.603）で、有意な変化はない。
- **alakazam は t2 の時点で既に反応していた**（0.423 → 0.497、p=0.049）。t3 の対策が
  それを 0.665 まで伸ばした。θ0 → t2 → t3 が単調に上がっており、偶然の一致ではない。
- **本命の archaludon は θ0 と同水準に戻っただけ**である（t2 で 0.297 まで落ちた
  ところから 0.367 へ）。上回ってはいない。
- レーン単位の p を 4 つ並べているので多重比較になる。alakazam は
  Bonferroni 補正（x4）後も p < 0.001 で残る。他 3 レーンはいずれも補正前から非有意。

### 7.7 ラウンド評価（24 局）は過大に出る — 検算済み

`ROUND_EVAL_SEATS=2` のラウンド評価は、相手 6 体 x 2 座席 x 2 局 = **24 seed しか
使わない**。複数ラウンド分を足しても seed の種類は 24 のままで、局数ほどの独立性はない。

t2 で検算した結果（同じ方策群を 2 通りの局数で測った比較ではなく、r1-6 の平均的な方策と
r6 の方策という違いは残るが、系統的なずれの目安になる）:

| lane | r1-6 プール(144局) | 384局 実測 | ずれ |
|---|---:|---:|---:|
| archaludon | 0.354 | 0.297 | -0.057 |
| grimmsnarl | 0.285 | 0.299 | +0.015 |
| alakazam | 0.556 | 0.497 | -0.058 |
| rocket | 0.458 | 0.432 | -0.026 |

**採用判断にラウンド評価を使ってはならない。**崩壊検知専用である。
実際 t3 でもラウンド評価プール（alakazam 0.610）と 384 局（0.665）は一致しなかった。

### 7.8 なぜ alakazam だけ効いたのか（未解明）

事実として言えるのは次まで。**因果の説明は得られていない。**

- alakazam は 4 レーン中もっとも方策が尖っている（エントロピー 0.188、他 0.21〜0.22）。
- θ0 時点の held-out 勝率が最高（0.423）。
- 学習プールに対する収集スコアも 8 ラウンドで最も伸びた（0.351 → 0.441）。
- t2 の時点から唯一有意に反応していた。

「元から強く、信号が通っていたレーンで対策が増幅された」という筋書きと整合するが、
検証していない。archaludon / rocket が動かない理由も分かっていない。

---

## 8. 次にやること

優先順位つき。

| # | 案 | 規模 | 根拠 |
|---|---|---|---|
| 1 | **alakazam の Promotion Gate を通し、Champion 差し替えを判断** | 小 | +0.241（p<0.001、382局）は十分な差。ただし Gate 未通過で Champion を変えない規律がある（AGENTS.md） |
| 2 | **t3 を alakazam だけ延長する** | 小 | 8 ラウンドでまだ単調上昇中（収集 0.351 → 0.441）。頭打ちを確認していない |
| 3 | **archaludon / rocket の非反応の切り分け** | 中 | 本命が動かない。alakazam との差は何か。§7.8 の観察を仮説に落として検証する |
| 4 | 相手のカリキュラム化（弱い相手から昇順） | 中 | 勝率 0.21〜0.29 のレーンは正例が少なすぎる。正典 L7 の ascent curriculum |
| 5 | 評価の再現性修正（§4） | 中 | A/B を繰り返すなら先に直したほうが総コストは下がる |
| 6 | 報酬密度を上げる（中間報酬） | 大・要注意 | 終端のみ 1.7% が本質。競技の目的関数を歪めるリスクがあり慎重な設計が要る |

再実行の型（RUN_TAG は未使用のものにすること。**t1 / t2 / t3 は使用済み**）:

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical && \
RUN_TAG=t4 DEADLINE_HOURS=4 \
ADV_NORM=standardize BC_COEF=0.4 RHO_BAR=2.0 ENTROPY_COEF=0.001 RL_STEPS=24 \
setsid nohup runs/pipeline-logs/run_overnight.sh \
  > runs/pipeline-logs/pipeline8.log 2>&1 < /dev/null &
```

監視と判定:

```bash
runs/pipeline-logs/watch.sh                     # 進捗
RUN_TAG=t4 runs/pipeline-logs/trend.sh          # 収集局から学習トレンド（無料）
runs/pipeline-logs/transfer_check.sh 32 t4      # held-out 384局 x 4レーン + θ0比較（約30分）
```

**危険信号: `dead_rho` が 0.05 を超えたら止める。**崩壊へ向かっている印である
（t3 の設定では 0.002〜0.004 だった）。`dlogp` が -1.0 を下回る場合も同様。

判定ルール:

| trend.sh の傾き | transfer_check の結果 | 判断 |
|---|---|---|
| +0.005/round 以上 | — | 学習している。続行 |
| 平坦〜負 | θ0 と有意差なし | 止めてよい |
| 平坦〜負 | θ0 を有意に上回る | 収集指標が誤誘導。転移側で追う |

**何ラウンドで判定できるか**: 収集局は毎ラウンド約 2,000 局あり 1 標準誤差 0.011。
傾きの検出力はラウンド数で決まるが頭打ちが早い。

| ラウンド数 | 見分けられる傾き | 累計で見分けられる変化 |
|---:|---:|---:|
| 3 | ±0.015/round | 0.044 |
| **6** | **±0.005/round** | **0.030** |
| 13 | ±0.0016/round | 0.020 |

---

## 9. 現在の差分（すべて未コミット）

`git commit` / `git push` / Kaggle 提出は**ユーザーの明示指示がある場合のみ**行う（AGENTS.md）。

### 9.1 変更

```
 M deck.csv                                              ← ユーザーの既存変更。触っていない
 M opponents/pool_manifest.json                          ← medal_* 36体を登録
 M scripts/run_bc_distillation.py                        ← --read-workers
 M scripts/run_parallel_lanes.py                         ← --max-torch-threads
 M src/mage_ptcg/meta_specialist/actor_pool_v1.py        ← fault detail に engine error、opponent_instance_id 是正
 M src/mage_ptcg/meta_specialist/cli.py                  ← --opponent-kinds/--opponent-schedule/--advantage-normalization
 M src/mage_ptcg/meta_specialist/collect_trajectories_v1.py ← 相手巡回・座席カーソル・stride scheduling
 M src/mage_ptcg/meta_specialist/opponent_pool_v1.py     ← callable object を素の関数で包む
 M src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py ← AdvantageNormalizationV1
 M src/mage_ptcg/meta_specialist/vtrace_bridge_v1.py     ← advantage_shift/scale とモーメント
 M tests/meta_specialist/test_actor_pool_v1.py
 M tests/meta_specialist/test_collect_trajectories_cli.py
```

### 9.2 新規

```
?? cg/                                                   ← ユーザーの既存追加。触っていない
?? configs/meta_specialist/opponent_schedule_v1.json
?? docs/evidence/bc-thread-oversubscription-20260807.md
?? docs/evidence/rl-round-cost-and-actor-faults-20260807.md
?? docs/evidence/vtrace-rl-degrades-against-eval-pool-20260807.md
?? docs/evidence/vtrace-no-progress-20260807.md          ← 本書
?? opponents/medal_*/                                    ← 36 ディレクトリ
?? report/leaderboard-deck-analysis-0804.{json,md}
?? scripts/make_medal_opponents.py
?? scripts/make_opponent_schedule.py
?? tests/meta_specialist/test_advantage_normalization_v1.py
?? tests/meta_specialist/test_lane_thread_budget_v1.py
```

`runs/pipeline-logs/` 配下の監視スクリプトは `runs/` が gitignore 対象のため上の一覧に
出ない。**バックアップが必要ならこのディレクトリも別途保全すること。**

| スクリプト | 役割 |
|---|---|
| `run_overnight.sh` | 締切駆動の RL 反復。走行中の編集に耐えるよう自身を複製して `exec` する |
| `status.py` / `status.sh` / `watch.sh` | 3 段（bc / rl-collect / rl-train）の進捗表示 |
| `trend.py` / `trend.sh` | 収集局の `record.json` からラウンド別スコアと傾きを出す。ラウンド評価より 83 倍の標本 |
| `transfer_check.sh` | held-out で 384 局 x 4 レーンを測り、`compare_theta0.py` へ渡す |
| `compare_theta0.py` | `theta0-baseline-*.json` を全合算した基準との二標本検定 |
| `summary.py` / `summary.sh` | run 終了後のまとめ |

### 9.3 t3 の主要成果物

| パス | 内容 |
|---|---|
| `runs/pipeline-logs/pipeline7.log` | t3 の実行ログ（8 ラウンド、WARN/FAIL ゼロ） |
| `runs/pipeline-logs/current-t3-<lane>.txt` | 各レーンの最終 checkpoint へのポインタ |
| `runs/meta-specialist-training/t3-r{1..8}-<lane>/checkpoints/` | 全 32 checkpoint |
| `runs/meta-specialist-training/t3-final-<lane>-strength.json` | 最終評価 96 局 |
| `runs/pipeline-logs/transfer-t3-*-<lane>.json` | **確定値の 384 局測定**（§7.5 の出典） |
| `runs/meta-specialist-bc-distill/t1-<lane>/theta0-baseline-t3.json` | t3 開始時の θ0 再測定 |

**alakazam の採用候補 checkpoint**: `runs/pipeline-logs/current-t3-alakazam.txt` が指す
`checkpoint-cf5c974fc70b9...`（round 8）。384 局で 0.665。

### 9.4 テスト

- `tests/meta_specialist/test_advantage_normalization_v1.py`: **14 passed**
- 全体: **3,526 passed / 9 failed / 44 skipped**
- **9 件の失敗は `deck.csv` のユーザー変更が原因で、本作業とは無関係。**
  `git stash push -- deck.csv` すると当該 20 件はすべて pass する（確認済み、
  `stash pop` で復元済み）。失敗するのは
  `test_o2_c4_bridge.py` / `test_o2_training_loop.py` / `test_outcome_optimization.py` /
  `test_sparse_policy_optimization.py` の一部で、エラーは
  `FamilyAgentError: family anchor is absent from the bound deck`。

---

## 10. 引き継ぎ時の注意

### 10.1 守るべき制約

- `git commit` / `git push` / **Kaggle 提出はユーザーの明示指示がある場合のみ。**
  実験 runner・CI・エージェント指示から提出 CLI/API を呼んではならない。
- `local_eval_only` の資産（相手プール 102 体すべて）を提出 bundle に入れない。
- `deck.csv` と `cg/` はユーザーの既存変更。**上書き・整形・削除しない。**
- パイプライン実行中に `src/mage_ptcg/meta_specialist/` を編集しない。ワーカーが
  再 import して新旧のコードが混ざる。
- `run_overnight.sh` は自身を `runs/pipeline-logs/.run_overnight.running.$$.sh` へ複製して
  `exec` する。走行中の編集は次回起動から効く（実行中のスクリプトを書き換えて壊した
  実績があるための防御）。

### 10.2 この機体での落とし穴

- **torch のスレッド数は 2〜4 が最速。**28 を渡すと 37 倍遅い。プロセス並列（レーン）で
  並列度を稼ぎ、intra-op スレッドは増やさない。今回も `--torch-threads 8` を渡して
  ablation が実質停止した。
- **収集はレーン並列で伸びる。**単一レーンで worker を 28 にしても 1.0 局/s 止まり。
  4 レーン x 7 worker で合計 1.75〜2.17 局/s。
- **GPU（RTX PRO 5000 Blackwell 48GB, CUDA 利用可）は一切使っていない。**モデルが
  407K パラメータ / 1.63MB と小さく、収集はバッチ 1 推論で engine 律速、学習は
  float64 多用かつ可変長バッチ。移行の価値は未検証で、やるなら先に計測から。
- `pkill -f "..."` は**自分自身のシェルのコマンド行にも一致しうる。**今回それで自滅した。
  プロセス停止は別のツール呼び出しに分けること。

### 10.3 ディスク

- WSL 内の空きは 244GB に見えるが、**効く制約は Windows C: の 94GB。**WSL2 の ext4 は
  C: 上の疎な `.vhdx` で、書けば増えるが消しても縮まない。
- 増加ペースは 1 ラウンド約 1.7GB（`record.json` が 1 局あたり約 650KB x 2,000 局）。
- 回収可能な中間データ:
  - `runs/meta-specialist-actor-pool/t1-*`, `t2-*`（**38GB**、実測）— 各ラウンドの学習で
    消費済み。`--collection-run-dir` は当該ラウンドのディレクトリ 1 つだけを指すので
    二度と読まれない。
  - `runs/meta-specialist-teacher-records/t1-*/records/`（**23GB**、実測）— 生の中間データ。
    BC が読むのは `dataset-*.jsonl` のほう。
  - **checkpoint 群は合計 968MB しかない。これが成果物本体で、消してはならない。**
- WSL 内で削除しても C: の空きは戻らない。`wsl --shutdown` してから `Optimize-VHD` が要る。

### 10.4 検証用に残っている作業ディレクトリ

`runs/meta-specialist-training/` に `ab2-*` / `ab3-*` / `probe-*`、
`runs/meta-specialist-actor-pool/` に `warmup-*` / `sched-verify*` / `medal-verify*` /
`rot-*` が残っている。t3 とは干渉しない。
