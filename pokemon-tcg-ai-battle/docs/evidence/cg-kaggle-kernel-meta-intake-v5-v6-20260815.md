# cg Kaggle public kernel meta intake v5–v6 / merged CEM — 2026-08-15

## 結論

公開 Kaggle kernel から新しい `local_eval_only` meta source を追加取得し、smoke 済み
poolを別artifactへ昇格してから結合する経路を実装した。v5は3件、v6は2件を受理し、
各batchのTRAIN-only smokeは合計10/10 `DONE`・fault 0だった。5 sourceをTRAIN 3／DEV 1／
FINAL 1へhash-bindしてP1固定CEMへ接続したが、60/60 `DONE`・fault 0、candidateは全件
seat-collapse invalid、elite空、P1 center保持となった。性能改善・BestKnown更新・Champion変更は
成立していない。

入力 intake と smoke artifact は不変で、昇格・結合は別rootへ書き出した。FINALのZoli
referenceはv6のsource smokeには含まれるが、split後のCEM探索・選抜・再評価では読んでいない。
したがって「search-untouched」であり、smoke-untouchedな真のholdoutではない。

## 実装したsource接続

- `scripts/promote_historical_meta_smoke_v1.py`
  - `COMPLETE`、fault 0、全pool対象、pool SHA一致を確認してから別rootへ `smoke_ok=true` を封印。
  - 入力poolを変更せず、出力 `pool_manifest.json`、`fresh_meta.json`、再束縛済み
    `smoke_summary.json`、promotion reportを生成する。
- `scripts/merge_historical_meta_smoke_v1.py`
  - 複数のsmoke-promoted rootをsource ID重複検査付きで結合し、新しいpool/fresh metaを生成。
  - source batchごとのpool/fresh/smoke SHAを記録し、authorityは全てfalse。
- 回帰テスト：
  - `tests/test_promote_historical_meta_smoke_v1.py`: 2件
  - `tests/test_merge_historical_meta_smoke_v1.py`: 2件

## v5 intake

configは `configs/meta_specialist/cg_kaggle_kernel_meta_v5.json`、rootは
`runs/cg-kaggle-kernel-meta-intake-v5-20260815/`。

- accepted 3: Pixiux Lucario v63、Ryota Alakazam、Yaroslav Lucario/Crustle
- rejected 2: Prvsiyan v12（`filesystem_write`）、Romanrozen v10（`source_identity_reused`）
- pool SHA: `42c4b40c4f1585a16732b589c9d1b454538af640c841a045a20f5a91a2e9fa83`
- fresh SHA: `48257b9bd08a90d2b487b1b88e83a3fadffa51d6b958898f0afc01b303e4effe`
- intake report SHA: `9c80e01f6c5f83a7515d6c4ba3fbd74573101c736be001151d92e2ae7c9c9062`
- smoke: `runs/cg-kaggle-kernel-meta-smoke-v5-20260815/smoke_summary.json`
  - SHA `56a2dc2274b78455f96ed5ccb70b36b7eacca313c6f98d01f0761f9346e46a9a`
  - 6/6 `DONE`、fault 0、W/D/L `0/0/6`
- smoke-promoted root: `runs/cg-kaggle-kernel-meta-intake-v5-smoke2-20260815/`
  - pool SHA `9162424fa71e10b1c1ea478f9221b34da69ae4e6da06c2443bc37d597b033a76`
  - fresh SHA `bb53af0d244849d7d3ece7774899b9a94c67de30723a0b40c5a4a5c0f3b378b2`
  - promoted smoke SHA `c996521cb2f463af08efc7b71b45b50f397821a1fe0cc869b43d0ffce38adbf7`

## v6 intake

configは `configs/meta_specialist/cg_kaggle_kernel_meta_v6.json`、rootは
`runs/cg-kaggle-kernel-meta-intake-v6-20260815/`。

- accepted 2: Skarin Dragapult、Zoli Dragapult Tempo
- rejected 3: Aristophanivan（`artifact_identity_reused`／`filesystem_write`）、Souta
  （`source_identity_reused`）、Tetsutani（`artifact_identity_reused`／`source_identity_reused`）
