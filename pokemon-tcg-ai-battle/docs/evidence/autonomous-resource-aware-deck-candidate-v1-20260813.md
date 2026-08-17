# ResourceGovernor接続・deck candidate warm-up v1（2026-08-13）

## 結論

既存の性能runner・production entrypoint・既存run rootを変更せず、新規 research-only wrapperへ `ResourceGovernor.decide` を接続した。wrapperは governor が返す CPU・memory・task cap の最小値を `safe_workers` として封印し、warm-up の計画段階として `1→2→4` の admitted ramp、recycle 分割、fault/throughput の保存欄を持つ一次 telemetry を一度だけ atomic no-clobber 公開する。workerのspawn、process kill、CABT、training、submission、longrunはこのターンでは起動していない。

## 変更と契約

- `scripts/run_resource_aware_deck_candidate_v1.py`
  - `ResourceBudget.from_json` → `ResourceGovernor.decide` → safe worker cap の順に呼び出す。
  - `normal` / `warning` だけを warm-up ready とし、source error・critical・emergency・GPU-required拒否は `blocked`、worker数0へ fail-closedする。
  - `requested_ramp_workers=[1,2,4]`、`recycle_games=16`、`performance_run_started=false`、全 authority false を保存する。
  - telemetryは一時ファイルをfsync後にexclusive `os.link`で公開し、既存destinationを上書きしない。
- `tests/meta_specialist/test_resource_aware_deck_candidate_v1.py`
  - governorの最小worker cap、ramp、source-error fail-closed、no-clobber/temp cleanupを固定する。

## 検証

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_resource_aware_deck_candidate_v1.py
3 passed in 0.08s

PYTHONPATH=.:src .venv/bin/python -m py_compile \
  scripts/run_resource_aware_deck_candidate_v1.py \
  tests/meta_specialist/test_resource_aware_deck_candidate_v1.py
exit=0
```

## 一次 telemetry

```text
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_resource_aware_deck_candidate_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-deck-candidate-v1-20260813/warmup-telemetry.json \
  --task-cap 12
```

出力: `runs/final-sprint-autonomous/resource-aware-deck-candidate-v1-20260813/warmup-telemetry.json`

| 項目 | 観測値 |
|---|---:|
| logical CPUs | `28` |
| memory available | `46790103040` bytes |
| GPU | `1`（compute processなし） |
| state | `normal` |
| safe workers | `12` |
| admitted ramp | `[1,2,4]` |
| faults / kills | `0 / 0` |
| performance run | `false` |

- payload SHA: `63c0181fdd4fb3dbca85221498577eab39dc28208afcbba69198a1f07bdd8219`
- file SHA: `312e0af21513cd52b2e0935fb763c7c1b508a4dcd6aa66f54508d9675b18e480`

## SHA

```text
scripts/run_resource_aware_deck_candidate_v1.py
a4aa0c75dd2563796cd6b52cd4af8dda692a5899a1e1e78a313613029b7dfcfb

tests/meta_specialist/test_resource_aware_deck_candidate_v1.py
f35dbff72969db0c8c47b4268cd6d6bba9ff28e4040889ddf149a5a265892def
```

## 次 gate

候補生成では既評価 multiset を除外し、META_TRAINのみを weighted objectiveへ使う。Lucifer/Plamen等のheld-out行は weight updateへ混入させず、common24 guardrailでの評価対象に限定する。未評価候補の静的manifest封印と、warm-up throughput/fault計測を確認してから、fresh rootで weighted48 → common24-96 を実行する。候補は candidate-only・authority false のままとし、positiveでも384は別判断にする。

既存artifactの上書き、production変更、commit、push、CABT提出は行っていない。
