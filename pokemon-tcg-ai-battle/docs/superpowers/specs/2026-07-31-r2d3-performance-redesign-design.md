# R2D3 性能再設計

## P0: 深度

R2。学習・評価・採用判断を同時に変更するが、提出、提出エージェント、非公開情報境界には触れない。

## P1: 問題と成功条件

v15/v16 は合法性を満たした一方、開発評価で 76/384 (19.8%) に留まった。原因は、seat と opponent の交絡、偏った Replay/PSRO mixture、終局報酬のみの Q 学習、直接模倣の欠如、Replay に対する過大な更新回数である。

成功条件は、新しい artifact 系列が次をすべて記録・検証できることとする。

- 各評価 opponent を両 seat で同数だけ対戦し、seat 別・opponent 別・集約勝率を出力する。
- architecture 選抜と開発評価の seed 群を分離する。
- actor-visible な情報だけで作った報酬と教師信号を使う。
- 各 Replay source と PSRO opponent の最低比率を満たし、実際の sample 構成を manifest に残す。
- 学習段階ごとの nominal Replay draw/window を上限以下にする。
- Kaggle 提出、commit、push、holdout の自動消費を行わない。

反証条件は、いずれかの評価セルが欠けること、教師や報酬へ相手の非公開情報が入ること、PSRO のいずれかの member が指定下限を下回ること、Replay 再利用上限を超えること、既存の safety gate を緩めることとする。

今回扱わないものは、提出物の変更、Kaggle API、ルールエンジンの変更、外部データ取得、新アルゴリズムへの全面移行である。

## P2: 前提台帳

| 前提 | 状態 | 根拠・確認方法 |
|---|---|---|
| 開発評価は seat と opponent が交絡していた | 確実 | `development_validation_results.csv`: waterbox は seat 0 のみ、slowking は seat 1 のみ |
| Replay は source と family が偏る | 確実 | v15 `replay.json` を集計し、RULE_V0 family が 51.0% |
| full training は nominal 568 draw/window | 確実 | 37,500 updates × batch 512 / 33,810 windows |
| potential-based shaping が学習を改善する | 未検証 | 新系列で terminal-only と同一 budget/seed で比較する |
| 勝利軌跡だけの教師が品質を上げる | 推定 | 品質確認済みの local submitted agent に限定し、BC ablation で検証する |

## P3: 選択肢

1. 評価だけを修正する。診断精度は上がるが、低い方策品質の原因を残すため不採用。
2. R2D3 のまま評価、Replay、PSRO、報酬、教師、更新予算を再設計する。既存資産を安全に使い、各効果を manifest で検証できるため採用。
3. 新しい自己対戦アルゴリズムへ全面移行する。比較不能な大規模変更となり原因切り分けを失うため不採用。

## 設計

### 1. 独立かつ seat-balanced な評価

`validation_schedule(split, games, seed_namespace)` を導入する。split 内の各 asset と seat 0/1 の直積を均等反復し、`games` が `len(assets) * 2` の倍数でなければ fail-closed にする。各 job は asset index、candidate seat、評価用 seed を明示的に受け取る。

architecture screen と multiseed は共通の `selection` namespace を用い、full training、PSRO best-response、promotion 前の development validation は相互に異なる namespace を用いる。これにより候補間比較には共通乱数を使い、採択時の評価使い回しは防ぐ。

`evaluation_summary.json` には、全体、asset、seat、asset×seat の games/wins/win rate/Wilson 95% CI と fault 件数を記録する。promotion の既存しきい値は全セル均等の aggregate win rate にだけ適用し、holdout の消費規則は変更しない。

### 2. 教師品質と直接模倣

Replay collection の前に `teacher_calibration` stage を追加する。training split の submitted asset を Rule v0 相手に両 seat 同数でローカル対戦し、fault なしで balanced win rate が 0.5 以上の asset を teacher として採択する。Kaggle Replay や validation/holdout asset は教師に使わない。

採択 teacher の**勝利局**だけを demonstration にする。敗北局と、PPO/BC/family/Rule/PSRO の軌跡は TD 学習用として残すが、BC 対象にはしない。`R2D3Transition` に教師重みを追加せず、既存の demonstration flag を品質済み勝利軌跡にだけ付ける。

`LearnerConfig.bc_weight` を実装し、demonstration action に対する masked cross entropy を `bc_weight` で TD loss へ加える。既存の margin と demonstration priority bonus は維持する。BC 指標を training curve と manifest へ保存する。

### 3. actor-visible potential-based reward

軌跡 collector は actor view の `self.prize_count` と `opponent.prize_count` から、公開情報だけを用いて potential を保存する。

```
phi(s) = 0.10 * (opponent_prize_count - own_prize_count) / 6
F(s, s_next) = gamma * phi(s_next) - phi(s)
```

非終端 reward は `F`、終端 reward は `game_outcome - phi(s)` とする。terminal potential を 0 とするため、完全軌跡の discounted return は初期 potential 0 の終局勝敗と一致する。C51 support は shaped return を表せる範囲へ明示的に拡張する。旧 Replay は potential を持たないため新系列では再利用しない。

### 4. source-balanced Replay と更新予算

Replay は `submitted_demonstration`、`ppo_online`、`ppo_vs_environment_top_deck`、`bc_recurrent`、`family_alakazam`、`gate3_clean_online` の source strata を保持する。各 learner batch は source 別に均等配分し、demonstration 枠だけは採択教師から追加する。優先度、importance weight、checkpoint/resume は stratum ごとに完全復元する。

学習 budget は reference update 数ではなく `max_nominal_draws_per_window` で決める。screen、multiseed、full、PSRO best-response ごとに上限を profile へ持ち、実効 update 数を `floor(replay_windows * cap / batch_size)` 以下にする。実際の draws/window と source 配分を training manifest に記録する。

### 5. PSRO の多様性

meta strategy をそのまま使用せず、各 member に floor probability を与え、残りを solver の比率で再正規化する。production の 4 member では各 0.15 を下限とし、online schedule は確率抽選でなく整数 quota から決定的に組み立てる。2,000 局では各 member が少なくとも 300 局となる。

online replay は各 member の games と sequence 数を manifest に記録し、いずれかの floor を満たさなければ best-response を開始しない。best-response は offline/online だけでなく online member strata も均等に sample する。

## P5: 反証

- seat-balanced 化で勝率が変わる可能性はあるが、弱い方策が強くなったことは意味しない。改善主張には新しい development と未消費 holdout の双方が必要である。
- potential shaping は理論上方策不変でも、近似・有限 support 下では影響する。terminal-only recipe を同一新 Replay・同一評価で併走し、shaping の有無を記録する。
- 勝利局だけの BC は過適合しうる。教師数、教師勝率、demo transition 数を gate にし、不足時は fail-closed とする。
- source-balanced sampling は希少 source を過剰反復しうる。source ごとの draw/window 上限と優先度状態を記録し、再利用率を検証する。

最強の反論は「低勝率は固定 deck の相性であり、学習変更では解決しない」である。この判断を変えるため、deck を固定したまま同一 opponent の両 seat 評価を取り、改善後に deck holdout と final holdout を独立に確認する。

## P7: 決定と停止条件

決定は選択肢 2 とする。実装は評価、Replay/learner、PSRO、runner profile/manifest、回帰テストの順で行う。

停止条件は、actor-visible reward を計算できる public trace がない、calibration 後に teacher が 1 件も残らない、または既存の replay/checkpoint contract を安全に移行できない場合とする。その場合は該当 stage を fail-closed とし、代替データを無断で使わない。
