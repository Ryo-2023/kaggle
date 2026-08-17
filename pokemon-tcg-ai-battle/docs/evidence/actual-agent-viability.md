---
project: MAGE-PTCG
slice: C3-C5
status: ready
as_of: 2026-07-16
---

# Actual Agent Viability v0

## 結論

canonical base `36029e57b9fadd5defa6742b5011a02f2a164321`からevaluation-only adapterを追加し、work commit `93e83e70592fd07736382776e6e38d3258d66190`でactual cabtの可用性を確認した。Champion、submission defaultはRule Agent v0、Promotionは`NO_DECISION`のままである。

これは100-game screening、400-game Promotion評価、性能改善の結論ではない。machine-readable provenanceは`engine_seed_supported=false`、`agent_seed_schedule_deterministic=true`、`seat_schedule_deterministic=true`、`resume_idempotent=true`、`engine_outcomes_deterministic=false`を分離する。W-L-Dやlatencyの完全再現性は主張しない。

## Gate 0 — runtime inventory

| Agent | Factory | Artifact | Actual-callable | Effective policy | Classification |
|---|---|---|---|---|---|
| Rule v0 | `make_rule_agent` | 不要 | yes | Rule Agent v0 | `RUNNABLE` |
| deterministic | `make_deterministic_agent` | 不要 | yes | deterministic baseline | `RUNNABLE` |
| C3 Bounded Search | `make_bounded_search_agent` | public `EngineAdapter`なし | yes | Rule v0 fallback pending adapter | `RUNNABLE_WITH_FALLBACK` |
| C4 Student v0 | `make_student_agent` | model artifactなし | no | Rule v0 fallback | `BLOCKED_BY_MISSING_ARTIFACT` |
| C5 | なし | 該当なし | no | `NOT_APPLICABLE` | `NOT_A_RUNTIME_AGENT` |

C5はActionKey adapter、attestation、teacher binding、League infrastructureであり、独立runtime policyではない。架空のC5 agentは作成していない。

## Evaluation-only contract

`scripts/run_actual_agent_viability.py`と`mage_ptcg.evaluation.actual_agents`は、登録済みIDだけをfail-closedにfactoryへ接続する。各agentはcalls、decisions、legal／invalid selections、exception／timeout、fallback reason、latency、effective policyとruntime feature counterをaggregateだけで返す。`main.py`は変更せず、submission defaultへ自動接続しない。

artifactにはcanonical base／work commit、cabt package identity、environment／deck fingerprint、agent version／config hash／model hash、schedule、W-L-D、seat別W-L-D、failure counter、fallback、latency、effective policy、resume状態、artifact hashを保存する。raw observation、candidate／card identity、hidden information、exception text、object repr、absolute pathは保存しない。

`cfc4e05`でruntime privacy scannerを追加した。decision aggregate、match record、League summary、provenance manifestをpublish前にscanし、`privacy_scan_executed`、`privacy_violations`、controlled `privacy_violation_categories`だけを保存する。scan未実行・`UNKNOWN`・違反>0はGate PASS不可である。raw値や検出detailは保存しない。以前の20-game artifactに対する手動mechanical scanと、このruntime scannerを混同しない。

## Gate 1 — 1-game actual probe

正規環境は`env -u PYTHONPATH .venv/bin/python ...`である。capability probeは`READY`、`kaggle-environments 1.32.0`、`cabt` registered、`make("cabt")`成功だった。

| Matchup | Gate | W-L-D（Rule v0） | invalid / crash / timeout / privacy | Challenger effective policy | Artifact hash |
|---|---|---:|---|---|---|
| Rule v0 vs deterministic | PASS | 1-0-0 | 0 / 0 / 0 / 0 | deterministic baseline | `ee959fa7…` |
| Rule v0 vs C3 | STOPPED | 0-1-0 | 0 / 0 / 0 / 0 | Rule Agent v0 fallback only | `d658b2e5…` |

deterministicは15 legal decisions、fallback 0だった。C3は24 legal decisionsで、`search_requested=24`、`search_started=24`、`search_completed=0`、`search_blocked=24`、`engine_adapter_unavailable=24`、nodes expanded 0だった。全decisionがRule v0 fallbackのため、C3を独立challengerの勝率比較へ進めない。

C4はmodel artifactがないためloader／inference countは`NOT_RUN`、C5のteacher applicationは`NOT_APPLICABLE`である。

fix後のRule v0対deterministic 1-game probeは`privacy_scan_executed=true`、`privacy_violations=0`、category `{}`、`engine_seed_supported=false`でPASSした。challengerは23 decisions／23 legal decisions、`legal_action_rate=1.0`、`decision_latency_samples=23`、agent単位のtimeout countは直接計測していないため`UNKNOWN`である。artifact hashは`50019167…`。

## Gate 2 — 20-game viability smoke

Gate 1を通過したdeterministicだけを、同一`deck.csv` mirror、side swap（各seat 10 games）、seed／seat schedule、resume有効で実行した。

| Matchup | attempted / completed | W-L-D（Rule v0） | seat 0 | seat 1 | invalid / crash / timeout / privacy |
|---|---:|---:|---:|---:|---|
| Rule v0 vs deterministic | 20 / 20 | 17-3-0 | 8-2-0 | 9-1-0 | 0 / 0 / 0 / 0 |

- Rule v0: 665 legal decisions、fallback 0、call latency p50／p95／max = 0.0231／0.0730／0.1909 ms
- deterministic: 306 legal decisions、fallback 0、call latency p50／p95／max = 0.0199／0.0752／0.1283 ms
- match latency p50／p95／max = 0.0711／0.4087／0.4171 s
- artifact hash: `d251d5b350fa289f9308b10aeba8748a0d9245c148985e2cf35abeb5cb1dbb21`
- resume duplicate executionは`resume_duplicate_execution_detected=false`／`resume_duplicate_measurement=STRUCTURAL_GUARANTEE`として表す。completed match再利用後もartifact byte hashは不変

この結果はofficial full episode、evaluation adapter、side swap、resumable public artifactのviabilityを確認するpipeline smokeである。Rule v0の統計的優位、C3／C4／C5性能、Promotion条件成立は証明しない。

## 次の判断

- 100-game screening eligible: なし。deterministicはbaselineでありchallengerではない
- stopped: C3（public arbitrary-state forward／`EngineAdapter`欠落でfallback-only）、C4（model artifact欠落）、C5（runtime agentなし）
- C3の再開条件は、actor-visibleかつdocumentedなpublic forward APIによる`EngineAdapter`である
- C4の再開条件は、provenance付きmodel artifactをloadし、actual inference countが正となる1-game Gate 1通過である
