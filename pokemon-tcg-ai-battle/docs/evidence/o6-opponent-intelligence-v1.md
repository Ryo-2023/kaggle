---
title: O6 Opponent Intelligence Platform v1
date: 2026-07-21
base_commit: 5be120ceb0eb31aa6161dc8eab1cdf88180421cb
status: independent-audit-phase-a-remediated
---

# O6 Opponent Intelligence Platform v1

## 結論

Population identity を caller 指定文字列から content-derived hash（`population_identity_hash` の先頭16桁を含む `team-agents-v1-<hash16>` 形式）へ変更し、`population_id` を `build_population()` の引数から削除した。各 VALIDATED Native Team Agent について、pinned commit から materialize した source tree・adapter 仕様・dependency 記述・per-file SHA-256 を含む Portable Runtime Bundle（`runtime/<agent_id>/...`）を実装し、`/home/bfe-lab-ono/kaggle/opponent-artifacts/store`（`/tmp` 以外の永続 path、CLI `--artifact-store` で指定、コードへハードコードなし）へ publish した。Publish 後、O6 worktree の git 履歴を一切参照しない fresh client 相当の手順（別 cache dir、fetch→hash verify→extract→isolated cabt smoke）で 3 VALIDATED Agent 全件の実行に成功した。3 Team Agent + Rule Agent v0 の全組合せ round-robin League（6 pair × 10 games = 60 games）を実行し、invalid/crash/timeout は 0 件だった。探索系2 source は `UNSUPPORTED_RUNTIME` のままだが、静的推定ではなく実測プロファイリングで根本原因を特定した（[search_agent_runtime_diagnosis.json](o6-opponent-intelligence-v1/search_agent_runtime_diagnosis.json)）。Champion/default Rule Agent v0、`main.py`、`deck.csv`、`promotion.py` は不変。

## Population Identity（content-derived）

- 導出元: schema version、sorted opponent_ids、sorted source_snapshot_ids、sorted source_commit_shas、deck/agent/strategy registry hash、permission policy hash、validation summary hash（timing 等の非決定要素は除外）、adapter/runtime contract version、ruleset version、cabt version、selection policy、build config hash。
- 除外: `created_at`、ローカル path、一時 directory、`display_name`、alias。`build_population()` に `population_id` 引数は存在せず、caller は identity を上書きできない。
- 実測 ID: `team-agents-v1-a3976401cae4fd38`（`population_identity_hash` = `a3976401cae4fd386c4e962b402e6303c69706337432cc6343b7c42654200370`、`manifest_hash` = `f60a40f4da43fee7a699693236e19910eea2624502f44c136d0d49652b278a50`）。`display_name` は `team-agents-20260721-v1-validated3` としたが identity には不参加。
- テスト `test_population_id_is_content_derived_and_order_independent`（`tests/opponents/test_core.py`）で以下を確認: 同一内容・別 display_name → 同一 ID、member 順序入替 → 同一 ID、source commit 変更 → 別 ID、permission policy hash 変更 → 別 ID、adapter version 変更 → 別 ID。`test_build_population_has_no_caller_id_parameter` で caller 指定不可を確認。
- 全 manifest fields: [population_manifest.json](o6-opponent-intelligence-v1/population_manifest.json)。

## Portable Runtime Bundle

- 構造: `runtime/<agent_id>/{runtime_manifest.json, adapter.json, dependencies.json, deck/deck.json, hashes.json, source/**}`。`source/**` は pinned commit から `TeamBranchCollector.materialize()` で git object 読み出しのみで再構成した tree（symlink・submodule は inventory 段階で quarantine 済み）。`.git`／credential／Kaggle token／user HOME／private data／venv／cache／large run log は含まない。
- `hashes.json` は全 file の per-path SHA-256 と `bundle_sha256` を持ち、実行前に fresh client 側で再検証する（`run_fresh_client_smoke`）。安全な展開は `safe_extract_tar_gz()` が担い、symlink/hardlink member・`..`／絶対 path member を拒否する（`test_safe_extract_rejects_traversal_and_symlinks`）。
- 実測: 3 agent 分で計 315 file、store 内 `runtime/` 総量 27MB。
- visibility: `adapter.json.visibility = "TEAM_INTERNAL_ONLY"`。Team Permission が許可する `team_redistribution` の範囲でのみ配布し、public redistribution は含めていない。
- launch は `no_implicit_pip_install: true`、`fail_closed_on_missing_dependency: true`。`dependencies.json` は source 側に requirements 宣言が無いため `resolution: "NOT_DECLARED_BY_SOURCE"` とし、host interpreter の site-packages に依存する旨と、不足時は `BLOCKED_DEPENDENCY` へ fail-closed する旨を明記した（捏造の requirements.txt は書いていない）。
- subprocess は起動ごとに独立 HOME（`tempfile.mkdtemp`）、`cwd`/`PYTHONPATH` をbundle source root に固定、stdout/stderr size 上限、timeout、`finally` での確実な `shutil.rmtree` による state reset を行う。OS レベルの network namespace 分離は実装していないため `NETWORK_ISOLATION_UNAVAILABLE` を維持する（"network isolated" とは主張しない）。

## 永続 Artifact Store

- 実体: `/home/bfe-lab-ono/kaggle/opponent-artifacts/store`（環境固有の絶対 path。CLI 引数として渡しており、コード中にはハードコードしていない。Population identity にも含まれない）。`LocalArtifactStore.__init__(root)` は任意 path を受け付ける。
- 実装した要件: staging→`os.replace` による atomic publish、manifest は最後に書く（bundle.tar.gz と validation_summary.json の後）、`fcntl.flock` による population 単位の advisory lock、同一内容の再 publish は idempotent（`test_store_refuses_unapproved_and_detects_manifest_corruption`）、同一 ID・別内容は拒否（`test_store_rejects_same_id_different_content`）、`fetch()` は manifest hash と bundle sha256 を再計算して検証、`fetch_to_cache()` は store と別 directory へコピーしてそこで再検証するため cache 破損を検知できる（`test_store_fetch_to_cache_is_isolated_and_verified`）、`aliases/latest-approved.json` の atomic 更新、`store_index.json` によるストア内一覧、`export_bundle()` によるポータブル単一 tar.gz 出力。旧 caller-supplied snapshot（`/tmp/o6-team-population-store` 配下の `team-agents-20260721-v1-validated3` 等）は削除・改変せず obsolete として残置した。
- Git には schema／小 fixture／manifest／hash／doc／evidence のみを commit し、runtime bundle 本体（27MB）は commit していない。

## Fresh-client Actual Smoke

以下は O6 worktree の `.git` 履歴を一切参照しない手順で実行した（別 `HOME`、別 cache dir、`origin/agents/*` の checkout なし、population snapshot の fetch とその場での hash 再検証のみ）:

```text
python -m mage_ptcg.opponents list-populations --artifact-store <durable-store>
python -m mage_ptcg.opponents fetch team-agents-v1-a3976401cae4fd38 --artifact-store <durable-store> --cache-dir <fresh-cache> --offline
python -m mage_ptcg.opponents list --population team-agents-v1-a3976401cae4fd38 --cache-dir <fresh-cache>
python -m mage_ptcg.opponents inspect <opponent-id> --population team-agents-v1-a3976401cae4fd38 --cache-dir <fresh-cache>
python -m mage_ptcg.opponents smoke <opponent-id> --population team-agents-v1-a3976401cae4fd38 --cache-dir <fresh-cache> --timeout-seconds 30
```

