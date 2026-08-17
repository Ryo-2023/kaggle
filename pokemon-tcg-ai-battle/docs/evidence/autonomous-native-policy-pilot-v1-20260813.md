---
title: Autonomous Meta Fine-Tuning native policy pilot v1
date: 2026-08-13
status: research-only
---

# 結論

native `deck + agent` を保持したまま、score-bias と search-knob の候補を共通
opponent pool へ投入する研究専用 evaluator を追加した。tomato の score-bias は
2つの384局 blockで結果が反転し、768局 pooled では native を下回ったため長時間学習
候補から除外した。plamen の `USE_SEARCH=0` は最初の二つの368局相当 blockでは改善を
示したが、第三の736局 blockで native を下回り、1472局 pooled でも差は負となった。
従って現時点で `LONGRUN_READY` を満たす candidate はない。

すべての block は fault 0 で、engine seed setter がないため independent stratified
block として扱う。小さいblockの上振れだけで BestKnown を更新しない。

## 1. 実装と境界

追加した主要ファイル:

| artifact | SHA-256 |
|---|---|
| `src/mage_ptcg/meta_specialist/native_preserving_adapter_v1.py` | `7b1da512a44a402eaef897844937dc00fb1637ec619426d6a1f52ee8bf09d4b8` |
| `scripts/run_native_policy_candidate_pilot_v1.py` | `bd546642dd4fac6b3af69cab4f80b9f804e2e9d51a6bf297718eb7baf7182c72` |
| `tests/meta_specialist/test_native_preserving_adapter_v1.py` | `28578578b8471b2b0b8467edeb3c58183cacc5ebc8435cb737e0dd3c5280e886` |
| `tests/meta_specialist/test_run_native_policy_candidate_pilot_v1.py` | `cd87a681db7be9f0556fdac0cd1ba49492892013f7e8e4b4d02a7bf8c06206` |

adapter契約:

- native agentを先に呼び、候補はMAIN selectionだけに限定する。
-候補が不正、未知、例外、非合法 index、min/max違反の場合はnative actionへ完全復帰する。
- score biasは有限かつ絶対値1000以下の設定へ限定し、設定SHAを候補identityへ bindする。
- module loaderはsource SHA由来の隔離module名を使用し、`main`/`agents`/`__main__`の
  repository moduleをimport後に復元する。候補moduleの `agent` callableを必須化する。
- `promotion_authority`, `training_authority`, `submission_authority` は常に false。
-既存 `main.py`、pool、production actor、Champion、Kaggle packageは変更しない。

## 2. fixed arena

- pool manifest: `opponents/pool_manifest.json`
- pool SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- opponent reference: `configs/meta_specialist/performance_first_broad_pool_v1.json` の24 ID
- subject自己対戦を除外するため実際の1 blockは23 opponent × 2 seat × repetition
- 96相当 = 92 games、384相当 = 368 games、768相当 = 736 games
- CABT evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- worker: spawn、8 workers、recycle 32、thread cap 1、faultを分母へ保持
- engine seed setter: false; paired gameとは呼ばない

## 3. tomato score-bias

subject:

- pair: `tomatomato_archaludon`
- deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- policy/source SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- candidate config: `ATTACK=+5`, `EVOLVE=+2`, `PLAY=-5`
- config SHA: `bd2cf688fac3f1e67943e1f167ebf64a4405d9b32d5ce3e13e54cf6295e009ae`

同じ設定でnative baselineも必ず測定した。

| block | candidate | native | delta | faults |
|---|---:|---:|---:|---:|
| seed 9500000, 384 | 275/384 = 71.6146% | 259/384 = 67.4479% | +4.1667pt | 0 |
| seed 9600000, 384 | 251/384 = 65.3646% | 271/384 = 70.5729% | −5.2083pt | 0 |
| pooled 768 | 526/768 = 68.4896% | 530/768 = 69.0104% | −0.5208pt | 0 |

block2 candidate seat scoresはseat0 `135/192=70.3125%`、seat1
`116/192=60.4167%`でgap 9.896ptとなり、longrun gateの5pt上限を超えた。

主要artifact:

