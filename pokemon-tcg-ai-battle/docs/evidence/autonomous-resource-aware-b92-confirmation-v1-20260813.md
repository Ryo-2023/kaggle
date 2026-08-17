# b92a resource-aware common24 384 confirmation (2026-08-13)

## 結論

v2 の META_TRAIN weighted48 と common24-96 で唯一 positive だった b92a3b55c5fa3485…（1185→1159）を、同じ Tomato native policy の parent と fresh common24 384 で確認した。candidate は **255-0-129 / 384 = 66.406%**、parent は **282-0-102 / 384 = 73.438%**、差分は **−7.031pt**。従って b92a は candidate-only とし、768/longrun へ進めない。

## 一次 artifact

- run root: `runs/final-sprint-autonomous/resource-aware-b92-confirmation-v1-20260813/`
- source manifest: `runs/final-sprint-autonomous/resource-aware-weighted-deck-halving-v2-20260813/candidate_manifest.json`
- source manifest SHA-256: `4efca96cba35abadc7d123f50c56911fd5cc522695a8603d05433f9ed18996ab`
- candidate deck SHA-256: `499c68d84a072b251d6bea616f1ca83e3185a81b321ea479d34a0f7f3d87c274`
- candidate multiset SHA-256: `f75bfb9fdac9cb1c846c53fc4cffd1487605e3077be93bd0c4715df952e71ec3`
- parent deck SHA-256: `c69076bc43426b5453e39e910c37ad62b2af42992abe1093157b893d44f3038d`
- Tomato native policy SHA-256: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- common24 config SHA-256: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- evaluator SHA-256: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- `warmup_telemetry.json`: `e780b9ad4af464966c47c2d4fdb1eafc2f4a9d69586d03215f579c9df1e744a5`
- `common24-384/evaluation/ledger.jsonl`: `41c8807834dc33fbd1917c627ebc0131bf1f4eb2ba7456c3ae9fe8e1d1835ad0`
- `common24-384/evaluation/summary.json`: `55868450abe08074f8a30bd46826624ddad47cf9512b0129e3514f1c07061aeb`
- `confirmation_summary.json`: `9729fdc7ec7b3034a4825f220c1639cc97335ff08144ff642ae3cb6ff41eb372`
- `confirmation_summary.md`: `6b3cc8471361bb5e81f89ee6ccb5b15bd7472bc72b7c01317543fca27f40dd50`
- `final_summary.json`: `ecff62992d01fe86a17310675f860eb307ccf6d1334a5c7cc735590a6204803a`

## Integrity and resource gates

- requested/completed: 768 total rows（parent 384 + candidate 384）
- terminal status: 768 `DONE`, faults 0, denominator 384 per arm
- seat support: 192 per seat per arm
- opponent support: 16 rows per opponent per arm（24 opponents）
- candidate/parent strata keys: identical（opponent × seat × repetition）
- candidate/parent seed schedule: identical; seeds unique within each arm
- game IDs: 768 unique; evaluator pairing is independent stratified, not engine-level RNG pairing
- ResourceGovernor: normal、safe workers 12、GPU compute process なし、kill 0、ramp 1/2/4/8/12 は全て 4/4 DONE・fault0、recycle 16
- authority: research-only、execution/training/promotion/submission/longrun は全て false

## Reproduction

```bash
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_resource_aware_b92_confirmation_v1.py
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_resource_aware_b92_confirmation_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-b92-confirmation-v1-20260813
```

同じ output root への再実行は no-clobber で拒否される。768、longrun、submission は起動していない。
