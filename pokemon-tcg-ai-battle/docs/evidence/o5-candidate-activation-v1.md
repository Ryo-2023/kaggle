---
title: O5 Actual Student Candidate Activation, Performance/Safety Split, Fault Attribution
date: 2026-07-21
base_commit: 514a56a6f74b592b9a8401870d378cb0bfc482b1
status: infrastructure-complete-real-cabt-executed-candidate-promotion-not-decided
---

# O5 Actual Student Candidate Activation, Performance/Safety Split, Fault Attribution

## 結論

前回実装（[o5-versioned-benchmark-evaluation-runner-v1.md](o5-versioned-benchmark-evaluation-runner-v1.md)）を敵対的に再監査し、manifest hashのcandidate拘束不足、Benchmark artifactのcandidate間衝突可能性、`--dry-run`とreal実行でmanifest内容が変わる問題を修正した。`NEURAL_ACTUAL_TRAINED`（`offline-long-run-actual-20260718-r1` lineage、model_hash `94564328a10f…`）をhash-pinned Candidate Factoryとして安全に接続し、実cabtで**2件の実装バグ**を発見・修正したうえで、Safety BenchmarkとPerformance Benchmarkを完全分離し、agent／seat別のfault直接帰属を実装した。Actual Student（`neural_actual_trained`）対Rule Agent v0の実cabt評価を**200試合**（100 logical pair、常時seat swap、10 seed）実行し、勝率**49.0%**（95% Wilson CI **[0.422, 0.559]**、50%を含む）を記録した。Candidateのinvalid／exception／timeout／fallbackは合計680試合中すべて0であり、legal 100%を直接測定で確認した。Champion／submission defaultはRule Agent v0のまま不変。

## 1. 前回実装の敵対的監査結果

| # | 監査項目 | 判定 | 詳細 |
|---|---|---|---|
| 1 | Benchmark manifest hashがcandidate artifact／opponent／seed／seat swap／game count／設定全体を拘束しているか | FIXED | `candidate_artifact_id`（人間可読ラベル）だけではmodel checkpointを区別できなかった。`candidate_artifact_hash`をmanifestへ追加しhashへ拘束した（[o5_benchmark.py](../../src/mage_ptcg/competition_intelligence/o5_benchmark.py)） |
| 2 | 同じBenchmark ID／Versionで中身の異なるmanifestを生成できないか | FIXED | manifest_hashは`benchmark_kind`／`candidate_artifact_hash`を含む全設定から決定的に算出される。CLIはmanifest／reportファイル名へ`{benchmark_kind}__{candidate_agent_id}`を含め、異なる設定を同一ファイル名へ書き込ませない |
| 3 | resume時に別candidate／別config／別manifestの古い結果を誤再利用しないか | FIXED | per-member/seed artifactを`manifest_hash`の先頭16文字で名前空間化した。異なるmanifestは物理的に別ファイルになるため、`league.actual_runner`自身のconfig_hash不一致検知（既存、ValueErrorでfail-closed）に加えて構造的にも衝突し得ない |
| 4 | partial artifactが途中で壊れた場合にfail-closedまたは安全に再開できるか | PASS | `atomic_write_json`はtempfile＋`os.replace`によるatomic writeであり、途中破損状態は原理的に発生しない。仮に破損ファイルが存在してもJSON decode失敗でfail-closedにraiseする（既存動作、変更なし） |
| 5 | CandidateとOpponentのseat、勝敗、invalid、crash、timeoutが逆転して記録される経路がないか | FIXED＋実測確認 | `play()`のseat入替ロジック自体は既存`run_actual_league.py`と同型で問題なかったが、単体テストが存在しなかった。`test_play_closure_attributes_seat_and_winner_correctly_when_champion_is_seat_1`等を追加し、championが seat 1 のケースを含めて検証した |
| 6 | `decided_games`、draw、invalid、crash、timeoutの分母が指標ごとに正しいか | PASS（前回修正を維持） | `win_rate`／`wilson_ci_95`は`decided_games`（=wins+losses+draws）を分母に統一済み。回帰テストで維持を確認 |
| 7 | fault injection opponentとの試合を通常の性能勝率へ混ぜていないか | FIXED（構造的に不可能化） | 前回は`sets`が単一manifestに混在していた。`benchmark_kind`（`performance`／`safety`）でmanifest自体を分離し、safety opponentは`performance` manifestの`sets`へ物理的に含まれない設計へ変更した |
| 8 | CLIの`--dry-run`と実行モードでmanifestの意味が変わらないか | FIXED | 前回は`--dry-run`時に`cabt_version="unknown"`固定だったため同じ引数でも`--dry-run`の有無でmanifest_hashが変わっていた。dry-runでも常に`diagnose_cabt_capability()`を実行するよう修正し、両モードで同一manifestになることをテストで確認した |
| 9 | Experimental Pilot Profileがdefault経路へ到達しないか | PASS | `EXPERIMENTAL_PILOTS`は`DEFAULT_PILOTS`／`build_opponent_population`のデフォルト引数に一切現れない。grepで使用箇所が定義・テスト・`__all__`のみであることを確認 |
| 10 | O5関連コードがsubmission packageや`main.py`から到達不能であること | PASS | `tests/test_competition_intelligence_runtime_isolation.py`は今回のfull regressionに含まれ変わらずPASS。新規モジュールは全て`mage_ptcg.competition_intelligence`配下 |

