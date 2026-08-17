# Native public advantage source route comparison v1（2026-08-13）

## 結論

実advantageを作る経路は二つある。既存のsealed teacher snapshotをpublic-onlyへ
投影する A は新規対局 0 局で最短だが、現在の許可は `training-local` の派生weight
に限定され、`behavior_allowed=false` であるため、明示的なbehavior-source許可が
追加されるまで実行不可である。Tomato native自身を操縦させる B は外部teacher
actionを使わずに進められる設計だが、per-decision public captureを行う新collector
契約と最小96局の新規snapshotが必要である。

本資料は設計・fixture比較だけを行った。実collector、CABT、evaluator、training、
submission、longrunは起動しておらず、既存 `local_eval_only` / `behavior_allowed=false`
を変更していない。

機械可読な比較fixtureは
`docs/evidence/autonomous-native-advantage-source-route-comparison-v1-20260813.json`
（SHAは生成後にこの文書と親contextへ記録）である。

## 現在の入力ゲート

| artifact | SHA-256 | 観測 |
| --- | --- | --- |
| `opponents/pool_manifest.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` | 102 pairの元pool。全行の元 `usage_boundary=local_eval_only` |
| `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` | 102 rows、`training_allowed=2`、`behavior_allowed=0`、`submission_allowed=0` |
| `runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json` | `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a` | `teacher_behavior_eligible=0`。Task 2 strict gateで拒否 |
| common24 adapter | `0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89` | outcome/protocol入力としてreload可能 |
| current blocked materializer progress | `bbc6510516d94c4396cc75eaa17b21616cbf48c94e2b931ae91f00749d5fdfa8` | `ready_for_evaluation=false` |
| current blocked run manifest | `def3733f0cbde92da13d4668727eb2203012f1f882071603f06265327f3b4b63` | all authority false、candidate artifactなし |

real META_TRAIN public advantage tableはまだ存在しない。Task 1 synthetic fixture
（`d62bb3ec85115976c1e101282c60c0aa1d23e90b8b07382fd9268ad159b183b0`）は契約テスト
専用であり、性能根拠・BestKnown・candidate・longrunへ昇格させない。

## A: explicit permissioned META_TRAIN behavior source

### 既存資産と不足

fresh v2b teacher snapshotはTomato、Lucifer、Plamen各96局でsealedされている。
manifest、collection contract、permission trusted bytes、record SHAは閉じており、
各permissionは `allowed_usages=["training-local"]` を持つ。しかし teacher manifest
の `teacher_usage_boundary` は `local_eval_only` のままで、これは元poolの評価境界と
整合する。したがって「派生weightをtraining-localへ使える」ことから「外部chosen
actionをbehavior sourceとして再利用できる」ことを推論してはならない。

Tomato snapshotのmanifest SHAは
`de04f029ff18cbe0e2209c57dd17a73d90d5ae7a4ac6a0bc8706543349e2d41c`、Luciferは
`a03dfc1f410cdf23b2404cdd3411271776805018f679ad61f6777f32ea949e0d`、Plamenは
`ade4643925ac9ec3b4c737499b0cb8994279555505fb458d629ae5fdc8e1f45e` である。
collector sourceは
`src/mage_ptcg/meta_specialist/collect_teacher_records_v1.py` SHA
`a9c49337b6686ea528bf213e9b75cc7ee1862fea0cdf23a64745cc4568fd1198`、public projection
contractは `native_public_advantage_v1.py` SHA
`dfdcf729debf3699e935412d8fc9f8ed149a90affd8dcbf8e8148a4165293e3d` である。

### Aの最小設計

既存snapshotを直接書き換えず、issuered decisionを根拠に新しい
`derived_teacher_snapshot_public_projection` manifestを作る。derived manifestだけが
次を明示する。

- chosen actionをlocal research behavior sourceとして使う明示的許可（既存の
  `training-local` derived-weight許可とは別のdecision reference）。
- 元snapshot SHA、collection contract SHA、policy/deck SHA、permission manifest ID、
  permission content hash、trusted bytes SHAの全結合。
- `training_allowed=true`、`behavior_allowed=true`、training-local usage、
  `submission_allowed=false` のMETA_TRAIN rows。現行102-row manifestの値を変更しない。
- top-levelのtraining/promotion/submission/external-execution authorityは全てfalse。

