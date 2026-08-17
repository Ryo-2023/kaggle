# Tomato Night Stretcher child common24 guardrail（2026-08-14）

## 結論

weighted48で `+2.5259pt` だった `1152→1097 Night Stretcher` を、Tomato native親と全24-opponent common24 guardrailで比較した。candidateは **68/96 = 70.8333%**、親は **63/96 = 65.6250%**、差は **+5.2083pt（+5勝）**。全192局がDONE/fault0で、seat・opponent・paired seed/strata・GID gateを満たした。これはcandidate-onlyのguardrail positiveであり、384/longrun/submissionは自動起動しない。

| arm | W-D-L / 96 | score |
|---|---:|---:|
| Tomato native parent | 63-0-33 | 65.6250% |
| 1152→1097 Night Stretcher | 68-0-28 | 70.8333% |

## Identity / protocol

- Parent deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- Parent policy SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- Candidate deck file SHA: `b49944fa5400daa03c4f7ed14eccc2cf388bb268349fe6cb8549e6834abbae57`
- Candidate multiset SHA: `46abcda0f1fb64f72c38734f3829db577ca8b7ef169581a7d6b073240063d82a`
- Source weighted manifest SHA: `0cc44dbe6cc144e4943842d6911ebd7083feb8728345aa716034f76aa60024a2`
- Source weighted summary SHA: `59222a445c251f573bda2c001f81b423c9187ed620327bc6133b477aceafe54f`
- common24 config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- Evaluator implementation SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

全24 IDs、両seat、各repetition 2局の96局/arm。各arm seat48/48、各opponent4局、arm内GID/seed unique、全arm合計GID192/seed96 unique。candidate-parentの`(opponent, seat, repetition)` keysとseed scheduleは完全一致した。authorityはresearch-only、execution/training/promotion/submission/longrunはfalse。

## Resource / artifact

ResourceGovernorはnormal、workers12、worker recycle16、GPU compute processなし、kill0、restart0。192/192 DONE、wall 11.900秒、throughput 16.135 games/s。

Root: `runs/final-sprint-autonomous/resource-aware-tomato-night-common24-v1-20260814/`

- `common24_summary.json`: `102327fcf2868556b1fbc22bee19904e8cf6f4a8f5fc19832bdd7a958bdafbd0`
- `common24_summary.md`: `e9b3ee43a6ca319a322d4ed7e855f5ff93635593e1bbbbfae903a01cb6994dd9`
- `final_summary.json`: `f24084ac7ee2b057645df5361824c42a46066d6eec1a709dd7d66c5eba5541c4`
- ledger: `common24-96/evaluation/ledger.jsonl`（192 records）
- research-only wrapper SHA: `971343901735acb719d563032318d4eee44592a9706cf115b09907bcce364366`

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_resource_aware_tomato_night_common24_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-tomato-night-common24-v1-20260814
```

384/longrunへ進むには親側の明示判断が必要であり、このwrapperは自動昇格しない。既存weighted root、production runner、evaluator、Champion、submissionは変更していない。
