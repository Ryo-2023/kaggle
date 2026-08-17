---
project: MAGE-PTCG
evidence_type: c4-python-automation
as_of: 2026-07-16
---

# C4 Python automation

`scripts/run_c4_pipeline.py`は既存collector/exporter/consumer/Gateを再利用するconfig-driven上位CLIである。`status`、`collect`、`export`、`validate`、`train`、`evaluate`、`build-model`、`gate-a`、`gate-b`、`run`を持ち、state hash mismatchを拒否し、完了stageを再実行しない。

```bash
python scripts/run_c4_pipeline.py --config configs/c4/actual_train_v0.json run
python scripts/run_c4_pipeline.py --config configs/c4/actual_train_v0.json --dry-run run
python scripts/run_c4_screening.py --config configs/c4/screening_100_v0.json
python scripts/build_kaggle_submission.py --config configs/kaggle/rule_v0.json
python scripts/verify_kaggle_submission.py --artifact dist/kaggle/rule-v0/submission.tar.gz
```

100-game screeningは50/50 seat schedule、Wilson/bootstrap区間、hard fail、resumeを既存actual viability runnerに委譲する。engine seedはunsupportedであり、真のpaired testやPromotion根拠とは記載しない。Rule packageはsource defaultを変更せず、Student packageは`ACTUAL_TRAINED` model hashを明示する。package buildはChampion変更ではない。

Student build時だけpackage rootへ専用`main.py`を生成し、repositoryの`main.py`はRule v0のままである。package entrypointは相対package pathだけを使い、model/manifest hashを照合してStudent policyを選ぶ。load失敗時は決定的Rule v0へfallbackし、runtime telemetryで検出できる。

2026-07-16のarchive-only smokeはfresh extract、`python -I`、repository root非importで`make("cabt")`の1 actual gameを実行した。model loaded/hash一致、inference requested/completed 19/16、Student selection 16、fallback/invalid/crash/timeout/privacy violation 0、legal action rate 1.0、両player `DONE`を確認した。package内からoffline dataset moduleを除外し、training bundleやprivate bindingをimportしない。結果は`PACKAGE_READY`であるが、Kaggle提出contractは未確定のため`CONTRACT_CONFIRMATION_REQUIRED`である。
