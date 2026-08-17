# Outcome-only iteration-1 bounded action screens（2026-08-13）

## 実装

iteration-1 hard-negative schedule（semantic SHA `5c12410d3f05419fa26f73f1e64a86b99cc9a6a7c6fb39f709b3369a54ff3def`）を strict reload し、Rule v0 の合法候補内 bounded action overlay を最大2候補だけ materialize した。`ATTACK=+120` は 384 confirmation 済みのため再実行していない。各 screen は control 96 + candidate 96、同一 opponent/seat/seed/repetition strata、META_TRAIN only、heldout exposure 0、local_eval_only pool、authority false、execution false である。

実装は `src/mage_ptcg/meta_specialist/outcome_only_iteration1_action_screen_v1.py`、CLI は `scripts/build_outcome_only_iteration1_action_screen_v1.py`、strict runner は `scripts/run_outcome_only_iteration1_action_screen_v1.py`。production `main.py`、`agents/rule_agent.py`、evaluator は変更していない。

## Candidate 1: PLAY=-120

- root: `runs/final-sprint-autonomous/v4-seed1-iteration1-action-play-minus-120-96-20260813/`
- screen semantic SHA: `8d16d95820780f845451f4372768e3800a8a0b83f991298783c0e9b9cdd18625`
- manifest SHA: `1042f57c996be0d412279963caabf4a2f772a0f81fa1b6c7701b74ee71a0acf8`
- games SHA: `4ffa55f3c454169bf1591f2ec522bfd7c08baf1dc19bc8fb11d7bc722f1a1ebf`
- evaluation summary SHA: `3799842317223d4c2bda3e8b0e0ff1eb3c43c79c3baa1b0c8071b2d04d0d918b`
- ledger SHA: `661f7fec1be8dd9a01e980136781c47fa493368423d42dfed9f4fea2a59baf66`
- run-result SHA: `67476b87362672e91004f95d9376e7171f988543403998e372bb3cfd023e4020`
- result: control 10W/0D/86L = 10.4167%; candidate 6W/0D/90L = 6.25%; Δ −4.1667pt; paired L→W 4 / W→L 8; fault 0; seat candidate 3/3 wins vs control 5/5.
- decision: NO-GO, stop; no 384 confirmation.

## Candidate 2: EVOLVE=+120

- root: `runs/final-sprint-autonomous/v4-seed1-iteration1-action-evolve-plus-120-96-20260813/`
- screen semantic SHA: `fbd89bfb83ea1ca13dd4519443093262ecb78c0c6072e442b1b80825b422bcca`
- manifest SHA: `a98035527ce4ac0289ff1feca8abda0fd9b7f37084cefbf50ac6e6b82825475f`
- games SHA: `d9408066e7c4884d7f18daa3772088ab995a8ed02a0f5513c650b30c3b810c3f`
- evaluation summary SHA: `9d0e7cc78e108d15f4b1ef2084f0c8326fd9e59079ec24d60710921f59b86a89`
- ledger SHA: `e0469188c75d30787a52a4b64907739afa362f1fb77d117e90150cc8371d761e`
- run-result SHA: `21248695a490dc366fbfac261932c44bc9aa892c4c07b2b6f4bf6da95c0c986d`
- result: control 16W/0D/80L = 16.6667%; candidate 13W/0D/83L = 13.5417%; Δ −3.125pt; paired L→W 9 / W→L 12; fault 0; seat candidate 6/7 wins vs control 8/8.
- decision: NO-GO, stop; no 384 confirmation.

The two control rates differ because the evaluator records `engine_seed_supported=false`; each fresh arm is an independent stratified run, not a game-paired common-RNG replay. The paired keys are still exact and are the primary within-screen diagnostic.

## Gate

Neither candidate is promoted relative to the current Rule v0 control. No 384/768/longrun was started. A future candidate must be positive against its own native control at 96 and clear the same fault/seat gates before confirmation.
