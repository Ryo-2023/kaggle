# Autonomous Native Meta-Overfit Dry-Run v1（2026-08-13）

## 結論

Task 4 の run-root materializerを実装し、strict input verificationを実artifactへ適用した。新規 run root は `BLOCKED` として安全に記録された。既存の common24 outcome adapter と dynamic curriculum は参照できたが、現行 curriculum の `META_TRAIN` entry が `teacher_behavior_allowed=false` であり、Task 2 の source permission gate（training-local、training、behaviorの全てtrue）を満たさないため、iteration manifestとcandidate tableのmaterializationは行わなかった。`ready_for_evaluation=false`、全authority falseを維持している。

今回参照した public advantage table は Task 1 の synthetic contract fixtureであり、実META_TRAINのpublic-state advantageではない。したがって性能根拠、native BestKnown超越、CABT、学習、提出候補、longrun readinessへ昇格しない。real META_TRAIN advantage tableが未生成の間は、materializerは候補を評価可能状態へ進めない。

## 変更範囲

- `scripts/build_native_meta_overfit_iteration_v1.py`
  - `--run-root` を追加し、repo-containedな新規rootだけをclaimする。既存rootは上書きしない。
  - dry-run成功時は `candidate-public-advantage-table.json`、`iteration-manifest.json`、`progress_summary.json`、`run-manifest.json` をatomicに記録する。
  - strict verification failureを任意で `--record-blocked` として記録できる。blocked rootには入力inventory、failure、SHA、progressだけを保存し、candidate artifactは保存しない。
  - `--execute`、evaluator、trainer、subprocess、CABT、submission、longrunは常に拒否・未起動。
- `tests/meta_specialist/test_build_native_meta_overfit_iteration_v1.py`
  - dry-run root、no-clobber、repo containment、forged table SHA、blocked evidence、`--execute`、child process不在を検証する。
- `runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813/`
  - strict failureを昇格させない新規blocked run root。既存performance rootを変更していない。

## 実artifact入力とSHA

| role | path | file SHA256 | 状態 |
| --- | --- | --- | --- |
| dynamic curriculum | `runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json` | `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a` | verified sourceへ到達したがpermission gateで拒否 |
| common24 adapter | `runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2/adapter-manifest.json` | `0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89` | 既存strict artifact |
| native baseline identity | `runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813-inputs/native-baseline-identity.json` | `f969439bfa4c38d8a2104c311bcb0a317c06016a848cdc7a14affece067d1c7c` | Tomato native policy/deck/evaluator SHAをbound |
| public advantage input | `runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813-inputs/synthetic-task1-public-advantage-table.json` | `d62bb3ec85115976c1e101282c60c0aa1d23e90b8b07382fd9268ad159b183b0` | **synthetic contract only、性能根拠ではない** |

native baseline identityの内部bindingは次の通りである。

```text
candidate_id: tomatomato_archaludon
policy_sha256: 8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e
deck_sha256:   42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e
evaluator_sha256: 0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84
```

real META_TRAIN advantage tableはrepo内に存在せず、missing-pathのstrict probeも実施した。missing tableを渡した場合は `FileNotFoundError` でrootをcleanupし、synthetic tableを渡した場合は次のpermission gateで停止する。

## 実行結果

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/build_native_meta_overfit_iteration_v1.py \
  --repo-root . \
  --curriculum-manifest runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json \
  --outcome-adapter-manifest runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2/adapter-manifest.json \
  --public-advantage-table runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813-inputs/synthetic-task1-public-advantage-table.json \
  --native-baseline-identity runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813-inputs/native-baseline-identity.json \
  --run-root runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813 \
  --record-blocked
```

出力は次の通りである。

```text
status=BLOCKED
block_reason=NativeMetaOverfitIterationError: META_TRAIN curriculum entry lacks exposure/behavior permission
ready_for_evaluation=false
candidate_artifacts_materialized=false
processes_launched=false
cabt_started=false
training_started=false
submission_started=false
```

run rootのprimary artifact SHA:

```text
runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813/progress_summary.json
bbc6510516d94c4396cc75eaa17b21616cbf48c94e2b931ae91f00749d5fdfa8
runs/final-sprint-autonomous/native-meta-overfit-dryrun-v1-20260813/run-manifest.json
def3733f0cbde92da13d4668727eb2203012f1f882071603f06265327f3b4b63
```

blocked rootは `progress_summary.json` と `run-manifest.json` のみを含み、`iteration-manifest.json`、candidate table copy、評価結果は含まない。既存 destinationを上書きしないテストも通過した。

## 検証

```text
PYTHONPATH=.:src .venv/bin/python -m py_compile scripts/build_native_meta_overfit_iteration_v1.py
exit=0

PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_build_native_meta_overfit_iteration_v1.py \
  tests/meta_specialist/test_native_meta_overfit_iteration_v1.py
28 passed

python scripts/docs/validate_docs.py
Validated 13 canonical documents.

git diff --check -- scripts/build_native_meta_overfit_iteration_v1.py tests/meta_specialist/test_build_native_meta_overfit_iteration_v1.py
exit=0
```

## 次の再開条件

1. `training_allowed=true`、`behavior_allowed=true`、`usage_boundary ∈ {training_local, training_local_and_eval}`、`submission_allowed=false` を満たす verified `META_TRAIN` populationを作る。
2. その populationと同一 source SHAへbindした real actor-visible public-state rows（state/action identity、outcome、seat、weight）を生成し、Task 1 tableを再構築する。
3. native baseline、common24 protocol、table、curriculum、adapterを一つのstrict iteration manifestへreloadできることを確認する。
4. その後も `ready_for_evaluation=false` を維持し、別途permission/package/evaluator closureとcommon24 96局 gateを満たした場合のみ性能screenを検討する。

commit、push、remote branch、Champion変更、Kaggle submission、CABT、training、longrunは行っていない。

## Fix round 1 — partial-copy cleanup (2026-08-13)

独立 review で、candidate tableを直接 destinationへ `shutil.copyfile` してから
`owned` に登録する順序により、copy途中の `OSError` で partial table が blocked
rootへ残る反例を確認した。`_atomic_copy_new` は sibling temporaryへstream copyし、
`flush`/`fsync` 後に `os.replace` で公開する。失敗時はtemporaryと、この呼び出しで
公開したdestinationだけをcleanupするため、候補tableのpartial bytesは残らない。

注入回帰 `test_partial_public_table_copy_is_atomic_and_not_left_in_blocked_root` は
PASSし、blocked rootは従来どおり `progress_summary.json` と `run-manifest.json`
だけになった。今回も性能/CABT/学習/提出/longrunは起動していない。

```text
focused materializer + iteration suite: 29 passed in 11.44s
py_compile: PASS
git diff --check: PASS
```

## Fix round 2 — competing destination no-clobber (2026-08-13)

独立 probe で、run-root claim後に別writerがdestinationを先に作る競合では
`os.replace` がwinner bytesを上書きし得る反例を確認した。publishを同一filesystem
上のexclusive `os.link(temp, destination)`へ変更し、`FileExistsError`時はwinnerを
保持して当方のtemporaryだけをcleanupする。

`test_competing_destination_is_never_clobbered_by_atomic_copy` はwinner bytes保持と
temporary消去を確認し、partial-copy回帰も継続している。focused materializer +
iteration suiteは30 passed、py_compile/git diff --checkはPASS。性能/CABT/学習/提出/
longrunは起動していない。
