---
title: O6 Team Opponent Population — Quick Start
date: 2026-07-21
base_commit: 5be120ceb0eb31aa6161dc8eab1cdf88180421cb
---

# O6 Team Opponent Population — Quick Start

本書はチーム内で `mage_ptcg.opponents` の Population を fresh client として使う最短手順を示す。設計・実測 Evidence は [../evidence/o6-opponent-intelligence-v1.md](../evidence/o6-opponent-intelligence-v1.md) を正とする。

## 前提

- 使ってよいのは、`configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml` の namespace policy が承認した `agents/*` source から生成された、VALIDATED 状態の Population のみ。
- Population は team-internal 利用限定（`team_redistribution` は許可、`public_redistribution`／`submission_bundle` は禁止）。Kaggle 提出物へ Team Agent の実装を混入させないこと。
- 永続 artifact store の絶対 path は環境固有であり、都度 `--artifact-store` で指定する。

## 手順

```bash
# 1. 利用可能な Population を一覧
python -m mage_ptcg.opponents list-populations --artifact-store <durable-store>

# 2. 自分の作業用 cache dir へ fetch し、hash 検証を通す（offline）
python -m mage_ptcg.opponents fetch <population-id> \
  --artifact-store <durable-store> --cache-dir <your-cache-dir> --offline

# 3. cache だけを見て opponent 一覧・詳細を確認
python -m mage_ptcg.opponents list --population <population-id> --cache-dir <your-cache-dir>
python -m mage_ptcg.opponents inspect <opponent-id> --population <population-id> --cache-dir <your-cache-dir>

# 4. runtime bundle を hash 検証しながら実際に isolated cabt smoke を実行
python -m mage_ptcg.opponents smoke <opponent-id> --population <population-id> \
  --cache-dir <your-cache-dir> --timeout-seconds 30
```

`smoke` は `runtime/<agent_id>/hashes.json` に記録された全 file の SHA-256 を再検証してから、独立 HOME・独立 subprocess で isolated cabt smoke を実行する。O6 worktree の git 履歴や `origin/agents/*` の checkout は一切参照しない。

## Team League を再実行する

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 scripts/run_o6_team_league.py \
  --artifact-store <durable-store> \
  --population <population-id> \
  --cache-dir <league-cache-dir> \
  --output-dir <evidence-root>/league \
  --evidence-root <evidence-root> \
  --games-per-pair 10
```

3 Native Team Agent + Rule Agent v0 の全組合せ round-robin を実行する。各 Native Team Agent は game ごとに新規 subprocess を起動し（`NativeAgentWorker`）、game 間で state を共有しない。結果は `--output-dir` 配下の pair 別 JSON、pair 別 `*__trajectory.json`（game ごとの initial/action/terminal/complete digest、`cabt` の実測 seed capability）、`league_summary.json` に保存される。

`league_summary.json.trajectory_statistics` は raw execution 数と unique trajectory 数を明確に分離する。`per_pair.<pair>.unique_trajectory_wilson_ci_a` は `effective_independent_sample_size`（unique trajectory 数）が `--min-effective-independent-sample-size`（既定 5）未満だと `null` になり、`unique_trajectory_wilson_ci_status` が `INSUFFICIENT_INDEPENDENT_SAMPLES` になる。`raw_execution_wilson_ci_a` は常に計算されるが `raw_execution_wilson_ci_is_descriptive_only: true` を伴う参考値であり、独立試行数の根拠にしない。`bradley_terry_raw_execution`／`bradley_terry_deduplicated_trajectory` はいずれも `descriptive_only: true`、`statistically_supported_ranking: false` であり、rating の参考値であって統計的に支持されたランキングではない。cabt は `configuration` に `seed` キーを持たないため（`engine_seed_support_status: "ENGINE_SEED_UNSUPPORTED"`）、記録される `seed` は帳簿用の pairing 識別子であり、実行結果を再現させる機能は持たない。

### 独立検証済み Raw Trajectory Evidence（O6-AUD-002 最終是正）

`--evidence-root` 配下（`games/<game-dir-id>/{public_trajectory.jsonl.gz,trajectory_manifest.json,game_metadata.json,trajectory_digest.txt,hashes.json}`、`league_run_manifest.json`、`trajectory_summary.json`、`checksums.sha256`）に、fail-closed public-only gate を通過した raw canonical public trajectory（`INITIAL_PUBLIC_OBSERVATION`／`PUBLIC_ACTION`／`TERMINAL_PUBLIC_OBSERVATION` の順序付き event）を保存する。`scripts/run_o6_team_league.py` は全 pair 完了後、独立実装（`mage_ptcg.opponents.trajectory` の digest 関数を import しない）の verifier を **別 subprocess** で実行し、1件でも `digest_mismatches`／`malformed_trajectories`／`privacy_violations` があれば `league_summary.json` を書かずに中断する。既存の evidence root へ再実行しても、内容が同一なら idempotent（no-op）、内容が異なれば `ImmutableEvidenceConflict` で拒否される（tamper／使い回し検出）。

evidence root だけを対象に独立検証を単独で再実行する場合:

```bash
python -m mage_ptcg.opponents verify-league-trajectories --evidence <evidence-root> --json
```

これは `python -m mage_ptcg.opponents.independent_trajectory_verifier --evidence <evidence-root> --json` を別 subprocess として呼ぶだけであり、League runner や runtime digest コードを import しない。

## やってはいけないこと

- Population identity（`population_id`）を手で指定・上書きしない。`build_population()` は常に content から導出する。
- `runtime/` bundle のハッシュ検証をスキップして subprocess を起動しない。
- Native Agent の実装コードを `main.py`／`deck.csv`／submission bundle へコピーしない（Team Permission は redistribution を許可するが submission bundle は明示的に禁止している）。
- 探索系2 source（`ozawa-metal-psychic-search`、`water-box-search`）を VALIDATED 扱いにしない。既知の native library crash リスクがある（[../evidence/o6-search-agent-runtime-diagnosis.md](../evidence/o6-search-agent-runtime-diagnosis.md)）。
- 独立検証で `digest_mismatches`／`malformed_trajectories`／`privacy_violations` が1件でも出た evidence root を「League 完了」として扱わない。`league_summary.json` が存在しないこと自体がその signal になる（script が意図的に書かない）。
- 既存の `--evidence-root` 配下のファイルを、内容を変えて上書きしない。別 run として比較したい場合は新しい evidence root（新しい `league_run_id`）を使う。
