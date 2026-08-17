# Meta-specialist performance-first sprint（2026-08-10）

## 結論

長時間学習の起点はまだ確定していない。現行runtimeで動く v2smoke R2 を暫定baselineとして保持し、
旧R2重みの意味付きtransferは BC初期値候補、recurrent v4は本命候補として短期学習を進める。
「学習を開始したこと」ではなく、独立validation改善とCABT非悪化を満たした経路だけを拡大する。

## 実行可能checkpointの再確認

historical `cf5c…` と旧 t1-R2 checkpoint は payload自体は読めるが、現行runtime modelへは
`pokemon_count_encoder` / `opponent_value_embedding` 欠落と encoder shape差でloadできない。
現行runtimeでloadできる暫定baselineは次の v2smoke finalである。

- Alakazam: `checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt`
- Archaludon: `checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt`

両laneとも actual collector 2局（両seat）をfault 0で完走した。Alakazamの2 held-out opponent ×
両seatの4局screenは1勝3敗、Archaludonも1勝3敗だった。これは接続確認であり、強さの証拠ではない。

## R2 legacy transfer

旧 `cf5c…` の35 exact-shape tensorをコピーし、Pokémon/endpoint encoderは意味列でmap、意味が変化した
scalarとv2新規branchは再初期化した。公式v2 checkpointとして発行し、runtime loader、inference loader、
BC bootstrap loaderをstrictに通過した。

- checkpoint: `runs/meta-specialist-transfer-v2/alakazam/checkpoint-ad29d4f72ccd8cea5187bb8e8e88366ced0c22e740a182f02fe5a1f0eeb11338.pt`
- Gate1 train/validation complete-action NLL: transfer `0.755/0.770`、v2smoke `7.804/3.496`
- 同一seed 4局 CABT: 両方2勝2敗、fault 0
- 同一seed 24局 CABT: transfer 8勝16敗（0.333）、v2smoke 9勝15敗（0.375）、双方fault 0

従ってtransferは小標本validationで有望なBC初期値だが、実勝率優位は未確認である。暫定baselineを
置換せず、fine-tune比較の候補として残す。

## current-R2 online update pilot

各lane2局のfresh rolloutからV-traceを1 updateした。更新とcheckpoint再loadは成功したが、4局screenは
両laneとも更新前後で1勝3敗のままだった。Alakazamの平均valueはreturn 0に対して−0.728、
Archaludonはreturn −1に対して+1.403で、criticは明確に未較正だった。blindなRL継続は行わない。

quick-screen orchestrationは同率時にchallengerを選んでいたため、既定の必要改善幅を正値へ変更し、
評価faultを通常敗戦ではなく比較無効として拒否する回帰testを追加した（11 tests pass）。

## recurrent v4で判明した性能バグ

短期pilot前の独立レビューと実データ確認で、次を検出した。

1. 1つのcomplete actionに属するsemantic prefix各行をGRUの別time stepとして流していた。
   正しくは1 physical record（1実行行動）につきhiddenを1回だけ進める。
2. sequence lossをsumして1 updateしており、長いepisodeとprefix行数を過重化していた。
   record単位の平均objectiveへ直す。
3. `reach_mass`を落としてprefix-row CEを等重みにしていた。canonical record lossは
   `sum(reach_mass * conditional_prefix_CE)` で、その後record単位に平均する。実sealed recordには
   reach `[1.0, 1.0]` があり、record内reachの和を1へ正規化してはならない。
4. 初期32-record設定は実episode（約59–71 records）より小さく、complete episodeを1つも収容できず
   全indexを走査していた。episode/component coverageを持つbounded subsetへ変更する。
5. best validationを同じpositive signalへ再利用するとselection biasになる。最初の判断は固定1 epochの
   initial→after比較に限定し、複数epochのbest値だけでpositive判定しない。

これらを修正し、各partitionを物理順から層化して4 complete episode / 4 componentずつ選ぶ
bounded readerを実装した。V4 checkpointを既存runtimeへ接続するadapterとactor-pool bindingも実装し、
fixture checkpointおよび実学習checkpointでCABTをfaultなく完走した。

## corrected V4 1-epoch CPU pilot

固定1 epochのためbest-of-validation選択はなく、学習に使っていないvalidation componentの
initial→afterを2 seedで比較した。

| lane | train / validation records | seed | initial NLL | after NLL | delta |
|---|---:|---:|---:|---:|---:|
| Alakazam | 343 / 349 | 0 | 1.6564 | 1.5766 | −0.0798 |
| Alakazam | 343 / 349 | 1 | 1.7128 | 1.6480 | −0.0648 |
| Archaludon | 217 / 285 | 0 | 1.2416 | 1.2116 | −0.0300 |
| Archaludon | 217 / 285 | 1 | 1.2561 | 1.1998 | −0.0563 |

