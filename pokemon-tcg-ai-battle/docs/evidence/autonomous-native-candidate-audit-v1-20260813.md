---
title: Autonomous native candidate audit v1
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 目的と判定

この監査は、Strong Asset主線の次段階である「native BestKnownを維持したまま、直接調整可能な候補を作り、共通meta arenaで比較する」ための実装契約を固定する。`main.py`、`deck.csv`、opponent pool、既存評価結果は読み取り専用で扱い、CABT、学習、提出、Champion変更は実行していない。

現時点の判定は **native candidate pilot は実行可能な設計段階、longrun GO は未達** である。候補生成は、次の三つの条件が満たされるまで性能主張をしてはならない。

1. candidateごとに policy/deck/source/config/evaluator/meta-manifest のSHAが閉じている。
2. native baseline自身を同じ座席・対戦相手分布・fault denominatorへ必ず含め、候補のfallbackが全局面で動作する。
3. 固定 `META_DEV` で96→384→768→1536局を通過し、fault 0、座席崩壊なし、native baseline超過が再現する。`META_FINAL` は最終ゲート以前の選択に使わない。

## 入力artifactとSHA

| 役割 | パス | SHA-256 |
|---|---|---|
| 強資産census | `docs/evidence/strong-asset-census-20260812.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`（pool manifest SHA。census本文は後続生成物として別管理） |
| pool manifest | `opponents/pool_manifest.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| broad arena config | `configs/meta_specialist/performance_first_broad_pool_v1.json` | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` |
| native surface audit | `runs/final-sprint-autonomous/native-surface-v1/audit-v3.json` | `abd20c6c2badc7d2471fb9aeac5bc95c298c6835a02b98fc0506191202078654` |
| surface implementation | `src/mage_ptcg/meta_specialist/native_tuning_surface_v1.py` | `f4012a0b7afcd1ef622d5099492a17449f450036401e3836f1885bffcbc7440f` |
| native ranking runner | `scripts/run_asset_pair_ranking_v1.py` | `58c0d3841a99dc20c0661a1f76116a3a3db39e952d50171cb0e6e9f71de99167` |
| parallel evaluator | `scripts/parallel_cabt_evaluator_v1.py` | `b633fa02c910353f7aedd40dbf974b451976fe9bd39d7eb505a5f15b6c8999ba` |
| fixed meta manifest | `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| fixed schedule | `runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json` | `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a` |

The census source itself is byte-bound by its internal source inventory; the pool manifest SHA above is not a substitute for a future census-file SHA. A candidate descriptor must record both the census-file SHA and the pool-manifest SHA separately.

## Native pair identity

The pair identity is `(raw deck SHA, policy SHA)`, not the display name and not the declared canonical deck hash. The declared hash is retained as a separate compatibility field because the census found raw/declared deck mismatches.

| native pair | raw deck SHA | declared canonical deck SHA | policy SHA | source SHA | surface |
|---|---|---|---|---|---|
| `tomatomato_archaludon` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` | `0963c2daca1844b539e1be78c4dfcc10ec6806d6b9bd6142b22c64efe49f7501` | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` | `f6c81277903e60343a797515f6afef3843056d36ecf86d9f59b461c529511042` | score/threshold, native fallback |
| `lucifer19_battlecore` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | `da1d56e33b96abccedb056d4f8b31da68bfd2c224ade3a4914b5709135aa7535` | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` | `86d882d9a908572ddbed0a3975f9ce778224a53814e44e896afdb43c39fd2092` | score/threshold, native fallback |
| `plamen06_steel` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | `da1d56e33b96abccedb056d4f8b31da68bfd2c224ade3a4914b5709135aa7535` | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` | `167c155b93f9290be6d5b38a9b434b49a38850faeddbde1b92b9abc797d76f5f` | score/threshold, optional search, native fallback |

`lucifer19_battlecore` と `plamen06_steel` は同じ raw deck SHAでも policy SHAが異なるため、同一pairへ統合してはならない。candidateの `pair_id` は少なくとも次の値を含む。

```text
<base_asset_id>::deck=<raw_deck_sha[:16]>::policy=<base_policy_sha[:16]>
  ::candidate=<candidate_family>::config=<canonical_config_sha[:16]>
  ::meta=<meta_manifest_sha[:16]>::evaluator=<evaluator_sha[:16]>
