# Strong Asset eligibility audit（2026-08-12）

## 結論

`opponents/pool_manifest.json` に登録された 102 件は、すべて元の
deck + agent をそのまま提出するための asset ではない。manifest の
`usage_boundary` は 102/102 件が `local_eval_only` であり、現在の pool が
直接保証するのはローカル評価だけである。

このため、BestKnown の区分は次のように分けて扱う。

| 区分 | 現時点の判定 | 根拠 |
|---|---|---|
| `EvaluationEligible` | 102 件が登録済み。ただし 101 件が smoke-ready、1 件は quarantine | pool manifest、`smoke_ok` |
| `TrainingEligibleByPermission` | 6 件（公開 3＋内製 3）の派生学習許可が判断記録にある | `docs/decisions/2026-08-05-archaludon-teacher-derivation.md`、内製 permission policy |
| `TrainingArtifactReadyNow` | 2 件（`tomatomato_archaludon`、`lucifer19_battlecore`） | 現行 source commit に結び付いた sealed teacher snapshot |
| `SubmissionEligible`（102 pool の元 pair） | **0 件** | 全件 `local_eval_only`、agent code の bundle 混入禁止、現行 package builder は root Rule v0 のみ |
| `SubmissionEligibleBestKnown` | pool 外の Rule v0 + root `deck.csv` が現行 packageable anchor | archive-only clean-room smoke pass の既存 evidence |
| `EvaluationBestKnown` / `TrainingEligibleBestKnown` / `BestKnownArchaludon` / `GlobalBestKnown` | **共通 arena の native pair ranking 完了まで未確定** | 既存の 24/48/96 局、外部 LB、student 評価は 102 native pair の順位ではない |

特に、`TrainingEligibleByPermission=6` と `TrainingArtifactReadyNow=2` は同じ意味
ではない。許可がある asset は fresh collection を行える候補だが、旧 worktree
由来の snapshot を現行 source と自動的に再利用してはいけない。逆に、現行 snapshot
があることだけで元の external agent を提出できることにもならない。

## 1. 監査対象と再現性

読み取り対象は次の通りである。

| artifact | SHA-256 |
|---|---|
| `opponents/pool_manifest.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| `configs/meta_specialist/performance_first_broad_pool_v1.json` | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` |
| `configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml` | `8adebfe8b886831c21883e5d7c4298afcd39827a5f22d75958e12c5ce8261f05` |
| `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` | `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` |
| `scripts/build_submission.py` | `bfae4baaa385bc6df7e816e0d8df06ed55716b9c0d00b8d2b69b15351257efec` |
| `scripts/build_performance_submission_bundle_v1.py` | `07d4835e91c5a658df1f9708ad54e6127343d4934911f52c9ae258cfce872906` |
| `docs/evidence/strong-asset-census-20260812.json` | `d0f4448b00de495efb049ae6233a7735a4e919a35103aeac13b72115891936b7` |
| `docs/status/chatgpt_context_pack_since_0356_2026-08-12.md` | `203ab6cb4954c031dd29973926ef0e6fe9746e7c432647e506ca66d9300ee32d` |

監査では学習、CABT、package build、Kaggle API、提出を起動していない。既存
manifest、source、permission、package 実装を read-only で突合した。

## 2. Evaluation の境界

### 2.1 pool 全体

`pool_manifest.json` の集計は次の通りである。

| 項目 | 件数 |
|---|---:|
| 登録 pair | 102 |
| `source=public` | 71 |
| `source=internal` | 31 |
| policy SHA unique | 58 |
| declared canonical deck SHA unique | 77 |
| raw `deck.csv` SHA unique | 79 |
| `usage_boundary=local_eval_only` | **102** |
| `smoke_ok=true` | 101 |
| `smoke_ok=false` | 1 |

従って「102 pair を同一 common arena で測る」こと自体は evaluation の範囲に
ある。ただし `smoke_ok=false` は性能 0 と同義ではなく、runtime 未解決の
quarantine である。ランキング runner はこの 1 件を明示的な diagnostic arm として
扱い、fault を隠して通常の ready pool に混ぜてはならない。

