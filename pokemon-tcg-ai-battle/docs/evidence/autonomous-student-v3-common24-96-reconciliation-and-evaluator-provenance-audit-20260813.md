# Student v3 common24 96局 reconciliation / evaluator provenance監査

## 結論

2026-08-13 時点の一次 artifact を formal reconciler で再検証した。Student v3
`theta0` は native `tomatomato_archaludon` に対して **7/96 対 66/96**、AWR は
**3/96 対 66/96**であり、差はそれぞれ **-61.458pt / -65.625pt**だった。全 arm
fault 0、exact 24 opponent、両 seat、各2反復、requested denominator 96、seed 集合
`13000000..13000095` を満たす。

一方、現行 evaluator v1 の永続 artifact は `timeout_seconds` と `runner_ref` を
ledger / game sidecar / manifest / summary のいずれにも保存しない。両値は実行時 payload
では使用されるが、事後に一次実行 receipt から復元できない。reconciler はこの制約を
隠さず、request 内の宣言だけを cross-bind する
`timeout_binding=request_and_arm_declaration_only` として扱う。したがって今回の結果は
common24 の結果・identity・seed・seat・分母については厳密だが、timeout / injected
runner の実行時 provenance は宣言ベースであり、ledger 自身による観測証明ではない。

この監査では production、evaluator、candidate runner、native runner を編集していない。
CABT、学習、package build、Champion変更、commit、push、提出も実行していない。

追加の read-only 監査では、native population の正典である meta-distribution v1 と
dynamic META_TRAIN curriculum iteration 0 を独立再生成・formal reloadした。既存 common24
は `META_TRAIN=20 / META_DEV=0 / META_FINAL=4` であり、4件の `META_FINAL` を含むため、
96局 ledger 全体を次iterationの hard-negative feedbackや `LONGRUN_READY` の
`META_DEV` evidenceへ流してはならない。正式 curriculum はこの境界を守り、20件の
`META_TRAIN` のみに合計96 exposureを与え、`META_FINAL` 4件は quota / weightとも0、
teacher behavior eligibleも0、authorityも全てfalseだった。

## Formal reconciliation artifacts

| 比較 | request file SHA-256 | reconciliation file SHA-256 | semantic SHA-256 |
|---|---|---|---|
| theta0 vs native | `13ec6ab7a8206b6a7a820cfcf8699c69d1324ab22fd102a04533e89db458c728` | `81bfda4621ec1fc6952dd781e04a569d41c5e5389e5dabe821f2ecce03fab0bf` | `a46cfef693951cd809a7d8fcd546e6853521b35da43482bb043a361f5bbc6bd4` |
| AWR vs native | `b6fc34b2eefed0fe74b0ddddf547acc6b4b5dce365cb0303fa94cfd571a9f889` | `6482a3f613af330985bc0d5bcb829884f744a20433cc6e612d71aae189f38b93` | `10fd95ea939b332c2c49ed3e1687040a5e186cb7f3bdf3dc6431f2b56518bae5` |

Artifact paths:

- `runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/request.json`
- `runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/reconciliation.json`
- `runs/final-sprint-autonomous/student-v3-awr-native-common24-reconcile-96-v2/request.json`
- `runs/final-sprint-autonomous/student-v3-awr-native-common24-reconcile-96-v2/reconciliation.json`

AWR request と reconciliation は共有 workspace 上の同時生成 race を一度検出した。
request の最終 bytes を formal API で検証した後、reconciliation を `--overwrite` で
atomic 再生成し、最終 artifact 内の `request.sha256` が上表の request file SHA と一致
することを確認した。以後、親 lane は同 path を触らない協調状態で最終 SHA を固定した。

## 96局の結果

fault は requested denominator に残す契約だが、今回は全 arm で 0 件だった。

| arm | W-D-L-F | score / 96 | score rate | 95% Wilson interval |
|---|---:|---:|---:|---:|
| Student v3 theta0 | 7-0-89-0 | 7 | 7.292% | 3.577%–14.293% |
| Student v3 AWR | 3-0-93-0 | 3 | 3.125% | 1.068%–8.789% |
| native Tomato | 66-0-30-0 | 66 | 68.750% | 58.908%–77.149% |

