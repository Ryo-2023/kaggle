---
title: o6-opponent-intelligence-v2 League Evidence — Legacy Status
date: 2026-07-21
---

# Legacy Status: `o6-opponent-intelligence-v2/league/`

このディレクトリの League evidence（`league_summary.json`、pair 別 `*.json`／`*__trajectory.json`）は **`legacy_digest_only`** かつ **`independently_unverifiable`** として扱う。

## 理由

`league/*__trajectory.json` は各 game の `initial_observation_digest`／`action_trace_digest`／`terminal_observation_digest`／`complete_trajectory_digest`（SHA-256 hex）のみを保存しており、その digest を再計算する元になる raw `kaggle_environments` `env.steps`（canonical public observation/action/status）は一切保存されていない。そのため、記録された「60 raw executions、60 unique complete trajectories、重複0件」という主張は、**保存済み digest 値の集計としては正しい**が、**第三者が独立に再計算して確認することはできない**（Codex 最終監査 `O6-AUD-002`、HIGH、`PARTIALLY_FIXED`→本 evidence 追加により是正）。

## 扱い

- 本ディレクトリの既存ファイルは一切変更・削除しない（immutable）。
- 独立再計算可能な raw trajectory evidence は新しい versioned evidence root [`../o6-opponent-intelligence-v3/`](../o6-opponent-intelligence-v3/) に、新規 League run（`league_run_id: o6-team-league-f4c8f9b87ae6-raw-v1`、base_seed 83000、同一 Population `team-agents-v1-f4c8f9b87ae6601a`）として保存した。旧 60 games の digest を後付けで raw 化することはしていない（既存 digest 入力を変えて旧報告の「60 unique」を追認するような操作はしない、という是正方針どおり）。
- 詳細と新 run の実測値は [O6 Evidence — Phase C 節](../o6-opponent-intelligence-v1.md#phase-c-o6-aud-002-最終是正raw-public-trajectory-evidence2026-07-21codex-最終監査--claude-是正同-feature-branch) を正とする。
