# Common24 Reconciliation → Dynamic META_TRAIN Outcome Adapter v1

## 結論

common24 の Student v3 candidate 対 native Tomato の正式 reconciliation を、dynamic META_TRAIN curriculum が直接読める4列 canonical JSONLへ変換する strict adapter を実装した。実96局candidate ledgerのうち、`META_TRAIN` 20 opponents・80局だけを出力し、`META_FINAL` 4 opponents・16局は明示的に除外した。`META_DEV` は該当0局だった。出力は学習データやteacher behaviorではなく、次iterationのopponent curriculum更新用のresearch-only outcomeであり、全authorityはfalseである。

既存iteration 0は変更していない。`runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json` のfile SHA-256は作業前後とも `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a` のままである。CABT、学習、提出は起動していない。

## 実装

- `src/mage_ptcg/meta_specialist/common24_curriculum_outcome_adapter_v1.py`
  - reconciliation artifactのcanonical JSON、semantic SHA、request path/file SHAを検証する。
  - requestから正式reconcilerを再実行し、source reconciliationと完全一致することを要求する。
  - candidate/native両armの全ledgerを再読し、全arm横断の`game_id`一意性、`seed == base_seed + ordinal`、ledger/manifest/summary SHAを検証する。
  - meta distributionをsource SHA込みで正式loadし、各opponentのpolicy/deck identityとledger内identityを照合する。
  - candidate/nativeのsubject ID、policy/deck SHA、artifact/native source path、runner refを保持する。
  - protocol semantic SHA、evaluator implementation SHA、candidate/native runner source SHAをexecution closureへ保持する。
  - candidate armの`META_TRAIN`行だけを出力し、`META_DEV` / `META_FINAL`は出力せず、ID・件数・理由をmanifestへ記録する。
  - 出力JSONLはdynamic curriculumの既存closed schemaである`candidate_score/fault/opponent_id/seat`だけを持つ。対応するgame ID、split、subject/opponent identity、seed/base_seed、status/raw_statusはsidecar manifestの1対1 recordへ保持する。
  - manifest semantic SHAとsource/output SHAを検証し、sourceから完全再構築できないartifactをfail-closedにする。
- `scripts/build_common24_curriculum_outcome_adapter_v1.py`
  - build後に正式verifierを再実行し、manifest/ledger SHAとsplit別件数を出力する。
- `tests/meta_specialist/test_common24_curriculum_outcome_adapter_v1.py`
  - synthetic 96局/arm fixtureで`META_TRAIN` 88局だけが出力され、`META_DEV` 4局、`META_FINAL` 4局が除外されることを確認する。
  - game ID、seed/base_seed、runner source SHA、authority、dynamic curriculum reader互換性を確認する。
  - outcome JSONL改変を正式verifierが拒否することを確認する。
  - CLIのbuild→verifyと、実96局artifactのformal reloadを回帰テストに含める。

## 一次入力

| artifact | file SHA-256 | semantic SHA-256 |
|---|---|---|
| `runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/reconciliation.json` | `81bfda4621ec1fc6952dd781e04a569d41c5e5389e5dabe821f2ecce03fab0bf` | `a46cfef693951cd809a7d8fcd546e6853521b35da43482bb043a361f5bbc6bd4` |
| reconciliation request | `13ec6ab7a8206b6a7a820cfcf8699c69d1324ab22fd102a04533e89db458c728` | 該当なし |
| `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` | 該当なし |

execution closureは次を固定した。

- protocol SHA-256: `126acf547ef016dfad8b6532a17e0b2eba9544e97a6efa7f633aea165d58c767`
- evaluator implementation SHA-256: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- candidate runner source SHA-256: `fbe9fbd8a32a0f42a0b4039ae879677ec3abfd08687c5a724c0421e5136ca239`
- native runner source SHA-256: `7c559621eb960f7be0a63ad53adf615bacaf30b7058885e6b433b2a83d951a32`
- combined execution closure SHA-256: `b8fe183f78245bb91a34b440b0087c3bb5f17a65b1b9a1af5d97d629f2cb0de2`

