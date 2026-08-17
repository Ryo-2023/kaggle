# FINAL-SPRINT 2×2 と提出runtime監査（2026-08-14）

## 結論

現時点で提出可能性が閉じている最強pairは、production `main.py` の Rule v0 と直下 `deck.csv` である。既存common24 96局は **11/96 = 11.4583%**（fault 0）で、提出候補のBestKnownとして扱う。V4 seed1 checkpoint は Archaludon deck では **54/96 = 56.25%**（fault 0）まで実測できるが、現行production entrypointへ接続されておらず、提出候補とはまだ扱わない。

2×2の未測セルを埋めるために、研究専用runnerへ `--subject-deck` を正しく配線し、Rule v0 × Archaludon deckをfresh rootで再測定した。修正前の初回runは指定deckを無視してroot deckを使っており、2件の `DeckValidationError` を含むため性能結果から除外する。修正後の正典runは **15W/0D/81L/0F = 15.625%** である。

V4 × root deckは、V4 runtimeが `archetype_id="archaludon"` のstrict deck qualificationを要求するため、root deckに必須core ID `[169, 190]` がなく、96/96が資格エラーになった。これは性能0%ではなく、`V4_DIRECT_ROOT_DECK_CELL = CLOSED` という互換性結果である。偽core追加や資格ゲート迂回は行わない。

## 2×2比較

| policy | root / bundle deck | Archaludon deck |
|---|---:|---:|
| Rule v0 | **11/96 = 11.4583%**, 96 DONE, fault 0（既存BestKnown） | **15/96 = 15.6250%**, 96 DONE, fault 0（今回） |
| V4 seed1 | **CLOSED**: 96 FAULT、`missing core [169,190]`（性能値ではない） | **54/96 = 56.2500%**, 96 DONE, fault 0（既存） |

共通条件は同じbroad config（24 opponent IDs、両seat、games_per_seat=2、96局、evaluator SHA固定）である。ただし既存セルはそれぞれ別のseed帯であるため、4セル間の差は厳密なpaired因果推定ではなく、policy/deck interactionを判断する比較である。全てのnative opponentは`local_eval_only`であり、native action/teacher labelは保存・学習利用していない。

- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- opponent pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- evaluator implementation SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`

### Cell D: Rule v0 × root deck（SubmissionEligible BestKnown）

- root: `runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1/`
- summary SHA: `916e2223803ea54b3b3ddd3403c398436723a04f7e38ddbcc81af6d5f388f11a`
- manifest SHA: `9f76ba6a15e5024b9cbc4ba89a1d69f6393d4f538097ab7f336614fe673a9d15`
- ledger SHA: `91190a18ebce76f0e7d6597f872ad07f47ba168226831c2fcd47ac1d9d6ca3cf`
- policy SHA（`main.py` + `agents/__init__.py` + `agents/rule_agent.py`）: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- seat 0: 8W/40L、seat 1: 3W/45L
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`

### Cell C: V4 seed1 × Archaludon deck（既存の強い研究pair）

