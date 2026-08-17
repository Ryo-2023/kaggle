# Native-preserving meta-overfit alternating bridge v1（2026-08-13）

## 結論

Task 3 の alternating state bridge を research-only で実装した。既存 `alternating_meta_optimizer_v1` の `CandidateStateV1`、固定タイムスケール更新、successive-halving、native baseline保持を再利用し、新規bridgeではTask 2 iteration manifestのstrict verifier/source再hash、public-advantage/protocol identity、candidate/native per-game WDL summary、seat/fault/strata/seed/game-id gate、opaque regression journalによるnative regression stop-after-two、checkpoint SHA-bound rollback descriptorを追加検証する。

このTaskは状態と証跡の契約を作っただけであり、CABT、training、promotion、submission、longrun、commit、pushは起動・実行していない。全authorityはfalseである。

## 変更範囲

- `src/mage_ptcg/meta_specialist/native_meta_overfit_alternating_v1.py`
  - Task 2 `verify_native_meta_overfit_iteration_v1`を必ず呼び、manifest sourcesのrepo-root containment/file SHAを再hashする。bound public tableもcanonical reload、table semantic SHA、native baseline policy SHAを再検証する。
  - 既存 `CandidateStateV1` を使って `POLICY_FIXED_SHORT` / `DECK_FIXED_LONG` を構築・遷移する。固定側のSHAを変更する不正遷移は既存optimizerへ委譲してfail-closedにする。
  - exact stage `(96, 384, 768, 1536)`のみを許可し、candidate/native両方の同stage summaryを要求する。
  - candidate/native pair ID、policy/deck/evaluator SHA、protocol SHA、per-game WDL、common24 game-id/seed/strata universe、fault count、両seatゲーム数・seat gapを検証し、aggregateをrecordsから再導出する。native control ledgerのartifact SHAと、native score・protocol・seed/strataを束ねたcontrol block SHAも再導出し、fault非ゼロまたはseat gap超過は昇格不可。
  - `NativeRegressionJournalV1`がstate SHA・summary SHA・decision SHAと連続回数を保持し、authorityとscalarをimmutableに閉じ、content sealを再導出して低レベル改変も拒否し、2回連続regressionでstate advancement/promotionを停止しrollback requiredを返す。
  - private journal `_bind`は直前countとsummaryのcandidate/native scoreから次countを再導出し、decisionのcount/stop/rollback flagと一致しない任意resetを拒否する。公開`bind` authority surfaceは存在しない。
  - `_bind`はsummaryのcandidate ID、stage、native/candidate identity、control fieldsとdecisionのcandidate/stage/score/protocol/summary SHAをstateへ再束縛し、`rebind_state`はrevision・stage・phase固定次元・manifest/schedule/native baselineの連続性を検証する。
  - state advancementは評価summaryの有無にかかわらずbound journalを必須化し、非評価遷移でもjournalのstate SHAを次stateへ再bindする。successive-halvingは全candidate IDを覆うjournal map以外を拒否するため、journalなしの2連続regression経路を許可しない。
  - rollback descriptorは`consecutive_native_regressions >= 2`、reason=`two native regressions`、authority falseを必須化し、object verify時にもauthorityを再構築・再検証する。summaryのgame recordsとauthorityはdeep immutableで、low-level mutationもevaluation入口のcanonical再構築で拒否する。
  - deck mutation candidateは既存`DeckMutationCandidateV1`を受け、exact deck multiset SHAとstate deck SHA、mutation authority falseを要求する。
- `tests/meta_specialist/test_native_meta_overfit_alternating_v1.py`
  - phase invariant、exact stage、candidate/native pair、seat/fault、successive-halving、native stop-after-two、missing journal fail-closed、protocol/iteration SHA、native control artifact/block SHA、PROVEN baseline、journal roundtrip、gate promotion防止、rollback checkpoint SHA、deck mutation identity、authorityを検証する。

## Task 2 fixed manifest binding

bridgeはTask 2 iteration manifestについて次を要求する。

| binding | 条件 |
| --- | --- |
| schema/purpose | `meta-specialist-native-meta-overfit-iteration-v1` / research-only purpose |
| semantic | canonical payloadから`iteration_sha256`を再計算して一致 |
| gates | curriculum/outcome/table/native/`META_TRAIN`/heldout/authority gatesはtrue |
| disabled gates | package/evaluator/performance gateはfalse、`ready_for_evaluation=false` |
| native | pair ID、policy/deck/evaluator SHA、authority false、research-only |
| outcome closure | protocol SHA、execution-closure SHA |
| public table | table semantic SHAとfile SHA |

strict verifierが再導出したmanifestとbridge入力を比較し、`sources=[]`、tampered source、任意のtable JSON、permission/authority変更だけをsemantic SHA再計算で偽装する経路を拒否する。

stateの`meta_manifest_sha256`はTask 2 iteration manifest file SHA、`meta_schedule_sha256`はschedule file SHAへ固定される。manifestまたはscheduleの改変は次のbridge API呼び出し時に拒否される。

## State / evaluation contract

既存optimizerの責務を複製せず、bridgeは次を提供する。

