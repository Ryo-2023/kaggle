# V4 GPU medium campaign runner（2026-08-10）

## 結論

GPU端末で一度だけ貼り付ければ、Alakazam / Archaludon の V4 medium BC を順に学習し、各 lane の
seed 0 / 1 の best checkpoint 全てを固定 held-out 6 opponent × 両 seat × 2 games = 24 games で
評価する runner を追加した。これは実験実行の自動化であり、性能改善の結果はまだ含まない。

## 固定契約

- training: `cuda:0`、`max_records=8192`、train/validation 各 32 complete episode / 32 component、
  positive STOP target を両partitionで必須、hidden 128、embedding 64、TBPTT 8、3 epochs、seed 0 / 1。
- held-out: canonical pool の先頭 6 opponent、両seat、各 seat 2 games、base seed `9400000`、max steps 2000。
- training JSON の各 best checkpoint path、file SHA-256、tensor-state SHA-256 を読み、checkpoint file の
  現在SHAと照合する。評価 JSON の file/tensor SHA およびcheckpoint pathもtraining JSONと完全一致しなければ停止する。
- 評価側の `faults != 0`、`comparison_status != "valid"`、24 games未満、6 opponent以外はfail-closedである。
- 同じ output root に、契約を満たすtraining/evaluation artifact が既にあれば再利用する。不完全・壊れた
  artifactは黙って上書きせず、原因を示して停止する。

## 実行コマンド

GPUが見えるユーザー端末で、repository rootから次だけを実行する。

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_meta_specialist_v4_gpu_campaign.py
```

出力は `runs/meta-specialist-v4-gpu-campaign/` に保存する。全lane・全seedがfault 0で完走した場合だけ
`campaign-summary.json` を生成する。途中停止後も同じコマンドでverified artifactを再利用して再開できる。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest \
  tests/meta_specialist/test_run_meta_specialist_v4_gpu_campaign.py \
  tests/meta_specialist/test_recurrent_bc_v4.py \
  tests/meta_specialist/test_measure_v4_checkpoint_strength.py -q
```

結果: `24 passed, 1 skipped`（CUDA実機実行は未実施）。

## 残る判定

このcampaignはValidationのbest epochをcheckpointとして評価するため、validation NLLだけで採否を決めない。
次の採用判断は、生成された独立held-out 24-game artifactを、同一protocolで新規作成したv2smoke baselineと
比較して行う。Kaggle提出、commit、pushは実施していない。
