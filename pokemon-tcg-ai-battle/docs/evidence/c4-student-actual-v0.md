---
project: MAGE-PTCG
evidence_type: c4-student-actual-runtime
as_of: 2026-07-16
---

# C4 Student actual runtime v0

## 結論

`SMOKE_ONLY` の provenance 付き Student model を生成し、evaluation-only registry へ model と manifest を明示指定して、actual cabt 1-game Gate A を `CLEAN_PASS` で完走した。これは runtime 接続・合法性・privacy の確認であり、性能またはPromotionの根拠ではない。Champion／submission default は Rule Agent v0、Promotion は `NO_DECISION` のままである。

このfixture段階の記録は履歴として保持する。actual Rule BC sourceを用いた `ACTUAL_TRAINED` model、actual Gate A、20-game Gate Bの実測正典は[actual-trained evidence](c4-actual-trained-v0.md)へ更新済みであり、Champion／default／Promotionの結論は不変である。

## Artifact とデータ分類

- artifact type / purpose: `C4_STUDENT_MODEL` / `SMOKE_ONLY`
- performance eligible: `false`。actual training data は未配置であり、既存の canonical C4 fixture 12 source episode / 12 decision / 36 candidate を接続確認だけに使った。
- model format/version: `student-v0-json-linear-candidate-scorer` / `student-v0-model-v1`、96 feature（weight 96 + bias 1）。
- model hash: `a7d52201dfbc66a2e500b72738c7068bb6f50aa732da53a177025cc51c47d100`、1,086 bytes。
- feature schema hash: `552d3bf4c4792d84fc509bfa51c322e23e84dd6c04697f0dab8dca80ea864484`。
- dataset hash: `4eb71dcbfef07fe5ce2fd8080c050ff6564b39abd7aa18e5ae6856593e837c41`。raw trace／raw private identity は artifact に保存しない。
- split: `source_id_sha256_modulo_percent`、train 10 / validation 2 episode、overlap 0、split hash `3a1a48a854927b8a0edc781c19d652bbe27616558a32d73dc4dc6842af1b9da7`。
- training: standard-library Python float full-batch、CPU、CUDA unavailable、GPU `NONE`。80 epoch、seed `NOT_APPLICABLE`。
- storage: Git 管理外の `models/c4-student-actual-v0/`。submission artifact へは同梱しない。

fixture holdout の top-1/top-3 fidelity と legal action rate は各 1.0、holdout loss は 0.02779 だった。fixture は同型の小標本であるため、これを実cabt性能・汎化・非劣性としては主張しない。

## 実装と fail-closed 境界

- `scripts/build_student_actual_artifact.py` は dataset 未指定時に canonical fixture から `SMOKE_ONLY` artifact を生成し、dataset 指定時だけ `ACTUAL_TRAINED` を許す。
- `src/mage_ptcg/student/artifact.py` は manifest の用途、format/version、model hash/size、feature schema、privacy scan を model load 前に検証する。manifest 欠落、model 欠落、hash/schema mismatch、malformed model は registry で `BLOCKED_BY_MISSING_ARTIFACT` または `BLOCKED_BY_INVALID_ARTIFACT` となる。
- `src/mage_ptcg/evaluation/actual_agents.py` は declared agent、model hash/purpose、inference requested/completed/failed、feature success/failure、Student selection、fallback、legal action、latency、effective policy count を decision aggregate に記録する。fallback の合法手を Student selection には加算しない。
- `scripts/run_actual_agent_viability.py` は Student Gate A で model load/hash、inference、Student effective policy、privacy、invalid/crash/timeout を個別に判定する。`SMOKE_ONLY` は Gate B 20-game の performance screening 対象外である。

## Offline verification

- model train/build、manifest hash・schema検証、model reload、deterministic scorer、candidate order stability、legal-only selection、malformed input fallback を focused test で確認した。
- model hash mismatch、schema mismatch、missing manifest/model、`SMOKE_ONLY` performance ineligible、actual instrumentation と fallback 分離を focused test で確認した。
- artifact manifest と Gate A aggregate の public artifact privacy scan はともに `executed=true`、violations 0 だった。

## Actual Gate A

実行は shared venv の `kaggle_environments==1.32.0`、`cabt` capability `READY`、engine seed unsupported の条件で行った。

| Gate A field | Result |
|---|---:|
| actual cabt / game completed | true / true |
| declared agent / effective policy | Student / `Student v0 with Rule Agent v0 fallback` |
| model loaded / model hash present | true / true |
| inference requested / completed / failed | 20 / 20 / 0 |
| Student selection / fallback | 20 / 0 |
| feature success / failure | 20 / 0 |
| legal decisions / invalid | 24 / 0 |
| crash / timeout | 0 / 0 |
| privacy scan / violations | true / 0 |
| Gate A | `CLEAN_PASS` |

実cabtの乱数は engine seed unsupported のため、上の decision 数・latency・勝敗を再実行一致の性能値とは扱わない。Gate B は `SMOKE_ONLY` / `performance_eligible=false` により **未実施** である。

## 決定と残リスク

runtime 接続の最小 Gate は満たしたが、actual privacy-safe dataset、episode/near-duplicate/OOD group holdout、ACTUAL_TRAINED artifact、paired non-inferiority は未測定である。よって Student は evaluation-only のままにし、Champion／default を変更せず、Promotion は `NO_DECISION` とする。
