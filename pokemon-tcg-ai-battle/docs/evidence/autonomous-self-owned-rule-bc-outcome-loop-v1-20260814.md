# 自己所有 Rule v0 → 結果重み付き Student 実ループ（2026-08-14）

## 結論

提出互換の自己所有 Rule v0 を subject として、broad pool 24 ID × 両seat × 2 repetition の 96 局を `workers=12` で実行し、4,814 件の actor-visible `RuleBCExample` と terminal WDL sidecar を収集した。勝ち局 1.5、引き分け 1.0、負け局 0.5 の結果重みで Student v0 を学習し、同一 seed/seat/opponent schedule の 96 局で評価したが、Student は 10/96（10.4167%）で、自己所有 Rule v0 baseline 12/96（12.5000%）を下回った。winner-heavy（3.0/1.0/0.1）も 6/96（6.2500%）、plain（1.0/1.0/1.0）も 10/96（10.4167%）であり、提出候補・384局・longrun へは昇格しない。

この結果により、Rule v0 の deterministic BC をそのまま outcome weighting する経路は、少なくともこの 96 局では改善ループとして失敗した。失敗は fault ではなく性能差であり、同じ候補を blind retry しない。次の候補は、既存 hard-negative と重複しない deck mutation または public-only search/target surfaceで、同じ 96→384 gate を使う。

## 実行条件

- subject policy: repository Rule v0 (`main.make_rule_agent`)
- subject deck: `deck.csv`（SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）
- opponent pool: `configs/meta_specialist/performance_first_broad_pool_v1.json` の24 ID、pool SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- schedule: 各 opponent × seat 0/1 × repetition 0/1、seed `20260814..20260909`
- evaluation workers: `12`、worker recycle policy: `16`、fault-inclusive denominator
- policy/deck/teacher/submission authority: 全 false。pool は local-eval-only であり、相手の private/action/teacher label は dataset に書いていない。

## 収集結果

Run root: `runs/final-sprint-autonomous/self-owned-rule-bc-v1-20260814/`

- games requested/completed/faulted: `96 / 96 / 0`
- decision examples: `4,814`
- dataset: `rule_bc_outcome_weighted.jsonl`、SHA `e024e723ebf8b8502a9e25f573e3f99596d641fd718d1e6600a3f11ea0a85b59`
- outcome weights: `outcome_weights.json`、SHA `73fa4de488c4782a4bfc79690b2fd6a0afd8562322fb5f27727c030b0db6c501`
- collection manifest: SHA `2d4fed9dd17ce0b7fa779707d1ff9eb76943cb720653eba753de61b9273a9ff4`
- collector source SHA: `40cad3071571e2992fec25f1ffcd2838d8db5acdfcb2df7f6fee5e82377b9f31`

Rule v0 baseline W/D/L は `12 / 0 / 84`（12.5000%）、seat別勝利は seat0=`5`、seat1=`7`。

## 学習候補

trainer は `src/mage_ptcg/student/model.py` の deterministic full-batch linear candidate scorerへ `example_weights` を追加した。重みは actor-visible RuleBCExample にだけ適用し、opponent policyや private opponent stateを teacher として利用していない。

| variant | win/draw/loss weight | model SHA | validation top1 | 備考 |
|---|---:|---|---:|---|
| outcome-weighted | 1.5 / 1.0 / 0.5 | `1f2b8efd25b0b9b34dbdec4cd81fed699316559775fcff0428b84dc192dfd7fa` | 75.61% | 初回80 epoch |
| plain | 1.0 / 1.0 / 1.0 | `a4b646f9c3c1cdce0c5beac75a4203d00c339858463ea54ea1c248e11557c540` | 75.61% | 40 epoch |
| winner-heavy | 3.0 / 1.0 / 0.1 | `880d669fc16c7436314db94de922270f04b87f4eac342ff20c4b7712c6e08b7e` | 75.48% | 40 epoch |

