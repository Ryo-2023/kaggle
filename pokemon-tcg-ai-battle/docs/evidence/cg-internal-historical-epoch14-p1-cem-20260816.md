# cg internal historical epoch14 source intake／P1 CEM（2026-08-16）

## 結論

first-parent historical snapshot intakeで、現行の公開／生成poolとidentityが重複しない内部Git source 12件を新しい local-eval-only poolへ封印した。P1 packageを両seat各1局でbounded smokeし、24/24 `DONE`・fault0を確認した。その後、P1 policy＋root deck固定のMETA_TRAIN-only CEMを1世代実行したが、独立2 blockのrisk-aware gateを満たす候補はなく、`incumbent-center`を保持した。P1、root deck、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`は不変である。

## source intake

- generator: `scripts/discover_fresh_internal_meta_v1.py`
- intake mode: `--history-depth 16`、first-parent、checkout／import／network／Git mutationなし
- selected refs: `agent/nihei-cynthias-garchomp`、`agents/ozawa-rocket-rule`、`agents/ozawa-starmie`、`agents/nihei-festival-lead`
- accepted: 12件（Cynthias 3、Rocket 7、Starmie 2）
- rejected: 52件（既存source commit／artifact identity、batch重複など）
- usage boundary: `local_eval_only`
- pool: `runs/cg-internal-historical-epoch14-depth16-20260816/`
- pool manifest SHA: `590ee18351ec4e4dc2fabb4a3d17857ecf9089f86ef70a1146dffe30e97c9525`
- fresh meta SHA: `7b708d3dab394947d77abb0e68de416333b0c3958249df89e2adb94b1f11d610`

sourceは内部Git履歴のsnapshotであり、Kaggle公開／native性能の証拠ではない。公開source名や提出権限を付与していない。

## split／runtime smoke

`scripts/build_historical_meta_split_v1.py`で、source lineageを意識して次のsplitを封印した。

- `META_TRAIN`: Cynthias 3件＋Rocket 5件
- `META_DEV`: Rocket 2件
- `META_FINAL`: Starmie 2件

split SHAは `bf24b182f8ef85af3faea9dd9202144bdc19fa97cc5962e9b2cab45c19868ec9`。P1 package `runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1/` を subject とし、`scripts/run_historical_meta_smoke_v1.py` を workers 2、両seat各1局で実行した。

- requested／completed: 24／24
- status: 24 `DONE`
- faults／draws: 0／0
- W／L: 5／19（20.8333%）
- smoke summary SHA: `48ad4aa460c6ad2bfc45a7ccd1a325c0526a1bcf36cd5b81110f3b8ede7806b3`

この勝率は1局／seatのruntime smokeであり、性能比較やCEM選抜の根拠にはしない。

## P1固定 CEM

`scripts/run_cg_p1_cem_v1.py` を、P1 source／control package、root deck固定、`META_TRAIN_ALL`、population／elite `8／2`、1世代、独立再評価2 block（各opponent／seat 1局）で実行した。ResourceGovernorは一時的な自分の再帰identity scan終了後に正常 admissionし、CABT実行中の追加faultはなかった。

- screen: 288／288 `DONE`・fault0（candidate 8件＋control）
- independent re-evaluation: 96／96 `DONE`・fault0
- screen上位 `cg-p1-cem-g00-c07-83ed0b234507`: control比 `+15.625pt`
- c07 independent delta: `+6.25pt / −18.75pt`、mean `−6.25pt`、minimum `−18.75pt`
- c07: seat-safe／opponent-seat-safe不成立
- もう1 elite c01: independent `−18.75pt / −18.75pt`
- selection: `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`
- new center: P1 center（`incumbent-center`×2）

campaign manifest SHAは `151343beb6450702f3311a683f81cde1d4dca88b9840d3c56974663f1819b9b5`、generation manifest SHAは `094fedc0637a7525ff4643c1cad652707cfae255ac03844e4d8abc6dd5ab0498`、results SHAは `b3680e0b28ed223b869413b0356e56986442cfa901e7d9a878075967cda2677c`。DEV／FINALはCEM選抜中に読んでいない。

## 判定と次の再開条件

判定は `SOURCE_GENERATION_PASS / BOUNDED_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。このpool、split、seed、candidateは性能使用済みとしてblind retryしない。次は別policy lineageまたは新しいruntime-safe source recipeを新epochで生成し、`legality → static safety → bounded fault0 → TRAIN-only → independent positive → seat-safe/opponent-seat-safe → unused DEV → unused FINAL`を通過した候補だけをBestKnown loopへ渡す。

実行コマンド、全artifact、authorityは各run rootのmanifestを正とする。commit、push、Kaggle提出は行っていない。