candidate identityは`student-v3-theta0-candidate-v2`、policy SHA `451da7e9981a4682e6a7e22b1cace6d9ba1abf36431968d089bd0e7887973d9f`、deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。native comparator identityは`tomatomato_archaludon`、policy SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`、deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`である。

## 実変換結果

出力rootは`runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2/`。

| artifact | file SHA-256 | semantic SHA-256 / rows |
|---|---|---|
| `adapter-manifest.json` | `0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89` | `6ff323c8ec5cf377f8f2c9c75230416dcbafe9dfab01b801aada557ad6369454` |
| `outcome-ledger.jsonl` | `18f1bec6a1f5804996060be95265b68ccb6929d39a2133f4b270723ee14d47aa` | 80 rows |

変換内訳は次の通り。

- candidate source: 96局
- emitted `META_TRAIN`: 20 opponents、80局、seat 0 = 40局、seat 1 = 40局
- emitted outcome: win 7、draw 0、loss 73、fault 0
- excluded `META_DEV`: 0 opponents、0局
- excluded `META_FINAL`: 4 opponents、16局
  - `aristophanivan_multiply`
  - `dashimaki360_crustlecounter`
  - `lucifer19_battlecore`
  - `plamen06_steel`
- emitted game ID: 80件、全件一意

`META_FINAL`の16局はadapter manifestへ`HELDOUT_SPLIT_REJECTED_FROM_CURRICULUM_OUTCOME`として残すが、dynamic curriculum用JSONLへは1行も含めない。native 96局はcomparison identityとexecution provenanceの検証に使うだけで、outcome JSONLには含めない。

## 2026-08-13 re-seal

初回seal後に `scripts/run_native_policy_candidate_pilot_v1.py` の実体が更新され、manifestが保持していた native runner source SHA `bd546642dd4fac6b3af69cab4f80b9f804e2e9d51a6bf297718eb7baf7182c72` と現行 `7c559621eb960f7be0a63ad53adf615bacaf30b7058885e6b433b2a83d951a32` が不一致になった。source reconciliation、meta distribution、両arm ledger、80行のoutcome ledgerは一致していたため、既存targetを `runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2-pre-reseal-20260813/` へ退避し、同じsourceからtargetをatomic directory renameで再sealした。

再seal後は native runner SHA `7c559621…`、execution closure SHA `b8fe183f…`、adapter semantic SHA `6ff323c8…`、manifest file SHA `0679bc79…` となった。ledger SHAは `18f1bec6…` から不変である。退避した初回manifestは比較用に保持し、現行targetは正式verifierで完全再現できる。

## 再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/build_common24_curriculum_outcome_adapter_v1.py \
  --repo-root . \
  --reconciliation runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/reconciliation.json \
  --meta-manifest runs/final-sprint-autonomous/meta-distribution-v1/manifest.json \
  --output-dir runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2
```

artifactはimmutable new-writeであり、同一pathへの再実行は`FileExistsError`になる。再検証だけを行う場合は`verify_common24_curriculum_outcome_adapter_v1(manifest_path, repo_root)`を使う。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/meta_specialist/test_common24_curriculum_outcome_adapter_v1.py \
  tests/meta_specialist/test_dynamic_meta_train_curriculum_v1.py \
  tests/meta_specialist/test_student_v3_native_common24_reconcile_v1.py
```

結果はadapter固有 `4 passed`（再seal後のactual source formal verificationを含む）。dynamic curriculum、Full6、reconciliationを含む統合focused suiteも再実行対象とする。

## 境界と残リスク

- このartifactはcurriculum updateへ利用可能なoutcome観測を提供するだけで、policy training label、teacher behavior、promotion、longrun、submissionのauthorityを一切付与しない。
- evaluator v1自体は`timeout_seconds`をledger rowへ保持しない。正式reconciliationがrequestとarm declarationを介してtimeoutを固定する既存契約を継承している。
- runner source SHAはadapter生成時点のexact sourceを固定する。実CABT起動時点のrunner SHAをledger v1が直接保持していたわけではないため、reconciliationのrunner refと現在のexact source closureを組み合わせたpost-hoc bindingである。この制約はmanifestで隠していない。
- 本outcomeは性能が大差で劣ったtheta0 candidateの96局結果であり、新しい性能改善を示さない。次iterationを実際に生成・採用するかは別判断とする。