Wilson interval は ledger の二値 W/L を再集計した記述統計であり、reconciler の gate
判定値ではない。CABT engine は common-RNG seed setter を保証せず
`engine_seed_supported=false` のため、同じ seed ordinal を持っていても paired-game
検定としては扱わない。

### nativeとの差

| candidate | overall delta | seat 0 delta | seat 1 delta | win差 |
|---|---:|---:|---:|---:|
| theta0 | -61.458pt | -58.333pt | -64.583pt | -59 |
| AWR | -65.625pt | -66.667pt | -64.583pt | -63 |

### seat別

| arm | seat 0 | seat 1 | signed gap (seat0 - seat1) |
|---|---:|---:|---:|
| theta0 | 4/48 = 8.333% | 3/48 = 6.250% | +2.083pt |
| AWR | 0/48 = 0.000% | 3/48 = 6.250% | -6.250pt |
| native Tomato | 32/48 = 66.667% | 34/48 = 70.833% | -4.167pt |

AWR の劣化は片側 seat だけで native 差が生じた結果ではない。seat 0 / 1 のいずれも
native を 64pt 以上下回り、AWR 自身の勝ちは seat 1 の3件だけだった。

### opponent別 W/4

| opponent | theta0 | AWR | native |
|---|---:|---:|---:|
| `aman_crustleaware_fighting` | 0 | 0 | 3 |
| `aristophanivan_multiply` | 0 | 0 | 3 |
| `aristophanivan_probabilistic` | 0 | 0 | 2 |
| `biohack44_crustlecounter2` | 0 | 0 | 4 |
| `dashimaki360_crustlecounter` | 0 | 0 | 3 |
| `ferozahmedds_solution` | 1 | 0 | 3 |
| `harukiharada_crustle` | 1 | 1 | 4 |
| `itsuki9180_lucario_jp` | 0 | 0 | 2 |
| `kiyotah_abomasnow` | 1 | 0 | 4 |
| `kiyotah_dragapult` | 0 | 0 | 1 |
| `kiyotah_iono` | 1 | 0 | 2 |
| `kojimar_lucario` | 0 | 1 | 3 |
| `kokinnwakashuu_lucario_search` | 0 | 0 | 3 |
| `lucifer19_battlecore` | 0 | 0 | 3 |
| `masamikobayashi_garchomp` | 0 | 0 | 1 |
| `medal_0001_77a53ffc` | 2 | 0 | 4 |
| `naoto714_kangaskhan` | 0 | 0 | 3 |
| `naoto714_slowking` | 0 | 1 | 4 |
| `naoto714_ursaluna` | 0 | 0 | 1 |
| `official_random` | 1 | 0 | 4 |
| `pilkwang_lucario_alakazam` | 0 | 0 | 1 |
| `plamen06_steel` | 0 | 0 | 2 |
| `prvsiyan_grimmsnarl` | 0 | 0 | 3 |
| `rauffauzanrambe_advanced` | 0 | 0 | 3 |

native は24 opponentすべてに最低1勝した。AWR は21/24 opponentで0勝、theta0 は
18/24 opponentで0勝だった。これは特定1～2 matchupだけの局所崩壊ではなく、現行
Student v3 policy と native behavior 間の広い性能差として整合する。ただし96局は
screen であり、各 opponent は4局しかないため、個別 matchup順位は確定扱いしない。

## Native population / meta-distribution v1 の固定点

### 一次 artifact と byte-exact 再生成

| artifact | path | file SHA-256 |
|---|---|---|
| meta distribution | `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| static schedule | `runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json` | `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a` |

manifest は102 pairを `META_TRAIN=90 / META_DEV=6 / META_FINAL=6` へ固定する。
`load_meta_distribution_manifest_v1(..., verify_sources=True)` で全sourceを再hashし、独立した
`/tmp` rootへ次のコマンドで再生成したところ、manifest / scheduleの双方が上表と
byte-exactに一致した。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_meta_distribution_manifest_v1.py \
  --output /tmp/mage-meta-distribution-audit/manifest.json \
  --eval-quota 512 \
  --train-quota 256
```