### 2.2 R7 の扱い

`public_archaludon_cinderace_r7` は次の identity を持つ。

| 項目 | 値 |
|---|---|
| raw deck SHA | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| policy SHA | `c08588467c3faa2cbc748703acc8e7099c6362c32747c84cb2cec8131d6a4ca3` |
| `usage_boundary` | `local_eval_only` |
| `smoke_ok` | `false` |
| 既存 fixed-six 参考値 | 62/96、64.58%、fault 0（`runs/meta-specialist-strength/teacher-archaludon-r7-fixed6-seed9700000-96.json`） |

この 62/96 は「測定上の暫定ローカル上限」であり、102 native pair の共通 arena
GlobalBestKnown ではない。R7 は smoke failure と local-eval 境界のため、現行の
training source、promotion、submission のいずれにも使わない。旧 worktree に
R7 の `training-local` teacher manifest が残るが、それは現在の effective status を
上書きしない（旧 source commit、smoke quarantine、現行 status の再確認が必要）。

## 3. Training の二層判定

### 3.1 permission 上は派生学習が許可された 6 asset

2026-08-05 の判断記録は、元の agent `main.py` を提出 bundle に入れることを禁止
したまま、行動を蒸留した別 checkpoint の初期値に使える asset を明示している。

| asset | source | policy SHA | raw deck SHA | permission の意味 |
|---|---|---|---|---|
| `tomatomato_archaludon` | public | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` | `training-local` 派生 |
| `lucifer19_battlecore` | public | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | `training-local` 派生 |
| `plamen06_steel` | public | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | `training-local` 派生 |
| `ozawa_grimmsnarl_v2` | internal | `48621429950e717e8dbd2928fd58876ee73b6cd4eb397dc8f629899a41ce2014` | `92b92bac9f9163ecff933b3dc39294d2cc154c8684f3c8497877661419ebc59d` | 内製 `training-local` |
| `ozawa_rocket_v2` | internal | `a3b9cc59b82ebb34afafed2fd52053f1769f85d7d55b9452fe21bcf0e791c83b` | `0c4a1f66c862ca1d2391b780c5622cbdf76a7845f89259d47290c05021384fbb` | 内製 `training-local` |
| `nihei_alakazam` | internal | `a502b37132b5558fdd329a40337c2cc8a0b27098ed278b249b7c2222fd2df711` | `167d43335013f7b68441356d750dab335088171c1ab929e083deb85a2c79e5b1` | 内製 `training-local` |

公開 3 件の derivation qualification は、元 agent の redistribution/submission
許可ではない。内製 3 件についても、permission policy は評価・training data
generation を許す一方、`submission_bundle` と external redistribution を禁止し、
source snapshot、commit pin、security、legality、state leakage、runtime isolation
を要求している。

### 3.2 現行 source に再現可能な sealed artifact は 2 asset

現行 repository root の Strong Asset 主線で、source commit と policy SHA が pool
manifest に一致し、96 局の sealed teacher snapshot があるのは次の 2 件である。

| asset | snapshot manifest | manifest SHA | games | records | seat | outcome | permission ID |
|---|---|---|---:|---:|---|---|---|
| `tomatomato_archaludon` | `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/teacher_dataset_manifest.json` | `b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff` | 96/96 | 5,146 | 48/48 | 60W/36L | `441a6b83373c9ff2e7af765bb1d7e926bc5af9b3967537dc5d0be8d842956ca0` |
| `lucifer19_battlecore` | `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-96-strong-20260812/teacher_dataset_manifest.json` | `d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84` | 96/96 | 5,102 | 48/48 | 72W/24L | `83074da078f50149081a73c740803476f5b548d4c795f533b3f3e800ad74a70f` |

どちらも manifest 内では `teacher_usage_boundary=local_eval_only` のままだが、
permission subobject の `allowed_usages=[\"training-local\"]` と issuer が sealed
されている。これは「この snapshot から public-state / action / value の派生物を
研究用に作る」根拠であり、元の `main.py` を package にコピーする根拠ではない。

### 3.3 再検証が必要な旧 artifact

`runs/from-worktree/meta-specialist-canonical/...` には、R7、`nihei_alakazam`、
`ozawa_grimmsnarl_v2`、`ozawa_rocket_v2` 等の古い permission manifest が残る。
それらは permission ID、source commit、records 状態が現行 Strong Asset 主線と
異なる。特に R7 は current pool で smoke false であり、旧 manifest の
`allowed_usages=[\"training-local\"]` だけで再利用してはならない。これらを使う場合は、
pool manifest の現在 SHA、policy/deck bytes、decision issuer、技術検証、snapshot
split を同時に再確認して新しい immutable snapshot を作る必要がある。

したがって今回のランキング後に training 起点を選ぶときは、次の順にする。

1. 102 native ranking で性能順位を確定する。
2. 上位 pair が 6 asset の permission-qualified 集合に含まれるか確認する。
3. 現行 source SHA を再固定し、必要なら fresh 96 局以上の on-policy snapshot を作る。
4. value / AWR / filtered BC は派生 artifact として扱い、元 agent code を package に流用しない。

## 4. Submission の境界

### 4.1 現行 pool asset は元 pair として 0 件

`opponents/*/SOURCE.md` と permission policy の共通ルールは、local bench 用の
`main.py` をそのまま再配布・提出しないことである。公開 asset の SOURCE は
「local, offline evaluation」「not redistributed」「never submitted as-is」を明記し、
内製 policy はさらに「agent logic must not be copied into a submission bundle without
separate explicit approval」とする。

従って、102 件の native pair について `SubmissionEligible=true` と自動判定できる
ものは 0 件である。これは性能ランキングから除外するという意味ではなく、
`SubmissionEligibleBestKnown` と `EvaluationBestKnown` を混同しないという意味である。

### 4.2 現行 package builder が監査できる範囲

`scripts/build_submission.py` の `RUNTIME_PATHS` は次の root Rule v0 の 4 ファイル
だけである。

~~~text
main.py
deck.csv
agents/__init__.py
agents/rule_agent.py
~~~

`scripts/build_performance_submission_bundle_v1.py` も、(a) Rule v0 + root deck の
archive build / clean-room smoke と (b) Wave6 V4 + Archaludon の identity audit を
行うだけで、`opponents/<asset>/main.py` を arbitrary candidate として package 化する
機能を持たない。V4 側も production entrypoint、card vocabulary、dependency closure
が未接続のため `submission_ready=false` である。

既存 evidence の Rule v0 archive は 2 game clean-room smoke、fault 0、illegal 0 を
通過している。これは `SubmissionEligibleBestKnown` の現在の anchor だが、102 pool
の native asset ではない。

anchor の source-bound identity は、combined policy SHA
`750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`、root deck SHA
`2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、archive
`runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz`
SHA `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a` である。
この値は pool asset の性能順位ではなく、現行提出経路が閉じていることの証拠である。

`waterbox_search_v3` は SOURCE 上「提出版に戻せば自己完結」と記載される一方、pool
に入っている copy は bench 用探索 budget `0.05` で、`usage_boundary=local_eval_only`
である。提出版 budget、source permission、bundle closure を別途固定・承認するまで、
現行 pool pair として `SubmissionEligible` には数えない。

### 4.3 fast96 native ranking の暫定観測

eligibility 監査後、smoke-ready かつ比較的高速な 96 asset を native pair として
96 games/asset（24 reference opponents、両 seat、2 repetition）測定した。これは
permission や提出可否を変更するものではなく、slow 5 asset と R7 diagnostic を
別途測る前の provisional ranking である。

| 順位 | asset | W/L/D/F | score |
|---:|---|---:|---:|
| 1 | `plamen06_steel` | 76/20/0/0 | 79.17% |
| 2 | `tomatomato_archaludon` | 73/23/0/0 | 76.04% |
| 3 | `lucifer19_battlecore` | 70/26/0/0 | 72.92% |
| 4 | `aristophanivan_multiply` | 65/31/0/0 | 67.71% |
| 5 | `nihei_alakazam` | 65/31/0/0 | 67.71% |

一次 artifact は `runs/meta-specialist-asset-ranking-primary-fast96-20260812/asset_ranking.json`
（SHA `7ad461caebd8bc8b21b1600f1719d8107f4654c0b2236c8ddcb57996f8b94b29`）である。
全 9,216 games のうち 9,207 完走、draw 8、fault 9（すべて
`medal_0019_df6f7443` subject の `STEP_LIMIT`）だった。slow 5 asset
(`kinoshita_pimc_search`, `ozawa_metal_psychic_search`, `water_box_search`,
`waterbox_search_v3`, `tientrum_alakazam_search`) と R7 はこの artifact に含まれず、
GlobalBestKnown は未確定のままである。上位 3 件はいずれも permission/identity
上の別 pair であり、Lucifer student の成績を native teacher の成績へ転記していない。

## 5. BestKnown 判定への入力としての扱い

| 名称 | 現在の入力 | まだできない主張 |
|---|---|---|
| `EvaluationBestKnown` | 102 native pair（R7 は diagnostic/quarantine と明記） | R7 の 62/96、外部 LB、student 勝率を GlobalBestKnown と確定すること |
| `TrainingEligibleBestKnown` | permission-qualified 6 pair。そのうち fresh sealed snapshot は 2 pair | Lucifer の BC が失敗したから strong asset 主線全体が失敗した、または未測定 pair が弱いと断定すること |
| `SubmissionEligibleBestKnown` | 現在は pool 外 Rule v0 + root deck | local_eval_only の native pairをそのまま submit できると扱うこと |
| `BestKnownArchaludon` | native common-arena ranking 完了後に deck/policy pair で決定 | Wave6、Rule v0、R7、student を native ranking の代用にすること |
| `GlobalBestKnown` | 102 pair の共通 arena ranking完了後に決定 | archetypeごとの別 pool、異なる deck、異なる局数を単純合算すること |

ランキングの identity は常に次の tuple を保存する。

~~~text
(asset_id, policy_sha256, raw_deck_sha256, canonical_deck_hash,
 source, usage_boundary, permission_manifest_id, smoke_ok)
~~~

同じ 60 枚 deck でも policy が違う pair は別 identity であり、`tomatomato_archaludon`
と R7、`lucifer19_battlecore` と `plamen06_steel`、内製 frozen pair の deck 重複を
成績・権限ごとに混ぜない。

## 6. 推奨する effective gate

native ranking runner が出す machine-readable summary には、少なくとも次を含める。

~~~text
evaluation_registered       # pool manifest に存在
evaluation_smoke_ready      # smoke_ok=true かつ runtime smoke pass
training_permission         # 明示 decision/permission のみ true
training_snapshot_ready     # 現行 source SHA と sealed snapshot が一致
submission_permission       # explicit bundle approval がある場合だけ true
package_closure_verified    # builder/clean-room が通った場合だけ true
~~~

`submission_eligible = submission_permission && package_closure_verified` とし、
`local_eval_only`、`smoke_ok=false`、欠落した permission、旧 source commit、未検証の
dependency closure のいずれかがあれば fail-closed にする。評価順位のために
`evaluation_registered` を保持しつつ、提出順位には混ぜない。

## 7. 再開条件

1. 102 pair（R7 は別 diagnostic flag）を同じ common arena、同じ seat/seed/局数で
   native pair として測る。
2. 96 → 384 → 768 → 1536 の順で、上位 pair だけを拡張する。
3. 各候補の policy/deck/source/permission SHA を ranking artifact と再照合する。
4. 上位 pair が permission-qualified なら、current source SHA で fresh on-policy
   data を生成する。
5. hard-label/outcome-weighted BC 以外の public-state value + AWR/filtered BC、
   必要なら public-only search/Q、deck optimization を一つずつ比較する。
6. candidate が対応する native BestKnown を明確に越えた場合のみ、package permission
   と clean-room closure を別工程で審査する。

この監査では BestKnown を更新していない。ランキング完了前の暫定値を採用せず、
元の Rule v0 提出物、Champion、remote branch、Kaggle submission も変更していない。
