# Submission-compatible policy × deck 2×2 performance probe (2026-08-14)

## 結論

未測だった2セルを、同一 broad pool・同一 evaluator・同一 seed schedule で測定した。V4 seed-1 を root deck へ載せる経路は、V4 の Archaludon core（169/190）を要求するため全96局が `DeckQualificationError` となり、性能値ではなく **V4×root deck の直接経路は現状成立しない** という結果になった。Rule v0 を既存 Archaludon deck に載せたセルは 7/96 = 7.2917%（fault 0）で、既存 Rule v0×root deck の 11/96 = 11.4583% より低かった。

既測セルと合わせると、現時点の4セルは次のとおりである。V4×Archaludon は 54/96 = 56.25%、Rule v0×root deck は 11/96 = 11.4583% であり、policy/deck の相互作用が大きい。なお、V4 の 56.25% は native checkpoint の性能測定であり、提出可否や promotion を意味しない。

| policy | root deck | Archaludon deck |
|---|---:|---:|
| Rule v0 | 11/96 = 11.4583%（既測） | 7/96 = 7.2917%（今回） |
| V4 seed-1 | 0/96、全fault（今回、deck適合不可） | 54/96 = 56.25%（既測） |

## 今回の実測契約

- pool: `configs/meta_specialist/performance_first_broad_pool_v1.json` の24 opponent IDs
- 各セル: 24 opponent × 2 seat × 2 repetition = 96局
- base seed: `14920000`、セル間で同じ `(opponent, seat, repetition, seed)` strata
- evaluator: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- workers: 12、worker recycle: 16、spawn evaluator、fault は分母へ残す
- native behavior labels/private observations/teacher labels は保存しない
- authority: training/promotion/submission/longrun はすべて false

2セルを一つの evaluator block（192局）へ投入した。初回の集計処理に再帰バグがあり、ゲーム実行後のsummary publishだけが失敗したが、`evaluation/ledger.jsonl` 192行は全て保存されていた。再実行はせず、ledgerを読み直して `summary.json` と `manifest-complete.json` を別名で封印した。

## 結果詳細

### V4 seed-1 × root deck

- W/D/L/F: 0/0/0/96
- seat0: 0/48、seat1: 0/48
- fault reason: `DeckQualificationError: deck is missing core card IDs: [169, 190]`（96局すべて同一）
- 解釈: V4 checkpoint が Archaludon deck binding を要求するため、root deckを単純に差し替えて測定できない。これは 0% の性能主張ではなく、V4 direct submission path の現状 blocker である。

### Rule v0 × Archaludon deck

- W/D/L/F: 7/0/89/0
- score rate: 7.2917%
- seat0: 4/48 = 8.3333%、seat1: 3/48 = 6.25%
- Rule v0 root-deck既測値との差: 11.4583% → 7.2917%（-4.1667pt、seedは別run）
- 解釈: deckだけをArchaludonへ置換しても Rule v0 の提出可能性能は改善しなかった。

## 成果物とSHA

- bridge: `scripts/run_submission_2x2_performance_v1.py` — `287321632f442c48671988575ad3e4a0f8fb877526c369eaa98343d30457272f`
- tests: `tests/meta_specialist/test_submission_2x2_performance_v1.py` — `2ca3a8f6037fe515c002e0309cb00c44ed7bf3736461b8f6d56a25d9b3d51bd8`
- run root: `runs/final-sprint-autonomous/submission-2x2-20260814-v1/`
- immutable evaluator ledger: `evaluation/ledger.jsonl` — `2604f5c7cd43b7a93295ef151c7cf3ac3002321035ef9b6bbe247de891952ce3`
- evaluator summary: `evaluation/summary.json` — `c8c129cff53c389f9c4c24d2ac80580b13a8dd0341fab3c224dd6ad2c846a985`
- derived 2×2 summary: `summary.json` — `49404201f7ef438d37deb9618e31351961263a187f4af9d4b9165b36c982bb53`
- completion manifest: `manifest-complete.json` — `e819337f6b72a1c615a1c8b325330c004de763ebe441094113cbf1ccf8a9e0c1`

既測セルの根拠は次の既存artifactである。

- V4 seed-1 × Archaludon: `runs/final-sprint-autonomous/wave6-seed1-meta-train-common24-96-serial-v1/summary.json` — 54/96, fault0
- Rule v0 × root deck: `runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1/summary.json` — 11/96, fault0

## 次の判断

V4×root は deck binding が閉じているため、root deckへ無理に適用するbridgeを作らない。次の主線は、V4が実際に受理する bundle-compatible deckの候補生成・package feasibility probe、または提出可能 Rule v0 の小さな deck/policy surface探索である。native 72%を submission promotion minimum として扱わず、Rule v0の11%帯より再現的に強い submission-compatible pairを優先する。

このartifactは性能測定とruntime blockerの記録であり、V4 checkpointを提出packageへコピーしたものではない。Kaggle submission、permission変更、commit/push、Champion変更は行っていない。