3件全て `exit_status: "OK"`、`legal_action_validation: "CABT_SMOKE_PASS"`、`bundle_hash_verified: true`、`no_source_branch_checkout: true`、`no_live_worktree_reference: true`、deck 60・`deck_replay_equal: true`・`states: ["DONE","DONE"]`。実測 `runtime_seconds` は 3.2〜4.0 秒、`extract_seconds` は 0.14〜0.16 秒。抽出後の scratch directory は `finally` で確実に削除し、leak を確認していない（`find <cache>/smoke-scratch` が空）。

## Team League v1

3 Native Team Agent + Rule Agent v0 の全 4 者総当たり（6 pair）、各 pair 10 games（side-swap 込み、`mage_ptcg.league.actual_runner.run_actual_league` を再利用）を実測した。

- 総 games: 60、invalid_actions/crashes/timeouts: 全 pair 0、`reproducible: true`（全 pair 完走）。
- graph は 4 者連結（`graph_connected: true`）。Bradley-Terry log-strength（Elo ではない、Champion 自動変更なし）: nihei-alakazam系 `0.331`、他2 Team Agent `0.1142`、`rule-agent-v0` `-1.0059`（単一 seed 帯・60 game のみの参考値）。
- worst matchup: `ozawa-crustle-rule` vs `rule-agent-v0` = 10–0（win rate 1.0、Wilson 95% CI `[0.7225, 1.0]`）。
- latency（game 単位）: p50 `0.333s`、p95 `0.611s`、max `1.269s`（n=60）。
- 詳細（per-pair raw records、seat 別 W/L/D、fallback/exception attribution）は [league/league_report.json](o6-opponent-intelligence-v1/league/league_report.json) と [league/league_summary.json](o6-opponent-intelligence-v1/league/league_summary.json) を正とする。
- 役割候補（Champion/default は変更していない）:
  - Benchmark Anchor: `rule-agent-v0`（既存 Champion、比較基準として維持）
  - Runtime Champion candidate: 未指定（本 League 単独では昇格判断を行わない、`Promotion: NO_DECISION` 継続）
  - Training Teacher candidate: `ozawa-crustle-rule@9c9a776`（rule-agent-v0 に 10–0、ただし単一条件のみ）
  - Safety Fallback candidate: `rule-agent-v0`（既存の合法性保証を維持）
  - Population Member: 3 Native Team Agent 全件
- 実行 isolation: Team Agent は game ごとに新規 subprocess（`NativeAgentWorker`、game 間で state を共有しない設計）、Rule Agent v0 は host-process 内 trusted code として直接実行。native vs native の 2 pair でも module 名前空間衝突は観測しなかった（各 agent 専用 subprocess のため）。

## 探索系2 Agent の Runtime 診断

`ozawa-metal-psychic-search@8472164` と `water-box-search@3759a983` は引き続き `UNSUPPORTED_RUNTIME` である。20 秒 timeout の実測プロファイリング（`faulthandler.dump_traceback_later` によるスタック捕捉、`cg.utils.to_dataclass`/`cg.api.search_step` の呼び出しカウンタ計測）の結果、単純な累積オーバーヘッドではなく、両 agent が共有する native `cg` package（`cg/libcg.so`）内 `search_step`（ctypes 経由）の単発呼び出しが 6〜16 秒以上ブロックし、さらに 2/2・1/1 の再現試行でセグメンテーション違反（SIGSEGV）を確認した。両 agent が bundle する `libcg.so`（sha256 `ffd89bf9…`）は `kaggle_environments` package 同梱の `libcg.so`（sha256 `7acbfc7b…`）と異なるバイナリである。`ldd`/`readelf --version-info` では glibc/libstdc++ の未解決シンボルは無く、単純な binary 非互換ではない。詳細と生 stack trace は [o6-search-agent-runtime-diagnosis.md](o6-search-agent-runtime-diagnosis.md) と machine-readable [search_agent_runtime_diagnosis.json](o6-opponent-intelligence-v1/search_agent_runtime_diagnosis.json) を正とする。

- 分類: `UNSUPPORTED_RUNTIME`（運用状態）、root cause は owner 側 fix が必要な native library の memory-safety defect の疑いが強い（`NEEDS_OWNER_FIX` 相当だが、このサンドボックス環境固有の可能性を完全には排除できないため運用分類は据え置いた）。
- Agent ロジックは変更していない。`SEARCH_NUM_WORLDS`/`SEARCH_MAX_CANDIDATES`/`ROLLOUT_MAX_SELECTS` 削減などの強さを変える変更は適用していない。
- O6 の per-game isolated-subprocess adapter 設計により、この crash の影響範囲は使い捨て subprocess 内に閉じており、League/validate の host process はクラッシュしなかった。これは今回の isolation 設計を維持すべき根拠である。

## Public Evidence Intake

`collect_public_inbox()` に対し EXACT/DECK_FAITHFUL/BEHAVIORAL_SURROGATE/OBSERVED_ONLY の4分類 fixture と非対応分類を投入するテスト（`test_public_inbox_classifies_four_fidelity_tiers_and_rejects_unsupported`）を追加した。live network 取得は行っていない（要求されていない）。`origin/feature/leaderboard-deck-analysis@8e3962f` と `origin/feature/meta-opponents@00a7c66` は read-only で固定済み。

