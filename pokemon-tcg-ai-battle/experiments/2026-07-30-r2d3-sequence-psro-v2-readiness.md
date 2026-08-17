# R2D3 sequence／PSRO v2 長時間学習 readiness

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-30 04:45 JST |
| 担当 | Codex |
| 種別 | local experiment |
| commit | `db2087da051680fa0ad71cd624ec476cb033c5e5` |
| branch | `local/offline-scaleup-v2` |
| model provenance | Codex GPT-5 family（実model IDはUI非公開）、high effort、codex-cli 0.144.1 |
| simulator / data | CABT local runtime、Kaggle leaderboard snapshot `2026-07-29T17:48:05.581206+00:00`、deck pool `53845668ada8fa9be78631061d44ae92945e7e7b1706b5dd023e697046780eef` |

## 目的と反証条件

- **問い**: ChatGPTレビューで指摘された「系列長1、batch平均priority、frozen replayだけのPSRO、hash-only特徴」を是正し、実CUDA／CABTで長時間productionを開始できるか。
- **仮説**: burn-in 8、learner unroll 20、5-step lookahead、item-wise PER、CQL、PSRO mixture online replay、構造化特徴、resource-adaptive worker/batchを同じcontrollerで完走できる。
- **反証条件**: 系列後半から先頭状態へ勾配が届かない、lookahead終端が誤terminalになる、priorityがsample別でない、PSRO収集provenanceが欠ける、NaN／illegal／timeout、replay identity不一致、protected file変更のいずれか。
- **変更点**: recurrent learner、episode-first PER、CQL、categorical auxiliary head、二層normalized encoder、production hidden 256、PSRO online collection／offline-online交互学習、実worker/batch sweep、上位deck pool。
- **固定条件**: Rule v0 Champion、`main.py`、`deck.csv`、holdout分割、Promotion Gateを変更しない。smokeは性能判断に使わない。

## 再現

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest tests/test_submitted_opponents_r2d3.py tests/test_gate5_evaluation.py -q

bash scripts/policy_learning/run_r2d3_multiseed_psro_performance.sh \
  --profile smoke \
  --artifact-root /home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-sequence-psro-v3-fast-smoke-20260730 \
  --run-root runs/r2d3-sequence-psro-v3-fast-smoke-20260730 \
  --gpu-id 0 \
  --source-artifact /home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-opponents-r2d3-psro-v2-deck-disjoint-20260730 \
  --deck-pool data/opponent_deck_pool_20260730/opponent_deck_pool.json

.venv/bin/python scripts/policy_learning/run_r2d3_learner_soak.py \
  --replay-artifact /home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-sequence-psro-v2-resume-smoke-20260730 \
  --artifact-root /home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-sequence-learner-soak-v1-20260730 \
  --updates 200 --batch-size 2048 --hidden-size 256 --core gru --gpu-id 0