```python
state = build_native_meta_overfit_state_v1(
    iteration_manifest_path=...,
    meta_schedule_path=..., candidate_id=...,
    deck_sha256=..., policy_config_sha256=...,
    native_baseline=NativeBaselineArmV1(...),
    phase="POLICY_FIXED_SHORT",
)

decision = evaluate_native_meta_overfit_stage_v1(
    EvaluationSummaryV1(...), state,
    iteration_manifest_path=...,
)
next_states = promote_native_meta_overfit_successive_halving_v1(
    states, {candidate_id: summary},
    iteration_manifest_path=..., next_stage_games=384,
)
rollback = build_rollback_descriptor_v1(
    state=state, checkpoint_path=..., iteration_manifest_path=...,
    reason="two native regressions", consecutive_native_regressions=2,
)
verify_rollback_descriptor_v1(rollback, state=state, checkpoint_path=..., iteration_manifest_path=...)
```

`EvaluationSummaryV1`はcandidate/native policy/deck/evaluator SHA、stage games、protocol SHA、candidate/nativeのper-game `game_id`・seed・opponent/family・seat・WDL・fault recordを保持する。native control artifact SHAはnative ledgerとbaseline/evaluator/protocol/seed/strataを再現し、control block SHAはartifactとnative scoreを再現する。両armのgame-id/seed/strata universeは一致し、aggregate WDL、seat score、fault count、seat gamesはrecordsから再導出される。両armは同じstage・native pair・protocolでなければならない。

promotionは既存`promote_successive_halving_v1`へ委譲し、score降順・candidate ID tie-break・上位半数・次のstageだけを許可する。bridge自身は実行callback、trainer、CABT、submission、longrunを持たない。

successive-halving前には全candidateのnative control block identity（native pair、policy/deck/evaluator、artifact、score、protocol、game-id/seed/strata SHA）を完全一致させる。candidateごとに別native score・別ledger・別seed/strataを混ぜたpromotionはfail-closedになる。

## Native regression / rollback

candidate scoreがnative score未満のsummaryでは`NativeRegressionJournalV1`の連続regression countを1増やす。改善または同等でcountは0へ戻る。2回連続で `stop_after_two=true` と `rollback_required=true` を返し、journalを介したstate advancement/promotionは拒否する。rollback descriptorはstate SHAとcheckpoint file SHAを再ハッシュし、checkpoint bytesが変わればfail-closedになる。

`advance_native_meta_overfit_state_v1`は`regression_journal=None`を常に拒否する。評価summaryを伴わないphase遷移もjournalを要求し、成功した新stateのSHAだけを再bindして連続regression count・直前summary/decision lineageを保持する。`promote_native_meta_overfit_successive_halving_v1`は候補集合と同じキーを持つjournal mapを要求し、欠落・余分な候補またはjournalを拒否する。これによりjournalなしで96→384→768へ進む経路を閉じる。

control用途の`NativeBaselineArmV1.status`は`PROVEN`以外を拒否する。`NativeRegressionJournalV1.from_dict`とcontent SHAにより、プロセス再開時もcandidate/state lineageを再検証できる。

## TDD / 検証

RED:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_meta_overfit_alternating_v1.py
ModuleNotFoundError: No module named 'mage_ptcg.meta_specialist.native_meta_overfit_alternating_v1'
exit=2
```

独立review反例のRED（既存16件は通過し、journal欠落2件だけが失敗）:

```text
test_advance_rejects_missing_regression_journal: DID NOT RAISE
test_successive_halving_rejects_missing_regression_journals: DID NOT RAISE
2 failed, 16 passed in 6.39s
```

独立review追加反例のRED（候補Bへ異なるnative score/control ledgerを与えるとpromotionが受理され、forged control block SHAも未検出）:

```text
test_successive_halving_rejects_native_control_score_or_artifact_mismatch: DID NOT RAISE
test_summary_rejects_forged_native_control_block_sha: unknown field
test_unproven_native_baseline_is_rejected_before_state_binding: DID NOT RAISE
test_regression_journal_roundtrip_preserves_candidate_and_lineage: AttributeError
```

focused GREEN:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_meta_overfit_alternating_v1.py
32 passed in 6.41s
exit=0
```

joint regression:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_native_meta_overfit_alternating_v1.py \
  tests/meta_specialist/test_alternating_meta_optimizer_v1.py \
  tests/meta_specialist/test_deck_mutation_v1.py \
  tests/meta_specialist/test_joint_optimization_v1.py
58 passed in 7.61s
exit=0
```

静的検証:

```text
PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/native_meta_overfit_alternating_v1.py
exit=0

python scripts/docs/validate_docs.py
Validated 13 canonical documents.
exit=0

git diff --check -- Task 3 module/tests/evidence
exit=0
```

## SHA / remaining boundary

```text
src/mage_ptcg/meta_specialist/native_meta_overfit_alternating_v1.py
24515c230c48c894994ae5cc9de608d248079acd7dae76fc829e4d9de638daf1

tests/meta_specialist/test_native_meta_overfit_alternating_v1.py
387167f9c63f697cbe1f4a83c12d2fc080f2245ea67294319f5067e8c31152b0
```

このevidenceのSHAは、本文確定後に`sha256sum`で取得する。Task 3は性能screen、native BestKnown超越、deck/policy学習、longrun readinessを主張しない。次のTaskでは、実Task 2 iteration manifestと実common24 evaluation summaryをこのbridge APIへ接続し、96→384→768→1536 gateを別artifactとして記録する。