manifestが直接拘束するsource SHAは次の通り。

| role | source | SHA-256 |
|---|---|---|
| census | `docs/evidence/strong-asset-census-20260812.json` | `d0f4448b00de495efb049ae6233a7735a4e919a35103aeac13b72115891936b7` |
| native ranking | primary fast96 | `7ad461caebd8bc8b21b1600f1719d8107f4654c0b2236c8ddcb57996f8b94b29` |
| native ranking | top3 confirm384 block1 | `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b` |
| native ranking | top3 confirm384 block2 | `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e` |
| native ranking | top3 confirm384 block3 | `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7` |
| native ranking | top3 confirm384 block4 | `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5` |
| native ranking | R7 diagnostic | `7787f191ffdfd559d26a29b8365974c7e384a21950e5d8068aef2bd1137785ac` |

### META_TRAIN / DEV / FINAL の用途境界

`META_DEV` のexact IDは次の6件。

- `kiyotah_lucario`
- `nihei_megalopunny`
- `ozawa_crustle_v2`
- `skarin_dragapult`
- `sue124_alakazam`
- `yaroslav_crustleaware_lucario`

`META_FINAL` のexact IDは次の6件。

- `aristophanivan_multiply`
- `dashimaki360_crustlecounter`
- `lucifer19_battlecore`
- `nihei_alakazam`
- `ozawa_starmie`
- `plamen06_steel`

用途は次のように分離する。

| split | 用途 | dynamic exposure | candidate選択への使用 |
|---|---|---:|---|
| `META_TRAIN` | opponent rollout、hard-negative更新、curriculum再配分 | 可（permission内） | 可 |
| `META_DEV` | native差、fault、seat、独立seed blockによる開始/継続gate | 0 | 可。ただし学習feedbackへ戻さない |
| `META_FINAL` | 最後の一回の隔離評価 | 0 | final gate以前は不可 |

static scheduleは `META_TRAIN_EVALUATION` が89 rows / quota 512、
`META_TRAIN_PERMISSION_FILTERED` が1 row（`tomatomato_archaludon`）/ quota 256である。
前者の `local_eval_only` rowsはopponentとしてのローカル対戦に限って使用でき、teacher
action、label、behavior policyを許可しない。後者でTomatoに `training_allowed=true` が
あっても、現meta rowは `behavior_allowed=false` かつ `usage_boundary=local_eval_only` なので、
behavior sourceにはならない。

なお static `meta_schedule.json` 自体は meta manifest path / file SHAを内包しない。
したがってconsumerはschedule単独をauthorityにせず、下記dynamic artifactのように
manifestとscheduleの双方を独立sourceとしてSHA拘束する必要がある。

## common24 と固定splitの交差

common24をmeta manifestのexact opponent IDへjoinした結果は次の通り。

| split | common24内件数 | IDs |
|---|---:|---|
| `META_TRAIN` | 20 | `aman_crustleaware_fighting`, `aristophanivan_probabilistic`, `biohack44_crustlecounter2`, `ferozahmedds_solution`, `harukiharada_crustle`, `itsuki9180_lucario_jp`, `kiyotah_abomasnow`, `kiyotah_dragapult`, `kiyotah_iono`, `kojimar_lucario`, `kokinnwakashuu_lucario_search`, `masamikobayashi_garchomp`, `medal_0001_77a53ffc`, `naoto714_kangaskhan`, `naoto714_slowking`, `naoto714_ursaluna`, `official_random`, `pilkwang_lucario_alakazam`, `prvsiyan_grimmsnarl`, `rauffauzanrambe_advanced` |
| `META_DEV` | 0 | 該当なし |
| `META_FINAL` | 4 | `aristophanivan_multiply`, `dashimaki360_crustlecounter`, `lucifer19_battlecore`, `plamen06_steel` |

