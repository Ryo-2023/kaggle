# V4 promotion gate（2026-08-11）

## 目的

`src/mage_ptcg/meta_specialist/v4_promotion_gate.py` と
`scripts/evaluate_v4_promotion_gate.py` は、V4 candidate 2 seed と baseline 2 seedの既存held-out artifactを、
同一評価条件で機械的に比較する。ゲームを実行したりcheckpointをコピーしたりはせず、判定だけを行う。

## 固定条件

- candidate/baseline はそれぞれ2 seed、各96局の `meta-specialist-v4-heldout-checkpoint-strength-v1` artifact。
- subject archetype、subject deck SHA、base seed、6 opponentの順序とfingerprint、games/seat、max steps、共有 `evaluation_protocol_sha256` が全4 artifactで完全一致すること。V2 baselineは `meta-specialist-v2-fixed-heldout-checkpoint-strength-v1`、candidateはV4 schemaを許容するが、protocol identityは共通でなければならない。
- `comparison_status=valid`、96/96完走、fault 0を必須とする。
- 2 seedともbaselineよりscoreが高く、seed平均差が `+0.05` 以上。
- seat平均は両seatでbaseline以上、4 seed×seat cell中3以上が非負。
- matchupは6中4以上が非負、単一matchupの低下は `-0.25` 未満を許容しない。
- validation carryのoffline imitationは両seedでcomplete-action top1 `>=0.68`、平均 `>=0.70`、root平均 `>=0.71`、STOP各 `>=0.80`。
- common action type (CARD/PLAY/ATTACH/ATTACK) は固定floor。重点対象のEVOLVE/ATTACK/ENDは、各seedでそれぞれ `0.60/0.60/0.50` 以上を必須とし、さらに旧来の `0.05` collapse拒否も維持する。
- imitationの各seedは、候補held-out artifactと同じcheckpointのfile SHA/tensor-state SHAを持つことも必須とする。指標だけを別checkpointから持ち込むことは `NO_GO`。

不足artifact、identity drift、fault、action metric欠落はすべて `NO_GO` とする。`PROMOTION_READY` は長時間学習や提出を自動開始する許可ではなく、次の実験armへ進める機械判定である。

## 実行

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/evaluate_v4_promotion_gate.py \
  --candidate <candidate-seed-0.json> <candidate-seed-1.json> \
  --baseline <baseline-seed-0.json> <baseline-seed-1.json> \
  --imitation <candidate-imitation-metrics.json> \
  --output <promotion-gate.json>
```

Wave6が完走するまではcandidate artifactが存在しないため、判定は実行できない。完走後、同じbase seedでV2 baselineを測定し、candidate imitation metricsを生成してから実行する。

## Wave6 実測判定（2026-08-11）

一次artifact:

- V4 seed 0: `runs/meta-specialist-strength/v4-fixed-heldout-archaludon-wave6-seed0-10000000.json`
- V4 seed 1: `runs/meta-specialist-strength/v4-fixed-heldout-archaludon-wave6-seed1-10000000.json`
- V2 baseline seed 0: `runs/meta-specialist-strength/v2-fixed-heldout-archaludon-seed10000000.json`
- V2 baseline seed 1（独立再実行）: `runs/meta-specialist-strength/v2-fixed-heldout-archaludon-seed10000000-repeat.json`
- imitation: `runs/meta-specialist-v4-archaludon-longrun-wave6-current/archaludon-imitation-metrics.json`
- gate: `runs/meta-specialist-strength/v4-promotion-gate-wave6-archaludon-independent-baseline.json`

全4 held-out artifactは同じ Archaludon subject deck（SHA `42165967…`）、同じ6 opponent順、base seed `10000000`、96局、max steps `2000`、fault 0、共通 protocol SHA `0f98f699…` を持つ。V4は seed 0 `45/96`（0.46875）、seed 1 `44/96`（0.45833）。V2は独立2回が `21/96`（0.21875）と `22/96`（0.22917）で、V4−V2のseed差は `+0.25000` と `+0.22917`、平均 `+0.23958` である。matchupは6/6、seatは4/4 cellがV4非負である。

validation carry imitationは complete-action top-1 `0.80116/0.80179`、root `0.79377/0.79607`、STOP `0.89394/0.86364`。action-type平均は type 3 `0.84136`、7 `0.76207`、8 `0.85195`、13 `0.72088`、12 `0.89881`、9 `0.65278`、14 `0.58955`で、固定floorを全て上回る。

固定CLIの実判定は `PROMOTION_READY`、reasons は空。これは次の長時間armへの昇格許可であり、Kaggle提出やChampion変更を自動実行するものではない。

## 追加再検証

checkpoint bindingを強化した後、同じWave6 V4 2 seed・V2 2 seed・imitation artifactを再読込した。結果は `PROMOTION_READY`、reasons空、candidate complete-action mean `0.8014798489`。V2旧artifactがtensor-state SHAを持たない形式であることも確認し、V2 baselineだけはfile SHA互換、V4 candidateとimitationはfile/tensor SHA必須として扱う。
