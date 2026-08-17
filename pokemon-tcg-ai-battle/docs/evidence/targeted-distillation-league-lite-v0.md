---
project: MAGE-PTCG
slice: C5
status: implemented-fixture-contract-validated
as_of: 2026-07-15
---

# C5 Targeted Distillation / League-lite v0

## 結論

C5のoffline infrastructureは実装済みで、synthetic fixtureによる契約検証を完了した。B1〜B3の独立レビュー修正は承認済みであり、C5 infrastructure foundationは **GO** とする。ただしactual cabt recordsは0件、actual league gamesは0試合で、実性能は未評価である。Student/C3 runtime promotionは **NO_DECISION**、Championおよびsubmission defaultはRule Agent v0のままである。

## 実装範囲

- `canonical-decision-v1`はpublic observation、許可済みown private state、短い公開履歴、public Stable ActionKey payload、Rule/Student/C3 opinion、source/provenance/privacy metadataをcanonical JSONとhashで保存する。C4 Rule BCのprivate ActionKey core digestは変換中だけに限定し、C5 recordへ保存しない。
- validatorはschema/hash、合法teacher label、重複record、forbidden key、NaN/Infinity、source revision、public action digestをfail closedで検査する。episodeとnear-duplicate connected componentを同一splitに置く。
- selectorはRule/Student disagreement、Student fallback/low margin、rare type/family、Rule rankingの近接、actual-cabtと明示されたC3 disagreementだけを用いる。episodeとnear-duplicate quotaを固定し、入力順不変である。
- registryは`rule-agent-v0`、`student-v0`、`bounded-search-v0`、external予約entryをversion/capability付きで管理する。C3は`public_engine_adapter` capabilityなしでは利用不可である。
- League-liteはdeterministic seed schedule、Champion/Challenger side swap、deck/config/environment provenance、atomic resumable manifestを定義する。公開かつprivacy-safeなcabt runnerがないため、実行CLIは`CAPABILITY_UNAVAILABLE`で非成功終了する。
- promotion gateはactual cabt provenance、正のgame数、環境version、legal 100%、安全指標、latency、clean artifact、再現性、paired CIを要求する。fixture、synthetic、0 game、欠損evidenceは`NO_DECISION`である。

## 独立レビュー Blocking findings B1〜B3 の修正

- B1: Rule BCからpublic action identityへ変換する際、教師選択数とpublic ID数が縮退したらfail closedにした。record validatorもre-hash後を含めて`min_count <= len(chosen_action_ids) <= max_count`、ID型、重複、legal candidate所属を検査する。optional selectionの`min_count=0`かつ空選択は維持する。
- B2: split manifestを`c5-episode-near-duplicate-split-v2`へ更新し、config・seed・入力content hash・episode/near-duplicate component hashから決定的に再構成して、assignments・counts・hashを完全照合する。`validate --selection`はcanonical datasetとselection manifestからsubsetを再構成し、そのsubsetのsplit manifestを事後検証する。subset manifestをselectionなしのfull datasetへ適用するとexit 2で拒否する。
- B3: selection manifestを`targeted-selection-v2`へ更新し、canonicalなrecord ID順、source dataset hash、policy config、selected entriesとcomponent scoresを含むselection hashを再検証する。さらにpolicyを決定的に再実行してmanifest全体を照合するため、ID追加・削除、hash流用、入力順変更、別datasetへの流用をfail closedにする。

このv2 manifestはv1 manifestを受理しない明示的な互換性境界である。canonical decision recordのschemaは変更していない。

## CLI

`python scripts/c5_distillation.py --output-dir <dir> <command>`を入口とする。commandは`build`、`validate`、`select`、`convert`、`train`、`evaluate`、`registry`、`league`、`gate`、`report`である。

- exit 0はcontract処理成功、2はunsafe/invalid input（`quarantine/`へ理由を保存）、3はactual cabt capability unavailableである。
- C4 Rule BC v1はactual cabt provenanceをattestできないため、`build --actual-cabt`は拒否する。fixtureをactualへ再ラベルして集計へ混ぜない。
- `train`はC4 trainerを再利用し、model hash、input dataset hash、configのprovenance sidecarを作る。offline fidelityは実性能と扱わない。

## 検証

2026-07-15に次を実行した。

```bash
python -m pytest tests/test_targeted_distillation_v0.py tests/test_student_v0.py tests/test_bounded_search_v0.py -q
```

初回C5受入時の結果は41 passed、全repositoryは494 passed、warning 3件（既存Pydantic deprecation）であった。これは今回のレビュー修正後の値と混同しない。

B1〜B3修正後はC5 focused 14 passed、C5関連regression bundle（`test_targeted_distillation_v0.py`、`test_student_v0.py`、`test_bounded_search_v0.py`、`test_rule_agent.py`、`test_submission_artifact.py`）は74 passed、warning 3件である。追加分はre-hash済みselection bound、duplicate public identity、component全体移動、assignment追加/削除、seed/config改ざん、selection ID追加/削除、source/selection hash改ざん、shuffled input、selection付きCLI事後検証を含む。

統合後はLeague planのunknown field、必須field欠損、不正型をbare tracebackでなくquarantine付きexit 2へ分離する最小 hardeningを追加した。C5 focusedは15 passed、C3/C4/Rule/Knowledgeを含むintegration regressionは137 passed、repository suiteは499 passed、warning 3件である。単一command上限のため全node IDを3 batchへ分割し、collect比較で重複・欠落・追加はいずれも0件と確認した。

Rule v0 submissionは修正後にも2回buildしてtar.gzのSHA-256が一致し、verifyのrepo外`python3 -I` clean-room importをpassした。submission artifactはRule v0 runtimeだけであり、training/league依存を含まない。

## 未実施と判定

| 項目 | 状態 | 根拠 |
|---|---|---|
| actual cabt data collection | NOT DONE | 正規trace adapterと認証済み環境がない |
| actual league evaluation | NOT DONE | documented public cabt runnerがない |
| Student retraining | NOT DONE（actual） | fixture pipelineのみ動作確認 |
| Student runtime promotion | NO_DECISION | actual paired evidenceなし |
| C3 runtime promotion | NO_DECISION | public EngineAdapter/actual paired evidenceなし |

actual cabt recordsは0件、actual league gamesは0試合のままである。fixtureのfidelity、model優劣、League勝率は報告しない。0 gameを勝率0%や評価済みとして保存しない。
