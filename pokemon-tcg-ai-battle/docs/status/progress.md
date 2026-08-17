---
project: MAGE-PTCG
document_status: progress
as_of: 2026-07-15
overall_progress_percent: 37
---

# Progress

総合進捗は、Kaggleへ提出できる強いAgentの完成度を基準にした重み付き推定。

| 領域 | 重み | 進捗 | 状態 |
|---|---:|---:|---|
| 設計・正典 | 10% | 100% | 第三者レビュー反映 |
| Bootstrap | 10% | 90% | 隔離・検証・承認・resume |
| P0 Submission | 10% | 70% | Rule v0、評価基盤。正式package残り |
| C1 Public Belief | 15% | 65% | v0新規実装・privacy／lifecycle／全suite／400試合完了。数値は全体再見積もりまで据え置き |
| C2a Knowledge Pack | 10% | 5% | 実接続未着手 |
| C2b Competition Probe | 5% | 10% | 経路確認、実Probe未実施 |
| C3 Bounded Search | 15% | 0% | 未実装 |
| C4 Student v0 | 15% | 0% | 未実装 |
| C5 Distillation／League-lite | 5% | 10% | 評価基盤とcurated knowledgeのoffline wiringを検証。actual Gateは未着手 |
| Optional | 10% | 0% | 条件付き |

加重合計：約37%。

2026-07-15のC1完了判定は[Evidence](../evidence/public-belief-c1-new-implementation-2026-07-15.md)に基づく。今回の作業では指示に従い、C1行の既存進捗値と総合進捗を再計算していない。

2026-07-16のC5 curated knowledge wiringは[Evidence](../evidence/c5-curated-knowledge-wiring.md)で確認した。actual cabt record／paired League／promotion evidenceは増えていないため、進捗率と総合進捗は変更していない。

2026-07-16のC5 ActionKey adapterは[Evidence](../evidence/c5-actionkey-adapter.md)で確認した。22件teacher ruleをoffline decisionへ決定的・fail-closedに対応付けたが、実C4 fixtureはattestation不在で適用0件であり、actual cabt record／promotion evidenceは増えていない。したがって進捗率と総合進捗は変更していない。

2026-07-16のcabt capability recoveryは[Evidence](../evidence/cabt-capability-recovery.md)で確認した。official cabtの1-game traceと20-game side-swap smokeは実行できたが、promotionに必要な比較設計・証拠は未完了である。進捗率と総合進捗は再見積もりなしに変更していない。

2026-07-16のactual agent viabilityは[Evidence](../evidence/actual-agent-viability.md)で確認した。Rule v0対deterministicの20-game smokeは完走したが、C3はfallback-only、C4はmodel artifact欠落、C5はruntime policyなしであり、Promotion evidenceは増えていない。進捗率と総合進捗は変更していない。

同日のreview fixでruntime privacy scannerとengine seed provenanceを追加した。これはviability artifactの安全性計測を強化するものであり、C3／C4／C5の実効policyやPromotion evidenceを増やさないため、進捗率と総合進捗は変更していない。

2026-07-16のC4 Student actual runtimeは[Evidence](../evidence/c4-student-actual-v0.md)で確認した。`SMOKE_ONLY` artifactによるactual 1-game Gate AはCLEAN_PASSだが、actual training data・performance eligibility・paired比較は未取得である。したがって進捗率と総合進捗は変更していない。

## 更新規則

進捗変更時はEvidence（`docs/evidence/`または`experiments/`）、Gate結果、[current_status.md](current_status.md)、Notion管理ページ、[../notion/last_sync.json](../notion/last_sync.json)を同時更新する。進捗率はEvidenceなしに変更しない。
