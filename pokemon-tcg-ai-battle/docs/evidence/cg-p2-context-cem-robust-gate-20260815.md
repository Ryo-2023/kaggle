# P2 context CEM robust independent-block gate — 2026-08-15

## 結論

P2 context CEMへ、screen候補を独立ブロックで再評価し、全ブロックが `fault=0`・seat-safe・正差である場合だけ、最小ブロック差をCEM更新へ渡すrobust gateを追加した。単一screenのpositiveをそのまま次centerへ流さない契約である。

実CABTでは、screenで最大 `+11.2770pt` だった候補を独立2ブロックへ送ったが、`−3.9885pt`（seat unsafe）と `−6.3147pt`（seat safe）へ反転した。robust gateはcenterを保持し、P2/P3、BestKnown、Champion、production、deck phase、submissionは不変である。fresh・unused metaはローカルに無いため、両runとも `BLOCKED_NO_LOCAL_UNUSED_META` の診断扱いである。

## 実装契約

- `rank_robust_results` は各候補に2以上の `independent_blocks` を要求する。
- 各ブロックについて `faults == 0`、`candidate_seat_safe is True`、`delta_objective > 0` を同時に要求する。
- 更新スコアはブロック差の最小値（lower tail）とする。1ブロックでも負差・unsafe・faultなら候補を除外する。
- ブロック結合は宣言されたhashを信用せず、configからSHA-256を再計算して行う。
- `independent_blocks=0` は既存screen互換。robust modeでは対象候補が不足した場合もcenterを保持する。

変更対象は `src/mage_ptcg/meta_specialist/cg_p2_context_cem_v1.py` と `scripts/run_cg_p2_context_cem_v1.py`。promotion/training/longrun/submission authorityは全てfalseのままである。

## Run A: screenでsafe候補なし

root: `runs/final-sprint-autonomous/cg-p2-context-cem-robust-gate-diagnostic-20260815/`

設定は signed `damaged_active_threat_attack_bonus=-6000` 親、population 4、elite 1、1 generation、screen repetitions 2、独立block設定2（対象候補1、各repetition 2）、workers 12、base seed `491260000`。screenは240局、全て `DONE/fault0` だった。

| candidate | screen差 | seat gap | safe |
|---|---:|---:|---|
| c00 | +0.6139pt | 8.3333% | no |
| c01 | −3.0336pt | 16.6667% | no |
| c02 | −8.0111pt | 8.3333% | no |
| c03 | −5.3560pt | 12.5000% | no |

positiveかつsafeな候補が無かったため、独立blockは起動せず `CENTER_HELD_NOT_ENOUGH_ROBUST_POSITIVE_ELITES`。summary SHAは `4a43f2b2728c9a4f8af0e00d7571fc98becc33c4c015bc824d0cb0d40999ee5d`。

## Run B: screen positiveから独立2ブロックへ

root: `runs/final-sprint-autonomous/cg-p2-context-cem-robust-gate-c06-20260815/`

Campaign 2 c06（`-6114,-8020,-12769,-15294`）近傍を親に、population 3、elite 1、1 generation、screen repetitions 2、独立2 block（対象候補1、各repetition 2）、workers 12、base seed `492260000`で実行した。screen 192局、独立blockは各96局、合計384局を全て `DONE/fault0` で完了した。

screen上位のc01は `+11.2770pt`、candidate seat gap `4.1667%` でsafeだったが、独立ブロックで次の結果になった。

| independent block | 差 | candidate seat gap | safe |
|---:|---:|---:|---|
| 0 | −3.9885pt | 8.3333% | no |
| 1 | −6.3147pt | 4.1667% | yes |

最小ブロック差は負であり、robust elite 0件、`CENTER_HELD_NOT_ENOUGH_ROBUST_POSITIVE_ELITES`。summary SHAは `94044391812fc2fbcbaac1ddcf5c7e99a4193895beda85b681a9ebfa657cee6e`。

## 検証

- focused P2/CEM/public-holdout suite: `30 passed`
- `python -m py_compile`（core、runner、tests）: PASS
- `python scripts/docs/validate_docs.py`: PASS（13 canonical documents）
- `git diff --check`: PASS
- active heavy process: なし

runner SHA `2190a2c7d1c95d07a38ea1d6f2a9051a902e15441dca991434bbb5b6c229ab83`、core SHA `c016a5b77166497e45336f54adc57d3b4bc225ca34b0e6f5ee8e9f2ef3407e78`。

## 次の再開条件

新しい未使用meta sourceが得られたら、robust gateをそのmetaへ適用する。screenでpositiveでも、独立2 blockの全てで正差・seat-safe・fault0を満たさない候補は更新しない。現時点ではP1 `cg-lethal-target-v1`＋root deckを運用BestKnownとして維持し、既評価surfaceのblind retry、Champion変更、commit、push、Kaggle提出は行わない。
