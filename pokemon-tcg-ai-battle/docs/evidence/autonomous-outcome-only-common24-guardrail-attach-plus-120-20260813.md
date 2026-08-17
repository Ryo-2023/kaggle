# Outcome-only ATTACH+120 common24 guardrail

## 結論

META_TRAIN weighted48 では候補が +2.083pt に見えたが、同じ ATTACH+120 を broad common24（24 IDs、両seat×2 repetition）へ拡張した evaluation-only guardrail では **NO-GO** となった。candidate は control より −9.375ptで、同じ action overlay を 384 局へ昇格しない。ATTACH surfaceだけを停止し、outcome-only black-box 全体と deck child laneは停止しない。

## Protocol

- fresh root: `runs/final-sprint-autonomous/v4-seed1-outcome-only-common24-guardrail-attach-plus-120-96-20260813-v1/`
- candidate: self-owned Rule v0 `ATTACH:+120` bounded public action-type overlay
- control: current Rule v0 root policy（policy SHA `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`）
- candidate policy SHA: `e2a64a60ec81742dc897ebaa38a02494c1390167ac41b70641542b6a6d061de2`
- 24 broad IDs × 2 seats × 2 repetitions = 96 games per arm; 192 requested total
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- `workers=1`, `worker_recycle_games=16`, engine seed capability remains false
- candidate/control exact stratum keys and seeds; 192 unique game IDs
- all rows `DONE`, fault 0; opponent boundary `local_eval_only`; synthetic=false
- the four sealed heldout IDs (`aristophanivan_multiply`, `dashimaki360_crustlecounter`, `lucifer19_battlecore`, `plamen06_steel`) were evaluation-only. `heldout_training_exposure=0`, no hard-negative weight update, teacher labels, action trace, private fields, or training data.

## Result

| arm | W-D-L | score rate | META_TRAIN 80 | heldout META_FINAL 16 |
|---|---:|---:|---:|---:|
| ATTACH+120 candidate | 6-0-90 | 6.25% | 6/80 = 7.50% | 0/16 = 0.00% |
| Rule v0 control | 15-0-81 | 15.625% | 13/80 = 16.25% | 2/16 = 12.50% |

Paired transition counts are control→candidate: `LL=77`, `WL=13`, `LW=4`, `WW=2`; net candidate wins are −9. Seat wins were candidate `seat0=3`, `seat1=3`, control `seat0=7`, `seat1=8`.

## Gate decision

`PROMOTION_STATUS=NOT_PROMOTABLE`; `EXPLORATION_PRIORITY=STOP_LOCAL_ATTACH_SURFACE`. The weighted48 positive was short-screen variance and is not evidence for common24 or longrun promotion. Do not run ATTACH+120 384, and do not rerun PLAY/EVOLVE/ATTACK. Continue only with a separately identified deck child or another untested black-box surface under the same common24/fault/seat gates.

## Artifact hashes

- screen semantic SHA: `102d15ba16fc6caa4596b6257b104c2c73d8f87ba5b6c9210a60a2e012047d6d`
- `screen.json` file SHA: `d55359ad72e40210fd0e09b28cbfb4d3c311e5afedf6e4308b9cb35cb64f44c3`
- evaluation ledger SHA: `ff6ed1a03a334fc95175a0e8fcf3da5d2ae1a2c79c9e2c55a8218901eaddf442`
- evaluation summary SHA: `e7e5e62e1cee06b7853b73a9ff6073de2e39476d0557bca82bd407329f75015c`

再現コマンド:

```bash
PYTHONPATH=.:src python scripts/build_outcome_only_common24_guardrail_v1.py \
  --repo-root . \
  --schedule runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json \
  --broad-config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --candidate-id attach-plus-120 --action-deltas-json '{"ATTACH":120}' \
  --base-seed 14910480 \
  --output runs/final-sprint-autonomous/v4-seed1-outcome-only-common24-guardrail-attach-plus-120-96-20260813-v1/screen.json
PYTHONPATH=.:src python scripts/run_outcome_only_common24_guardrail_v1.py \
  --screen runs/final-sprint-autonomous/v4-seed1-outcome-only-common24-guardrail-attach-plus-120-96-20260813-v1/screen.json \
  --games runs/final-sprint-autonomous/v4-seed1-outcome-only-common24-guardrail-attach-plus-120-96-20260813-v1/screen.games.json \
  --output runs/final-sprint-autonomous/v4-seed1-outcome-only-common24-guardrail-attach-plus-120-96-20260813-v1/evaluation \
  --workers 1 --worker-recycle-games 16
```

