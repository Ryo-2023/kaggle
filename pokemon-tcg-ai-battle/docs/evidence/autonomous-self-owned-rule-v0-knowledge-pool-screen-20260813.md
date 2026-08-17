---
project: MAGE-PTCG
document_status: evidence
canonical_source: git
language: ja
title: 2026-08-13 self-owned Rule v0 KnowledgePack/action screen
---

# self-owned Rule v0 KnowledgePack/action screen

## 結論

production `main.py` と `agents/rule_agent.py` を変更せず、root Rule v0を
hash-bound KnowledgePack tie-breakおよびbounded public action-type deltaへ接続する
research-only bridgeを作成し、native common24へ96局screenを実行した。全5 armが
96/96 DONE、fault 0だったが、seed 14910000のbaselineが3/96と低く、別seedの既存
baseline 11/96との差が大きい。したがって候補を昇格せず、384局へ進む場合もまず
同一seed universeのpaired confirmationを必要条件とする。

## 実行条件

- run root: `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-96/`
- bridge: `scripts/run_rule_v0_knowledge_pool_screen_v1.py`
- tests: `tests/test_rule_v0_knowledge_pool_screen_v1.py`
- base seed: `14910000`
- games per opponent/seat: `2`（24 opponent × 2 seat × 2 = 96/arm）
- evaluator implementation SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- root policy closure SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`

再現コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_rule_v0_knowledge_pool_screen_v1.py \
  --output runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-96 \
  --games-per-seat 2 --base-seed 14910000 --workers 16 \
  --worker-recycle-games 32
```

## 一次結果

`runs/.../summary.json` SHA: `61af50a621392abac463a44c61bb07a142a64b1c2689f45df565b0f2f4bc9a45`

| arm | wins / 96 | score rate | faults |
|---|---:|---:|---:|
| baseline-no-pack | 3 / 96 | 3.125% | 0 |
| play-minus (KnowledgePack -2) | 11 / 96 | 11.458% | 0 |
| play-plus (KnowledgePack +2) | 11 / 96 | 11.458% | 0 |
| attack-plus-200 | 11 / 96 | 11.458% | 0 |
| play-minus-200 | 10 / 96 | 10.417% | 0 |

全armのゲームセルは同一 `(opponent_id, seat, repetition)` でpaired比較した。
baselineからの方向差は、play-minus 8 loss→win / 0 win→loss、play-plus 10 / 2、
attack-plus-200 9 / 1、play-minus-200 9 / 2だった。ただしこれは96局1 seedの
screenであり、既存のbase14900000 baseline 11/96と一致しないため、candidateの
継続的優越を示さない。

arm manifest SHA:

- baseline: `92906410bf6dabfe53c92f62b9d4a2847d1876a39682cba97ba87b579d1091b6`
- play-minus: `538f8e7e3a7fb3b8c541a0c031e7d83da94ca42ab9fd30c69623c06fa681c02f`
- play-plus: `810bba1072bef46b9852c6eb4406cc1b9b8f39908000d1d0fd813bc596c4a6e6`
- attack-plus-200: `05eb734b87379f657d60f287ded4b6cc999dcf24592af81785e40d4847be5e98`
- play-minus-200: `f0e58b0fcebd9ed1a28e8206fdfd23dc5bb2682b8c5f8fcb5033cb8effda7a9d`

## 契約と検証

KnowledgePackは既存のimmutable `KnowledgePack`/canonical serializationを使い、pack
SHAをcandidate identityへ含めた。action deltaは `PLAY/ATTACH/EVOLVE/ABILITY/ATTACK/END`
のpublic option typeだけを対象にし、各値を有限かつ絶対値200以下へ制限した。MAIN以外、
不正metadata、範囲外index、例外時はproduction Rule v0の戻り値へfail-closedする。
manifestにはroot policy/deck/pool/config/evaluator/packまたはdelta SHAをbindし、
`research_only=true`、`usage_boundary=local_eval_only`、training/promotion/submission
authorityを全てfalseにした。native assetはteacher labelやtraining sourceとして使っていない。

検証:

- focused: `8 passed`
- related KnowledgePack/adapter/root arena: `40 passed`
- `py_compile`: pass
- `git diff --check`: pass

artifact SHA:

- bridge: `77cc7eb0f802a5d1dc3ceeb32ae96ed29dfdc93c68e75fcade1fb1a06e4c9970`
- focused tests: `24eb14555b4b5dd577b1258bee1bb9289e1ab5a4f5d99b5bdf37c683bc0d8eda`

## 判定

現時点は `SCREEN_ONLY / NOT_PROMOTABLE`。seed-disjointまたは同一seed pairedの
384 confirmationなしにcandidateをBestKnown、longrun初期policy、提出候補へ昇格しない。
bridgeは研究評価専用で、production main/rule_agent、submission package、Champion、
training/longrunを変更・起動していない。
