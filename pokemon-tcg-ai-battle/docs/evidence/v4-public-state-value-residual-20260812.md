# V4 public-state value residual bounded実験記録（2026-08-12）

## 結論

Wave6を凍結したまま、sealed actor-visible replayから交差検証したpublic-state value targetを使い、粗い公開bucket×semantic action残差だけを更新するbounded armを実装・実行した。実装・provenance・coverage・CABT faultは正常だったが、seed別残差とseed共有残差のどちらも、両seed・両seat・複数blockで安定した改善を示さなかった。したがって、このarmは性能candidate・Champion・longrunへ昇格しない。得られた勝率差は、engine seed setterがなくgame-level pairingも成立しないCABTの独立層化結果としてのみ扱う。

今回の実験で確認できた事実は次の通りである。

- public-state targetから実際のresidual tableを学習できた。
- base V4 checkpointは凍結され、residual tableだけが更新された。
- coarse gateはunknown public bucketをbaseへpass-throughし、known bucketでは高いcoverageを得た。
- lr=0.1ではtop1 action changeが0で、勝率差は行動変更を伴わないnoiseだった。
- episode-normalized/lr=1000ではtop1 changeが発生したが、seed0/seed1・block・seatで方向が反転した。
- seed0/seed1 rowsを共有表へ混ぜてもseed1 seat0/seat1の安定性は得られなかった。
- 既知のWave6 noise（seed1 96局block SD 7.51pt）を考慮すると、現在の+数勝だけで因果改善とは言えない。

## 実験対象と固定条件

