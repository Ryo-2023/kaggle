# Autonomous Native-Preserving Meta-Overfit Iteration v1（2026-08-13）

## 結論

Task 2 の strict hard-negative iteration adapter を実装し、dynamic curriculum、common24 の META_TRAIN outcome adapter、Task 1 public-state advantage table、native baseline identity を一つの再現可能な research-only manifestへ束ねられる状態にした。出力は `META_TRAIN` の outcome だけを admissionし、`META_DEV` / `META_FINAL` は exposure・quota・weight を常にゼロにする。source file SHA、semantic SHA、protocol / execution-closure SHA、native policy/deck identity、seedを再計算してからatomic no-clobber claimし、reload時に同じ入力から完全再生成できないmanifestはfail-closedになる。

このTaskでは性能評価、CABT、training、submission、longrunは起動していない。`ready_for_evaluation` は package closure・evaluator closure・performance gateを未充足として常に falseであり、artifact生成を評価結果や提出可否へ昇格させない。

## 実装範囲

- `src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py`
  - 既存 `verify_dynamic_curriculum_manifest_v1` と `verify_common24_curriculum_outcome_adapter_v1` を必ず通し、authority exact-false、canonical JSON、重複key、finite値、repo-root containmentを確認する。
  - outcome adapterは execution closure の `protocol_sha256` と `execution_closure_sha256` を必須化し、recordsは `META_TRAIN` かつ一意な `game_id` のみ許可する。held-out opponentの混入も拒否する。
  - native baselineのpolicy/deck/evaluator identityをSHAで固定し、adapterにnative arm identityがある場合はbaselineと一致させる。optional legal deck candidateもmanifest/deck SHA、legal、research-only、authority falseを検証する。
  - opponentごとに loss hard-negative、seat imbalance、under-exposure、family diversity、reliabilityを deterministic に合成し、opponent cap・family floor/capをbounded projectionで適用する。faultとseat exposureは統計へ明示する。
  - iteration seed、curriculum/adapter/table/baseline/candidate source binding、weight semantic SHA、manifest semantic SHA、gate statusをcanonical JSONへ保持する。
  - outputは存在済みpathを上書きせず、temporary file + fsync + atomic no-clobber claim後にstrict reloadする。公開後の失敗cleanupは自分のraw bytesと一致する場合だけ許可する。
- `scripts/build_native_meta_overfit_iteration_v1.py`
  - 既定動作はDRY_RUNのmanifest materialization。`--execute` は常に拒否し、CABT、trainer、subprocess、longrun、submissionを起動できない。
- `tests/meta_specialist/test_native_meta_overfit_iteration_v1.py`
  - META_TRAIN-only admission、held-out reject、family floor/cap、fault/seat weighting、source SHA、deterministic seed、authority false、optional legal candidate、protocol SHA、native-arm binding、CLI dry-run、forged permission map拒否、claim-loss race保護を固定する。

## 入出力とgate

入力は、既存verified dynamic curriculum manifest、strict common24 outcome adapter、Task 1 public advantage table、research-only native baseline identity、任意のlegal deck candidate manifestである。出力manifestの主要契約は次の通り。

| 項目 | 契約 |
| --- | --- |
| admission | `META_TRAIN` recordsのみ。未知opponent、held-out record、duplicate game IDは拒否 |
| held-out | `META_DEV` / `META_FINAL` は統計へ zero exposure / zero weight / zero quota |
| weighting | bounded loss・seat・under-exposure・family diversity・reliability。合計1、opponent cap、family floor/cap |
| identity | curriculum / adapter / table / native policy・deck・evaluator / optional candidate deckをsource SHAで再束縛 |
| authority | training / promotion / submission / external execution は全て `false`、`research_only=true` |
| readiness | package / evaluator / performance gateが未充足なので `ready_for_evaluation=false` |

weight componentは `loss_hard_negative=0.40`、`seat_imbalance_correction=0.20`、`under_exposure_correction=0.15`、`family_diversity_floor=0.15`、`reliability=0.10` と固定した。これは学習済みpolicyやperformance claimではなく、次iterationのMETA_TRAIN opponent samplingへ渡す deterministic weighting artifactである。

## TDD / 検証結果

REDでは、実装前の focused collection が次で失敗した。

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_meta_overfit_iteration_v1.py
ModuleNotFoundError: No module named 'mage_ptcg.meta_specialist.native_meta_overfit_iteration_v1'
```

実装後のfocused suite:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_meta_overfit_iteration_v1.py
12 passed in 4.83s（初回実装時）
```

既存 verifierとの回帰確認:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_native_meta_overfit_iteration_v1.py \
  tests/meta_specialist/test_dynamic_meta_train_curriculum_v1.py \
  tests/meta_specialist/test_common24_curriculum_outcome_adapter_v1.py
28 passed in 14.60s（latest combined regression）
```

追加の静的・reload確認:

```text
PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py \
  scripts/build_native_meta_overfit_iteration_v1.py
exit=0

