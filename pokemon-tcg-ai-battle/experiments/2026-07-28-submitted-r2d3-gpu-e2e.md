# Submitted Opponent・R2D3 GPU E2E 統合

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-28 18:43–19:30 JST |
| 担当 | Codex |
| 種別 | local experiment |
| commit | `254a9f5becd9b431b3e47807634caf123024e6ee`（dirty working tree） |
| branch | `local/offline-scaleup-v2` |
| model provenance | OpenAI Codex。実 model ID・effort・CLI version は実行環境から未取得 |
| simulator / data | CABT / `submitted-opponents-r2d3-psro-v1-20260728_180801` / `gate3c-clean-2000` |

## 目的と反証条件

- **問い**: 資格済みsubmitted assetを固定commitから実CABTへ接続し、Replay、CUDA R2D3、checkpoint resume、candidate、中央推論、PSROまで一括で完走できるか。
- **仮説**: 9 training assetの72局と50% submitted populationの256局がfault-freeであり、CUDA 200 updateと100 step resume後のcandidateがvalidation opponentへ合法に接続できる。
- **反証条件**: snapshot identity不一致、split leakage、CABT fault/timeout、CUDA未使用、NaN/Inf、resume不成立、candidate illegal、PSRO行列欠損のいずれか。
- **変更点**: 固定snapshot JSONL bridge、prioritized recurrent Replay、CUDA learner、spawn actor＋IPC中央GPU推論、candidate runtime、小規模PSROを接続した。
- **固定条件**: default `deck.csv`、Rule v0、Champion、`main.py`は変更しない。final holdoutは未使用。

## 再現

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

PYTHON_BIN="$PWD/.venv-gpu/bin/python" \
CUDA_VISIBLE_DEVICES=0 \
bash scripts/policy_learning/run_submitted_r2d3_e2e.sh \
  /home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-opponents-r2d3-psro-v1-20260728_180801 \
  runs/policy-learning-submitted-r2d3-e2e-v1 \
  0 \
  8
```

生成物:

`/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-r2d3-e2e-v1-20260728_184333`

各fileのhashとsizeは同rootの `artifact_manifest.json` と `checksums.sha256` を正とする。

## 結果

| condition | games / updates | legal | fault / timeout | runtime | 備考 |
|---|---:|---:|---:|---:|---|
| submitted asset smoke | 72 games | 72 | 0 / 0 | asset別CSV参照 | 9 asset × 両side 4局 |
| submitted population | 256 games | 256 | 0 / 0 | CSV参照 | submitted 128局、選択率50% |
| CUDA learner | 200 updates | 該当なし | NaN/Inf 0 | 10.09 s | BF16、target sync 8回、100 step resume |
| CPU inference | 128 games | 128 | 0 / 0 | 2.650 games/s | 同一checkpoint |
| central GPU inference | 128 games | 128 | 0 / 0 | 7.987 games/s | batch平均2.63、最大7 |
| trained candidate safety | 32 games | 32 | 0 / 0 | CSV参照 | submitted validationのみ |
| candidate comparison | 64 games | 64 | 0 / 0 | CSV参照 | action divergence 40.625% |
| PSRO payoff | 336 games | 336 | 0 / 0 | CSV参照 | 7 policy、21 pair、各16局 |

- **sanity check**: metadata coverage 100%、split leakage 0、Replay save/reload一致、checksum検査pass。
- **負の所見**: Gate3 sourceは2000局終了済みだが3 candidate faultのため正典上`BLOCKED`。1997 valid legal trajectoryのみ使用した。単一actionへ落とせない620 decisionはsequence境界として除外し、境界跨ぎは0。
- **不確実性**: CABT engine seedは完全固定を保証しない。200 updateとPSRO smokeは接続確認であり、性能推定の標本設計ではない。

## 解釈と判断

- **観測事実**: Submitted bridgeからCUDA learner、再開checkpoint、validation candidate、中央GPU推論、PSROまで実データ経路が完走した。
- **解釈**: 中央GPU推論は今回の8 actor条件でCPU推論より高throughputだったが、actor数やGPU共有条件が変われば選択は再評価が必要である。
- **判断**: E2E統合は採用。Champion昇格とPSRO Population追加は保留し、best-responseは`DRY_RUN_NO_EXPANSION`とする。
- **言わないこと**: R2D3がRule v0やPPOより強い、またはKaggle scoreを改善するとは結論しない。
- **次 action**: (1) owner: agent、長時間学習前にReplay source weightを設計する。(2) owner: agent、独立validationで性能区間を測る。(3) owner: human、Champion promotion実験の実施可否を判断する。

## Kaggle 提出（該当時）

該当なし。Kaggle提出、commit、pushは実施していない。
