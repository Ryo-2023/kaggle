# Meta Specialist v3 — Luna Max 完遂状況・最終エビデンス

生成日時: `2026-08-08T22:48:43.290706+09:00`  
worktree: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`  
branch: `feature/meta-specialist-canonical`  
HEAD: `a4e6475255ff7ac56469f87cfd0ca6214de749af`  
source diff SHA-256: `3a4f6337a7e78840137a35e0376b1924615286718988938548d204172d60ddf9`
source tree SHA-256 (src/tests/scripts/configs): `e23fa6cdb010094d0be7ab8ab04ca43f77543e2b1c6dfb0bcdc1ddfb39f26d5e`

> このレポートは、計画書に記載された全責務を「実装済み」「bounded smokeで検証済み」「本番規模では未実施」に分けて記録する。未実施の評価を成功結果として補完しない。

## 結論

主要な実装部品（representation v3、outcome critic、full-BC形式、trajectory provenance、fresh PPO/consume-once V-trace/AWR-CRR primitives、opponent schedule、fault/evaluation protocol、search/DAgger dataset、manifest）が揃い、テストとbounded smokeを完了した。一方、正式な性能向上はまだ証明されていない。Gate 1（表現の十分な実データ比較）とGate 3（sealed formal θ0）が未通過であり、GPUがOSにより遮断されているため、Phase 7–12の4,096局promotion評価も未実施である。

判定: **DO NOT PROMOTE / 実装は継続可能**。

## Gate 状態

| Gate | 状態 | 根拠 |
|---|---|---|
| 0.1 census | PASS | dirty state、source diff、実行環境を保存 |
| 0.2 focused tests | PASS | actor pool 74、collection 37、trainer 19(+2 skipped) |
| regression suite | PASS | isolated namespaceで1481 passed、23 skipped、2 warnings、93.66s |
| 0.3 reproducibility | CONDITIONAL | fresh processを標準化したが、exact replayは未成立 |
| 0.4 RNG/lifecycle | PARTIAL | local seedは導入、native engine RNG/lifecycleは完全固定できていない |
| 1 representation | NOT PASSED | relation testsは通過、4 lane各128件のみ、NLLは一貫せず、R3 latencyはR2より約3–5倍 |
| 2 critic | CONDITIONAL | uniform Brier改善のsmokeとC0/C1/C2 ablationは実施、real-corpus calibrationは未実施 |
| 3 formal θ0 | SMOKE ONLY | rocket 128件のBC best checkpoint、full corpus/3 seeds/sealed manifestではない |
| 4–6 | IMPLEMENTED + SMOKE | schema/diagnostics/evaluation/learner primitives、64 decision/64-game synthetic smoke |
| 7–9 | CONTRACT SMOKE ONLY | learner/schedule/DAgger wiringはtoy smoke、real two-lane screening未実施 |
| 10–12 | NOT RUN | Gate依存、GPU/再現性/compute制約により本番規模未実施 |

## Phase 1: representation v3 bounded real-record result

同一seed=7、各lane 128 records、3 epochs、現行ベクトル化encoderで測定した。NLLは低いほど良い。これはfull corpusのGate 1ではなく、laneごとの限定スライスである。

| lane | R2 NLL | R3-A NLL | R3-B NLL | R2 p95 ms | R3-A p95 ms | R3-B p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| alakazam | 1.820462 | 1.852043 | 1.864900 | 0.491163 | 2.096263 | 1.684764 |
| archaludon | 1.517733 | 1.482217 | 1.476455 | 0.572303 | 2.258415 | 1.997485 |
| grimmsnarl | 1.948337 | 2.020154 | 2.075685 | 0.458658 | 2.395239 | 2.262706 |
| rocket | 2.079650 | 2.312241 | 2.367937 | 0.688289 | 3.932665 | 2.629171 |

解釈: ArchaludonではR3-B NLLがR2をわずかに下回るがtop-1は下がる。Alakazam/Grimmsnarl/RocketではR3-B NLLがR2を上回る。R3 latencyはベクトル化後に改善したものの、R2よりおおむね3倍以上で、データ量・seed数・early-stopping統制が不足している。したがってR3-Bを正式採用したとは扱わず、full-corpus equal-budget benchmarkを残課題とする。

## Phase 2: critic

64 episode/4 step/2 epoch warm-upでは、uniform Brier `0.666667` から final `0.665898` へ僅かに改善し、valueは[-1,1]内だった。これは実データの十分な校正を意味しない。

C0（conditioningなし）、C1（stable opponent family）、C2（game-seed negative control）のtoy ablationでは、C1のvalidation BrierがC0より小さく、C2はtrainで見かけの相関を作れてもvalidationで相関が消えた。この結果はstable categoryを使う設計の妥当性を示すが、実laneの勝率予測性能を示さない。

## Phase 3: BC θ0

rocket teacher record 128件（train 102 / validation 26、episode/near-duplicate split）で、best epoch `2`、validation NLL `1.567107`、checkpoint SHA `4b41ec0535d97f1a1969bb314b6a048256281a3fce6470ccfd76239e8353f348` を得た。ただし1 lane・1 seed・限定sliceのため formal θ0としてsealしていない。

## Phase 4–6: learner/evaluation infrastructure

trajectory schemaは全legal-action base logits/log-prob、chosen behavior log-prob、sampling mode、hidden hash、latencyを保持し、Gumbel logitsの誤流入を拒否する。learner diagnostics smokeでは exact forward KL 4.216e-5、reverse KL 4.214e-5、TV 0.00364、argmax flip 0、normalized entropy 0.840、V-trace effective horizon 43を得た。これらは健全性の計測結果であり、学習性能向上ではない。

synthetic paired evaluation 64局は candidate win rate 0.59375 / paired delta 0.1875 だったが、runnerが決定論的toy outcomeを生成するため、promotion evidenceから除外した。

Phase 7–9のintegration smokeでは、PPO exact KL、consume-once V-trace（queue消費）、AWR/CRR weight、O0/O1/O2 opponent schedule、soft search targetとDAgger near-duplicate dedupを同一runnerで通した。ただし全learnerの同じdiagnostic primitiveを呼ぶ契約smokeであり、real actor rollout、3 seeds、512 paired screening、teacher queryは未実施である。

## 未実施・未完了の理由

1. `nvidia-smi` は `GPU access blocked by the operating system`。計画のRTX PRO 5000 Blackwell実機はこのsessionから利用できない。
2. 現行native engineは同一ledgerでもfresh/persistent間の完全再現が成立せず、Alakazam再実行ではfaultも発生した。これを解消しないまま4,096局を性能証拠にすると、paired comparisonの前提が壊れる。
3. teacher rootsは巨大で、今回の再検証は各lane 512 record bounded manifest smokeに限定した。full corpus/3 seedsは実行していない。
4. Gate 1とformal θ0が依存関係上未通過なので、Phase 7 learner screening、Phase 8 opponent distribution、Phase 9 DAgger、Phase 10 full training、Phase 11 promotionを成功扱いにできない。

## 再現コマンド（worktreeで実行）

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/run_meta_specialist_v3_ablation.py --seed 7 --epochs 3 --teacher-root runs/meta-specialist-teacher-records/t1-rocket --limit 128 --output runs/meta-specialist-v3/phase1-rocket-benchmark-vectorized-128.json
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/run_meta_specialist_v3_critic_conditioning.py --seed 7 --episodes 96 --validation-episodes 48 --steps 4 --epochs 100 --output runs/meta-specialist-v3/phase2-critic-conditioning-ablation.json
python scripts/build_meta_specialist_v3_report.py --output docs/evidence/meta-specialist-v3-final-report.md
```

## 最終成果物

- `runs/meta-specialist-v3/final/model_manifest.json`: θ0未sealを明記
- `runs/meta-specialist-v3/final/evaluation_manifest.json`: promotion未sealedを明記
- `runs/meta-specialist-v3/final/per_lane.csv`, `per_matchup.csv`: bounded/syntheticを区別
- `runs/meta-specialist-v3/final/fault_report.json`, `training_health.json`: 診断結果
- `runs/meta-specialist-v3/final/source.diff`、`source.diff.sha256`、`source.tree.sha256`
- `runs/meta-specialist-v3/phase7-9-smoke.json`: learner/schedule/DAgger contract smoke
- `docs/evidence/meta-specialist-v3-phase0-preflight-20260808.md`、`meta-specialist-v3-phase1-representation-20260808.md`、`meta-specialist-v3-bc-smoke-20260808.md`

## 次の実行順序

full teacher corpusをepisode/near-duplicate componentで分割し、R2/R3-A/R3-Bを3 seedsでequal-budget比較する。Gate 1を通過したencoderだけで4 laneのformal BC θ0をsealし、criticを64 completed episodes以上で実データ校正する。その後にのみ、同一sealed θ0からPhase 7のAlakazam/Archaludon learner screeningへ進む。
