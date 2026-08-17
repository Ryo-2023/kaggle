# Tomato parent 1086／1192 weighted48 screen（2026-08-14）

## 結論

既存119 multiset と opponents 全deckを走査して重複を除外し、Tomato native parent の未評価1-card swapを2件だけ評価した。META_TRAIN上位12（held-out除外）48局 screen は両候補とも parent より負で、common24／384 へは進めない。

| arm | W-D-L / 48 | weighted META_TRAIN score | parent差 |
|---|---:|---:|---:|
| Tomato parent | 36-0-12 | 0.762530779 | — |
| 1244 Full Metal Lab → 1086 Buddy-Buddy Poffin | 31-0-17 | 0.644721496 | −11.7809pt |
| 1244 Full Metal Lab → 1192 Carmine | 27-0-21 | 0.554529435 | −20.8001pt |

両候補とも `candidate-only`、fault 0、DONE 48/arm、seat 24/arm/seat、weighted subset 12 opponent ×4。従ってこのlaneの追加384/common24/longrunは停止する。

## 一次 artifact と SHA-256

| artifact | SHA-256 |
|---|---|
| run root | `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1086-1192-weighted-v1-20260814/` |
| wrapper | `scripts/run_resource_aware_tomato_overlay_1086_1192_weighted_v1.py` — `b90b681fbe31a8a19d60128ed0d5566b47707ec1db132d1838bdefa7b3043fbf` |
| candidate manifest | `1a7e2695789d3fc5b235c6ce6c4b0065e7411a3ab00791d180d8d9a82847be6b` |
| candidate 1244→1086 deck | `a2ce08d19e4756fba7acbf57909955462a8180d02695cf338763ee0a7d77cec3` |
| candidate 1244→1192 deck | `dddc77e23a31e37101778b9189a5861a49793810e68ebea78f9048d26cc860bb` |
| weighted summary JSON | `dbc54b333d2dee42ea450ef0d66c526dc1f1d88991782f3bddf6900a1d06b115` |
| weighted summary MD | `bf211d531d6278a0a966eac419f84112064b214572fb0ecdb8bc35e713514d63` |
| final summary | `5bee64cbf480ac0b792e44c1e2c9362c5da7c07bf800ac5c932ddb721be6ef77` |
| warmup telemetry | `d5438261ff501c00978b5f1e2218e14c5228b383e329b28d82ee18c1d582ab59` |
| weighted ledger | `ea215df059331992face061eb8795883ddb1e8ba6e222816a9e2b2addf0f9ca2` |
| weighted evaluator manifest | `9a6757469fd0da30fd8e1c5ef064fac4b711b236a3edd016d0801f4138f9fa32` |

## Integrity／resource gate

- total requested/completed 144（parent＋2 candidates）、全行 `DONE`、fault 0、score denominator 48/arm。
- each arm has 24 games per seat; game IDs and seeds are unique within arm; candidate arms share parent `(opponent, seat, repetition)` keys and seed schedule.
- ResourceGovernor: `normal`, recommended workers 12, `worker_recycle_games=16`, GPU compute process 0, kill/restart 0。warmup ramp 1/2/4/8/12 は全て fault 0。
- weighted subset SHA `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`。Lucifer／Plamen等held-outは重み更新へ投入していない。
- authority は research-only=true、execution/training/promotion/submission/longrun=false。production runnerと既存artifactは変更していない。

## 再現

```bash
PYTHONPATH=.:src python -m py_compile \
  scripts/run_resource_aware_tomato_overlay_1086_1192_weighted_v1.py
PYTHONPATH=.:src python \
  scripts/run_resource_aware_tomato_overlay_1086_1192_weighted_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-tomato-overlay-1086-1192-weighted-v1-20260814
```

同じ output root は no-clobber で再実行を拒否する。positive候補が無いため common24、384、longrun、submission は起動していない。

