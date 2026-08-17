---
project: MAGE-PTCG
evidence_type: c4-student-actual-data-feasibility
as_of: 2026-07-16
---

# C4 Student actual data feasibility / runtime input contract

## 結論

`ACTUAL_TRAINED` の生成は **NO-GO** とする。調査した worktree と Git 管理外 artifact には actual cabt decision trace、private ActionKey binding、actor-visible attestation、`rule-bc-v1` JSONL、split manifest がいずれも 0 件である。既存の public trace 形式は設計上 actor hand identity と candidate-to-option binding を永続化しないため、仮に public trace だけがあっても C4 の candidate feature、chosen binding、teacher ranking を安全に復元できない。

この結論は Student runtime の可用性を否定しない。canonical fixture 12 episode / 12 decision / 36 candidate から作られた `SMOKE_ONLY` model は存在するが、`performance_eligible=false` であり、actual 学習データや性能根拠へ転用しない。

## 調査範囲と観測

- 調査対象: tracked repository、`.local_artifacts/`、`artifacts/`、`runs/`、`outputs/`、`tmp/`、`models/`、`data/`。不在の候補 directory は作成していない。
- actual cabt trace: 0 file / 0 episode / 0 decision。collector 実装はあるが、完了 trace とその manifest は未配置。
- public trace: 0 file。既存 writer は episode、decision、seat、public candidate ID、選択 index を出力できるが、actor-private ActionKey core と hand identity を保持しない。
- private binding / actor-visible attestation: 0 file。writer/binder 実装のみで、actual binding artifact は未配置。
- candidate feature / chosen binding / teacher target / teacher score: actual record は各 0。canonical fixture にのみ 12 / 12 / 12 / 12 の contract data がある。
- existing Student dataset / train-validation manifest: `rule-bc-v1` JSONL と manifest は 0。知識 JSONL は存在するが、Student training input ではない。
- model / League: `SMOKE_ONLY` model 1、manifest 1、actual Gate A aggregate 1。actual League artifact は 0。

存在する model の provenance は `SMOKE_ONLY`、fixture source、dataset hash `4eb71dcbfef07fe5ce2fd8080c050ff6564b39abd7aa18e5ae6856593e837c41`、model hash `a7d52201dfbc66a2e500b72738c7068bb6f50aa732da53a177025cc51c47d100` である。相対 artifact path は `models/c4-student-actual-v0/`。raw/private content は本書に記録しない。

## ACTUAL_TRAINED availability matrix

| Required field | Status | 根拠または安全な導出条件 |
|---|---|---|
| `episode_group_id` | MISSING | actual trace 0 件。collector の `episode_index` は、actual trace manifest と一対一で provenance hash を付ける場合のみ安全に group ID へ導出できる。 |
| `decision_index` | MISSING | actual trace 0 件。public collector は episode 内決定順を出せる。 |
| `actor` / `seat` | MISSING | actual trace 0 件。public collector は actor `seat` を出せる。 |
| `state_features` | MISSING | C4 feature は actor public state + own private state + visible history から作る。public projection だけでは own-private component を復元できない。 |
| `candidate_features` | UNSAFE_TO_USE | public candidate ID は redacted・順序非結合であり、hand-origin candidate の private ActionKey identity を復元できない。推測で結合してはならない。 |
| `candidate_count` | MISSING | actual trace 0 件。attested public trace の `select.option_count` からは安全に導出できる。 |
| `chosen_candidate_index` | MISSING | actual trace 0 件。将来は同一 decision の option-index namespace を明示した private binding が必要である。 |
| `teacher_target` | MISSING | actual Rule callback / binding が未配置。public action index だけを別 candidate namespace へ再解釈しない。 |
| `teacher_score` | MISSING | existing trace format は complete teacher ranking/score を保存しない。actor-visible raw decision input 上で Rule v0 ranking を同時に生成する必要がある。 |
| `feature_schema_version` | AVAILABLE | `student-v0-features-v1`、schema hash `552d3bf4c4792d84fc509bfa51c322e23e84dd6c04697f0dab8dca80ea864484`。 |
| `source_agent` / `source_agent_version` | MISSING | actual trace manifest 0 件。source agent ID、version、commit を trace manifest へ固定する必要がある。 |
| `trace_provenance_hash` | MISSING | actual trace/manifest 0 件。trace bytes SHA-256 と canonical config hash から導出可能。 |
| privacy status | MISSING | actual dataset/binding 0 件。public/private artifact ごとに scanner 実行、violation 0 を証明して初めて AVAILABLE とする。 |

