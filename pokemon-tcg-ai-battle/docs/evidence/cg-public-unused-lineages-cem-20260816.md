# 公開未使用policy lineage pool / P1 CEM（2026-08-16）

## 結論

既存のbounded smokeだけを通過し、まだP1 CEMへ投入していない公開policy snapshotを2つの新しいsource epochとして整理した。Raunak／Prvsiyan／Koushikrudra／Marnie static variantの4件は、`META_TRAIN=Raunak+Prvsiyan`、`META_DEV=Koushikrudra`、`META_FINAL=Marnie`へ分離してsealした。別のJazi／Kaiwalya／Yaminh 4-source poolは既存のfresh artifactを正本としてP1 root deck CEMへ接続した。

両CEMとも screen は全行 `DONE`・fault 0 だったが、screen上でpositiveかつseat-safeなcandidateがelite数に達せず、独立再評価・DEV／FINAL・BestKnown更新へ進まなかった。判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P1、root deck、BestKnown、Champion、production、submission、`opponents/`、commit、pushは不変である。

## 1. 新規統合 pool（未使用lineage）

入力は、個別にfault-free smoke済みでCEM未使用の次の4 rootである。

- `runs/cg-kaggle-kernel-meta-promoted-v7-raunak-20260815/`
- `runs/cg-kaggle-kernel-meta-promoted-v9-20260815/`
- `runs/cg-kaggle-kernel-meta-promoted-v4-20260815/`
- `runs/cg-kaggle-kernel-meta-promoted-marnie-base-static-v2-20260816/`

統合rootは `runs/cg-kaggle-public-unused-lineages-v1-20260816/`。

| artifact | SHA-256 |
|---|---|
| pool manifest | `3b53afa3aed3e4a25494c34dc3aa855efb903a72d3c710c75aada17624065e25` |
| fresh meta | `83d72a55548b9bb7887b0e2b9c8b0138d9dfa806e3e620a2d98db976dc74e456` |
| meta manifest | `aa4ea9bed4d1eef6b2a18fb740627407ac7634bba743f2f0af63e383b2675d4e` |
| historical split | `3d0eebe7b5389e96119a43bfffd2cca1a8809b53104bbde8046271e75e61974f` |

`load_weekend_split(..., verify_sources=True)` はPASS。全rowは `training_exposure=0`、`usage_boundary=local_eval_only`、source artifactのSHA bindingを満たす。Marnieは公開rootの静的境界外依存を除いた compatibility adapter snapshotであり、独立作者lineageや公開score性能の根拠とは扱わない。

## 2. 未使用lineage poolへの self-owned deck CEM

実験rootは `runs/cg-self-owned-cg-policy-cem-public-unused-lineages-v1-20260816/`。self-owned v4 deck package（deck SHA `86fa825dd449e45683d5a08bb2f0bb6e5028f5d6b7439ae8d5739c21f90833cd`）へ不変P1 policy surfaceを束ねた。P1 source／control、campaign seed `2026084617`、population／elite `8／2`、1 generation、`META_TRAIN_ALL`、screen各candidate/control 2局×opponent×seat、positive／risk-aware gateを使用した。

- screen: `72/72 DONE`、fault 0
- candidate: 8件、valid screen candidate 1件（c05、差 `0pt`）
- elite: 0件、`screen_valid_candidates_below_elite_count_preserve_center`
- new center: P1 default config（不変）
- DEV／FINAL: 未使用、独立再評価: 未起動
- campaign manifest SHA: `19f69ae50047c96b58388b6d48c9b85adac605edead4b7147a45c8280e3fc7be`
- generation manifest SHA: `0a834a8de262511a9ffd7d808e7932dd8b9e1c281e5a1aa8c126930460c3d0b7`
- results SHA: `eac6f29b6741450a3968cde87808d3bf6e2c64c98110af02e566de496cd38109`
- evaluation summary SHA: `8085eae9c5be0dd7b11194b8188e0853fc22280cda7534e860f883c56b88da01`

最初にcore runnerへscratch deckを直接渡した `runs/cg-p1-cem-public-unused-lineages-v1-20260816/` は、候補のdeck-registrationとcontrolが一致しないためstatic smokeでfail-closedした。これは性能結果ではなくbridge契約の不一致であり、削除せず隔離した。self-owned materializer bridgeへ切り替えた上記rootを正とする。

## 3. 別の公開4-source poolへの root-deck CEM

`runs/cg-kaggle-kernel-meta-promoted-public-new4-20260816/` は、Jazi Archaludon v28 staged、Kaiwalya payload B、Yaminh v3 staged、Jazi standalone rank1 snapshotを含む、既存のfresh・smoke-promotedかつCEM未使用のpoolである。splitは `META_TRAIN=Jazi Archaludon+Kaiwalya`、`META_DEV=Yaminh staged`、`META_FINAL=Jazi rank1`。pool／fresh／meta／split SHAはそれぞれ `0c734ad4802b00605cda9a8d77215a5e6dfdbb94ed0f569254286be5cfc4574c`／`d7a28d33e7aa6e07dafd0cf4f76e2e894f441c3655b3093448a839f7ca954f07`／`2faf259f965011dea4fc17b047870f1c0d890cb162bcd4807ebe8d64dc426c73`／`d6783a320a631ccbf978bbba7cf04696248f8c03b3d69f7178aac5774cc1d81b`。

実験rootは `runs/cg-p1-cem-public-new4-v1-20260816/`。root P1 control、campaign seed `2026084621`、population／elite `8／2`、1 generation、`META_TRAIN_ALL`、positive／risk-aware gateを使用した。

- screen: `72/72 DONE`、fault 0
- candidate: 8件、valid screen candidate 1件（c06、差 `0pt`）
- elite: 0件、P1 center保持
- DEV／FINAL: 未使用、独立再評価: 未起動
- campaign manifest SHA: `6a525e48b3325efef77240dc7f425b6ca27fbebf219f2bdf0673d83ba43874ea`
- generation manifest SHA: `7ae3fdc4f8d8ecc2b9d3ad3f1571e328b4c355c199537b8e48e7538dab48b772`
- results SHA: `57aa0080dba7931d105f6e92716ace0a18df7d13ebf23ebfb3a502564b0f0010`
- evaluation summary SHA: `f13d41c82dee23de5273955bfe81e8be4ddfa6938969537c046c1a257466d7d9`

## 4. 再開条件

今回の2つのscreenで性能使用済みになったのは、それぞれの `META_TRAIN` rowsだけである。Koushikrudra／MarnieおよびYaminh／Jazi rank1は各splitのDEV／FINALとして未使用のまま保全した。ただし、同じsource epochのblind retryやelite数を変えた再実行は行わない。次は別の未性能使用policy lineage、または相関を下げた複数runtime-safe familyを新epochへ追加し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を通過した候補だけを `cg_bestknown_loop_v1.py` へ接続する。