並行して得られた `pokemon-tcg-ai-battle-o6-research-gemini` worktree（`feature/o6-intelligence-research-gemini`、`origin/main` 起点、O6へ **merge していない**）の `public_source_inventory.json`／`team_agent_strategy_profiles.json` を review input として検証した結果、`content_hash_candidate` が正式 SHA-256 でない（8桁のみ、再計算対象の実体ファイルもキャッシュされていない）こと、カード ID `1122` の名称が文書内で「Nest Ball」と「Pokegear 3.0」に矛盾していること等を確認した。詳細は [o6-gemini-review-corrections.md](o6-gemini-review-corrections.md) を正とする。これらの未検証値は Population identity や分類根拠として使用していない。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q tests/opponents
# 13 passed
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m mage_ptcg.opponents sync-team-branches --repo . --permission-policy configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml --output-dir <build-state> --json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m mage_ptcg.opponents validate --repo . --output-dir <build-state> --timeout-seconds 20 --json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m mage_ptcg.opponents build-registry --repo . --output-dir <build-state> --permission-policy configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml --display-name team-agents-20260721-v1-validated3 --json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m mage_ptcg.opponents publish --output-dir <build-state> --artifact-store <durable-store> --population team-agents-v1-a3976401cae4fd38 --approve --json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m mage_ptcg.opponents fetch team-agents-v1-a3976401cae4fd38 --artifact-store <durable-store> --cache-dir <fresh-cache> --offline --json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m mage_ptcg.opponents smoke <opponent-id> --population team-agents-v1-a3976401cae4fd38 --cache-dir <fresh-cache> --json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 scripts/run_o6_team_league.py --artifact-store <durable-store> --population team-agents-v1-a3976401cae4fd38 --cache-dir <league-cache> --output-dir <league-out> --games-per-pair 10
```

## 既知の制限

- Opponent Instance は3件（目標10件は未達）。振る舞いを捏造せずに作れる approved variant が無いため、seat/seed だけの水増しはしていない。
- League は単一 seed 帯・60 games のみで、Champion 変更判断には使用しない（`Promotion: NO_DECISION` 継続）。
- 探索系2 source は native library crash の疑いで `UNSUPPORTED_RUNTIME` のまま。owner 側の修正待ち。
- `NETWORK_ISOLATION_UNAVAILABLE` を維持（OS レベルの network namespace 分離は未実装）。
- cabt は engine seed 非対応のため、League の `seed` field は帳簿上の pairing 識別子であり、`kaggle_environments.make('cabt', ...)` へ機能的には渡していない。

## 再開条件

Opponent Instance 拡大には、実在する複数 Deck/config/documented search budget variant が新たに承認された場合のみ着手する。探索系2 source は owner 側で native `cg` library の SearchState 解放漏れ疑いを修正した新 commit が提供された場合に再検証する。Champion/default Rule Agent v0、`main.py`、`deck.csv`、`promotion.py` は変更していない。

## Phase A 独立監査是正（2026-07-21、Codex 監査 → Claude 是正）

Codex による独立監査（`/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-team-population-independent-audit`、review range `fe88599..51378c3`）は CONDITIONAL 判定で HIGH merge blocker 2 件を報告した。本節は各 Finding の是正結果を記録する。全 machine-readable evidence は [o6-opponent-intelligence-v2/](o6-opponent-intelligence-v2/) を正とする。

### Finding 別 closure

| finding_id | severity | merge_blocker | 是正 | 検証 |
|---|---|---|---|---|
| O6-AUD-001 | HIGH | 是正済み | `build_agent_runtime_bundle()` を allow-list closure builder（`mage_ptcg.opponents.runtime_closure.build_runtime_closure()`）へ置換 | 3 Agent 全件 fresh-client smoke 3/3 PASS、`tests/opponents/test_runtime_closure.py`（13 tests） |
| O6-AUD-002 | HIGH | 是正済み | 全 game に trajectory digest を保存し、Wilson CI／Bradley-Terry を raw execution と unique trajectory へ分離（`mage_ptcg.opponents.trajectory`） | `tests/opponents/test_trajectory.py`（20 tests）、実 League 60 games 再実行 |
| O6-AUD-003 | MEDIUM | 非 blocker | `runtime_contract.json`（`SOURCE_PORTABLE_HOST_CONTRACT_REQUIRED`）を追加し、host package・version・availability probe・fail-closed 挙動を明記 | 3/3 fresh-client smoke が実測した python/kaggle_environments version と一致確認。以下「未実施の検証」参照 |
| O6-AUD-004 | MEDIUM | 非 blocker | League summary へ `trajectory_statistics.actual_student` を追加し `NOT_CONNECTED_TO_POPULATION_FACTORY` を明記 | `scripts/run_o6_team_league.py` 出力の `league_summary.json` |
| O6-AUD-005 | LOW | 非 blocker | 通常 worktree 環境で full pytest・docs validator を完走 | 本節末尾「検証」参照（1639 passed、docs validator PASS） |

network isolation（監査で LOW として言及）は今回も `NETWORK_ISOLATION_UNAVAILABLE` を維持し、実装したと表現しない。OS network namespace 分離は未実装のまま known limitation として残す（merge blocker ではない）。

### O6-AUD-001: Runtime Bundle 最小化

`build_runtime_closure()` は、entrypoint からの静的 Python import graph（`ast`）と、実際に 1 deck 構築 + 1 完全 game を実行した際の動的 file-open／`ctypes.dlopen` trace（`sys.addaudithook`）を組み合わせ、両者の候補を hard deny-list category（docs/tests/report/experiments/data/cache/vcs/credential/foreign-platform-binary 等）で再検査する。allow-list に紛れ込んだ deny-list 該当ファイルは `blocked` として記録し bundle へ含めない（`tests/opponents/test_runtime_closure.py::test_import_reachable_but_forbidden_file_is_blocked_not_included`）。

3 Agent 全件で以下の削減を実測した（[runtime_bundle_minimization_comparison.json](o6-opponent-intelligence-v2/runtime_bundle_minimization_comparison.json)）。

| agent (branch) | old files | new files | old bytes | new bytes | old bundle_sha256 (先頭12桁) | new bundle_sha256 (先頭12桁) |
|---|---|---|---|---|---|---|
| nihei-festival-lead | 56 | 14 | 5,880,264 | 1,489,724 | `e24664270547` | `1583e4f777de` |
| nihei-alakazam | 54 | 14 | 5,830,456 | 1,460,161 | `f5eab0965d15` | `70c2c724da7c` |
| ozawa-crustle-rule | 205 | 14 | 7,866,007 | 1,472,534 | `4a97281312c6` | `cc6011f4baa9` |
| **合計** | **315** | **42** | **19,576,727（18.67 MiB）** | **4,422,419（4.22 MiB）** | — | — |

3 Agent とも最小 closure は同一構成（`main.py`、`cg/__init__.py`、`cg/api.py`、`cg/sim.py`、`cg/utils.py`、`deck.csv`、host platform（Linux/x86_64）に一致する `cg/libcg.so`）に収束した。`cg/game.py` はどの entrypoint からも import されず（静的・動的いずれの trace でも未検出）、`excluded`（category: `not_in_runtime_closure`）に分類され bundle から除外された。`cg.dll`／`libcg.dylib`／`libcg-arm64.so` は `native_binaries.excluded_not_used_on_build_host` に分類され bundle へ含まれない。`unresolved_imports.unknown_third_party` は 3 Agent とも空であり、bundle 化された agent 自身の直接 import は標準ライブラリのみである（host が `kaggle_environments` を提供する必要は `runtime_contract.json` で別途宣言する）。

**Population identity の設計修正**: 是正前の `compute_population_identity()` は abstract な registry hash のみを入力とし、runtime bundle 自体のバイトを含んでいなかった。そのため最小化 closure へ切り替えても `population_id`／`manifest_hash` が変化せず、`LocalArtifactStore.publish()` の idempotent-republish 経路（同一 `manifest_hash` は無変更で既存 target を返す）により新 bundle が実際には publish されない設計バグが存在した。`compute_runtime_bundle_registry_hash()`（runtime bundle 全 file の SHA-256 map を hash 化）を追加し、`population_identity_hash` の意味入力へ組み込んだ（`POPULATION_IDENTITY_SCHEMA_VERSION` を v1→v2 へ bump）。`tests/opponents/test_core.py::test_population_id_is_sensitive_to_runtime_bundle_content` で回帰確認済み。

- 旧 Population ID: `team-agents-v1-a3976401cae4fd38`（`population_identity_hash=a397640…0370`）— 変更せず、旧 store snapshot はそのまま残置（immutable）。
- 新 Population ID: `team-agents-v1-f4c8f9b87ae6601a`（`population_identity_hash=f4c8f9b…19ad1`、`manifest_hash=088f81f…87700`）— [population_manifest.json](o6-opponent-intelligence-v2/population_manifest.json)。
- Fresh-client smoke 3/3: 全 Agent `exit_status: "OK"`、`bundle_hash_verified: true`、`files_extracted_and_verified: 13`、`legal_action_validation: "CABT_SMOKE_PASS"`、`no_source_branch_checkout: true`、`no_live_worktree_reference: true`。[fresh_client_smoke_results.json](o6-opponent-intelligence-v2/fresh_client_smoke_results.json)。

**Runtime Contract**（`runtime_contract.json`、3 Agent 共通構造。サンプル: [973619b52534-runtime_contract.json](o6-opponent-intelligence-v2/runtime-samples/973619b52534-runtime_contract.json)）: 分類は `SOURCE_PORTABLE_HOST_CONTRACT_REQUIRED`（bundle 化された agent 自身の code は標準ライブラリのみで完結するが、実行には host が `kaggle_environments`（実測 `kaggle-environments-1.32.0`）の `cabt` environment を提供する必要があるため `SELF_CONTAINED` ではない）。`required_host_packages: ["kaggle_environments"]`、`incompatible_host_behavior: fail_closed`（`BLOCKED_DEPENDENCY`、暗黙 pip install なし）を明記した。

未実施の検証（O6-AUD-003 の recommended_fix「clean venv or container での検証」）: `kaggle-environments==1.32.0` は PyPI 公開パッケージであり `cabt` environment を同梱していることを確認した（`pip show -f kaggle-environments` で `envs/cabt/` 配下 14 file を確認）。ただし依存が重量（jax、gymnasium、open_spiel、pokerkit 等）であり、本セッションでは fresh venv への実 install は実施していない。再現手順: `python3 -m venv /path/to/venv && /path/to/venv/bin/pip install kaggle-environments==1.32.0 && PYTHONPATH=src /path/to/venv/bin/python -m mage_ptcg.opponents smoke <opponent-id> --population team-agents-v1-f4c8f9b87ae6601a --cache-dir <fresh-cache>`。この検証は merge blocker ではない（audit_findings.json では `merge_blocker: false`）。

### O6-AUD-002: League Trajectory Evidence と統計表現

`mage_ptcg.opponents.trajectory` を新設し、`mage_ptcg.league.actual_runner`（O5 共有・protected ではないが変更していない）とは別に、pair 別の side-channel evidence file（`<pair>__trajectory.json`、match_index 単位で resumable）へ 1 game ごとの `initial_observation_digest`／`action_trace_digest`／`terminal_observation_digest`／`complete_trajectory_digest`（`remainingOverageTime` 等の timing 情報を除去した canonical JSON hash）、`engine_seed_support_status`、`game_length`、`winner_participant` を記録する。

**cabt engine seed capability の実測確認**: `kaggle_environments.make('cabt', ...).configuration.keys()` は `{decks, episodeSteps, actTimeout, runTimeout}` のみで `seed` key が存在しないことを実測確認した。`determine_engine_seed_capability()` はこれを live に再確認し `ENGINE_SEED_UNSUPPORTED` を返す（hardcode ではない）。同一 configuration で 2 回連続実行した際に trace hash が毎回異なることも確認しており（native `cg` library 内部の非公開 RNG に起因、Python 側からは制御不能）、League の `seed` field は帳簿用の pairing 識別子であり、記録された `seed` を再入力しても同一 trajectory を再現する機能はない。

**60 game 実行結果**（新 population `team-agents-v1-f4c8f9b87ae6601a`、6 pair × 10 games、side-swap 込み、`--base-seed 71000`）を再実行した。[league/league_summary.json](o6-opponent-intelligence-v2/league/league_summary.json) が正。

- raw executions: 60、unique complete trajectories: 60（重複 0 件）、effective independent sample size: 60。60 raw executions は全て相互に異なる trajectory であり、独立試行数の水増しは発生していない。
- invalid_actions／crashes／timeouts: 全 pair 0。
- 勝者方向を pair ごとに named key（`{agent_a: wins, agent_b: wins}`）で明記する（例: `973619b52534…` vs `rule-agent-v0` は `{"973619b525…": 10, "rule-agent-v0": 0}`、`973619b525…` の 10 勝）。曖昧な "worst matchup" 表現は使用していない。
- Wilson CI は raw-execution basis（常に計算、`raw_execution_wilson_ci_is_descriptive_only: true`）と unique-trajectory basis（`effective_independent_sample_size >= 5` の場合のみ計算、未満は `unique_trajectory_wilson_ci_status: "INSUFFICIENT_INDEPENDENT_SAMPLES"` で `null`）を分離した。今回は全 pair で `effective_independent_sample_size = 10` のため unique-trajectory Wilson CI が全 pair で計算された。
- Bradley-Terry は raw-execution 版と deduplicated-trajectory 版を分離して算出（今回は重複 0 件のため両者は同値）。いずれも `descriptive_only: true`、`statistically_supported_ranking: false` を明記し、league 規模のサンプルでは「rating の参考値」であって「統計的に支持されたランキング」ではないことを区別する。graph は 4 participant で connected（`graph_connected: true`）。
- `actual_student`: `NOT_CONNECTED_TO_POPULATION_FACTORY`（`scripts/run_o6_team_league.py` の `_load_participants()` は rule-agent-v0 と VALIDATED native spec のみを構成し、Student adapter は接続されていない）。merge blocker ではない known limitation。

### 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q tests/opponents
# 47 passed
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q \
  tests/competition_intelligence tests/test_run_o5_benchmark_cli.py \
  tests/test_actual_league_runner.py tests/test_actual_league_cli.py
# 464 passed
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q
# 1639 passed in 273.88s
python3 scripts/docs/validate_docs.py
# Validated 12 canonical documents.
```

