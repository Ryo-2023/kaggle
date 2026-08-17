---
title: Autonomous native guarded-score candidate pilot v1
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 結論

Tomato native policyをnative-firstで保持し、既存のbounded score-biasを「native actionのscoreを最低5000上回る単一MAIN選択だけ」へ制限したguarded candidateをcommon24 arenaでscreen/confirmした。96局の同一base-seed screenでは候補75/96に対してnative 66/96だったが、独立384局blockでは候補262W/1D/121L (68.36%)、native 274W/0D/110L (71.35%)となり、候補は **−2.99pt** だった。両armともfault 0だが、native超過は再現せず、候補は768局へ進めず **NO-GO** とする。

この結果は「guarded native-preserving surfaceの実装契約」と「この具体的なscore設定の性能」を分離する。adapter/pilotは研究用の再利用可能部品として残すが、candidateのtraining/promote/submission authorityは付与しない。

## 変更と境界

| artifact | SHA-256 |
|---|---|
| `src/mage_ptcg/meta_specialist/native_preserving_adapter_v1.py` | `d8a6764e998d160e912d36e8b4e7d32e95b7f5847be0ddbd0fe2d2f2a9ed0464` |
| `scripts/run_native_policy_candidate_pilot_v1.py` | `7c559621eb960f7be0a63ad53adf615bacaf30b7058885e6b433b2a83d951a32` |
| `tests/meta_specialist/test_native_preserving_adapter_v1.py` | `f4fef97a8527ef3685f4fc4eab8cb7acb7b39b361754f5fbb52d6e78ce774770` |
| `tests/meta_specialist/test_run_native_policy_candidate_pilot_v1.py` | `81e5dc8791fbbaea7cc86a4b56b7ae484033fdfdbd8e40c5be40ad4b19338567` |

guarded adapterの契約は次の通り。

- native agentを最初に呼び、対象はpublic observationの単一 `MAIN` selectionのみ。
- native actionが不正、multi-select、score変換失敗、未知context、例外、候補index不正ならnative actionへ完全fallback。
- candidate optionは常にnative option listのindexから選ぶ。
- `min_score_gain` と option-type biasは有限かつboundedで、candidate config SHAへbindする。
- `promotion_authority`, `training_authority`, `submission_authority` は常に `false`。
- upstream `opponents/tomatomato_archaludon/main.py` / `deck.csv` は編集していない。

## 共通arenaと入力

- pair: `tomatomato_archaludon`
- raw deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- native policy/source SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- opponent reference: `configs/meta_specialist/performance_first_broad_pool_v1.json` の24 ID（subject自身を含まない）
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- seats: seat 0/1 balanced、独立stratified block、engine seed setterなし
- workers: spawn 8、recycle 32、thread cap 1、faultをdenominatorへ保持
- candidate config: `ATTACK=+5`, `EVOLVE=+2`, `PLAY=-5`, `min_score_gain=5000`
- candidate config SHA: `ae742c21c28cbf3ed3b11d4e9f342027e3ba0a22be707638362b684db87b3af6`

## 結果

| stage | candidate artifact | native artifact | candidate | native | delta | faults |
|---|---|---|---:|---:|---:|---:|
| 96 screen (base 9,500,000) | `runs/final-sprint-autonomous/native-tomato-guarded-score-5000-common24-96-v1/candidate_summary.json` | `runs/final-sprint-autonomous/native-tomato-guarded-score-5000-native-common24-96-v1/candidate_summary.json` | 75/96 = 78.125% | 66/96 = 68.750% | +9.375pt | 0/0 |
| 384 confirm (base 9,600,000) | `runs/final-sprint-autonomous/native-tomato-guarded-score-5000-common24-384-block1-v1/candidate_summary.json` | `runs/final-sprint-autonomous/native-tomato-guarded-score-5000-native-common24-384-block1-v1/candidate_summary.json` | 262W/1D/121L = 68.359% | 274W/0D/110L = 71.354% | −2.995pt | 0/0 |

96局のcandidate/native差は同一base seedでの一時的な上振れであり、384局で反転したため、追加blockは実施しない。これは24/48局だけで棄却する判断ではなく、事前に定めた96→384 successive evaluationのconfirm NO-GOである。

## manifest / identity

各run rootにcandidate/nativeを分離した `candidate_manifest.json` を保存した。代表artifactは次の通り。

| role | manifest SHA-256 |
|---|---|
| 96 candidate | `37b830ab600cec11c781cbb286111ab53a959b264fe884fc65d713862b86992a` |
| 96 native | `44909489e1a9fa39ff2ac81008aac3c72b8c57b4baccb9fe9af48fd745bb6896` |
| 384 candidate | `37b830ab600cec11c781cbb286111ab53a959b264fe884fc65d713862b86992a` |
| 384 native | `44909489e1a9fa39ff2ac81008aac3c72b8c57b4baccb9fe9af48fd745bb6896` |

manifestにはraw policy/deck/source/config SHA、reference IDs/config SHA、pool/evaluator SHA、runner ref、`native_first`, `fail_closed`, `research_only`、3 authority falseを保存した。summary SHAは96 candidate `b27cd9341e53a9e00011721c4dddbf0a4447a43de8103ff5395c95d2c3e0325d`、96 native `646b3b84b86a31e4f8ebb65e0829f532a1e55dbaac46a3928d6ab8009eccc52b`、384 candidate `08adda12d4b87f4422caa0219d8f4ccbf8511e45bad4df3eb1c309231975d2c4`、384 native `342c056a8a0bf99743ab5e78800b04d86fa2c697778c3c9c21ca6f383592bc62` である。

## 検証と判定

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/pytest -q -s \
  tests/meta_specialist/test_native_preserving_adapter_v1.py \
  tests/meta_specialist/test_run_native_policy_candidate_pilot_v1.py
13 passed in 0.41s
```

判定は `research_only=true`, `promotion_authority=false`, `training_authority=false`, `submission_authority=false`, `faults=0`。ただし候補は384局でnativeを下回るため、`768/1536`、longrun、package promotion、Champion変更、Kaggle submissionは起動していない。

## 次の利用可能な作業

このadapterは、今後のpublic-state value/AWRやdeck mutationの候補をnative-first・fail-closedで包む土台として再利用できる。性能候補としてはguarded-5000を凍結し、次は別の明示的なstate-conditioned value/search candidateを新しいconfig SHAで作り、同じcommon24 protocolへ投入する。native baselineを測らずにcandidateだけを延長してはならない。