したがって current common24 96局には2つの用途制約がある。

1. broad descriptive screenとしては有効であり、Student崩壊の観測は保持する。
2. `META_FINAL` 4件を既に含むhistorical screenを、candidate選択、curriculum outcome、
   AWR/value target、`META_DEV` start gateへ転用しない。

`META_DEV` が0件なので、common24をsubset化しても `LONGRUN_READY` evidenceにはならない。
次のgateは上記exact `META_DEV` 6件を対象とする新規ledgerでなければならない。

## Dynamic META_TRAIN curriculum iteration 0

### 正式 artifact と独立再現

| 項目 | 値 |
|---|---|
| path | `runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json` |
| file SHA-256 | `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a` |
| semantic curriculum SHA-256 | `df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4` |
| schema | `meta-specialist-dynamic-meta-train-curriculum-v1` |
| purpose | `META_TRAIN_OPPONENT_ROLLOUT_RESEARCH_ONLY` |

正式artifactとは別に `/tmp` へ同一入力から再生成し、file SHA、semantic SHA、全bytesが
正式artifactと一致した。再現コマンドは次の通り。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_dynamic_meta_train_curriculum_v1.py \
  --repo-root "$PWD" \
  --meta-manifest runs/final-sprint-autonomous/meta-distribution-v1/manifest.json \
  --meta-schedule runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json \
  --broad-pool-config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --output-manifest /tmp/mage-dynamic-curriculum-audit/manifest.json \
  --quota 96 \
  --seed common24-dynamic-curriculum-v1 \
  --iteration 0
```

artifactが拘束する4 sourceは次の通り。

| role | file SHA-256 |
|---|---|
| `meta_distribution_manifest` | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| `meta_schedule` | `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a` |
| `common24_broad_pool_config` | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` |
| `opponent_pool_manifest` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |

formal verifierはsource path/file SHAを再hashし、同じbuilderでimmutable再構築して全manifest
semanticを比較する。iteration 0の実集計は次の通り。

| split | selected | nonzero exposure | quota | teacher behavior eligible |
|---|---:|---:|---:|---:|
| `META_TRAIN` | 20 | 20 | 96 | 0 |
| `META_DEV` | 0 | 0 | 0 | 0 |
| `META_FINAL` | 4 | 0 | 0 | 0 |

training familyは12。`META_FINAL` entriesは履歴上のselected setとしてidentityを保持するが、
reasonは `held_out_split_zero_exposure`、weight / quota / training exposureは全て0である。
`training_authority / promotion_authority / submission_authority /
external_execution_authority` は全てfalseである。

negative integrationとして `lucifer19_battlecore` のoutcomeをiteration ledgerへ渡し、
`held-out opponent appeared in iteration ledger` でfail-closedになることも確認した。
iteration 1以降はprevious curriculumのpath/file/semantic SHAとcanonical JSONL outcome ledgerの
path/file SHAを両方必須とし、非連続iteration、missing lineage、unknown opponent、heldout row、
closed schema違反を拒否する。

### Consumer統合で必要な追加oracle

正式iteration 0自体はSHA拘束されformal GREENだが、次の境界はdownstream consumer側で
必ず確認する必要がある。

- selected 24のpolicy SHAとusage boundaryは meta manifest / opponent poolで24/24一致し、
  pool側は24/24 `smoke_ok=true` だった。
- meta manifestの `deck_sha256` はraw `deck.csv` file SHA、pool manifestの
  `canonical_deck_hash` は60-card multisetのcanonical SHAで別hash domainである。このため
  24/24で文字列が異なること自体はdriftではない。consumerは両者を誤って等値比較せず、
  raw bytes SHAとcanonical deck SHAを別fieldとして保持し、実行ledgerではmeta側raw file
  SHAをexact joinする。current common24 ledgerはこのraw SHAへ一致している。