- pool SHA: `549ae21f10a9a42e4181cb795eb5caca24f9c93838dba3388d0b2267ce9c3ded`
- fresh SHA: `d48ba8058b4ec292c6b18b0ca055b1fc74472024566a6927f7b8f9c1bd6803a9`
- intake report SHA: `0839658347ad276406c201a7eb0adb444d45fa0a944a68fdf47b57be88f552f0`
- smoke: `runs/cg-kaggle-kernel-meta-smoke-v6-20260815/smoke_summary.json`
  - SHA `20dcdef68f1841a9a5b935cfe1b9f3bed12277d3d2f6ee7ef0443978a61f46da`
  - 4/4 `DONE`、fault 0、W/D/L `0/0/4`
- smoke-promoted root: `runs/cg-kaggle-kernel-meta-intake-v6-smoke2-20260815/`
  - pool SHA `c82e1c36e7e7b72086fec4d011cb5ba388d6f531697699c71aa8ccec6918564d`
  - fresh SHA `83cc5f0dcda1da64f616e0115e9d35d466bfec07bda4fa105197da3b34879ac1`
  - promoted smoke SHA `ed6f20f6e208289722577a3f609840e363420c2824550bf630c206caf770568a`

## merged pool / split

結合rootは `runs/cg-kaggle-kernel-meta-merged-fg-smoke3-20260815/`。

- 5 references: `kaggle_pixiux_lucario_v63_20260815`,
  `kaggle_ryotasueyoshi_alakazam_20260815`, `kaggle_skarin_dragapult_20260815`,
  `kaggle_yaroslav_lucario_crustle_20260815`, `kaggle_zoli_dragapult_tempo_20260815`
- pool SHA: `2820e5d58ad97de9b6a590c342af015c724d248515c64482ac1b816b1e6efac5`
- fresh SHA: `839b42fadeff241f6eaba4be0712882ef66592386d7494a81c2d452517b83e63`
- meta SHA: `498cc9c6a53bfba5eb9ad553350dd3fcda86107a774e13aad0eeb641a10aae7e`
- split SHA: `95bc2bd113b8260df44620e7bd4b7a21963bd57d882e19739268a01fb78efd02`
- split: TRAIN = Pixiux／Ryota／Skarin、DEV = Yaroslav、FINAL = Zoli

## CEM

rootは `runs/cg-kaggle-kernel-meta-cem-fg-20260815/`。P1／root deckをcontrolに固定し、
`population=4, elite=1, generations=1, all_train_refs, campaign_seed=202608157,
positive_delta_gate=true, risk_aware_update=true, reeval_repeats=2` で実行した。

- 60/60 `DONE`、fault 0、W/D/L `5/0/55`、evaluator SHA
  `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- generation manifest SHA: `ef2c2755fd247cc464d21209e2c2603c06baab7ddb89117c806048a0e34169e3`
- results SHA: `5e1249f6a3b5c3df29038abb3a1b3d7006e1888e22d63d43c4c06853779229ce`
- candidate 4件は全て `valid=false`、`seat_collapse=true`。screen eliteは空、centerはP1。
- top candidateの粗いscoreは `2/12` win でも、seat 0 が `0/6` で gate外。従ってP1更新は不成立。
- `META_DEV`／`META_FINAL` は CEM探索に投入していない。FINALはsearch-untouchedとして保持したが、
  v6 source smoke自体は実施済みである。

判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。この一連は新sourceの取得、
安全性、fault-free接続、CEMのfail-closedを確認したものであり、勝率改善やnative性能の証拠ではない。

## Verification / authority

- `PYTHONPATH=.:src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -s -q tests/test_promote_historical_meta_smoke_v1.py tests/test_merge_historical_meta_smoke_v1.py`: 4 passed
- 既存 intake／CEM focused tests: pass（直近 `tests/test_kaggle_kernel_meta_v1.py` 10 passed、CEM 29 passed）
- split loader、smoke、CEM evaluator: pass、fault 0
- `python scripts/docs/validate_docs.py` と `git diff --check` は docs更新後に再実行する
- training／promotion／longrun／submission authorityは全artifactでfalse。Champion、BestKnown、production、deck、commit、pushは変更していない。

## 次の再開条件

同じ公開kernel identityのblind retryはしない。次は、(1) source familyの相関をさらに下げる許可済み
snapshotを追加し、(2) TRAIN-only smokeだけを先に実施してDEV/FINALをsmokeから分離し、(3) source単位の
fault0 → 独立 seedの複数block → seat-safe candidate → 未使用 DEV/FINAL の順で検証し、(4) その条件を満たす候補だけを
`cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` へ渡す。