- root: `runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-serial-v1/`
- summary SHA: `db0f32c8dac532576aa82a6fb8dc7d3c37520d0d06a04e6e876d1a8da0c565a5`
- manifest SHA: `c7fcf10e25ad310b8a8717260ed255433f215fd6ad415917882190d4523667b5`
- ledger SHA: `7c141aca5b51462962e3cca25057add8c62752042a755d8743e131bf76a94ff3`
- checkpoint file SHA: `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`
- checkpoint tensor SHA: `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- seat 0/1: 各27W/21L
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- serial実行時間: 95.953944秒（約1.00秒/局）。これはローカル研究runtimeの観測値であり、Kaggle実行時間の保証ではない。

### Cell B: Rule v0 × Archaludon deck（今回の正典run）

- root: `runs/final-sprint-autonomous/final-sprint-2x2-rule-v0-archaludon-deck-96-v2-20260814/`
- summary SHA: `f9240ce41e556c77f9c5e7ee2f265e7a47286853eb78d303bf6e836d52a421d2`
- manifest SHA: `90eaf881819016b9adadf31d2c07802c225e3eae1c7c5a46883269ac4c14b1cb`
- ledger SHA: `16cc4bc252b1bd05be0f8f40103be5b9de88b3aea35331d6be956626ca027a78`
- runner SHA（subject deck配線後）: `99cbc5f062e053aa07ea40fab1751f1a66e793defb4c9fb167bb5016d0e4d6cf`
- test SHA: `47a3e23dfbab405f77a178412869f511d9aac1e249e5ceda2b4c968cc7f7f7a2`
- policy SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- seat 0: 9W/39L、seat 1: 6W/42L
- opponent別W数: `biohack44=1, ferozahmedds=2, harukiharada=1, itsuki9180=1, kiyotah_dragapult=1, medal_0001=2, naoto714_kangaskhan=0, naoto714_ursaluna=2, official_random=3, pilkwang=2`（その他14 opponentは0W）
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- runtime: 41.962341秒、workers=12、worker recycle=16、fault 0

修正前の除外root `runs/final-sprint-autonomous/final-sprint-2x2-rule-v0-archaludon-deck-96-v1-20260814/` は、runnerが`ROOT_DECK`を固定参照していた。faultは2件とも `DeckValidationError: deck must contain exactly 60 cards, got 0` で、指定Archaludon deckの結果ではない。除外rootは上書きせず保全している。

### Cell A: V4 seed1 × root deck（互換性closed）

- root: `runs/final-sprint-autonomous/final-sprint-2x2-v4-seed1-root-deck-96-v1-20260814/`
- summary SHA: `36639d12cd7633ab3e996c41ba6eaeef5ec6bf0ef981c634393a595013ab2b3a`
- 96/96 FAULT、DONE 0、性能値として不採用
- 全faultの原因: `DeckQualificationError: deck is missing core card IDs: [169, 190]`
- V4 runnerは`archetype_id="archaludon"`とstrict vocabulary/deck lockを要求するため、60枚で合法なroot deckでもこの研究runtimeのsubjectとしては非互換
- production/V4 runtimeの資格ゲートを迂回するbridge、fake card、core ID追加は未実施

## 提出runtime / package feasibility

### Local primary evidence

| 項目 | 観測 |
|---|---|
| 取得済みcompetition ZIP | `data/raw/pokemon-tcg-ai-battle.zip`, 315,883,284 bytes, SHA `e880396d10da32adb3fb3c83b1b3088e635a8988057a33085e5aac49a8202d02` |
| sample entrypoint | `sample_submission/sample_submission/main.py` の`agent(obs_dict)->list[int]`、初期局面では60枚deck、通常局面ではoption index list |
| sample runtime files | `cg/api.py`, `cg/game.py`, `cg/sim.py`, `libcg.so`, `libcg.dylib`, `libcg-arm64.so`, `cg.dll`を含む。sample内にrequirements/READMEは見当たらない |
| Rule archive | `submission.tar.gz`, 5,908 bytes、SHA `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a` |
| Rule archive smoke | 2局、1W/1L、DONE 2、fault 0、illegal 0、legality pass。latency p50 0.0135ms / p95 0.0252ms / max 9.69ms（local archive-only smoke） |
| V4 checkpoint | seed1 file 3,451,469 bytes、SHA `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`、tensor SHA `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244` |
| local Python deps | torch `2.11.0+cu128`、numpy `2.5.1`、pandas `3.0.3`、gymnasium `1.2.0` |
| local accelerator | `torch.cuda.is_available()=True`、CUDA `12.8`。これはKaggle提出環境の可用性を意味しない |
| V4 builder audit | `production_entrypoint_not_connected`、`production_card_vocabulary_gate`、`runtime_dependency_closure_unvendored` の3 blocker。`submission_ready=false` |

V4 adapter自体は研究CABTでcheckpoint SHA/tensor SHAを検証し合法 indexを返すが、現行`main._DEFAULT_AGENT`はcheckpointをロードしない。`src/mage_ptcg`とtorch依存を外部checkoutなしで提出archiveへ閉じ込めた証拠もなく、V4 tarballは作っていない。

### 未確定で外部確認が必要な項目

以下はローカルZIP・リポジトリ資料から確定できないため、`EXTERNAL_CONFIRMATION_REQUIRED` とする。

- Kaggle提出時のtorch/numpy許可とversion、GPUの有無
- 提出archiveの最大サイズとcheckpoint同梱可否
- 1 action / 1 gameの実時間上限、CPU/RSS制限、worker filesystemの永続性
- `cg`共有ライブラリを含む最終archiveのOS/architecture条件
- 現行のRules / Submit tabの締切・提出回数・依存制限

Kaggle API、CLI submit、外部状態変更は実行していない。

## 判定と次の主線

`SUBMISSION_PROMOTION`では、現BestKnownのRule v0 + root deck（11.4583%）を基準にする。Rule v0 + Archaludon deckの15.625%は上回るが、Archaludon deck自体が現行root archiveのsubmission deckではなく、今回の組合せだけで提出candidateへ昇格させない。`PERFORMANCE_TARGET`ではV4 + Archaludonの56.25%がnative 72%級への中間benchmarkである。

次の主線は、V4の新規学習やsemantic bridge拡張ではなく、まず **SubmissionCompatible policy × bundle-compatible deck** の交互最適化である。V4 package closureが外部確認または最小portable bridgeで閉じるまでは、P0はRule v0、D0はroot deckに固定する。Rule v0 × Archaludonの差はdeck寄与の上限診断として使うが、native/local-eval deckをsubmissionへコピーしない。次の候補は既存hard-negativeを避けた新規deck neighborhoodを48→96でscreenし、明確なpositiveだけ384へ進める。72%未達を理由にcandidateを止めず、11.4583%のSubmissionEligible基準を超える40–50%候補を最優先で探す。

## 検証・変更境界

- TDD regression: `PYTHONPATH=.:src TMPDIR=/tmp pytest -q tests/test_performance_first_arena_v1.py` → **5 passed**
- archive smoke: **PASS**
- V4/Rule 2×2実測: Rule×Archaludon **96 DONE/fault0**、V4×Archaludon既存 **96 DONE/fault0**、V4×root **closed/96 qualification faults**
- `py_compile`: subject-deck配線後runnerでPASS
- docs validator / `git diff --check`: evidence更新後に再実行する
- production `main.py` / `agents/`、既存性能artifact、Champion、permission、submission、commit、pushは変更していない