- candidate block1 summary SHA `480c4f2d92f1db5ceeeda0e7d5f824d57f6237704e2c72df9784141bdcc2930c`
- candidate block2 summary SHA `95c6fb35923c47673178db89b77e7b962e659980cda1de80137c7bbe4f7c8eec`
- native block1 summary SHA `908b93b1f934bf3b972a935a9d9e69de08299d0eb98e350750e70abe933aa189`
- native block2 summary SHA `bb679b32e77b93ab64db4a0570f0db7837bb0228debdf5264e070a4dd5e36c9d`

## 4. plamen search knob

subject:

- pair: `plamen06_steel`
- deck SHA: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- policy/source SHA: `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`
- candidate: environment `USE_SEARCH=0`, all score biases empty
- config SHA: `210f6386da24d0e1ab015e64cb3b2a277640733df5e5e37dbc8c749072b28a55`

| block | candidate | native | delta | faults |
|---|---:|---:|---:|---:|
| seed 9700000, 92 | 73/92 = 79.3478% | 68/92 = 73.9130% | +5.4348pt | 0 |
| seed 9800000, 368 | 276/368 = 75.0000% | 259/368 = 70.3804% | +4.6196pt | 0 |
| seed 9900000, 368 | 267/368 = 72.5543% | 267/368 = 72.5543% | 0.0000pt | 0 |
| seed 10000000, 736 | 532/736 = 72.2826% | 555/736 = 75.4076% | −3.1250pt | 0 |
| pooled 1472 | 1075/1472 = 73.0299% | 1081/1472 = 73.4375% | −0.4076pt | 0 |

従って `USE_SEARCH=0` は「効果がない」と即断はしないが、現行証拠では native
BestKnownを超えたとは言えない。次に続ける場合は、search budget/candidate/depthの
候補を個別に同じ successive-halvingで測る必要がある。native sourceへの直接編集はしない。

主要artifact:

- no-search 368 block1: `runs/final-sprint-autonomous/native-pilot/plamen-nos-search-384`
- no-search 368 block2: `runs/final-sprint-autonomous/native-pilot/plamen-nos-search-384-block2`
- no-search 736 block3 summary SHA `0149c76064fa1279b13c48097f51f3c7b4dfaf3f86bedb48ed8638c6cac536dd`
- native 368 block1 summary SHA `4ccf0ff4f48445dddc5443d4016f2319f30baa1e2eade7fb2cf35c71e5866fa2`
- native 368 block2 summary SHA `ce854763e258743393c60f29159479fa3effcff36e2fced01fbd33d757ee0619`
- native 736 block3 summary SHA `98e0e965988d5e12a071f132863b692529ec732a00b1d2959c002175d6b7a2b4`

## 5. longrun gate

tomato candidateを2 block evidenceへ入力し、longrun stateをdry-runとして初期化した。

- run config SHA: `9943e733381043e6c4fec08dcbf5ba5b4d7b638f28ca4ae7e9887fac0e19825e`
- state: `BLOCKED`
- gate reasons: `seat_balance_ok`, `dev_improvement_ok`, `package_closure`, `rollback_ready`
- dry-runは training/CABT/submission を起動していない

この状態で `LONGRUN_READY` や `LONGRUN_STARTED` と報告してはならない。native超過が
2独立META_DEV blockで再現し、seat gap/fault/package/rollback gateを満たした候補が
得られた場合だけ、明示的 runnerを使う `execute=True` へ進む。

## 6. 次の許可された作業

1. plamenを親にした合法1/2/3/4 card mutation manifestを作り、native policy固定で
   96→384→768→1536をscreenする。
2. candidateがnativeを2 block連続で上回った場合だけ、policy-fixed/deck-fixedの
   alternating raceへ昇格する。
3. training-local permissionが明示されたtomato/Luciferのデータを使う場合は、
   AWR targetのsource/permission SHAを別manifestに固定する。local_eval_onlyを
   submissionや無許可behavior sourceへ拡張しない。
4. gate未達の間はlongrunを開始せず、native baselineを常に同一blockへ含める。

このartifactは性能昇格を意味せず、native候補が短期上振れを含むか、長期的にnativeを
超えるかを分離するための証拠である。