## 2. Candidate Artifact

「最新っぽいファイル」を推測選択せず、`docs/evidence/offline-training-v1-long-run-20260718.md`／`.json`と、実際のexport JSON自身が宣言する`schema_version`／`model_hash`／`feature_schema_hash`／`feature_schema_version`／`dataset_hash`／`config_hash`をそのまま読み取って固定した。

| 項目 | 値 |
|---|---|
| `candidate_artifact_id` | `neural_actual_trained` |
| `candidate_model_hash` | `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4` |
| `feature_schema_hash` | `552d3bf4c4792d84fc509bfa51c322e23e84dd6c04697f0dab8dca80ea864484` |
| `feature_schema_version` | `student-v0-features-v1` |
| `dataset_artifact_id` | `offline-long-run-actual-20260718-r1` |
| `dataset_hash`（export内`dataset_hash`） | `a3ba4c1cd2903491d2e5e3489907ac8a4b179fba840cecbb3332a8a7b942ff60` |
| `training_config_hash`（export内`config_hash`） | `22e08ebebb9f59134ecb5e61330d7757e5a7ae9f5dc934449476445643a2bd78` |
| `action_schema_version` | `NOT_APPLICABLE`（このcodebaseにaction schemaという独立概念は存在しない。捏造せず明示） |
| `model_format_version` | `offline-training-v1-neural-export-v1` |
| `source_commit` | `062533feee8ac91914d10fd67231181f6ef7949e` |

Loaderは既存の`mage_ptcg.offline_training.neural_runtime.NeuralRuntimePolicy.load(path, expected_feature_hash=..., expected_model_hash=...)`をそのまま再利用した（[o5_candidate_factory.py](../../src/mage_ptcg/competition_intelligence/o5_candidate_factory.py)）。model_pathはCLI引数として要求し、ソースコードへローカル絶対パスをハードコードしていない。

Load時のhash不一致・欠損ファイル・export自己不整合はすべてfail-closedで`O5CandidateError`を送出し、candidateを構築しない（テストで実際にself-consistentだが異なるmodelを検出できることを確認）。推論時に`NeuralRuntimePolicy.choose()`がNoneを返した場合のみRule Agent v0へ決定的fallbackし、`fallback_count`／`last_fallback_reason`を直接記録する。private fieldは一切参照しない（`build_decision_state`のactor-visible契約をそのまま利用）。

## 3. 実cabtで発見した実装バグ（2件）

1. **deck提出コールの誤処理**（前回Sliceの`o5_adversarial_agents.py`と同型のバグを本Sliceでは`o5_candidate_factory.py`が最初から正しく実装済み。前回発見済みの教訓を適用したため今回は再発しなかった）
2. **kaggle_environmentsの呼び出し規約不一致**（新規発見）: `kaggle_environments.agent.Agent.act()`は`agent.__code__.co_argcount`を見て呼び出し引数を1個（observationのみ）へ切り詰めるが、class instanceの bound `__call__`には`__code__`属性がなく、常に`(observation, configuration)`の2引数で呼ばれる。本リポジトリの既存agentは全てplain functionを返すfactoryだったためこの問題が顕在化しなかったが、`NeuralCandidateAgent`はclass instanceであり、実cabt実行で全試合が`TypeError: NeuralCandidateAgent.__call__() takes 2 positional arguments but 3 were given`により`AGENT_ERROR`になっていた。`debug=True`でkaggle_environmentsの例外を可視化して原因を特定し、`__call__(self, obs_dict, configuration=None)`へ修正した。修正後は実cabtでcandidate_exception 0を確認した。

## 4. Benchmark（Performance／Safety分離）