全4 cellでcanonical complete-action NLLが改善し、両laneとも`SHORT_PILOT_POSITIVE`となった。
Alakazam sliceはSTOP available行を含むがpositive STOP teacher targetは0、Archaludon validationは
positive STOP target 6行を含み、conditional STOP NLLはseed 0=`1.191`、seed 1=`0.818`だった。

- `runs/meta-specialist-v4-bc-pilot/alakazam-epoch1.json`
- `runs/meta-specialist-v4-bc-pilot/archaludon-epoch1.json`

seed 0 checkpointをRule v0との両seat CABTへ接続した4 gameは、すべて実行完了・fault 0だったが
4敗だった。小型hidden 16・各lane 8 episode・1 epochは学習可能性の確認であり、強いmodelではない。
次はhidden 128、各partition 32 episode、2 seedのGPU中規模pilotを行い、独立held-out poolで
v2smoke baselineと比較する。

## 次の判断条件

1. Alakazam / Archaludon 各laneでcomplete episodeと複数validation componentを含む固定1 epoch、2 seedを実行。
2. canonical complete-action validation NLLが両seedで改善し、STOP/ordered-prefixを含むcoverageを記録。
3. 改善checkpointをruntime adapterでCABT評価し、v2smokeに対してfault 0かつ非悪化を確認。
4. 条件を満たしたlane/modelだけをGPUの中規模pilotへ拡大し、その後に長時間学習を開始する。

commit、push、Kaggle提出はいずれも実施していない。

## GPU medium BC と固定pool実測（2026-08-10）

RTX PRO 5000 Blackwell上で hidden 128 / embedding 64、各partition 32 complete episode / 32 component、
positive STOP target必須、2 seed、3 epochを実行した。実行前にcanonical record loss、record単位recurrence、
GPU device配線を修正し、学習後のCABT接続では非公開Prize選択の投影不一致とlarge multi-selectのtimeoutを
修正した。旧checkpointはsource closure変更後にstrict loaderが拒否するため再利用せず、次waveは新sourceで
新checkpointを発行する。

| lane | records train / validation | seed | initial NLL | best NLL | delta | best epoch |
|---|---:|---:|---:|---:|---:|---:|
| Alakazam | 2,790 / 2,679 | 0 | 1.6841 | 0.7068 | -0.9774 | 2 |
| Alakazam | 2,790 / 2,679 | 1 | 1.5942 | 0.7280 | -0.8662 | 2 |
| Archaludon | 1,731 / 1,585 | 0 | 1.2948 | 0.9773 | -0.3174 | 2 |
| Archaludon | 1,731 / 1,585 | 1 | 1.2728 | 0.9737 | -0.2991 | 2 |

Alakazamの正しいsubject deckは`opponents/nihei_alakazam/deck.csv`である。初回campaignが指定した別の
materialized deckによる評価はモデル比較へ使わない。固定6相手、両seat、各seat 8局の96局確認では、
Alakazam V4 seed 0はruntime修正前後のrunでV2を安定して上回らず、現budgetの長時間化対象から外した。

Archaludon seed 1は96局をfault 0で完走し、V4 37勝59敗（0.385）に対してv2smoke baselineは
24勝72敗（0.250）だった。seat別もV4 0.354 / 0.417、V2 0.292 / 0.208で両seatが正方向だった。
matchup別は6相手中4相手で改善、1相手で同率、1相手で悪化した。CABT engine seedはattestできず
厳密paired比較ではないが、Archaludonを次のdata/update budget拡大対象とするには十分な傾向である。

- V4: `runs/meta-specialist-strength/v4-confirm-archaludon-gpu-seed1-seed9500000-96.json`
- V2: `runs/meta-specialist-strength/v2-confirm-archaludon-v2smoke-seed9500000-96.json`
- medium training: `runs/meta-specialist-v4-gpu-campaign/{alakazam,archaludon}-training.json`

training throughputは同一physical recordのdecoder prefixごとにstate encode / GRUを再計算していたため、
record-group APIで1回へ統合した。logits、hidden、record loss、全parameter gradientの同値をtestし、
CPU microbenchmarkは4.2683 ms/recordから2.4901 ms/record（1.714倍）へ改善した。さらにepochごとの
train sequence順をseed固定でshuffleし、validation順、objective、update数は維持した。

現在の次waveはArchaludon限定で各partition 64 episode / 64 component、2 seed、4 epoch（最大256
optimizer update/seed）を新source closureで実行する。新checkpointが96局でV2を上回りfault 0なら、
128 episode級・約1,000 updateの長時間runを開始可能と判断する。上回らなければ、容量拡大より先に
same-topology static BC warmupとsampling/update budgetを再検討する。
