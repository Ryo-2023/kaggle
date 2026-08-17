# V4 research logit ensemble / recurrence reset results

## 結論

ChatGPT Proレビューに従い、Wave6 checkpointを変更せず、semantic logitsをdecoder前で一様平均する研究専用adapterをCABTへ接続した。Wave6 seed0+seed1のuniform ensembleはfixed-six 24局で **11/24、fault 0**。同じblockのWave6単体はseed0/seed1とも11/24であり、ensembleによる改善は確認できない。

同一checkpointを2つの独立hidden memberへ複製して行ったreset ablationは、seed0ではnormal/action/turnがすべて12/24、seed1ではnormal 15/24、action 14/24、turn 11/24だった。全てfault 0だが、CABT engine seed setterがなくgame-level pairing不能で、24局/cellの差はnoise floor以下である。turn resetはseed1で悪化したため、現時点でnormal carryを置き換える根拠はない。

## 実装境界

- adapter: `src/mage_ptcg/meta_specialist/research_logit_ensemble_v1.py`
- evaluator: `scripts/measure_v4_research_ensemble_strength.py`
- tests: `tests/meta_specialist/test_research_logit_ensemble_v1.py`, `tests/meta_specialist/test_measure_v4_research_ensemble_strength.py`
- production V4 / actor pool / Rule v0 は未変更
- `ResearchLogitEnsemblePolicyFactoryV1` は各memberをfresh-per-gameで生成する
- semantic domain長とSTOP合法性は全member一致を要求する
- semantic logits/STOPを算術平均し、既存 `greedy_decode_runtime_action_v2` へ渡す
- complete actionの同一semantic actionを各member sessionへcommitし、hiddenは平均・共有しない
- `normal` / `action` / `turn` resetを明示し、action中のturn変化はfail-closed
- ensemble identity、DeckLock lineage、checkpoint file/tensor SHAをartifactへ保存する
- engine seedは未対応のため `independent_stratified_not_game_paired`

## 固定条件

- subject deck: `opponents/tomatomato_archaludon/deck.csv`
- subject archetype: `archaludon`
- deck lineage: `776fdce711497b8f98f761343602a31f001992c1842a39df9fbd0e16527afb65`
- held-out six: `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`, `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario`
- protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- base seed: `10100000`
- games: 2 games/opponent/seat = 24 games/arm
- max steps: 2000
- all artifacts: `research_only=true`, `promotion_authority=false`, `longrun_allowed=false`

Members:

| member | checkpoint file SHA | tensor state SHA |
|---:|---|---|
| Wave6 seed0 | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` |
| Wave6 seed1 | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` |

## Results

### Wave6 seed0 + seed1 uniform ensemble

Artifact: `runs/meta-specialist-v4-research-ensemble-20260812/wave6-seed0-seed1-fixed-six-24-final.json`  
Artifact SHA: `14a1b04dc04c37c549013c4177143c07ca9ae029494bcde876652f793ca6f2ac`  
Ensemble identity: `f35dc7d0fd2773d4576a0a4547cce1f99bdd2d42723c7ba5658c546e997fe117`

| arm | wins/24 | seat0 | seat1 | fault |
|---|---:|---:|---:|---:|
| Wave6 s0+s1 uniform ensemble | 11 | 6/12 | 5/12 | 0 |

This is equal to the two Wave6 single-checkpoint 24-game results measured at the same fixed-six recipe, not an improvement. Since the engine RNG is not controllable, equal base seed does not make this a paired comparison.

### Same-checkpoint independent-hidden reset ablation

The same checkpoint is intentionally supplied twice. The two policy objects have independent recurrent state but identical frozen weights. This is a reset diagnostic, not a policy diversity ensemble.

| training checkpoint | reset mode | wins/24 | seat0 | seat1 | fault | artifact SHA |
|---|---|---:|---:|---:|---:|---|
| Wave6 seed0 | normal | 12 | 7/12 | 5/12 | 0 | `10ae4e8415efd0ea0a419a2baf1388b755a90053b5de5a2c002a4ac5c964a884` |
| Wave6 seed0 | action | 12 | 6/12 | 6/12 | 0 | `c714d9da547f27114aace112f7672b47d77ae5a4e2e9541c10c10a397fd10f8f` |
| Wave6 seed0 | turn | 12 | 6/12 | 6/12 | 0 | `392fbfe15508a367329f495bd66edf60e845086c91ead06d58c981db95fdc150` |
| Wave6 seed1 | normal | 15 | 8/12 | 7/12 | 0 | `b0fb7a34f0ae2fde54668eee1a2c5670bade292e2bea6d929857a68dddbe1d2a` |
| Wave6 seed1 | action | 14 | 9/12 | 5/12 | 0 | `046a1cd1cb9419c8d13c2ba49bdbe358aea4591a2e6ac4c5a462085c41b5f3d5` |
| Wave6 seed1 | turn | 11 | 4/12 | 7/12 | 0 | `510fafda8e3f4783aedaa38b9fcab0d99aa463b92ffe7ffad4ba5d026aa49377` |

The seed0 three-way tie and seed1 normal > action > turn ordering do not establish causal recurrence superiority because each cell has only 24 independent games and engine RNG is unpaired. The only safe conclusion is that no reset mode has demonstrated a reproducible improvement over normal carry; turn reset should not replace normal carry at this stage.

## Verification

```text
pytest tests/meta_specialist/test_research_logit_ensemble_v1.py \
       tests/meta_specialist/test_measure_v4_research_ensemble_strength.py
10 passed
py_compile PASS
git diff --check PASS
```

## Next decision

Do not weight-tune or longrun this ensemble. Keep normal carry as the default recurrent contract. The next main performance objective remains a frozen Wave6 residual with anchor KL/L2 or a value-based residual; choose one only after the common evidence pack and shadow-C identity freeze are integrated. Shadow-C is deck-OOD only because its six medal agents share one generic policy hash.