`git diff --stat -- agents/rule_agent.py agents/rule_agent_v1.py deck.csv main.py src/mage_ptcg/evaluation/promotion.py` は空（protected files 不変）。`tests/test_check_o1_protected_files.py` 8 passed。

### 既知の制限（追加分）

- fresh venv/container での host dependency 実地検証は本セッションで未実施（上記「未実施の検証」参照）。
- Runtime Closure Builder の動的 trace は「1 deck 構築 + 1 完全 game」の実行経路のみをカバーする。静的 import graph で捕捉されない、文字列組み立て `importlib.import_module` 経由の稀な分岐は理論上見逃し得る（今回の 3 Agent では該当なし、`unresolved_imports.unknown_third_party` は全件空で確認済み）。
- League の `seed` は pairing 識別子のままであり、cabt 自体への機能的な再現性付与は行っていない（is not implementable without upstream cabt changes）。

## Phase B Hardened Public Corpus 統合（2026-07-21、同 feature branch）

Phase A 完了ゲート（HIGH Finding 2件是正、runtime bundle 最小化、trajectory evidence、focused/O5/full/docs 検証、protected files 不変、worktree clean commit）を満たした後に着手した。

### スコープ

- 統合元: Public Corpus（`/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-public-source-corpus-v1`、`corpus_semantic_hash=19e57ef7…09dd8cb85409f5`、`checksums.sha256` 12/12 一致確認）、Collector Prototype（`/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-public-collector-prototype-v1`、設計参照のみでコードは丸ごとコピーしていない）。
- 新モジュール `mage_ptcg.opponents.public_source` が、各 Source の分類 JSON（`source_manifest`／`code`／`deck`／`behavior`／`provenance`／`permissions`／`technical_validation`、任意で `classification`／`deck_validation`／`hashes`）のみを import する。**`sources/<id>/raw/`・`extracted/`（Public Agent の実コード）は一切読み込まない**（`_FORBIDDEN_IMPORT_DIRS` で構造的に隔離、`test_unknown_code_never_imported_or_executed` で検証）。Full Corpus はリポジトリ外（`/home/bfe-lab-ono/kaggle/handoff-artifacts/`）に留め置いた。
- Public Agent を実行・import・CABT smoke するコマンドはこのモジュールに一切存在しない（`test_activation_fail_closed_no_execute_function_exists` で `execute`/`activate`/`build`/`run`/`invoke`/`smoke` という名の関数が存在しないことを検証）。

