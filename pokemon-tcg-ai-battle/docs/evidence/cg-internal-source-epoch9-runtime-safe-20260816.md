# cg internal source epoch9 / runtime-safe Metal family — 2026-08-16

## 結論

新しい meta source の獲得方法として、許可済み Git ref の未使用 historical snapshot を static-only で取り込み、runtime-safe な visible-state behavior familyへ固定変換する経路を検証した。raw snapshot は P1 対の bounded smoke で `6/8` が `parent_timeout` となったが、既存の探索停止変換を新しい policy 表形状へ拡張した4 variant familyは `8/8 DONE・fault0` で完了した。

この結果は source generation と runtime safety の前進であり、性能改善や独立性の証拠ではない。4件は同一 branch・同一 source commit・同一 canonical deckからの派生であるため、作者・deck・policy lineageを独立4件として数えない。CEM、DEV／FINAL holdout、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は行わない。

## raw source intake

履歴参照は network／checkout／import／CABTなしの `git show` 読み取りで行った。

- source epoch: `internal-20260816-epoch9-history`
- seed namespace: `internal-cg-seed-20260816-epoch9-history`
- ref: `refs/remotes/origin/agents/ozawa-metal-psychic-search`
- accepted snapshot: 4件（`641519c7a215`、`65cabdfef7c3`、`b815464f206b`、`dec382a2ce57`）
- 全件の branch: `agents/ozawa-metal-psychic-search`
- 全件の canonical deck SHA: `e32f681f9ca9505b17bcd1a48acab223d0ae63b0b40e169cf18d926333781c1f`
- raw pool SHA: `0378030b4689159cecbba9fe5188365630add833df52d7dd64248feaea704bee`
- raw fresh meta SHA: `e5c80e66c9a1bc9eec222b9d04cce21859a89aac8c709a0e2f381d75cf8a416d`
- intake report SHA: `39f891efed67e8b01a25cfbb7ba88c66bd125603f51e3cecf448134eab8532e4`

raw sourceのpolicyには探索実装が残っており、source自体の static findings は空だったが、runtime上の bounded性を満たさなかった。

| raw smoke | 局数 | DONE | fault | 判定 |
|---|---:|---:|---:|---|
| P1対、両seat、120秒 timeout | 8 | 2 | 6 | `RUNTIME_SMOKE_FAIL_TIMEOUT` |

raw smoke summary SHAは `501862362c60f590f0eb57ae1a50503448106bc7834270eeb4acd4d27b1e0c65` である。faultは `parent watchdog exceeded game timeout grace` で、faultを勝率へ換算しない。

## runtime-safe generation

実装は `src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py` の `_replace_metal_behavior` に、旧 snapshotの `PRINPLUP`を含む priority tableと、epoch9 snapshotの `PRINPLUP`なし tableを別々の exact shapeとして追加した。複数shape一致は従来どおり fail-closed とし、探索を構造的に停止する `_replace_metal_runtime_safe_behavior`（`SEARCH_NUM_WORLDS = 0`、local fixed budget `0.0`）を経由した。

base source:

- candidate: `internal_ozawa-metal-psychic-search_dec382a2ce57`
- source commit: `dec382a2ce5788749ac24b30aea03625f1320489`
- source policy SHA: `6994181f09f78b758584368115c24291e2a1b8bb70f3f2f606313c7b509ba4fe`
- canonical deck SHA: `e32f681f9ca9505b17bcd1a48acab223d0ae63b0b40e169cf18d926333781c1f`

sealed family:

- root: `runs/cg-internal-source-epoch9-metal-runtime-safe-20260816/`
- source epoch: `internal-20260816-epoch9-metal-runtime-safe`
- seed namespace: `internal-cg-seed-20260816-epoch9-metal-runtime-safe`
- variants: `RULE_ONLY_PIPLUP_FIRST`、`RULE_ONLY_METAGROSS_FIRST`、`RULE_ONLY_RECEIVER_FIRST`、`RULE_ONLY_LUCARIO_PLAN_FIRST`
- accepted: 4件、全件 `STATIC_AND_EXACT_60`、`visible_state_only`、`local_eval_only`
- pool SHA: `016a18aeff4d3a707fd4e907851acfc5dfb46d461fddd0646397b3b5c07867f6`
- fresh meta SHA: `cb6a82456aa2d170973f6b288230468fe071179ee4d0c4b3062d5e21a12a3e31`
- split SHA: `afa414b826f290d1903f7f0993004193e0899ea2f38afcc675598f4998208d0`
- meta manifest SHA: `75975df8da9b1e348761e61c89fff749988f08a6ec6648d83e9a86cd19a646db`
- intake report SHA: `8f1c034a174c8fbfba703c285b930c66ea387427db3e3a0b4e2289ffde285515`

派生 policy SHAは次の4件で、既存 pool／指定 scan rootsとの identity衝突は検出されなかった。

| variant | policy SHA |
|---|---|
| `RULE_ONLY_PIPLUP_FIRST` | `8973fdb57e2029c884ac0ff5e86b17d63e83cb00473ae381c46c20b67571bc79` |
| `RULE_ONLY_METAGROSS_FIRST` | `63af5e58592ea29a96f76d34d0538d89f4de4c7bbadc83ff2e7096b04dc1106b` |
| `RULE_ONLY_RECEIVER_FIRST` | `7c9f1a132418690b20edd80287bef82c294e1f2dea89485baa3d30718d5ee75a` |
| `RULE_ONLY_LUCARIO_PLAN_FIRST` | `2423bcc2af334d9c63fa1d2d2a7dbafd55a6ec7c0a317560f142bb5a5bb8437f` |

## runtime smoke

P1 package `runs/cg-self-owned-cg-policy-cem-v1-p1-source/package`（policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`）を候補に固定し、4 source × 両seat × 1局、seed `202608991`、120秒 timeoutで実行した。

| 条件 | 局数 | DONE | fault | 結果 |
|---|---:|---:|---:|---|
| runtime-safe family、両seat | 8 | 8 | 0 | 5W-3L、fault rate 0%、8.119秒 |

smoke summary SHAは `cba52369bb4a23f590d472d1e8d4538173aa36cb1a9f289115d39266cc524a32`、evaluator implementation SHAは `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08` である。

## 判定と次の条件

- source generation: `PASS`
- raw source runtime: `FAIL_TIMEOUT`
- runtime-safe derived family: `PASS`（bounded smoke fault0）
- source independence: `INSUFFICIENT`（同一branch／同一deck／同一base lineage）
- CEM／DEV／FINAL／BestKnown: 未実施／不変
- authority: `training_allowed=false`、`promotion_allowed=false`、`submission_allowed=false`、`longrun_allowed=false`

次にCEMへ進めるには、別作者または別policy/deck lineageを先に確保し、今回の4 variantを4独立sourceとして水増ししないこと。新しい family は runtime-safe source供給器の検証用として保持し、性能holdoutへは未接続のままとする。