- curriculum entry単体は `opponent_id / family / split / weight / quota` を中心とし、deck / policy
  SHAを行内に複製しない。runnerはbound meta manifestからexact pair identityを解決し、実際の
  opponent bytesを再hashしたreceiptをledgerへ保存しなければならない。
- current iteration outcome schemaは
  `opponent_id / candidate_score / fault / seat` の4 fieldだけで、game ID、game seed、candidate /
  native identity、protocol SHA、requested denominator、duplicate gameの検査情報を持たない。
  よって任意の集計JSONLを直接feedbackへ使わず、strict evaluation reconcilerが
  `META_TRAIN` ledgerからevery/only rowを導出したhash-bound adapterを前段に置く必要がある。
- generic schedule validatorは正式SHAをsourceとして拘束するためcurrent artifactには問題が
  ないが、別scheduleを作る場合はschema version、`training_authority=false`、duplicate ID 0、
  exact membership / count / normalized weightも統合testで確認する。schedule fileのSHA一致だけを
  permission判断にしない。

## Student fail gate / longrun接続

current Student v3をlongrunへ接続する判定は **NO-GO / `LONGRUN_READY`未達**である。
根拠は独立した二層に分かれる。

1. 性能層: theta0はnative差 `-61.458pt`、AWRは `-65.625pt`。fault 0でもnative超過の
   証拠がなく、どちらも開始候補にできない。
2. protocol層: common24は `META_DEV=0` かつ `META_FINAL=4`。`META_DEV` start gateの
   split要件を満たさず、final isolationもcandidate選択用途としては満たさない。

現行 `longrun_autonomous_v1` のstart gateは少なくとも、exact fixed split、PROVEN native
baseline、`META_DEV` のみの2 independent block / 2 seed、全block fault 0、seat gap 5pt以下、
各block candidate deltaが既定でnative比 `+1pt` 以上、package closure、rollback readinessを
要求する。`META_FINAL` はstart gateへ入れない。96 common24 reconcilerの
`SCREEN_COMPLETE_CONTINUE` は構造的screen完了を表すだけであり、このlongrun gateを
上書きしない。

残るprovenance上の注意として、`GateEvidenceV1.manifest_sha256` は型上optionalであり、
`BlockEvidenceV1` はaggregate値だけを保持してledger path/file SHA、exact opponent IDs、
game seed集合を直接保持しない。sealed longrun config側はmanifest SHAとsplit membershipを
拘束するが、将来の正式start gateではstrict ledger reconcilerまたは同等のreceiptを前段に
置き、aggregate blockだけを根拠にしないことが安全である。

## Full6 repair完了待ち後の統合監査

Full6 raw scanは10分のtime boundで停止し、repair成功またはtraining-readyとは扱われなかった。
代わりに次のformal blocked descriptorが生成された。

| 項目 | 値 |
|---|---|
| path | `runs/final-sprint-autonomous/student-v3-full6-repair-v1/manifest.json` |
| file SHA-256 | `a38e0a6ce8ff2396e53064bd5c2e2352f8806bb09a81fbb8acc7d9443d6703c7` |
| semantic repair SHA-256 | `f5c50c93e33e95bb815154ba6c60a4f34271a17f647bfdc9b016cc2509e840f2` |
| source decisions | 36,684 |
| planned unordered-compatible decisions | 36,680 |
| published rows | 0 |
| performance training ready | false |

quick formal verifierはcanonical bytes / semantic SHA / blocked Full6 bridge / Tomato clean bridge /
catalog bindingを再hashしてPASSした。ただし `primary_reproduction.complete=false` であり、
このrunはprimary raw recordを再現したとは主張しない。blocked reasonsは次の3件。

- `component_split_assignment_unmaterialized`
- `ordered_pointer_head_quarantine_unmaterialized`
- `primary_reproduction_incomplete`