```

候補のdeck mutationでは、deckの順序を正規化したmultiset SHAと、実行に使ったraw `deck.csv` SHAを両方保存する。60枚構造、コピー制限、engine legalityは候補ごとに再検査し、失敗時は候補を落としてnativeへ無言で置換しない。

## 観測済みnative baseline

同じbroad reference pool（24 opponent IDs、seat 0/1、faultをrequested denominatorへ含める）で、トップ3 native pairは96局screen後に384局を4 block実行した。engineにseed setterがないため、blockは独立stratified評価であり、同一乱数列のpaired比較ではない。

| pair | fast96 | block1 | block2 | block3 | block4 | pooled1536 |
|---|---:|---:|---:|---:|---:|---:|
| `tomatomato_archaludon` | 73/96 (76.042%) | 279/384 (72.656%) | 273/384 (71.094%) | 280/384 (72.917%) | 275/384 (71.615%) | 1107/1536 (72.070%) |
| `lucifer19_battlecore` | 70/96 (72.917%) | 266/384 (69.271%) | 282/384 (73.438%) | 273/384 (71.094%) | 282/384 (73.438%) | 1103/1536 (71.810%) |
| `plamen06_steel` | 76/96 (79.167%) | 275/384 (71.615%) | 272/384 (70.833%) | 278/384 (72.396%) | 277/384 (72.135%) | 1102/1536 (71.745%) |

Block artifact SHAs are respectively `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b`, `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e`, `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7`, and `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5`. The pooled ranking artifact is documented in `docs/evidence/strong-asset-top3-pooled1536-20260812.md` (SHA `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863`). The near-tie means the 96-game order is not a promotion oracle; every candidate comparison must include all three native baselines where runtime permits.

## 直接調整可能な surface

### tomato / Lucifer

- `_SETUP_ACTIVE_PRIORITY`: setup active Pokémon preference mapping.
- `_ICE_CREAM_HP_THRESHOLD`: matchup-specific Jumbo Ice Cream threshold.
- `score_*`, `score_option`, and `apply_overrides` are native decision stages, but changing their code is a research-copy operation only.
- No optional search path was observed. A score override must be applied only to main single-choice contexts and must return `None`/native on unknown context, conversion error, illegal index, timeout, or empty options.

### plamen

In addition to the above, source exposes an optional high-level search path:

| parameter | source default | eligibility / risk |
|---|---:|---|
| `USE_SEARCH` | env default `1` gated by `_SEARCH_OK` | only `MAIN`, `minCount=maxCount=1`, at least two options |
| `BEAM_CAND` | `6` | candidate breadth; runtime increases |
| `BEAM_MAXD` | `40` | rollout depth; runtime/fault risk |
| `BEAM_MARGIN` | `4000` | heuristic-vs-search tie threshold |
| `SP_BUDGET` | `2.0` seconds | hard per-decision budget; timeout must return heuristic |

Search calls `search_begin/search_step/search_end` and falls back to `_heuristic_agent`/`choose_options` on unavailable API or error. A candidate evaluator must measure search-disabled native plamen separately; otherwise any apparent gain can be a runtime artifact. Slow candidates must be quarantined from broad races and admitted only with a bounded kill/recycle policy.

## Candidate evaluator contract

The evaluator required by this audit is not yet a production or submission component. It must satisfy the following contract before a pilot is considered valid.

1. **Immutable descriptor.** Store base asset ID, base policy/deck/source SHAs, candidate config canonical JSON and SHA, candidate policy/deck SHAs, native surface audit SHA, pool manifest SHA, meta manifest/schedule SHA, evaluator implementation SHA, engine version, and timestamp. Recompute all bytes at worker start and fail closed on mismatch.
2. **Native-first invocation.** The candidate wraps the exact native policy. It may override only an eligible decision and must return the exact native action for all ineligible, malformed, illegal, exception, timeout, or no-option cases. Record override attempts, accepted overrides, fallback count, and illegal/timeout count.
3. **Observation boundary.** Only public actor observation is passed to score/value/search code. Opponent ID, hidden cards, future RNG, schedule weights, and source metadata remain scheduler-side and never enter policy features.
4. **Deck binding.** For a policy-only candidate, the raw deck SHA must equal the native baseline. A deck candidate gets a new legal 60-card raw SHA and mutation manifest; deck and policy changes are never silently combined.
5. **Common arena.** Candidate and native baseline run against exactly the same fixed reference IDs, seat protocol, max steps, evaluator timeout, and broad arena engine. The manifest schedule is sampled by split; candidate selection uses `META_DEV` only. `META_FINAL` is sealed until the final gate.
6. **Fault denominator.** Report requested, completed, wins, draws, losses, faults, timeout/step-limit status, fault rate, score rate, seat split, opponent split, and override coverage. Any fault, missing result, or worker loss remains in the denominator and blocks promotion when the gate requires fault 0.
7. **Progression.** Run 96 screen, 384 confirm, 768 stability, and 1536 final. Do not reject or promote on 24/48局. At each stage compare against the untouched native baseline and retain all candidate descriptors and block artifacts.
8. **No self-mirror.** If the requested opponent list contains the candidate itself, replace it with an explicitly declared fallback ID. Never let missing IDs become a self-mirror.

## 共通meta scheduleとの結合

Current manifest `e430f128...` has 102 rows, disjoint `META_TRAIN` (90), `META_DEV` (6), and `META_FINAL` (6). The generated evaluation schedule has quota 512 and 89 non-zero rows; the permission-filtered schedule has quota 256 but only `tomatomato_archaludon` is eligible. `lucifer19_battlecore` is in `META_FINAL`, and `plamen06_steel` is in `META_FINAL`, so neither is legally available to a training schedule under this immutable split.

This exposes a required pre-training decision: the current artifact is safe for evaluation, but it is not yet a complete BestKnown behavior-policy curriculum. It marks all three source pairs `usage_boundary=local_eval_only`; `tomato` and `Lucifer` additionally have census `training_usable=yes_bounded_local_teacher_collection`, while `behavior_allowed=false`. Therefore an AWR/on-policy collector must not infer behavior permission from `training_allowed=true`. It needs an explicit, source-backed behavior permission flag or a new manifest whose authority is reviewed. Until then, the safe route is native evaluation plus public-state/value experimentation using only permitted data; do not collect or replay external chosen actions as “on-policy” labels.

The schedule also has a weighting caveat: the component targets are `top_meta=.60`, `hard_negative=.25`, `diversity=.15`, but row-level weights are normalized across all 102 assets and are therefore near-uniform. This is a valid observed-pool proxy, not a claim that current meta prevalence is 60% for a single row. A future family-weighted schedule may be useful, but it must be a new immutable manifest with a new SHA and must not mutate this one in place.

## 最小 pilot設計

The lowest-risk native pilot is a policy-only candidate sweep with no deck change:

| stage | candidates | games per candidate | gate |
|---|---|---:|---|
| screen | tomato direct score/threshold small perturbations; Lucifer same; plamen search disabled plus one bounded search config | 96 | no runtime fault, legal actions, override coverage measured |
| confirm | top candidates from screen + untouched native baselines for all applicable pairs | 384 | candidate not below its own native baseline beyond the predeclared confidence margin |
| stability | at most 2 candidates per archetype | 768 | improvement on every independent block or predeclared aggregate CI; no seat/family collapse |
| final | one or two candidates | 1536 | repeated native excess, fault 0, package closure, rollback and stop gates |

For tomato/Lucifer, prefer score adapters or research-copy AST patching over broad neural distillation. Perturb one mechanism at a time (`_SETUP_ACTIVE_PRIORITY` or one matchup threshold) and use bounded config grids, not an untracked arbitrary rule rewrite. For plamen, run `USE_SEARCH=0` baseline and optional search candidates as separate policy identities; report runtime and step-limit faults independently.

## GO / NO-GO

### GO for bounded pilot

- candidate source copy is byte-closed and policy SHA changes exactly when expected;
- raw deck unchanged for policy-only candidate and legal for deck candidate;
- native fallback wrapper is present, tested, and observably used on rejected decisions;
- common `META_DEV` schedule and native baseline are fixed;
- 96-game smoke has fault 0 and no illegal action;
- timeout/recycle budget is bounded and slow search candidates are isolated.

### NO-GO for longrun or submission

- relying on 96-game rank alone;
- using `META_FINAL` to choose a candidate;
- treating `local_eval_only` as behavior or submission permission;
- collecting external chosen actions while `behavior_allowed=false`;
- missing raw deck/policy/config/meta/evaluator SHA;
- candidate changes deck bytes without a legal-deck/mutation artifact;
- candidate can bypass native fallback on exception, timeout, malformed observation, or illegal selection;
- any fault/step-limit/seat collapse at a stage advertised as promotion evidence;
- using the current all-rows schedule as if its row weights were a measured 60% top-meta distribution.

## AWR/value接続の不足

The native surface audit proves where a rule/search candidate can be adjusted; it does not provide public-state value targets, cross-fitted `V(s)`, behavior probabilities, or permission to use external actions. AWR needs a permitted `(public_state, chosen_action, return, cross-fitted V)` record stream. Native `score_option` values are heuristic scores, not calibrated advantages and must not be treated as `Q(s,a)` without a separate value audit. The current manifest can produce an evaluation opponent schedule, but cannot by itself authorize the required behavior-policy collection. This is the principal missing artifact before the requested long-running meta-fine-tuning loop can be marked GO.

## Reproduction / read-only checks

The audit inputs and baseline summaries were inspected with deterministic JSON/`sha256sum` checks. No CABT or training runner was launched for this evidence. Existing native surface tests were recorded in `docs/evidence/autonomous-native-tuning-surface-v1-20260813.md` as `4 passed`.