### 実装した要件（是正原文との対応）

| 要件 | 実装 |
|---|---|
| Source schema | `CORPUS_SCHEMA_VERSION` 完全一致検証。unknown/future schema は import 拒否 |
| deterministic hashing | `compute_source_metadata_hash()`（O6 側の広域 semantic hash、7ファイル全体をカバー。corpus 側 `source_package_hashes`（`source_manifest.json` のみ）とは別に、両方を検証） |
| archive safety | `core.safe_extract_tar_gz()` を拡張（backslash/Windowsドライブパス拒否、per-file/total/file-count/圧縮比 limit）。実 Corpus は plain directory のため未使用だが、将来のアーカイブ経路に備え `extract_corpus_archive()` として実装・テスト済み |
| Deck validation, card conflict reporting | 外部 card DB を再フェッチせず、Corpus 提供の `deck_validation.json`（`is_legal`/`issues`/`mismatches`/`total_count`）を構造的に再検証（`card_ids` 長との整合性等）。card DB 自体は O6 worktree に存在しないため独自の再検証は行わない（未確認事項として明記） |
| classification metrics | Corpus 提供の `classification.json`（archetype/Jaccard 等）をそのまま import・提供。O6 側での再計算・検証はしていない（Corpus 側の一次分類として扱う） |
| provenance | `source_url`/`retrieved_at`/`explicit_license` を record へ保持 |
| permission enums | Team Source と同一の `USAGE_SCOPES` 語彙を再利用。`explicit_license == "UNKNOWN"` は固定表（本節末尾参照）と一致することを import 時に検証、不一致は拒否 |
| check-permissions | `PermissionReviewRequiredError`（exit code 6）。CLI `main()` で `OpponentError` より先に catch |
| Candidate state | `derive_candidate_state()`。`source_id` を一切参照しない rule-based 判定（プロトタイプの `if source_id in [...]` 式ハードコードを排除）。`CANDIDATE_STATES` の5値のみへキャップし、`NATIVE_OPPONENT`/`VALIDATED`/`APPROVED`/`PUBLISHED` を要求する override は拒否 |
| technical validation NOT_RUN | import 時に全 5 項目が `NOT_RUN` であることを検証し、それ以外の値を持つ Source は import 自体を拒否 |
| metadata import CLI | `mage_ptcg.opponents public-source {import,list,inspect,verify-metadata,check-permissions}` |
| tests | `tests/opponents/test_public_source.py`（37 tests）、`core.py` 側の archive hardening を含む |
| docs | 本節、runbooks/o6-public-source-intake.md |

UNKNOWN license の固定表:

| scope | 値 |
|---|---|
| evaluation | REVIEW_REQUIRED |
| training_data_generation | REVIEW_REQUIRED |
| strategy_analysis | ALLOWED_METADATA_ONLY |
| team_redistribution | REVIEW_REQUIRED |
| public_redistribution | DENIED |
| submission_bundle | DENIED |

### 7 Source 実 Import 結果

`python -m mage_ptcg.opponents public-source import --corpus <corpus-root> --output-dir /home/bfe-lab-ono/kaggle/opponent-artifacts/public-sources` を実行し、7 Source 全件を import した（`docs/evidence/o6-public-sources-v1/` に記録を複製）。

| source_id | code | deck_fidelity | candidate_state | review_override_applied | corpus 側 usability |
|---|---|---|---|---|---|
| harukiharada_crustle | EXACT | EXACT | REVIEW_REQUIRED | false | REVIEW_REQUIRED |
| itsuki9180_lucario_jp | EXACT | RECONSTRUCTED | DECK_STANDARD_PILOT_CANDIDATE | true | DECK_STANDARD_PILOT_CANDIDATE |
| naoto714_kangaskhan | EXACT | EXACT | REVIEW_REQUIRED | false | REVIEW_REQUIRED |
| romanrozen_strongstart | EXACT | EXACT | REVIEW_REQUIRED | false | REVIEW_REQUIRED |
| sue124_alakazam | EXACT | EXACT | REVIEW_REQUIRED | false | REVIEW_REQUIRED |
| tientrum_alakazam_search | EXACT | EXACT | REVIEW_REQUIRED | false | REVIEW_REQUIRED |
| tomatomato_archaludon | EXACT | RECONSTRUCTED | DECK_STANDARD_PILOT_CANDIDATE | false | DECK_STANDARD_PILOT_CANDIDATE |

O6 側の独立 rule-derivation は Corpus 側の判定と全件一致した（`itsuki9180_lucario_jp` のみ override 適用、`review_override.json` の内容どおり監査記録した）。全 7 件が `explicit_license: UNKNOWN` のため、`permission_scopes.evaluation` は全件 `REVIEW_REQUIRED`。`verify-metadata` は 7/7 一致（tamper なし）。`check-permissions` は exit code 6（意図された fail-closed、`review_required`: 7件全て）。NATIVE_OPPONENT_CANDIDATE／VALIDATED／APPROVED／PUBLISHED へ進んだ Source はゼロ件。

### Team Population 非破壊の確認

Public Corpus 統合は `mage_ptcg.opponents.core`（Team pipeline）へ一切変更を加えていない。import 前後で以下を実測確認した。

- `store_index.json`／`list-populations` の `team-agents-v1-f4c8f9b87ae6601a` の `manifest_hash`（`088f81f…87700`）は import 前後で同一。
- 同 Population の fresh-client fetch → smoke（`973619b52534…` agent）を import 後に再実行し、`bundle_hash_verified: true`、`files_extracted_and_verified: 13`、`exit_status: "OK"` を再確認した。
- Rule Agent v0／Champion／`main.py`／`deck.csv`／`promotion.py` は変更していない。

### 既知の制限

