# Autonomous Meta Fine-Tuning Longrun v1 — 2026-08-13

## 結論

`src/mage_ptcg/meta_specialist/longrun_autonomous_v1.py` と
`scripts/run_autonomous_meta_finetune_longrun_v1.py` は、強い native
`deck + agent` を固定した研究用 longrun の契約だけを実装した。CABT、学習、
デッキ変更、package build、Kaggle submission は起動しない。CLI の既定は
`execute=False` の dry-run であり、`LONGRUN_READY` が記録されていない
`--execute` は fail-closed になる。

## 固定するもの

- `META_TRAIN` / `META_DEV` / `META_FINAL` の ID と順序は meta manifest の
  source SHA とともに `run-manifest.json` へ封印する。
- native BestKnown の pair ID、deck SHA、policy SHA、evaluator SHA を
  `NativeBaselineV1` へ封印する。`PROVEN` でない baseline は start gate を
  通過しない。
- run config は canonical JSON の SHA-256 (`config_sha256_v1`) で checkpoint、
  state、resume に結び付ける。manifest の bytes またはその source artifact が
  変わった場合は再開を拒否する。

## Start gate

`evaluate_longrun_gate_v1` は `META_DEV` の独立 block を受け取り、次を同時に
確認する。2 block 以上、2 seed 以上、fault 0、両 seat の成績差が設定値以下、
全 block で candidate が native baseline を `min_dev_delta` 以上上回ること、
META_FINAL が block へ混入していないこと、package closure と rollback が準備
されていることが必要である。いずれかが欠ければ `GateEvidenceV1.ready` は
false で、`record_gate_v1` は `BLOCKED` 状態を保存する。

`META_FINAL` は gate の candidate selection へ渡さず、split identity として
保持するだけである。

## Checkpoint / resume / stop / rollback

- checkpoint bytes を再ハッシュし、`checkpoints/<ordinal>-<sha>.json` へ atomic
  descriptor を書く。
- `events.jsonl` と `progress_summary.json` を fsync し、途中終了後も最新
  checkpoint と active checkpoint を再取得できる。
- `stop_longrun_v1` は checkpoint を削除せず `STOPPED` にする。
- `rollback_longrun_v1` はこの run が公開した checkpoint だけを active に戻し、
  `ROLLED_BACK` と rollback count を記録する。
- `record_native_regression_v1` は native baseline を下回る block を累積し、
  sealed `stop_after_regressions=2` に達した時点で自動的に `STOPPED` へ遷移する。

## 検証

実行した focused test:

```text
PYTEST_ADDOPTS=--capture=no pytest -q tests/meta_specialist/test_longrun_autonomous_v1.py
6 passed
```

変更は新規の research-only module、CLI、focused test、本文書だけであり、既存
V4、native source、CABT evaluator、production entrypoint、Champion は編集して
いない。`execute=False` のテストでは callback が呼ばれないこと、実行前の
`LONGRUN_READY` 不足が拒否されること、manifest drift、固定 split、gate、
checkpoint、stop、rollback を確認した。

## 残課題

`LONGRUN_READY` を実際に満たすには、親タスクが native baseline と固定 meta
schedule で 96→384→768→1536 の評価を完了し、独立 seed/block の gate evidence
と package closure を生成する必要がある。本 artifact 自体はその実学習や
CABT を開始しない。
