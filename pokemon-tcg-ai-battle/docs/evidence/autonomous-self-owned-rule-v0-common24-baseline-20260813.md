---
project: MAGE-PTCG
document_status: evidence
canonical_source: git
language: ja
title: 2026-08-13 self-owned Rule v0 common24 baseline
---

# self-owned Rule v0 common24 baseline

## 結論

現行のsubmission-compatible Rule v0 + root deckは、broad common24を24 opponent × 両seat × repetition 2（96局）で評価すると11勝/96、score rate 11.4583%、fault 0だった。seat0は8/48、seat1は3/48であり、このpoolに対する現行Rule v0は弱い。したがって同じRule v0を固定datasetで長時間化する根拠はなく、self-owned bounded policy/deck optimizationへ進む。

この測定はlocal evaluationのみである。opponentは`local_eval_only`であり、native行動をteacher labelやbehavior sourceとして使用していない。native BestKnown 72%級との直接比較ではなく、後続candidateの同一pool・同一protocol基準として使う。

## 実行条件

- run root: `runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1/`
- runner: `scripts/run_performance_first_arena_v1.py`
- base seed: `14900000`
- games per opponent/seat: `2`
- opponent IDs: `configs/meta_specialist/performance_first_broad_pool_v1.json`の24件
- evaluator implementation SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- runner SHA: `bf1d325cae93874aa0878a6b4c3f1abadbcd4e4143ca077692e4e9fef42f08c6`
- root policy closure SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`

再現コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_performance_first_arena_v1.py \
  --opponent-ids aman_crustleaware_fighting,aristophanivan_multiply,aristophanivan_probabilistic,biohack44_crustlecounter2,dashimaki360_crustlecounter,ferozahmedds_solution,harukiharada_crustle,itsuki9180_lucario_jp,kiyotah_abomasnow,kiyotah_dragapult,kiyotah_iono,kojimar_lucario,kokinnwakashuu_lucario_search,lucifer19_battlecore,masamikobayashi_garchomp,medal_0001_77a53ffc,naoto714_kangaskhan,naoto714_slowking,naoto714_ursaluna,official_random,pilkwang_lucario_alakazam,plamen06_steel,prvsiyan_grimmsnarl,rauffauzanrambe_advanced \
  --output runs/final-sprint-autonomous/self-owned-rule-v0-common24-96-v1 \
  --games-per-seat 2 --base-seed 14900000 --workers 16 \
  --worker-recycle-games 32 --timeout-seconds 600
```

## 結果と一次SHA

`summary.json`:

```json
{
  "completed_games": 96,
  "draws": 0,
  "faults": 0,
  "losses": 85,
  "requested_games": 96,
  "score_rate": 0.11458333333333333,
  "status_distribution": {"DONE": 96},
  "wins": 11
}
```

- manifest SHA: `9f76ba6a15e5024b9cbc4ba89a1d69f6393d4f538097ab7f336614fe673a9d15`
- summary SHA: `916e2223803ea54b3b3ddd3403c398436723a04f7e38ddbcc81af6d5f388f11a`
- ledger SHA: `91190a18ebce76f0e7d6597f872ad07f47ba168226831c2fcd47ac1d9d6ca3cf`

opponent別の勝利は ferozahmedds_solution 2/4、itsuki9180_lucario_jp 1/4、naoto714_kangaskhan 1/4、naoto714_slowking 1/4、naoto714_ursaluna 1/4、official_random 3/4、pilkwang_lucario_alakazam 2/4、その他17 opponentは0/4だった。これは診断用の少数cellであり、opponent別の確定順位ではない。

## 解釈と次のgate

過去の同系統12局screen（8.33%および0%）と方向は一致する。もっとも、Tomato native common arenaのpopulationとは異なるため、Tomato 72.0703%との差をこのrunだけから主張しない。

次の候補はproduction Rule v0を変更せず、hash-bound KnowledgePack tie-breakまたはbounded action-type priority overlayをresearch-only wrapperとして作る。baseline packなしと2〜3 candidateを同じcommon24/seed/seat/evaluatorで96局screenする。behavior permission、teacher label、synthetic table、submission promotionはfalse固定する。

- 明確な改善なし: 384へ延長せずpolicy route停止。
- 改善あり、fault0、seat collapseなし: seed-disjoint 384へ進む。
- 384でnative control比おおむね+3ptを再現: `LONGRUN_READY_CANDIDATE`候補としてhard-negative/meta update設計へ進む。

現時点でcandidate bridge、384、real public-state advantage table、longrun、CABT submission、Champion変更は未成立である。