## GO / NO-GO 判定

NO-GO の反証条件は、同一 actual episode を group split でき、actor-visible input だけから legal candidate feature と chosen target を一意に結合し、private binding と dataset provenance を検証できる record set が見つかることである。今回の inventory ではその record set は 0 件だった。

既存 `scripts/build_student_actual_artifact.py --dataset ...` は、妥当な `rule-bc-v1` input を受ければ `ACTUAL_TRAINED` artifact を生成できる。しかし input を public trace、fixture、knowledge JSONL から代用する経路は存在せず、作ってはならない。従って `.local_artifacts/c4_actual_feasibility/` の dataset、split、model を生成せず、Gate A も再実行していない。

## Claude data-ops への runtime input contract

### Required trace and private binding

1. public trace manifest は `actual=true`、official `cabt` environment/version、source agent ID/version、source/work commit、deck fingerprint、episode count、canonical config hash、trace SHA-256 を持つ。episode は `episode_group_id`、`decision_index`、actor seat で一意に参照できる。
2. private offline binding は同じ `(trace_provenance_hash, episode_group_id, decision_index, seat)` を key とし、actor-visible `public_state`、`own_private_state`、bounded `visible_history`、selection type/context/bounds を保持する。opponent hand/deck/prize contents、logs、opaque engine state、future outcome は含めない。
3. candidate binding は option-index namespace を明示し、各 legal candidate の Stable ActionKey canonical payload/digest と feature source を同じ decision key に結合する。public candidate ID だけ、または hash だけの逆変換は禁止する。
4. teacher binding は source agent/version、chosen option index、chosen ActionKey digest、complete candidate ranking `(digest, integer_score)`、fallback flag、teacher quality を持つ。teacher target は legal candidate set の部分集合、ranking は legal set 全体をちょうど一度覆う。
5. public trace、private binding、actor-visible attestation は別 artifact のままとし、manifest に各 content hash と privacy scan result を記録する。

### Required dataset row and manifests

既存 trainer の入力行は `rule-bc-v1` の `RuleBCExample` である。必須 field は `schema_version`、redacted `example_id` / `source_id`、`public_state`、`own_private_state`、`visible_history`、selection type/context/min/max、`legal_actions`、`target_action_digests`、`teacher_ranking`、`fallback_used`、`deck_fingerprint`、`source_revision`、redacted string `metadata` である。

data-ops はこの JSONL に加え、runtime 外部 manifest を出す。dataset manifest の最低 field は `artifact_purpose=ACTUAL_TRAINED`、`performance_eligible=true`、dataset source/provenance hashes、episode/decision/candidate counts、feature schema version/hash、teacher source/quality、privacy scan/violations である。split manifest は group assignment、train/validation episode count、split hash、overlap count 0 を持ち、同一 `episode_group_id` を跨がせない。

### Existing trainer, builder, runtime, Gate A

- Trainer input: `scripts/train_student_v0.py --dataset <rule-bc-v1.jsonl> --model <model.json>`。trainer は既存 group split を `source_id` hash で行い、train/validation metrics を返す。
- Provenance builder: `scripts/build_student_actual_artifact.py --dataset <rule-bc-v1.jsonl> --output-dir <dir> --canonical-base <sha>`。output は `student-v0.json` と `manifest.json`。manifest は `C4_STUDENT_MODEL`、`ACTUAL_TRAINED`、`performance_eligible=true`、model hash/size、schema hash、dataset/split counts and hashes、backend/device/seed、train/validation metrics、privacy scan を含む。
- Actual runner arguments: `scripts/run_actual_agent_viability.py --challenger student --student-model <model> --student-manifest <manifest> --games 1 --canonical-base <sha> --output <aggregate>`。
- Gate A required metrics: `model_loaded=true`、inference requested/completed > 0、Student selection > 0、fallback 0、effective policy に Student、privacy scan executed / violations 0、invalid/crash/timeout 0。`SMOKE_ONLY` は Gate A 接続確認だけに使え、20-game Gate B は `ACTUAL_TRAINED` のみ許可する。

## Decision and residual risk

採用した案は NO-GO + exact runtime contract である。public trace を C4 dataset へ疑似変換する案は、actor-private candidate feature と chosen binding を失い、hidden information または推測を導入するため棄却した。最強の反論は「public selected index を label にできる」であるが、candidate feature namespace と teacher ranking が対応しないため判断は変わらない。解消には上記 private binding と manifest を伴う複数 actual episode が必要である。