clean-room plain artifact は `runs/final-sprint-autonomous/self-owned-rule-bc-v1-20260814/student-v0-plain-submission-artifact/`、archive SHA `9aabb1edd0479fca825e214cd210103983f325ddd6cdb183a7081005d8405182`。`student-v0-rule-v0-fallback` として構築・検証され、Championや `main.py` の既定 agent は変更していない。

## 同一96局の policy screen

| policy | W/D/L | 勝率 | baseline比 | paired (baseline→candidate) |
|---|---:|---:|---:|---|
| Rule v0 baseline | 12/0/84 | 12.5000% | — | — |
| outcome-weighted | 10/0/86 | 10.4167% | −2.0833pt | LL78, WL8, LW6, WW4 |
| plain | 10/0/86 | 10.4167% | −2.0833pt | LL77, WL9, LW7, WW3 |
| winner-heavy | 6/0/90 | 6.2500% | −6.2500pt | LL82, WL8, LW2, WW4 |

全 screen は `DONE=96 / fault=0 / draw=0`。outcome-weighted screen manifest SHA `7f16dfe950390e0de60aef1463bc4a8bbed4f3ca008daf26896be93140a0b52c`、plain `753c7d22cb95d5f49c88b20194ad715379feef4db9b930950fc16325ecd43ec1`、winner-heavy `21ee941bfa4b876c9f86905f838638120060d33d20fda6347c25462982ab5906`。

## 実装・テスト

- collector: `scripts/collect_self_owned_rule_bc_v1.py` SHA `40cad3071571e2992fec25f1ffcd2838d8db5acdfcb2df7f6fee5e82377b9f31`
- weighting module: `src/mage_ptcg/student/outcome_weighting_v1.py` SHA `a6995c93c5e52e7134a9ae7c77004386d379ab704c96dfd8a5f3f67011f371ea`
- weighted trainer: `scripts/train_outcome_weighted_student_v1.py` SHA `12c08ea6c782543b9fa9920180be87cfa9c5584a1e3098b4423825ccf909b244`
- evaluator: `scripts/evaluate_self_owned_student_v1.py` SHA `f5476614ac18736c710ecb48a7f6761a3fc5ceebdd4a6a722245a0aec3ddc64e`
- weighted model support: `src/mage_ptcg/student/model.py` SHA `2ee5bfa5a9cd8bbc78d216b7377b49e3f7946ba47b88f9dcf4811e00d7489d11`
- focused tests: collection/weight/trainer/evaluator 合計 `15 passed`
- py_compile: PASS
- docs validator: PASS（13 canonical documents）
- `git diff --check`: PASS

No production evaluator/main/agents changes, no native teacher reuse, no CABT training authority, no commit/push/Kaggle submission.

## 追加比較: Rule v1（非採用）

同じ evaluator 入口に既存の提出互換 `make_rule_agent_v1` を差し替え、同一96 seed scheduleを2回 fresh rootで確認した。初回は `14/95 DONE` と `1 WORKER_ERROR`、retryは `10/95 DONE` と `1 WORKER_ERROR`。fault reasonはいずれも `DeckValidationError: deck must contain exactly 60 cards, got 0` で、異なるseedで再現した。欠落seedの単体直接実行はDONEだったため、これは勝敗へ変換できない並列/import raceとして扱う。fault-inclusive denominatorでRule v1の採用claimは閉じ、Rule v0 baseline超越とは判断しない。

- 初回 root: `runs/final-sprint-autonomous/self-owned-rule-bc-v1-20260814/rule-v1-screen-96/`、manifest SHA `98c7d35749e8576378063075997c3f8469bc65cf1ba5994196d6752263ba41c8`
- retry root: `runs/final-sprint-autonomous/self-owned-rule-bc-v1-20260814/rule-v1-screen-96-retry/`、manifest SHA `f0849302a0ed58e7c8b951a380ecd4a5722b9ede038cdaf24d936fad5cdf7669`
- generic evaluator SHA: `edea82d78496891b6c39f5bd3f8c2d1b417cb1365cc556916c118f960cd48983`

この比較は `workers=12` で行ったがfault-free admissibilityを満たさないため、384/longrun/Champion変更へ進めていない。Rule v1の同条件blind retryも停止する。