| | Performance（neural vs rule_v0/random_legal） | Performance control（rule_v0 vs random_legal） | Safety（neural vs fault agents） |
|---|---|---|---|
| benchmark_id | `o5-benchmark-neural-vs-rule-v0-v1` | `o5-benchmark-rule-v0-control-v1` | `o5-benchmark-neural-safety-v1` |
| benchmark_kind | performance | performance | safety |
| candidate_artifact_id | `neural_actual_trained` | `rule_v0` | `neural_actual_trained` |
| candidate_artifact_hash | `94564328a10f…` | `NOT_APPLICABLE` | `94564328a10f…` |
| manifest_hash | `6b37f971556e9917c0709b8b74d17d4174bcb580ca918fa317a738e8cc4ad6a3` | `6908a74dfcb8032835b797a925c93b9696fa1b7ea294e74886ade63079b4a273` | `384e14ea89d7389b9918351a0a702f24f90630c1099c562a822f34a0f196de2a` |
| commit | `e7ed86e` | `e7ed86e` | `e7ed86e` |
| seeds | 93000〜93009（10個） | 93000〜93009（同一10個） | 94000〜94003（4個） |
| games/member/seed | 20 | 20 | 4 |
| 総試合数 | 400（vs rule_v0 200、vs random_legal 200） | 200 | 80（5 member × 16） |

manifest／report全文は [o5-candidate-activation-v1/](o5-candidate-activation-v1/) を正とする。

## 5. Actual Student評価結果

### neural_actual_trained 対 Rule Agent v0（主要比較、200試合、100 logical pair）

| 指標 | 値 |
|---|---|
| total_games | 200 |
| decided_games | 200 |
| logical_pairs | 100 |
| candidate_wins（wins） | 98 |
| champion_wins（losses） | 102 |
| draws | 0 |
| candidate_win_rate | **49.0%** |
| Wilson 95% CI | **[0.4216, 0.5588]**（50%を跨ぐ） |
| seat_0（先手）win rate | 51/100 = 51.0% |
| seat_1（後手）win rate | 47/100 = 47.0% |
| seat asymmetry | 4.0pt（試行数100で有意性は主張しない） |
| candidate invalid／exception／timeout | 0／0／0 |
| candidate fallback games／total | 0／0（本評価では推論失敗が一度も発生していない） |
| opponent（Rule v0）invalid／exception／timeout | 0／0／0 |
| attribution_available | true（680試合全件で観測完了） |
| latency p50／p95／max（秒） | 0.190／1.358／1.685 |
| game length p50／p95（手数） | 34.5／176.0 |
| reproducible | true |

**観測事実と既知の不確実性**: この49.0%（CI [0.422, 0.559]）は、同一model_hash（`94564328a10f…`）に対して過去記録されている[offline-training-v1-long-run-20260718.md](offline-training-v1-long-run-20260718.md)の57.75%（95% CI [0.5286, 0.6250]）と重ならない。原因は未確認であり、以下のいずれか、または複数の組み合わせと考えられる。

- cabtのengineは`engine_seed_supported=false`であり、名目上の「seed」はagent側RNGのみを制御する。本評価のseed（93000〜93009）と過去評価のseedは異なるため、エンジン内部の非決定性による純粋なsampling varianceの可能性がある。
- 評価対象の候補プールの違い（本評価はrandom_legal／rule_v0の2種のみ、過去評価は不明な相手構成）。
- 評価に使うagent factoryの違い（本評価は`neural_runtime.NeuralRuntimePolicy.load`を直接使用。過去評価が同一経路かは本証跡単独では確認できていない）。

いずれも未確認のため、どちらの数値がより「正しい」かを断定しない。両方を事実として記録し、次回以降の追加評価（複数の独立したseed batchでの再現、evaluation harnessの経路一致確認）が必要な既知の限界として扱う。

### neural_actual_trained 対 random_legal（control比較、200試合、100 logical pair）

| 指標 | 値 |
|---|---|
| candidate_win_rate | 68.5%（137勝63敗、decided 200） |
| Wilson 95% CI | [0.6177, 0.7454] |
| candidate invalid／exception／timeout／fallback | 0／0／0／0 |

### Rule Agent v0 対 random_legal（同一条件control、200試合、100 logical pair）

| 指標 | 値 |
|---|---|
| candidate_win_rate | 71.0%（142勝58敗、decided 200） |
| Wilson 95% CI | [0.6436, 0.7685] |
| candidate invalid／exception／timeout | 0／0／0 |

neural（68.5%、CI [0.618, 0.745]）とRule v0（71.0%、CI [0.644, 0.768]）のrandom_legalに対する性能はCIが大きく重なり、同程度のbaseline耐性を示す。

### neural_actual_trained Safety Benchmark（80試合）

| Opponent | games | decided | candidate_invalid | candidate_exception | candidate_timeout | candidate_fallback | opponent fault |
|---|---|---|---|---|---|---|---|
| exception_agent | 16 | 0 | 0 | 0 | 0 | 0 | opponent_exception 16/16（設計どおり） |
| invalid_artifact | 16 | 0 | 0 | 0 | 0 | 0 | opponent_invalid 16/16（設計どおり） |
| unknown_selection | 16 | 0 | 0 | 0 | 0 | 0 | opponent_invalid 16/16（設計どおり） |
| slow_agent | 16 | 16 | 0 | 0 | 0 | 0 | opponent fault 0（legal） |
| random_legal | 16 | 16 | 0 | 0 | 0 | 0 | opponent fault 0（legal） |

