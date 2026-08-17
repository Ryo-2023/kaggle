# R3 Recurrent θ0 Gate 設計

## 結論

長時間学習用の θ0 は、temporal hidden state を持たない `current-R2` ではなく、既存の `SpecialistModelV3` を対象にした独立の recurrent Gate を通したモデルだけから作る。静的 Gate 1 の `current-R2` 保持は安全な既存baselineを意味するだけであり、R3 の昇格や recurrent θ0 の承認を意味しない。

## 目的

Alakazam と Archaludon の sealed teacher data から、episode順序・hidden carry・episode boundary reset・padding・burn-in・canonical semantic/STOP soft target を同時に検証する R3 θ0 candidate を作る。recurrent validation が baseline と比べて健全であることを確認できた場合だけ、teacher quality manifest と source/data/split/model hash を結んだ atomic θ0 checkpoint を作る。

## 非目標

- `SpecialistPolicyModelV1` へ GRU や隠れ状態を後付けしない。
- 静的 Gate 1 の R3-A/R3-B 結果を recurrent θ0 の採否へ流用しない。
- 本Gateだけで長時間学習開始、Kaggle提出、checkpoint promotionをしない。
- synthetic teacher record、synthetic outcome、unattested selectionを性能根拠にしない。

## アーキテクチャ

`SpecialistModelV3` の既存 `forward_v3(..., hidden_state, episode_start)` を唯一の recurrent model contract とする。teacher record は Gate input と同じ snapshot、trusted permission、raw line/content hash、connected-component split を再検証して materialize し、record内の canonical semantic loss rows を episode内順序で束ねる。各 sequence は最初の step だけ `episode_start=true`、その後は前stepの hidden state を受け継ぐ。padding step は loss と hidden update の両方から除外し、burn-in 区間は hidden warmup のみを行って loss 集計から除外する。

R3-A と R3-B は同じ sequence、split、soft target、training seed、max update budget で比較する。static R2 は recurrent candidate として比較しない。各R3 checkpointは同一validation sequenceで hidden carry と stepごとのreset-only ablationを評価する。採用候補は各laneの3-seed平均で、carry complete-action NLL と carry STOP NLL のどちらも reset-onlyより絶対 `0.02` を超えて悪化せず、かつ少なくとも一方のlaneでいずれかのNLLを `0.01` 以上改善しなければならない。候補間はlane等重みのcomplete-action NLLを最小化し、R3-BはR3-Aより `0.01` 以上低いcomplete-action NLL、または `0.02` 以上低いSTOP NLLを示す場合だけ選ぶ。それ以外は小さいR3-Aを選ぶ。少なくとも1候補が actual optimizer update と non-degenerate hidden carry を示すことも必要条件とする。両R3が不合格なら recurrent θ0 は作らず、長時間学習を停止する。

## Gate 入力とデータ境界

- Task 3 の `gate1-selection-v3-cuda-0.json` とその out-of-band file SHA/result SHA を入口にする。`active_representation=current-R2` は R3承認ではないため、R3 recurrent Gate はこのbaseline artifactを比較根拠にせず、同じ sealed lane inputs の data authority だけを使用する。
- 各laneの input manifest は runtimeで snapshot chunks のphysical SHA、examples total、trusted permission bytes、selected raw lines、record/content hash、26/6 split、episode/near-duplicate overlap 0を再検証する。
- performance Gate用には32 record/laneの静的sliceを再利用しない。full teacher corpusから、episode/component splitを新たにsealedした recurrent training/validation selectionを作る。full record dictを常駐させず、資格確認済みの grouping metadata と physical pin はdisk-backed spoolへ置き、整数union-findとcomponent hash順のdeterministic greedy partitionをbounded memoryで実行する。selection本体は単一巨大JSON配列にせず、streamable index fileと小さなroot manifestに分け、両方のbytes SHAをroot manifestへpinする。実train/evalもindexを再検証しながらepisode単位のbounded batchへstreamし、全 `BCExampleV3` / model state / sequence をepoch全体に常駐させない。同一episodeの物理順再登場はsequenceを分断せずfail closedにする。splitやteacher weightが未確定なら fail closedにする。

## Teacher quality

各recordのweightは既存の入力済み `quality_weight` を信用せず、current-pool result、fault、policy/deck/version provenance、confidence/agreement/search strengthから再導出する。これらの一次証拠がないrecordはθ0 training inputから除外し、除外理由・件数・source hashをteacher manifestへ残す。

## Recurrent Gate の受入条件

1. sequence materializer は同一episode内の順序、boundary reset、component partition、padding mask、burn-in maskを保存し、train/validationのepisode/near-duplicate overlapは0である。
2. R3-A/R3-B は完全な legal semantic+STOP domain と canonical soft massを使用し、forced sole STOPはlossへ入れない。
3. validationに learned STOP targetを持ち、STOP NLLとcomplete-action NLLを候補・seedごとに保存する。order-sensitive非empty prefixが教師dataで不在なら、実数0と探索範囲をmanifestへ記録し、order behaviorはunit/integration fixtureで別証明する。
4. 各candidate×lane×seedは実optimizer update、parameter delta、hidden carryがreset-onlyでないこと、early-stop history、best checkpointを保存する。
5. R3選択は上記の事前固定 non-inferiority threshold（per-lane carry対reset-only +0.02、少なくとも1laneで -0.01改善、R3-B採用差 -0.01/-0.02）とlane均等 macro ruleに従う。metric欠落、seed/cell欠落、CUDA証拠欠落、teacher evidence不足は選択不能とする。
6. θ0 は best validation checkpointをatomic `.pt`として保存し、新process reload後にtensor hash一致を確認する。θ0 manifest はsource/data/split/teacher/model/config/command/seed/metric/result artifact SHAを閉じる。

## 停止条件

- R3 recurrent Gateで両候補とも両laneの事前固定NLL/STOP条件を満たさない。
- hidden carry、split isolation、teacher authority、checkpoint reloadのいずれかが破れる。
- full recurrent corpusのquality evidenceを再導出できない。

これらの場合、R2 baselineをrecurrent θ0として偽装せず、長時間学習開始を保留する。

## 検証

- RED/Green unit tests: sequence ordering/reset/padding/burn-in、full target domain、hidden carry、STOP metric、threshold/manifest tamper。
- bounded end-to-end: two lanes × two R3 candidates × three seeds、same sealed split and budget、CPU/CUDA metrics。
- θ0 seal: atomic checkpoint write、new-process reload、expected file/result SHA anchor、manifest strict reader。