ordered `5:34` 4件のrecord IDs / teacher-order target sequencesと、global
non-ubiquitous cross ID
`5a996ab25264020f3a776c00489771e41b1bfbd2a0cff63eb0c907a8953e80ed`
を解消するcomponent assignmentはいずれもnullで、`silent_drop=false`、authorityは全false。
Full6 dataset、GPU shard、AWR、学習へは接続できない。詳細正典は
`docs/evidence/autonomous-full6-repair-and-dynamic-curriculum-v1-20260813.md`。

### Full6 / curriculum / Studentを接続する統合test oracle

今後 Full6が一次再現を完走した時点で、少なくとも次を同一integration gateで検証する。

1. blocked descriptorではなく、6 teacher exact setとcatalog file / semantic / decision SHA、各
   snapshot index SHAをbindしたcomplete primary artifactであること。
2. ordered 4件をsilent dropせず、exact record ID、selection schema、teacher-order target
   sequence、source record SHA、teacher deck/policy SHAを持つquarantineまたはordered headへ
   every/only joinすること。
3. episode + non-ubiquitous near-duplicate connected componentを原子単位に再splitし、episode
   cross 0、non-ubiquitous cross 0、assignment SHAをformal再現すること。
4. repaired splitからvalue / AWRを新規cross-fitし、旧sealed split由来AWRを再利用しないこと。
   GPU dataset / AWR sidecarはrecord ID + content SHAをexact joinし、train以外を拒否すること。
5. dynamic opponent curriculumは学習record splitとは別のmeta opponent splitとして扱い、
   `META_TRAIN` rolloutのみfeedbackを許すこと。`META_DEV` / `META_FINAL` rowがcurriculum outcome、
   value target、AWR weightへ入ればfail-closedにすること。
6. Studentの開始判定は新規exact `META_DEV` ledgerをstrict reconcileし、native pair identity、
   candidate identity、requested denominator、fault、seat、seed、protocol/runner closureを固定する。
   Full6 data readinessやTRAIN loss改善だけで `LONGRUN_READY` を付与しないこと。

現時点ではFull6はこのgateの1～4を満たさずblocked、dynamic iteration 0は5のstatic境界のみ
formal GREEN、Studentは6の性能/protocol両方でNO-GOである。

## Protocol / seed / fault integrity

formal reconciler が各比較で確認した内容は次の通り。

