# Offline Scale-up v2

`mage_ptcg.offline_scaleup` は、既存の CABT 実行と Student v0 の安全契約を置換せず、ローカル大規模実行のための population、league、dataset、Student v1 の境界を追加する。

## 実行順

外部成果物 root を指定して、次の順で実行する。

```bash
bash scripts/offline_scaleup/01_build_population.sh /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2 2
bash scripts/offline_scaleup/02_run_smoke_100.sh /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2 2
```

`runs/smoke-100/run_summary.json` の Gate が `PASS` の場合だけ、`03`、`04`、`05`、`06`、`07` の順に進める。中断した run は次で resume する。

```bash
bash scripts/offline_scaleup/resume_incomplete_run.sh /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2 2 smoke-100
```

## 安全境界

- registry は実在する source/evidence だけを登録し、runtime loader がない Family record を実行可能な code として扱わない。
- game result は append-only JSONL で、重複 terminal completion を拒否する。
- dataset は legal、fault-free、非 quarantine 教師だけを export し、相手の非公開情報と future information を拒否する。
- Student は legal action candidates だけを順位付けし、失敗時は Rule Agent v0 に fallback する。

100試合以上の CABT、GPU 学習、holdout league はこの実装の smoke 範囲には含めない。