PYTHONPATH=.:src .venv/bin/python -m pytest -q
```

生成物はWSL停止後も残るGit管理外の`/home/bfe-lab-ono/kaggle/handoff-artifacts/`に置く。`/tmp`はWSL停止時に消失したため長時間runには使用しない。

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| development smoke | 1 | 8 | 25.00% | 0 | 0 | smoke | 40 updateだけの非性能評価 |
| PSRO best-response smoke | 1 | 4 | 0.00% | 0 | 0 | smoke | mixture online 4局、6 sequence |
| complete controller | 3＋screen | 128 replay＋各gate | — | 0 | 0 | PASS | 15 stageすべてPASS、別process resume PASS |

- **sanity check**: 公式Kaggle公開Replayは35/35 deckを取得し、23 deckがunique。remote refを合わせた候補45 sourceは24 unique exact-60 deckへ正規化した。Comfey Library-out（27 unique card ID）とHydreigon Deck-out（34 unique card ID）を含む。
- **系列学習**: recurrent coreは20 learner stepと5 lookaheadを処理し、後半lossから先頭state encoderへ非ゼロ勾配が到達した。replayはepisodeを一度だけ保存し`window_refs`でburn-in／unroll／lookaheadをmaterializeする。
- **priority／再開**: sample別priorityを返し、sample ID、旧値、新値、importance weightを監査記録する。v2 checkpointはmodel／target／optimizer／RNGに加え、全PER priorityとtraining identityを保存する。連続12 updateと6 update後にfresh model／Replayへ復元した12 updateの最終model、target、priority、次sample indices／weightsは完全一致した。各architecture／seedはimmutable transition payloadを共有しつつ独立priorityから開始する。
- **独立監査是正**: 旧source splitはtrainingとdeck holdoutに同一deck hashを1件含んでいたため、旧v2 smokeをsplit証拠から除外した。policy hash／source lineage／deck hashの連結成分でsplitし直し、training 12／validation 2／deck holdout 1／final holdout 1の全組合せでdeck hash非交差を確認した。旧Gate 5のdeck holdoutもtraining deck fingerprintを除外する。
- **holdout／identity**: deck／final holdoutはともにCABT前にfsync付き`RESERVED`を書き、途中停止後の再消費を拒否する。campaign identityは`main.py`、`agents/`、`src/mage_ptcg/`、policy-learning scripts、依存定義を含む316 filesへ拡張し、stage開始後のsource rebaselineを拒否する。full checkpointは現在のtraining identity／step／SHAをload前に照合する。
- **PSRO**: meta-strategyをgame開始時に固定seedでsampleし、mixture hash、member probability、policy／lineage、結果を全局保存する。best-responseはfrozen offlineとonline partitionを交互にsampleした。payoff 12局は局単位のdurable prefixを保存し、単体テストでは2局後の模擬停止から残り3局だけを再実行した。
- **資源計測**: 最新fast smokeの実process-pool sweepは12 workerを選択（各8局だけのため性能採用根拠ではない）。CPU収集workerをCUDA評価へ流用せず、独立CUDA contextは最大4 process。CUDA warm-up後はbatch 128を選択（1,145.79 sequence/s、peak reserved 962 MiB）。
- **production形状soak**: hidden 256／batch 2,048で200 update、40 durable checkpointを完走し、実行中VRAM 32,184 MiBを観測した。lossは1.726→0.197、distributional lossは0.826→0.021で、loss／TD／priority／gradientは全記録で有限。100 updateでfresh model／Replayへ復元し、その後200まで完走した。完了後の別process `--resume`はSHA照合後に再学習せずcheckpointを再利用した。
- **production資源幅**: actor 4〜28、CUDA batch 64〜3,072を実測する。3,584以上を含む境界探索中にWSLが停止したため、安定性を優先して3,072を上限とした。総sequence budgetはbatch 128基準で保存し、update数を換算する。
- **負の所見**: smoke勝率は昇格水準ではない。development 25.0%、best-response 0.0%のためdeck／final holdoutを開かず、`NO_PROMOTION_RECOMMENDED`とした。
- **不確実性**: smokeとsoakは実行健全性の証拠であり性能比較ではない。production multi-seedと独立holdoutを完了するまで性能改善を主張しない。旧20,000局Replayはsemantic feature versionが異なるため再利用しない。

## 解釈と判断

- **観測事実**: レビューの主要な実装指摘はコードと実テストで再現され、系列、PER、PSRO collection、CQL、表現、episode samplingを是正した完全controllerがfault 0で完走した。
- **解釈**: hidden 256と大batchはGPU処理量を増やすが、性能効果はproduction評価まで未確定である。sequence-budget保存と平方根learning-rate補正により、batch増加だけでreplay exposureを水増ししない。
- **判断**: 新規production artifactを開始可能。旧v7 checkpoint／旧semantic replayへのresumeは互換性がないため禁止する。production収集は動作確認目的の20,000局を繰り返さず、現semanticで5,000局（上位deck 1,000局を含む）＋既存raw Gate 3再encodeへ縮小した。
- **言わないこと**: smokeの勝率、Kaggle上位deckを相手にしたReplay収集、throughput向上から、Kaggle score又はRule v0超えを推論しない。
- **次 action**: ユーザーの最新指示によりproductionは本作業では起動しない。別途開始指示がある場合だけ、commit固定済みの新規rootで開始し、controllerが選んだworker／batchと推定時間を確認する。Promotion Gate通過時だけsubmission candidateをbuildする。

## Kaggle 提出（該当時）

| 項目 | 値 |
|---|---|
| submission name | 未作成 |
| submitted at | 未実行 |
| source commit | `db2087da051680fa0ad71cd624ec476cb033c5e5` |
| local verification | deck-disjoint fast smoke 15 stage＋別process resume PASS、production形状soak 200 update PASS、focused 49 passed／1 skipped、全suite 1,998 passed／11 skipped |
| Public LB | 未確定 |
| Private LB | 未確定 |
| Kaggle URL / ID | 該当なし |
| 備考 | smokeがPromotion Gate未達のため提出しない |