- Live Acquisition（自動発見・ダウンロード・増分同期）は未実装。現在動作する経路は Repository Snapshot ベースのオフライン import のみ。
- Card ID の正当性再検証は Corpus 提供の `deck_validation.json` の構造的整合性チェックに留まる。O6 worktree には `data/raw/EN_Card_Data.csv`／`JP_Card_Data.csv`（canonical worktree にのみ存在、`.gitignore` 対象）が無く、独自の card-DB 突合は実施していない。
- `classification.json`（archetype／Jaccard 類似度）は Corpus 側の一次分類をそのまま import しており、O6 側での再検証・再計算は行っていない。
- Archive（`.tar.gz`）経路の安全策（traversal/symlink/hardlink/device/FIFO/Windows path/backslash/nested archive/size/file count/compression ratio）は実装・テスト済みだが、現行の実 Corpus は plain directory であるため実運用では未使用。

## Phase C: O6-AUD-002 最終是正（raw public trajectory evidence）（2026-07-21、Codex 最終監査 → Claude 是正、同 feature branch）

Codex による post-integration 最終独立監査（`/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-post-integration-final-audit`、review range `51378c3..6d0481f`）は **FAIL** 判定を出し、Phase A で `是正済み` と記録した `O6-AUD-002` を HIGH merge blocker として `PARTIALLY_FIXED` へ差し戻した。理由: 保存されていたのは trajectory digest（SHA-256 hex）のみで、その入力となる raw canonical public observation/action/status（`env.steps`）が一切保存されておらず、「60 raw executions・60 unique complete trajectories・重複0件」という report を第三者が独立再計算で確認できなかった。本節はこの是正結果を記録する。全 machine-readable evidence は [o6-opponent-intelligence-v3/](o6-opponent-intelligence-v3/) を正とする。

### 実装

- `mage_ptcg.opponents.privacy_gate`: fail-closed の public-only スキャナ。構造的チェック（実測した cabt 観測構造 `observation.current.players[i].hand` が非acting seatでは`null`に redact されていることを前提に、そうでなければ opponent hand 漏洩として拒否）と、key/value denylist（`hidden`/`secret`/`credential`/`rng_state`/`environ`等の field 名パターン、Python object repr・絶対パス・AWS風credential等の value パターン）の二段構成。
- `mage_ptcg.opponents.raw_trajectory_evidence`: 1 game の canonical per-step per-seat record を `INITIAL_PUBLIC_OBSERVATION`／`PUBLIC_ACTION`／`TERMINAL_PUBLIC_OBSERVATION` の順序付き event へ分解し、全 event を `assert_public_only()` で検査してから（1件でも違反があれば当該 game の evidence を一切書き込まない）、gzip 圧縮 JSONL（`public_trajectory.jsonl.gz`）・`trajectory_manifest.json`・`game_metadata.json`・`trajectory_digest.txt`・`hashes.json` を書き出す。`game_id`／`pair_id`／`execution_index`／`requested_seed`／timestamp／filesystem path は `game_metadata.json` にのみ存在し、digest 対象の event には含まれない。`write_immutable_json()`／gzip 内容比較により、同一 game への内容不一致な再書き込みは `ImmutableEvidenceConflict` で拒否する（tamper／same-run-id-different-content 拒否）。
- `mage_ptcg.opponents.independent_trajectory_verifier`: raw JSONL を独立 parse し、canonical JSON 直列化と domain-prefixed SHA-256 を **独自に再実装**（`mage_ptcg.opponents.trajectory` の digest 関数・`mage_ptcg.competition_intelligence.canonical`・League runner を import しないことを AST 解析でテスト強制、`tests/opponents/test_independent_trajectory_verifier.py::test_verifier_source_does_not_import_runtime_digest_or_league_runner`）。runtime 側が記録した digest と独立再計算した digest を比較し、`match`／`malformed`／`privacy_valid` を per-game に返す。`python -m mage_ptcg.opponents verify-league-trajectories --evidence <path>` および `scripts/run_o6_team_league.py` の finalize step から、いずれも別 subprocess（`subprocess.run([sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", ...])`）として呼び出す。
- `scripts/run_o6_team_league.py`: 各 game の raw evidence を persist した後、全 pair 完了後に独立 verifier を実行し、`digest_mismatches`／`malformed_trajectories`／`privacy_violations` が1件でもあれば `SystemExit` で中断し `league_summary.json` を書かない（`digest_basis: "independently_verified"` の final summary は、独立検証を通過した場合にのみ生成される）。unique trajectory 集計・Wilson CI・Bradley-Terry は独立再計算した digest のみを入力に再構築する。`--evidence-root` 配下の全ファイルを最後に `checksums.sha256`（`sha256sum -c` 互換、自己非参照）でカバーする。

### League runtime のバグ発見と是正（本タスクのスコープ外だが Task 5 実行をブロックしたため修正）

実 League を `docs/evidence/o6-opponent-intelligence-v3` という in-repo 相対パスへ向けて実行したところ、Native Team Agent が絡む全 game が `AGENT_ERROR`（elapsed ~0.02s）で即座に失敗した。`NativeAgentWorker` の worker subprocess の stderr を直接捕捉して再現・特定した結果、`subprocess.Popen(cwd=source_root)` は相対 `source_root` を親プロセスの cwd に対して解決するが、`_WORKER_HARNESS` はその同じ相対文字列を子プロセス自身の（既に移動済みの）cwd に対して再度 `os.path.join(root, rel)` しており、相対パスを渡すと二重連結された存在しないパスで `FileNotFoundError` になっていた。これまでの全 League 実行が `--cache-dir`／`--output-dir` に絶対パスを渡していたため顕在化していなかった潜在バグである。`Path(source_root).resolve()` を `NativeAgentWorker.__init__` の先頭で行うよう修正し、回帰テスト（`tests/opponents/test_league_runtime.py::test_native_agent_worker_accepts_relative_source_root`）を追加した。Team Agent 自体のロジックには変更していない。

### 60 game 実行結果（新 evidence root、独立検証済み）

同一 Population `team-agents-v1-f4c8f9b87ae6601a`（`population_identity_hash: f4c8f9b87ae6601ab86805203a51df7b10e5c505a4d30558b26be2c238419ad1`、Phase A から不変）に対し、`--games-per-pair 10 --base-seed 83000` で再実行した（[league_run_manifest.json](o6-opponent-intelligence-v3/league_run_manifest.json)、`league_run_id: o6-team-league-f4c8f9b87ae6-raw-v1`）。上記バグ修正前の同一 evidence root への実行試行（base_seed 81000/82000、native agent 側 100% crash）は評価に値しない不正な実行として破棄し、リポジトリへコミットしていない。

- raw executions: 60、raw trajectory files（`games/<id>/public_trajectory.jsonl.gz` 一式）: 60、独立検証成功: 60/60。
- `digest_mismatches: 0`、`malformed_trajectories: 0`、`privacy_violations: 0`（[trajectory_summary.json](o6-opponent-intelligence-v3/trajectory_summary.json)）。
- unique initial observations: 1（coin-flip 前の初期状態は共通）、unique action traces: 60、unique terminal observations: 60、unique complete trajectories: 60（重複0件、`effective_independent_sample_size_total: 60`）。
- invalid_actions／crashes／timeouts: 全 pair 0。
- Wilson CI は全 6 pair で `effective_independent_sample_size = 10 >= 5` のため unique-trajectory basis も `COMPUTED`。raw-execution basis は今回重複が無いため unique-trajectory basis と同値。
- Bradley-Terry（raw-execution 版／deduplicated-trajectory 版、今回重複0件のため同値）はいずれも `descriptive_only: true`、`statistically_supported_ranking: false`、4 participant で `graph_connected: true`。
- `checksums.sha256`: evidence root 配下 315 ファイル全件を `sha256sum -c` で検証済み（自己非参照）。

