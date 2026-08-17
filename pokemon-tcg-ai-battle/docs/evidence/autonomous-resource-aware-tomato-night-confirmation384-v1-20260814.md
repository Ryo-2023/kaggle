# Tomato native × Night Stretcher 384 確認（2026-08-14）

## 結論

Night Stretcher（Poke Pad 1152→Night Stretcher 1097）は、Tomato native parent と同一 common24／seed／seat／repetition schedule で各384局を実行した。candidate は **262-0-122 / 384 = 68.229%**、parent は **284-0-100 / 384 = 73.958%**、差分は **−5.729pt** だった。fault 0 でも native parent を下回ったため、Night は candidate-only とし、768、longrun、submission へ進めない。

## 一次 artifact と SHA-256

| artifact | SHA-256 |
|---|---|
| run root | `runs/final-sprint-autonomous/resource-aware-tomato-night-confirmation384-v1-20260814/` |
| source manifest | `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1097-1159-weighted-v1-20260814/candidate_manifest.json` |
| source manifest | `0cc44dbe6cc144e4943842d6911ebd7083feb8728345aa716034f76aa60024a2` |
| candidate deck | `b49944fa5400daa03c4f7ed14eccc2cf388bb268349fe6cb8549e6834abbae57` |
| candidate multiset | `46abcda0f1fb64f72c38734f3829db577ca8b7ef169581a7d6b073240063d82a` |
| parent deck | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| Tomato native policy | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` |
| common24 config | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` |
| evaluator | `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08` |
| wrapper | `scripts/run_resource_aware_tomato_night_confirmation384_v1.py` — `176f3c5f99f352bfd117a7530c764af24e8fc64df9bb58b1ab277745632fbd4b` |
| warmup telemetry | `00ad40849d73305bcbcf5f494b58c38c60d7d70fa159aca6eb283c221e2cc3b2` |
| evaluation ledger | `92fc3055363f3a0eb2bc3f9f6e30e714e960cdb8636c4fb038c17aa69a3e4be3` |
| evaluation manifest | `89689d97356dd7fe41d42f18c1547581be585b991859c6d24756d7306dc3833b` |
| evaluation summary | `26d29e4e195ede4e286f2e1e3a33eda7251be2fa1171e2dee48918376b5323a3` |
| confirmation summary JSON | `6f8baa4037ad3dff675ad80b7c42c5a7d175752fdc8b5162aa6a9fadd5caf73c` |
| confirmation summary MD | `04f7e0b40734883e39d5317a76f2dc73f5ed0742979df0ceb10565780778d796` |
| final summary | `5f75ad7fa1aac14cd504707d4da94045bd92c7bbdf26bb208411ba77f13e211f` |

## Integrity／resource gate

- requested/completed: **768**（parent 384 + candidate 384）、全行 `DONE`、fault 0、denominator 384/arm。
- seat: 各 arm 192/seat（0, 1）。opponent: 24 opponents × 16/arm。
- candidate／parent の `(opponent, seat, repetition)` keys は一致し、seed schedule も一致。repetition は 0–7、各 arm 384 unique seeds、全体 GID 768 unique。
- ResourceGovernor は `normal`、workers 12、`worker_recycle_games=64`、GPU compute process 0、kill/restart 0。warm-up ramp 1/2/4/8/12 は各4局・fault 0。
- authority は `research_only=true`、execution/training/promotion/submission/longrun は全て `false`。既存 root／production runner は変更していない。

## 再現コマンド

```bash
PYTHONPATH=.:src python -m py_compile \
  scripts/run_resource_aware_tomato_night_confirmation384_v1.py
PYTHONPATH=.:src python \
  scripts/run_resource_aware_tomato_night_confirmation384_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-tomato-night-confirmation384-v1-20260814
```

同じ output root は no-clobber で再実行を拒否する。768／longrun／Kaggle submission は起動していない。

