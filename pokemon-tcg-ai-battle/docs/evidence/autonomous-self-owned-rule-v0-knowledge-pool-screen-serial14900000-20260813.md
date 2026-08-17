---
project: MAGE-PTCG
document_status: evidence
canonical_source: git
language: ja
title: 2026-08-13 self-owned Rule v0 KnowledgePack serial confirmation
---

# self-owned Rule v0 KnowledgePack serial confirmation

## 結論

parallel worker/import経路で2件の `deck must contain exactly 60 cards, got 0`
faultが出たscreenとは別のfresh run rootで、同じbase seed/common24を
`workers=1`, `worker_recycle_games=32`へ固定して再実行した。5 arm × 96局の全480局が
`DONE`、fault 0となった。したがって候補policy/factory単体のdeck欠落は再現せず、
parallel経路の一過性raceまたは外部import状態として、fault付きparallel rootは
`SCREEN_INVALID`のまま保持する。

## 実行条件

- run root: `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-matched14900000-serial-v1/`
- command:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_rule_v0_knowledge_pool_screen_v1.py \
  --output runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-matched14900000-serial-v1 \
  --games-per-seat 2 --base-seed 14900000 --workers 1 --worker-recycle-games 32
```

- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- root policy closure SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`

## 結果

summary SHA: `28e31a1cc3f8c8f5b64b86817da0ee031e69db7dcf47611d6e6e17fadf55f096`

| arm | wins | draws | losses | score rate | faults | seat0 / seat1 wins |
|---|---:|---:|---:|---:|---:|---:|
| baseline-no-pack | 12 | 1 | 83 | 13.021% | 0 | 6 / 6 |
| play-minus (KnowledgePack -2) | 18 | 0 | 78 | 18.750% | 0 | 10 / 8 |
| play-plus (KnowledgePack +2) | 13 | 0 | 83 | 13.542% | 0 | 6 / 7 |
| attack-plus-200 | 8 | 0 | 88 | 8.333% | 0 | 5 / 3 |
| play-minus-200 | 8 | 0 | 88 | 8.333% | 0 | 4 / 4 |

baselineとの同一 `(opponent_id, seat, repetition)` paired差:

- play-minus: 13 loss→win、8 win→loss、1 draw→win。net score +5.5/96、+5.729pt。
- play-plus: 10 loss→win、9 win→loss。+0.5/96、+0.521pt。
- attack-plus-200: 3 loss→win、8 win→loss、1 draw→win。-4.5/96、-4.688pt。
- play-minus-200: 7 loss→win、11 win→loss。-4/96、-4.167pt。

opponent support（candidate wins > baseline wins / regressions / equal）は、
play-minus `10 / 5 / 9`、play-plus `6 / 7 / 11`、attack-plus-200 `2 / 6 / 16`、
play-minus-200 `2 / 6 / 16`。

## Fault切り分け

問題のparallel root
`runs/.../rule-v0-knowledge-pool-screen-v1-matched14900000/`では、play-plusの
aman seat0 seed14900000とattack-plus-200のaman seat1 seed14900003だけが
`DeckValidationError: deck must contain exactly 60 cards, got 0`だった。fresh serial
rootでは同じ4 cellをworkers=1/2各5反復（80局）して全てfault 0、さらに候補単体の
同じseed direct callも全てDONEだった。deck bytesはroot/amanとも60行・記録SHA一致で、
candidate pack/action factoryの単体不備は確認できなかった。よってfault付きparallel
artifactを再測定で上書きせず、serial rootのみfault-free evidenceとする。

## 判定

`play-minus`は同一seedの96局で+5.729ptだが、opponent supportは10/24、regression
5/24であり、1 blockだけでは昇格しない。次のgateは同じcandidateとbaselineの
seed-disjoint 384局（同一workers=1、fault0必須）。play-plusは実質同等、action
delta二つはNO-GOで384へ進めない。BestKnown、longrun、training、submission、
Champion変更は未成立。native assetはteacher label/behavior sourceとして使っていない。

## Artifact SHA

- serial summary: `28e31a1cc3f8c8f5b64b86817da0ee031e69db7dcf47611d6e6e17fadf55f096`
- serial baseline manifest: `92906410bf6dabfe53c92f62b9d4a2847d1876a39682cba97ba87b579d1091b6`
- serial play-minus manifest: `538f8e7e3a7fb3b8c541a0c031e7d83da94ca42ab9fd30c69623c06fa681c02f`
- serial play-plus manifest: `810bba1072bef46b9852c6eb4406cc1b9b8f39908000d1d0fd813bc596c4a6e6`
- serial attack-plus manifest: `05eb734b87379f657d60f287ded4b6cc999dcf24592af81785e40d4847be5e98`
- serial play-minus-200 manifest: `f0e58b0fcebd9ed1a28e8206fdfd23dc5bb2682b8c5f8fcb5033cb8effda7a9d`
