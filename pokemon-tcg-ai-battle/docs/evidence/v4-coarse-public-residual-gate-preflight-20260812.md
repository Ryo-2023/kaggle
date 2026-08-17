# Coarse public-bucket residual gate preflight — 2026-08-12

## 判定

ChatGPT Pro レビューで確認された exact context SHA gate の低 coverage に対する次段として、research-only の coarse public-bucket gate 最小契約を追加した。これは既存の frozen residual v1、V4 production actor、CABT runner、学習器を変更せず、勝率・CABT・学習を実行していない。

## 変更範囲

- [coarse public gate module](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/src/mage_ptcg/meta_specialist/coarse_public_residual_gate_v1.py)
- [focused contract tests](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/meta_specialist/test_coarse_public_residual_gate_v1.py)

既存 `public_confidence_ood_v1.py` の固定 bucket 仕様と `build_public_confidence_reference_bundle.py` の closed bundle schemaを読み取るだけで、production経路へ importされない。

## Hash-bound reference bundle

loaderは次を検証する。

1. bundle file SHA-256 が呼び出し側の期待値と一致する。
2. bundle schema、bucket schema、train partition、source count（2以上）が一致する。
3. source list の ordinal、source SHA、distinctness、ordered source-list SHA-256 が一致する。
4. bucket count、bucket ID、positive count が一致する。
5. privacy flags と promotion authority が全て false である。

loaderが返す `CoarsePublicReferenceBundleV1.descriptor()` は `training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`、`performance_evidence=false` を固定する。

## Runtime gate contract

`CoarsePublicResidualGateV1.adjust_logits()` は actor-visible `SpecialistModelInputV1` / `SpecialistStepInputV1` と finite base logits を受ける。残差を加えるのは、以下を全て満たす場合だけである。

- public OOD v1 bucket ID が reference bundle に存在する。
- semantic action class と legal domain/STOP arity が canonical schemaとして検証できる。
- residual tableに同じ bucketとsemantic action keyが登録されている。
- residualが有限かつ `max_abs_residual` 内である。

未知 bucket、未知 action、malformed public input、arity/STOP mismatch は detached base logitsへ exact pass-throughする。zero-init（空の residual table）は known bucketでも base logitsと一致し、`residual_applied=0`、`nonzero=0` になる。

## Coverage counters

adapterは次を研究用 snapshot として集計する。

- total/valid input
- known bucket count/rate
- valid semantic action slots
- residual applied/nonzero count/rate
- top-1 change
- OOD pass-through count/rate と reason
- legal STOP/known STOP
- bucket別 decision count

coverageは性能結果や authorityを付与せず、descriptorの false flagsを維持する。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test PYTHONPATH=.:src \
  .venv/bin/pytest -q -s \
  tests/meta_specialist/test_coarse_public_residual_gate_v1.py
5 passed
```

`py_compile` と `git diff --check` も passした。テストは bundle SHA mismatch、closed authority/schema、zero-init parity、known bucket + bounded semantic action適用、unknown bucket/malformed pass-through、residual bound rejection を確認する。

## 残リスクと次の再開条件

- residual tableの生成・学習・cross-fitted value targetは未実装である。
- coarse bucketがexact contextより一般化するか、bucket collisionによる過適用がないかは未測定である。
- このadapterは既存sidecar/runnerへ未接続であり、CABT coverageや勝率を示さない。
- 次に進む場合は、同一base・固定budgetで zero-init parity と coverage smoke を先に測定し、性能比較とは分離する。

