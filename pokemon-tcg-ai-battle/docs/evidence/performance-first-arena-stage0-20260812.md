---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
scope: performance-first-arena-stage0
---

# Performance-First Stage 0 — root pair and Wave6 diagnostic

## 結論

新しい process-pool evaluator で、現在の提出 pair（Rule v0 + current root deck）と
coherent な研究 pair（Wave6 V4 + Archaludon subject deck）を同じ six-opponent、両seat、
12局/block の recipe で測定した。全 block で `fault=0`、`DONE` で完走したが、12局は
候補採否に十分な標本ではない。root pair は2 block 合計 `1/12 + 0/12 = 1/24`
（4.17%）、Wave6 は seed0 `5/12`（41.67%）、seed1 `4/12`（33.33%）だった。
これは root pair が極端に弱い可能性を示す強いscreen signalだが、最終的な数pt差の
証明・Champion変更・提出許可ではない。

## 固定 identity

- branch: `feature/belief-guided-search`
- HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16`
- evaluator schema: `meta-specialist-parallel-cabt-evaluator-v1`
- evaluator implementation SHA: `ee3a9e4e352006af41355bf660dd599bcabbbbb5c30666970cc890ac10ce6363`
- pairing: `independent_stratified_not_game_paired`
- engine seed setter: unsupported
- opponents: `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`,
  `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario`
- cells: opponent × seat × 1 repetition = 12 games/block
- max steps: 2,000
- worker: spawn process pool, 6 workers, recycle every 4 games, BLAS/OpenMP threads=1
- fault is retained in the requested-game denominator

## Pair identity

### Current submission pair

- policy route: `main._DEFAULT_AGENT -> make_rule_agent -> agents.choose_rule_indices`
- policy identity SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- deck: current worktree `deck.csv`
- deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- archive SHA from the fresh Rule bundle: `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`

### Wave6 diagnostic pair

- subject deck: `opponents/public_archaludon_cinderace_r7/deck.csv`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- seed0 checkpoint file SHA: `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`
- seed0 tensor SHA: `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a`
- seed1 checkpoint file SHA: `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6`
- seed1 tensor SHA: `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a`

## Results

| arm | block | requested | wins | draws | losses | faults | score rate | artifact |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Rule v0 + root deck | base seed 220000 | 12 | 1 | 0 | 11 | 0 | 8.33% | `runs/meta-specialist-performance-sprint-v1/root-arena-stage0-coherent/` |
| Rule v0 + root deck | base seed 230000 | 12 | 0 | 0 | 12 | 0 | 0.00% | `runs/meta-specialist-performance-sprint-v1/root-arena-stage0-seed230000/` |
| Wave6 seed0 + Archaludon | base seed 220000 | 12 | 5 | 0 | 7 | 0 | 41.67% | `runs/meta-specialist-performance-sprint-v1/wave6-seed0-arena-stage0/` |
| Wave6 seed1 + Archaludon | base seed 230000 | 12 | 4 | 0 | 8 | 0 | 33.33% | `runs/meta-specialist-performance-sprint-v1/wave6-seed1-arena-stage0/` |

The two root blocks are independent repetitions, not paired replays. The first earlier
experimental root block under `root-arena-stage0/` used a different policy identity hash and
is superseded for joinable evidence; it is retained but must not be merged with the coherent
ledger above.

## Interpretation and next gate

The root pair's 1/24 result is sufficient to prioritize a larger root baseline measurement and
to reject the unverified claim that the current submission is competitive with Wave6. It is not
sufficient to claim an exact improvement percentage. The next measurement is Stage 1 with the
same evaluator and a frozen broad pool (at least 384 requested games per arm); V4 package work
remains a separate hard gate because the Wave6 checkpoint is not connected to the production
entrypoint and no clean Wave6 archive exists.

No Kaggle submission, Champion switch, or production `main.py` change was performed.