Candidate（neural_actual_trained）は、fault-injection opponentとの対戦を含む全80試合でinvalid・exception・timeout・fallbackすべて0。Safety Benchmarkの結果はPerformance Benchmarkの勝率集計へ一切混入していない（`benchmark_kind`による構造的分離、[o5_evaluation.py](../../src/mage_ptcg/competition_intelligence/o5_evaluation.py)）。

## 6. Current Meta Population

- exact deck候補数: 31（すべてTEAM_SHARED、PUBLIC_OTHER／OWN_KAGGLEは0件）
- active数: 0
- blocked理由: 31件全件が`TEAM_SHARED_PENDING_PERMISSION`（署名済みteam-artifact-permission-v1manifest不在）。Rules attestationは`UNVERIFIED_RULES_CONSTRAINT`のままだが、このsnapshotにPUBLIC_OTHER／OWN_KAGGLE decksは0件のため、Rules attestation自体は今回の31件を直接ブロックしていない
- review packet: [o5-current-meta-review-v1/](o5-current-meta-review-v1/)（`team_permission_deck_review.md`／`.json`、`rules_attestation_deck_review.md`／`.json`、`current_meta_review_summary.json`）
- 人間が必要な操作: branch ownerまたはrepository adminが`team_permission_manifest_templates/permission.template.json`へ`provider_id_hash`／`repository`／`commit_or_branch`／`artifact_selectors`／`allowed_use`を記入し、`reviewed_at`／`reviewed_by_hash`を付けて署名する。PUBLIC_OTHER decksが将来観測された場合は別途`rules_attestation_template.yaml`のVERIFIED化が必要
- 承認後の再開コマンド: 署名済みmanifestを`o5_activation.TeamPermissionManifest.from_mapping()`で読み込み、`activate_artifacts()`でdeckを活性化した後、既存`o5 acquire-environment-top-decks`／registry再分類を実行し、`current_meta`が非空になった新しいVersioned Benchmark manifestで本Evaluation Runner（`scripts/run_o5_benchmark.py`）をそのまま再実行する。新規コード追加は不要

## 7. テスト

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/competition_intelligence/ tests/test_run_o5_benchmark_cli.py \
  tests/test_actual_league_runner.py tests/test_actual_league_cli.py \
  tests/test_generate_o5_review_packets.py
# 469 passed
```

full regression、docs validation、diff check、secret scan、privacy scan、clean-room、runtime isolationの結果は本証跡の親文書（最終報告）にまとめる。

## 8. 情報境界

- `NeuralCandidateAgent`はactor-visibleな`obs_dict`のみを参照し、`build_decision_state`の既存privacy境界をそのまま継承する。private fieldへの直接アクセスは追加していない
- Candidate model artifactの実体（1.3MB JSON）はgit管理外（`runs/`、既存`.gitignore`）のまま。本証跡には識別用hashのみを記載し、artifact本体やローカル絶対パスは記載していない
- Rules attestationとTeam permissionは捏造していない。31 deckは引き続きすべて非activeであり、本Sliceのどの実行もこのgateを迂回していない
- Champion／submission default（Rule Agent v0）、Kaggle提出は不変

## 9. 既知の限界

重要度順。

1. **57.75%（過去評価）と49.0%（本評価）の差異が未解明**（重要度：高）。同一model_hashに対する評価結果の不一致であり、Candidate Promotion判断に直接影響する。次回、同一evaluation harness経路であることを確認したうえで、複数独立seed batchでの再現評価が必要
2. **archetype population は引き続きblocked**（重要度：高）。31 TEAM_SHARED deckはすべて署名待ち。`current_meta`評価は今回も0試合
3. **seat非対称性の統計的評価は未実施**（重要度：中）。51.0% vs 47.0%のseat別勝率差を、試行数100のみで有意性判定していない
4. **per-seat fault帰属はcabtの`agent_status`配列に依存**（重要度：中）。この配列の意味論（"ERROR"と"INVALID"の区別等）はcabtのドキュメント化された仕様ではなく実測に基づく。将来のcabtバージョンで値が変わる可能性がある
5. **シナリオレベルadversarial（setup事故等）は引き続き未実装**（重要度：低、前回証跡から不変）。cabtに確認済みのseed制御／局面注入APIがないため
6. **候補間比較（neural同士のseed違い等）は未実施**（重要度：低）。今回はmodel seedが単一のため対象外

## 10. 正典統合手順

review range、検証コマンド、merge候補は最終報告（会話内）を正とする。実際のmerge／push／worktree削除は行っていない。
