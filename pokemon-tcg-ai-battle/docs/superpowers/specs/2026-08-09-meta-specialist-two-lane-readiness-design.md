# Meta Specialist 2レーン長時間学習 readiness 設計

## 結論

最初の長時間学習対象を Alakazam と Archaludon に限定し、正しい表現・独立split・sealed θ0・実trajectory learner を一つの縦経路として完成させる。短時間の3-seed pilotが事前固定した性能・健全性条件をすべて満たした場合だけ、同じ構成で長時間学習を開始する。

## 成功条件

- R2/R3の比較が同じlegal-action target、connected-component split、学習予算、3 seedで行われる。
- episodeおよび非ubiquitous near-duplicateのtrain/validation overlapが0である。
- AlakazamとArchaludonについて、独立validationで選んだrecurrent BC checkpointと、そのsource/data/split/teacher/model/critic hashを持つsealed θ0が存在する。
- 実collectorが保存した同じtrajectory schemaをPPO、V-trace、AWR/CRRが読み、各learnerが実optimizer update、checkpoint保存、reload、評価まで完走する。
- 2 lane × 3 training seed pilotで、候補と同seed θ0を、seat・opponent familyで層化した独立armとして比較できる。
- pilotのprimary macro勝率差が点推定+5ポイント以上、片側95%下限+2ポイント超である。
- 6 lane×seedセル中5セル以上が正で、各laneの3-seed平均が非負である。
- fault率が絶対2%以下かつθ0比+2ポイント以内、`dead_rho <= 0.05`、`dlogp >= -1.0`、20-step median trace productが0.02以上である。

## 反証・停止条件

- split leakage、hidden-information混入、source/hash/manifest不一致を1件でも検出したら停止する。
- learnerがsynthetic featureやsynthetic outcomeでしか更新できない場合は性能pilotへ進まない。
- 2 seed終了時に同一laneの両seedが非改善、またはpooled片側90%上限が+2ポイント以下なら、そのlaneの3 seed目と長時間学習を停止する。
- 3 seed後に成功条件を満たさない場合は、追加roundやcheckpoint選択で後付け最適化せず、長時間化根拠なしと判定する。

## 評価設計

CABT native engineはseed固定能力を公開しておらず、同一canonical identityでもfresh processのtrajectoryが一致しない。そのためexact paired outcomeを性能推論に使わない。

評価はcandidateと対応するθ0を独立armとして実行し、lane、training seed、seat、opponent family、repetitionのblockを事前固定する。各arm内でランダム化し、stratumごとの成績を固定重みでmacro集計する。faultを除外せずcandidate lossとして集計する。paired promotion APIはengine replay capabilityが成立するまでfail-closedを維持する。

pilotの目安は各lane×seed×policyについてheld-out 6 opponent × 2 seat × 8 repetitionの96 attempted games、全体1,152 attempted gamesとする。正確な件数は実行前manifestに固定し、結果を見て変更しない。

## 実装境界

### 表現とsplit

- `PublicEntityLocatorV3`は`(owner_role, semantic_zone, zone_ordinal)`で表す。`zone_ordinal`はActorInformationViewに見えるzone内順序から作る一時的なpublic alignment keyで、card serialではなくmodel inputへ数値特徴として入れない。
- 旧v1 recordにはlocatorがないため、v1→v3変換ではowner、zone、完全なpublic Pokemon snapshotが一意に一致する場合だけcanonical ordinalを再構成する。完全一致候補が複数あるrecordは推測せず`ambiguous_public_locator`としてGate 1入力から除外し、件数をmanifestへ保存する。
- R3-Aはown active、own bench、opponent active、opponent bench、other publicを別poolにする。
- R3-Bは192次元、4 head、2 pre-norm block、FFN 512、dropout 0.05とし、same-owner、same-host、active、source/target、public-evolution relationを持つ。
- stable action IDはalignment/provenance専用とし、semantic embeddingへ入れない。
- multi-selectionはcanonical set order、selected mask、duplicate exclusion、order-sensitive stepを表現する。
- splitはepisode edgeとnear-duplicate edgeのconnected component単位にする。
- split成果物は`split_manifest_v3.json`とし、schema、source dataset hash、ubiquitous key判定、`record_id -> component_id -> partition`、partition counts、episode overlap、near-duplicate overlap、manifest hashを保存する。BC、critic、learnerはこのmanifestを必須入力とし、独自再splitを禁止する。

### 教師、BC、critic、θ0

- teacher quality weightはcurrent-pool結果、fault、confidence、agreement、search/strength evidenceから導出し、入力済み既定値1.0を信用しない。
- recurrent BCはepisode順序を維持し、境界だけでhidden stateをresetし、paddingとburn-inを扱う。
- criticは実episodeのeventual outcomeを使い、overall、seat、opponent family、trajectory position別にuniform baselineと比較する。
- game seedはproduction conditioningへ入れない。低頻度categoryはunknownへ落とす。
- θ0はatomic checkpointと限定列挙したsource/config/data/split/teacher/model/critic manifestからsealする。
- source manifestは当該θ0へ到達する実行でimportまたはCLI指定された相対pathだけをallowlist化し、各content SHA-256を保存する。`__pycache__`、`.pyc`、`runs/`、一時file、無関係なrepository fileは拒否する。未追跡sourceもallowlistに含まれる限りhash対象とする。

### learner

- collectorはordered decision、legal action IDs/masks、base behavior logits/log-probabilities、chosen action/log-probability、reward/outcome、episode boundary、actor version、opponent/deck/seat/seed/fault provenanceを保存する。
- PPO、V-trace、AWR/CRRは同じsequence batchとmodel/critic schemaを使う。
- correctness gateでは3 learnerすべての実parameter updateを要求する。性能pilotに投入するlearnerと選択規則はpilot manifestで事前固定する。

### pilotと長時間runner

- 評価recordはlane ID、training seed、policy role、candidate/θ0 artifact hash、seat、opponent family、repetition、outcome/fault、canonical game identityを持つ。`(lane,seed,role,opponent,seat,repetition)`は一意で、candidateが参照するθ0 hashは同lane/seedのθ0 arm artifact hashと一致しなければならない。
- 各lane×seed×policyの6 opponent×2 seat×8 repetitionはattempt ledger上で完全でなければならない。fault/incompleteは欠損扱いにせずlossとして残し、attempt自体の欠落はfail closedにする。
- bootstrap seedは`20260809`、replicatesは`20_000`に固定する。lane×seedを等重み、seat×opponent familyを各cell内で等重みにし、primaryはmacro deltaの片側95%下限、futilityは完了済み2 seedのpooled片側90%上限とする。
- 長時間runnerはpilotで選択・sealされたlearner manifestだけを受け付ける。既定budgetは各lane 100,000 completed training decisions、checkpoint間隔10,000 decision、health評価間隔5,000 decisionとし、いずれもrun manifestへ開始前に固定する。
- `--dry-run`は入力hash、GPU、disk、opponent disjointness、progress artifact pathだけを検証する。実行時は`run_status.json`へPID、start time、completed decisions、latest checkpoint、health、stop reasonをatomic更新し、resume時は同一manifest hashを要求する。

## 作業方法

- 現在のfeature branch上で作業し、新しいworktreeは作らない。
- 既存dirty差分を保全し、Taskごとに担当ファイルを分離する。
- production変更は必ずRED→GREENのTDDで行う。
- 実装者とreviewerを分離し、主担当が差分とテスト結果を確認する。
- commit、push、Kaggle提出、checkpoint promotionは行わない。
