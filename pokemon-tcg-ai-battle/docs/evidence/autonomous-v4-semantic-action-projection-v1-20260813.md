# V4 minimal semantic-action projection bridge (2026-08-13)

## 結論

closed V4 seed-1 checkpointから、既存V4 trace rootを変更せず、common24を両seat・repetition 2で96局再実行した。ゲーム評価は96/96 DONE、game fault 0だった。新規wrapperはcallback中にtyped public decision stateを再構成し、V4が返した合法option位置をpublic semantic ActionKeyへ写像した。保存したのはpublic state digest、合法semantic key（public action ID＋semantic operation）、選択semantic key、selection/boundary、game/episode/opponent/seat/seed、terminal WDLだけで、raw observation、public state tree、option index、private/native/teacher labelは保存していない。

ただし、public semantic identityが衝突するdecisionが3,621件あり、これらは推測・重複解決せずfail-closedで除外した。残り1,289 decision rowsは12 semantic operationを含むが、欠落率を無視したcandidate screen昇格は不適切である。そのため `usable_signal=false`、`ready_for_candidate_screen=false` とし、V4 semantic bridgeからのpolicy update・candidate screen・384/longrunは起動しない。次はoutcome-only hard-negative/deck laneへ戻す。

## 入力と再現

* checkpoint: `runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt`
  * file SHA: `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`
  * tensor-state SHA: `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244`
* subject deck: `opponents/public_archaludon_cinderace_r7/deck.csv`
  * file SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
* broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
* pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
* command:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/run_v4_semantic_action_projection_smoke_v1.py \
  --config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --checkpoint runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --games-per-seat 2 --base-seed 14913000 \
  --output runs/final-sprint-autonomous/v4-seed1-semantic-action-projection-common24-96-serial-20260813-v2
```

`engine_seed_supported=false` は既存 evaluator の観測値であり、seed identityはledgerへ保持したが、seed差の再現性を主張しない。

## Artifact

| artifact | path | SHA-256 |
|---|---|---|
| summary | `runs/final-sprint-autonomous/v4-seed1-semantic-action-projection-common24-96-serial-20260813-v2/summary.json` | `db973af333e63209ca26a2fe94e289a34d3cd5e8715195a3158b434b800bcb3e` |
| game ledger | `runs/final-sprint-autonomous/v4-seed1-semantic-action-projection-common24-96-serial-20260813-v2/ledger.jsonl` | `abdf4354e4594fb625673be2895456e25541f66b31f599f4ff4fe7f0c89ee801` |
| semantic rows | `runs/final-sprint-autonomous/v4-seed1-semantic-action-projection-common24-96-serial-20260813-v2/semantic-action-projection.jsonl` | `4fb4654ada3fa4697b6a941e0e07ff9beea8f3abf7ec87c580272f6a150e4988` |

Summary projection SHA: `9d954cc649f45d79335e2aa6ae4ec263aa8b5a4238ff1da626ce9cf29811a927`.

## Coverage and gate

| measure | value |
|---|---:|
| requested / completed / game faults | 96 / 96 / 0 |
| persisted semantic rows | 1,289 |
| projection collisions rejected | 3,621 |
| distinct games / episodes | 96 / 96 |
| distinct semantic operations | 12 |
| complete rows / STOP-available rows | 1,289 / 100 |
| selected operation examples | ATTACK 256, PLAY 119, ATTACH 99, END 98, EVOLVE 29, SKILL 8, other public OPTION classes 680 |
| usable signal / ready | false / false |

Every persisted row was reloaded with canonical row SHA verification before aggregation. A post-run token scan over semantic rows found no raw observation, hand, prize, deck, serial, option index, private action digest, native label, or teacher label key. The only authority/permission fields are explicit false markers.

## Tests and limits

* `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_v4_semantic_action_projection_bridge_v1.py` → 3 passed.
* `python -m py_compile scripts/run_v4_semantic_action_projection_smoke_v1.py src/mage_ptcg/meta_specialist/v4_semantic_action_projection_bridge_v1.py` → pass.
* `git diff --check` → pass at handoff.

The collision rate is a property of the closed V4 public projection (many decisions have non-injective public identities), not an evaluator fault. Since a chosen key cannot be recovered uniquely in those rows, the bridge is diagnostic-only and does not authorize training, promotion, submission, or longrun.