python scripts/docs/validate_docs.py
exit=0（canonical documents validated）

git diff --check -- \
  src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py \
  scripts/build_native_meta_overfit_iteration_v1.py \
  tests/meta_specialist/test_native_meta_overfit_iteration_v1.py \
  docs/evidence/autonomous-native-meta-overfit-iteration-v1-20260813.md
exit=0
```

focused testはbuild後のstrict `verify_native_meta_overfit_iteration_v1` reloadを含む。CLI fixtureではDRY_RUNのJSON summaryに `processes_launched=false`、`cabt_started=false`、`training_started=false`、`submission_started=false` を確認し、`--execute` はexit 2で拒否した。

## SHA / 再現性

最終変更後のprimary source SHAは完了reportへ同じコマンドの出力として記録する。

```bash
sha256sum \
  src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py \
  scripts/build_native_meta_overfit_iteration_v1.py \
  tests/meta_specialist/test_native_meta_overfit_iteration_v1.py \
  docs/evidence/autonomous-native-meta-overfit-iteration-v1-20260813.md
```

再現は、実在する verified inputを `scripts/build_native_meta_overfit_iteration_v1.py` の `--repo-root`、`--curriculum-manifest`、`--outcome-adapter-manifest`、`--public-advantage-table`、`--native-baseline-identity`、`--output-manifest` に渡して実行する。output pathが既に存在する場合は上書きせず失敗する。Task 2ではsynthetic fixtureを用いた契約検証のみであり、common24 96→384→768→1536、native BestKnown比較、deck-policy optimization、training、longrunの性能結果を主張しない。

## Context pack更新案と次段階

最新context packには、本evidenceを「Task 2 artifact」として追加し、次を明記する。

1. native baselineをcontrolとして identity-bound に保持したまま、META_TRAINだけをhard-negative weightingへ使える。
2. protocol / execution closure / source SHAが一致しないadapterは採用せず、held-out leakageは即時rejectする。
3. `ready_for_evaluation=false` は意図的であり、Task 3以降でpackage closure、evaluator closure、common24 gateを別artifactとして満たすまでCABTや学習へ自動接続しない。
4. Task 2は性能改善の証拠ではなく、BestKnownを起点とする次のmeta-overfit loopへ安全に入力を渡すためのmaterialization contractである。

commit、push、remote branch、Champion変更、Kaggle submissionは行っていない。既存dirty fileと既存performance artifactは変更していない。

## Independent re-review fix round（I-1 / I-2）

Task 2 の独立re-reviewで、iteration adapterがdynamic curriculum entryへ複製されたpermissionだけを検査し、source meta distributionの `usage_boundary`、`training_allowed`、`behavior_allowed`、`research_only`、authorityを再確認していない点（I-1）と、`exists → os.replace` の競合窓が既存destinationをclobberし得る点（I-2）が見つかった。両方をfail-closedで修正した。

I-1では、verified curriculumの `meta_distribution_manifest` sourceをSHA再検証後にstrict loadし、source manifestが `research_only=true` かつ training/promotion/submission authority falseであることを確認する。各 `META_TRAIN` entryについて、entryの `training_exposure_allowed=true` / `teacher_behavior_allowed=true` と、source rowの `usage_boundary ∈ {training_local, training_local_and_eval}`、`training_allowed=true`、`behavior_allowed=true`、`submission_allowed=false` を二重に要求する。検証済みsource permission mapはmodule-private `_VerifiedCurriculum` proof tokenとpermission digestへ束ね、`_derive_weighting`でもopaque objectとdigestを再検査するため、plain/構造偽造map、digest mutation、false permission、`local_eval_only`をweightへ流せない。held-out entryは従来どおりweight/quota/exposure zeroのみを許す。

I-2では `_atomic_write_new` から `os.replace` を除去した。payloadを一時ファイルへwrite+fsyncした後、同一directory内の `os.link(temp, destination)` をatomic no-clobber claimとして使い、destinationが先に存在すれば `FileExistsError` で終了する。builderはclaim-loss時に競合先をunlinkせず、公開後のverify失敗時も自分のraw bytesと一致する場合だけcleanupする。temporary unlink後は可能な環境でdirectory fsyncも行う。新規destinationでも `os.replace` が呼ばれないこと、既存destinationのbytesが不変であること、claim-loss raceの勝者bytesが保持されることをregression testで固定した。

fix round focused verification:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_native_meta_overfit_iteration_v1.py
20 passed in 6.19s

PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/native_meta_overfit_iteration_v1.py \
  scripts/build_native_meta_overfit_iteration_v1.py
exit=0

python scripts/docs/validate_docs.py
exit=0

git diff --check -- Task 2 module/tests/evidence
exit=0
```

The latest combined Task2 + dynamic-curriculum + common24 regression suite is
28 passed in 14.60s. The focused suite above includes the forged-map and
claim-loss race tests.

このfixでも性能run、CABT、training、submission、longrunは起動していない。Task 2 primary SHAは完了reportとfix review packageへ記録する。
