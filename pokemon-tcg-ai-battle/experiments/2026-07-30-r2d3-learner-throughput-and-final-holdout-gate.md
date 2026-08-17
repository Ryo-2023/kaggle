# R2D3 learner高速化と final holdout gate の是正

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-30 JST |
| 担当 | agent (Claude Code) |
| 種別 | local experiment / performance profiling + fail-closed gate 修正 |
| commit | `254a9f5becd9b431b3e47807634caf123024e6ee`（作業ツリーは未コミット差分あり） |
| branch | `local/offline-scaleup-v2` |
| model provenance | claude-opus-5 / Anthropic / adaptive thinking / Claude Code 2.1.220 |
| simulator / data | replay artifact `r2d3-multiseed-psro-production-v7-recovered`、`replay_sha256=e5220308073ea680cf96eb1decdb2f3e040bbda13906073b02054f0bd520a0d8`、sequences=106,081（source 31,420／stride 4） |

## 目的と反証条件

- **問い**: R2D3 production runの学習時間はどこで消費されており、意味論を変えずにどれだけ短縮できるか。また `final_holdout_gate` の実行条件は設計意図どおりか。
- **仮説**: GPUではなくPython側の逐次処理が律速である。
- **反証条件**: profiling でGPU計算が支配的であれば、CPU側最適化の効果は出ない。
- **変更点**: (1) replay sampler のキャッシュ化、(2) `_learner_batch` のNumPy化、(3) validation CABTの並列化、(4) PPO update のバッチ化、(5) offline collate のNumPy化、(6) holdout前提条件の明示化、(7) 学習の中断再開経路。
- **固定条件**: 同一replay形状（106,081 window）、batch 128、同一GPU（RTX PRO 5000 Blackwell）、`OMP_NUM_THREADS=1`。計測中は他ジョブが同一マシンで稼働していたため、絶対値は悲観側。

## 計測結果

### 1 learner step の内訳（batch 128、window 106,081相当）

| 工程 | 変更前 | 変更後 | 備考 |
|---|---|---|---|
| `replay.sample()` | 48.8 ms | 4.9 ms | window数に比例。demonstration表とα重みのキャッシュ化 |
| `_learner_batch()` | 14.7 ms | 6.5 ms | 14 tensor全てbyte一致を確認 |
| `learner.update()` | 26.9 ms | 26.9 ms | 未変更。kernel launch律速（host同期は2.1 msのみ） |
| **合計（実測）** | **約91 ms** | **45.5 ms** | 2.0× |

残り490,000 update（multiseed 290,000＋full 150,000＋PSRO 50,000）の見積りは **12.4 h → 6.2 h**。

`learner.update()` はbatch sizeにほぼ非依存（128／512／2048／8192で29.6–33.7 ms）であり、batch拡大でsample処理量を8–16倍にできるが、学習の意味論が変わるため既定値128は変更していない。

### CABT側

- 1局ごとの `python -m mage_ptcg.offline_scaleup worker` 起動固定費は無負荷時 **約1.0 s**（うち `import torch` が1.07 s）。
- 実績ログ21本（各800–1024局）で、worker壁時間のうち `run_match` 外が **16 worker評価で61–64%**、**8 worker rolloutで35–47%**。
- 候補ポリシー推論は律速ではない。スレッド固定後のrunでcallback p50は3.7–5.4 ms、対局時間の1.0–4.4%。
- validation CABTは `Controller.validate` が逐次実行だったため、`cabt_workers` による並列化を追加した。残stageのvalidation/holdout約5,120局が対象。

### 永続CABT workerのA/B（24局、6 worker）

| 条件 | wall/局 | gate |
|---|---|---|
| `--worker-reuse-games 1`（従来） | 0.261 s | PASS |
| `--worker-reuse-games 8` | 0.125 s | PASS |

速度は2.08×。ただし**同一性は確認できていない**。同一設定の isolated run を2回実行すると24/24局で結果が異なるため、reuse有無の差（24/24局）を分離できない。原因は `scripts/test_sim.py` が `engine_seed_supported: False` であり、schedule seedがCABT engineへ渡らないこと。**既定は `1`（従来の1局1プロセス）のまま**とし、有効化には数百局規模の統計的A/Bを前提とする。

### PPO update（Gate 5a）

- 変更前は1勾配ステップあたり320回の逐次forward（batch 13）。派生見積り約3.2分／round。
- 変更後は800 episode・4 epoch・minibatch 64の1 roundを **42.4 s** で実測。

## 是正した fail-closed 欠陥

`conditional_holdout` の実行条件が development validation の勝率のみで、`final_holdout` も同じ条件を共有していた。`STAGES` の順序により deck holdout／PSRO は「実行済み」ではあるが、その**結果は一切参照されていなかった**ため、deck holdoutが閾値未達でも final holdout が消費されうる状態だった。あわせて `run_promotion` も `holdout_used` のみを見ており、holdout勝率を閾値と比較していなかった。

修正内容:

- `HOLDOUT_PREREQUISITE_STAGES` を導入し、`final_holdout` は development validation・deck holdout（使用済みかつ勝率≥閾値）・psro_payoff・psro_best_response の全PASSを要求する。未達時は `NOT_USED` を記録して消費しない。
- 消費前に `RESERVED` マーカーを書き、成功後に `USED` へ更新する。CABT途中でクラッシュしても「未使用」に見えず、再実行は one-time 違反として拒否される。
- `run_promotion` は両holdoutの勝率を閾値と比較し、判断根拠を `promotion_decision.md` に記録する。

## 再現

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONPATH=.:src .venv/bin/python -m pytest tests/ -q
```

- 追加テスト: `tests/test_submitted_opponents_r2d3.py`（holdout前提条件4件＋sampler bit一致1件）、`tests/test_policy_learning.py`（PPO padding不変性1件）、`tests/test_offline_scaleup_worker_contract.py`（worker reuse契約1件）。
- ベンチマークスクリプトは scratchpad 配下のみ（成果物として保存していない）。

## 結果

- `PYTHONPATH=.:src .venv/bin/python -m pytest tests/ -q` → **1,971 passed / 10 skipped**（335 s）。
- replay sampler は旧実装を oracle とした比較で indices・weights・demonstrations が完全一致。
- `_learner_batch` は14 tensorすべて `torch.equal` で一致。

## 解釈

学習時間はGPUではなくPython側の逐次処理が支配していた。sampler と batch 構築の修正だけで learner は2.0×になり、意味論（サンプル列・重み・tensor値）は不変であることをテストで担保した。`learner.update()` が残る最大項だがkernel launch律速であり、これ以上はbatch拡大かCUDA Graph化が必要で、いずれも学習設定の変更を伴う。

## 既知の制約

- 計測中に別ジョブ2本が同一マシンで稼働していたため、絶対値は悲観側に振れている。比率のほうが信頼できる。
- 永続CABT workerの結果同一性は未検証（上記）。既定では無効。
- `_learner_batch` は各sequenceの先頭transitionと1step先だけを使い、modelは系列長1でGRUを回している。burn-in 8／unroll 20という replay の系列構造が学習に使われていないように読める。**(要検証)** 設計上の意図か未実装かを確認していない。これが実装されるとGPU負荷は上がり、上記のbatch拡大余地の見積りも変わる。
- CABT engineがschedule seedを受け取らないため、局単位の再現性は元から存在しない。勝率比較は統計的にのみ解釈すべきである。