subject deckは`opponents/tomatomato_archaludon/deck.csv`（SHA-256 `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）。対応Wave6 V4 base checkpointはseed0/1とも、preflight manifestにbindされたものを使用した。

| 項目 | 固定値 |
|---|---|
| held-out opponents | `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`, `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario` |
| protocol SHA | `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba` |
| public reference bundle | `runs/meta-specialist-public-bucket-reference-bundle-20260812/train-bundle.json` |
| bundle SHA | `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda` |
| ordered source-list SHA | `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb` |
| preflight manifest | `runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json` |
| preflight SHA | `7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689` |
| evaluation pairing | `independent_stratified_not_game_paired` |
| CABT engine seed setter | `false` |
| initial smoke | 2 games/opponent×seat = 24 games/seed |
| noise-aware blocks | 8 games/opponent×seat = 96 games/seed/block |
| authority | training/performance/promotion/longrun all `false` |

CABTのengine RNGは`std::random_device`/`std::shuffle`由来で、`run_match`から渡せるのはagent側seedだけである。同じbase seedを再利用してもgame-level common random numberは成立しない。そのため以下の差分はMcNemarやpaired bootstrapではなく、seed×seat×opponent×repetitionで層化した独立診断である。

## 1. public-state value target

`src/mage_ptcg/meta_specialist/cross_fitted_public_state_value_v1.py` と `scripts/build_cross_fitted_public_state_value_manifest_v1.py` を新規追加した。入力はsealed actor-visible replayのterminal reward、公開構造bucket、episode SHAであり、opponent ID、seat、policy identity、private stateはtargetに使わない。

episode内returnは、逆順に

```text
G_t = reward_t + discount_t * G_(t+1)
```

で計算した。baselineは同一episodeを含めないdeterministic fold外のpublic bucket平均を優先し、bucketがfold外にない場合はfold外global transition-return平均へfallbackする。targetは`clip(G_t - V_hat(public_bucket), -1, 1)`で、schema上のtarget kindは`signed_public_state_value_residual`、objectiveは`cross_fitted_public_bucket_value_advantage`である。

| seed | episodes | transitions | public-bucket source | global fallback | artifact | SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 74 | 3,678 | 3,500 | 178 | `runs/meta-specialist-public-state-value-20260812/seed-0-public-state-value-v1.json` | `15809fb7fe3e473a7d3c37c223c1d803bd5feeab87bc6ccb27942963d86872ce` |
| 1 | 69 | 3,892 | 3,707 | 185 | `runs/meta-specialist-public-state-value-20260812/seed-1-public-state-value-v1.json` | `e31a2ed1e3c4949eb043b5f7e5e9671fe3560de00213420db7335dbd30cd906` |

（seed1 SHAは生成時の正本を`sha256sum`で再確認した値。manifest内のsource screen SHAはseed0 `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`、seed1 `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26`。）

このtargetはteacher hard labelでもcounterfactual Qでもない。on-policy episode outcomeからpublic bucket baselineを差し引いたdirectional residualであり、負weightを含むため通常NLLの減少として評価してはならない。

## 2. V4 replay row materialization

`scripts/build_coarse_public_value_rows_v1.py` は、各seedの対応Wave6 checkpointをstrict loadし、`representation_v4_from_step_input_v1`と`forward_record_group_v4`でsealed transitionsを時系列再生した。record内prefixは同じincoming recurrent tokenで計算し、record間だけhiddenをcarryした。semantic action keyはソートしてからbase logitと同じ並びへ再配置し、chosen target indexを再計算した。

| seed | episodes | transitions | prefix rows | public buckets | target source | row artifact SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 74 | 3,678 | 7,784 | 371 | external global fallback 375 / public bucket 7,409 | `07ff84efb01cc70ceeac8f42f32ef14a827c950cfdbd5c4f349d855ddf56bc26` |
| 1 | 69 | 3,892 | 8,259 | 375 | external global fallback 399 / public bucket 7,860 | `f210883d51d33009c31e4ca4d1ace648895023e5e2f8470ec84628238bc16b80` |

row schemaは`specialist-coarse-public-value-logit-rows-v1`で、各rowは`episode_id`、`record_id`、`prefix_index`、public `bucket_id`、sorted legal `action_keys`、detached `base_logits`、legal `target_index`、signed weightを持つ。train source SHA、value manifest SHA、checkpoint file/tensor SHA、authority falseを閉じ込めた。全prefixのtarget indexはlegal semantic domain内で、STOP availabilityも一致した。

## 3. coarse gateとzero-init coverage smoke

`src/mage_ptcg/meta_specialist/coarse_public_residual_gate_v1.py` と `coarse_public_residual_factory_v1.py` は、public bucketがreference bundleに存在し、action keyがvalidで、residualがfinite/boundedの場合だけ加算する。それ以外はbase logitsをdetachしたまま返す。semantic decode、physical alias選択、legality、STOP、GRU commitはbase V4へ委譲した。

zero tableは`runs/meta-specialist-public-bucket-reference-bundle-20260812/zero-table.json`（SHA `3d2c06c55a42c3a221eefcf518ef111aac44c9f986961ee3e817de02ea983480`）である。zero-init armは「gateがruntimeで適用可能か」の確認で、性能candidateではない。

| seed | score | decisions | known bucket | applied slots | nonzero slots | top1 change | OOD pass-through |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 12/24 | 1,627 | 1,614 (99.2010%) | 4,945/6,436 (76.8334%) | 0 | 0 | 13 (0.7990%) |
| 1 | 12/24 | 1,705 | 1,692 (99.2375%) | 5,870/7,369 (79.6580%) | 0 | 0 | 13 (0.7625%) |

zero tableを通すだけで、known bucket coverageとapplied slot coverageは高い。従ってexact context/action gateのような`<1%` coverage問題はcoarse bucketで解消できる。しかしzero residualである以上、ここでの勝率はbase policyの独立CABT noiseであり、coarse gateの性能証拠ではない。

## 4. complete-action normalization trainer

`src/mage_ptcg/meta_specialist/coarse_record_residual_trainer_v1.py` はrecord内全prefixをgroup化し、complete-action log probabilityを構成する。record IDがepisode間で再利用される実dataに対応するため、内部group keyを`(episode_id, record_id)`へ修正した。record/episode normalizationの比率を分け、base logitsはdetached、coarse tableだけをSGD更新した。

anchor KLは理論上非負だが、float32の`p*(log p-q_log)`で微小負値になることがあり、result validatorがarmを失敗させていた。これはtargetやCABTの問題ではなく数値丸めだったため、KL regularizer/diagnosticを`clamp_min(0)`する修正を入れ、fixture 4 testsを再PASSさせた。

### lr=0.1、3 updatesのseed別arm

| seed | mode | table SHA | max residual | signed complete loss | 24局 score | top1 changes |
|---:|---|---|---:|---:|---:|---:|
| 0 | record | `3eff40693018fa1b7589e9abea1f03cdb62871c1477325701bb862b58898d51a` | 1.2138e-05 | 0.01708 | 10/24 | 0 |
| 0 | episode | `b34feec41e5b52d9f6227d10845f3d96f102b4f3eff14a074bc6021c702f386d` | 1.4686e-05 | 0.000990 | 12/24 | 0 |
| 1 | record | `1862e6fba5745eb8c7bf62f8f771e54e19f0c8a0e0101b0fac1c9cdfa7d908ce` | 8.5242e-06 | 0.05325 | 11/24 | 0 |
| 1 | episode | `81ffff1f12156e6277f4fac477aa9522cdcff96f598dc1aa6a34ce6e37c52dd1` | 8.0391e-06 | 0.03711 | 13/24 | 0 |

lr=0.1では4 armすべてのtop1 changesが0だった。したがってscore差は行動差を伴わないCABT RNG差であり、candidateとして解釈しない。

### episode-normalized、lr=1000のseed別arm

行動変更が生じるかを判定するため、normalization modeをepisodeへ固定したまま、学習率を一段だけ大きくしたbounded armを実行した。これは勝率を見て選んだthresholdではなく、lr=0.1のtop1=0を受けて「residualが実際にdecodeを変えるか」を診断する一回の条件変更である。

| seed | table SHA | max residual | 24局 score | top1 changes | 96局block1 | 96局block2 |
|---:|---|---:|---:|---:|---:|---:|
| 0 | `c7428bbf52185939d3180de4c96c485f53c324213cf809be19044ea43f3bda20` | 0.13545 | 14/24 | 6 | 49/96 | 56/96 |
| 1 | `a86b31d5d302a809e6552a50ff6f717228a761f9eb7228783b034f08bc6b5b25` | 0.10785 | 10/24 | 3 | 51/96 | 42/96 |

coverageの詳細は以下である。

| seed/block | decisions | known bucket | applied slots | nonzero residual | top1 change | OOD |
|---|---:|---:|---:|---:|---:|---:|
| 0 / 24 | 1,471 | 98.50% | 74.45% | 94.52% | 6 | 1.50% |
| 1 / 24 | 1,408 | 99.36% | 76.68% | 94.93% | 3 | 0.64% |
| 0 / 96 block1 | 6,055 | 98.96% | 74.88% | 94.80% | 22 | 1.04% |
| 1 / 96 block1 | 5,907 | 98.98% | 75.89% | 94.63% | 16 | 1.02% |
| 0 / 96 block2 | 5,791 | 未集計（artifact参照） | — | 94.63% | 18 | — |
| 1 / 96 block2 | 6,012 | 未集計（artifact参照） | — | 94.59% | 10 | — |

### 96局 block1

同じbase seed recipeのzero controlを別の独立CABT blockとして実行した。pairedではないが、同じpool、seat、games/cell、protocol、base checkpointで比較した。

| seed | arm | wins/96 | losses/96 | score | seat0 | seat1 |
|---:|---|---:|---:|---:|---:|---:|
| 0 | episode residual lr1000 | 49 | 47 | 51.04% | 21/48 (43.75%) | 28/48 (58.33%) |
| 0 | zero control | 42 | 54 | 43.75% | 23/48 (47.92%) | 19/48 (39.58%) |
| 1 | episode residual lr1000 | 51 | 45 | 53.13% | 26/48 (54.17%) | 25/48 (52.08%) |
| 1 | zero control | 45 | 51 | 46.88% | 26/48 (54.17%) | 19/48 (39.58%) |

block1 aggregateはcandidate 100/192、control 87/192で差+13勝（+6.77pt）だった。しかしCABTは独立評価で、candidate seed0の改善は主にnihei/ozawa/yara、seed1の改善はsueで、同じopponentへ一貫しない。

### 96局 block2

base seedを`10101000`へ変更し、candidate/controlを並列に再評価した。seed1 baselineの最初の起動は誤ったtable pathを渡してSHA mismatchで即fail-closedし、正しいzero-tableで再実行した。失敗runは勝率artifactとして採用していない。

| seed | arm | wins/96 | losses/96 | score | seat0 | seat1 |
|---:|---|---:|---:|---:|---:|---:|
| 0 | episode residual lr1000 | 56 | 40 | 58.33% | 28/48 (58.33%) | 28/48 (58.33%) |
| 0 | zero control | 43 | 53 | 44.79% | 23/48 (47.92%) | 20/48 (41.67%) |
| 1 | episode residual lr1000 | 42 | 54 | 43.75% | 25/48 (52.08%) | 17/48 (35.42%) |
| 1 | zero control | 49 | 47 | 51.04% | 24/48 (50.00%) | 25/48 (52.08%) |

block2 aggregateはcandidate 98/192、control 92/192で差+6勝（+3.13pt）だが、seed1が明確に逆方向である。block1+block2の合算はcandidate 198/384 (51.56%)、control 179/384 (46.61%)、差+19勝/+4.95pt。しかし同一game pairingがないため、これは「候補が4.95pt改善した」と確定する統計証拠ではない。seed1 block2のseat1は17/48で、両seat非悪化gateに失敗した。

## 5. seed共有 residual arm

seed別学習がtraining seedへ過適応している仮説を切り分けるため、`scripts/train_coarse_public_value_shared_residual_v1.py` を追加した。seed0/1 rowsを`seed{0,1}:episode/record`へnamespaceし、同じepisode-normalized、3 updates、lr=1000、max residual=0.25で単一表を学習した。両checkpointのbase logitsを同じ粗いtableへ写像するため、評価時には各seedのfrozen base policyへ共有tableをwrapした。

| artifact | value |
|---|---|
| table | `runs/meta-specialist-coarse-public-value-residual-20260812/shared-episode_normalized-lr1000/table.json` |
| table SHA | `048cd017139d55a06f67b468537da4f1cec7f4ebfb8635e706a4541c3f9df15d` |
| report | 同ディレクトリ`training-report.json` |
| rows | 16,043 prefix / 7,570 records |
| max residual | 0.0788811 |
| target | `signed_public_state_value_residual` |

base seed `10102000`の96局/seed結果は次の通り。

| seed | wins/96 | losses/96 | score | seat0 | seat1 | top1 changes |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 50 | 46 | 52.08% | 32/48 (66.67%) | 18/48 (37.50%) | 9 |
| 1 | 38 | 58 | 39.58% | 14/48 (29.17%) | 24/48 (50.00%) | 10 |

shared armもseed1 seat0で崩れ、seed間安定性を改善しなかった。したがってseed共有だけでは根因を解消できない。

## 6. 判断

### 合格しているもの

- row materializationのcheckpoint/target/source SHA binding。
- complete-action record groupingとseed間record ID再利用への対応。
- zero-init、bounded tanh residual、base detach、base tensor不変。
- unknown bucket pass-through、semantic/STOP arity、V4 decoder/GRU commit委譲。
- CABT 24局/96局のfault=0、coverage telemetry、独立層化の明示。

### 不合格のもの

- seed0/seed1の両方でcandidateがcontrolを安定して上回ること。
- 両seat非悪化。
- 第1/第2 noise-aware blockで同方向になること。
- seed共有表による再現性改善。
- Rule v0、shadow-C、Champion、Kaggle提出へ進むpromotion gate。

特に、block1では両seedがcontrolより正方向だったが、block2ではseed1が逆方向になった。これは既存Wave6 noiseと同程度の不安定性であり、lr=1000 armを長時間学習へ拡張する根拠にならない。現時点の最終判定は `RESEARCH_DIAGNOSTIC_ONLY / RESIDUAL_ARM_NOT_PROMOTABLE` である。

## 7. 再現コマンド

### public target/rows

```bash
PYTHONPATH=.:src .venv/bin/python scripts/build_cross_fitted_public_state_value_manifest_v1.py ...
PYTHONPATH=.:src .venv/bin/python scripts/build_coarse_public_value_rows_v1.py ...
```

### seed別学習（代表）

```bash
PYTHONPATH=.:src .venv/bin/python scripts/train_coarse_public_value_residual_v1.py \
  --rows runs/meta-specialist-coarse-public-value-rows-20260812/seed-0-rows.json \
  --rows-sha256 07ff84efb01cc70ceeac8f42f32ef14a827c950cfdbd5c4f349d855ddf56bc26 \
  --reference-bundle-sha256 7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda \
  --reference-source-list-sha256 b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb \
  --mode episode_normalized --max-updates 3 --learning-rate 1000 \
  --max-abs-residual 0.25 --output TABLE.json --report-output REPORT.json
