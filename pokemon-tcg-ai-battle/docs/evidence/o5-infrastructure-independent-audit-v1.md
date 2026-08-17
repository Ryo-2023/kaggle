---
title: O5 Infrastructure Independent Audit v1
date: 2026-07-21
base_commit: 514a56a6f74b592b9a8401870d378cb0bfc482b1
status: audit-complete-approved-after-fixes
---

# O5 Infrastructure Independent Audit v1

## 結論

`514a56a..7524529`（O5 Versioned Benchmark／Candidate Activation実装、6コミット）を、実装者の報告を事実と仮定せず、コード・テスト・実際のCLI実行から再検証した。18監査項目のうち**4件で実際に再現可能なバグを発見**し、いずれも同じfeature branch上で最小修正・回帰テスト追加・実cabt再検証を行った。修正後、focused 469、full regression 1592 passed（0 failed）、security/privacy/protected/submission/runtime-isolation 76 passed、docs 12/12、diff check・conflict marker・secret scanすべてクリーンを確認し、O5 evaluation infrastructureをcanonicalへの統合可能と判定した。

## 発見したバグ（4件、いずれも実際に再現して確認）

| # | 内容 | 再現方法 | 修正commit |
|---|---|---|---|
| 1 | top-level manifest/report fileが`{benchmark_kind}__{candidate_agent_id}`のみで命名されており、同じ`--output-dir`へ異なる`--benchmark-id`/`--benchmark-version`/`--games-per-member`で2回実行すると1回目のmanifestファイルが無警告で上書きされる | 実際にCLIを2回実行し、2回目実行後に1回目のmanifest_hashがそのディレクトリから復元不能になることを確認 | `727b17f` |
| 2 | `attribution_available`／`attribution_missing_games`が`NOT_OBSERVABLE`のみを未観測として扱い、観測されたが分類不能な`"UNKNOWN"` seat statusを「完全にattributed」と誤って報告していた | `champion_status="UNKNOWN"`を返す`run_match`で`run_actual_league`を実行し、`attribution_available`が`True`のままであることを確認 | `88fdcbc` |
| 3 | `win_rate`／`wilson_ci_95`がfallback-assisted試合とfallbackなし試合を区別せず合算しており、fallback勝利が純粋なNeural方策の勝利と同一視される設計だった | `champion_fallback_count`が試合ごとに異なる値を返す`run_match`で検証し、win_rateが混在していることを確認 | `a13f912` |
| 4 | `seed_set`に重複値を許容しており、`run_o5_benchmark`が同一の完了済み試合をresume経由で複数回読み込み、aggregate統計へ二重計上していた | `seed_set=(1,1,2), game_count=2`で実行し、本来4試合のところ6試合と誤計上されることを実際に確認 | `11a0544` |

いずれも、実際の評価結果（`docs/evidence/o5-candidate-activation-v1.md`記載の49.0%／68.5%／71.0%等）には影響しない：#1と#4は当該runで使用したseed/output-dirの組み合わせでは発生条件を満たさず（seedは全てunique、各benchmarkは専用output-dirを使用）、#2は`agent_status`が常に`DONE`/`INVALID`/`ERROR`のいずれかで観測され`UNKNOWN`は発生せず、#3はfallback使用試合が0件だったため。ただし、インフラとして再発しうる欠陥であり、修正せずに正典統合することは認められないと判断した。

## 18監査項目の判定

| # | 項目 | 判定 |
|---|---|---|
| 1 | Candidate artifact ID／hash／schema拘束 | PASS（model_hashが文書全体のcontent hashであるため、dataset_hash／config_hash／feature_schema_version等は明示検証がなくても`model_hash`一致検証によって推移的に保証される。この構造を確認した） |
| 2 | 別Candidate/config/manifestのresume混入不可能性 | FIXED（#1, #4） |
| 3 | manifest hashの全意味フィールド拘束 | PASS（`VersionedBenchmarkManifest`の全dataclass fieldが`_public_payload()`に含まれることをコードで確認） |
| 4 | Benchmark ID／Versionのimmutable性 | FIXED（#1）。ただし同一(id, version)ラベルで異なる内容のmanifestを構築すること自体は禁止していない（content hashで区別されるため実害はない） |
| 5 | Performance/Safety分離（集計・artifact・CLI） | PASS（`benchmark_kind`によるsets構造的分離を確認） |
| 6 | seat 0/1勝敗帰属 | PASS（`test_play_closure_attributes_seat_and_winner_correctly_when_champion_is_seat_1`等で確認） |
| 7 | invalid/exception/timeout/fallbackのactor帰属 | PASS（実cabtでcandidate側0・opponent側は設計どおりの件数と一致することを再確認） |
| 8 | UNKNOWN/NOT_OBSERVABLEを誤って0処理しないか | FIXED（#2）。件数自体は元々正しく0だったが、完全性flagが誤っていた |
| 9 | Wilson interval/decided denominator | PASS |
| 10 | fallback試合を純粋なNeural勝率へ混ぜていないか | FIXED（#3） |
| 11 | partial artifact/atomic write/resume/duplicate除外 | FIXED（#4）。atomic write自体はtempfile+os.replaceで元々安全 |
| 12 | CLIでCandidate省略/暗黙fallback | PASS（`--candidate-agent-id`は`required=True`でdefaultなし） |
| 13 | dry-run/本実行のmanifest意味一致 | PASS（前回修正済み、cabt_versionを常にprobe） |
| 14 | Experimental Pilotのdefault経路到達 | PASS（grep確認、定義・テスト・`__all__`以外に参照なし） |
| 15 | main.py/submission runtimeからの到達不能性 | PASS（`test_competition_intelligence_runtime_isolation.py`維持） |
| 16 | review packetの署名/承認捏造なし | PASS（`allowed_use`は常に空配列、テストで固定） |
| 17 | private情報/secret/大容量artifactの混入 | PASS（最大artifact 76KB、secret scanは偽陽性1件のみ） |
| 18 | docsの数値とmachine-readable evidenceの一致 | PASS（主要な勝率・CI数値を実JSONと直接照合） |

## 検証結果

```text
focused: 469 passed
full regression: 1592 passed, 0 failed, 5 warnings（既存・無関係）
security/privacy/protected/submission/runtime-isolation: 76 passed
docs validation: 12/12
diff --check: クリーン
conflict marker scan: 0件
secret scan: 偽陽性1件のみ（実秘密情報なし）
protected files（main.py/deck.csv/rule_agent.py/rule_agent_v1.py/promotion.py）: 無変更
```

実cabt再検証: 修正後にneural_actual_trained候補で実cabt smoke（performance/safety両方）を再実行し、candidate_invalid/exception/timeoutが0のまま、fallback_breakdownフィールドが正しく機能することを確認した。

## 判定

```text
APPROVED_AFTER_FIXES
```