- reference config file SHA
  `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- exact 24 unique opponent IDs、reference config順
- 各 opponent × seat 0/1 × repetition 0/1 の96 strataをexact 1回
- armごとの row順、manifest `game_ids`、ledger順の一致
- arm間を含む game ID重複なし
- candidate / native とも base seed `13000000`、seed min/max
  `13000000 / 13000095`、96 unique
- 両 arm共通 seed-set SHA
  `8e7f68b79a56ed31d49d7d754b502c551ef7ca2b5bed3e03480132614519fcb7`
- exact opponent policy/deck identityのarm間一致
- `max_steps=2000`、`requested=1`、evaluator closure SHA、outcome/winner/status整合
- ledger再集計とmanifest/summaryのrequested/W/D/L/fault/score一致
- candidate / deck / policy identity と formal candidate artifact、native raw bytes SHAの一致
- authorityは training / promotion / submission / longrun の全て exact false

両 reconciliation の mechanical status は `SCREEN_COMPLETE_CONTINUE`、
`promotion_gate_eligible=false` である。この status は96局screenが構造的に完成したことを
示し、性能上のGOやpromotionを意味しない。reconciler は設計通り small / negative delta
だけで自動棄却しない。今回の差は小差ではなく、現時点で native 超過を支持する証拠はない。

## timeout / runner_ref provenance gap

### 実行時には使われる

`EvaluationGameV1` は `timeout_seconds` と `runner_ref` を保持し、spawn payloadへ含める
（`scripts/parallel_cabt_evaluator_v1.py:132,137,174,179`）。worker は
`runner_ref` を import resolveし、`timeout_seconds` を worker-local `SIGALRM` に渡す
（同 `:266-296`）。親 watchdog も bounded in-flight future の submit時刻から
`timeout_seconds + 5.0` を監視する（同 `:671-708`）。したがって値が無視されている
わけではない。

### 永続結果には残らない

game row builder の共通フィールドは `max_steps` までは保存するが、
`timeout_seconds` / `runner_ref` を保存しない（同 `:405-433`）。manifest builderも
worker/recycle/thread/pairing/requested/game IDs/block IDsを保存するが、この2項目は含まない
（同 `:609-623`）。実際の theta0 / AWR / native 3 artifactについて、次の全てで
`timeout_seconds` / `runner_ref` path が0件であることを `jq paths(scalars)` で確認した。

- `ledger.jsonl` 全 row / `games/*.json`
- `manifest.json`
- `summary.json`
- Student v3 `student_v3_candidate_summary.json`
- native `candidate_summary.json`

### evaluator implementation SHAも injected runner全体ではない

ledger の evaluator SHAは現在の実装でも
`0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
と再現する。ただしその導出対象は evaluator moduleと `scripts/test_sim.py` の2ファイルだけ
である（同 `:368-376`）。injected Student / native runner moduleのbytes自体はこのSHAの
対象外である。Student candidate metadataには別のruntime closureがあるが、ledger内の
実runner選択と対称的に結び付く receiptではなく、native側には同等のruntime closureがない。

### reconcilerが保証する範囲

reconcilerはこの欠落をmodule docstringで明示し、closed requestの各 armで
`timeout_seconds=600.0` と固定 runner refを要求する。request、input file SHA、formal
subject identity、全 strata/seed/resultを結ぶため、異なる宣言を混ぜるとfail-closedになる。
しかし元ledgerに観測値がない以上、post-hoc request宣言が実際のlaunch payloadと同じだった
ことをledgerだけから証明はできない。このため出力は明示的に次を保持する。

```json
{
  "timeout_binding": "request_and_arm_declaration_only",
  "ledger_v1_omits_timeout_seconds": true
}
```

`runner_ref` も同じ制約を持つ。現reconcilerはcandidate/nativeの許容文字列を固定して
cross-bindするが、それはlaunch receiptの復元ではない。

## 判断と残リスク

- **結果 integrity:** common24 strata、opponent identity、seat、seed schedule、subject
  identity、requested denominator、W/D/L/faultはformal GREEN。
- **性能:** theta0 / AWRともnativeを大幅に下回る。AWRはtheta0よりさらに4勝少ないが、
  engine common RNGがない独立runなので、4局差単独をAWR効果の厳密paired推定とはしない。
- **384局:** 96局screenの構造は次段へ進めるが、現信号は有望ではない。384局を行うなら
  事前計画した反証・安定性確認として扱い、96局の小幅上振れを期待する追加sweepにはしない。
- **provenance:** timeout / runner実行値は宣言ベース。今後新schemaを作る場合は各 rowと
  manifestに両値、runner module/file SHA、可能ならcanonical game-spec file path/SHAを保存
  すればこのgapを閉じられる。本監査では親指示に従い既存 evaluatorを変更していない。
- **authority:** すべてfalse。reconciliationはpromotion、longrun、package、submissionの
  権限を付与しない。

## 再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python - <<'PY'
from mage_ptcg.meta_specialist.student_v3_native_common24_reconcile_v1 import (
    reconcile_student_v3_native_common24_v1,
)

for path in (
    "runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/request.json",
    "runs/final-sprint-autonomous/student-v3-awr-native-common24-reconcile-96-v2/request.json",
):
    result = reconcile_student_v3_native_common24_v1(path)
    print(
        path,
        result["reconciliation_sha256"],
        result["candidate"]["wins"],
        result["candidate"]["faults"],
        result["native"]["wins"],
        result["native"]["faults"],
        result["comparison"]["candidate_minus_native_score_rate"],
        result["gate"]["status"],
    )
PY
```

確認結果:

```text
theta0: a46cfef... 7 0 66 0 -0.6145833333333334 SCREEN_COMPLETE_CONTINUE
AWR:    10fd95e... 3 0 66 0 -0.65625            SCREEN_COMPLETE_CONTINUE
```