```

### CABT 96局/block

```bash
PYTHONPATH=.:src .venv/bin/python scripts/run_coarse_public_residual_cabt_eval_v1.py \
  --table TABLE.json --table-sha256 TABLE_SHA \
  --bundle runs/meta-specialist-public-bucket-reference-bundle-20260812/train-bundle.json \
  --bundle-sha256 7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda \
  --source-list-sha256 b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb \
  --preflight runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json \
  --preflight-sha256 7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689 \
  --seed 0 --subject-deck-csv opponents/tomatomato_archaludon/deck.csv \
  --subject-archetype-id archaludon --games-per-cell 8 --base-seed 10100000 \
  --output RESULT.json --execute
```

`--games-per-cell 8`は本実験で追加したbounded noise-aware modeであり、既存zero/24 smokeの`2`も維持している。出力JSONは`performance_evidence=false`を強制する。

## 8. 次の作業

このresidual armは、これ以上lr、epoch、threshold、normalizationを勝率で振らない。次の性能主線を選ぶ場合は、今回の失敗を明示した上で、別目的の候補（qualified teacher soft target、public-only value/advantageのより適切なaction-conditioned model、またはsearch/Qの公開境界実装）を一つだけ設計する。現状で許可されるのは、artifact/evidenceの整理、独立レビュー、次objectiveの設計である。

- `shadow-C`勝率評価は候補がfixed-six gateを通過していないため実施しない。
- longrun、Champion変更、Kaggle提出は実施しない。
- `lr=0.1` score差はtop1=0なので性能 evidenceに使わない。
- `lr=1000` score差はtop1 changeを伴うが、seed/block/seat reversalによりpromotion不可。
- `shared residual`はseed1崩壊を再現したため不採用。

## 検証履歴

- `tests/meta_specialist/test_coarse_record_residual_trainer_v1.py`: 4 passed（KL丸め修正後）。
- coarse gate/factory/normalization/public target/row trainer focused suite: 14 passed（zero/coarse前段）。
- 各学習runner: base provenance/authority falseのclosed schemaを検証。
- `py_compile`: public target, row builder, train scripts, evaluator pass。
- `git diff --check`: pass（最終統合前に再実行する）。
- `scripts/docs/validate_docs.py`: canonical docs validationを最終編集後に再実行する。

