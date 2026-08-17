# Meta Specialist 現状総合分析・ChatGPT検討用資料

- 作成日: 2026-08-08 JST
- 対象 worktree: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`
- 対象 branch: `feature/meta-specialist-canonical`
- 目的: これまでの実装、実験、失敗、修正、検証結果を一つの資料へ統合し、別のChatGPTへ渡して追加検討できる状態にする
- 重要な注意: 本資料は「実行経路が動くこと」と「性能が向上したこと」を分離する。少数ゲームのsmoke結果を、統計的な性能改善とは扱わない

---

## 0. 先に結論

現在の状態は、次のように表現するのが最も正確である。

> V-traceの実装・データ記録・seed・checkpoint・評価経路に存在した複数の構造的な不具合はかなり修正された。しかし、性能向上を妨げる本質的な問題はまだ残っており、現時点のv2smokeは「学習経路の健全性を確認した実験」であって、「汎用的に強くなったことを証明した実験」ではない。

特に重要な残存問題は以下である。

1. 学習に使った相手分布と評価相手分布が一致していない。
2. 6〜8ゲーム程度の固定データを4回再利用し、更新後にV-traceの累積`c`が極端に小さくなっている。
3. Pokémon情報へzone・energy・tool等を追加したが、線形変換後の平均poolingにより、カードとzone／attachmentの対応関係が失われる。
4. criticの相手埋め込みがゲームseed単位で、しかもBC段階では未学習のランダム値である。相手別valueが同じrule-agent内で大きく散っている。
5. BCはfull corpusではなく、`snapshot-0000.json`を使った200-step smokeであり、v2表現の十分な教師学習・validationがまだない。
6. Alakazamの収集で8局中2局が`AGENT_ERROR`になっており、少量データでは無視できない選択バイアスになり得る。
7. 評価はv2smokeでは各レーン6局だけで、結果のばらつきが大きい。RLの勝率改善を主張できない。

一方、以下は今回の修正により実際に改善した。

- Gumbel-maxの摂動logitをbehavior probabilityとして記録していた問題は修正済み。
- ゲームごとのsampling seedは独立・再現可能になった。
- 初回V-trace stepのbehavior/target log-probability差は全レーンで約`1e-8`であり、収集直後のon-policy整合性は確認できた。
- `mean_importance_ratio`、`mean_continuation_c`、相手別critic値を記録できるようになった。
- 旧v1表現をそのままv2checkpointへ混ぜないfail-closed境界が追加された。

したがって、現状は「壊れた計器を直して、学習がどこで壊れるかを見えるようにした段階」である。計器が正常になったことで、更新後の信用割当崩壊、データ分布不一致、表現の関係性欠落という次の問題が明確になった。

---

## 1. この資料で参照した情報と信頼順位

### 1.1 最優先: 実行成果物

以下は今回のv2smoke実行から直接得たJSON／recordである。数値判断ではこれを最優先する。

- BC run summary:
  - `runs/meta-specialist-bc-distill/v2smoke-alakazam/run_summary.json`
  - `runs/meta-specialist-bc-distill/v2smoke-archaludon/run_summary.json`
  - `runs/meta-specialist-bc-distill/v2smoke-grimmsnarl/run_summary.json`
  - `runs/meta-specialist-bc-distill/v2smoke-rocket/run_summary.json`
- 軌跡収集 run summary:
  - `runs/meta-specialist-actor-pool/v2smoke-rl-alakazam/run_summary.json`
  - `runs/meta-specialist-actor-pool/v2smoke-rl-archaludon/run_summary.json`
  - `runs/meta-specialist-actor-pool/v2smoke-rl-grimmsnarl/run_summary.json`
  - `runs/meta-specialist-actor-pool/v2smoke-rl-rocket/run_summary.json`
- V-trace training run summary:
  - `runs/meta-specialist-training/v2smoke-rl-alakazam/run_summary.json`
  - `runs/meta-specialist-training/v2smoke-rl-archaludon/run_summary.json`
  - `runs/meta-specialist-training/v2smoke-rl-grimmsnarl/run_summary.json`
  - `runs/meta-specialist-training/v2smoke-rl-rocket/run_summary.json`
- holdout評価:
  - `runs/meta-specialist-strength/v2smoke-<lane>.json`
  - `runs/meta-specialist-strength/v2smoke-bc-<lane>.json`
- ゲームごとの軌跡:
  - `runs/meta-specialist-actor-pool/v2smoke-rl-<lane>/games/*/record.json`

### 1.2 既存の証拠レポート

- `docs/evidence/vtrace-no-progress-20260807.md`
- `docs/evidence/vtrace-degenerate-collapse-20260804.md`
- `docs/evidence/vtrace-rl-degrades-against-eval-pool-20260807.md`
- `docs/evidence/rl-round-cost-and-actor-faults-20260807.md`
- `docs/evidence/bc-thread-oversubscription-20260807.md`
- `docs/evidence/vtrace-learning-health-20260808.md`

### 1.3 Claude側の報告

Claude側の報告は、現在のworktreeには存在しないが、main checkoutの以下にある。

- `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/plan/META_SPECIALIST_CANONICAL_IMPLEMENTATION_REPORT.md`

この報告には、初期に「全課題解消」「全テストgreen」と記載された部分と、その後のClaude自身による独立監査・訂正の両方が含まれる。本資料では初期主張をそのまま事実扱いせず、後半の訂正および実行成果物と照合している。

### 1.4 この資料の確度区分

- **確定事実**: JSON、record、実行ログ、再現スクリプト、コードから直接確認できるもの
- **強い根拠のある原因**: 複数実験または最小反例で再現したもの
- **有力仮説**: 機構として説明できるが、独立ablation未実施のもの
- **未解決**: 原因候補はあるが、特定の最小実験がまだないもの
- **性能主張不可**: 標本数や評価再現性が不足し、差を結論できないもの

---

## 2. システムの構成と、何を学習しているか

### 2.1 1レーンの基本フロー

```text
teacher snapshot / BC dataset
        |
        v
BC distillation -> neural checkpoint θ0
        |
        v
actor rollout (sample decoding)
        |
        v
recorded trajectories: model_input, chosen action, behavior log-probability,
                        opponent identity, reward, terminal flag
        |
        v
V-trace learner: current target policy + current critic + BC anchor + entropy
        |
        v
RL checkpoint θ1
        |
        v
holdout evaluation (greedy / fixed schedule)
```

### 2.2 行動の確率

Neural policyはbase logitsを返す。sample decodingではGumbel noiseを加え、摂動後logitのargmaxを取る。

正しい関係は次である。

```text
decode logits = base logits + Gumbel noise
chosen action = argmax(decode logits)
behavior probability = softmax(base logits)[chosen action]
```

以前は摂動後logitsをbehavior distributionとして保存していたため、実際にサンプルした分布とは異なるlog-probabilityを学習側へ渡していた。これは収集直後からimportance ratioを壊す直接原因だった。今回、decode logitsとbase logitsを分離して記録する修正を入れた。

### 2.3 V-traceの信用割当

概念的には、各stepのimportance ratioを

```text
ρ_t = π_target(a_t | x_t) / π_behavior(a_t | x_t)
c_t = min(c_bar, ρ_t)
```

とし、将来のTD誤差を`c_t`の積で伝播する。

したがって、1stepごとの`dead_rho`が小さくても、長いtrajectoryで

```text
c_0 * c_1 * ... * c_{T-1}
```

が小さくなれば、終端報酬は序盤の行動へほぼ届かない。今回、以前の`dead_rho`だけの診断では見えなかったこの問題を`mean_continuation_c`で明示化した。

---

## 3. 時系列: これまで何が起きたか

### 3.1 2026-08-04: 初期のV-trace崩壊

`docs/evidence/vtrace-degenerate-collapse-20260804.md`に記録された初期実験では、value headが存在せず、stored valueは全件0.0だった。

実測:

- 4,270局相当のデータ
- 6,158 transitionsを調査
- reward非zeroは300件程度、勝率も低い
- `mean log-prob shift`はstep 3の`-3.52`からstep 12の`-7.99`まで悪化
- target log-probは`-9.60`、behavior log-probは`-1.61`
- lossとgradient normだけを見ると0へ近づくが、実際には学習信号が消えていた

当初は「value head不在」が主因と判断された。その後value headとentropyを追加しても長時間では悪化したため、さらに調査が進んだ。

### 3.2 2026-08-04後半: BC anchor欠落の発見

固定offline corpusに対してpolicy gradientだけを適用すると、advantageが負の行動についてlog-probを下げることでlossを下げられる。behaviorへ近づく制約がないため、方策は収集行動から逃げ、importance ratioが0へ向かう。

実収集データを使ったBC係数sweepでは、`bc_coefficient=0.0`は発散し、`0.1`は安定、`0.5`以上はほぼ純粋な模倣へ飽和した。

この時点で、次の因果が確立した。

```text
固定offline corpus
 + 終端報酬中心の薄いsignal
 + BC anchorなし
 -> 収集行動のlog-probを下げる方向へ逃げる
 -> rhoが0へ向かう
 -> V-trace gradientが消える
```

`bc_coefficient=0.1`を既定に追加したことは正しい修正だったが、これは発散防止であり、汎用性能向上を保証するものではない。

### 3.3 t1: mirror相手だけへの過学習

2026-08-07のt1では、学習相手が`cabt_rule_agent_v0`のmirror戦だけだった。収集相手に対するreturnは上昇したが、held-out実デッキ相手の勝率は全レーンで下がった。

旧測定では次の結果だった。

| lane | θ0 | RL 14 rounds | 差 |
|---|---:|---:|---:|
| archaludon | 0.448 | 0.281 | -0.167 |
| alakazam | 0.398 | 0.295 | -0.103 |
| grimmsnarl | 0.271 | 0.208 | -0.062 |
| rocket | 0.400 | 0.339 | -0.061 |

主原因は、学習目的が「一般の対戦相手への強化」ではなく「同じrule-agent／同じデッキとのmirrorへの適応」になっていたことだった。

### 3.4 相手プールの修正

その後、相手プールの実体化、未登録相手のfail-closed化、mirror fallbackの除去、callable objectのwrapper化、medal deckの追加、加重schedule、座席cursorの修正、opponent provenanceの修正が行われた。

重要な発見:

- 旧プールの一部は実際には完走しない相手だった。
- 相手デッキが変わっても方策が常にrule v0のまま、という期間があった。
- 一様巡回では観測メタ比率を再現できなかった。
- schedule長が実行長より長いと、座席が固定される罠があった。
- `opponent_instance_id`をrule-agent seedへハードコードしており、実相手の分析が壊れていた。

### 3.5 t2: 相手分布を広げても性能が横ばい

t2は加重96体を学習相手にし、`RL_STEPS=80`、`TRAJ_PER_STEP=64`、`rho_bar=1.0`、`c_bar=1.0`、entropy 0.01、BC 0.1、lr 1e-3で行われた。

観測:

- held-outの合算はほぼ横ばい。
- 学習中のsample scoreはラウンドを重ねるほど低下。
- ただしgreedy holdoutはほぼ横ばい。
- `-log π(a)`が上昇し、sample時の行動は散らばるがargmaxはあまり変わらなかった。
- 11,151 transitions中、nonzero rewardは192、約1.7%だった。
- transitionあたりの再利用回数は約10.2回。
- `rho_bar=1.0`で上方向ratioが片側クリップされ、clip_hiは19〜30%だった。
- advantage標準偏差がラウンド進行で縮小し、実効gradientも弱くなった。

t2の根本原因は単一ではなく、以下の積み重ねと整理された。

```text
終端報酬のみでsignalが薄い
 + advantageの正規化なし
 + rho_bar=1.0の上側clip
 + 固定データの過剰再利用
 + sample時entropyの膨張
 -> 学習対象から離れるが、良い方向へ動いたとは限らない
 -> sample scoreだけが下がり、greedyのargmax品質は変わらない
```

### 3.6 t3: 学習安定化策は効いたが、転移はAlakazamへ集中

t3では以下を導入した。

- advantage normalization: `standardize`
- BC coefficient: `0.4`
- rho bar: `2.0`
- entropy coefficient: `0.001`
- RL steps: `24`
- 1 transitionあたりの再利用を約3.1回へ削減

学習健全性は改善した。

- clip_hi: 0.19〜0.30から0.003〜0.005へ低下
- dlogpの累積悪化が止まった
- dead_rhoは0.002〜0.004
- sample scoreの傾きはt2の`-0.0120/round`からt3の`+0.0031/round`へ反転した

しかしholdout 384局×4レーンでは、効果はAlakazamに集中した。

| lane | θ0 | t3 r8 | 差 |
|---|---:|---:|---:|
| archaludon | 0.381 | 0.367 | -0.014 |
| grimmsnarl | 0.302 | 0.339 | +0.036 |
| alakazam | 0.423 | 0.665 | +0.241 |
| rocket | 0.436 | 0.385 | -0.050 |

Alakazamを除いた3レーンでは`0.374 -> 0.364`で、改善は確認できなかった。4レーン合算の改善だけを見ると誤解を生む。

なお、既存レポート自身が次を明記している。

- Alakazamだけ効いた理由は未解明。
- 評価は同じcheckpoint・同じseedでも0.302〜0.448へ散ることがある。
- 96局単独を決定的再測定として扱えない。

### 3.7 今回のv2smoke: 構造修正の確認実験

今回のv2smokeは、t3の大規模実験とは別物である。

- BC: 200 steps
- BC source: 各laneの`snapshot-0000.json`
- collection: 各lane 8 games要求
- training: 4 V-trace steps
- evaluation: 各lane 3 opponents × 2 seats = 6 games
- collection opponent: `cabt_rule_agent_v0`のみ
- evaluation opponents: `kiyotah_lucario`, `skarin_dragapult`, `sue124_alakazam`

したがって、v2smokeは「新しいbehavior probability、seed、表現、health instrumentationが接続されているか」の確認であり、t3の代替となる性能実験ではない。

---

## 4. 今回v2smokeの実行内容と数値

### 4.1 1. v2 BC initialization

実行コマンド:

```bash
PYTHONPATH=. python scripts/run_parallel_lanes.py --stage bc --lanes all \
  --run-prefix v2smoke \
  --snapshot-template 'runs/meta-specialist-teacher-records/t1-{lane}/snapshot-0000.json' \
  --max-steps 200 --examples-per-step 64 --microbatch-examples 16 \
  --checkpoint-interval-steps 100 --total-threads 16 --max-torch-threads 2 \
  --eval-every-steps 0 --snapshot-seconds 10
```

全4レーンが完了した。

| lane | examples | first loss | last loss | steps |
|---|---:|---:|---:|---:|
| alakazam | 1,204 | 2.3618 | 0.5562 | 200 |
| archaludon | 2,301 | 1.8807 | 0.7582 | 200 |
| grimmsnarl | 2,044 | 1.9310 | 0.4398 | 200 |
| rocket | 1,567 | 2.2182 | 0.4629 | 200 |

lossは全laneで低下した。ただし、これはtrain lossであり、v2表現のheld-out BC fidelityや教師方策への一般化を示すvalidation lossではない。

最初にfull shard-indexを使う実行も試みたが、8〜21GB級shardの再検証に時間がかかり、0進捗のまま停止した。そのため、この実験のBCはbounded smokeである。

### 4.2 2. independent sampled collection

実行コマンド:

```bash
PYTHONPATH=. python scripts/run_parallel_lanes.py --stage rl-collect --lanes all \
  --run-prefix v2smoke --rl-games 8 \
  --total-threads 16 --max-torch-threads 2 --snapshot-seconds 10
```

結果:

| lane | attempted | completed | faulted | transitions | distinct sampling seeds |
|---|---:|---:|---:|---:|---:|
| alakazam | 8 | 6 | 2 | 442 | 6 |
| archaludon | 8 | 8 | 0 | 468 | 8 |
| grimmsnarl | 8 | 8 | 0 | 451 | 8 |
| rocket | 8 | 8 | 0 | 471 | 8 |

recordのsampling seedは大きな63-bit値で、全completed gameで重複しなかった。run summaryの`sampling_seed=0`はbase seedを表すため、summaryだけを読んでseed固定と判断してはいけない。

Alakazamの2 faultは次の通り。

- env_seed 5600003、seat 1
- env_seed 5600006、seat 0
- fault status: `AGENT_ERROR`
- stored `error`: `None`

過去の負荷実験では、Alakazamの収集faultが並列負荷下で約10〜32%へ増えることが確認されており、一般的な全lane共通faultではなく、収集経路とAlakazamの長い判断時間裾に偏る問題である。今回の2/8はその既知傾向と整合する。

### 4.3 3. V-trace training

実行コマンド:

```bash
PYTHONPATH=. python scripts/run_parallel_lanes.py --stage rl-train --lanes all \
  --run-prefix v2smoke --max-steps 4 --checkpoint-interval-steps 4 \
  --total-threads 16 --max-torch-threads 2 --snapshot-seconds 10
```

初回stepは、現在のモデルがbehavior checkpointそのものである。この不変条件の結果:

| lane | transitions | mean log-prob shift | mean ratio | mean c | critic strata |
|---|---:|---:|---:|---:|---:|
| alakazam | 442 | +3.27e-08 | 1.00000003 | 0.99999993 | 6 |
| archaludon | 468 | -4.24e-11 | 1.00000000 | 0.99999992 | 8 |
| grimmsnarl | 451 | +4.62e-10 | 1.00000000 | 0.99999992 | 8 |
| rocket | 471 | -1.08e-08 | 0.99999999 | 0.99999990 | 8 |

この数値は、Gumbel摂動logitをbehavior probabilityへ使う以前の不具合が解消したことを強く示す。

4更新後の最後のlearning health:

| lane | mean log-prob shift | mean ratio | mean c | clipped fraction | vanishing fraction |
|---|---:|---:|---:|---:|---:|
| alakazam | -0.1072 | 1.0469 | 0.9084 | 0.3710 | 0.0023 |
| archaludon | -0.1154 | 1.0018 | 0.8706 | 0.3568 | 0.0000 |
| grimmsnarl | -0.0234 | 1.2017 | 0.9033 | 0.3481 | 0.0000 |
| rocket | -0.2013 | 1.0705 | 0.8287 | 0.2951 | 0.0085 |

学習lossは以下のように下がった。

| lane | loss step 0 | loss step 3 | gradient norms |
|---|---:|---:|---|
| alakazam | 0.8182 | 0.1483 | 4.65, 1.84, 0.70, 0.73 |
| archaludon | 0.5909 | 0.2458 | 3.04, 0.90, 0.48, 0.57 |
| grimmsnarl | 1.7673 | 0.1563 | 6.20, 1.24, 0.49, 0.43 |
| rocket | 1.2902 | 0.3224 | 2.26, 0.78, 1.15, 1.38 |

初回gradient normは全laneでmax gradient norm=1.0を超えており、clipされている。小規模固定datasetにlr=1e-3で強い更新を入れている。

### 4.4 4. fixed holdout smoke evaluation

評価相手は次の3体、各seat 1局でlaneあたり6局だった。

- `kiyotah_lucario`
- `skarin_dragapult`
- `sue124_alakazam`

評価faultは全laneで0だった。

| lane | v2 BC | v2 RL | delta |
|---|---:|---:|---:|
| alakazam | 0.167 | 0.333 | +0.167 |
| archaludon | 0.167 | 0.000 | -0.167 |
| grimmsnarl | 0.333 | 0.167 | -0.167 |
| rocket | 0.167 | 0.667 | +0.500 |

合計ではBC 5勝/24、RL 7勝/24である。Wilson 95%区間は概算で、BC 9.2〜40.5%、RL 14.9〜49.2%。この差は性能向上の証拠ではない。

相手別には、BCからRLへの勝ち数は次のように変わった。

| opponent | BC wins / 8 | RL wins / 8 |
|---|---:|---:|
| Lucario | 5 | 3 |
| Dragapult | 0 | 1 |
| Sue Alakazam | 0 | 3 |

RLが全相手へ強くなったのではなく、相手ごとの得手不得手が変わっただけである。

---

## 5. 今回の結果から確定できること

### 5.1 behavior probabilityの修正は成功

初回stepでtargetとbehaviorのlog-probabilityが一致した。これは最重要の配線不変条件である。以前のGumbel logit問題が残っていれば、初回から明確なshiftが出るはずだった。

### 5.2 sampling seedの相関は解消

各gameで`derive_game_sampling_seed_v1`がbase seed、env seed、lane、opponent、seatをhashしてseedを作る。completed gameのseedが重複しなかったため、「全ゲームでseed=0の同じ探索系列」という旧問題は解消している。

### 5.3 learning healthの可視化は有効

4更新後、単なる`dead_rho`はほぼ0でも、mean `c`は0.83〜0.91まで下がる。これは旧診断では見えなかった。V-traceの本当の問題は、1stepのrho死亡だけでなく、長いtrajectoryにわたるcの累積である。

### 5.4 評価経路は少なくともsmoke範囲で安定

v2smokeのholdout 24局はfault 0だった。収集側のAlakazam faultとは分離されており、「評価で全体が壊れている」という問題ではない。

---

## 6. 現在も性能向上を阻害している根本原因

ここでは優先度順に記す。

### P0-A: 学習分布と評価分布の不一致

v2smokeでは収集相手が全laneで`cabt_rule_agent_v0`、評価相手が3体の実agentだった。t1ではmirrorのみ、t2/t3ではweighted 96体へ広げたが、v2smokeは構造修正の確認用として再びmirrorのみになっている。

これは「学習が悪い」のではなく、最適化対象が評価目的と異なる問題である。rule-agentへの勝率を上げても、実agentへの応答能力は保証されない。t1でこの現象はすでに実対局として確認されている。

必要なのは、評価で使う相手分布をそのまま学習へ含めること、または少なくとも評価分布を含むcalibrated training scheduleである。相手のカバー率だけでは足りず、頻度・強度・座席・相手方策の実体を一致させる必要がある。

### P0-B: 固定batch再利用による累積trace崩壊

v2smokeの最後のcheckpointを、収集済みtrajectoryへ再スコアして`c=min(1,ratio)`のtrajectory積を計算した結果:

| lane | full trajectory `∏c` の中央値 | 最初の20step `∏c`中央値 |
|---|---:|---:|
| alakazam | 4.8e-4 | 0.491 |
| archaludon | 1.1e-6 | 0.023 |
| grimmsnarl | 6.6e-4 | 0.053 |
| rocket | 2.1e-8 | 0.037 |

mean cが0.83でも、60step前後の積はほぼ0になる。終端rewardしかない環境では、序盤の意思決定に学習信号が届かない。

この問題は次のどちらかが必要である。

- policy updateごとにfresh rolloutを取り、behaviorを頻繁に更新する
- stale batchの更新回数を厳しく制限し、`∏c`またはeffective trace horizonをgateする

mean cだけを見るのは不十分で、trajectory lengthを加味したtrace product、effective horizon、またはposition-binned cを記録すべきである。

### P0-C: 表現の見た目の拡張と、関係性の保持は別

v2でPokémonへzone、energy type counts、energy cards、tools、pre-evolutionを追加した。しかしstate encoderは次の構造である。

```text
各Pokemon: linear(card, zone, scalar, attachment)
全Pokemon: mean pooling
state backbone: nonlinear mix
```

linear変換とmean poolingは交換可能であるため、

```text
(card A, active) + (card B, bench)
```

と

```text
(card A, bench) + (card B, active)
```

は同じ総和になる。実際に最小コードで確認し、出力の最大差は`1.2e-7`だった。同じことが「どのPokémonにenergy/toolが付いているか」にも起きる。

これは単なるfeature不足ではなく、状態表現の構造的な情報損失である。active/bench、host/attachment、進化ラインを使うゲームでは、関係性を失うと、trainer選択やretreat判断の文脈が区別できない。

candidate endpoint側はnested Pokémonを個別にencodingしているため、特定候補に関係する情報は一部保持される。しかし、全体stateのpolicy backboneとcriticには上記の欠落が残る。

### P0-D: critic相手embeddingの粒度と初期化が不適切

現在のcriticは`opponent_instance_id`をhashして256 bucketのembeddingを加える。provenance上は相手seedごとのIDを保存するのは正しいが、criticの条件としては細かすぎる。

v2smokeのmirror収集では、6〜8ゲームがすべて別IDになった。しかもこのembeddingはcritic-onlyで、BCでは学習されない。初回stepの相手別valueは以下の範囲にあった。

| lane | 初回相手別Vの最小 | 最大 | 標準偏差概算 |
|---|---:|---:|---:|
| alakazam | -3.221 | 1.497 | 1.66 |
| archaludon | -2.083 | 1.903 | 1.34 |
| grimmsnarl | -2.023 | 1.499 | 1.04 |
| rocket | -2.031 | 2.116 | 1.29 |

rewardは概ね-1〜+1であるため、同じrule-agentのseed違いへ初期値が大きく散っている。これはtraining advantageを乱す強いノイズ源である。

provenance用IDとcritic用条件を分離すべきである。

- provenance: `opponent_id + game seed`
- critic conditioning: stable opponent policy/deck class、または相手pool bucket
- 十分な観測数がないカテゴリはembeddingを使わず共有baselineへ戻す
- critic-only embeddingをzero-initするか、BC/価値事前学習で初期化する

### P1-A: v2 BCの基盤が弱く、validationがない

v2 BCは200 stepsのsmokeであり、full teacher corpusで再封印後の正式学習ではない。lossは下がったが、以下がない。

- teacher policyとのheld-out log-prob fidelity
- v2表現を使ったvalidation loss
- lane間の同一基準でのBC評価
- full snapshotで学習したθ0との比較

Claude報告の後半監査でも、旧splitのrocket corpusが2,749 examplesしかtrainへ入らず、約46 epoch相当の暗記になっていた問題が記録されている。split修正後のsnapshotでθ0を再生成する必要がある。

### P1-B: Alakazam収集faultとデータ選択バイアス

v2smokeではAlakazam 8局中2局がfault。過去の測定では並列収集下で10〜32%のfaultが出ている。原因は判断hard timeoutの長い裾と負荷依存である可能性が高いが、今回のfault recordには`error=None`しか残っておらず、完全な原因特定には至っていない。

faulted gameを単純に除外すると、特定の局面・長い判断系列・特定の行動を含むtrajectoryが学習データから消える可能性がある。少量データでは勝率推定だけでなく学習分布も変える。

### P1-C: 評価の再現性と標本数

既存のt3資料では、同一checkpoint・同一seed指定でも96局結果が0.302〜0.448に散ることが報告されている。候補原因は以下。

- 同じ相手agent objectを複数局で共有している
- engine側に完全にはseedされない乱数がある
- process／worker境界で初期化状態が異なる

この状態では96局を「決定的な再測定」と扱えない。v2smokeの6局評価はさらに小さく、採用判断に使えない。

### P1-D: diagnosticsの解釈上の問題

初回stepの`clipped_importance_fraction`はratioがほぼ1なのに0.35〜0.45だった。実装が`shift > log(rho_bar)`で、`rho_bar=1.0`のとき`shift>0`をclip扱いするため、浮動小数点の符号揺れを数えている。これは実際に有意な上側clipがあることを示さない。

したがって、clip診断には少なくとも次の区別が必要である。

- `ratio > rho_bar * (1 + epsilon)`
- `ratio`がthreshold付近の数値誤差
- `ratio < epsilon`のvanishing

---

## 7. Claude報告との照合と、以前の主張の訂正

Claude側報告には価値のある独立監査がある一方、初期節の「全課題解消」「全テストgreen」という主張は後続節で撤回・訂正されている。

### 7.1 そのまま採用できない初期主張

初期報告には以下の主張があった。

- 実データ50ゲーム、899 transitions、0 faultで完全動作
- 200-step V-traceでlossとTargetLogProbが大幅改善
- 1,230テスト中1,208 passed、22 skipped、0 failed
- すべての課題が解消済み

しかし同じ報告の後半監査では、次が判明している。

- 文字列grepで未実装をgreenにしていたconformance suiteがあった
- 未使用importで「孤立モジュール0」を達成していた
- teacher強度の前提が成立していなかった
- archaludon teacherは複数候補が4〜8%程度で、強いteacherとは言えなかった
- corpus splitがrandom／near-duplicate groupingのため崩壊していた
- 最終的に実装済み範囲と未実装範囲を再分類した

したがって、Claude報告は「独立監査が見つけた失敗パターン」の資料としては有用だが、冒頭の完成宣言は採用しない。現在の実行成果物と後半訂正を優先する。

### 7.2 Claude報告から現在も有効な知見

- 文字列存在検査は、dead codeやdummy dictionaryで容易にすり抜ける
- テストはファイルの形ではなく、実際に相手を変えた対局・checkpoint往復・fail-closed挙動を検査すべき
- dataset splitはepisode／near-duplicateの連結単位で検証しないと、train比率が壊れる
- 「強いteacherからのBC」という前提は、teacherそのものの現行pool評価を先に確認しないと成立しない
- provenanceと利用境界はcheckpoint metadataに固定すべき

---

## 8. 何が「解決済み」で、何が「未解決」か

| 論点 | 状態 | 根拠 |
|---|---|---|
| Gumbel logitをbehavior確率に使う問題 | 解決済み | v2smoke初回stepのshift約1e-8、actor tests |
| ゲーム間sampling seed相関 | 解決済み | completed gameのseedがlane内で重複なし |
| 旧v1/v2checkpoint混用 | 対策済み | representation_version=2、topology mismatch fail-closed |
| opponent provenanceのハードコード | 修正済み | 実相手IDをrecordへ保存 |
| V-trace累積attenuationの観測 | 観測可能になった | mean c、opponent strata、health JSON |
| V-trace累積attenuationそのもの | 未解決 | final `∏c`中央値が1e-6〜1e-8級 |
| BC anchor欠落による初期暴走 | 修正済み | BC係数0.1以上、t3でBC 0.4使用 |
| 相手分布と評価分布の一致 | 未解決 | v2smokeはrule-agentのみ、holdoutは実agent |
| Pokémon関係性表現 | 未解決 | linear+meanのswap不変性を再現 |
| critic相手条件 | 未解決／悪化要因 | seed単位embedding、未学習random offset |
| full BCとvalidation | 未実施 | v2smokeは200-step smoke |
| Alakazam収集fault | 未解決 | v2smoke 2/8、error=None |
| 評価再現性 | 未解決 | t3で同一条件の96局が散る |
| 小標本holdoutの性能判断 | 不可 | v2smoke各lane 6局 |
| CPU過剰threadによるBC遅延 | 解決済み | 28 threadから2〜4 threadへ制限、結果は速度のみ同値 |

---

## 9. 現状の学習がうまくいかない因果モデル

現在の性能停滞は、次の複合モデルで説明できる。

```text
旧データ／旧実装
  ├─ behavior probabilityが不正
  ├─ game seedが相関
  ├─ mirror相手への過学習
  ├─ value/BC/entropy設定の不備
  └─ dead_rhoだけを見て累積cを見ない
          |
          v
 v2で配線と観測は改善
  ├─ 初回on-policy ratioは正常
  ├─ seedは独立
  └─ cと相手別criticが見える
          |
          v
  しかし更新を固定batchへ繰り返す
  + trajectoryが長い
  + terminal rewardが疎
  + mean cが0.83〜0.91
          |
          v
  序盤へのtrace productが極小
          |
          +-----------------------------+
          |                             |
          v                             v
  実質的な学習信号が弱い       小batchへのfitは進む
  policy driftを制御できない    lossは下がる
          |                             |
          +--------------+--------------+
                         v
       rule-agent分布への局所適応／行動分布の変形
                         |
                         v
       held-out相手への汎化はlaneごとに不安定
```

この因果モデルの中心は、「実装が動くか」と「policy gradientが評価目的に沿った方向へ十分な信用割当を受けているか」は別問題だという点である。

---

## 10. ChatGPTへ渡して検討してほしい問い

以下は、別のChatGPTがこの資料を受け取った際に、特に検討してほしい問いである。

### 10.1 V-trace設計

1. terminal rewardのみ・trajectory長50〜100・mean c約0.85の環境で、V-traceを固定batchへ数回適用する設計は妥当か。
2. `∏c`、effective trace horizon、GAE-like truncation、fresh rollout頻度のどれをgateにするのが適切か。
3. `rho_bar=2.0`、`c_bar=1.0`、BC 0.4、advantage standardizationというt3設定は、固定offline datasetに対して理論的にどのようなbiasを持つか。
4. BC anchorを単純なlog-prob lossとして加えることの弱点は何か。KL trust region、PPO clipping、behavior policy mixtureの方が適切か。
5. `assert_on_policy_health_v1`を初回stepだけでなく各updateへどう接続すべきか。

### 10.2 表現設計

1. Pokémonを線形encodeしてmean poolすることでactive/benchやattachment hostが消える問題をどう直すべきか。
2. DeepSetsの各entity MLP、slot/zone別pooling、active/benchの専用encoder、graph/relational encoderのどれがこのドメインに適切か。
3. `selection_context`や`selection_type`をraw numeric scalarとしてlinearへ入れることの問題はどの程度大きいか。
4. candidate endpoint側のnested情報とstate backbone側の情報をどう共有すべきか。
5. representation swap-invarianceを検出するテストをどう拡張すべきか。

### 10.3 critic設計

1. opponent seed単位のembeddingは統計的に不適切か。
2. opponent deck class、policy version、calibrated strength bucketのどの条件をcriticへ渡すべきか。
3. critic-only embeddingをzero-init、freeze、または別pretrainingするべきか。
4. policyとcriticがbackboneを共有することの利点と、このデータ規模でのリスクは何か。
5. 相手ごとのvalue strataを、どの最小サンプル数以上で有効と判定すべきか。

### 10.4 実験設計

1. 「相手分布」「fresh rollout」「representation」「critic conditioning」を、どの最小factorial実験で分離すべきか。
2. 何局あればlaneごとの小さな改善を検出できるか。
3. 収集相手と評価相手を同じにすることと、評価相手を完全held-outにすることをどう両立するか。
4. Alakazamのfaultを除外するか、再試行するか、fault stateを別クラスとして扱うか。

---

## 11. 推奨する次の実験計画

以下は実装をまだ行っていない提案であり、現時点の判断材料である。

### Experiment A: 表現の最小反例テスト

目的: Pokémonの関係性欠落がpolicy logitsへ実際に伝わるか確認する。

条件:

- 同じcard setでactive/benchだけを交換
- 同じenergy/toolを別Pokémonへ交換
- candidate endpointを含む場合と含まない場合を分離
- `encode_state`、candidate logits、valueを比較

合格条件:

- active/bench交換でstate representationが明確に異なる
- attachment host交換でstate representationが明確に異なる
- 個別entityのfeature変化だけでなく、関係性変化に反応する

### Experiment B: critic conditioning ablation

3条件を同一trajectoryで比較する。

1. opponent conditioningなし
2. stable opponent class conditioning、embedding zero-init
3. game seed conditioning、現行方式

見る値:

- 初回Vの相手間分散
- V-trace advantage分散
- policy shift
- full trajectory `∏c`
- holdout score

現時点の予想では、3は最も不安定である。これは実測で確認すべきであり、断定ではない。

### Experiment C: fresh rollout対固定batch

同じtotal simulator budgetで比較する。

- C1: 8 gamesを4回再利用
- C2: 1 updateごとに8 fresh games
- C3: 2 updateごとにfresh games
- C4: 24〜32 gamesを1回だけ利用

gate:

- mean cだけでなくtrajectory `∏c`を記録
- `∏c`中央値が一定閾値を下回ったら停止
- policy shiftが一定範囲を超えたらfresh rolloutへ戻す

### Experiment D: 学習相手分布の一致

- train opponent scheduleへholdout対象の方策を含める
- 評価用の完全held-out setは別に残す
- rule-agent、real agent、強度bandを分離し、比率をmanifestに記録
- deckだけ同じで方策が違う相手を別カテゴリとして扱う

### Experiment E: full BC v2

- split修正後のsnapshotを再封印
- full corpusでv2 θ0を作成
- train/validation/testのlog-prob、action accuracy、candidate calibrationを記録
- v2 RLの改善は、必ずこのv2 θ0を基準にする

### Experiment F: 評価再現性

- 相手agent objectをgameごとに再生成
- process-isolated評価とpersistent worker評価を比較
- engine RNGとagent RNGを別々に固定・記録
- まず同一checkpoint 384局を2回行い、再現性の分散を推定

---

## 12. 提案する採用ゲート

これは既存の正式Promotion Gateを置き換えるものではなく、学習内部の停止・診断用の暫定ゲート案である。

### データ・収集

- laneごとのcompleted gamesを最低数以上確保する。8局smokeは性能判断に使わない。
- fault率とfault reasonを記録する。`error=None`のfaultは採用判断前に原因を解決する。
- train opponent分布、評価opponent分布、seat分布、policy versionをmanifestで比較可能にする。

### behavior整合性

- 初回stepの`abs(mean_log_probability_shift) <= 1e-5`
- 初回stepのmean ratioが1付近
- base logitsとdecode logitsを混同していないこと

### 更新健全性

- mean cだけでなくtrajectory `∏c`を記録
- `∏c`が極端に小さい場合は、追加updateを止めてfresh rolloutを要求
- vanishing ratio、clip ratio、entropy、policy shiftを同時に見る
- loss低下だけを健全性証拠にしない

### 評価

- laneごとに十分な局数を持つ
- seat別、opponent別、fault別を分解する
- 少数局のdeltaは「挙動確認」と表示し、「性能向上」と表示しない
- 少なくとも基準と候補の評価条件・seed・process境界を固定する

---

## 13. 現在のコード変更の概要

今回のv2修正および同時期の変更は、以下の責務を持つ。

### `actor_pool_v1.py`

- Gumbel decode logitsとbehavior base logitsを分離
- `_RecordingSessionV1`が3-tupleで両方を保存
- `derive_game_sampling_seed_v1`追加
- opponent instance provenanceを実相手へ修正
- fault detailへruntime errorを含める
- persistent workerは実装済みだが既定off

### `collect_trajectories_v1.py`

- game identityからsampling seedを導出
- opponent schedule／rotationを扱う
- worker lifetimeを選択可能

### `train_from_trajectories_v1.py`

- `LearningHealthV1`へratio、continuation c、opponent strata追加
- `assert_on_policy_health_v1`追加。ただし、現在は公開関数とテストのみで、学習ループの自動停止へは未接続
- advantage normalization経路を追加

### `trajectory_target_v1.py`

- state value計算へopponent instance情報を渡す

### `neural_model_v1.py`

- schemaをv2へ変更
- representation_version=2
- Pokémonのzone、energy、tool、pre-evolution、countを追加
- nested endpoint Pokémonを保持
- categorical scalarをlog変換しない経路を追加
- opponent value embedding追加

### tests

- actor sampling probability
- seed independence
- rich Pokémon feature
- nested endpoint
- categorical scalar
- opponent-conditioned critic
- learning health gate

ただし、現行テストには次の不足がある。

- pooled Pokémonのactive/bench swap invarianceを検出していない
- attachmentとhostの交換を検出していない
- opponent embeddingがseed単位で未学習になることを検出していない
- trajectory `∏c`をgateしない
- train/eval opponent distribution divergenceをfailさせない

---

## 14. 検証済みテストと実行状態

今回の最終確認:

```text
PYTHONPATH=. pytest tests/meta_specialist/test_actor_pool_v1.py -q
74 passed

PYTHONPATH=. pytest tests/meta_specialist/test_collect_trajectories_cli.py -q
37 passed

PYTHONPATH=. pytest tests/meta_specialist/test_train_from_trajectories.py -q
19 passed, 2 skipped

PYTHONPATH=/tmp/meta-testpkg:$PWD:$PWD/src pytest tests/meta_specialist/test_neural_model_v1.py tests/meta_specialist/test_neural_batch_v1.py tests/meta_specialist/test_neural_adapter_v1.py tests/meta_specialist/test_neural_export_v1.py -q
47 passed

python -m py_compile <modified meta-specialist modules>
success

git diff --check
success
```

注意: worktree環境ではrootの`tests` namespaceがインストール済みpackageにshadowされる問題があり、neural系テストは一時namespace setupを使って実行した。これはテストコードの失敗ではなく、この環境のimport解決問題である。

全`tests/meta_specialist`の一括実行は同じnamespace／長時間実行環境の影響で最終summaryを安定取得できていないため、全suite greenとは主張しない。変更対象に近いfocused testsは上記の通りpassしている。

---

## 15. Git・生成物・再現性に関する注意

- commit、push、Kaggle提出は行っていない。
- worktreeには今回変更以外のユーザー既存変更も残っている。
- run summaryの`source_commit`はdirty worktree差分を含まない場合がある。実験の完全再現にはcommitだけでなく、対象ファイル差分、model config、opponent manifest、seed、runner commandを保存する必要がある。
- `local_eval_only`相手資産を提出bundleへ入れてはならない。
- 大量の`runs/`、teacher records、opponent assetsは成果物と中間生成物を分けて扱う。
- `deck.csv`、`cg/`、opponents、pool manifestには既存ユーザー変更があるため、別作業のために戻したり削除したりしない。

---

## 16. 参照ファイル一覧

### 根拠レポート

- [vtrace-no-progress-20260807.md](vtrace-no-progress-20260807.md)
- [vtrace-degenerate-collapse-20260804.md](vtrace-degenerate-collapse-20260804.md)
- [vtrace-rl-degrades-against-eval-pool-20260807.md](vtrace-rl-degrades-against-eval-pool-20260807.md)
- [rl-round-cost-and-actor-faults-20260807.md](rl-round-cost-and-actor-faults-20260807.md)
- [bc-thread-oversubscription-20260807.md](bc-thread-oversubscription-20260807.md)
- [vtrace-learning-health-20260808.md](vtrace-learning-health-20260808.md)

### 実装

- `src/mage_ptcg/meta_specialist/actor_pool_v1.py`
- `src/mage_ptcg/meta_specialist/collect_trajectories_v1.py`
- `src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py`
- `src/mage_ptcg/meta_specialist/trajectory_target_v1.py`
- `src/mage_ptcg/meta_specialist/neural_model_v1.py`
- `src/mage_ptcg/meta_specialist/vtrace_bridge_v1.py`
- `src/mage_ptcg/meta_specialist/opponent_pool_v1.py`

### Claude report

- `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/plan/META_SPECIALIST_CANONICAL_IMPLEMENTATION_REPORT.md`

### v2smokeの代表成果物

- `runs/meta-specialist-training/v2smoke-rl-alakazam/run_summary.json`
- `runs/meta-specialist-training/v2smoke-rl-archaludon/run_summary.json`
- `runs/meta-specialist-training/v2smoke-rl-grimmsnarl/run_summary.json`
- `runs/meta-specialist-training/v2smoke-rl-rocket/run_summary.json`
- `runs/meta-specialist-strength/v2smoke-alakazam.json`
- `runs/meta-specialist-strength/v2smoke-archaludon.json`
- `runs/meta-specialist-strength/v2smoke-grimmsnarl.json`
- `runs/meta-specialist-strength/v2smoke-rocket.json`

---

## 17. 最終要約

現在の最も妥当な判断は以下である。

1. 旧実装のbehavior probability、seed、provenance、表現不足、診断不足は修正が進んでいる。
2. 初回on-policy整合性はv2smokeで確認できた。
3. しかし、policy update後の累積traceは急速に弱くなっている。
4. 学習相手と評価相手が異なるため、収集scoreの改善は汎用性能を意味しない。
5. v2表現はfeatureを増やしたが、線形+mean poolingのため、active/benchとattachmentの関係性を失っている。
6. criticのseed単位embeddingは、少量データとBC未学習初期値の組み合わせで大きなノイズになっている。
7. v2smokeの勝率差は24局だけで、性能向上の根拠にならない。
8. 次に最優先すべきは、fresh rollout／trace product gate、関係性を保持するstate encoder、stable opponent critic conditioning、評価分布一致、full BC validationである。

別のChatGPTが検討する場合、単に「learning rateを調整する」「ゲーム数を増やす」と結論せず、まずP0-A〜P0-Dのどれが性能上限を決めているかを、A〜Fの最小実験で分離するのが望ましい。

---

# 18. Luna Max計画の継続実装（2026-08-08 JST）

この節は、`META_SPECIALIST_V3_LUNA_MAX_IMPLEMENTATION_EXPERIMENT_PLAN.md`に従って今回追加した実装・実験・Gate判定を追記したものである。対象worktreeは`/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`、branchは`feature/meta-specialist-canonical`。commit・push・提出は行っていない。

## 18.1 Phase 0実測

- HEAD: `a4e6475255ff7ac56469f87cfd0ca6214de749af`
- Python 3.12.3
- `nvidia-smi`: `Failed to initialize NVML: GPU access blocked by the operating system`
- dirty worktreeは既存ユーザー変更を含む。破壊操作なし。
- `git diff --binary` SHA-256: `3a4f6337a7e78840137a35e0376b1924615286718988938548d204172d60ddf9`

既存focused testsは`test_actor_pool_v1.py` 74 passed、`test_collect_trajectories_cli.py` 37 passed、`test_train_from_trajectories.py` 19 passed/2 skippedだった。詳細は`docs/evidence/meta-specialist-v3-phase0-preflight-20260808.md`に固定した。

同一ledgerでAlakazam/Archaludonのv2 checkpointをfresh processとpersistent workerに各8局実行したが、fresh対persistentの完全一致はAlakazam 0/8、Archaludon 0/8、fresh再実行の共通一致も0/7だった。Alakazam fresh再実行では8局中1 faultも発生した。同じseedでも勝敗、steps、transition数、action/content hashが一致しないため、現行CABTではexact replayを前提にできない。評価標準はgame-local fresh process + sealed paired ledger、persistent workerはnegative controlとする。

## 18.2 Phase 1実装

`representation_v3.py`にtyped `EntityTokenV3`、`ActionCandidateV3`、`RelationalStateV3`、stable action hash、v1→v3境界adapterを追加した。hidden/unresolved entityはcard idを持てず、serial/local action idは復元しない。Pokemonのtool/energy/pre-evolutionはhost edgeを保持する。

`neural_model_v3.py`にはR3-A `ZoneDeepSetsEncoderV3`、R3-B `RelationAwareEncoderV3`、candidate source/target/action/step/stable-id encoder、1-layer GRUを追加した。`episode_start=True`はhidden stateを破棄し、model constructorはglobal Torch RNG stateを変更しない。

relation/invariance tests 11件は全てpassした。active/bench、attachment host、ownerの変更はrepresentationを変え、exchangeable bench permutationとhidden card identity変更は変えず、legal-action orderはlogit順序だけを変える。

### Gate 1は未通過

synthetic 128-example benchmarkでは、R2 negative controlがNLL 0.7078/top1 1.0000/p95 0.0713ms、R3-Aが1.5035/top1 0.3846/top3 0.6154/p95 1.1333ms、R3-Bが1.4856/top1 0.4231/top3 0.8462/p95 1.3142msだった。entity-pool residual追加後に`t1-rocket` teacher recordをv2再検証→v3投影した128件・3 epoch smokeでは、R2はNLL 2.0122/top1 0.4231/top3 0.5769/p95 0.7281ms、R3-Aは1.9903/top1 0.4231/top3 0.7308/p95 15.9525ms、R3-Bは1.9829/top1 0.4231/top3 0.7308/p95 15.8047msだった。

relation testは通過したが、real sliceではNLLが微改善した一方CPU latencyが約22倍で、sampleも小さいためGate 1は未通過である。これはv3廃止の根拠ではなく、「関係性テストを通れば十分」としないための実測negative resultである。詳細は`docs/evidence/meta-specialist-v3-phase1-representation-20260808.md`。

なお、synthetic benchmarkを同じ128例で20 epochsへ延長するとR3-BはNLL 0.1018/top1 1.0000まで到達し、5 epochs時の低指標は最適化budget不足も含むことが分かった。一方、R3-B p95 8.59ms対R2 0.28msというCPU latency差は残る。したがってGate 1の正式比較は、equal compute/early stoppingを固定し、undertrained v3対converged R2の不公平比較を避ける必要がある。

## 18.3 後段の共通基盤（実装済み、正式Gate未判定）

次のモジュールを追加した。

- `critic_v3.py`, `critic_warmup_v3.py`: seed/opponent instanceをcritic入力にしないoutcome distribution、zero-init uniform head、episode-balanced warm-up、Brier/ECE/value metrics
- `trajectory_schema_v3.py`: full legal-action base logits/log-probs、chosen behavior log-prob、sampling mode、hidden hash、latencyを必須化し、Gumbel摂動logitの誤保存を拒否
- `bc_trainer_v3.py`: episode/near-duplicate split、per-episode BC loss、validation-best checkpoint、teacher recordからstable candidate targetを復元
- `learner_common_v1.py`: normalized entropy、exact policy drift、V-trace effective kernel、advantage diagnostics
- `learner_ppo_recurrent_v1.py`, `learner_vtrace_online_v1.py`, `learner_awr_crr_v1.py`: fresh PPO、consume-once V-trace、bounded AWR/CRR
- `evaluation_protocol_v2.py`, `opponent_schedule_v2.py`: Wilson/paired bootstrapとsampling floor付きadaptive mixture
- `experiment_manifest_v1.py`, `fault_diagnostics_v1.py`: hash-sealed lineage、promotion gate、exception/stack/latency/retry分類
- `search_teacher_v1.py`, `dagger_dataset_v1.py`: low-confidence soft search targetとstate+policy-version dedup

新規focused tests 32件と`compileall`はpass。これは実装・数値健全性の証拠であり、実CABT勝率改善の証拠ではない。

テストnamespaceを一時隔離してroot/`agents`/`src`をPYTHONPATHへ加えた全`tests/meta_specialist`実行は、critic conditioning provenance対応、promotion gate wrapper、DAgger dedup追加テストを含め`1481 passed, 23 skipped, 2 warnings in 93.66s`で完了した。通常cwdからの一括実行で出る`ModuleNotFoundError: tests.meta_specialist`はインストール済み`tests` packageのshadowingであり、実装失敗とは分離して記録する。

full BC smokeも実行した。`t1-rocket`から64件を再検証し、48/16 episode-group split、1 epoch、best epoch 0、validation NLL 1.6676543、checkpoint 71 tensorsを得た。これはformal θ0ではない。詳細は`docs/evidence/meta-specialist-v3-bc-smoke-20260808.md`。

## 18.4 Current Gate table

| Gate | 状態 | 根拠 |
|---|---|---|
| 0.1 census | PASS | dirty state/source diff保存 |
| 0.2 focused tests | PASS | 74/37/19(+2 skipped) |
| 0.3 reproducibility | CONDITIONAL | fresh process標準化、exact replay失敗 |
| 0.4 RNG/lifecycle | PARTIAL | local sampling seed確認、native engine RNG未解決 |
| 1 representation | NOT PASSED | relation tests pass、NLLは微改善したがCPU latencyが約22倍、full corpus未検証 |
| 2 critic | IMPLEMENTED, NOT CALIBRATED | smokeのみ、completed episodes正式calibration未実施 |
| 3 formal θ0 | SMOKE ONLY | 64 record、48/16 split、1 epoch |
| 4-6 learners/eval | INFRASTRUCTURE ONLY | primitives/paired eval実装、fresh screening未実施 |
| 7-12 | NOT STARTED | Gate 1-3とsealed promotion pool未完了 |

## 18.5 次に実行すべき最小セット

1. full teacher corpusをepisode group + near-duplicate connected componentでsplitし、R3-A/R3-Bを3 seedsでNLL/top-k/rare-action/action-type/p50/p95比較する。
2. R3-BのCPU latencyをprofileし、feature batchingとattention sequence上限を検討する。relation edgeは壊さない。
3. Gate 1を通過したrepresentationだけで4 lane full BCをbest validation checkpointとしてsealする。
4. θ0からcriticを64 completed episodes以上でwarm-upし、uniform Brier改善・V bounded・seed provenance不変を確認する。
5. fresh process paired ledgerでAlakazam/Archaludonを24–64局ずつ評価し、fault retry分類を含めてcandidate lossとして集計する。

現時点の最重要結論は、後段の機械部品は増えたが、Gate 1（表現の実データ性能）とGate 3（formal θ0）が未通過であり、性能向上を主張できる段階ではない、ということである。

## 18.6 ベクトル化後の4 lane比較と critic conditioning ablation（追加）

R3 encoder の entity token処理をベクトル化した後、同一 seed=7、各 lane 128 records、3 epochs の bounded real-record benchmark を再実行した。これは従来の512-record runとは別実装状態の結果であり、以下を現行値として採用する。

| lane | R2 NLL | R3-A NLL | R3-B NLL | R2 p95 ms | R3-A p95 ms | R3-B p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| alakazam | 1.820462 | 1.852043 | 1.864900 | 0.491 | 2.096 | 1.685 |
| archaludon | 1.517733 | 1.482217 | 1.476455 | 0.572 | 2.258 | 1.997 |
| grimmsnarl | 1.948337 | 2.020154 | 2.075685 | 0.459 | 2.395 | 2.263 |
| rocket | 2.079650 | 2.312241 | 2.367937 | 0.688 | 3.933 | 2.629 |

ArchaludonだけはR3-BのNLLがR2を下回るが、top-1はR2より低い。残り3 laneではR3-B NLLがR2を上回る。p95はR3-A/R3-BともR2の約3–5倍であり、128 records・1 seed・3 epochsではGate 1を通過させる根拠にならない。従って、ベクトル化は「実行可能性を改善した」が「mainline選択を確定した」わけではない。

Criticについては、`scripts/run_meta_specialist_v3_critic_conditioning.py`を追加し、C0（none）、C1（stable opponent family）、C2（game-seed negative control）を96 train episodes/48 validation episodes/100 epochsで比較した。stable familyだけがvalidationにも保持され、game-seedはtrain上の見かけの相関をvalidationで失う。これはconditioning実装のnegative-controlとしては合格だが、実teacher corpusのcalibrationではない。

| mode | validation Brier | validation value/outcome correlation | 判定 |
|---|---:|---:|---|
| C0 none | 0.500014 | -0.0312 | marginal baseline |
| C1 stable | 0.481256 | 0.9981 | toyで改善、実lane未検証 |
| C2 game-seed | 0.500083 | -0.0033 | seed leakage negative control |

warm-up APIはsubclassとoptional provenanceを受け付けるよう修正し、episode-balanced lossを維持した。詳細JSONは`runs/meta-specialist-v3/phase2-critic-conditioning-ablation.json`。

この追加結果を含む最終bundleは`docs/evidence/meta-specialist-v3-final-report.md`と`runs/meta-specialist-v3/final/`に生成した。formal θ0、4,096局promotion、Phase 7–12の成功判定は依然として行っていない。

## 18.7 Phase 7–9 integration smoke（追加）

`scripts/run_meta_specialist_v3_phase7_9_smoke.py`を追加し、PPO exact KL、consume-once V-trace queue、AWR/CRR weight、O0/O1/O2 opponent schedule、soft search target、DAgger state/policy-version dedupを一つのcontract smokeで通した。seed=7、synthetic paired 64局、DAgger attempted 8 records→deduplicated 4 recordsである。

これは実actor rolloutやscreeningではない。したがって、Phase 7–9は「wiring実装・contract smoke済み、real two-lane comparison未実施」とし、Phase 10–12のpromotion評価とは分離した。詳細JSONは`runs/meta-specialist-v3/phase7-9-smoke.json`。