recordをTask 1 tableへ投影する際は、actor-visible public projectionからのみ
`state_digest`を作り、stable public `action_key`を使う。`private_state`、hidden card、
opponent hidden identity、future RNG、local action indexはmodel inputへ入れない。
outcome、seat、opponent stratumは監査metadataとして保持し、fault/unknownは黙って
有効rowへ変換しない。Aで得られるのはpublic value、filtered BC、outcome-weighted
action rowであり、native log-probabilityやAWR importance correctionの証明ではない。

### Aコストとゲート

新規対局コストは **0局**。既存sealed recordsの検証・投影・canonical table生成だけ
である。ただし現状はbehavior permissionがないため **BLOCKED**。再開条件は、
issuered behavior decision、derived manifestのstrict reload、public-only projection
監査、Task 1 table self-verify、Task 2 materializer reloadの全てである。

## B: native Tomato self-rollout

### 目的と境界

Tomato native policy + Tomato deckをsubjectとし、common24の24 opponent IDsを
両seat・各2 repetitionで回す。最小snapshotは **96局**（24×2×2）、faultは要求局数
のdenominatorに含める。native agentが選んだactionをteacher labelとは呼ばず、
public state/action/outcomeの自己trajectoryとして扱う。

subject identityは policy SHA
`8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`、deck SHA
`42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` で固定する。
既存 native pilot runner（SHA `7c559621eb960f7be0a63ad53adf615bacaf30b7058885e6b433b2a83d951a32`）
はゲーム結果のみでper-decision public captureを持たないため、collectorは新規契約
として設計する。

### B collector契約

`native-public-rollout-collector-v1` は次をbindする。

- native policy/deck/evaluator/engine/runner source SHA、pool/meta/protocol SHA。
- opponent IDs、game IDs、seed、seat schedule、source commit、collector snapshot SHA。
- public projection schema SHAとstable action-key schema SHA。
- per-game sidecar、append-only attempt ledger、contract-bound resume、atomic final
  table/manifest publish。

1 rowの最小フィールドは `game_id`、`seed`、`seat`、`opponent_id`、
`public_state_digest`、`action_key`、`terminal_outcome`、`fault_status`。teacher label、
teacher action target、native log probability、private/hidden state、opponent hidden
informationは拒否する。authorityは全false、research-onlyで固定する。self-rollout
recordを作る許可も、元poolの `local_eval_only` から推論せず、local research用の
別decision/manifestへ結ぶ。

### Bコストとゲート

最小は96局。96/96完走、fault/step-limitの扱い、seat/opponent coverage、game ID/seed
universe、public-only action legality、native baseline identity、real table self-verify、
Task 2 strict materializer reloadを確認する。self-rollout単体はnativeを再現するだけ
で改善を証明しないため、その後にpublic value/searchまたは別student policyが必要で
ある。候補として昇格するには既存のnative controlと **96→384→768→1536** の逐次gate
を通す。

## 比較と次実装

| 観点 | A: derived permissioned snapshot | B: native Tomato self-rollout |
| --- | --- | --- |
| 新規対局 | 0局 | 96局/snapshot |
| 現在の状態 | behavior permission不足でBLOCKED | collector未実装のDESIGN_ONLY |
| 最短の実装 | permission decision + public projection/materializer接続 | public rollout collector契約＋fixture |
| 強み | 既存sealed recordsを再利用できる | 外部teacher chosen actionを使わずに済む |
| 主リスク | training-local許可をbehavior許可へ誤変換しやすい | runtime captureとpublic/private境界の実装負荷、自己模倣の限界 |
| AWR適性 | logprobなし。value/filtered BC中心 | logprobなし。value/self-imitation診断中心 |

推奨順序は次の通りである。

1. 現行local_eval_onlyとbehavior falseを不変に保ったまま、Aのbehavior-source許可を
   issuerへ確認する。
2. Aの許可が閉じれば、既存v2b snapshotからpublic-only derived manifest/tableを作る。
   新規対局0局のため最短である。
3. 許可が閉じない場合に備え、B collectorの契約・public projection・fixtureを実装し、
   契約とself-rollout許可のGREEN後だけ96局を起動する。
4. どちらの経路でもreal tableとTask 2 strict reloadが通るまで
   `ready_for_evaluation=false`。その後native baseline比較を96→384→768→1536で行い、
   native BestKnown超越を別artifactで判定する。

この設計段階ではcollector/evaluator/trainer/CABTを起動しない。commit、push、remote
branch、Champion変更、Kaggle submissionも行っていない。