### 旧 60 games（`o6-opponent-intelligence-v2/league/`）の扱い

既存ファイルは無改変で残置し、[LEGACY_NOTE.md](o6-opponent-intelligence-v2/LEGACY_NOTE.md) に `legacy_digest_only`／`independently_unverifiable` である旨を明記した。旧 report の「60 unique」を維持するために新 evidence の digest 入力を変更する、といった操作は行っていない。

### 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q tests/opponents
# 115 passed
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q \
  tests/competition_intelligence tests/test_run_o5_benchmark_cli.py \
  tests/test_actual_league_runner.py tests/test_actual_league_cli.py tests/test_run_o6_team_league.py
# 466 passed
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q
# 1709 passed in 256.08s
python3 scripts/docs/validate_docs.py
```

### 既知の制限（追加分）

- Public-only gate の denylist は既知カテゴリ（engine internal state、Python repr、絶対パス、credential 風文字列）を対象とした構造的+パターンベースの検査であり、cabt の全フィールドを網羅した allowlist ではない（判断不能な field 名パターンは拒否するが、パターンに一致しない未知の漏洩形態を理論上見逃し得る）。
- `engine_seed_support_status` は本 run でも `ENGINE_SEED_UNSUPPORTED` のままであり、`seed` は pairing 識別子のままである（Phase A から変更なし）。
- raw trajectory evidence は gzip 圧縮 JSONL として保存する（生 JSON をそのままコミットすると 60 game で数百 MB 規模になり得るため）。独立検証はまず gzip を展開してから raw JSONL を再構成する。

## Phase D: O6-AUD-002 限定再監査 FAIL 是正（public trajectory projection + integrity chain）（2026-07-22、Codex 限定再監査 → Claude 是正、同 feature branch）

Codex による O6-AUD-002 限定再監査（`/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-raw-trajectory-targeted-reaudit`）は Phase C の実装を **FAIL** 判定とし、3件の HIGH blocker を報告した。

- **PRIVACY-001**: raw trajectory ファイルに `logs`（12,054箇所）、`search_begin_input`（12,054箇所）、手札・プライズの全要素内容（23,686箇所、うち行動主体自身の手札を含む）が生のまま残存していた。
- **PRIVACY-002**: `privacy_gate` は denylist スキャナであり、未知・改名されたフィールドを fail-open で通過させていた（`unknown_field`、`observation.unrecognized.card_identity` のいずれも PASS していた）。
- **INTEGRITY-001**: `trajectory_manifest.json`／`hashes.json`／`game_metadata.json` を改ざんしても独立検証は検出しなかった（`checksums.sha256` 自体は検出するが、self-referential で外部 anchor が無いため checksums ファイルごと差し替えられると無力）。

本節はこの3件の是正結果を記録する。全 machine-readable evidence は [o6-opponent-intelligence-v4/](o6-opponent-intelligence-v4/) を正とする。

### 旧 evidence（`o6-opponent-intelligence-v3/`）の無効化

`o6-team-league-f4c8f9b87ae6-raw-v1`（Phase C で生成）は上記3件の finding により無効と判定し、次の状態へ変更した: `INVALID_PRIVATE_EVIDENCE` / `PRIVACY_VALIDATION_FAILED` / `NOT_EXPORTABLE` / `NOT_MERGE_EVIDENCE`。`docs/evidence/o6-opponent-intelligence-v3/` を `git rm --cached`（`-r` 単体では destructive-command guard に阻まれたため `git ls-files -z | xargs -0 git rm --cached` で個別ファイル指定、ユーザー承認済み）で git 追跡から除外し、`.gitignore` へ追加した。ファイル自体はローカルディスク上の restricted quarantine として残置（自動削除はしていない、316 files）。git 上に残るのは機微情報を含まない tombstone のみ（[o6-opponent-intelligence-v3-TOMBSTONE.json](o6-opponent-intelligence-v3-TOMBSTONE.json)）。

### アーキテクチャ全面置換

- `mage_ptcg.opponents.public_trajectory_projection`（新設）: raw canonical step から `PUBLIC_TRAJECTORY_PROJECTION_V1` event を**再帰的 allow-list**で構築する。元 observation を copy してから deny-list で削る方式は廃止し、認識できないキーが深さを問わず1つでもあれば `PublicSchemaUnknownFieldError` で当該 game の evidence 生成全体を fail-closed にする。許可リストは `docs/evidence/cabt-observation-schema.md` の監査済み構造を土台とし、本 Phase での実機観測で複数箇所を訂正した（次節）。
- `mage_ptcg.opponents.public_trajectory_evidence`（`raw_trajectory_evidence` を置換）: raw observation を一切ディスクへ書かない。projection → privacy_gate（defense-in-depth）→ canonical JSON → `public_projection_trajectory.jsonl.gz` の順で永続化する。
- `mage_ptcg.opponents.independent_trajectory_verifier`（全面改修）: 独自の recursive privacy scan（`independent_privacy_scan`）と JSON Schema 適合検査（`validate_event_schema`、`public_trajectory_schema_v1.json` を参照するが projection builder は import しない）を追加。`verify_run_chain`（`--mode full`）は `run_manifest.json`／`run_summary.json`／`run_root.sha256`／per-game `hashes.json` を全て独立検証し、`--trusted-root-registry` または `--expected-root-sha256` のいずれも無ければ `UNANCHORED_EVIDENCE` で fail する。
- `mage_ptcg.opponents.league_integrity_chain`（新設、runtime/orchestrator 専用）: `run_manifest.json` 構築と `run_root.sha256`（run directory 内の全ファイルの sorted `{path: sha256}` マッピングの SHA-256）を計算する。
- `docs/evidence/o6-trusted-league-roots.json`（新設）: run directory 外部の trusted anchor registry。git 追跡下にあるため、この registry 自体の改ざんは git 履歴で検出できる。
- `src/mage_ptcg/opponents/public_trajectory_schema_v1.json`（新設）: `additionalProperties: false` を全階層に適用した JSON Schema（draft 2020-12）。writer と verifier の両方が参照するが、payload 構築ロジックは共有していない。

### 実機観測で判明した cabt 生データの実際の構造（`cabt-observation-schema.md` の記述と相違）

新 allow-list 実装を実 League（Rule Agent v0 同士の自己対戦、20 game 以上）で検証する過程で、次の4点が既存文書の記述と異なることを直接観測で確認した（推測で合わせず、実機の値をそのまま採用した）。

1. **`current.stadium`** はドキュメントが「非null時の形状は一度も観測されておらず要検証」としていた `{"id": int}` ではなく、`active`/`bench`/`discard` と同じ **カードスロットのリスト**（0または1要素）だった。同じ card projection allow-list で処理するよう変更した。
2. **`current.looking`**（デッキ検索等の結果）はドキュメントが「一度も非null を観測していない」としていたが、実際には多くの試合で非null になり、手札・プライズと同様に私的ゾーンから実カード ID を露出する。出現頻度が高いため、他の未知フィールドと違い**ゲーム全体を fail-closed にはせず**、内容を常に破棄する（`logs`/`search_begin_input` と同じ扱い）。
3. **`select` の候補配列キーは `option`（単数形）であり、既にリストそのもの**だった。旧実装は存在しない `options`（複数形）キーを仮定し、単一要素として wrap する誤った変換をしていた。
4. **各 option 要素はフラットな dict**（`{"type": int, "area": ..., "index": ..., ...}`）であり、`fields` サブキーへのネストは存在しない。そのネスト構造は `cabt-observation-schema.md` 自身が設計した別トレース形式（`cabt_trace.py`）の**出力**スキーマであり、raw engine の入力形状ではなかった。
5. **select と応答 action の対応関係**: raw step の `observation.select` は decision prompt であり、そのseat の応答は同一 step index の `action` ではなく、**1つ後の raw step index の `action`** に記録される（実 agent callable をラップして呼び出し時の (observation, action) 実測値と `environment.steps` を突き合わせて確認）。decision は応答側の index に紐付け、prompt 側（=INITIAL イベント）には決して紐付けないよう実装した。これにより `initial_observation_digest` は後続 action の内容に依存せず独立性を保つ。

### 60 game 実行結果（新 evidence root、独立検証済み）

同一 Population `team-agents-v1-f4c8f9b87ae6601a`（Phase A から不変）に対し、`--games-per-pair 10 --base-seed 92000` で実行した（[run_manifest.json](o6-opponent-intelligence-v4/run_manifest.json)、`league_run_id: o6-team-league-f4c8f9b87ae6-public-v2`）。旧 v3 の run とは異なる base_seed・異なる evidence root であり、旧 raw evidence を変換・再利用していない。

- raw executions: 60、public projection trajectory files: 60、独立検証成功: 60/60。
- `digest_mismatches: 0`、`malformed_trajectories: 0`、`privacy_violations: 0`、`schema_violations: 0`（[run_summary.json](o6-opponent-intelligence-v4/run_summary.json) `independent_verification` 節）。
- unique initial observations: 1（対局開始盤面は共通）、unique action traces: 60、unique terminal observations: 60、unique complete trajectories: 60（重複0件、`effective_independent_sample_size_total: 60`）。
- invalid_actions／crashes／timeouts: 全 6 pair 0。
- 生成物への直接スキャン（`zcat games/*/public_projection_trajectory.jsonl.gz` を全60ゲーム分連結、6,331 event）で `logs`／`search_begin_input`／`deck`／`hand`／`remainingOverageTime` の文字列出現数を確認し、0件だった。event の top-level key は `schema_version`/`event_type`/`step_index`/`seat_direction`/`public_payload` のみ、`event_type` は `INITIAL_PUBLIC_STATE`/`PUBLIC_ACTION`/`TERMINAL_PUBLIC_STATE` の3値のみだった。
- 各 pair の unique-trajectory win rate（Wilson 95% CI、`effective_n=10` で全 pair `COMPUTED`）:
  - `03d38399…992bc4f` vs `9144af0d…8c5812`: 6–4（`[0.313, 0.832]`）
  - `03d38399…992bc4f` vs `973619b5…9d899`: 7–3（`[0.397, 0.892]`）
  - `03d38399…992bc4f` vs `rule-agent-v0`: 6–4（`[0.313, 0.832]`）
  - `9144af0d…8c5812` vs `973619b5…9d899`: 8–2（`[0.490, 0.943]`）
  - `9144af0d…8c5812` vs `rule-agent-v0`: 10–0（`[0.722, 1.0]`）
  - `973619b5…9d899` vs `rule-agent-v0`: 9–1（`[0.596, 0.982]`）
- Bradley-Terry（raw-execution／deduplicated-trajectory、今回重複0件のため同値）は `descriptive_only: true`、`statistically_supported_ranking: false`、4 participant で `graph_connected: true`。旧 v3 の勝敗数はこの新 run の統計に一切流用していない。
- `checksums.sha256` は evidence root 配下の全ファイルを `sha256sum -c` 互換でカバー。`run_root.sha256` は `docs/evidence/o6-trusted-league-roots.json` に登録した外部 anchor（`run_root_sha256: 935c577f…bd96a28`、`source_commit`）と一致することを `--mode full` で確認した。

### Integrity chain tamper 検出

新設した `tests/test_o6_integrity_tamper.py`（15 tests）で、trajectory byte／runtime digest／independent digest／`hashes.json`／`trajectory_manifest.json`／`run_summary.json`／`run_manifest.json`／`run_root.sha256`／trusted registry／game 挿入／game 削除／同一 run_id 別内容差し替え、のいずれの単独改ざんも `--mode full` の独立検証で検出されることを固定した。実装過程で2件の実際の検出漏れを発見・修正した: (1) `run_root.sha256` はそれ自身の hash 計算から自己除外されるため、ファイル内容だけを改ざんしても anchor との比較に現れず無検出だった → 独立検証側で on-disk `run_root.sha256` の内容を再計算値と直接照合する追加チェックを実装した。(2) `checksums.sha256` を run_root hash の計算対象から除外していた箇所と除外していなかった箇所が writer/verifier 間で不一致になり誤検出（false failure）を起こしていた → 除外対象を `run_root.sha256` のみに統一した。

### 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q tests/opponents
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q \
  tests/competition_intelligence tests/test_run_o5_benchmark_cli.py \
  tests/test_actual_league_runner.py tests/test_actual_league_cli.py \
  tests/test_run_o6_team_league.py tests/test_o6_integrity_tamper.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q
python3 scripts/docs/validate_docs.py
python3 -m mage_ptcg.opponents.independent_trajectory_verifier \
  --evidence docs/evidence/o6-opponent-intelligence-v4 --json --mode full \
  --trusted-root-registry docs/evidence/o6-trusted-league-roots.json
```

### 既知の制限（Phase D 追加分）

- Public Action Projection は `option` 要素のうち `option_type`／`option_type_name`／`player_index`／`attack_id`／`count`／`number` のみを転送し、`area`／`index`／`inPlayArea`／`inPlayIndex`／`energyIndex` は意図的に除外している。これらのゾーン位置としての意味（手札／盤面／山札のどれを指すか）は `cabt-observation-schema.md` 自身が「未検証」としており、本 Phase でも解決していない。安全側に倒して除外しているため、action の完全な positional 情報は本 evidence には含まれない。
- `current.looking` は内容を一切保存しない（件数すら保存しない）。デッキ検索アクションの発生自体は action projection の `option_type_name` 等から間接的に推測できる場合があるが、検索対象・結果は一切含まれない。
- Public-only gate（`privacy_gate`）は writer 側の defense-in-depth 層として維持しているが、実際の unknown-field fail-closed 保証は `public_trajectory_projection` の allow-list が担う。denylist 単体では新規・改名された未知フィールドを検知できない既知の限界がある（`tests/opponents/test_privacy_gate.py::test_genuinely_novel_unrecognized_key_name_is_not_caught_by_denylist_alone` で明示的に固定）。
